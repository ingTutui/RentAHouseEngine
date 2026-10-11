import json

from motore import doppioni
from motore.analisi import Annuncio
from motore.archivio import Archivio
from motore.config import carica_config

from conftest import RADICE

CFG = carica_config(RADICE)


def annuncio(uid, fonte="Immobiliare.it", tipo="portale", **k) -> Annuncio:
    base = dict(uid=uid, url=f"https://x.it/{uid}", fonte=fonte, tipo_fonte=tipo, comune_fonte=k.pop("sede", ""),
                titolo=k.pop("titolo", "Bilocale"), testo="")
    base.update(k)
    return Annuncio(**base)


class LLMFinto:
    def __init__(self, risposta):
        self.risposta = risposta
        self.domande = []

    def disponibile(self):
        return True

    def chiedi_json(self, compito, sistema, utente, schema):
        self.domande.append(utente)
        return self.risposta


def test_stesso_immobile_su_agenzia_e_portale(tmp_path):
    arc = Archivio(tmp_path / "r.db")
    arc.salva(annuncio("immobiliare.it:1", prezzo=680, mq=58, locali=2, comune="Formigine",
                       titolo="Bilocale via Giardini, Formigine"), "inviato")
    nuovo = annuncio("https://ag.it/b-1", fonte="Agenzia Rossi", tipo="agenzia", prezzo=680, mq=58, locali=2,
                     comune="Formigine", indirizzo="Via Giardini 12")
    e = doppioni.cerca(nuovo, arc, None, CFG)
    assert (e.gruppo, e.certezza) == ("immobiliare.it:1", "sicuro")


def test_ripubblicato_con_prezzo_ritoccato(tmp_path):
    arc = Archivio(tmp_path / "r.db")
    arc.salva(annuncio("casa.it:1", fonte="Casa.it", prezzo=700, mq=62, locali=3, comune="Sassuolo"), "inviato")
    e = doppioni.cerca(annuncio("casa.it:2", fonte="Casa.it", prezzo=690, mq=62, locali=3, comune="Sassuolo"), arc,
                       None, CFG)
    assert e.gruppo == "casa.it:1"


def test_immobili_diversi(tmp_path):
    arc = Archivio(tmp_path / "r.db")
    arc.salva(annuncio("immobiliare.it:1", prezzo=650, mq=60, locali=2, comune="Modena"), "inviato")
    assert doppioni.cerca(annuncio("immobiliare.it:2", prezzo=650, mq=80, locali=3, comune="Modena"), arc, None,
                          CFG).gruppo == ""
    assert doppioni.cerca(annuncio("immobiliare.it:3", prezzo=650, mq=60, locali=2, comune="Sassuolo"), arc, None,
                          CFG).gruppo == ""
    assert doppioni.cerca(annuncio("immobiliare.it:4", prezzo=560, mq=60, locali=2, comune="Modena"), arc, None,
                          CFG).gruppo == ""


def test_scartati_non_contano(tmp_path):
    arc = Archivio(tmp_path / "r.db")
    arc.salva(annuncio("immobiliare.it:1", prezzo=650, mq=60, locali=2, comune="Modena"), "scartato", "fuori zona")
    assert doppioni.cerca(annuncio("casa.it:9", fonte="Casa.it", prezzo=650, mq=60, locali=2, comune="Modena"), arc,
                          None, CFG).gruppo == ""


def test_sede_agenzia_come_indizio(tmp_path):
    arc = Archivio(tmp_path / "r.db")
    arc.salva(annuncio("immobiliare.it:1", prezzo=620, mq=70, locali=3, comune="Maranello"), "inviato")
    stessa_sede = annuncio("https://ag.it/t-1", fonte="Ag", tipo="agenzia", sede="Maranello", prezzo=620, mq=70,
                           locali=3)
    assert doppioni.cerca(stessa_sede, arc, None, CFG).certezza == "sicuro"
    altra_sede = annuncio("https://ag2.it/t-1", fonte="Ag2", tipo="agenzia", sede="Vignola", prezzo=620, mq=70,
                          locali=3)
    e = doppioni.cerca(altra_sede, arc, None, CFG)
    assert e.gruppo == "" and e.simile is not None          # senza LLM: arriva con "forse e' lo stesso di"


def test_nei_dubbi_decide_llm(tmp_path):
    arc = Archivio(tmp_path / "r.db")
    arc.salva(annuncio("immobiliare.it:1", prezzo=620, mq=70, locali=3, comune="Maranello"), "inviato")
    dubbio = annuncio("https://ag2.it/t-1", fonte="Ag2", tipo="agenzia", sede="Vignola", prezzo=620, mq=70, locali=3)
    si = LLMFinto({"stesso_di": 1, "certezza": 0.9, "motivo": "stessa via"})
    assert doppioni.cerca(dubbio, arc, si, CFG).certezza == "llm"
    assert "Candidati" in si.domande[0]
    no = LLMFinto({"stesso_di": 0, "certezza": 0.8, "motivo": "diversi"})
    e = doppioni.cerca(dubbio, arc, no, CFG)
    assert e.gruppo == "" and e.simile is None
    incerto = LLMFinto({"stesso_di": 1, "certezza": 0.4, "motivo": "boh"})
    assert doppioni.cerca(dubbio, arc, incerto, CFG).gruppo == ""


def test_senza_prezzo_confronta_la_metratura(tmp_path):
    arc = Archivio(tmp_path / "r.db")
    arc.salva(annuncio("https://ag.it/x-1", fonte="Ag", tipo="agenzia", prezzo=None, mq=64, locali=2,
                       comune="Spilamberto", titolo="Bilocale arredato centro Spilamberto"), "inviato")
    e = doppioni.cerca(annuncio("immobiliare.it:7", prezzo=600, mq=64, locali=2, comune="Spilamberto",
                                titolo="Bilocale arredato centro Spilamberto"), arc, None, CFG)
    assert e.gruppo == "https://ag.it/x-1"
    assert json.loads(arc.db.execute("SELECT dati FROM annunci").fetchone()[0])["mq"] == 64
