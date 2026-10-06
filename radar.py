#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Radar Affitti - aggregatore di annunci in affitto per l'area di Modena.

Controlla i siti delle agenzie (fonti_agenzie.csv) e le pagine dei portali
ordinate dal piu' recente (fonti_portali.csv), riconosce gli annunci mai visti
e li manda su Telegram. Rispetta robots.txt e distanzia le richieste.

Comandi principali:
    python radar.py --telegram-setup        collega il bot Telegram (una volta)
    python radar.py                         un controllo completo di tutte le fonti
    python radar.py --loop 30               controlla ogni 30 minuti finche' resta aperto
    python radar.py --prova "Mirantecase"   mostra cosa estrae da una fonte, senza salvare
    python radar.py --stato                 stato delle fonti (ultimi errori, annunci visti)
    python radar.py --esporta               salva tutti gli annunci visti in annunci_visti.csv
"""
from __future__ import annotations

import argparse
import csv
import json
import logging
import random
import re
import sqlite3
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from datetime import datetime
from logging.handlers import RotatingFileHandler
from pathlib import Path
from typing import Dict, List, Optional, Tuple
from urllib import robotparser
from urllib.parse import parse_qsl, urlencode, urljoin, urlparse, urlunparse

try:  # Python 3.11+
    import tomllib
except ModuleNotFoundError:  # Python 3.9 / 3.10: pip install tomli
    import tomli as tomllib  # type: ignore

import requests
from bs4 import BeautifulSoup

try:
    import lxml  # noqa: F401
    PARSER = "lxml"
except ImportError:
    PARSER = "html.parser"

BASE = Path(__file__).resolve().parent
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/126.0 Safari/537.36")
log = logging.getLogger("radar")

# --------------------------------------------------------------------------
# Configurazione
# --------------------------------------------------------------------------
DEFAULT_CFG = {
    "telegram": {"token": "", "chat_id": ""},
    "esecuzione": {
        "intervallo_minuti": 30,
        "pausa_stesso_sito_sec": 4,
        "timeout_sec": 25,
        "usa_browser_per_js": True,
        "pagine_iniziali_immobiliare": 30,
        "pagine_ordinarie_immobiliare": 1,
        "max_messaggi_singoli": 15,
        "notifica_prima_esecuzione": False,
        "avvisa_fonte_ko_dopo": 3,
        "connessioni_parallele": 6,
    },
    "filtri": {
        "prezzo_min": 0,
        "prezzo_max": 0,
        "mq_min": 0,
        "locali_min": 0,
        "escludi_stanze": False,
        "prezzo_affitto_plausibile_max": 6000,
        "tipi_esclusi": ["negozio", "ufficio", "capannone", "magazzino", "laboratorio",
                         "terreno", "garage", "box", "posto auto", "locale commerciale"],
        "zone_fuori_area": [],
        "zone_fuori_area_azione": "segnala",
    },
    "idealista": {"api_key": "", "api_secret": "", "centro": "44.6471,10.9252",
                  "raggio_metri": 16000, "since_date": "T", "ogni_ore": 12},
}


def carica_config(path: Path) -> dict:
    cfg = json.loads(json.dumps(DEFAULT_CFG))
    if path.exists():
        with open(path, "rb") as f:
            user = tomllib.load(f)
        for sez, valori in user.items():
            if isinstance(valori, dict):
                cfg.setdefault(sez, {}).update(valori)
    return cfg


# --------------------------------------------------------------------------
# Fonti
# --------------------------------------------------------------------------
@dataclass
class Fonte:
    tipo: str            # "portale" | "agenzia"
    nome: str
    comune: str
    url: str
    js: bool = False
    pattern: str = ""


def _si(v: str) -> bool:
    return (v or "").strip().lower() in ("si", "sì", "s", "yes", "y", "1", "true", "x", "probabile")


def leggi_csv(path: Path) -> List[dict]:
    if not path.exists():
        return []
    for codifica in ("utf-8-sig", "cp1252"):  # cp1252: file risalvato da Excel su Windows
        try:
            with open(path, encoding=codifica, newline="") as f:
                sample = f.read(4096)
                f.seek(0)
                sep = ";" if sample.count(";") >= sample.count(",") else ","
                righe = list(csv.DictReader(f, delimiter=sep))
            return [{(k or "").strip().lower(): (v or "") for k, v in r.items()} for r in righe]
        except UnicodeDecodeError:
            continue
    raise SystemExit(f"Non riesco a leggere {path.name}: salvalo come CSV UTF-8")


def carica_fonti(cartella: Path) -> List[Fonte]:
    fonti: List[Fonte] = []
    for r in leggi_csv(cartella / "fonti_portali.csv"):
        if r.get("attiva", "sì").strip().lower() in ("no", "0", "n"):
            continue
        if r.get("url"):
            fonti.append(Fonte("portale", (r.get("portale") or "").strip(), (r.get("comune") or "").strip(),
                               r["url"].strip()))
    for r in leggi_csv(cartella / "fonti_agenzie.csv"):
        if r.get("attiva", "sì").strip().lower() in ("no", "0", "n"):
            continue
        url = (r.get("url_affitti") or "").strip()
        if url.startswith("http"):
            fonti.append(Fonte("agenzia", (r.get("agenzia") or "").strip(), (r.get("comune") or "").strip(),
                               url, js=_si(r.get("js", "")), pattern=(r.get("pattern_link") or "").strip()))
    return fonti


# --------------------------------------------------------------------------
# Scaricamento pagine (robots.txt, pause, browser per i siti in JavaScript)
# --------------------------------------------------------------------------
class Scaricatore:
    def __init__(self, cfg: dict):
        e = cfg["esecuzione"]
        self.timeout = e["timeout_sec"]
        self.pausa = e["pausa_stesso_sito_sec"]
        self.usa_browser = e["usa_browser_per_js"]
        self.s = requests.Session()
        self.s.headers.update({
            "User-Agent": UA,
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Accept-Language": "it-IT,it;q=0.9,en;q=0.6",
        })
        self.ultimo: Dict[str, float] = {}
        self.robots: Dict[str, Optional[robotparser.RobotFileParser]] = {}
        self._pw = None
        self._browser = None

    def _attendi(self, host: str) -> None:
        t = self.ultimo.get(host)
        if t is not None:
            mancano = self.pausa + random.uniform(0, 1.5) - (time.monotonic() - t)
            if mancano > 0:
                time.sleep(mancano)

    def consentito(self, url: str) -> Tuple[bool, str]:
        p = urlparse(url)
        base = f"{p.scheme}://{p.netloc}"
        if base not in self.robots:
            rp = robotparser.RobotFileParser()
            try:
                r = self.s.get(base + "/robots.txt", timeout=self.timeout)
                if r.status_code >= 500:
                    self.robots[base] = None          # server in errore: riprova al prossimo giro
                    return False, f"robots.txt non disponibile (HTTP {r.status_code})"
                rp.parse(r.text.splitlines() if r.status_code < 400 else [])  # 4xx = nessuna regola
            except requests.RequestException as ex:
                return False, f"sito non raggiungibile ({type(ex).__name__})"
            self.robots[base] = rp
        rp = self.robots[base]
        if rp is None:
            del self.robots[base]
            return False, "robots.txt non disponibile"
        if not rp.can_fetch(UA, url):
            return False, "vietato da robots.txt"
        return True, ""

    def scarica(self, url: str, js: bool = False) -> Tuple[Optional[str], str]:
        ok, motivo = self.consentito(url)
        if not ok:
            return None, motivo
        host = urlparse(url).netloc.lower()
        self._attendi(host)
        if js and self.usa_browser:
            html = self._scarica_browser(url)
            self.ultimo[host] = time.monotonic()
            if html:
                return html, ""
        errore = ""
        for tentativo in (1, 2):
            try:
                r = self.s.get(url, timeout=self.timeout, allow_redirects=True)
                self.ultimo[host] = time.monotonic()
                if r.status_code == 200:
                    if not r.encoding or r.encoding.lower() == "iso-8859-1":
                        r.encoding = r.apparent_encoding
                    return r.text, ""
                if r.status_code in (401, 403, 429):
                    return None, f"HTTP {r.status_code}: il sito rifiuta le richieste automatiche"
                errore = f"HTTP {r.status_code}"
            except requests.RequestException as ex:
                errore = f"errore di rete ({type(ex).__name__})"
            if tentativo == 1:
                time.sleep(3)
        return None, errore

    def _scarica_browser(self, url: str) -> Optional[str]:
        try:
            if self._browser is None:
                from playwright.sync_api import sync_playwright
                self._pw = sync_playwright().start()
                self._browser = self._pw.chromium.launch(headless=True)
            page = self._browser.new_page(user_agent=UA, locale="it-IT")
            try:
                page.goto(url, wait_until="networkidle", timeout=self.timeout * 2000)
                page.wait_for_timeout(1500)
                return page.content()
            finally:
                page.close()
        except ImportError:
            log.warning("Playwright non installato: %s letto senza browser (vedi LEGGIMI)", url)
            self.usa_browser = False
        except Exception as ex:  # timeout, chiusura inattesa, ecc.
            log.warning("Browser: errore su %s (%s)", url, type(ex).__name__)
        return None

    def chiudi(self) -> None:
        try:
            if self._browser is not None:
                self._browser.close()
            if self._pw is not None:
                self._pw.stop()
        except Exception:
            pass


# --------------------------------------------------------------------------
# Estrazione degli annunci da una pagina
# --------------------------------------------------------------------------
PORTALI = {
    "immobiliare.it": re.compile(r"^https?://(?:www\.)?immobiliare\.it/(?:[a-z]{2}/)?annunci/(\d+)"),
    "casa.it": re.compile(r"^https?://(?:www\.)?casa\.it/immobili/(\d+)"),
    "subito.it": re.compile(r"^https?://(?:www\.)?subito\.it/[\w-]+/[\w-]*?-?(\d{6,})\.htm"),
    "wikicasa.it": re.compile(r"^https?://(?:www\.)?wikicasa\.it/(?:[a-z]{2}/)?annuncio/(\d+)"),
}
NOMI_PORTALI = {"immobiliare.it": "Immobiliare.it", "casa.it": "Casa.it", "subito.it": "Subito.it",
                "wikicasa.it": "Wikicasa", "idealista.it": "Idealista"}
SUGGERIMENTI = ("immobil", "annunc", "scheda", "propert", "propriet", "dettagli", "affitt", "locazion",
                "appartament", "villa", "rif", "detail", "listing", "offert", "abitazion", "casa-",
                "monolocale", "bilocale", "trilocale", "quadrilocale", "attico", "loft")
DA_SALTARE = ("privacy", "cookie", "contatt", "chi-siamo", "chisiamo", "login", "account", "wp-content",
              "wp-json", "/feed", "/tag/", "/category/", "/categoria/", "lavora-con-noi", "valutazion",
              "mutuo", "/blog", "/news", "/page/", "/pag/", "orderby=", "sort=", "ordina", "/cerca",
              "ricerca?", "facebook.com", "instagram.com", "linkedin", "youtube", "whatsapp",
              "newsletter", "registrati", "preferiti", "stampa", "print", "share", "condividi")
ESTENSIONI_FILE = (".jpg", ".jpeg", ".png", ".gif", ".webp", ".svg", ".pdf", ".zip", ".mp4", ".css", ".js")
PARAMETRI_INUTILI = re.compile(r"^(utm_|fbclid|gclid|mc_|ref$|source$|from$)")


def portale_di(url: str) -> Optional[str]:
    """Portale riconosciuto dall'indirizzo. I mini-siti delle agenzie ospitati dai portali
    (es. nome.agenzie.casa.it) si leggono come siti normali; le vetrine Subito no."""
    host = urlparse(url).netloc.lower()
    for dom in PORTALI:
        if host in (dom, "www." + dom) or (dom == "subito.it" and host == "impresapiu.subito.it"):
            return dom
    return None


def canonico(url: str) -> str:
    p = urlparse(url)
    query = urlencode([(k, v) for k, v in parse_qsl(p.query, keep_blank_values=True)
                       if not PARAMETRI_INUTILI.match(k.lower())])
    path = p.path.rstrip("/") or "/"
    return urlunparse((p.scheme.lower(), p.netloc.lower(), path, "", query, ""))


def _dominio(url: str) -> str:
    h = urlparse(url).netloc.lower().split(":")[0]
    return h[4:] if h.startswith("www.") else h


def stesso_sito(a: str, b: str) -> bool:
    ha, hb = _dominio(a), _dominio(b)
    return ha == hb or ha.endswith("." + hb) or hb.endswith("." + ha)


def sembra_annuncio(url: str, pagina: str) -> bool:
    if not stesso_sito(url, pagina):
        return False
    p = urlparse(url)
    path = p.path.lower()
    tutto = (path + "?" + p.query).lower()
    if path.endswith(ESTENSIONI_FILE) or any(s in tutto for s in DA_SALTARE):
        return False
    if canonico(url) == canonico(pagina):
        return False
    segmenti = [s for s in path.split("/") if s]
    ultimo = segmenti[-1] if segmenti else ""
    ha_id = bool(re.search(r"\d{3,}", tutto))
    ha_parola = any(h in tutto for h in SUGGERIMENTI)
    slug = ultimo.count("-") >= 2 and len(ultimo) >= 12
    return (ha_parola and (ha_id or slug)) or (ha_id and slug)


def testo_scheda(a, uid: str, uid_di: Dict[int, str]) -> str:
    """Testo della 'card' che contiene il link: sale finche' il blocco contiene solo questo annuncio."""
    migliore = a.get_text(" ", strip=True)
    nodo = a
    for _ in range(6):
        nodo = nodo.parent
        if nodo is None or nodo.name in ("body", "html", "main"):
            break
        altri = {uid_di.get(id(x)) for x in nodo.find_all("a", href=True)} - {None, uid}
        if altri:  # il blocco contiene anche altri annunci: fermati
            break
        t = nodo.get_text(" ", strip=True)
        if len(t) > 800:
            break
        migliore = t
    return re.sub(r"\s+", " ", migliore)[:800]


