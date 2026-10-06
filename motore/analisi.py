"""Dati di un annuncio (prezzo, metratura, locali, comune) e filtri."""
from __future__ import annotations

import re
from dataclasses import asdict, dataclass, fields
from typing import List, Optional
from urllib.parse import urlparse

from .comuni import comune_ufficiale, trova_luogo, valuta_area
from .estrazione import NOMI_PORTALI, portale_di
from .fonti import Fonte

RE_EURO = re.compile(
    r"(?:€|euro\b|eur\b)\s*(\d{1,3}(?:[.\s']\d{3})+|\d+)(?:,\d{1,2})?"
    r"|(\d{1,3}(?:[.\s']\d{3})+|\d+)(?:,\d{1,2})?\s*(?:€|euro\b|eur\b)", re.I)
RE_MQ = re.compile(r"(\d{2,4})(?:[.,]\d+)?\s*(?:mq|m²|m2|mt2|metri\s*quadr)", re.I)
RE_LOCALI = re.compile(r"(\d)\s*(?:locali|vani)\b", re.I)
PAROLE_LOCALI = {"monolocale": 1, "bilocale": 2, "trilocale": 3, "quadrilocale": 4, "pentalocale": 5}
PAROLE_AFFITTO = ("affitt", "locazion", "/mese", "al mese", "mensil", "canone", "/mm")
RE_STANZA = re.compile(
    r"^\W*(?:stanza|stanze|camera|camere|posto letto|posti letto)\b"
    r"|\b(?:affitt(?:o|asi|iamo)|offro|offresi)\s+(?:una\s+)?(?:stanz[ae]|camer[ae]|posto letto|posti letto)\b"
    r"|\b(?:stanz[ae]|camer[ae])\s+(?:singol[ae]\s+|doppi[ae]\s+)?(?:in affitto|a studenti|per studenti|a studentesse)\b"
    r"|\bposti? letto\b", re.I)
TIPOLOGIE_NON_ABITATIVE = ("commerciale/ufficio", "garage/posto auto", "terreno")


@dataclass
class Annuncio:
    uid: str
    url: str
    fonte: str                       # nome mostrato: agenzia o portale
    tipo_fonte: str                  # "agenzia" | "portale"
    comune_fonte: str                # comune della pagina del portale o sede dell'agenzia
    titolo: str
    testo: str                       # testo della scheda nell'elenco
    prezzo: Optional[int] = None
    mq: Optional[int] = None
    locali: Optional[int] = None
    stanza: bool = False
    vendita: bool = False
    comune: Optional[str] = None     # comune dell'immobile
    frazione: Optional[str] = None
    origine_luogo: str = ""          # "portale" | "dettaglio" | "testo"
    area: str = ""                   # dentro | fuori | incerto | sconosciuto
    zona_avviso: str = ""
    spese: Optional[int] = None
    arredato: str = ""
    disponibile: str = ""
    indirizzo: str = ""
    riassunto: str = ""
    note: str = ""
    contratto: str = ""
    tipologia: str = ""
    non_annuncio: bool = False
    dettagli: str = ""               # "llm" | "pagina" | "" (da dove vengono i dati in piu')
    fonte_url: str = ""
    trovato_il: str = ""
    nota_doppione: str = ""

    def come_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def da_dict(cls, d: dict) -> "Annuncio":
        nomi = {f.name for f in fields(cls)}
        return cls(**{k: v for k, v in d.items() if k in nomi})


def importi(testo: str) -> List[int]:
    out = []
    for g1, g2 in RE_EURO.findall(testo or ""):
        cifra = re.sub(r"[.\s']", "", g1 or g2)
        if cifra.isdigit():
            out.append(int(cifra))
    return out


def leggi_numeri(testo: str, url: str, cfg: dict) -> dict:
    """Prezzo, metratura e locali letti con le espressioni regolari."""
    plaus = int(cfg["filtri"].get("prezzo_affitto_plausibile_max", 6000))
    basso = (testo or "").lower()
    valori = importi(testo)
    affitti = [v for v in valori if 150 <= v <= plaus]
    prezzo = affitti[0] if affitti else None
    m = RE_MQ.search(testo or "")
    mq = int(m.group(1)) if m else None
    locali = None
    for parola, n in PAROLE_LOCALI.items():
        if parola in basso:
            locali = n
            break
    if locali is None:
        m = RE_LOCALI.search(testo or "")
        if m:
            locali = int(m.group(1))
    parla_di_affitto = any(k in basso or k in url.lower() for k in PAROLE_AFFITTO)
    vendita = (not parla_di_affitto) and bool(valori) and prezzo is None and max(valori) > plaus
    if not vendita and ("vendita" in url.lower() or "/vendite/" in url.lower()) and not parla_di_affitto:
        vendita = True
    return {"prezzo": prezzo, "mq": mq, "locali": locali, "vendita": vendita}


