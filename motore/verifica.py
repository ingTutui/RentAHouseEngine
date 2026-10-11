"""Autoverifica veloce (python radar.py --verifica): la usa l'aggiornamento automatico prima di tenere
una versione nuova del codice. Non scarica niente da internet e non manda messaggi."""

import shutil
import tempfile
import traceback
from datetime import datetime
from pathlib import Path
from collections.abc import Callable

from .config import Percorsi, carica_config, carica_segreti

HTML_PORTALE = """<ul><li class="in-card"><a href="https://www.immobiliare.it/annunci/131929004/"
title="Bilocale via Emilia Centro, Modena">Bilocale via Emilia Centro, Modena</a><div>€ 650/mese · 2 locali · 60 m²</div></li>
<li class="in-card"><a href="/annunci/133148816/">Trilocale corso Canalgrande</a><div>€ 900/mese · 80 m²</div></li></ul>"""
HTML_AGENZIA = """<div><article><h3><a href="/immobile/bilocale-arredato-casinalbo-1234/">Bilocale arredato a Casinalbo</a></h3>
<p>58 mq · € 680/mese</p></article><a href="/contatti/">Contatti</a><a href="/privacy-policy/">Privacy</a></div>"""


def verifica(base: Path) -> int:
    errori: list[str] = []

    def prova(nome: str, funzione: Callable[[], bool]) -> None:
        try:
            if not funzione():
                errori.append(f"{nome}: risultato inatteso")
        except Exception:
            errori.append(f"{nome}: {traceback.format_exc(limit=3)}")

    percorsi = Percorsi(base)
    cfg = {}

    def config_ok() -> bool:
        nonlocal cfg
        cfg = carica_config(base)
        carica_segreti(percorsi, cfg)
        return bool(cfg.get("filtri"))

    prova("configurazione", config_ok)
    if not cfg:
        return _fine(errori)

    def fonti_ok() -> bool:
        from .fonti import carica_fonti, pattern_sbagliati
        sbagliati = pattern_sbagliati(carica_fonti(percorsi.fonti))
        if sbagliati:
            raise ValueError("pattern_link non valido in fonti_agenzie.csv: " + "; ".join(sbagliati))
        return (percorsi.fonti / "fonti_agenzie.csv").exists() and (percorsi.fonti / "fonti_portali.csv").exists()

    def estrazione_ok() -> bool:
        from .analisi import analizza
        from .estrazione import estrai
        from .fonti import Fonte
        t = estrai(HTML_PORTALE, "https://www.immobiliare.it/affitto-case/modena/?criterio=dataModifica&ordine=desc")
        if sorted(t) != ["immobiliare.it:131929004", "immobiliare.it:133148816"]:
            return False
        url, testo, titolo = t["immobiliare.it:131929004"]
        a = analizza("immobiliare.it:131929004", url, testo, titolo,
                     Fonte("portale", "Immobiliare.it", "Modena", "https://www.immobiliare.it/x"), cfg)
        if (a.prezzo, a.mq, a.locali, a.comune) != (650, 60, 2, "Modena"):
            return False
        t = estrai(HTML_AGENZIA, "https://agenzia.example/affitti/")
        if list(t) != ["https://agenzia.example/immobile/bilocale-arredato-casinalbo-1234"]:
            return False
        uid = next(iter(t))
        a = analizza(uid, *t[uid][:1], t[uid][1], t[uid][2], Fonte("agenzia", "Prova", "Modena", "https://agenzia.example/"),
                     cfg)
        return (a.prezzo, a.mq, a.comune, a.frazione) == (680, 58, "Formigine", "Casinalbo")

    def silenzio_ok() -> bool:
        from .notifiche import in_silenzio
        return in_silenzio(datetime(2026, 1, 1, 23, 30), "22:00", "08:00") and \
            not in_silenzio(datetime(2026, 1, 1, 12, 0), "22:00", "08:00")

    def archivio_ok() -> bool:
        from .archivio import Archivio
        with tempfile.TemporaryDirectory() as d:
            copia = Path(d) / "radar.db"
            if percorsi.db.exists():
                shutil.copy2(percorsi.db, copia)
            arc = Archivio(copia)  # applica le migrazioni a una copia, mai all'archivio vero
            ok = isinstance(arc.conta(), dict) and isinstance(arc.in_coda(), list)
            arc.chiudi()
            return ok

    def llm_ok() -> bool:
        from .llm import carica_modello
        carica_modello(base, cfg, carica_segreti(percorsi, cfg))
        return True

    def moduli_ok() -> bool:
        from . import aggiorna, cli, controllo, dettagli, doppioni, idealista, servizio  # noqa: F401
        return True

    for nome, f in (("fonti", fonti_ok), ("estrazione", estrazione_ok), ("orario di silenzio", silenzio_ok),
                    ("archivio", archivio_ok), ("file del modello", llm_ok), ("moduli", moduli_ok)):
        prova(nome, f)
    return _fine(errori)


def _fine(errori: list[str]) -> int:
    if errori:
        print("Verifica NON superata:")
        for e in errori:
            print(" -", e)
        return 1
    print("Verifica OK")
    return 0
