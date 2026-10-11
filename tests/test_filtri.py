import copy

from motore.analisi import Annuncio, analizza, aggiorna_area, e_stanza, importi, leggi_numeri, scarta
from motore.comuni import comune_ufficiale, trova_luogo, valuta_area
from motore.config import carica_config
from motore.fonti import Fonte

from conftest import RADICE

CFG = carica_config(RADICE)
AGENZIA = Fonte("agenzia", "Agenzia Prova", "Sassuolo", "https://ag.it/affitti/")


def nuovo(**k) -> Annuncio:
    base = dict(uid="u", url="https://ag.it/immobile/x-1", fonte="Agenzia Prova", tipo_fonte="agenzia",
                comune_fonte="Sassuolo", titolo="Bilocale", testo="")
    base.update(k)
    return Annuncio(**base)


def test_valori_scelti_da_ste():
    # l'unico test legato ai valori di config.toml: se li cambi, aggiorna anche questo (e PIANO.md)
    f = CFG["filtri"]
    assert (f["prezzo_max"], f["mq_min"], f["escludi_stanze"]) == (750, 60, True)
    assert (CFG["notifiche"]["silenzio_dalle"], CFG["notifiche"]["silenzio_alle"]) == ("22:00", "08:00")


def test_prezzo_e_metratura():
    prezzo, mq = CFG["filtri"]["prezzo_max"], CFG["filtri"]["mq_min"]
    assert scarta(nuovo(prezzo=prezzo, mq=mq), CFG) == ""
    assert scarta(nuovo(prezzo=prezzo + 50, mq=mq + 5), CFG) == "prezzo alto"
    assert scarta(nuovo(prezzo=prezzo - 50, mq=mq - 5), CFG) == "metratura piccola"


def test_dati_mancanti():
    assert scarta(nuovo(prezzo=None, mq=60), CFG, finale=True) == ""      # "invia": arriva con l'avviso
    cfg = copy.deepcopy(CFG)
    cfg["filtri"]["dati_mancanti"] = "scarta"
    assert scarta(nuovo(prezzo=None, mq=60), cfg, finale=False) == ""     # prima della pagina dell'annuncio no
    assert scarta(nuovo(prezzo=None, mq=60), cfg, finale=True) == "prezzo non indicato"
    assert scarta(nuovo(prezzo=600, mq=None), cfg, finale=True) == "metratura non indicata"


def test_stanze():
    assert e_stanza("Camera singola in affitto a studenti", "", "https://x.it/a")
    assert e_stanza("Posto letto in doppia zona università", "", "https://x.it/a")
    assert e_stanza("Affittasi stanza arredata", "", "https://x.it/a")
    assert not e_stanza("Trilocale con due camere doppie", "", "https://x.it/a")
    assert not e_stanza("Bilocale con camera matrimoniale e terrazzo", "", "https://x.it/a")
    assert scarta(nuovo(prezzo=350, mq=None, stanza=True), CFG) == "stanza"


def test_comuni_e_frazioni():
    assert trova_luogo("Bilocale a Casinalbo di Formigine") == ("Formigine", "Casinalbo")
    assert trova_luogo("Appartamento Castelvetro di Modena centro") == ("Castelvetro di Modena", None)
    assert trova_luogo("Trilocale Sassuolo (MO), provincia di Modena") == ("Sassuolo", None)
    assert trova_luogo("Trilocale con bagno e prato") == (None, None)       # parole comuni, non luoghi
    assert comune_ufficiale("Reggio Emilia") == "Reggio nell'Emilia"
    assert comune_ufficiale("Fiorano") == "Fiorano Modenese"
    assert comune_ufficiale("Baggiovara") == "Modena"


def test_area():
    f = copy.deepcopy(CFG["filtri"])
    f["zone_fuori_area"].append("Limidi")        # frazione di Soliera esclusa (fissa qui, non da config.toml)
    assert valuta_area("Formigine", None, "", f)[0] == "dentro"
    assert valuta_area("Vignola", None, "", f) == ("fuori", "Vignola")
    assert valuta_area("Carpi", "Gargallo", "", f)[0] == "dentro"
    assert valuta_area("Carpi", None, "Bilocale a Gargallo di Carpi", f)[0] == "dentro"
    assert valuta_area("Carpi", None, "Bilocale in centro", f)[0] == "incerto"
    assert valuta_area("Soliera", "Limidi", "", f)[0] == "fuori"
    assert valuta_area("Soliera", None, "a due passi da Limidi", f)[0] == "incerto"
    assert valuta_area(None, None, "bel trilocale", f)[0] == "sconosciuto"


def test_fuori_zona_scarta_solo_se_sicuro():
    a = nuovo(prezzo=600, mq=60, comune="Vignola", origine_luogo="dettaglio")
    aggiorna_area(a, CFG)
    assert scarta(a, CFG).startswith("fuori zona")
    b = nuovo(prezzo=600, mq=60, comune="Vignola", origine_luogo="testo")   # solo citato nel testo: avviso
    aggiorna_area(b, CFG)
    assert scarta(b, CFG) == ""
    c = analizza("u2", "https://ag.it/immobile/bilocale-2", "Bilocale a Pavullo 60 mq € 600/mese",
                 "Bilocale a Pavullo", AGENZIA, CFG)
    assert (c.comune, c.origine_luogo, c.area, scarta(c, CFG)) == ("Pavullo nel Frignano", "testo", "fuori", "")


def test_prezzo_seguito_dalla_metratura():
    # "€ 700" e "105 mq" in due elementi vicini della scheda: il testo li unisce in "€ 700 105 mq"
    assert importi("€ 700 105 mq") == [700] and importi("€ 700 120 m²") == [700]
    assert importi("€ 125 000") == [125000] and importi("1 200 €") == [1200] and importi("€ 1.200/mese") == [1200]
    n = leggi_numeri("Trilocale € 700 105 mq", "https://ag.it/immobile/trilocale-1", CFG)
    assert (n["prezzo"], n["mq"], n["vendita"]) == (700, 105, False)
