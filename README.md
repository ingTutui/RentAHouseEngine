# RentAHouseEngine · Radar Affitti

Ogni 30 minuti controlla i siti di oltre 120 agenzie immobiliari della zona di Modena e i portali
(Immobiliare.it, Casa.it, Subito.it, Wikicasa) e ti scrive su Telegram **solo** quando c'è un affitto nuovo
che rispetta i tuoi filtri. L'obiettivo è vedere gli annunci che le agenzie pubblicano sul proprio sito prima
(o senza) che arrivino su Immobiliare.it.

## Le regole

| Regola | Come funziona |
|---|---|
| **Incrementale** | Ogni annuncio ti arriva una volta sola. Se lo stesso immobile compare su più fonti (sito dell'agenzia, poi Immobiliare.it, poi Casa.it) viene riconosciuto e non lo rivedi. Se cambia solo il prezzo di poco, è lo stesso annuncio. |
| **Filtri** | Affitto fino a **700 €/mese** (spese condominiali escluse), almeno **55 mq**, nei comuni dell'area. Niente stanze, negozi, uffici, box. Si cambiano in `config.toml`. |
| **Orari** | **Silenzio dalle 22:00 alle 08:00**. Di notte il radar continua a controllare, ma i messaggi restano in coda e ti arrivano tutti insieme dopo le 8:00. |
| **Solo novità** | Se non trova niente non scrive. Gli avvisi sulle fonti rotte viaggiano in fondo a un messaggio con novità, al massimo una volta a settimana (a parte solo dopo 3 giorni senza altri messaggi). |
| **Aggiornamenti da GitHub** | Le modifiche al codice arrivano come pull request. Il PC sempre acceso scarica da solo solo ciò che hai unito a `main`. |
| **LLM (facoltativo)** | Tramite OpenRouter, con chiave in `.env` e modello in `llm.toml`. Legge le schede degli annunci e decide i doppioni dubbi. Senza LLM il radar funziona lo stesso. |

## Come lavorano i due PC

```
PC dove programmi ── push del ramo ──▶ GitHub ── pull request ──▶ tu premi "Merge" ──▶ main
                                                                                         │
PC sempre acceso: ogni 30 minuti  python radar.py --servizio  ◀──── scarica main ───────┘
    1. git fetch + merge (solo avanzamento: non sovrascrive niente)
    2. python radar.py --verifica sul codice nuovo
    3. se la verifica fallisce torna alla versione di prima e non riprova quella
    4. fa il controllo e consegna i messaggi
```

Il radar gira **solo** sul PC sempre acceso. Sul PC di sviluppo si programma e si prova.

## Installazione sul PC sempre acceso

Servono **Python 3.14** (https://www.python.org/downloads/) e Git. Nei comandi che seguono `python` è il
Python 3.14; su Windows, se `python` non c'è o è una versione più vecchia, scrivi `py -3.14`.

```bash
git clone https://github.com/ingTutui/RentAHouseEngine.git
cd RentAHouseEngine
python -m pip install -r requirements.txt
```

Su Linux e Mac usa un ambiente virtuale (`python3.14 -m venv .venv`, poi `.venv/bin/python -m pip install -r
requirements.txt` e lancia i comandi con `.venv/bin/python`): l'avvio automatico usa lo stesso Python con cui lo installi.

1. **Segreti.** Copia `.env.example` in `.env`. Il file resta solo su questo PC (è escluso da git).
2. **Telegram.** `python radar.py --telegram-setup`: ti guida a creare il bot con @BotFather, trova da solo il
   tuo chat id e scrive token e chat id in `.env`. Ti arriva un messaggio di prova.
3. **OpenRouter** (facoltativo). Scrivi la chiave in `.env` (`OPENROUTER_API_KEY=...`) e il modello in `llm.toml`
   (`modello = "..."`). Se preferisci un file tuo, indicalo in `.env` con `LLM_CONFIG=percorso/del/file`
   (TOML o JSON). Prova con `python radar.py --prova-llm`.
4. **Primo controllo.** `python radar.py`. Per ogni fonte, la prima lettura salva quello che c'è già online come
   "visto" **senza avvisarti**: altrimenti ti arriverebbero centinaia di annunci vecchi. Se vuoi invece vedere
   subito anche quelli già online che rispettano i filtri (arrivano in un unico elenco), crea `config.local.toml`
   con `[esecuzione]` e `notifica_prima_esecuzione = true` prima di questo passo.
5. **Avvio automatico ogni 30 minuti.**
   - Windows: doppio clic su `installa\windows_avvio_automatico.bat`. L'attività parte quando sei collegato
     (utente connesso): lascia il PC con l'utente sempre collegato e disattiva la sospensione.
     Per toglierla: `installa\windows_togli_avvio.bat`.
   - Linux o Mac: `sh installa/linux_mac_avvio_automatico.sh` (usa cron).
6. **Controllo.** `python radar.py --stato` mostra annunci visti, coda, stato dell'LLM e fonti con problemi.

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
python -m pip install -r requirements-dev.txt
python -m pytest            # tutti i test (usano siti, Telegram e OpenRouter finti: niente rete)
python -m ruff check .      # controllo dello stile
python radar.py --verifica  # la stessa autoverifica che fa l'aggiornamento automatico
```

- Lascia vuoti `TELEGRAM_TOKEN` e `TELEGRAM_CHAT_ID` in `.env`: i messaggi compaiono solo a schermo e non
  duplichi quelli del PC sempre acceso (che ha un archivio suo).
- Non installare l'avvio automatico su questo PC. Se vuoi comunque impedire gli aggiornamenti, crea
  `config.local.toml` con `[aggiornamento]` e `automatico = false`.
- Per provare una fonte senza salvare niente: `python radar.py --prova "nome agenzia"` (con `--dettagli` legge
  anche le schede dei primi annunci).

**Per cambiare qualcosa:** crea un ramo (`git switch -c nome-modifica`), modifica, esegui `python -m pytest`,
poi `git push -u origin nome-modifica` e apri la pull request su GitHub. Quando premi **Merge**, entro 30 minuti
la modifica è sul PC sempre acceso.

## Python e librerie

- Il progetto è scritto per **Python 3.14**. Con una versione più vecchia `radar.py` si ferma subito con un
  messaggio chiaro, e l'aggiornamento automatico non tiene una versione che non supera `--verifica`.
- Le librerie in `requirements.txt` hanno come minimo le versioni più recenti (requests 2.34, beautifulsoup4 4.15,
  lxml 6.1; pytest 9.1 e ruff 0.16 per lo sviluppo). Per aggiornarle: `python -m pip install -U -r requirements.txt`.
  Quando alzi i minimi in `requirements.txt` e il PC sempre acceso scarica la modifica, installa da solo le nuove versioni.
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
| `[filtri]` | `prezzo_max` | 700 |
| `[filtri]` | `mq_min` | 55 |
| `[filtri]` | `dati_mancanti` | `"invia"`: se prezzo o metratura mancano anche nella pagina, l'annuncio arriva comunque, con "prezzo non indicato". `"scarta"` per escluderli. |
| `[filtri]` | `fuori_zona` | `"scarta"` solo se il comune è certo; se il luogo è solo citato nel testo arriva con un avviso. |
| `[notifiche]` | `silenzio_dalle`, `silenzio_alle` | `"22:00"`, `"08:00"` (vuoti = mai silenzio) |
| `[notifiche]` | `max_messaggi_singoli` | 15: oltre, un unico elenco |
| `[llm]` | `max_spesa_giornaliera_usd` | 0.5: oltre questa spesa lavora senza LLM per il resto del giorno |
| `[aggiornamento]` | `automatico` | `true` |

Un esempio di `config.local.toml` che sposta il silenzio solo su un PC:

```toml
[notifiche]
silenzio_dalle = "23:00"
```

## Comandi

| Comando | Cosa fa |
|---|---|
| `python radar.py` | Un controllo di tutte le fonti. |
| `python radar.py --servizio` | Quello che lancia l'avvio automatico: aggiorna da GitHub, verifica, controlla. |
| `python radar.py --loop 30` | Un controllo ogni 30 minuti restando aperto (alternativa all'avvio automatico). |
| `python radar.py --stato` | Annunci visti, coda, LLM, fonti con problemi. |
| `python radar.py --coda` | Gli annunci in attesa di essere inviati (per esempio durante il silenzio). |
| `python radar.py --invia-ora` | Controlla e invia la coda anche se è orario di silenzio. |
| `python radar.py --senza-telegram` | Mostra i messaggi a schermo invece di mandarli. |
| `python radar.py --solo agenzia` | Solo siti delle agenzie (oppure `--solo portale`). |
| `python radar.py --fonte testo` | Solo le fonti il cui nome o indirizzo contiene il testo. |
| `python radar.py --prova "nome"` | Mostra cosa estrae da una fonte, senza salvare. |
| `python radar.py --prova-llm` | Controlla chiave OpenRouter e modello. |
| `python radar.py --telegram-setup` | Collega il bot Telegram. |
| `python radar.py --esporta` | Salva tutti gli annunci visti in `stato/annunci_visti.csv`. |
| `python radar.py --verifica` | Autoverifica del codice. |
| `python radar.py --installa-avvio` / `--togli-avvio` | Installa o toglie il controllo automatico. |

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
   `da_inviare`, `inviato`. Con `python radar.py --esporta` li vedi tutti con il motivo.

Se un'agenzia rifà il sito e tutti i link cambiano, il radar se ne accorge e li salva come già visti, senza
inondarti di annunci vecchi.

## Cosa non legge

- **Facebook:** serve il login e le regole del sito vietano la lettura automatica.
- **Idealista:** solo con l'API ufficiale. Se ti danno le chiavi (https://developers.idealista.com/access-request),
  mettile in `.env` (`IDEALISTA_API_KEY`, `IDEALISTA_API_SECRET`); il radar la interroga ogni 12 ore.
- **Siti che caricano gli annunci in JavaScript:** per quelli segnati `js` nel CSV serve Playwright
  (`python -m pip install playwright`, poi `python -m playwright install chromium`). Senza, il radar li legge senza browser
  e quasi sempre non vede gli annunci: compaiono tra le fonti "senza annunci" in `--stato`.

## Se qualcosa non va

- Nessun messaggio ma ti aspetti novità: `python radar.py --coda` (potrebbero essere in coda per il silenzio) e
  `python radar.py --stato`.
- Telegram non risponde: controlla `TELEGRAM_TOKEN` e `TELEGRAM_CHAT_ID` in `.env`, oppure rilancia
  `--telegram-setup`.
- Una fonte non estrae niente: `python radar.py --prova "nome"`; il diario è in `stato/radar.log`.
- Aggiornamento non riuscito: `--stato` mostra l'ultimo errore. La versione che non ha superato la verifica
  non viene riprovata finché non ne arriva una nuova.

## Struttura

```
radar.py              punto di ingresso
config.toml           impostazioni condivise
llm.toml              modello OpenRouter
.env.example          modello del file dei segreti
requirements.txt      librerie (requirements-dev.txt: anche pytest e ruff); .python-version: 3.14
fonti/                elenchi di agenzie e portali (CSV)
installa/             avvio automatico (Windows, Linux, Mac)
motore/               codice (config, fonti, rete, estrazione, analisi, doppioni, notifiche, llm, aggiorna, ...)
tests/                test automatici (python -m pytest)
stato/                archivio e diario di questo PC (ignorata da git)
```

## Regole del progetto

- Python 3.14 e librerie alle versioni più recenti.
- Token e chiavi stanno solo in `.env`, mai nel codice né su GitHub.
- Il modello OpenRouter si sceglie in `llm.toml` (o nel file indicato da `LLM_CONFIG`), mai nel codice.
- Le modifiche passano da una pull request: sul PC sempre acceso arriva solo ciò che è in `main`.
