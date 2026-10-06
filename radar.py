#!/usr/bin/env python3
"""
Radar Affitti - aggregatore di annunci in affitto per l'area di Modena (RentAHouseEngine).

Comandi principali (elenco completo: python radar.py --help):
    python radar.py --telegram-setup        collega il bot Telegram (una volta, sul PC sempre acceso)
    python radar.py                         un controllo completo di tutte le fonti
    python radar.py --servizio              quello che lancia l'avvio automatico ogni 30 minuti
    python radar.py --prova "Mirantecase"   mostra cosa estrae da una fonte, senza salvare
    python radar.py --stato                 stato di fonti, coda e LLM
"""
import sys
from pathlib import Path

PYTHON_MINIMO = (3, 14)

if sys.version_info < PYTHON_MINIMO:
    # niente import del resto del programma: con un Python vecchio deve uscire solo questo messaggio
    sys.exit(f"Radar Affitti richiede Python {PYTHON_MINIMO[0]}.{PYTHON_MINIMO[1]} o piu' recente, "
             f"questo e' Python {sys.version_info[0]}.{sys.version_info[1]}.\n"
             "Scaricalo da https://www.python.org/downloads/ e rilancia il comando con quella versione "
             "(su Windows: py -3.14 radar.py ...).")

sys.path.insert(0, str(Path(__file__).resolve().parent))

from motore.cli import main  # noqa: E402

if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        print("\nFermato.")