def titolo_link(a) -> str:
    t = (a.get("title") or a.get_text(" ", strip=True) or "").strip()
    if not t:
        img = a.find("img")
        if img is not None:
            t = (img.get("alt") or "").strip()
    return re.sub(r"\s+", " ", t)[:160]


def estrai(html: str, url_pagina: str, pattern: str = "") -> Dict[str, Tuple[str, str, str]]:
    """Restituisce {uid: (url, testo_scheda, titolo)} per ogni annuncio linkato nella pagina."""
    soup = BeautifulSoup(html, PARSER)
    portale = portale_di(url_pagina)
    regex = re.compile(pattern, re.I) if pattern else None
    candidati = []
    for a in soup.find_all("a", href=True):
        href = a["href"].strip()
        if not href or href.startswith(("#", "mailto:", "tel:", "javascript:", "whatsapp:")):
            continue
        assoluto = urljoin(url_pagina, href)
        if portale:
            m = PORTALI[portale].match(assoluto)
            if not m:
                continue
            uid = f"{portale}:{m.group(1)}"
            url = canonico(assoluto)
        else:
            if regex is not None:
                if not regex.search(assoluto):
                    continue
            elif not sembra_annuncio(assoluto, url_pagina):
                continue
            url = canonico(assoluto)
            uid = url
        candidati.append((a, uid, url))
    uid_di = {id(a): uid for a, uid, _ in candidati}
    trovati: Dict[str, Tuple[str, str, str]] = {}
    for a, uid, url in candidati:
        testo = testo_scheda(a, uid, uid_di)
        titolo = titolo_link(a)
        prima = trovati.get(uid)
        if prima is None:
            trovati[uid] = (url, testo, titolo)
        else:  # stesso annuncio linkato piu' volte (foto + titolo): tieni il testo piu' ricco
            trovati[uid] = (url, testo if len(testo) > len(prima[1]) else prima[1],
                            titolo if len(titolo) > len(prima[2]) else prima[2])
    return trovati


