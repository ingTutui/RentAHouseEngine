"""Il config.toml e il llm.toml del repository.

I valori (prezzo, metratura, orari, comuni, intervallo...) si leggono dal file: ritoccarli NON rompe nessun test.
I test falliscono solo se il file e' sbagliato: una chiave scritta male, un valore del tipo sbagliato, un comune
che il radar non conosce, due impostazioni che si contraddicono. Sono gli stessi errori che l'autoverifica
(radar.py --verifica) cerca prima che l'aggiornamento automatico tenga una versione nuova."""
import copy
import re
import tomllib
import xml.dom.minidom
from collections import Counter
from datetime import datetime
from pathlib import Path
from urllib.parse import urlparse

import pytest

from motore.analisi import Annuncio, scarta
from motore.avvio import _xml_windows
from motore.comuni import comune_ufficiale, normalizza, valuta_area
from motore.config import DEFAULT_CFG, carica_config, controlla_config
from motore.fonti import carica_fonti
from motore.llm import carica_modello
from motore.notifiche import _ora, in_silenzio

from conftest import RADICE, FintoTelegram, aggiungi_agenzia, config_di_prova, giro, lista

CFG = carica_config(RADICE)
FILTRI = CFG["filtri"]
FILE_CONFIG = tomllib.loads((RADICE / "config.toml").read_text(encoding="utf-8"))
# chiavi della prima versione del radar che carica_config sa ancora leggere
SEZIONI_VECCHIE = {"telegram"}
CHIAVI_VECCHIE = {("esecuzione", "max_messaggi_singoli"), ("filtri", "zone_fuori_area_azione")}


def annuncio(**k) -> Annuncio:
    base = dict(uid="u", url="https://ag.it/immobile/x-1", fonte="Agenzia Prova", tipo_fonte="agenzia",
                comune_fonte="Modena", titolo="Bilocale", testo="", prezzo=600, mq=70, locali=2)
    base.update(k)
    return Annuncio(**base)


# --- il file e' scritto bene ---------------------------------------------------------------------------------------


def test_il_config_toml_vero_e_valido():
    assert controlla_config(CFG) == []


def test_nessuna_sezione_o_chiave_sconosciuta():
    # una chiave scritta male (prezzo_mx) verrebbe ignorata in silenzio e il filtro non si applicherebbe
    sconosciute = []
    for sezione, valori in FILE_CONFIG.items():
        if sezione in SEZIONI_VECCHIE:
            continue
        if sezione not in DEFAULT_CFG:
            sconosciute.append(f"[{sezione}]")
            continue
        sconosciute += [f"[{sezione}] {k}" for k in valori
                        if k not in DEFAULT_CFG[sezione] and (sezione, k) not in CHIAVI_VECCHIE]
    assert not sconosciute, "chiavi che il radar non conosce (errore di battitura?): " + ", ".join(sconosciute)


def test_nel_config_toml_non_ci_sono_segreti():
    # token e chiavi vanno nel file .env, che non finisce su GitHub
    nomi = re.compile(r"token|secret|password|api_?key|chiave|chat_id", re.IGNORECASE)
    trovati = [f"[{s}] {k}" for s, valori in FILE_CONFIG.items() if isinstance(valori, dict)
               for k, v in valori.items() if nomi.search(k) and v not in ("", None)]
    assert not trovati, "segreti scritti in config.toml (vanno in .env): " + ", ".join(trovati)


# --- filtri: i valori scritti nel file sono quelli che il radar applica -------------------------------------------

LIMITI = {   # chiave: (campo dell'annuncio, di quanto si va oltre il limite, motivo dello scarto)
    "prezzo_min": ("prezzo", -1, "prezzo basso"),
    "prezzo_max": ("prezzo", +1, "prezzo alto"),
    "mq_min": ("mq", -1, "metratura piccola"),
    "mq_max": ("mq", +1, "metratura grande"),
    "locali_min": ("locali", -1, "pochi locali"),
}


@pytest.mark.parametrize("chiave", LIMITI)
def test_il_limite_scritto_in_config_si_applica_esattamente(chiave):
    campo, passo, motivo = LIMITI[chiave]
    valore = FILTRI[chiave]
    cfg = copy.deepcopy(CFG)
    cfg["filtri"].update(prezzo_min=0, prezzo_max=0, mq_min=0, mq_max=0, locali_min=0, comuni_area=[])
    cfg["filtri"][chiave] = valore                       # una soglia alla volta, con il valore vero
    if not valore:                                       # 0 = nessun limite: niente da scartare
        assert scarta(annuncio(**{campo: 1}), cfg) == "" and scarta(annuncio(**{campo: 10**6}), cfg) == ""
        return
    assert scarta(annuncio(**{campo: valore}), cfg) == ""                    # sul limite: arriva
    assert scarta(annuncio(**{campo: valore + passo}), cfg) == motivo        # appena oltre: no


