"""Un controllo completo: legge le fonti, riconosce gli annunci nuovi, li filtra, toglie i doppioni,
li mette in coda e consegna la coda (se non e' l'orario di silenzio)."""
from __future__ import annotations

import logging
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from datetime import date
from typing import Dict, List, Optional, Tuple

from . import doppioni, idealista
from .analisi import Annuncio, analizza, scarta
from .archivio import Archivio, adesso
from .config import Percorsi
from .dettagli import arricchisci
from .estrazione import Trovati, dominio, estrai, pagina_immobiliare, portale_di
from .fonti import Fonte, carica_fonti
from .llm import ClienteLLM
from .notifiche import Telegram, consegna
from .rete import Scaricatore

log = logging.getLogger("radar")


@dataclass
class Contesto:
    cfg: dict
    archivio: Archivio
    scaricatore: Scaricatore
    llm: Optional[ClienteLLM]
    pagine: int = 0
    conteggi: Dict[str, int] = field(default_factory=dict)

    def conta(self, chiave: str) -> None:
        self.conteggi[chiave] = self.conteggi.get(chiave, 0) + 1


def leggi_fonte(f: Fonte, scaricatore: Scaricatore, inizializzata: bool, cfg: dict) -> Tuple[Trovati, str]:
    urls = [f.url]
    if portale_di(f.url) == "immobiliare.it":
        e = cfg["esecuzione"]
        n = e["pagine_ordinarie_immobiliare"] if inizializzata else e["pagine_iniziali_immobiliare"]
        urls = [pagina_immobiliare(f.url, i) for i in range(1, max(1, int(n)) + 1)]
    trovati: Trovati = {}
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


def serve_pagina(a: Annuncio, ctx: Contesto) -> bool:
    d, f = ctx.cfg["dettagli"], ctx.cfg["filtri"]
    if not d.get("leggi_pagina_annuncio", True) or ctx.pagine >= int(d.get("max_pagine_per_controllo", 40)):
        return False
    if a.uid.startswith("idealista:"):
        return False
    if a.tipo_fonte == "agenzia":
        return True
    filtri_attivi = f.get("prezzo_max") or f.get("prezzo_min") or f.get("mq_min") or f.get("mq_max")
    return bool(d.get("anche_portali_se_mancano_dati", True) and filtri_attivi and (a.prezzo is None or a.mq is None))


def elabora(a: Annuncio, js: bool, ctx: Contesto) -> str:
    """Decide cosa fare di un annuncio mai visto. Restituisce lo stato con cui viene salvato."""
    motivo = scarta(a, ctx.cfg)
    if not motivo and serve_pagina(a, ctx):
        ctx.pagine += 1
        arricchisci(a, js, ctx.scaricatore, ctx.llm, ctx.cfg)
    motivo = motivo or scarta(a, ctx.cfg, finale=True)
    if motivo:
        ctx.archivio.salva(a, "scartato", motivo)
        ctx.conta("scartati")
        log.debug("scartato (%s): %s", motivo, a.url)
        return "scartato"
    esito = doppioni.cerca(a, ctx.archivio, ctx.llm, ctx.cfg)
    if esito.gruppo:
        ctx.archivio.salva(a, "doppione", f"doppione ({esito.certezza})", gruppo=esito.gruppo)
        ctx.conta("doppioni")
        log.info("Doppione (%s, %d punti): %s", esito.certezza, esito.punti, a.url)
        return "doppione"
    if esito.simile is not None:
        a.nota_doppione = f"Forse e' lo stesso di: {esito.simile.titolo[:70]} ({esito.simile.fonte})"
    ctx.archivio.salva(a, "da_inviare", priorita=0 if a.tipo_fonte == "agenzia" else 1)
    ctx.conta("nuovi")
    return "da_inviare"


