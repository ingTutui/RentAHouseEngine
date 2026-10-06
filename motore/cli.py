"""Comandi del radar (python radar.py --help)."""
from __future__ import annotations

import argparse
import csv
import logging
import random
import re
import sys
import time
from datetime import date
from logging.handlers import RotatingFileHandler
from pathlib import Path

import requests

from . import __version__
from .config import Percorsi, carica_config, carica_segreti, scrivi_env

log = logging.getLogger("radar")


def configura_log(percorsi: Percorsi, verboso: bool) -> None:
    percorsi.prepara()
    log.setLevel(logging.DEBUG if verboso else logging.INFO)
    if log.handlers:
        return
    fmt = logging.Formatter("%(asctime)s %(message)s", "%d/%m %H:%M")
    fh = RotatingFileHandler(percorsi.log, maxBytes=1_000_000, backupCount=3, encoding="utf-8")
    fh.setFormatter(fmt)
    log.addHandler(fh)
    if sys.stdout is not None:  # con pythonw (avvio automatico su Windows) non c'e' console
        sh = logging.StreamHandler(sys.stdout)
        sh.setFormatter(fmt)
        log.addHandler(sh)


def prova_fonte(cfg: dict, percorsi: Percorsi, segreti: dict, nome: str, dettagli: bool) -> None:
    from .analisi import analizza, scarta
    from .archivio import Archivio
    from .dettagli import arricchisci
    from .estrazione import estrai
    from .fonti import carica_fonti
    from .llm import ClienteLLM
    from .notifiche import riga_dati
    from .rete import Scaricatore
    fonti = [f for f in carica_fonti(percorsi.fonti) if nome.lower() in f.nome.lower() or nome.lower() in f.url.lower()]
    if not fonti:
        print(f"Nessuna fonte contiene '{nome}'")
        return
    s = Scaricatore(cfg)
    archivio = Archivio(percorsi.db) if dettagli else None
    llm = ClienteLLM(cfg, segreti, archivio, percorsi.base) if dettagli else None
    if llm is not None:
        print(llm.stato())
    try:
        for f in fonti[:5]:
            print(f"\n== {f.nome} ({f.comune})  {f.url}  js={'sì' if f.js else 'no'}")
            html, errore = s.scarica(f.url, js=f.js)
            if html is None:
                print("   errore:", errore)
                continue
            trovati = estrai(html, f.url, f.pattern)
            print(f"   {len(trovati)} link ad annunci")
            for i, (uid, (url, testo, titolo)) in enumerate(list(trovati.items())[:15]):
                a = analizza(uid, url, testo, titolo, f, cfg)
                if dettagli and i < 3 and not scarta(a, cfg):
                    arricchisci(a, f.js, s, llm, cfg)
                motivo = scarta(a, cfg, finale=dettagli and i < 3)
                luogo = f" · {a.comune}" + (f" ({a.frazione})" if a.frazione else "") if a.comune else ""
                print(f"   - {riga_dati(a)}{luogo} {('[' + motivo + '] ') if motivo else ''}{a.titolo[:60]}\n     {url}")
                if a.dettagli:
                    print(f"     dati dalla pagina ({a.dettagli}): {a.riassunto or '-'} {a.note or ''}")
    finally:
        s.chiudi()
        if archivio is not None:
            archivio.chiudi()


def mostra_stato(cfg: dict, percorsi: Percorsi, segreti: dict) -> None:
    from .archivio import Archivio
    from .llm import ClienteLLM
    a = Archivio(percorsi.db)
    conta = a.conta()
    print(f"Radar Affitti {__version__}")
    dettaglio = ", ".join(f"{k or '?'}: {v}" for k, v in sorted(conta.items()))
    print("Annunci in archivio:", sum(conta.values()), f"({dettaglio})" if dettaglio else "")
    print("In coda per Telegram:", conta.get("da_inviare", 0), "| ultimo messaggio:", a.get("ultimo_invio") or "mai")
    llm = ClienteLLM(cfg, segreti, a, percorsi.base)
    chiamate, costo = a.uso_llm(date.today().isoformat())
    print(llm.stato(), f"| oggi {chiamate} chiamate, {costo:.4f} $")
    fallito = a.get("aggiornamento_fallito")
    if fallito:
        print("Ultimo aggiornamento non riuscito:", fallito)
    print("\nFonti con problemi:")
    righe = a.db.execute("""SELECT nome, url, errori_consecutivi, problema_dal, nota FROM fonti
                            WHERE problema_dal != '' ORDER BY problema_dal""").fetchall()
    for nome, url, n, dal, nota in righe:
        print(f"  dal {dal}  {nome[:40]:40} {nota[:70]}  ({n} errori di fila)\n      {url}")
    if not righe:
        print("  nessuna")
    print("\nFonti senza annunci all'ultimo controllo:")
    vuote = a.db.execute("SELECT nome,url FROM fonti WHERE errori_consecutivi=0 AND ultimi_link=0 AND problema_dal=''").fetchall()
    for nome, url in vuote:
        print(f"  {nome[:40]:40} {url}")
    if not vuote:
        print("  nessuna")
    a.chiudi()


