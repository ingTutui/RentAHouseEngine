# RentAHouseEngine · Radar Affitti

Ogni ora controlla i siti di oltre 120 agenzie immobiliari della zona di Modena e i portali
(Immobiliare.it, Casa.it, Subito.it, Wikicasa) e ti scrive su Telegram **solo** quando c'è un affitto nuovo
che rispetta i tuoi filtri. L'obiettivo è vedere gli annunci che le agenzie pubblicano sul proprio sito prima
(o senza) che arrivino su Immobiliare.it.

## Le regole

| Regola | Come funziona |
|---|---|
| **Incrementale** | Ogni annuncio ti arriva una volta sola. Se lo stesso immobile compare su più fonti (sito dell'agenzia, poi Immobiliare.it, poi Casa.it) viene riconosciuto e non lo rivedi. Se cambia solo il prezzo di poco, è lo stesso annuncio. |
| **Filtri** | Affitto fino a **800 €/mese** (spese condominiali escluse), almeno **60 mq**, nei comuni dell'area. Niente stanze, negozi, uffici, box. Si cambiano in `config.toml`. |
| **Orari** | **Silenzio dalle 22:00 alle 08:00**. Di notte il radar continua a controllare, ma i messaggi restano in coda e ti arrivano tutti insieme dopo le 8:00. |
| **Solo novità** | Se non trova niente non scrive. Gli avvisi sulle fonti rotte viaggiano in fondo a un messaggio con novità, al massimo una volta a settimana (a parte solo dopo 3 giorni senza altri messaggi). |
| **Aggiornamenti da GitHub** | Le modifiche al codice arrivano come pull request. Il PC sempre acceso scarica da solo solo ciò che hai unito a `main`. |
| **LLM (facoltativo)** | Tramite OpenRouter, con chiave in `.env` e modello in `llm.toml`. Legge le schede degli annunci e decide i doppioni dubbi. Senza LLM il radar funziona lo stesso. |

## Come lavorano i due PC

```
PC dove programmi ── push del ramo ──▶ GitHub ── pull request ──▶ tu premi "Merge" ──▶ main
                                                                                         │
PC sempre acceso: ogni 60 minuti  python radar.py --servizio  ◀──── scarica main ───────┘
    1. git fetch + merge (solo avanzamento: non sovrascrive niente)
    2. se sono cambiate le librerie (pyproject.toml, uv.lock): uv sync le allinea
    3. python radar.py --verifica sul codice nuovo
    4. se qualcosa non va torna alla versione di prima (codice e librerie)
    5. fa il controllo e consegna i messaggi
```

Il radar gira **solo** sul PC sempre acceso. Sul PC di sviluppo si programma e si prova.

## Installazione sul PC sempre acceso

Servono **uv** e **Git**. uv è il programma che prepara Python e le librerie: legge `.python-version` e
`pyproject.toml`, scarica da solo Python 3.14 se sul PC non c'è (o c'è una versione più vecchia) e crea l'ambiente
`.venv` dentro la cartella del progetto, con le versioni esatte di `uv.lock`. Non serve installare Python a mano.

Installa uv, una volta sola:

- Windows: `winget install --id=astral-sh.uv -e` (oppure, in PowerShell,
  `powershell -ExecutionPolicy ByPass -c "irm https://astral.sh/uv/install.ps1 | iex"`)
- Mac: `brew install uv`
- Linux e Mac: `curl -LsSf https://astral.sh/uv/install.sh | sh`

Poi, in un terminale nuovo (così trova uv):

```bash
git clone https://github.com/ingTutui/RentAHouseEngine.git
cd RentAHouseEngine
uv sync
```

`uv sync` crea `.venv` con Python 3.14 e le librerie. Da qui in avanti ogni comando comincia con `uv run`
(`uv run python radar.py --stato`): uv controlla ogni volta che l'ambiente sia in ordine e usa quello giusto.

1. **Segreti.** Copia `.env.example` in `.env`. Il file resta solo su questo PC (è escluso da git).
2. **Telegram.** `uv run python radar.py --telegram-setup`: ti guida a creare il bot con @BotFather, trova da solo il
   tuo chat id e scrive token e chat id in `.env`. Ti arriva un messaggio di prova.