def esegui(cfg: dict, percorsi: Percorsi, segreti: Dict[str, str], solo: str = "", nome: str = "",
           finto: bool = False, forza_invio: bool = False) -> int:
    """Un controllo completo. Restituisce il numero di annunci nuovi messi in coda."""
    percorsi.prepara()
    fonti = carica_fonti(percorsi.fonti)
    if solo:
        fonti = [f for f in fonti if f.tipo == solo]
    if nome:
        fonti = [f for f in fonti if nome.lower() in f.nome.lower() or nome.lower() in f.url.lower()]
    archivio = Archivio(percorsi.db)
    if not archivio.get("primo_avvio"):
        archivio.set("primo_avvio", adesso())
    tg = Telegram(segreti, finto=finto)
    scaricatore = Scaricatore(cfg)
    llm = ClienteLLM(cfg, segreti, archivio, percorsi.base)
    ctx = Contesto(cfg, archivio, scaricatore, llm)
    e = cfg["esecuzione"]
    stati = {f.url: archivio.fonte(f.url) for f in fonti}
    log.info("Controllo di %d fonti · %s", len(fonti), llm.stato())
    # 1) lettura: siti diversi in parallelo, stesso sito in sequenza; i siti in JavaScript alla fine
    gruppi: Dict[str, List[Fonte]] = {}
    for f in fonti:
        if not (f.js and e["usa_browser_per_js"]):
            gruppi.setdefault(dominio(f.url), []).append(f)
    letture: Dict[str, Tuple[Trovati, str]] = {}

    def leggi_gruppo(lista: List[Fonte]):
        return [(f.url, leggi_fonte(f, scaricatore, stati[f.url]["inizializzata"], cfg)) for f in lista]

    try:
        with ThreadPoolExecutor(max_workers=max(1, int(e.get("connessioni_parallele", 6)))) as pool:
            for esiti in pool.map(leggi_gruppo, gruppi.values()):
                letture.update(dict(esiti))
        for f in fonti:
            if f.url not in letture:
                letture[f.url] = leggi_fonte(f, scaricatore, stati[f.url]["inizializzata"], cfg)
        # 2) elaborazione: prima i portali (Immobiliare.it in testa), poi le agenzie, cosi' i confronti
        #    "gia' visto altrove" usano i dati piu' freschi
        ordine = sorted(fonti, key=lambda f: (0 if portale_di(f.url) == "immobiliare.it" else
                                              1 if f.tipo == "portale" else 2))
        for f in ordine:
            stato = stati[f.url]
            trovati, errore = letture[f.url]
            if errore:
                archivio.esito_fonte(f.url, f.nome, errore, 0)
                log.info("KO  %-40s %s", f.nome[:40], errore)
                continue
            archivio.esito_fonte(f.url, f.nome, "", len(trovati))
            prima_volta = not stato["inizializzata"]
            nuovi_uid = [u for u in trovati if not archivio.visto(u)]
            archivio.tocca([u for u in trovati if u not in set(nuovi_uid)])
            vecchi_presenti = len(trovati) - len(nuovi_uid)
            cambio_sito = (not prima_volta and f.tipo == "agenzia" and stato["ultimi_link"] >= 5
                           and len(nuovi_uid) >= 5 and vecchi_presenti <= 0.2 * stato["ultimi_link"])
            if cambio_sito:
                log.warning("%s: quasi tutti i link sono cambiati (sito rifatto?): li salvo senza avvisarti",
                            f.nome)
            esiti: Dict[str, int] = {}
            for uid in nuovi_uid:
                url, testo, titolo = trovati[uid]
                a = analizza(uid, url, testo, titolo, f, cfg)
                if (prima_volta and not e["notifica_prima_esecuzione"]) or cambio_sito:
                    archivio.salva(a, "base", scarta(a, cfg) or ("prima lettura" if prima_volta else "sito cambiato"))
                    esiti["base"] = esiti.get("base", 0) + 1
                    continue
                s = elabora(a, f.js, ctx)
                esiti[s] = esiti.get(s, 0) + 1
            archivio.commit()
            log.info("OK  %-40s %3d annunci, %d nuovi%s", f.nome[:40], len(trovati), esiti.get("da_inviare", 0),
                     (" (prima lettura: salvati senza avvisare)" if prima_volta else "") +
                     (f", {esiti['doppione']} gia' visti altrove" if esiti.get("doppione") else "") +
                     (f", {esiti['scartato']} scartati dai filtri" if esiti.get("scartato") else ""))
        if not solo or solo == "portale":
            extra, errore = idealista.cerca(cfg, segreti, archivio)
            if errore:
                log.info("KO  Idealista API: %s", errore)
            for a in extra:
                if not archivio.visto(a.uid):
                    elabora(a, False, ctx)
            archivio.commit()
    finally:
        scaricatore.chiudi()
    nuovi = ctx.conteggi.get("nuovi", 0)
    inviati = consegna(archivio, tg, cfg, forza=forza_invio)
    oggi = date.today().isoformat()
    if archivio.get("ultima_pulizia") != oggi:
        archivio.pulisci()
        archivio.set("ultima_pulizia", oggi)
    log.info("Fine controllo: %d nuovi in coda, %d segnalati ora, %d doppioni, %d scartati, %d pagine annuncio lette",
             nuovi, inviati, ctx.conteggi.get("doppioni", 0), ctx.conteggi.get("scartati", 0), ctx.pagine)
    archivio.chiudi()
    return nuovi