# --- area di ricerca -----------------------------------------------------------------------------------------------


def test_i_comuni_dell_area_sono_nomi_che_il_radar_conosce():
    ignoti = [c for c in FILTRI["comuni_area"] if comune_ufficiale(c) != c]
    assert not ignoti, f"comuni scritti diversamente dal nome ufficiale o sconosciuti: {ignoti}"


@pytest.mark.parametrize("comune", FILTRI["comuni_area"])
def test_un_annuncio_in_un_comune_dell_area_e_dentro(comune):
    assert valuta_area(comune, None, f"Bilocale a {comune}", FILTRI)[0] == "dentro"


def test_un_comune_noto_ma_non_in_elenco_e_fuori():
    candidati = ["Vignola", "Pavullo nel Frignano", "Mirandola", "Bologna", "Parma", "Zola Predosa"]
    area = {normalizza(c) for c in FILTRI["comuni_area"]} | {normalizza(c) for c in FILTRI["frazioni_area"]}
    fuori = next(c for c in candidati if normalizza(c) not in area)
    atteso = "fuori" if FILTRI["comuni_area"] else "sconosciuto"     # area vuota: nessun controllo sul comune
    assert valuta_area(fuori, None, "", FILTRI)[0] == atteso


def test_un_comune_non_e_insieme_dentro_e_fuori_area():
    # Carpi in comuni_area e anche in zone_fuori_area: ogni annuncio che lo cita finirebbe "incerto"
    dentro = {normalizza(c) for c in FILTRI["comuni_area"]}
    doppi = [z for z in FILTRI["zone_fuori_area"] if normalizza(z) in dentro]
    assert not doppi, f"nello stesso tempo in comuni_area e in zone_fuori_area: {doppi}"


def test_i_comuni_con_solo_alcune_frazioni():
    interi = {normalizza(c) for c in FILTRI["comuni_area"]}
    for comune, frazioni in FILTRI["frazioni_area"].items():
        assert comune_ufficiale(comune) == comune, f"{comune}: non e' il nome ufficiale di un comune"
        assert normalizza(comune) not in interi, f"{comune} e' gia' tutto in comuni_area: le frazioni non servono"
        for frazione in frazioni:
            assert valuta_area(comune, frazione, "", FILTRI)[0] == "dentro", f"{frazione} ({comune})"


# --- orari e intervallo ---------------------------------------------------------------------------------------------