# --------------------------------------------------------------------------
# Lettura dei dati dal testo della scheda
# --------------------------------------------------------------------------
RE_EURO = re.compile(
    r"(?:€|euro\b|eur\b)\s*(\d{1,3}(?:[.\s']\d{3})+|\d+)(?:,\d{1,2})?"
    r"|(\d{1,3}(?:[.\s']\d{3})+|\d+)(?:,\d{1,2})?\s*(?:€|euro\b|eur\b)", re.I)
RE_MQ = re.compile(r"(\d{2,4})(?:[.,]\d+)?\s*(?:mq|m²|m2|mt2|metri\s*quadr)", re.I)
RE_LOCALI = re.compile(r"(\d)\s*(?:locali|vani)\b", re.I)
PAROLE_LOCALI = {"monolocale": 1, "bilocale": 2, "trilocale": 3, "quadrilocale": 4, "pentalocale": 5}
PAROLE_AFFITTO = ("affitt", "locazion", "/mese", "al mese", "mensil", "canone", "/mm")
PAROLE_STANZA = ("stanza", "camera singola", "camera doppia", "posto letto", "posti letto")


def importi(testo: str) -> List[int]:
    out = []
    for g1, g2 in RE_EURO.findall(testo):
        cifra = re.sub(r"[.\s']", "", g1 or g2)
        if cifra.isdigit():
            out.append(int(cifra))
    return out


