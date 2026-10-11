"""Impostazioni del radar.

- config.toml        impostazioni condivise (sono nel repository: valgono su tutti i PC)
- config.local.toml  facoltativo, solo su questo PC: sovrascrive config.toml sezione per sezione
- .env               segreti (token Telegram, chiave OpenRouter...): mai nel repository
"""

import copy
import os
import re
import shutil
import tomllib
from pathlib import Path

DEFAULT_CFG: dict = {
    "esecuzione": {
        "intervallo_minuti": 30,
        "pausa_stesso_sito_sec": 4,
        "timeout_sec": 25,
        "usa_browser_per_js": True,
        "pagine_iniziali_immobiliare": 30,
        "pagine_ordinarie_immobiliare": 1,
        "notifica_prima_esecuzione": False,
        "connessioni_parallele": 6,
    },
    "filtri": {
        "prezzo_min": 0,
        "prezzo_max": 0,
        "mq_min": 0,
        "mq_max": 0,
        "locali_min": 0,
        "escludi_stanze": False,
        "dati_mancanti": "invia",
        "prezzo_affitto_plausibile_max": 6000,
        "tipi_esclusi": ["negozio", "ufficio", "capannone", "magazzino", "laboratorio", "terreno",
                         "garage", "box", "posto auto", "locale commerciale"],
        "comuni_area": [],
        "frazioni_area": {},
        "zone_fuori_area": [],
        "fuori_zona": "scarta",
    },
    "notifiche": {
        "silenzio_dalle": "",
        "silenzio_alle": "",
        "max_messaggi_singoli": 15,
        "anteprima_link": True,
        "avvisi_guasti": True,
        "guasto_dopo_ore": 24,
        "avvisi_guasti_ogni_giorni": 7,
        "guasti_da_soli_dopo_giorni": 3,
    },
    "doppioni": {
        "finestra_giorni": 120,
        "tolleranza_prezzo_pct": 3,
        "tolleranza_mq": 3,
        "chiedi_llm_nei_dubbi": True,
    },
    "dettagli": {
        "leggi_pagina_annuncio": True,
        "anche_portali_se_mancano_dati": True,
        "max_pagine_per_controllo": 40,
    },
    "llm": {
        "usa": True,
        "file_modello": "llm.toml",
        "max_chiamate_per_controllo": 40,
        "max_chiamate_al_giorno": 300,
        "max_spesa_giornaliera_usd": 0.5,
    },
    "aggiornamento": {
        "automatico": True,
        "remoto": "origin",
        "ramo": "main",
    },
    "idealista": {
        "centro": "44.6471,10.9252",
        "raggio_metri": 16000,
        "since_date": "T",
        "ogni_ore": 12,
    },
}

SEGRETI = ("TELEGRAM_TOKEN", "TELEGRAM_CHAT_ID", "OPENROUTER_API_KEY", "IDEALISTA_API_KEY", "IDEALISTA_API_SECRET")
ALTRE_VARIABILI = ("OPENROUTER_MODEL", "LLM_CONFIG", "TELEGRAM_API_URL", "OPENROUTER_BASE_URL")


class Percorsi:
    """Dove stanno i file. I dati che cambiano (archivio, diario) vanno in stato/, ignorata da git."""

    def __init__(self, base: Path):
        self.base = Path(base).resolve()
        self.stato = self.base / "stato"
        self.db = self.stato / "radar.db"
        self.log = self.stato / "radar.log"
        self.lock = self.stato / "radar.lock"
        self.esportazione = self.stato / "annunci_visti.csv"
        self.env = self.base / ".env"
        self.fonti = self.base / "fonti" if (self.base / "fonti").is_dir() else self.base

    def prepara(self) -> None:
        self.stato.mkdir(parents=True, exist_ok=True)
        vecchio = self.base / "radar.db"  # prima versione: archivio accanto a radar.py
        if vecchio.exists() and not self.db.exists():
            shutil.move(str(vecchio), str(self.db))


def carica_config(base: Path) -> dict:
    cfg = copy.deepcopy(DEFAULT_CFG)
    letti = []
    for nome in ("config.toml", "config.local.toml"):
        p = Path(base) / nome
        if not p.exists():
            continue
        with open(p, "rb") as f:
            dati = tomllib.load(f)
        for sez, valori in dati.items():
            if isinstance(valori, dict):
                cfg.setdefault(sez, {}).update(valori)
        # compatibilita' con la prima versione del radar
        es = dati.get("esecuzione", {})
        if "max_messaggi_singoli" in es and "max_messaggi_singoli" not in dati.get("notifiche", {}):
            cfg["notifiche"]["max_messaggi_singoli"] = es["max_messaggi_singoli"]
        fil = dati.get("filtri", {})
        if fil.get("zone_fuori_area_azione") and "fuori_zona" not in fil:
            cfg["filtri"]["fuori_zona"] = fil["zone_fuori_area_azione"]
        letti.append(nome)
    cfg["_letti"] = letti
    return cfg


