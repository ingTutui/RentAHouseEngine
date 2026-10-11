# RentAHouseEngine (Radar Affitti)

Aggregatore personale di annunci di affitto per la zona di Modena e Reggio. Ogni ora legge i siti di oltre
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
uv sync                                   # una volta: .venv con Python 3.14 e le librerie (anche pytest e ruff)
uv run pytest                             # 78 test, circa un minuto, nessuna rete (siti, Telegram e OpenRouter finti)
uv run ruff check .
uv run python radar.py --verifica         # autoverifica: la usa anche l'aggiornamento automatico
uv run python radar.py --stato            # archivio, coda, LLM, fonti con problemi
uv run python radar.py --prova "nome"     # cosa estrae da una fonte, senza salvare (con --dettagli legge anche le schede)
uv run python radar.py --senza-telegram   # un controllo con i messaggi a schermo
uv run python radar.py --prova-llm        # controlla chiave OpenRouter e modello
```

Python e librerie li gestisce **uv**: `.python-version` (3.14) e `pyproject.toml` dicono quale Python serve e uv lo
sceglie (o lo scarica) da solo. Non usare `pip` né un altro Python: lancia tutto con `uv run`. Se `uv` non c'è,
installalo (README, «Installazione sul PC sempre acceso»).

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
- Dipendenze: libreria standard più requests, beautifulsoup4 e lxml (Playwright è facoltativo, extra `browser`).
  Chiedi prima di aggiungerne altre; si aggiungono con `uv add nome` e `pyproject.toml` e `uv.lock` vanno nello
  stesso commit (un test controlla che `uv.lock` sia aggiornato). Non ricreare `requirements.txt`.
- Il processo che aggiorna il PC sempre acceso (`aggiorna.py`, `servizio.py`, `archivio.py` e quello che importano)
  non deve importare requests, bs4 o lxml a livello di modulo: su Windows `uv sync` non può sostituire una libreria
  già caricata. Gli import pesanti stanno dentro le funzioni; `tests/test_aggiorna.py` lo controlla.
- Python 3.14: `tomllib` della libreria standard, annotazioni di tipo native, niente
  `from __future__ import annotations`.
- I file `.bat` hanno fine riga CRLF (lo gestisce `.gitattributes`): non crearli con `echo` o `printf` di shell.
- Commit in italiano: una riga di titolo e, se serve, la spiegazione sotto.

## Mappa del codice (motore/)

| Modulo | Cosa fa |
|---|---|
| cli.py | Comandi (`uv run python radar.py --help`) |
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
| aggiorna.py, servizio.py, verifica.py, avvio.py | Aggiornamento automatico da GitHub (codice e, con `uv sync`, librerie), modalità servizio, autoverifica, avvio automatico (Windows, cron) con il Python di `.venv` |

I test sono in `tests/` (uno per area, `conftest.py` ha i server finti).

## Stato

Versione 2 su `main` (pull request #1), modello OpenRouter scelto (`anthropic/claude-haiku-5.5`). Mancano:
installazione sul PC sempre acceso, chiave OpenRouter, Telegram, primo giro reale sulle fonti. Elenco ordinato in PIANO.md, sezione 7.