@dataclass
class Annuncio:
    uid: str
    url: str
    fonte: str
    tipo_fonte: str
    comune_fonte: str
    titolo: str
    testo: str
    prezzo: Optional[int] = None
    mq: Optional[int] = None
    locali: Optional[int] = None
    stanza: bool = False
    vendita: bool = False
    fuori_area: str = ""


def analizza(uid: str, url: str, testo: str, titolo: str, fonte: Fonte, cfg: dict) -> Annuncio:
    f = cfg["filtri"]
    tutto = f"{titolo} {testo}"
    basso = tutto.lower()
    valori = importi(tutto)
    plaus = int(f.get("prezzo_affitto_plausibile_max", 6000))
    affitti = [v for v in valori if 150 <= v <= plaus]
    prezzo = affitti[0] if affitti else None
    mq = None
    m = RE_MQ.search(tutto)
    if m:
        mq = int(m.group(1))
    locali = None
    for parola, n in PAROLE_LOCALI.items():
        if parola in basso:
            locali = n
            break
    if locali is None:
        m = RE_LOCALI.search(tutto)
        if m:
            locali = int(m.group(1))
    parla_di_affitto = any(k in basso or k in url.lower() for k in PAROLE_AFFITTO)
    vendita = (not parla_di_affitto) and bool(valori) and prezzo is None and max(valori) > plaus
    if not vendita and ("vendita" in url.lower() or "/vendite/" in url.lower()) and not parla_di_affitto:
        vendita = True
    fuori = ""
    for zona in f.get("zone_fuori_area", []) or []:
        if re.search(r"\b" + re.escape(zona.lower()) + r"\b", basso):
            fuori = zona
            break
    nome_fonte = fonte.nome if fonte.tipo == "agenzia" else NOMI_PORTALI.get(portale_di(url) or "", fonte.nome)
    return Annuncio(uid=uid, url=url, fonte=nome_fonte, tipo_fonte=fonte.tipo, comune_fonte=fonte.comune,
                    titolo=titolo or testo[:120], testo=testo, prezzo=prezzo, mq=mq, locali=locali,
                    stanza=any(k in basso for k in PAROLE_STANZA), vendita=vendita, fuori_area=fuori)


def scarta(a: Annuncio, cfg: dict) -> str:
    """Motivo per cui l'annuncio non interessa ('' = va bene)."""
    f = cfg["filtri"]
    if a.vendita:
        return "vendita"
    inizio = (a.titolo or a.testo)[:80].lower().strip()
    path = urlparse(a.url).path.lower()
    for tipo in f.get("tipi_esclusi", []) or []:
        t = tipo.lower()
        if inizio.startswith(t) or f"/{t.replace(' ', '-')}" in path:
            return f"tipo {tipo}"
    if any(k in path for k in ("uffici-locali-commerciali", "box-garage", "terreni", "negozi")):
        return "non residenziale"
    if f.get("escludi_stanze") and a.stanza:
        return "stanza"
    if a.prezzo is not None:
        if f.get("prezzo_max") and a.prezzo > f["prezzo_max"]:
            return "prezzo alto"
        if f.get("prezzo_min") and a.prezzo < f["prezzo_min"]:
            return "prezzo basso"
    if f.get("mq_min") and a.mq is not None and a.mq < f["mq_min"]:
        return "metratura"
    if f.get("locali_min") and a.locali is not None and a.locali < f["locali_min"]:
        return "locali"
    if a.fuori_area and f.get("zone_fuori_area_azione", "segnala") == "scarta":
        return f"fuori area ({a.fuori_area})"
    return ""


