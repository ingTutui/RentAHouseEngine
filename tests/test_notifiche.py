import copy
import json
from datetime import datetime, timedelta

from motore.analisi import Annuncio
from motore.archivio import Archivio
from motore.config import carica_config
from motore.notifiche import Telegram, consegna, in_silenzio, messaggio

from conftest import RADICE, FintoTelegram

CFG = carica_config(RADICE)


def test_orario_di_silenzio():
    g = datetime(2026, 10, 6)
    s = [("21:59", False), ("22:00", True), ("23:30", True), ("03:00", True), ("07:59", True), ("08:00", False),
         ("12:00", False)]
    for ora, atteso in s:
        h, m = map(int, ora.split(":"))
        assert in_silenzio(g.replace(hour=h, minute=m), "22:00", "08:00") is atteso, ora
    assert in_silenzio(g.replace(hour=14), "13:00", "15:00")
    assert not in_silenzio(g.replace(hour=16), "13:00", "15:00")
    assert not in_silenzio(g.replace(hour=23), "", "")
    assert not in_silenzio(g.replace(hour=23), "ore 22", "08:00")       # orario scritto male: nessun silenzio


def metti_in_coda(arc, n, trovato="2026-10-06 23:10", tipo="agenzia"):
    for i in range(n):
        a = Annuncio(uid=f"u{i}", url=f"https://ag.it/immobile/{i}", fonte="Agenzia Rossi", tipo_fonte=tipo,
                     comune_fonte="Formigine", titolo=f"Bilocale {i}", testo="", prezzo=600 + i, mq=60, locali=2,
                     comune="Formigine", trovato_il=trovato, riassunto="Secondo piano con ascensore")
        arc.salva(a, "da_inviare", priorita=0)


def test_di_notte_in_coda_alle_8_tutto_insieme(tmp_path, server):
    tg_finto = FintoTelegram(server)
    tg = Telegram({"TELEGRAM_TOKEN": "TOKEN", "TELEGRAM_CHAT_ID": "42", "TELEGRAM_API_URL": server.url("")})
    arc = Archivio(tmp_path / "r.db")
    metti_in_coda(arc, 2)
    assert consegna(arc, tg, CFG, datetime(2026, 10, 6, 23, 30)) == 0
    assert consegna(arc, tg, CFG, datetime(2026, 10, 7, 7, 59)) == 0
    assert tg_finto.messaggi == []
    assert consegna(arc, tg, CFG, datetime(2026, 10, 7, 8, 5)) == 2
    assert len(tg_finto.messaggi) == 2
    assert "Non ancora su Immobiliare.it" in tg_finto.messaggi[0]
    assert "Trovato il 06/10 alle 23:10" in tg_finto.messaggi[0]
    # nessuna novita': nessun messaggio
    assert consegna(arc, tg, CFG, datetime(2026, 10, 7, 8, 35)) == 0
    assert len(tg_finto.messaggi) == 2


def test_anteprima_del_link_punta_all_annuncio(tmp_path, server):
    tg_finto = FintoTelegram(server)
    tg = Telegram({"TELEGRAM_TOKEN": "TOKEN", "TELEGRAM_CHAT_ID": "42", "TELEGRAM_API_URL": server.url("")})
    arc = Archivio(tmp_path / "r.db")
    metti_in_coda(arc, 1)
    assert consegna(arc, tg, CFG, datetime(2026, 10, 7, 9, 0)) == 1
    campi = tg_finto.campi[0]
    assert campi["chat_id"] == "42" and campi["parse_mode"] == "HTML"
    assert json.loads(campi["link_preview_options"]) == {"is_disabled": False, "url": "https://ag.it/immobile/0"}
    # con "anteprima_link = false" il messaggio parte senza anteprima
    cfg = copy.deepcopy(CFG)
    cfg["notifiche"]["anteprima_link"] = False
    arc2 = Archivio(tmp_path / "r2.db")
    metti_in_coda(arc2, 1)
    assert consegna(arc2, tg, cfg, datetime(2026, 10, 7, 9, 30)) == 1
    assert json.loads(tg_finto.campi[1]["link_preview_options"]) == {"is_disabled": True}


def test_tanti_annunci_un_solo_elenco(tmp_path, server):
    tg_finto = FintoTelegram(server)
    tg = Telegram({"TELEGRAM_TOKEN": "TOKEN", "TELEGRAM_CHAT_ID": "42", "TELEGRAM_API_URL": server.url("")})
    arc = Archivio(tmp_path / "r.db")
    metti_in_coda(arc, 20)
    assert consegna(arc, tg, CFG, datetime(2026, 10, 7, 8, 5)) == 20
    assert len(tg_finto.messaggi) == 1
    assert tg_finto.messaggi[0].startswith("<b>20 nuovi affitti</b> trovati stanotte")