3. **OpenRouter** (facoltativo). Scrivi la chiave in `.env` (`OPENROUTER_API_KEY=...`). Il modello è già scelto:
   `anthropic/claude-haiku-5.5`, in `llm.toml` e in `.env.example` (`OPENROUTER_MODEL`, che se c'è vale più di
   `llm.toml`). Se preferisci un file tuo, indicalo in `.env` con `LLM_CONFIG=percorso/del/file` (TOML o JSON).
   Prova con `uv run python radar.py --prova-llm`.
4. **Primo controllo.** `uv run python radar.py`. Per ogni fonte, la prima lettura salva quello che c'è già online come
   "visto" **senza avvisarti**: altrimenti ti arriverebbero centinaia di annunci vecchi. Se vuoi invece vedere
   subito anche quelli già online che rispettano i filtri (arrivano in un unico elenco), crea `config.local.toml`
   con `[esecuzione]` e `notifica_prima_esecuzione = true` prima di questo passo.
5. **Avvio automatico ogni ora** (`intervallo_minuti` in `config.toml`: vale quando lo installi, se lo cambi
   ripeti questo passo).
   - Windows: doppio clic su `installa\windows_avvio_automatico.bat`. L'attività usa il Python di `.venv` e parte
     quando sei collegato (utente connesso): lascia il PC con l'utente sempre collegato e disattiva la sospensione.
     Per toglierla: `installa\windows_togli_avvio.bat`.
   - Linux o Mac: `sh installa/linux_mac_avvio_automatico.sh` (usa cron).
   - A mano, equivale a `uv run python radar.py --installa-avvio` (con altri comandi non funziona: l'avvio
     deve usare il Python di `.venv`).
6. **Controllo.** `uv run python radar.py --stato` mostra annunci visti, coda, stato dell'LLM e fonti con problemi.

Non modificare il codice direttamente su questo PC. L'aggiornamento automatico non cancella mai niente: se qui
ci sono commit che non sono su GitHub, o modifiche in conflitto con la versione nuova, si salta (e lo scrive nel
diario). Se Git non c'è, il radar funziona lo stesso ma non si aggiorna.

Se il repository è privato, alla prima `git clone` Git ti chiede di accedere a GitHub e poi ricorda le
credenziali (su Windows nella Gestione credenziali). Verifica che `git fetch` funzioni senza chiedere nulla: l'avvio
automatico non ha nessuno davanti, quindi senza credenziali l'aggiornamento fallisce subito e il radar continua con
la versione che ha.

## Sul PC dove programmi

```bash
git clone https://github.com/ingTutui/RentAHouseEngine.git
cd RentAHouseEngine
uv sync                         # .venv con Python 3.14 e le librerie, pytest e ruff compresi
uv run pytest                   # tutti i test, circa due minuti (siti, Telegram e OpenRouter finti: niente rete)
uv run ruff check .             # controllo dello stile
uv run python radar.py --verifica   # la stessa autoverifica che fa l'aggiornamento automatico
```

- Lascia vuoti `TELEGRAM_TOKEN` e `TELEGRAM_CHAT_ID` in `.env`: i messaggi compaiono solo a schermo e non
  duplichi quelli del PC sempre acceso (che ha un archivio suo).
- Non installare l'avvio automatico su questo PC. Se vuoi comunque impedire gli aggiornamenti, crea
  `config.local.toml` con `[aggiornamento]` e `automatico = false`.
- Per provare una fonte senza salvare niente: `uv run python radar.py --prova "nome agenzia"` (con `--dettagli` legge
  anche le schede dei primi annunci).

**I test e `config.toml`.** I test del comportamento (filtri, notifiche, doppioni, giri completi) usano una
configurazione fissa, `config_di_prova()` in `tests/conftest.py`: cambiare `config.toml` non li rompe. Il file vero
lo guarda `tests/test_configurazione.py`, che legge i valori dal file (soglie, orario di silenzio, comuni, intervallo,
`llm.toml`, fonti) e fallisce solo se il file è sbagliato: chiave con un refuso, valore del tipo sbagliato, comune
che il radar non conosce, un comune sia dentro sia fuori area. `tests/test_architettura.py` controlla le regole del
progetto (nessun segreto su git, `.env` ignorato, TLS sempre attivo, solo le librerie ammesse, modello solo in
`llm.toml`, fine riga dei `.bat`).

