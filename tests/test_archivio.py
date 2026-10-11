import sqlite3

from motore.analisi import Annuncio
from motore.archivio import Archivio


def test_migrazione_dalla_prima_versione(tmp_path):
    p = tmp_path / "radar.db"
    db = sqlite3.connect(p)
    db.executescript("""
    CREATE TABLE annunci (uid TEXT PRIMARY KEY, url TEXT, fonte TEXT, tipo_fonte TEXT, comune TEXT, titolo TEXT,
        prezzo INTEGER, mq INTEGER, locali INTEGER, primo_visto TEXT, ultimo_visto TEXT,
        notificato INTEGER DEFAULT 0, scartato TEXT DEFAULT '');
    CREATE TABLE fonti (url TEXT PRIMARY KEY, nome TEXT, inizializzata INTEGER DEFAULT 0, ultimo_ok TEXT,
        ultimo_errore TEXT DEFAULT '', errori_consecutivi INTEGER DEFAULT 0, ultimi_link INTEGER DEFAULT 0,
        avvisato INTEGER DEFAULT 0);
    CREATE TABLE impostazioni (chiave TEXT PRIMARY KEY, valore TEXT);
    INSERT INTO annunci VALUES ('immobiliare.it:1','https://www.immobiliare.it/annunci/1','Immobiliare.it','portale',
        'Modena','Bilocale',650,60,2,'2026-10-06 10:00','2026-10-06 10:00',1,'');
    INSERT INTO annunci VALUES ('https://ag.it/immobile/a-1','https://ag.it/immobile/a-1','Agenzia','agenzia',
        'Sassuolo','Trilocale a Formigine',600,80,3,'2026-10-06 10:00','2026-10-06 10:00',0,'prima lettura');
    INSERT INTO annunci VALUES ('https://ag.it/immobile/v-1','https://ag.it/immobile/v-1','Agenzia','agenzia',
        '','Villa in vendita',NULL,200,NULL,'2026-10-06 10:00','2026-10-06 10:00',0,'vendita');
    INSERT INTO fonti VALUES ('https://ag.it/affitti/','Agenzia',1,'2026-10-06 10:00','',0,5,0);
    """)
    db.commit()
    db.close()
    a = Archivio(p)
    stati = dict(a.db.execute("SELECT uid, stato FROM annunci").fetchall())
    assert stati == {"immobiliare.it:1": "inviato", "https://ag.it/immobile/a-1": "base",
                     "https://ag.it/immobile/v-1": "scartato"}
    assert a.gruppo_di("immobiliare.it:1") == "immobiliare.it:1"
    # per le agenzie il comune diventa quello citato nel titolo, non la sede
    assert a.annuncio("https://ag.it/immobile/a-1").comune == "Formigine"
    assert a.get("schema") == "2"
    assert a.fonte("https://ag.it/affitti/")["inizializzata"] is True
    a.chiudi()
    Archivio(p).chiudi()  # la seconda apertura non cambia niente


def test_salute_delle_fonti(tmp_path):
    a = Archivio(tmp_path / "radar.db")
    u = "https://ag.it/affitti/"
    a.esito_fonte(u, "Ag", "", 6)
    assert a.problemi(0) == []
    a.esito_fonte(u, "Ag", "HTTP 500", 0)
    a.esito_fonte(u, "Ag", "HTTP 500", 0)
    p = a.problemi(0)
    assert len(p) == 1 and p[0]["nota"] == "HTTP 500"
    assert a.problemi(24) == []                 # non ancora "rotta da un giorno"
    a.esito_fonte(u, "Ag", "", 0)               # risponde ma senza annunci: resta un problema
    assert "nessun annuncio" in a.problemi(0)[0]["nota"]
    a.esito_fonte(u, "Ag", "", 4)               # tornata a posto
    assert a.problemi(0) == []


def test_coda(tmp_path):
    a = Archivio(tmp_path / "radar.db")
    x = Annuncio(uid="u1", url="https://ag.it/1", fonte="Ag", tipo_fonte="agenzia", comune_fonte="", titolo="T",
                 testo="", prezzo=600, mq=60)
    a.salva(x, "da_inviare", priorita=0)
    assert [y.uid for y in a.in_coda()] == ["u1"]
    a.segna_inviati(["u1"])
    assert a.in_coda() == [] and a.stato_di("u1") == "inviato"


def test_prima_lettura_vuota_non_inizializza(tmp_path):
    a = Archivio(tmp_path / "radar.db")
    u = "https://ag.it/affitti/"
    a.esito_fonte(u, "Ag", "", 0)               # letta, ma senza annunci (sito in JavaScript, captcha...)
    assert a.fonte(u)["inizializzata"] is False and a.fonte(u)["vuota"] is True
    a.esito_fonte(u, "Ag", "HTTP 500", 0)
    a.esito_fonte(u, "Ag", "", 0)               # dopo un errore, ancora vuota: non inizializzata
    assert a.fonte(u)["inizializzata"] is False
    a.esito_fonte(u, "Ag", "", 3)
    assert a.fonte(u)["inizializzata"] is True and a.fonte(u)["vuota"] is False
    a.esito_fonte(u, "Ag", "", 0)               # gia' inizializzata: resta tale
    assert a.fonte(u)["inizializzata"] is True
    a.chiudi()
