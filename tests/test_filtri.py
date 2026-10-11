import copy

import pytest

from motore.analisi import Annuncio, analizza, aggiorna_area, e_stanza, importi, leggi_numeri, scarta
from motore.comuni import comune_ufficiale, trova_luogo, valuta_area
from motore.fonti import Fonte

from conftest import config_di_prova

CFG = config_di_prova()
AGENZIA = Fonte("agenzia", "Agenzia Prova", "Sassuolo", "https://ag.it/affitti/")


def nuovo(**k) -> Annuncio:
    base = dict(uid="u", url="https://ag.it/immobile/x-1", fonte="Agenzia Prova", tipo_fonte="agenzia",
                comune_fonte="Sassuolo", titolo="Bilocale", testo="")
    base.update(k)
    return Annuncio(**base)


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
    f = CFG["filtri"]                            # Limidi: frazione di Soliera esclusa (vedi FILTRI_PROVA)
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


NESSUN_LIMITE = dict(prezzo_min=0, prezzo_max=0, mq_min=0, mq_max=0, locali_min=0)
SOGLIE = [   # (chiave in config, valore, annuncio sul limite, annuncio appena oltre, motivo dello scarto)
    ("prezzo_min", 400, dict(prezzo=400, mq=70), dict(prezzo=399, mq=70), "prezzo basso"),
    ("prezzo_max", 700, dict(prezzo=700, mq=70), dict(prezzo=701, mq=70), "prezzo alto"),
    ("mq_min", 55, dict(prezzo=600, mq=55), dict(prezzo=600, mq=54), "metratura piccola"),
    ("mq_max", 120, dict(prezzo=600, mq=120), dict(prezzo=600, mq=121), "metratura grande"),
    ("locali_min", 2, dict(prezzo=600, mq=70, locali=2), dict(prezzo=600, mq=70, locali=1), "pochi locali"),
]


@pytest.mark.parametrize(("chiave", "valore", "sul_limite", "oltre", "motivo"), SOGLIE, ids=[s[0] for s in SOGLIE])
def test_ogni_soglia_vale_esattamente_come_scritta(chiave, valore, sul_limite, oltre, motivo):
    cfg = copy.deepcopy(CFG)
    cfg["filtri"].update(NESSUN_LIMITE)           # una soglia alla volta
    cfg["filtri"][chiave] = valore
    assert scarta(nuovo(**sul_limite), cfg) == ""      # il valore scritto in config e' ancora ammesso
    assert scarta(nuovo(**oltre), cfg) == motivo
    cfg["filtri"][chiave] = 0                          # 0 = nessun limite
    assert scarta(nuovo(**oltre), cfg) == ""


def test_senza_limiti_e_senza_area_non_si_scarta_per_prezzo_metratura_o_comune():
    cfg = copy.deepcopy(CFG)
    cfg["filtri"].update(NESSUN_LIMITE, comuni_area=[], dati_mancanti="scarta")
    assert scarta(nuovo(prezzo=5000, mq=15), cfg) == ""
    assert scarta(nuovo(prezzo=None, mq=None), cfg, finale=True) == ""   # senza soglie il dato mancante non conta
    a = nuovo(prezzo=600, mq=60, comune="Vignola", origine_luogo="dettaglio")
    aggiorna_area(a, cfg)
    assert a.area == "sconosciuto" and scarta(a, cfg) == ""              # area non configurata: nessuno scarto


@pytest.mark.parametrize("tipo", CFG["filtri"]["tipi_esclusi"])
def test_tipi_esclusi(tipo):
    assert scarta(nuovo(titolo=f"{tipo.capitalize()} in affitto", prezzo=500, mq=70), CFG) != ""
    assert scarta(nuovo(titolo="Bilocale luminoso", prezzo=500, mq=70), CFG) == ""


def test_fuori_zona_segnala_non_scarta():
    cfg = copy.deepcopy(CFG)
    cfg["filtri"]["fuori_zona"] = "segnala"
    a = nuovo(prezzo=600, mq=60, comune="Vignola", origine_luogo="dettaglio")
    aggiorna_area(a, cfg)
    assert a.area == "fuori" and scarta(a, cfg) == ""


def test_stanze_se_non_escluse_arrivano():
    cfg = copy.deepcopy(CFG)
    cfg["filtri"]["escludi_stanze"] = False
    assert scarta(nuovo(prezzo=350, mq=None, stanza=True), cfg) == ""


def test_prezzo_seguito_dalla_metratura():
    # "€ 700" e "105 mq" in due elementi vicini della scheda: il testo li unisce in "€ 700 105 mq"
    assert importi("€ 700 105 mq") == [700] and importi("€ 700 120 m²") == [700]
    assert importi("€ 125 000") == [125000] and importi("1 200 €") == [1200] and importi("€ 1.200/mese") == [1200]
    n = leggi_numeri("Trilocale € 700 105 mq", "https://ag.it/immobile/trilocale-1", CFG)
    assert (n["prezzo"], n["mq"], n["vendita"]) == (700, 105, False)
