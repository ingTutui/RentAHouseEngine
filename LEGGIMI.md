# Radar Affitti

Controlla ogni 30 minuti i siti delle agenzie della tua zona e le pagine "più recenti" dei portali.
Quando compare un annuncio in affitto mai visto prima, ti arriva su Telegram. Se l'annuncio è sul sito
di un'agenzia e non risulta ancora su Immobiliare.it, il messaggio lo dice: è il tuo vantaggio.

## 1. Installazione (una volta)

1. Installa **Python 3.11 o più recente** da https://www.python.org/downloads/
   (su Windows spunta "Add python.exe to PATH" durante l'installazione).
2. Metti questa cartella dove preferisci, per esempio `Documenti\radar-affitti`.
3. Apri un terminale nella cartella (Windows: tasto destro nella cartella → "Apri nel terminale";
   Mac: Terminale, poi `cd ` e trascina la cartella) e lancia:

   ```
   pip install -r requirements.txt
   ```

4. Facoltativo ma consigliato, per i siti che caricano gli annunci in JavaScript:

   ```
   pip install playwright
   python -m playwright install chromium
   ```

   Senza questo passaggio quei siti vengono letti lo stesso, ma a volte risultano vuoti.

## 2. Collegare Telegram

```
python radar.py --telegram-setup
```

Il comando ti guida: su Telegram apri **@BotFather**, scrivi `/newbot`, scegli un nome e copia il token
che ti dà. Poi apri il tuo bot, premi Avvia e scrivigli "ciao". Il programma salva da solo token e chat
in `config.toml` e ti manda un messaggio di prova.

## 3. Primo avvio

```
python radar.py
```

La prima volta legge tutte le fonti e salva gli annunci già pubblicati **senza avvisarti**
(altrimenti riceveresti centinaia di messaggi). Ci vogliono 10-20 minuti. Dal secondo controllo in poi
ricevi solo le novità.

## 4. Controllo automatico ogni 30 minuti

- **Windows**: fai doppio clic su `installa_avvio_automatico_windows.bat`. Da quel momento Windows lancia
  il controllo ogni 30 minuti mentre il PC è acceso. Per toglierlo:
  `schtasks /Delete /TN RadarAffitti /F`
- **Mac / Linux**: lancia `./avvia_mac_linux.sh` e lascia aperta la finestra del Terminale.
  In alternativa aggiungi a `crontab -e` la riga
  `*/30 * * * * cd /percorso/radar-affitti && python3 radar.py >> radar_console.log 2>&1`

Se il computer dorme, i controlli si fermano e riprendono al risveglio.

## 5. Filtri (config.toml)

Apri `config.toml` con un editor di testo:

- `prezzo_max = 1000` per ricevere solo affitti fino a 1000 euro (0 = nessun limite)
- `locali_min = 2` per escludere i monolocali; `mq_min = 50` per una metratura minima
- `escludi_stanze = true` per non ricevere stanze e posti letto
- `zone_fuori_area`: frazioni e comuni fuori dalla tua area. Con `zone_fuori_area_azione = "segnala"`
  l'annuncio arriva con un avviso; con `"scarta"` non arriva proprio.

I filtri valgono sui dati che il programma riesce a leggere: se un sito non mostra il prezzo nell'elenco,
l'annuncio arriva comunque, per non perderlo.

## 6. Le fonti

- `fonti_agenzie.csv`: una riga per agenzia con la pagina dei suoi affitti.
  Colonne: `attiva` (sì/no), `agenzia`, `comune`, `url_affitti`, `js` (sì se il sito carica gli annunci
  in JavaScript), `pattern_link` (facoltativo: una parte fissa degli indirizzi degli annunci, per esempio
  `/immobile/` oppure `scheda.php\?id=`), `note`.
- `fonti_portali.csv`: Immobiliare.it, Casa.it, Subito e Wikicasa per ciascun comune, ordinati dal più recente.

Per controllare una fonte senza salvare nulla:

```
python radar.py --prova "nome agenzia"
```

Ti mostra gli annunci che il programma vede in quella pagina. Se ne vede troppi (anche link che non sono
annunci) o nessuno, compila `pattern_link` o cambia `url_affitti`.

## 7. Comandi utili

| Comando | A cosa serve |
|---|---|
| `python radar.py` | un controllo completo |
| `python radar.py --loop 30` | controlla ogni 30 minuti finché la finestra resta aperta |
| `python radar.py --solo agenzia` | solo i siti delle agenzie |
| `python radar.py --fonte "Sassuolo"` | solo le fonti il cui nome o indirizzo contiene il testo |
| `python radar.py --senza-telegram` | mostra i messaggi a schermo invece di inviarli |
| `python radar.py --stato` | fonti con errori o senza annunci |
| `python radar.py --esporta` | salva tutti gli annunci visti in `annunci_visti.csv` |

Il diario dei controlli è in `radar.log`. Gli annunci visti sono in `radar.db`: se lo cancelli, il prossimo
controllo riparte da zero (di nuovo senza avvisi).

## 8. Idealista e Facebook

- **Idealista** vieta la lettura automatica delle sue pagine, quindi il programma non le apre. La strada
  ufficiale è la sua API: chiedi le chiavi su https://developers.idealista.com/access-request
  (spiega che è un uso personale per cercare casa in affitto). Quando le hai, scrivile in `config.toml`
  nella sezione `[idealista]` e il radar le usa da solo (una ricerca ogni 12 ore, perché le chiamate
  gratuite sono poche). Nel frattempo attiva le notifiche push nell'app di Idealista.
- **Facebook** (gruppi e Marketplace) richiede il login e Meta vieta la raccolta automatica: rischieresti
  il blocco dell'account. Cerca su Facebook i gruppi "affitti Modena", "case in affitto Modena e provincia",
  "affitto Sassuolo" e simili, iscriviti e imposta le notifiche su "Tutti i post". Su Marketplace salva la
  ricerca "affitto" con le notifiche attive.

## 9. Buone regole

- Il programma rispetta `robots.txt` di ogni sito e fa una richiesta ogni pochi secondi allo stesso sito:
  è il carico di una persona che naviga, non di un robot aggressivo. Non abbassare troppo le pause.
- Le pagine dei portali vietano lo scraping massivo nei loro termini d'uso: il radar legge solo la prima
  pagina dei più recenti, per uso personale. Se un sito comincia a rispondere con errori, il radar ti avvisa.
- L'etichetta "Non ancora visto su Immobiliare.it" confronta prezzo e metri quadri con gli annunci già
  letti sul portale: è un'indicazione, non una certezza.
- Se un sito cambia grafica e smette di dare annunci, ricevi un avviso "fonti da controllare".