def e_stanza(titolo: str, testo: str, url: str) -> bool:
    if "/camere-posti-letto/" in url.lower() or "/stanze/" in url.lower():
        return True
    return bool(RE_STANZA.search(titolo or "")) or bool(RE_STANZA.search((testo or "")[:200]))


def analizza(uid: str, url: str, testo: str, titolo: str, fonte: Fonte, cfg: dict) -> Annuncio:
    tutto = f"{titolo} {testo}"
    n = leggi_numeri(tutto, url, cfg)
    nome_fonte = fonte.nome if fonte.tipo == "agenzia" else NOMI_PORTALI.get(portale_di(url) or "", fonte.nome)
    a = Annuncio(uid=uid, url=url, fonte=nome_fonte, tipo_fonte=fonte.tipo, comune_fonte=fonte.comune,
                 titolo=(titolo or testo[:120]).strip(), testo=testo, prezzo=n["prezzo"], mq=n["mq"],
                 locali=n["locali"], vendita=n["vendita"], stanza=e_stanza(titolo, testo, url),
                 fonte_url=fonte.url)
    if fonte.tipo == "portale" and fonte.comune:
        # il comune della pagina del portale e' sicuro; le frazioni citate nel testo restano un avviso
        a.comune, a.origine_luogo = comune_ufficiale(fonte.comune) or fonte.comune, "portale"
    else:
        comune, frazione = trova_luogo(titolo, testo)
        if comune:
            a.comune, a.frazione, a.origine_luogo = comune, frazione, "testo"
    aggiorna_area(a, cfg)
    return a


def aggiorna_area(a: Annuncio, cfg: dict) -> None:
    testo = " ".join(x for x in (a.titolo, a.testo, a.indirizzo, a.riassunto) if x)
    a.area, a.zona_avviso = valuta_area(a.comune, a.frazione, testo, cfg["filtri"])


def scarta(a: Annuncio, cfg: dict, finale: bool = False) -> str:
    """Motivo per cui l'annuncio non interessa ('' = va bene).
    finale=True quando i dati sono quelli definitivi (dopo l'eventuale lettura della pagina dell'annuncio)."""
    f = cfg["filtri"]
    if a.non_annuncio:
        return "non e' un annuncio"
    if a.vendita or a.contratto == "vendita":
        return "vendita"
    if a.tipologia in TIPOLOGIE_NON_ABITATIVE:
        return f"tipo {a.tipologia}"
    inizio = (a.titolo or a.testo)[:80].lower().strip()
    path = urlparse(a.url).path.lower()
    for tipo in f.get("tipi_esclusi", []) or []:
        t = tipo.lower()
        if inizio.startswith(t) or f"/{t.replace(' ', '-')}" in path:
            return f"tipo {tipo}"
    if any(k in path for k in ("uffici-locali-commerciali", "box-garage", "terreni", "negozi")):
        return "non residenziale"
    if f.get("escludi_stanze") and (a.stanza or a.tipologia == "stanza o posto letto"):
        return "stanza"
    if a.prezzo is not None:
        if f.get("prezzo_max") and a.prezzo > f["prezzo_max"]:
            return "prezzo alto"
        if f.get("prezzo_min") and a.prezzo < f["prezzo_min"]:
            return "prezzo basso"
    if a.mq is not None:
        if f.get("mq_min") and a.mq < f["mq_min"]:
            return "metratura piccola"
        if f.get("mq_max") and a.mq > f["mq_max"]:
            return "metratura grande"
    if f.get("locali_min") and a.locali is not None and a.locali < f["locali_min"]:
        return "pochi locali"
    if a.area == "fuori" and a.origine_luogo in ("portale", "dettaglio") \
            and str(f.get("fuori_zona", "scarta")).lower() == "scarta":
        return f"fuori zona ({a.zona_avviso or a.comune})"
    if finale and str(f.get("dati_mancanti", "invia")).lower() == "scarta":
        if a.prezzo is None and (f.get("prezzo_max") or f.get("prezzo_min")):
            return "prezzo non indicato"
        if a.mq is None and (f.get("mq_min") or f.get("mq_max")):
            return "metratura non indicata"
    return ""
