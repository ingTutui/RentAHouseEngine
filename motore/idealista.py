"""Idealista: solo con l'API ufficiale (le chiavi si chiedono su developers.idealista.com/access-request)."""

import time
from datetime import datetime

import requests

from .analisi import Annuncio, aggiorna_area
from .archivio import adesso, quando
from .comuni import comune_ufficiale


def cerca(cfg: dict, segreti: dict[str, str], archivio) -> tuple[list[Annuncio], str]:
    c = cfg["idealista"]
    chiave, segreto = segreti.get("IDEALISTA_API_KEY", ""), segreti.get("IDEALISTA_API_SECRET", "")
    if not chiave or not segreto:
        return [], ""
    ultimo = quando(archivio.get("idealista_ultimo", ""))
    if ultimo and (datetime.now() - ultimo).total_seconds() / 3600 < float(c.get("ogni_ore", 12)):
        return [], ""
    try:
        token = archivio.get("idealista_token", "")
        scade = float(archivio.get("idealista_token_scade", "0") or 0)
        if not token or time.time() > scade - 300:
            r = requests.post("https://api.idealista.com/oauth/token", auth=(chiave, segreto),
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
        archivio.set("idealista_ultimo", adesso())
    except Exception as ex:
        return [], f"API Idealista: {type(ex).__name__}"
    out = []
    for el in r.json().get("elementList", []):
        testo = " ".join(str(el.get(k, "")) for k in ("address", "municipality", "district", "description"))
        comune = comune_ufficiale(str(el.get("municipality", ""))) or str(el.get("municipality", "")) or None
        a = Annuncio(uid=f"idealista:{el.get('propertyCode')}", url=el.get("url", ""), fonte="Idealista",
                     tipo_fonte="portale", comune_fonte=str(el.get("municipality", "")),
                     titolo=f"{el.get('propertyType', 'immobile')} in {el.get('address', '')}".strip(),
                     testo=testo[:800], prezzo=int(el["price"]) if el.get("price") else None,
                     mq=int(el["size"]) if el.get("size") else None,
                     locali=int(el["rooms"]) if el.get("rooms") else None,
                     comune=comune, frazione=str(el.get("district", "")) or None,
                     origine_luogo="portale" if comune else "", indirizzo=str(el.get("address", ""))[:120],
                     fonte_url="https://api.idealista.com")
        a.stanza = el.get("propertyType") == "room"
        aggiorna_area(a, cfg)
        out.append(a)
    return out, ""