def mostra_coda(percorsi: Percorsi) -> None:
    from .archivio import Archivio
    from .notifiche import riga_dati
    a = Archivio(percorsi.db)
    coda = a.in_coda()
    print(f"{len(coda)} annunci in coda")
    for x in coda:
        print(f"  {x.trovato_il}  {riga_dati(x):40} {x.fonte[:30]:30} {x.url}")
    a.chiudi()


def esporta(percorsi: Percorsi) -> None:
    from .archivio import Archivio
    a = Archivio(percorsi.db)
    righe = a.db.execute("""SELECT primo_visto, stato, fonte, comune, titolo, prezzo, mq, locali, url, scartato, gruppo,
                            inviato_il FROM annunci ORDER BY primo_visto DESC""").fetchall()
    with open(percorsi.esportazione, "w", encoding="utf-8-sig", newline="") as f:
        w = csv.writer(f, delimiter=";")
        w.writerow(["primo_visto", "stato", "fonte", "comune", "titolo", "prezzo", "mq", "locali", "url", "motivo",
                    "gruppo", "inviato_il"])
        w.writerows(righe)
    a.chiudi()
    print(f"Salvati {len(righe)} annunci in {percorsi.esportazione}")


def telegram_setup(percorsi: Percorsi, segreti: dict) -> None:
    base = (segreti.get("TELEGRAM_API_URL") or "https://api.telegram.org").rstrip("/")
    print("1) Su Telegram apri @BotFather, scrivi /newbot e segui le istruzioni.")
    token = input("2) Incolla qui il token che ti ha dato BotFather: ").strip()
    if not re.match(r"^\d+:[\w-]{30,}$", token):
        print("Il token non sembra valido (formato 123456:ABC...). Riprova.")
        return
    input("3) Apri il tuo nuovo bot su Telegram, premi Avvia e scrivi 'ciao'. Poi premi Invio qui... ")
    r = requests.get(f"{base}/bot{token}/getUpdates", timeout=20).json()
    chats = [u["message"]["chat"]["id"] for u in r.get("result", []) if "message" in u]
    if not chats:
        print("Non vedo messaggi: scrivi qualcosa al bot e rilancia il comando.")
        return
    chat_id = str(chats[-1])
    p = scrivi_env(percorsi, {"TELEGRAM_TOKEN": token, "TELEGRAM_CHAT_ID": chat_id})
    requests.post(f"{base}/bot{token}/sendMessage", timeout=20,
                  data={"chat_id": chat_id, "text": "Radar Affitti collegato. Da ora i nuovi annunci arrivano qui."})
    print(f"Fatto: token e chat {chat_id} salvati in {p.name} (resta solo su questo PC). Ti ho mandato un messaggio di prova.")


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Radar Affitti: nuovi annunci in affitto da agenzie e portali")
    ap.add_argument("--cartella", default=str(Path(__file__).resolve().parent.parent),
                    help="cartella del radar (config.toml, fonti/, stato/)")
    ap.add_argument("--servizio", action="store_true",
                    help="per l'avvio automatico: aggiorna da GitHub (se attivo) e fa un controllo")
    ap.add_argument("--loop", type=float, metavar="MINUTI", help="ripete il controllo ogni N minuti")
    ap.add_argument("--solo", choices=["agenzia", "portale"], help="controlla solo siti delle agenzie o solo portali")
    ap.add_argument("--fonte", default="", help="controlla solo le fonti il cui nome o URL contiene questo testo")
    ap.add_argument("--prova", metavar="NOME", help="mostra cosa estrae da una fonte, senza salvare nulla")
    ap.add_argument("--dettagli", action="store_true", help="con --prova: legge anche le pagine dei primi 3 annunci")
    ap.add_argument("--prova-llm", action="store_true", help="controlla chiave OpenRouter e modello")
    ap.add_argument("--senza-telegram", action="store_true", help="mostra i messaggi a schermo invece di inviarli")
    ap.add_argument("--invia-ora", action="store_true", help="invia la coda anche se e' l'orario di silenzio")
    ap.add_argument("--telegram-setup", action="store_true", help="collega il bot Telegram")
    ap.add_argument("--stato", action="store_true", help="stato di fonti, coda e LLM")
    ap.add_argument("--coda", action="store_true", help="annunci in attesa di essere inviati")
    ap.add_argument("--esporta", action="store_true", help="esporta gli annunci visti in stato/annunci_visti.csv")
    ap.add_argument("--verifica", action="store_true", help="autoverifica del codice (usata dagli aggiornamenti)")
    ap.add_argument("--installa-avvio", action="store_true", help="controllo automatico ogni 30 minuti su questo PC")
    ap.add_argument("--togli-avvio", action="store_true", help="toglie il controllo automatico da questo PC")
    ap.add_argument("--controllo-interno", action="store_true", help=argparse.SUPPRESS)
    ap.add_argument("-v", "--verboso", action="store_true")
    args = ap.parse_args(argv)
    for flusso in (sys.stdout, sys.stderr):  # console di Windows: niente errori su caratteri particolari
        try:
            flusso.reconfigure(errors="replace")
        except (AttributeError, ValueError):
            pass
    base = Path(args.cartella).resolve()
    percorsi = Percorsi(base)
    if args.verifica:
        from .verifica import verifica
        return verifica(base)
    if args.installa_avvio or args.togli_avvio:
        from . import avvio
        if args.togli_avvio:
            return avvio.togli()
        minuti = int(carica_config(base)["esecuzione"].get("intervallo_minuti", 30))
        return avvio.installa(base, max(10, minuti))
    percorsi.prepara()
    cfg = carica_config(base)
    segreti = carica_segreti(percorsi, cfg)
    if args.telegram_setup:
        telegram_setup(percorsi, segreti)
        return 0
    if args.stato:
        mostra_stato(cfg, percorsi, segreti)
        return 0
    if args.coda:
        mostra_coda(percorsi)
        return 0
    if args.esporta:
        esporta(percorsi)
        return 0
    configura_log(percorsi, args.verboso)
    if args.prova_llm:
        from .archivio import Archivio
        from .llm import ClienteLLM, prova
        arc = Archivio(percorsi.db)
        print(prova(ClienteLLM(cfg, segreti, arc, base)))
        arc.chiudi()
        return 0
    if args.prova:
        prova_fonte(cfg, percorsi, segreti, args.prova, args.dettagli)
        return 0
    from .controllo import esegui
    if args.servizio:
        from .servizio import servizio
        try:
            return servizio(cfg, percorsi, segreti, finto=args.senza_telegram)
        except Exception:
            log.exception("Errore imprevisto durante il controllo")
            return 1
    if not args.loop:
        esegui(cfg, percorsi, segreti, args.solo or "", args.fonte, args.senza_telegram, args.invia_ora)
        return 0
    minuti = max(10.0, args.loop)
    log.info("Modalita' continua: un controllo ogni %.0f minuti (Ctrl+C per fermare)", minuti)
    while True:
        try:
            cfg = carica_config(base)          # le modifiche a config.toml valgono dal giro successivo
            segreti = carica_segreti(percorsi, cfg)
            esegui(cfg, percorsi, segreti, args.solo or "", args.fonte, args.senza_telegram)
        except KeyboardInterrupt:
            raise
        except Exception:
            log.exception("Errore imprevisto durante il controllo: riprovo al prossimo giro")
        attesa = minuti * 60 * random.uniform(0.85, 1.15)
        log.info("Prossimo controllo tra %.0f minuti", attesa / 60)
        time.sleep(attesa)
