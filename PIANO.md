# Piano del progetto: Radar Affitti (RentAHouseEngine)

Documento per chi apre il progetto senza contesto (per esempio una sessione nuova di Claude Code). Spiega cosa si
vuole ottenere, cosa è stato deciso e perché, com'è fatto il codice, cosa è già verificato e cosa no, e i prossimi
passi in ordine. Ultimo aggiornamento: 11 ottobre 2026.

## 1. Obiettivo

Trovare casa in affitto nella zona di Modena e Reggio vedendo gli annunci **prima** di chi guarda solo
Immobiliare.it: le agenzie spesso pubblicano prima, o solo, sul proprio sito. Il radar:

1. legge ogni ora (`intervallo_minuti`) i siti di oltre 120 agenzie e quattro portali (Immobiliare.it, Casa.it, Subito, Wikicasa);
2. riconosce gli annunci nuovi, li filtra (prezzo, metratura, zona, tipo) e toglie i doppioni tra fonti diverse;
3. manda su Telegram solo le novità, mai di notte.

Gli annunci presenti solo sul sito di un'agenzia sono il vantaggio: arrivano per primi e il messaggio dice
"Non ancora su Immobiliare.it".

## 2. Requisiti

| # | Requisito | Valore | Dove si regola | Codice | Test |
|---|---|---|---|---|---|
| 1 | Incrementale: un annuncio già segnalato non si segnala di nuovo, nemmeno se compare su un'altra fonte | | `[doppioni]` | archivio.py, doppioni.py, controllo.py | test_archivio, test_doppioni, test_integrazione |
| 2 | Filtri | affitto fino a 750 €/mese (spese condominiali escluse), almeno 60 mq, niente stanze, negozi, uffici, box | `[filtri]` | analisi.py (`scarta`) | test_filtri |
| 3 | Orari | silenzio dalle 22:00 alle 08:00: il radar controlla comunque, i messaggi restano in coda e partono dopo le 8 | `[notifiche]` | notifiche.py (`in_silenzio`, `consegna`) | test_notifiche |
| 4 | Solo novità: se non trova nulla non scrive | eccezione: avvisi sulle fonti rotte (vedi sezione 5) | `[notifiche]` | notifiche.py | test_notifiche |
| 5 | Repository GitHub, aggiornamenti con pull request; il PC sempre acceso scarica da solo `main` | https://github.com/ingTutui/RentAHouseEngine | `[aggiornamento]` | aggiorna.py, servizio.py | test_aggiorna |
| 6 | LLM solo tramite OpenRouter, chiave in `.env`, modello in un file di configurazione | `anthropic/claude-haiku-5.5` in `llm.toml` e `.env.example` | `[llm]`, `llm.toml` | llm.py, dettagli.py | test_llm |
| 7 | Due PC: uno sempre acceso (gira il radar), uno poco usato e spesso spento dove si programma | | | | |
| 8 | Python 3.14 e librerie alle versioni più recenti, gestiti con **uv**: il Python giusto per il venv lo sceglie uv | requests 2.34, beautifulsoup4 4.15, lxml 6.1; versioni esatte in `uv.lock` | `pyproject.toml`, `uv.lock`, `.python-version` | tutto, aggiorna.py | test_cli, test_progetto, test_aggiorna |
| 9 | Rispetto dei siti: robots.txt, una richiesta alla volta per sito con pausa | | `[esecuzione]` | rete.py | test_rete |