# --------------------------------------------------------------------------
# Archivio (SQLite)
# --------------------------------------------------------------------------
class Archivio:
    def __init__(self, path: Path):
        self.db = sqlite3.connect(str(path))
        self.db.executescript("""
        CREATE TABLE IF NOT EXISTS annunci (
            uid TEXT PRIMARY KEY, url TEXT, fonte TEXT, tipo_fonte TEXT, comune TEXT, titolo TEXT,
            prezzo INTEGER, mq INTEGER, locali INTEGER, primo_visto TEXT, ultimo_visto TEXT,
            notificato INTEGER DEFAULT 0, scartato TEXT DEFAULT '');
        CREATE TABLE IF NOT EXISTS fonti (
            url TEXT PRIMARY KEY, nome TEXT, inizializzata INTEGER DEFAULT 0, ultimo_ok TEXT,
            ultimo_errore TEXT DEFAULT '', errori_consecutivi INTEGER DEFAULT 0, ultimi_link INTEGER DEFAULT 0,
            avvisato INTEGER DEFAULT 0);
        CREATE TABLE IF NOT EXISTS impostazioni (chiave TEXT PRIMARY KEY, valore TEXT);
        """)

    @staticmethod
    def adesso() -> str:
        return datetime.now().strftime("%Y-%m-%d %H:%M")

    def fonte(self, url: str) -> dict:
        r = self.db.execute("SELECT inizializzata, errori_consecutivi, ultimi_link, avvisato FROM fonti WHERE url=?",
                            (url,)).fetchone()
        return {"inizializzata": bool(r[0]), "errori": r[1], "ultimi_link": r[2], "avvisato": bool(r[3])} if r else \
            {"inizializzata": False, "errori": 0, "ultimi_link": 0, "avvisato": False}

    def esito_fonte(self, url: str, nome: str, errore: str, n_link: int, avvisato: Optional[bool] = None) -> None:
        prima = self.fonte(url)
        if errore:
            self.db.execute("""INSERT INTO fonti(url,nome,ultimo_errore,errori_consecutivi) VALUES(?,?,?,1)
                ON CONFLICT(url) DO UPDATE SET nome=excluded.nome, ultimo_errore=excluded.ultimo_errore,
                errori_consecutivi=errori_consecutivi+1""", (url, nome, f"{self.adesso()} {errore}"))
        else:
            self.db.execute("""INSERT INTO fonti(url,nome,inizializzata,ultimo_ok,errori_consecutivi,ultimi_link)
                VALUES(?,?,1,?,0,?) ON CONFLICT(url) DO UPDATE SET nome=excluded.nome, inizializzata=1,
                ultimo_ok=excluded.ultimo_ok, errori_consecutivi=0, ultimi_link=excluded.ultimi_link""",
                            (url, nome, self.adesso(), n_link))
        if avvisato is not None or (not errore and prima["avvisato"]):
            self.db.execute("UPDATE fonti SET avvisato=? WHERE url=?", (1 if avvisato else 0, url))
        self.db.commit()

    def segna_avvisato(self, url: str) -> None:
        self.db.execute("UPDATE fonti SET avvisato=1 WHERE url=?", (url,))
        self.db.commit()

    def visto(self, uid: str) -> bool:
        return self.db.execute("SELECT 1 FROM annunci WHERE uid=?", (uid,)).fetchone() is not None

    def salva(self, a: Annuncio, notificato: bool, scartato: str = "") -> None:
        ora = self.adesso()
        self.db.execute("""INSERT INTO annunci(uid,url,fonte,tipo_fonte,comune,titolo,prezzo,mq,locali,
            primo_visto,ultimo_visto,notificato,scartato) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)
            ON CONFLICT(uid) DO UPDATE SET ultimo_visto=excluded.ultimo_visto""",
                        (a.uid, a.url, a.fonte, a.tipo_fonte, a.comune_fonte, a.titolo[:200], a.prezzo, a.mq,
                         a.locali, ora, ora, 1 if notificato else 0, scartato))

    def tocca(self, uid: str) -> None:
        self.db.execute("UPDATE annunci SET ultimo_visto=? WHERE uid=?", (self.adesso(), uid))

    def simile_su_immobiliare(self, a: Annuncio) -> Optional[bool]:
        """True se un annuncio con stesso prezzo (e metratura simile) e' gia' stato visto su Immobiliare.it."""
        if a.prezzo is None:
            return None
        q = "SELECT mq FROM annunci WHERE fonte='Immobiliare.it' AND prezzo=?"
        righe = self.db.execute(q, (a.prezzo,)).fetchall()
        if a.mq is None:
            return bool(righe)
        return any(r[0] is None or abs(r[0] - a.mq) <= 3 for r in righe)

    def get(self, chiave: str, default: str = "") -> str:
        r = self.db.execute("SELECT valore FROM impostazioni WHERE chiave=?", (chiave,)).fetchone()
        return r[0] if r else default

    def set(self, chiave: str, valore: str) -> None:
        self.db.execute("INSERT INTO impostazioni VALUES(?,?) ON CONFLICT(chiave) DO UPDATE SET valore=excluded.valore",
                        (chiave, valore))
        self.db.commit()

    def commit(self) -> None:
        self.db.commit()


# --------------------------------------------------------------------------
# Telegram
# --------------------------------------------------------------------------
def esc(t: str) -> str:
    return (t or "").replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


class Telegram:
    def __init__(self, cfg: dict, finto: bool = False):
        self.token = str(cfg["telegram"].get("token", "")).strip()
        self.chat = str(cfg["telegram"].get("chat_id", "")).strip()
        self.finto = finto or not (self.token and self.chat)
        if self.finto and not finto:
            log.warning("Telegram non configurato: i nuovi annunci vengono solo mostrati qui e salvati nel log")

    def invia(self, testo: str) -> bool:
        if self.finto:
            print("\n--- messaggio ---\n" + re.sub(r"<[^>]+>", "", testo) + "\n-----------------")
            return True
        for tentativo in range(3):
            try:
                r = requests.post(f"https://api.telegram.org/bot{self.token}/sendMessage", timeout=20,
                                  data={"chat_id": self.chat, "text": testo[:4000], "parse_mode": "HTML",
                                        "disable_web_page_preview": "false"})
                if r.status_code == 429:
                    time.sleep(int(r.json().get("parameters", {}).get("retry_after", 5)) + 1)
                    continue
                if r.ok:
                    time.sleep(1.1)
                    return True
                log.error("Telegram ha risposto %s: %s", r.status_code, r.text[:200])
                return False
            except requests.RequestException as ex:
                log.error("Telegram non raggiungibile (%s)", type(ex).__name__)
                time.sleep(3)
        return False


def riga_dati(a: Annuncio) -> str:
    pezzi = []
    if a.prezzo:
        pezzi.append(f"€ {a.prezzo:,}/mese".replace(",", "."))
    if a.mq:
        pezzi.append(f"{a.mq} mq")
    if a.locali:
        pezzi.append(f"{a.locali} locali")
    if a.stanza:
        pezzi.append("stanza")
    return " · ".join(pezzi)


def messaggio(a: Annuncio, su_immobiliare: Optional[bool]) -> str:
    righe = [f"<b>Nuovo affitto</b> · {esc(a.fonte)}" + (f" ({esc(a.comune_fonte)})" if a.comune_fonte else "")]
    righe.append(esc(a.titolo[:150]))
    dati = riga_dati(a)
    if dati:
        righe.append(dati)
    if a.tipo_fonte == "agenzia":
        if su_immobiliare is False:
            righe.append("<b>Non ancora visto su Immobiliare.it</b>")
        elif su_immobiliare is None:
            righe.append("Confronto con Immobiliare.it non possibile (prezzo non letto)")
    if a.fuori_area:
        righe.append(f"Attenzione: cita {esc(a.fuori_area)}, forse fuori zona")
    righe.append(a.url)
    return "\n".join(righe)


