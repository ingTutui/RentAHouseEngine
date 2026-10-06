"""Riconoscimento del comune di un annuncio e controllo dell'area di ricerca."""
from __future__ import annotations

import re
import unicodedata
from functools import lru_cache
from typing import Dict, Iterable, Optional, Tuple

from ._dati_comuni import COMUNI, FRAZIONI

# nomi brevi o scritti in modo diverso da quello ufficiale ISTAT
ALIAS = {
    "reggio emilia": "Reggio nell'Emilia",
    "reggio nell emilia": "Reggio nell'Emilia",
    "castelfranco": "Castelfranco Emilia",
    "san cesario": "San Cesario sul Panaro",
    "castelvetro": "Castelvetro di Modena",
    "fiorano": "Fiorano Modenese",
    "castelnuovo": "Castelnuovo Rangone",
    "pavullo": "Pavullo nel Frignano",
    "savignano": "Savignano sul Panaro",
    "marano": "Marano sul Panaro",
    "san felice": "San Felice sul Panaro",
    "concordia": "Concordia sulla Secchia",
    "prignano": "Prignano sulla Secchia",
    "sant ilario": "Sant'Ilario d'Enza",
    "castelnovo monti": "Castelnovo ne' Monti",
    "castelnovo ne monti": "Castelnovo ne' Monti",
    "persiceto": "San Giovanni in Persiceto",
    "casalecchio": "Casalecchio di Reno",
}

# province scritte accanto al comune: non sono il luogo dell'annuncio
_RUMORE = re.compile(r"\b(?:provincia di|prov\.? di|prov\.?) (?:modena|reggio emilia|reggio nell emilia|bologna)\b"
                     r"|\b(?:modena|reggio emilia|bologna) e provincia\b|\((?:mo|re|bo)\)")


def normalizza(testo: str) -> str:
    """minuscolo, senza accenti, apostrofi e trattini uniformati."""
    t = unicodedata.normalize("NFKD", testo or "")
    t = "".join(c for c in t if not unicodedata.combining(c))
    t = t.lower().replace("’", "'").replace("`", "'").replace("´", "'")
    t = re.sub(r"[-_/]", " ", t)
    t = t.replace("'", " ")
    return re.sub(r"\s+", " ", t).strip()


@lru_cache(maxsize=1)
def _indice() -> Tuple[Dict[str, Tuple[str, Optional[str]]], "re.Pattern[str]"]:
    nomi: Dict[str, Tuple[str, Optional[str]]] = {}
    for fr, com in FRAZIONI.items():
        nomi[normalizza(fr)] = (com, fr)
    for alias, com in ALIAS.items():
        nomi[normalizza(alias)] = (com, None)
    for com in COMUNI:
        nomi[normalizza(com)] = (com, None)
    chiavi = sorted(nomi, key=len, reverse=True)
    regex = re.compile(r"\b(?:" + "|".join(re.escape(k) for k in chiavi) + r")\b")
    return nomi, regex


def comune_ufficiale(nome: str) -> Optional[str]:
    """Nome ISTAT del comune (accetta frazioni e nomi brevi). None se non lo conosce."""
    n = normalizza(nome)
    if not n:
        return None
    nomi, _ = _indice()
    if n in nomi:
        return nomi[n][0]
    for pezzo in re.split(r"[,(]| \d", n):  # "Formigine (MO)", "Modena, zona Sacca"
        pezzo = pezzo.strip()
        if pezzo in nomi:
            return nomi[pezzo][0]
    return None


def trova_luogo(*testi: str) -> Tuple[Optional[str], Optional[str]]:
    """(comune, frazione) del primo luogo citato: prima nel titolo, poi nel resto del testo."""
    nomi, regex = _indice()
    for testo in testi:
        t = _RUMORE.sub(" ", normalizza(testo))
        m = regex.search(t)
        if m:
            return nomi[m.group(0)]
    return None, None


def _insieme(nomi: Iterable[str]) -> set:
    return {normalizza(x) for x in nomi or [] if x}


def valuta_area(comune: Optional[str], frazione: Optional[str], testo: str, filtri: dict
                ) -> Tuple[str, str]:
    """Dove si trova l'annuncio rispetto all'area di ricerca.

    Restituisce (esito, dettaglio) con esito:
      "dentro"      comune dell'area
      "fuori"       comune conosciuto ma fuori dall'area (o frazione esclusa)
      "incerto"     il testo cita una zona esclusa, o e' un comune di cui conta solo qualche frazione
      "sconosciuto" comune non riconosciuto
    """
    area = _insieme(filtri.get("comuni_area"))
    parziali = {normalizza(k): v for k, v in (filtri.get("frazioni_area") or {}).items()}
    zone_fuori = list(filtri.get("zone_fuori_area") or [])
    t = normalizza(testo)

    def cita(zone) -> str:
        for z in zone:
            if z and re.search(r"\b" + re.escape(normalizza(z)) + r"\b", t):
                return z
        return ""

    c = normalizza(comune_ufficiale(comune or "") or comune or "")
    f = normalizza(frazione or "")
    nomi, _ = _indice()
    if not f and comune and nomi.get(normalizza(comune), ("", None))[1]:
        f = normalizza(nomi[normalizza(comune)][1] or "")  # e' stata indicata una frazione come comune
    if not area:  # area non configurata: niente controlli sul comune, solo le zone escluse
        z = cita(zone_fuori)
        return ("incerto", z) if z else ("sconosciuto", "")
    if c and c in area:
        if f and f in _insieme(zone_fuori):
            return "fuori", frazione or ""
        z = cita(zone_fuori)
        return ("incerto", z) if z else ("dentro", "")
    if c and c in parziali:
        ammesse = parziali[c]
        if f and f in _insieme(ammesse):
            return "dentro", ""
        if cita([x for x in ammesse if normalizza(x) not in ("bagno", "prato")]):
            return "dentro", ""
        return "incerto", comune or ""
    if c and comune_ufficiale(c):
        return "fuori", comune_ufficiale(c) or comune or ""
    z = cita(zone_fuori)
    return ("incerto", z) if z else ("sconosciuto", "")