**Per cambiare qualcosa:** crea un ramo (`git switch -c nome-modifica`), modifica, esegui `uv run pytest`,
poi `git push -u origin nome-modifica` e apri la pull request su GitHub. Quando premi **Merge**, entro un'ora
la modifica è sul PC sempre acceso. Per aggiungere una libreria: `uv add nome` (aggiorna `pyproject.toml` e `uv.lock`,
che vanno nello stesso commit).

## Python e librerie (uv)

- Il progetto è gestito con **uv**. `.python-version` (3.14) e `requires-python` in `pyproject.toml` dicono quale
  Python serve: `uv sync` lo cerca sul PC e, se manca o è più vecchio, lo scarica. L'ambiente è `.venv` nella
  cartella del progetto (git lo ignora). Non c'è più `requirements.txt`.
- Le librerie sono in `pyproject.toml` (minimi: requests 2.34, beautifulsoup4 4.15, lxml 6.1; pytest 9.1 e ruff 0.16
  per lo sviluppo) e le versioni esatte in `uv.lock`, che sta nel repository: tutti i PC usano le stesse.
- Per aggiornarle: `uv lock --upgrade`, poi `uv sync`, `uv run pytest`, e `pyproject.toml` con `uv.lock` nello stesso
  commit. Quando arrivano su `main`, il PC sempre acceso li scarica e `uv sync` allinea da solo `.venv`; se la
  verifica poi non passa, torna indietro anche con le librerie. Se non riesce ad allinearle (rete assente, uv
  mancante) annulla l'aggiornamento e riprova al controllo dopo.
- Cambiare versione di Python (per esempio 3.15) si fa a mano sul PC sempre acceso: `git pull`, poi `uv sync`, che
  ricrea `.venv`. L'aggiornamento automatico non lo fa: su Windows non può sostituire l'interprete mentre il radar
  lo usa.
- Con un Python più vecchio `radar.py` si ferma subito con un messaggio che indica uv.
- Il codice usa solo la libreria standard per leggere i file TOML (`tomllib`) e le annotazioni di tipo native di
  Python 3.14: niente `tomli`, niente `from __future__`.

## Impostazioni

| File | A cosa serve | Nel repository |
|---|---|---|
| `config.toml` | Filtri, orari di silenzio, limiti, aggiornamenti. Valgono su tutti i PC. | sì |
| `config.local.toml` | Solo su questo PC: sovrascrive `config.toml` sezione per sezione. | no |
| `.env` | Segreti: Telegram, OpenRouter, chiavi Idealista. | **mai** |
| `llm.toml` | Modello OpenRouter, modelli di riserva, temperatura, limiti. | sì |
| `fonti/fonti_agenzie.csv` | Siti delle agenzie (colonna `attiva` sì/no, `js` per i siti che caricano gli annunci in JavaScript). | sì |
| `fonti/fonti_portali.csv` | Pagine dei portali per comune. | sì |

I valori più usati, in `config.toml`:

| Sezione | Chiave | Valore attuale |
|---|---|---|
| `[filtri]` | `prezzo_max` | 800 (0 = nessun limite) |
| `[filtri]` | `mq_min` | 60 (0 = nessun limite) |
| `[filtri]` | `dati_mancanti` | `"invia"`: se prezzo o metratura mancano anche nella pagina, l'annuncio arriva comunque, con "prezzo non indicato". `"scarta"` per escluderli. |
| `[filtri]` | `fuori_zona` | `"scarta"` solo se il comune è certo; se il luogo è solo citato nel testo arriva con un avviso. |
| `[notifiche]` | `silenzio_dalle`, `silenzio_alle` | `"22:00"`, `"08:00"` (vuoti = mai silenzio) |
| `[notifiche]` | `max_messaggi_singoli` | 15: oltre, un unico elenco |
| `[llm]` | `max_spesa_giornaliera_usd` | 1: oltre questa spesa lavora senza LLM per il resto del giorno |
| `[aggiornamento]` | `automatico` | `true` |