def riepilogo(lista: List[Tuple[Annuncio, Optional[bool]]]) -> List[str]:
    blocchi, corrente = [], [f"<b>{len(lista)} nuovi affitti</b>"]
    for a, su_imm in lista:
        extra = " · solo sito agenzia" if (a.tipo_fonte == "agenzia" and su_imm is False) else ""
        riga = f"• {esc(a.fonte)}: {esc(a.titolo[:70])} {riga_dati(a)}{extra}\n{a.url}"
        if sum(len(x) for x in corrente) + len(riga) > 3500:
            blocchi.append("\n".join(corrente))
            corrente = []
        corrente.append(riga)
    blocchi.append("\n".join(corrente))
    return blocchi


# --------------------------------------------------------------------------
# Idealista (solo API ufficiale, con credenziali richieste a idealista)
# --------------------------------------------------------------------------
def idealista_api(cfg: dict, archivio: Archivio) -> Tuple[List[Annuncio], str]:
    c = cfg["idealista"]
    if not c.get("api_key") or not c.get("api_secret"):
        return [], ""
    ultimo = archivio.get("idealista_ultimo", "")
    if ultimo:
        trascorse = (datetime.now() - datetime.strptime(ultimo, "%Y-%m-%d %H:%M")).total_seconds() / 3600
        if trascorse < float(c.get("ogni_ore", 12)):
            return [], ""
    try:
        token = archivio.get("idealista_token", "")
        scade = float(archivio.get("idealista_token_scade", "0") or 0)
        if not token or time.time() > scade - 300:
            r = requests.post("https://api.idealista.com/oauth/token", auth=(c["api_key"], c["api_secret"]),
                              data={"grant_type": "client_credentials", "scope": "read"}, timeout=30)
            r.raise_for_status()
            dati = r.json()
            token = dati["access_token"]
            archivio.set("idealista_token", token)
            archivio.set("idealista_token_scade", str(time.time() + float(dati.get("expires_in", 3600))))
        parametri = {"operation": "rent", "propertyType": "homes", "center": c["centro"],
                     "distance": str(c["raggio_metri"]), "order": "publicationDate", "sort": "desc",
                     "maxItems": "50", "numPage": "1", "language": "it", "sinceDate": c.get("since_date", "T")}
        r = requests.post("https://api.idealista.com/3.5/it/search", data=parametri, timeout=30,
                          headers={"Authorization": f"Bearer {token}"})
        r.raise_for_status()
        archivio.set("idealista_ultimo", Archivio.adesso())
    except Exception as ex:
        return [], f"API Idealista: {type(ex).__name__}"
    fonte = Fonte("portale", "Idealista", "", "https://www.idealista.it")
    out = []
    for el in r.json().get("elementList", []):
        testo = " ".join(str(el.get(k, "")) for k in ("address", "municipality", "district", "description"))
        a = Annuncio(uid=f"idealista:{el.get('propertyCode')}", url=el.get("url", ""), fonte="Idealista",
                     tipo_fonte="portale", comune_fonte=str(el.get("municipality", "")),
                     titolo=f"{el.get('propertyType', 'immobile')} in {el.get('address', '')}".strip(),
                     testo=testo[:800], prezzo=int(el["price"]) if el.get("price") else None,
                     mq=int(el["size"]) if el.get("size") else None,
                     locali=int(el["rooms"]) if el.get("rooms") else None)
        a.stanza = el.get("propertyType") == "room"
        for zona in cfg["filtri"].get("zone_fuori_area", []) or []:
            if re.search(r"\b" + re.escape(zona.lower()) + r"\b", testo.lower()):
                a.fuori_area = zona
                break
        out.append(a)
    return out, ""


# --------------------------------------------------------------------------
# Controllo delle fonti
# --------------------------------------------------------------------------
def pagina_immobiliare(url: str, n: int) -> str:
    if n <= 1:
        return url
    p = urlparse(url)
    q = [(k, v) for k, v in parse_qsl(p.query, keep_blank_values=True) if k != "pag"] + [("pag", str(n))]
    return urlunparse((p.scheme, p.netloc, p.path, "", urlencode(q), ""))


def leggi_fonte(f: Fonte, scaricatore: Scaricatore, inizializzata: bool, cfg: dict
                ) -> Tuple[Dict[str, Tuple[str, str, str]], str]:
    urls = [f.url]
    if portale_di(f.url) == "immobiliare.it":
        e = cfg["esecuzione"]
        n = e["pagine_ordinarie_immobiliare"] if inizializzata else e["pagine_iniziali_immobiliare"]
        urls = [pagina_immobiliare(f.url, i) for i in range(1, max(1, int(n)) + 1)]
    trovati: Dict[str, Tuple[str, str, str]] = {}
    for i, u in enumerate(urls):
        html, errore = scaricatore.scarica(u, js=f.js)
        if html is None:
            return (trovati, "") if (i > 0 and trovati) else (trovati, errore)
        pagina = estrai(html, u, f.pattern)
        nuovi = [k for k in pagina if k not in trovati]
        trovati.update(pagina)
        if i > 0 and not nuovi:
            break
    return trovati, ""