_ORA = re.compile(r"\s*(\d{1,2})[:.](\d{2})\s*")  # stessa regola di notifiche._ora (qui non si importa: usa requests)

# (sezione, chiave): (deve essere intero, minimo). 0 = "nessun limite" per prezzi e metrature.
_NUMERI: dict[tuple[str, str], tuple[bool, float]] = {
    ("filtri", "prezzo_min"): (False, 0), ("filtri", "prezzo_max"): (False, 0),
    ("filtri", "mq_min"): (False, 0), ("filtri", "mq_max"): (False, 0), ("filtri", "locali_min"): (True, 0),
    ("filtri", "prezzo_affitto_plausibile_max"): (False, 1),
    ("notifiche", "max_messaggi_singoli"): (True, 1), ("notifiche", "guasto_dopo_ore"): (False, 0),
    ("notifiche", "avvisi_guasti_ogni_giorni"): (False, 0), ("notifiche", "guasti_da_soli_dopo_giorni"): (False, 0),
    ("doppioni", "finestra_giorni"): (False, 1), ("doppioni", "tolleranza_prezzo_pct"): (False, 0),
    ("doppioni", "tolleranza_mq"): (False, 0),
    ("dettagli", "max_pagine_per_controllo"): (True, 0),
    ("llm", "max_chiamate_per_controllo"): (True, 0), ("llm", "max_chiamate_al_giorno"): (True, 0),
    ("llm", "max_spesa_giornaliera_usd"): (False, 0),
    ("esecuzione", "intervallo_minuti"): (True, 1), ("esecuzione", "pausa_stesso_sito_sec"): (False, 0),
    ("esecuzione", "timeout_sec"): (False, 1), ("esecuzione", "connessioni_parallele"): (True, 1),
    ("esecuzione", "pagine_iniziali_immobiliare"): (True, 1), ("esecuzione", "pagine_ordinarie_immobiliare"): (True, 1),
}
_BOOLEANI = (("filtri", "escludi_stanze"), ("notifiche", "anteprima_link"), ("notifiche", "avvisi_guasti"),
             ("doppioni", "chiedi_llm_nei_dubbi"), ("dettagli", "leggi_pagina_annuncio"),
             ("dettagli", "anche_portali_se_mancano_dati"), ("llm", "usa"), ("aggiornamento", "automatico"),
             ("esecuzione", "usa_browser_per_js"), ("esecuzione", "notifica_prima_esecuzione"))
_SCELTE = {("filtri", "dati_mancanti"): ("invia", "scarta"), ("filtri", "fuori_zona"): ("scarta", "segnala")}
_ELENCHI = (("filtri", "tipi_esclusi"), ("filtri", "comuni_area"), ("filtri", "zone_fuori_area"))
_TESTI = (("llm", "file_modello"), ("aggiornamento", "remoto"), ("aggiornamento", "ramo"))


def controlla_config(cfg: dict) -> list[str]:
    """Valori che romperebbero il radar o ne cambierebbero il comportamento senza dare errori: un numero scritto
    tra virgolette (il filtro darebbe TypeError), una stringa vuota in tipi_esclusi (scarterebbe ogni annuncio),
    un orario di silenzio scritto male (e' ignorato). Elenco vuoto = tutto a posto. La usa --verifica, quindi
    l'aggiornamento automatico non tiene una versione con una configurazione cosi'."""
    problemi: list[str] = []

    def segnala(sez: str, chiave: str, valore, motivo: str) -> None:
        letto = repr(valore)
        problemi.append(f"[{sez}] {chiave} = {letto if len(letto) <= 40 else letto[:37] + '...'}: {motivo}")

    for (sez, chiave), (intero, minimo) in _NUMERI.items():
        v = cfg.get(sez, {}).get(chiave)
        if isinstance(v, bool) or not isinstance(v, int if intero else (int, float)):
            segnala(sez, chiave, v, "serve un numero intero" if intero else "serve un numero (senza virgolette)")
        elif v < minimo:
            segnala(sez, chiave, v, f"non puo' essere minore di {minimo:g}")
    for sez, chiave in _BOOLEANI:
        v = cfg.get(sez, {}).get(chiave)
        if v is not None and not isinstance(v, bool):
            segnala(sez, chiave, v, "serve true o false (senza virgolette)")
    for (sez, chiave), ammessi in _SCELTE.items():
        v = cfg.get(sez, {}).get(chiave)
        if str(v).lower() not in ammessi:
            segnala(sez, chiave, v, "valori ammessi: " + ", ".join(f'"{a}"' for a in ammessi))
    for sez, chiave in _ELENCHI:
        v = cfg.get(sez, {}).get(chiave)
        if not isinstance(v, list) or any(not isinstance(x, str) or not x.strip() for x in v):
            segnala(sez, chiave, v, "serve un elenco di testi, nessuno vuoto")
    parziali = cfg.get("filtri", {}).get("frazioni_area")
    if not isinstance(parziali, dict) or any(
            not isinstance(v, list) or not v or any(not isinstance(x, str) or not x.strip() for x in v)
            for v in parziali.values()):
        segnala("filtri", "frazioni_area", parziali, "serve comune = [elenco di frazioni, nessuna vuota]")
    for sez, chiave in _TESTI:
        v = cfg.get(sez, {}).get(chiave)
        if not isinstance(v, str) or not v.strip():
            segnala(sez, chiave, v, "serve un testo non vuoto")
    n = cfg.get("notifiche", {})
    orari = [n.get("silenzio_dalle"), n.get("silenzio_alle")]
    if any(not isinstance(o, str) for o in orari):
        segnala("notifiche", "silenzio_dalle/silenzio_alle", orari, "servono testi, per esempio \"22:00\" e \"08:00\"")
    elif any(orari) and not all(
            (m := _ORA.fullmatch(o)) and int(m.group(1)) <= 23 and int(m.group(2)) <= 59 for o in orari):
        segnala("notifiche", "silenzio_dalle/silenzio_alle", orari,
                "servono due orari come \"22:00\" (o tutti e due vuoti per nessun silenzio)")
    f = cfg.get("filtri", {})
    for minimo, massimo in (("prezzo_min", "prezzo_max"), ("mq_min", "mq_max")):
        a, b = f.get(minimo), f.get(massimo)
        if all(isinstance(x, (int, float)) and not isinstance(x, bool) for x in (a, b)) and b and a > b:
            segnala("filtri", minimo, a, f"e' maggiore di {massimo} ({b})")
    return problemi