Scelte fatte dall'utente rispondendo alle domande: prezzo massimo 700 € al mese, almeno 55 mq, silenzio
22:00-08:00 (l'11 ottobre 2026 l'utente ha portato i filtri a 750 € e 60 mq, il controllo a ogni ora e il limite
LLM a 1 $ al giorno), GitHub con pull request (così sul PC sempre acceso arriva solo ciò che l'utente unisce a `main`).
Telegram per le notifiche e radar sul proprio computer (non nel cloud) erano già decisi prima.

## 3. Area di ricerca

Disegnata dall'utente sulla mappa di un portale e poi ristretta; i comuni sono ricavati incrociando l'area con i
confini ISTAT (mappe e ragionamento in `contesto/`, se presente). In `config.toml`:

- `comuni_area` (17): Modena, Campogalliano, Soliera, Bastiglia, Nonantola, Castelfranco Emilia, San Cesario sul
  Panaro, Spilamberto, Castelnuovo Rangone, Castelvetro di Modena, Formigine, Fiorano Modenese, Maranello, Rubiera,
  San Martino in Rio, Sassuolo, Casalgrande;
- `frazioni_area`: comuni di cui sono dentro solo alcune frazioni (Reggio Emilia: Bagno, Marmirolo, Castellazzo,
  Corticella; Scandiano: Arceto, Cacciola; Carpi: Gargallo; Correggio: Prato; Bomporto: Villavara);
- `zone_fuori_area`: frazioni fuori area di comuni inclusi (Sozzigalli, Manzolino, Riolo, Rastellino,
  Piumazzo, Cavazzona, Redù, Via Larga) e paesi vicini esclusi (Carpi, Correggio, Bomporto, Sorbara, Ravarino,
  Vignola, Savignano, Scandiano, Castellarano, Serramazzoni);
- `fuori_zona = "scarta"`: un annuncio si scarta per la zona **solo quando il luogo è certo** (comune dato dal portale
  o letto nella scheda). Se il luogo è solo citato nel testo, l'annuncio arriva con un avviso.

`motore/_dati_comuni.py` (144 comuni di Modena, Reggio Emilia e Bologna, 164 frazioni) è un dato generato da
confini ISTAT e GeoNames: lo script che l'ha prodotto non è nel repository.

## 4. Come funziona (flusso di un controllo)

`python radar.py` (o `--servizio` dall'avvio automatico) chiama `controllo.esegui`:

1. Carica le fonti da `fonti/fonti_portali.csv` (68 URL: 4 portali per 17 comuni) e `fonti/fonti_agenzie.csv` (131
   agenzie, 126 attive, 27 segnate `js`: servono Playwright).
2. Scarica in parallelo fonti di domini diversi (6 connessioni), una alla volta per lo stesso sito, con pausa e
   rispetto di robots.txt. Ordine di elaborazione: Immobiliare.it, altri portali, agenzie: così un annuncio che
   compare sia sul sito dell'agenzia sia sul portale viene riconosciuto come lo stesso.
3. Estrae i link agli annunci (estrazione.py). Un annuncio ha un identificativo stabile (URL canonico).
4. Se la fonte è letta per la prima volta, salva tutto come `base` (già visto) **senza avvisare**; stesso
   trattamento se un'agenzia rifà il sito e cambiano quasi tutti i link (almeno 5 nuovi e al più il 20% dei vecchi).
5. Per ogni annuncio nuovo: analisi (prezzo, mq, locali, comune, frazione, tipo), poi i filtri. Se mancano dati e
   i filtri lo richiedono, apre la scheda dell'annuncio (dettagli.py): prima i dati strutturati della pagina
   (JSON-LD), poi l'LLM, poi le regole sul testo. I numeri dati dall'LLM valgono solo se compaiono davvero nel
   testo della pagina.
6. Cerca i doppioni (doppioni.py) tra tutte le fonti degli ultimi 120 giorni: prezzo ±3%, mq ±3, locali, comune,
   via, titolo. Casi certi: doppione. Casi dubbi: decide l'LLM (se risponde con certezza di almeno 0.7); senza
   LLM il messaggio riporta "Forse è lo stesso di ...".
7. Mette in coda gli annunci rimasti (stato `da_inviare`) e consegna la coda (notifiche.py) se non è orario di
   silenzio: prima quelli presenti solo sul sito dell'agenzia; oltre 15 annunci un unico elenco; l'anteprima del
   link mostra la foto dell'annuncio.

Stati di un annuncio nell'archivio: `base`, `scartato` (con il motivo), `doppione` (con il gruppo), `da_inviare`,
`inviato`. `python radar.py --esporta` li salva tutti in `stato/annunci_visti.csv`.

L'avvio automatico (`--servizio`, ogni ora) prende un blocco di esclusione (un solo controllo alla volta),
prova ad aggiornare il codice da GitHub (aggiorna.py) e poi fa il controllo; se il codice è stato aggiornato rilancia
il controllo in un processo nuovo con il codice nuovo.

## 5. Decisioni di progetto e perché

- **Prima lettura senza avvisi.** Altrimenti al primo avvio arriverebbero centinaia di annunci vecchi.
  `notifica_prima_esecuzione = true` (in `config.local.toml`) li manda in un unico elenco.
- **Silenzio = coda, non pausa.** Di notte il radar controlla lo stesso: gli annunci trovati alle 3 sono già in
  coda e partono dopo le 8, con "Trovato alle HH:MM" se sono vecchi di oltre 45 minuti.
- **Guasti.** Se una fonte non funziona da almeno 24 ore, una riga in fondo al primo messaggio con novità (al
  massimo una volta a settimana). Un messaggio a parte solo se passano 3 giorni senza altri messaggi: senza questa
  eccezione un radar rotto sembrerebbe un radar senza novità. Si spegne con `avvisi_guasti = false`.
- **L'LLM non decide mai da solo i numeri.** Prezzo e metri dell'LLM sono accettati solo se letteralmente nella
  pagina. Senza chiave, senza modello o oltre i limiti (40 chiamate per controllo, 300 al giorno, 1 $ al giorno) il
  radar lavora con regole e dati strutturati.
- **OpenRouter.** Chiavi e modello separati: la chiave in `.env`, il modello in `llm.toml` (o file TOML/JSON indicato
  da `LLM_CONFIG`, o `OPENROUTER_MODEL`). Il client prova JSON schema rigoroso, poi `json_object`, poi solo
  istruzioni, e si ricorda cosa funziona per quel modello (non dopo un 503, che può essere un guasto del momento);
  risposte in cache. `temperature` si manda solo se scritta nel file del modello: i Claude 5 non la accettano e con
  `require_parameters` nessun fornitore risponderebbe. Errori: 401 e 402 spengono l'LLM per il giro; 403
  (moderazione, guardrail) salta solo quella richiesta e lo spegne dopo tre di fila; 503 (nessun fornitore
  soddisfa i requisiti) passa al modo successivo; 429 e gli altri 5xx riprovano una volta. Modello:
  `anthropic/claude-haiku-5.5` col ragionamento spento (`ragionamento = { enabled = false }` in `llm.toml`):
  basta per leggere prezzo e metri e costa 0,10 $ per milione di token in ingresso, 0,50 $ in uscita.
- **Segreti e impostazioni.** `.env` (mai su git) per i segreti; `config.toml` condiviso nel repository;
  `config.local.toml` (non su git) per le differenze di un solo PC. Le variabili d'ambiente del sistema battono `.env`.
- **Aggiornamento automatico prudente.** Solo `git merge --ff-only` di `origin/main`; se la cartella è su un altro
  ramo o ha commit locali o modifiche in conflitto, salta. Se sono cambiati `pyproject.toml` o `uv.lock` allinea le
  librerie con `uv sync --locked` (Playwright resta se c'era). Poi esegue `radar.py --verifica`; se fallisce torna
  alla versione precedente (`git reset --keep`, e `uv sync` di nuovo con le sue librerie) e non riprova quella
  versione finché non ne arriva un'altra. Se invece sono le librerie a non allinearsi (rete assente, uv mancante)
  annulla senza segnare la versione come rotta e riprova al giro dopo: l'aggiornamento è tutto o niente. L'avvio
  automatico non ha nessuno davanti: git viene chiamato senza prompt di credenziali.
- **Windows.** Attività pianificata (XML, `schtasks`) con il `pythonw.exe` di `.venv`, ogni `intervallo_minuti` (scritto nell'attività quando si installa), solo con
  l'utente collegato, una sola istanza alla volta, recupero dei giri persi. Linux e Mac: cron (riga con
  `# RadarAffitti`) con il Python di `.venv`. Si installa con `uv run python radar.py --installa-avvio`: con un altro
  Python il comando si rifiuta, perché l'attività userebbe un ambiente senza le librerie.
- **Idealista e Facebook.** Idealista vieta la lettura automatica: solo API ufficiale (chiavi da chiedere su
  developers.idealista.com/access-request, poi in `.env`; una ricerca ogni 12 ore). Facebook richiede il login e vieta
  la raccolta automatica: non si legge; l'utente segue i gruppi con le notifiche.
- **Python 3.14.** `tomllib` della libreria standard, annotazioni native (list, dict, `X | None`), `.python-version`.
  Con un Python più vecchio `radar.py` esce con un messaggio chiaro (che indica uv); per questo l'aggiornamento
  automatico non può portare una versione che non gira.
- **uv per Python, ambiente e librerie.** Richiesta dell'utente: così il Python giusto per il venv non dipende da quale
  `python` c'è sul PC. uv legge `.python-version` e `requires-python`, scarica 3.14 se manca e crea `.venv` nella
  cartella del progetto. Niente `requirements.txt`: un solo elenco, `pyproject.toml` (minimi) con `uv.lock` (versioni
  esatte, nel repository); un test controlla che `uv.lock` sia aggiornato. Playwright è l'extra `browser`
  (`uv sync --extra browser`). Il processo che aggiorna resta «leggero» (nessun requests, bs4 o lxml caricato, anche
  tramite archivio e estrazione): su Windows uv non può sostituire i file di una libreria in uso. Cambiare versione
  di Python resta un'operazione a mano (`git pull` e `uv sync` sul PC sempre acceso).
- **Struttura.** Il codice è in `motore/`, i dati che cambiano in `stato/` (ignorata da git), le fonti in `fonti/`.

## 6. Stato

### Fatto e verificato

- 78 test automatici (`uv run pytest`) passano su Python 3.14.6 con requests 2.34.2, beautifulsoup4 4.15.0, lxml
  6.1.3, pytest 9.1.1 (le versioni di `uv.lock`); `uv run ruff check .` pulito (target py314);
  `uv run python radar.py --verifica` OK; lo stesso da un clone pulito, con `uv sync` che parte senza Python 3.14 sul
  PC e lo scarica da solo.
- I test usano un piccolo server locale che fa da sito di agenzia, da Telegram e da OpenRouter: coprono estrazione,
  filtri, area, doppioni, silenzio e coda, guasti, client LLM, aggiornamento automatico con repository git veri,
  comandi su un clone appena fatto.
- Playwright 1.63 (facoltativo, extra `browser`) si installa e si importa su Python 3.14.

### Non verificato (serve il PC reale)

- Letture dei siti veri: dal cloud dove è stato scritto molti siti di agenzie non rispondono, quindi l'estrazione è
  provata su pagine finte e su campioni di HTML. Aspettarsi ritocchi a `fonti/fonti_agenzie.csv` (`url_affitti`,
  `pattern_link`, `js`) e, se un portale cambia pagina, a `estrazione.py`. Gli stessi siti noti come difficili: Solo
  Affitti e Tempocasa rispondono 403 ai programmi, Tecnocasa carica gli annunci in JavaScript.
- uv su Windows: installazione con winget, `.venv\Scripts\pythonw.exe` nell'attività pianificata, `uv sync` lanciato
  dal servizio (provato solo su Linux con un uv finto nei test e uno vero a mano). Il codice evita il caso peggiore
  (librerie già caricate) ma va guardato al primo aggiornamento che cambia `uv.lock`: `--stato` e `stato\radar.log`.
- Telegram reale, OpenRouter reale (modello scelto, manca la chiave), Playwright con un browser vero, API di Idealista
  (nessuna chiave), aggiornamento automatico verso GitHub vero, l'attività pianificata di Windows (l'XML è validato,
  non eseguito su Windows).

## 7. Prossimi passi, in ordine

1. **Push e pull request:** fatto, la versione 2 è su `main` (pull request #1).
2. **PC sempre acceso** (dopo il merge, altrimenti si clona la versione 1): uv (non serve Python: lo scarica uv) e
   Git, `git clone`, `uv sync`, copiare `.env.example` in `.env`, `uv run python radar.py --telegram-setup`,
   `uv run python radar.py` (primo giro: salva tutto senza avvisare), avvio automatico con `installa/`. Dettagli nel
   README.
3. **OpenRouter:** `OPENROUTER_API_KEY` in `.env` (il modello è già scelto: Haiku 5.5 in `llm.toml` e
   `.env.example`), poi `uv run python radar.py --prova-llm`.
4. **Primo giro reale sulle fonti.** `uv run python radar.py --stato` dopo il primo controllo: fonti con problemi e
   fonti senza annunci. Per ognuna `uv run python radar.py --prova "nome" --dettagli`; sistemare il CSV. Provare in particolare le
   fonti "home del sito" e i circa 50 siti che dal cloud non rispondevano.
5. **Dopo uno o due giorni:** guardare annunci scartati (`--esporta`, colonna motivo) e doppioni per tarare filtri e
   soglie.
6. Idee non richieste, da proporre all'utente prima di farle: GitHub Actions che esegue test e ruff a ogni pull
   request; avviso quando un annuncio già segnalato cambia prezzo; rendere privato il repository (ora risulta
   pubblico: il README spiega le credenziali per il PC sempre acceso).

## 8. Come si è arrivati qui

1. L'utente ha deciso di cercare in affitto e ha disegnato l'area su una mappa; l'area è stata tradotta in comuni
   (confini ISTAT) e poi ristretta (`contesto/mappe`, `contesto/ricerca-affitto-area.md`).
2. Ricerca delle agenzie della zona (638 nomi: 343 dai portali, 295 da elenchi di imprese; 135 con sito, 66 con pagina
   affitti; 43 con sito ma assenti da Immobiliare.it: il vantaggio): `contesto/dati/`.
3. Versione 1: un solo file `radar.py` (commit dda7fc2 su `main`).
4. Richieste per la versione 2 (incrementale, filtri, orari, solo novità, repository, OpenRouter): riscrittura in
   `motore/`, con archivio a stati, doppioni tra fonti, schede degli annunci, coda notturna, aggiornamento automatico.
5. Passaggio a Python 3.14 e alle librerie più recenti.
6. Passaggio a uv come gestore di Python, ambiente e librerie (richiesta dell'utente: «così mi usa il python giusto per
   il venv»): `pyproject.toml` e `uv.lock` al posto di `requirements.txt`, aggiornamento automatico con `uv sync`.
7. Versione 2 su `main` (pull request #1). L'utente alza i filtri (750 €, 60 mq, Limidi dentro l'area), passa al
   controllo ogni ora e a 1 $ al giorno di LLM; client OpenRouter rivisto sulla documentazione, modello Haiku 5.5.