def esegui(cfg: dict, cartella: Path, solo: str = "", nome: str = "", finto: bool = False) -> int:
    fonti = carica_fonti(cartella)
    if solo:
        fonti = [f for f in fonti if f.tipo == solo]
    if nome:
        fonti = [f for f in fonti if nome.lower() in f.nome.lower() or nome.lower() in f.url.lower()]
    archivio = Archivio(cartella / "radar.db")
    tg = Telegram(cfg, finto=finto)
    scaricatore = Scaricatore(cfg)
    e = cfg["esecuzione"]
    da_notificare: List[Tuple[Annuncio, Optional[bool]]] = []
    avvisi: List[str] = []
    stati = {f.url: archivio.fonte(f.url) for f in fonti}
    log.info("Controllo di %d fonti", len(fonti))
    # 1) lettura: siti diversi in parallelo, stesso sito in sequenza; i siti in JavaScript alla fine
    gruppi: Dict[str, List[Fonte]] = {}
    for f in fonti:
        if not (f.js and e["usa_browser_per_js"]):
            gruppi.setdefault(_dominio(f.url), []).append(f)
    letture: Dict[str, Tuple[Dict[str, Tuple[str, str, str]], str]] = {}

    def leggi_gruppo(lista: List[Fonte]):
        return [(f.url, leggi_fonte(f, scaricatore, stati[f.url]["inizializzata"], cfg)) for f in lista]

    with ThreadPoolExecutor(max_workers=max(1, int(e.get("connessioni_parallele", 6)))) as pool:
        for esiti in pool.map(leggi_gruppo, gruppi.values()):
            letture.update(dict(esiti))
    for f in fonti:
        if f.url not in letture:
            letture[f.url] = leggi_fonte(f, scaricatore, stati[f.url]["inizializzata"], cfg)
    # 2) elaborazione: Immobiliare.it per primo, cosi' il confronto "gia' sul portale" e' aggiornato
    ordine = sorted(fonti, key=lambda f: 0 if portale_di(f.url) == "immobiliare.it" else 1)
    try:
        for f in ordine:
            stato = stati[f.url]
            trovati, errore = letture[f.url]
            if errore:
                archivio.esito_fonte(f.url, f.nome, errore, 0)
                log.info("KO  %-40s %s", f.nome[:40], errore)
                if stato["errori"] + 1 >= int(e["avvisa_fonte_ko_dopo"]) and not stato["avvisato"]:
                    avvisi.append(f"{f.nome}: {errore}")
                    archivio.segna_avvisato(f.url)
                continue
            prima_volta = not stato["inizializzata"]
            if not trovati and stato["ultimi_link"] > 0 and not stato["avvisato"]:
                avvisi.append(f"{f.nome}: nessun annuncio trovato (prima ne aveva {stato['ultimi_link']}). "
                              "Il sito potrebbe essere cambiato.")
                archivio.esito_fonte(f.url, f.nome, "", 0)
                archivio.segna_avvisato(f.url)
            else:
                archivio.esito_fonte(f.url, f.nome, "", len(trovati))
            nuovi = 0
            for uid, (url, testo, titolo) in trovati.items():
                if archivio.visto(uid):
                    archivio.tocca(uid)
                    continue
                a = analizza(uid, url, testo, titolo, f, cfg)
                motivo = scarta(a, cfg)
                if prima_volta and not e["notifica_prima_esecuzione"]:
                    archivio.salva(a, notificato=False, scartato=motivo or "prima lettura")
                    continue
                if motivo:
                    archivio.salva(a, notificato=False, scartato=motivo)
                    continue
                su_imm = archivio.simile_su_immobiliare(a) if a.tipo_fonte == "agenzia" else True
                da_notificare.append((a, su_imm))
                archivio.salva(a, notificato=True)
                nuovi += 1
            archivio.commit()
            log.info("OK  %-40s %3d annunci, %d nuovi%s", f.nome[:40], len(trovati), nuovi,
                     " (prima lettura: salvati senza avvisare)" if prima_volta else "")
        if not solo or solo == "portale":
            extra, errore = idealista_api(cfg, archivio)
            if errore:
                log.info("KO  Idealista API: %s", errore)
            for a in extra:
                if archivio.visto(a.uid):
                    continue
                motivo = scarta(a, cfg)
                archivio.salva(a, notificato=not motivo, scartato=motivo)
                if not motivo:
                    da_notificare.append((a, True))
            archivio.commit()
    finally:
        scaricatore.chiudi()
    # prima gli annunci che non risultano su Immobiliare.it: sono il vantaggio
    da_notificare.sort(key=lambda x: 0 if (x[0].tipo_fonte == "agenzia" and x[1] is False) else 1)
    if len(da_notificare) > int(e["max_messaggi_singoli"]):
        for blocco in riepilogo(da_notificare):
            tg.invia(blocco)
    else:
        for a, su_imm in da_notificare:
            tg.invia(messaggio(a, su_imm))
    if avvisi:
        tg.invia("<b>Radar Affitti: fonti da controllare</b>\n" + "\n".join("• " + esc(x) for x in avvisi))
    log.info("Fine controllo: %d nuovi annunci inviati", len(da_notificare))
    return len(da_notificare)


# --------------------------------------------------------------------------
# Comandi di servizio
# --------------------------------------------------------------------------
def prova_fonte(cfg: dict, cartella: Path, nome: str) -> None:
    fonti = [f for f in carica_fonti(cartella) if nome.lower() in f.nome.lower() or nome.lower() in f.url.lower()]
    if not fonti:
        print(f"Nessuna fonte contiene '{nome}'")
        return
    s = Scaricatore(cfg)
    try:
        for f in fonti[:5]:
            print(f"\n== {f.nome} ({f.comune})  {f.url}  js={'sì' if f.js else 'no'}")
            html, errore = s.scarica(f.url, js=f.js)
            if html is None:
                print("   errore:", errore)
                continue
            trovati = estrai(html, f.url, f.pattern)
            print(f"   {len(trovati)} link ad annunci")
            for uid, (url, testo, titolo) in list(trovati.items())[:15]:
                a = analizza(uid, url, testo, titolo, f, cfg)
                motivo = scarta(a, cfg)
                print(f"   - {riga_dati(a) or 'dati non letti':28} {('[' + motivo + '] ') if motivo else ''}"
                      f"{a.titolo[:60]}\n     {url}")
    finally:
        s.chiudi()


def stato(cartella: Path) -> None:
    a = Archivio(cartella / "radar.db")
    print("Annunci in archivio:", a.db.execute("SELECT COUNT(*) FROM annunci").fetchone()[0],
          "| notificati:", a.db.execute("SELECT COUNT(*) FROM annunci WHERE notificato=1").fetchone()[0])
    print("\nFonti con problemi:")
    for nome, url, err, n in a.db.execute(
            "SELECT nome,url,ultimo_errore,errori_consecutivi FROM fonti WHERE errori_consecutivi>0 ORDER BY 4 DESC"):
        print(f"  {n}x  {nome[:40]:40} {err}")
    print("\nFonti senza annunci all'ultimo controllo:")
    for nome, url in a.db.execute("SELECT nome,url FROM fonti WHERE errori_consecutivi=0 AND ultimi_link=0"):
        print(f"  {nome[:40]:40} {url}")