Un esempio di `config.local.toml` che sposta il silenzio solo su un PC:

```toml
[notifiche]
silenzio_dalle = "23:00"
```

**Cambiare un valore in `config.toml` è sicuro.** Prima di tenere una versione nuova, il PC sempre acceso lancia
`--verifica`, che controlla anche i valori: un numero scritto tra virgolette (`prezzo_max = "800"`), una scelta che
non esiste (`fuori_zona = "boh"`), una stringa vuota in `tipi_esclusi` (scarterebbe ogni annuncio), un orario scritto
male (`"22"` invece di `"22:00"`), minimo maggiore del massimo. In quel caso la modifica non viene presa e il radar
resta alla versione di prima; `--stato` dice quale valore è sbagliato e, come per le fonti rotte, un avviso arriva
su Telegram in fondo a un messaggio con novità. Per provare prima del push: `uv run python radar.py --verifica`.
I test non dipendono dai valori di `config.toml`: puoi cambiare prezzi, metrature, orari e comuni senza toccarli
(vedi «Sul PC dove programmi»).

Due impostazioni vengono lette solo quando serve: `intervallo_minuti` vale quando installi l'avvio automatico (se lo
cambi, ripeti l'installazione sul PC sempre acceso) e `llm.toml` scritto male spegne l'LLM senza fermare il radar.

## Comandi

