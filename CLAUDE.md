# RentAHouseEngine (Radar Affitti)

Aggregatore personale di annunci di affitto per la zona di Modena e Reggio. Ogni 30 minuti legge i siti di oltre
120 agenzie e i portali (Immobiliare.it, Casa.it, Subito, Wikicasa) e manda su Telegram solo gli annunci nuovi che
rispettano i filtri. Scopo: vedere gli annunci che le agenzie pubblicano sul proprio sito prima (o senza) che
arrivino su Immobiliare.it.

Python 3.14. Tutto in italiano: codice, commenti, messaggi, commit.

Una sessione nuova non ha memoria di come si è arrivati qui: **leggi PIANO.md prima di cambiare qualcosa**
(requisiti, decisioni e perché, stato, prossimi passi). README.md spiega l'uso. La cartella `contesto/`, se c'è,
è solo su questo PC (non è su git): `contesto/INDICE.md` dice cosa contiene (le richieste originali dell'utente,
l'area di ricerca, le mappe, gli elenchi delle agenzie).

## Come lavorare con l'utente

- Scrive in italiano, a volte con refusi: interpretali. Rispondi in italiano, breve e concreto.
- Se una scelta cambia il comportamento (filtri, orari, contenuto dei messaggi) chiedi prima.
- Questo è il PC di sviluppo, spesso spento. Il radar vero gira su un altro PC, sempre acceso. Qui lascia vuoti
  `TELEGRAM_TOKEN` e `TELEGRAM_CHAT_ID` in `.env` (i messaggi compaiono solo a schermo) e non installare l'avvio
  automatico. Se l'utente dice che questo è invece il PC sempre acceso, segui la sezione «Installazione sul PC
  sempre acceso» del README.

## Comandi

```
python -m pip install -r requirements-dev.txt
python -m pytest                  # 60 test, circa 40 secondi, nessuna rete (siti, Telegram e OpenRouter finti)
python -m ruff check .
python radar.py --verifica        # autoverifica: la usa anche l'aggiornamento automatico
python radar.py --stato           # archivio, coda, LLM, fonti con problemi
python radar.py --prova "nome"    # cosa estrae da una fonte, senza salvare (con --dettagli legge anche le schede)
python radar.py --senza-telegram  # un controllo con i messaggi a schermo
python radar.py --prova-llm       # controlla chiave OpenRouter e modello
```

`python` è il Python 3.14 (su Windows: `py -3.14`).

## Regole

- Prima di ogni commit `pytest` e `ruff` devono passare. Se aggiungi un comportamento, aggiungi il test.
- Modifiche solo su un ramo (`git switch -c nome`) e pull request verso `main`. Mai push diretto su `main` (unica
  eccezione: il primo push della versione 1 nel repository ancora vuoto, PIANO.md sezione 7), mai `--force`. Il
  merge lo fa l'utente. Il PC sempre acceso scarica da solo `main` (motore/aggiorna.py) e lo tiene solo
  se `--verifica` passa: `main` deve restare sempre funzionante.
- Segreti (token Telegram, chiave OpenRouter, chiavi Idealista) solo in `.env`: mai nel codice, nei test, nei log,
  nei messaggi di commit; non stampare i valori. `.gitignore` tiene fuori `.env`, `config.local.toml`, `stato/` e
  `contesto/`: non aggiungerli a git.
- LLM solo tramite OpenRouter (motore/llm.py). Il modello si sceglie in `llm.toml` (o nel file indicato da
  `LLM_CONFIG`), mai nel codice. Il radar deve funzionare anche senza LLM.
- Rispetta `robots.txt` e le pause tra richieste (motore/rete.py): non toglierli. Idealista solo con l'API
  ufficiale, Facebook mai.
- Non disattivare la verifica TLS e non aggirare i blocchi di rete: se una richiesta è negata, segnalalo.
- Dipendenze: libreria standard più requests, beautifulsoup4 e lxml (Playwright è facoltativo). Chiedi prima di
  aggiungerne altre. Versioni minime in `requirements.txt`.
- Python 3.14: `tomllib` della libreria standard, annotazioni di tipo native, niente
  `from __future__ import annotations`.
- I file `.bat` hanno fine riga CRLF (lo gestisce `.gitattributes`): non crearli con `echo` o `printf` di shell.
- Commit in italiano: una riga di titolo e, se serve, la spiegazione sotto.

## Mappa del codice (motore/)

| Modulo | Cosa fa |
|---|---|
| cli.py | Comandi (`python radar.py --help`) |
| controllo.py | Un controllo completo: legge le fonti, riconosce i nuovi, filtra, toglie i doppioni, mette in coda, consegna |
| fonti.py, rete.py | Fonti da `fonti/*.csv`; scaricamento con robots.txt, pause, browser per i siti in JavaScript |
| estrazione.py | Link agli annunci in una pagina di elenco (portali e siti delle agenzie) |
| analisi.py, comuni.py | Dati dell'annuncio (prezzo, mq, locali, comune) e filtri; comune, frazione e area di ricerca |
| dettagli.py | Dati dalla scheda dell'annuncio quando l'elenco non basta (dati strutturati, poi LLM, poi testo) |
| doppioni.py | Lo stesso immobile su più fonti o ripubblicato |
| archivio.py | SQLite in `stato/radar.db`: annunci, coda, salute delle fonti, uso dell'LLM |
| notifiche.py | Messaggi Telegram, orario di silenzio, coda, avvisi sulle fonti rotte |
| llm.py | Client OpenRouter (JSON strutturato, limiti di spesa, cache) |
| idealista.py | API ufficiale di Idealista (serve la chiave) |
| config.py | `config.toml` + `config.local.toml` + `.env` |
| aggiorna.py, servizio.py, verifica.py, avvio.py | Aggiornamento automatico da GitHub, modalità servizio, autoverifica, avvio automatico (Windows, cron) |

I test sono in `tests/` (uno per area, `conftest.py` ha i server finti).

## Stato

Versione 2 completa e collaudata in locale. Mancano: push e pull request, installazione sul PC sempre acceso,
modello in `llm.toml`, Telegram, primo giro reale sulle fonti. Elenco ordinato in PIANO.md, sezione 7.