def leggi_env(path: Path) -> dict[str, str]:
    valori: dict[str, str] = {}
    if not path.exists():
        return valori
    for riga in path.read_text(encoding="utf-8-sig", errors="replace").splitlines():
        riga = riga.strip()
        if not riga or riga.startswith("#"):
            continue
        if riga.lower().startswith("export "):
            riga = riga[7:].strip()
        if "=" not in riga:
            continue
        chiave, valore = riga.split("=", 1)
        chiave, valore = chiave.strip(), valore.strip()
        if valore[:1] in ("'", '"') and valore.find(valore[0], 1) > 0:   # "valore"  # commento
            valore = valore[1:valore.find(valore[0], 1)]
        else:
            valore = re.split(r"\s+#", valore, maxsplit=1)[0].strip()
        valori[chiave] = valore
    return valori


def carica_segreti(percorsi: Percorsi, cfg: dict) -> dict[str, str]:
    """Valori del file .env; le variabili d'ambiente del sistema hanno la precedenza."""
    env = leggi_env(percorsi.env)
    out = dict(env)
    for k in SEGRETI + ALTRE_VARIABILI:
        v = os.environ.get(k)
        if v:
            out[k] = v
        else:
            out.setdefault(k, "")
    # compatibilita': token e chiavi scritti in config.toml dalla prima versione
    tg = cfg.get("telegram", {}) or {}
    out["TELEGRAM_TOKEN"] = out["TELEGRAM_TOKEN"] or str(tg.get("token", "") or "").strip()
    out["TELEGRAM_CHAT_ID"] = out["TELEGRAM_CHAT_ID"] or str(tg.get("chat_id", "") or "").strip()
    ide = cfg.get("idealista", {}) or {}
    out["IDEALISTA_API_KEY"] = out["IDEALISTA_API_KEY"] or str(ide.get("api_key", "") or "").strip()
    out["IDEALISTA_API_SECRET"] = out["IDEALISTA_API_SECRET"] or str(ide.get("api_secret", "") or "").strip()
    return out


def scrivi_env(percorsi: Percorsi, valori: dict[str, str]) -> Path:
    """Aggiorna (o crea) il file .env lasciando com'e' tutto il resto."""
    p = percorsi.env
    if p.exists():
        righe = p.read_text(encoding="utf-8-sig").splitlines()
    elif (percorsi.base / ".env.example").exists():
        righe = (percorsi.base / ".env.example").read_text(encoding="utf-8-sig").splitlines()
    else:
        righe = []
    fatti = set()
    for i, r in enumerate(righe):
        m = re.match(r"^\s*(?:export\s+)?([A-Za-z_][A-Za-z0-9_]*)\s*=", r)
        if m and m.group(1) in valori and m.group(1) not in fatti:
            righe[i] = f"{m.group(1)}={valori[m.group(1)]}"
            fatti.add(m.group(1))
    for k, v in valori.items():
        if k not in fatti:
            righe.append(f"{k}={v}")
    p.write_text("\n".join(righe) + "\n", encoding="utf-8")
    return p