| Comando | Cosa fa |
|---|---|
| `uv run python radar.py` | Un controllo di tutte le fonti. |
| `uv run python radar.py --servizio` | Quello che lancia l'avvio automatico: aggiorna da GitHub, verifica, controlla. |
| `uv run python radar.py --loop 30` | Un controllo ogni 30 minuti restando aperto (alternativa all'avvio automatico). |
| `uv run python radar.py --stato` | Annunci visti, coda, LLM, fonti con problemi. |
| `uv run python radar.py --coda` | Gli annunci in attesa di essere inviati (per esempio durante il silenzio). |
| `uv run python radar.py --invia-ora` | Controlla e invia la coda anche se è orario di silenzio. |
| `uv run python radar.py --senza-telegram` | Mostra i messaggi a schermo invece di mandarli. |
| `uv run python radar.py --solo agenzia` | Solo siti delle agenzie (oppure `--solo portale`). |
| `uv run python radar.py --fonte testo` | Solo le fonti il cui nome o indirizzo contiene il testo. |
| `uv run python radar.py --prova "nome"` | Mostra cosa estrae da una fonte, senza salvare. |
| `uv run python radar.py --prova-llm` | Controlla chiave OpenRouter e modello. |
| `uv run python radar.py --telegram-setup` | Collega il bot Telegram. |
| `uv run python radar.py --esporta` | Salva tutti gli annunci visti in `stato/annunci_visti.csv`. |
| `uv run python radar.py --verifica` | Autoverifica del codice e dei valori di `config.toml`. |
| `uv run python radar.py --installa-avvio` / `--togli-avvio` | Installa o toglie il controllo automatico. |

Archivio, diario (`radar.log`) ed esportazioni stanno nella cartella `stato/`, che git ignora.

## Come ragiona il radar

1. **Legge** ogni fonte (in parallelo fonti diverse, una per volta lo stesso sito, con una pausa tra le richieste:
   il carico di una persona che naviga) e rispetta il `robots.txt` di ogni sito. Prima Immobiliare.it, poi gli
   altri portali, poi le agenzie, così un annuncio presente anche sul portale viene riconosciuto.
2. **Estrae** prezzo, metri quadri, locali, comune. Se nell'elenco mancano, apre la scheda dell'annuncio e li legge
   dai dati strutturati della pagina; se serve chiede all'LLM. I numeri dell'LLM valgono solo se compaiono
   davvero nel testo della pagina.
3. **Filtra** per prezzo, metratura, tipo di immobile e zona. Il comune è "fuori area" solo quando è certo.
4. **Cerca i doppioni** tra tutte le fonti (prezzo ±3%, metri ±3, locali, comune, via, titolo). Se è un caso
   dubbio decide l'LLM; senza LLM il messaggio riporta "Forse è lo stesso di ...".
5. **Mette in coda** e consegna: fuori dall'orario di silenzio, prima gli annunci che sono solo sul sito
   dell'agenzia (il tuo vantaggio), poi quelli dei portali. Il messaggio dice se l'annuncio non è ancora su
   Immobiliare.it e dove altro compare. Se Telegram non risponde, gli annunci restano in coda.
6. Un annuncio ha sempre uno stato: `base` (c'era già alla prima lettura), `scartato`, `doppione`,
   `da_inviare`, `inviato`. Con `uv run python radar.py --esporta` li vedi tutti con il motivo.

Se un'agenzia rifà il sito e tutti i link cambiano, il radar se ne accorge e li salva come già visti, senza
inondarti di annunci vecchi.

## Cosa non legge

- **Facebook:** serve il login e le regole del sito vietano la lettura automatica.
- **Idealista:** solo con l'API ufficiale. Se ti danno le chiavi (https://developers.idealista.com/access-request),
  mettile in `.env` (`IDEALISTA_API_KEY`, `IDEALISTA_API_SECRET`); il radar la interroga ogni 12 ore.
- **Siti che caricano gli annunci in JavaScript:** per quelli segnati `js` nel CSV serve Playwright
  (`uv sync --extra browser`, poi `uv run playwright install chromium`). Senza, il radar li legge senza browser
  e quasi sempre non vede gli annunci: compaiono tra le fonti "senza annunci" in `--stato`.

## Se qualcosa non va

- `uv` non trovato: installalo (vedi sopra) e apri un terminale nuovo. Per controllare quale Python usa:
  `uv run python --version` (deve dire 3.14); se uv non lo scarica da solo, `uv python install 3.14`.
- Nessun messaggio ma ti aspetti novità: `uv run python radar.py --coda` (potrebbero essere in coda per il silenzio) e
  `uv run python radar.py --stato`.
- Telegram non risponde: controlla `TELEGRAM_TOKEN` e `TELEGRAM_CHAT_ID` in `.env`, oppure rilancia
  `--telegram-setup`.
- Una fonte non estrae niente: `uv run python radar.py --prova "nome"`; il diario è in `stato/radar.log`.
- Aggiornamento non riuscito: `--stato` mostra l'ultimo errore (anche un valore sbagliato in `config.toml`). La
  versione che non ha superato la verifica non viene riprovata finché non ne arriva una nuova. Se l'errore è «librerie non allineate» riprova da solo al
  controllo dopo; per farlo subito: `uv sync` nella cartella del progetto.

## Struttura

```
radar.py              punto di ingresso
CLAUDE.md             istruzioni per Claude Code: regole, comandi, mappa del codice
PIANO.md              piano: requisiti, decisioni e perché, stato, prossimi passi
config.toml           impostazioni condivise
llm.toml              modello OpenRouter
.env.example          modello del file dei segreti
pyproject.toml        librerie e impostazioni degli strumenti (uv, pytest, ruff)
uv.lock               versioni esatte delle librerie (la scrive uv: non modificarlo a mano)
.python-version       3.14: il Python che sceglie uv
.venv/                ambiente creato da uv sync (ignorata da git)
fonti/                elenchi di agenzie e portali (CSV)
installa/             avvio automatico (Windows, Linux, Mac)
motore/               codice (config, fonti, rete, estrazione, analisi, doppioni, notifiche, llm, aggiorna, ...)
tests/                test automatici (uv run pytest)
stato/                archivio e diario di questo PC (ignorata da git)
```

## Regole del progetto

- Python 3.14 e librerie alle versioni più recenti, gestiti con uv (`pyproject.toml` e `uv.lock` insieme).
- Token e chiavi stanno solo in `.env`, mai nel codice né su GitHub.
- Il modello OpenRouter si sceglie in `llm.toml` (o nel file indicato da `LLM_CONFIG`), mai nel codice.
- Le modifiche passano da una pull request: sul PC sempre acceso arriva solo ciò che è in `main`.