def test_orario_di_silenzio_come_scritto_in_config():
    dalle, alle = CFG["notifiche"]["silenzio_dalle"], CFG["notifiche"]["silenzio_alle"]
    assert bool(dalle) == bool(alle), "silenzio_dalle e silenzio_alle: o tutti e due o nessuno"

    def silenzio_alle(minuto_del_giorno: int) -> bool:      # anche negativo o oltre 1440: si riporta nelle 24 ore
        m = minuto_del_giorno % 1440
        return in_silenzio(datetime(2026, 10, 6, m // 60, m % 60), dalle, alle)

    def in_minuti(ora: str) -> int:
        h, m = _ora(ora)
        return h * 60 + m

    if not dalle:
        assert not any(silenzio_alle(m) for m in range(0, 1440, 30))     # nessun silenzio
        return
    inizio, fine = in_minuti(dalle), in_minuti(alle)
    if inizio == fine:
        assert not silenzio_alle(inizio)      # stesso orario: nessun silenzio
        return
    durata = (fine - inizio) % 1440           # anche a cavallo della mezzanotte
    assert silenzio_alle(inizio) and silenzio_alle(inizio + durata // 2) and silenzio_alle(fine - 1)
    assert not silenzio_alle(fine) and not silenzio_alle(inizio - 1)


def test_l_intervallo_in_config_diventa_un_avvio_valido():
    minuti = CFG["esecuzione"]["intervallo_minuti"]
    x = _xml_windows(r"C:\radar\.venv\Scripts\pythonw.exe", Path(r"C:\radar\radar.py"), Path(r"C:\radar"), minuti)
    dom = xml.dom.minidom.parseString(x.replace('encoding="UTF-16"', ""))
    assert dom.getElementsByTagName("Interval")[0].firstChild.data == f"PT{minuti}M"


# --- llm.toml --------------------------------------------------------------------------------------------------------


def test_llm_toml_si_legge_e_ha_un_modello():
    file = RADICE / CFG["llm"]["file_modello"]
    assert file.is_file(), f"{file.name}: il file indicato in [llm] file_modello non esiste"
    tomllib.loads(file.read_text(encoding="utf-8"))        # se e' scritto male il radar lo ignora e spegne l'LLM
    modello = carica_modello(RADICE, CFG, {})
    assert modello["file"], "llm.toml non e' stato letto"
    assert re.fullmatch(r"[\w.-]+/[\w.:-]+", modello["modello"]), \
        f"il modello deve essere nel formato fornitore/nome di openrouter.ai/models: {modello['modello']!r}"
    assert all("/" in m for m in modello["riserva"])
    assert modello["temperatura"] is None or isinstance(modello["temperatura"], float)
    assert modello["max_token"] > 0 and modello["timeout"] > 0


# --- fonti ------------------------------------------------------------------------------------------------------------


def test_le_fonti_rispettano_le_regole():
    fonti = carica_fonti(RADICE / "fonti")
    assert fonti, "nessuna fonte attiva in fonti/*.csv"
    doppi = [u for u, n in Counter(f.url for f in fonti).items() if n > 1]
    assert not doppi, f"fonti ripetute (si leggerebbero due volte): {doppi}"
    assert all(f.nome for f in fonti), "una fonte senza nome"
    # Facebook mai; Idealista solo con la sua API ufficiale, mai leggendo il sito
    vietate = [f.url for f in fonti if re.search(r"facebook|instagram|idealista|(^|\.)fb\.com$", urlparse(f.url).netloc)]
    assert not vietate, f"fonti non ammesse: {vietate}"
    sbagliati = [f"{f.nome}: {f.comune}" for f in fonti if f.comune and not comune_ufficiale(f.comune)]
    assert not sbagliati, f"comune della fonte non riconosciuto: {sbagliati}"


# --- il radar intero con il config.toml vero -----------------------------------------------------------------------


def valori_dentro_i_filtri(f: dict) -> tuple[int, int, int]:
    """Prezzo, metri quadri e locali che rispettano i filtri del file (sul limite, dove c'e')."""
    prezzo = f["prezzo_max"] or max(f["prezzo_min"], 600)
    mq = f["mq_min"] or (min(70, f["mq_max"]) if f["mq_max"] else 70)
    return prezzo, mq, max(f["locali_min"], 2)


def test_un_giro_completo_con_i_filtri_veri(cartella_vera, server):
    f = carica_config(cartella_vera)["filtri"]
    comune = f["comuni_area"][0] if f["comuni_area"] else "Modena"
    prezzo, mq, locali = valori_dentro_i_filtri(f)

    def riga(mq, prezzo):     # titoli neutri: "Bilocale" nel titolo varrebbe 2 locali anche con locali_min piu' alto
        return f"{mq} mq · € {prezzo}/mese · {locali} locali"

    tg = FintoTelegram(server)
    vecchio = [("appartamento-vecchio-1001", f"Appartamento vecchio a {comune}", riga(mq + 25, prezzo // 2))]
    server.pagina("/a/affitti/", lista("a", vecchio))
    aggiungi_agenzia(cartella_vera, "Agenzia Prova", comune, server.url("/a/affitti/"))
    assert giro(cartella_vera) == 0 and tg.messaggi == []                  # prima lettura: salva senza avvisare
    nuovi = [("appartamento-buono-1002", f"Appartamento buono a {comune}", riga(mq, prezzo))]     # sul limite: arriva
    if f["prezzo_max"]:
        nuovi.append(("appartamento-caro-1003", f"Appartamento caro a {comune}", riga(mq, f["prezzo_max"] + 100)))
    if f["mq_min"] > 1:
        nuovi.append(("appartamento-piccolo-1004", f"Appartamento piccolo a {comune}", riga(f["mq_min"] - 1, prezzo)))
    server.pagina("/a/affitti/", lista("a", vecchio + nuovi))
    assert giro(cartella_vera) == 1
    assert len(tg.messaggi) == 1 and "Appartamento buono" in tg.messaggi[0]


# --- controlla_config: gli errori che l'autoverifica deve fermare ---------------------------------------------------

SBAGLIATI = [
    ("filtri", "prezzo_max", "800"),                         # tra virgolette: il filtro darebbe TypeError
    ("filtri", "prezzo_max", -1),
    ("filtri", "prezzo_max", True),
    ("filtri", "mq_min", "60"),
    ("filtri", "locali_min", 1.5),
    ("filtri", "escludi_stanze", "true"),
    ("filtri", "dati_mancanti", "boh"),
    ("filtri", "fuori_zona", "forse"),
    ("filtri", "tipi_esclusi", ["", "negozio"]),             # la stringa vuota scarterebbe ogni annuncio
    ("filtri", "tipi_esclusi", "negozio"),
    ("filtri", "comuni_area", ["Modena", "  "]),
    ("filtri", "comuni_area", "Modena"),
    ("filtri", "zone_fuori_area", [1, 2]),
    ("filtri", "frazioni_area", {"Carpi": []}),
    ("filtri", "frazioni_area", {"Carpi": "Gargallo"}),
    ("filtri", "frazioni_area", ["Carpi"]),
    ("notifiche", "silenzio_dalle", "22"),                    # orario scritto male: il radar lo ignorerebbe
    ("notifiche", "silenzio_dalle", "25:00"),
    ("notifiche", "silenzio_dalle", ""),                      # uno solo dei due vuoto
    ("notifiche", "silenzio_dalle", 22),
    ("notifiche", "max_messaggi_singoli", 0),
    ("notifiche", "anteprima_link", "si"),
    ("llm", "max_spesa_giornaliera_usd", "1"),
    ("llm", "max_chiamate_al_giorno", 1.5),
    ("llm", "usa", "si"),
    ("llm", "file_modello", ""),
    ("esecuzione", "intervallo_minuti", "ogni ora"),
    ("esecuzione", "intervallo_minuti", 0),
    ("esecuzione", "intervallo_minuti", 30.5),
    ("esecuzione", "connessioni_parallele", 0),
    ("esecuzione", "timeout_sec", 0),
    ("aggiornamento", "automatico", "true"),
    ("aggiornamento", "ramo", ""),
    ("doppioni", "tolleranza_prezzo_pct", "3"),
]


@pytest.mark.parametrize(("sezione", "chiave", "valore"), SBAGLIATI)
def test_un_valore_sbagliato_viene_segnalato(sezione, chiave, valore):
    cfg = config_di_prova()
    assert controlla_config(cfg) == []
    cfg[sezione][chiave] = valore
    problemi = controlla_config(cfg)
    assert problemi and any(chiave in p for p in problemi), problemi


BUONI = [
    ("filtri", "prezzo_max", 0), ("filtri", "prezzo_max", 1250.5), ("filtri", "mq_min", 0), ("filtri", "mq_max", 200),
    ("filtri", "locali_min", 3), ("filtri", "fuori_zona", "Segnala"), ("filtri", "comuni_area", []),
    ("filtri", "zone_fuori_area", []), ("filtri", "frazioni_area", {}), ("filtri", "tipi_esclusi", []),
    ("notifiche", "silenzio_dalle", "22.00"), ("notifiche", "silenzio_dalle", "8:30"),
    ("llm", "max_spesa_giornaliera_usd", 0), ("llm", "max_spesa_giornaliera_usd", 0.25), ("llm", "usa", False),
    ("esecuzione", "pausa_stesso_sito_sec", 0), ("esecuzione", "intervallo_minuti", 15),
    ("aggiornamento", "automatico", False),
]


@pytest.mark.parametrize(("sezione", "chiave", "valore"), BUONI)
def test_i_valori_legittimi_non_vengono_segnalati(sezione, chiave, valore):
    cfg = config_di_prova()
    cfg[sezione][chiave] = valore
    assert controlla_config(cfg) == []


def test_nessun_silenzio_se_tutti_e_due_gli_orari_sono_vuoti():
    cfg = config_di_prova()
    cfg["notifiche"].update(silenzio_dalle="", silenzio_alle="")
    assert controlla_config(cfg) == []


@pytest.mark.parametrize("testo", ["22:00", "22.00", "8:00", " 22:00 ", "00:00", "23:59", "22", "ore 22", "25:00",
                                   "22:60", "", "22:0", "2200", "22:00:00"])
def test_gli_orari_validi_sono_gli_stessi_che_legge_il_radar(testo):
    # controlla_config non importa notifiche (usa requests): le due regole non devono divergere
    cfg = config_di_prova()
    cfg["notifiche"].update(silenzio_dalle=testo, silenzio_alle="08:00")
    assert (controlla_config(cfg) == []) == (_ora(testo) is not None)


@pytest.mark.parametrize(("minimo", "massimo"), [("prezzo_min", "prezzo_max"), ("mq_min", "mq_max")])
def test_minimo_maggiore_del_massimo_viene_segnalato(minimo, massimo):
    cfg = config_di_prova()
    cfg["filtri"].update({minimo: 500, massimo: 400})
    assert any(minimo in p for p in controlla_config(cfg))
    cfg["filtri"][massimo] = 0                               # massimo 0 = nessun limite: va bene
    assert controlla_config(cfg) == []


def test_controlla_config_non_fa_errori_su_una_configurazione_vuota():
    # config.toml con solo qualche sezione: il resto arriva dai valori predefiniti
    assert controlla_config(copy.deepcopy(DEFAULT_CFG)) == []