def esporta(cartella: Path) -> None:
    a = Archivio(cartella / "radar.db")
    righe = a.db.execute("SELECT primo_visto,fonte,comune,titolo,prezzo,mq,locali,url,notificato,scartato "
                         "FROM annunci ORDER BY primo_visto DESC").fetchall()
    with open(cartella / "annunci_visti.csv", "w", encoding="utf-8-sig", newline="") as f:
        w = csv.writer(f, delimiter=";")
        w.writerow(["primo_visto", "fonte", "comune", "titolo", "prezzo", "mq", "locali", "url", "notificato", "scartato"])
        w.writerows(righe)
    print(f"Salvati {len(righe)} annunci in annunci_visti.csv")


def telegram_setup(percorso_cfg: Path) -> None:
    print("1) Su Telegram apri @BotFather, scrivi /newbot e segui le istruzioni.")
    token = input("2) Incolla qui il token che ti ha dato BotFather: ").strip()
    if not re.match(r"^\d+:[\w-]{30,}$", token):
        print("Il token non sembra valido (formato 123456:ABC...). Riprova.")
        return
    input("3) Apri il tuo nuovo bot su Telegram, premi Avvia e scrivi 'ciao'. Poi premi Invio qui... ")
    r = requests.get(f"https://api.telegram.org/bot{token}/getUpdates", timeout=20).json()
    chats = [u["message"]["chat"]["id"] for u in r.get("result", []) if "message" in u]
    if not chats:
        print("Non vedo messaggi: scrivi qualcosa al bot e rilancia il comando.")
        return
    chat_id = str(chats[-1])
    testo = percorso_cfg.read_text(encoding="utf-8") if percorso_cfg.exists() else "[telegram]\ntoken = \"\"\nchat_id = \"\"\n"
    testo = re.sub(r'(?m)^token\s*=.*$', f'token = "{token}"', testo, count=1)
    testo = re.sub(r'(?m)^chat_id\s*=.*$', f'chat_id = "{chat_id}"', testo, count=1)
    percorso_cfg.write_text(testo, encoding="utf-8")
    requests.post(f"https://api.telegram.org/bot{token}/sendMessage", timeout=20,
                  data={"chat_id": chat_id, "text": "Radar Affitti collegato. Da ora i nuovi annunci arrivano qui."})
    print(f"Fatto: chat {chat_id} salvata in {percorso_cfg.name}. Ti ho mandato un messaggio di prova.")


def configura_log(cartella: Path, verboso: bool) -> None:
    log.setLevel(logging.DEBUG if verboso else logging.INFO)
    fmt = logging.Formatter("%(asctime)s %(message)s", "%d/%m %H:%M")
    fh = RotatingFileHandler(cartella / "radar.log", maxBytes=1_000_000, backupCount=3, encoding="utf-8")
    fh.setFormatter(fmt)
    sh = logging.StreamHandler(sys.stdout)
    sh.setFormatter(fmt)
    log.addHandler(fh)
    log.addHandler(sh)


def main() -> None:
    ap = argparse.ArgumentParser(description="Radar Affitti: nuovi annunci in affitto da agenzie e portali")
    ap.add_argument("--cartella", default=str(BASE), help="cartella con config.toml e i CSV delle fonti")
    ap.add_argument("--loop", type=float, metavar="MINUTI", help="ripete il controllo ogni N minuti")
    ap.add_argument("--solo", choices=["agenzia", "portale"], help="controlla solo siti delle agenzie o solo portali")
    ap.add_argument("--fonte", default="", help="controlla solo le fonti il cui nome o URL contiene questo testo")
    ap.add_argument("--prova", metavar="NOME", help="mostra cosa estrae da una fonte, senza salvare nulla")
    ap.add_argument("--senza-telegram", action="store_true", help="mostra i messaggi a schermo invece di inviarli")
    ap.add_argument("--telegram-setup", action="store_true", help="collega il bot Telegram")
    ap.add_argument("--stato", action="store_true", help="stato delle fonti")
    ap.add_argument("--esporta", action="store_true", help="esporta gli annunci visti in CSV")
    ap.add_argument("-v", "--verboso", action="store_true")
    args = ap.parse_args()
    for flusso in (sys.stdout, sys.stderr):  # console di Windows: niente errori su caratteri particolari
        try:
            flusso.reconfigure(errors="replace")
        except (AttributeError, ValueError):
            pass
    cartella = Path(args.cartella).resolve()
    percorso_cfg = cartella / "config.toml"
    if args.telegram_setup:
        telegram_setup(percorso_cfg)
        return
    if args.stato:
        stato(cartella)
        return
    if args.esporta:
        esporta(cartella)
        return
    cfg = carica_config(percorso_cfg)
    configura_log(cartella, args.verboso)
    if args.prova:
        prova_fonte(cfg, cartella, args.prova)
        return
    if not args.loop:
        esegui(cfg, cartella, args.solo or "", args.fonte, args.senza_telegram)
        return
    minuti = max(10.0, args.loop)
    log.info("Modalita' continua: un controllo ogni %.0f minuti (Ctrl+C per fermare)", minuti)
    while True:
        try:
            esegui(cfg, cartella, args.solo or "", args.fonte, args.senza_telegram)
        except KeyboardInterrupt:
            raise
        except Exception:
            log.exception("Errore imprevisto durante il controllo: riprovo al prossimo giro")
        attesa = minuti * 60 * random.uniform(0.85, 1.15)
        log.info("Prossimo controllo tra %.0f minuti", attesa / 60)
        time.sleep(attesa)


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\nFermato.")