def test_se_telegram_non_risponde_restano_in_coda(tmp_path, server):
    FintoTelegram(server, rifiuta=True)
    tg = Telegram({"TELEGRAM_TOKEN": "TOKEN", "TELEGRAM_CHAT_ID": "42", "TELEGRAM_API_URL": server.url("")})
    arc = Archivio(tmp_path / "r.db")
    metti_in_coda(arc, 3)
    assert consegna(arc, tg, CFG, datetime(2026, 10, 7, 9, 0)) == 0
    assert len(arc.in_coda()) == 3


def test_guasti_solo_in_coda_a_un_messaggio(tmp_path, server):
    tg_finto = FintoTelegram(server)
    tg = Telegram({"TELEGRAM_TOKEN": "TOKEN", "TELEGRAM_CHAT_ID": "42", "TELEGRAM_API_URL": server.url("")})
    arc = Archivio(tmp_path / "r.db")
    arc.set("primo_avvio", "2026-10-01 10:00")
    arc.esito_fonte("https://rotta.it/", "Agenzia Rotta", "HTTP 500", 0)
    arc.db.execute("UPDATE fonti SET problema_dal='2026-10-02 10:00'")
    adesso = datetime.now()
    arc.set("ultimo_invio", (adesso - timedelta(hours=5)).strftime("%Y-%m-%d %H:%M"))
    cfg = copy.deepcopy(CFG)
    cfg["notifiche"]["silenzio_dalle"] = ""
    # nessuna novita' e ultimo messaggio di poche ore fa: niente messaggio "solo guasti"
    assert consegna(arc, tg, cfg, adesso) == 0 and tg_finto.messaggi == []
    metti_in_coda(arc, 1, trovato=adesso.strftime("%Y-%m-%d %H:%M"))
    assert consegna(arc, tg, cfg, adesso) == 1
    assert "Da controllare: 1 fonti non funzionano" in tg_finto.messaggi[0] and "Agenzia Rotta" in tg_finto.messaggi[0]
    # la settimana dopo non lo ripete subito
    metti_in_coda(arc, 1, trovato=adesso.strftime("%Y-%m-%d %H:%M"))
    arc.db.execute("UPDATE annunci SET uid='nuovo', stato='da_inviare' WHERE uid='u0' AND stato='inviato'")
    consegna(arc, tg, cfg, adesso + timedelta(hours=1))
    assert all("Da controllare" not in m for m in tg_finto.messaggi[1:])


def test_guasti_da_soli_dopo_tre_giorni_di_silenzio(tmp_path, server):
    tg_finto = FintoTelegram(server)
    tg = Telegram({"TELEGRAM_TOKEN": "TOKEN", "TELEGRAM_CHAT_ID": "42", "TELEGRAM_API_URL": server.url("")})
    arc = Archivio(tmp_path / "r.db")
    adesso = datetime.now()
    arc.set("primo_avvio", (adesso - timedelta(days=10)).strftime("%Y-%m-%d %H:%M"))
    arc.set("ultimo_invio", (adesso - timedelta(days=4)).strftime("%Y-%m-%d %H:%M"))
    arc.esito_fonte("https://rotta.it/", "Agenzia Rotta", "HTTP 500", 0)
    arc.db.execute("UPDATE fonti SET problema_dal=?", ((adesso - timedelta(days=2)).strftime("%Y-%m-%d %H:%M"),))
    cfg = copy.deepcopy(CFG)
    cfg["notifiche"]["silenzio_dalle"] = ""
    assert consegna(arc, tg, cfg, adesso) == 0
    assert len(tg_finto.messaggi) == 1 and tg_finto.messaggi[0].startswith("<b>Radar Affitti</b>: 1 fonti")
    consegna(arc, tg, cfg, adesso + timedelta(hours=1))
    assert len(tg_finto.messaggi) == 1


def test_testo_del_messaggio():
    a = Annuncio(uid="u", url="https://ag.it/immobile/1", fonte="Agenzia <Rossi> & C", tipo_fonte="agenzia",
                 comune_fonte="Formigine", titolo="Bilocale arredato", testo="", prezzo=650, mq=58, locali=2,
                 comune="Formigine", frazione="Casinalbo", spese=40, arredato="sì", trovato_il="2026-10-06 10:00",
                 riassunto="Secondo piano, garage", note="contratto 4+4")
    t = messaggio(a, [("Casa.it", "portale", "https://www.casa.it/immobili/1")], datetime(2026, 10, 6, 10, 20))
    assert "Agenzia &lt;Rossi&gt; &amp; C" in t
    assert "€ 650/mese + 40 spese · 58 mq · 2 locali · Formigine (Casinalbo)" in t
    assert "Non ancora su Immobiliare.it" in t and "Anche su:" in t and "Trovato" not in t
    t2 = messaggio(a, [("Immobiliare.it", "portale", "https://www.immobiliare.it/annunci/1")], datetime(2026, 10, 6))
    assert "Non ancora su Immobiliare.it" not in t2
