"""Giri completi del radar contro siti finti in locale, con Telegram e OpenRouter finti."""
import json
from datetime import datetime, timedelta

from motore import doppioni
from motore.archivio import Archivio
from motore.config import Percorsi, carica_config, carica_segreti
from motore.controllo import esegui
from motore.fonti import Fonte, pattern_sbagliati
from motore.verifica import verifica

from conftest import FintoOpenRouter, FintoTelegram, aggiungi_agenzia, scrivi_locale

LOCALE = ("[esecuzione]\npausa_stesso_sito_sec = 0\ntimeout_sec = 5\nusa_browser_per_js = false\n"
          "[notifiche]\nsilenzio_dalle = \"{dalle}\"\nsilenzio_alle = \"{alle}\"\n")


def lista(prefisso, annunci):
    schede = "".join(f'<article class="card"><h3><a href="/{prefisso}/immobile/{s}/">{t}</a></h3><p>{info}</p></article>'
                     for s, t, info in annunci)
    return (f"<html><body><nav><a href='/{prefisso}/'>Home</a> <a href='/{prefisso}/contatti/'>Contatti</a></nav>"
            f"<div class='lista'>{schede}</div></body></html>")


def giro(cartella, **k):
    cfg = carica_config(cartella)
    p = Percorsi(cartella)
    return esegui(cfg, p, carica_segreti(p, cfg), **k)


def test_giro_completo(cartella, server):
    tg = FintoTelegram(server)
    a = [("bilocale-centro-1001", "Bilocale in centro a Formigine", "Formigine · 60 mq · € 600/mese"),
         ("trilocale-grande-1002", "Trilocale grande", "Formigine · 80 mq · € 900/mese")]
    server.pagina("/a/affitti/", lista("a", a))
    server.pagina("/b/affitti/", lista("b", [("quadrilocale-sacca-2001", "Quadrilocale Sacca",
                                              "Modena · 110 mq · € 1.300/mese")]))
    aggiungi_agenzia(cartella, "Agenzia Alfa", "Formigine", server.url("/a/affitti/"))
    aggiungi_agenzia(cartella, "Agenzia Beta", "Modena", server.url("/b/affitti/", host="localhost"))

    # 1) prima lettura: salva tutto senza avvisare
    assert giro(cartella) == 0 and tg.messaggi == []

    # 2) tre annunci nuovi: uno va bene, uno costa troppo, uno ha i dati solo nella sua pagina
    #    (valori lontani dalle soglie di config.toml, cosi' ritoccare i filtri non rompe il test)
    a += [("bilocale-arredato-casinalbo-1003", "Bilocale arredato a Casinalbo", "70 mq · € 600/mese · 2 locali"),
          ("bilocale-caro-1004", "Bilocale signorile a Formigine", "75 mq · € 1.400/mese"),
          ("trilocale-giardino-1005", "Trilocale con giardino", "Scopri di più")]
    server.pagina("/a/affitti/", lista("a", a))
    server.pagina("/a/immobile/trilocale-giardino-1005/",
                  "<html><head><meta property='og:title' content='Trilocale in affitto a Spilamberto'></head>"
                  "<body><main><h1>Trilocale con giardino</h1><p>Canone mensile 620 €. Superficie 85 mq.</p></main>"
                  "</body></html>")
    assert giro(cartella) == 2
    assert len(tg.messaggi) == 2
    testi = "\n".join(tg.messaggi)
    assert "Bilocale arredato a Casinalbo" in testi and "Formigine (Casinalbo)" in testi
    assert "€ 620/mese · 85 mq" in testi and "Spilamberto" in testi
    assert "Bilocale signorile" not in testi
    assert testi.count("Non ancora su Immobiliare.it") == 2

    # 3) lo stesso bilocale compare sul sito di un'altra agenzia: non viene segnalato di nuovo
    server.pagina("/b/affitti/", lista("b", [
        ("quadrilocale-sacca-2001", "Quadrilocale Sacca", "Modena · 110 mq · € 1.300/mese"),
        ("bilocale-casinalbo-2002", "Appartamento bilocale Casinalbo", "Formigine · 70 mq · € 600/mese · 2 locali")]))
    assert giro(cartella) == 0 and len(tg.messaggi) == 2
    arc = Archivio(cartella / "stato" / "radar.db")
    assert arc.stato_di(server.url("/b/immobile/bilocale-casinalbo-2002", host="localhost")) == "doppione"
    arc.chiudi()

    # 4) nessuna novita': nessun messaggio
    assert giro(cartella) == 0 and len(tg.messaggi) == 2

    # 5) orario di silenzio: resta in coda, parte quando il silenzio finisce
    adesso = datetime.now()
    scrivi_locale(cartella, LOCALE.format(dalle=(adesso - timedelta(hours=1)).strftime("%H:%M"),
                                          alle=(adesso + timedelta(hours=1)).strftime("%H:%M")))
    a.append(("monolocale-grande-1006", "Bilocale luminoso a Formigine", "90 mq · € 520/mese"))
    server.pagina("/a/affitti/", lista("a", a))
    assert giro(cartella) == 1 and len(tg.messaggi) == 2
    scrivi_locale(cartella, LOCALE.format(dalle="", alle=""))
    assert giro(cartella) == 0 and len(tg.messaggi) == 3
    assert "Bilocale luminoso" in tg.messaggi[2]


def test_con_llm(cartella, server):
    tg = FintoTelegram(server)
    risposte = {
        "trilocale-1101": {"e_annuncio_singolo": True, "contratto": "affitto", "tipologia": "appartamento",
                           "prezzo_mensile_euro": 690, "spese_mensili_euro": None, "mq": 70, "locali": 3,
                           "comune": "Spilamberto", "frazione_o_zona": "San Vito", "indirizzo": None,
                           "arredato": "no", "disponibile_da": "dicembre", "note": "contratto 4+4",
                           "riassunto": "Trilocale con giardino privato e garage"},
        "villetta-1102": {"e_annuncio_singolo": True, "contratto": "vendita", "tipologia": "casa indipendente/villa",
                          "prezzo_mensile_euro": None, "spese_mensili_euro": None, "mq": 150, "locali": 5,
                          "comune": "Spilamberto", "frazione_o_zona": None, "indirizzo": None, "arredato": "non indicato",
                          "disponibile_da": None, "note": None, "riassunto": "Villetta"},
        "notizie-casa-1103": {"e_annuncio_singolo": False, "contratto": "non indicato", "tipologia": "altro",
                              "prezzo_mensile_euro": None, "spese_mensili_euro": None, "mq": None, "locali": None,
                              "comune": None, "frazione_o_zona": None, "indirizzo": None, "arredato": "non indicato",
                              "disponibile_da": None, "note": None, "riassunto": "Articolo"},
        "bilocale-vignola-1104": {"e_annuncio_singolo": True, "contratto": "affitto", "tipologia": "appartamento",
                                  "prezzo_mensile_euro": 600, "spese_mensili_euro": None, "mq": 60, "locali": 2,
                                  "comune": "Vignola", "frazione_o_zona": None, "indirizzo": None, "arredato": "sì",
                                  "disponibile_da": None, "note": None, "riassunto": "Bilocale"},
    }

    def risposta(corpo):
        utente = corpo["messages"][1]["content"]
        for chiave, r in risposte.items():
            if chiave in utente:
                return r
        raise AssertionError("domanda inattesa")

    llm = FintoOpenRouter(server, risposta)
    (cartella / ".env").write_text((cartella / ".env").read_text() + "OPENROUTER_API_KEY=sk-or-prova\n"
                                   "OPENROUTER_MODEL=fornitore/modello-prova\n", encoding="utf-8")
    server.pagina("/c/affitti/", lista("c", [("bilocale-vecchio-1100", "Bilocale", "€ 500/mese")]))
    aggiungi_agenzia(cartella, "Agenzia Gamma", "Spilamberto", server.url("/c/affitti/"))
    assert giro(cartella) == 0 and llm.corpi == []          # prima lettura: niente LLM
    server.pagina("/c/affitti/", lista("c", [
        ("bilocale-vecchio-1100", "Bilocale", "€ 500/mese"),
        ("trilocale-1101", "Proposta in esclusiva", "Scopri"),
        ("villetta-1102", "Villetta a schiera", "Scopri"),
        ("notizie-casa-1103", "Come scegliere casa: la guida", "Leggi"),
        ("bilocale-vignola-1104", "Bilocale arredato", "Scopri")]))
    for slug, testo in (("trilocale-1101", "Trilocale, canone 690 euro, 70 mq, giardino. San Vito di Spilamberto."),
                        ("villetta-1102", "Villetta in vendita, 150 mq."), ("notizie-casa-1103", "Consigli."),
                        ("bilocale-vignola-1104", "Bilocale a Vignola, 600 euro al mese, 60 mq.")):
        server.pagina(f"/c/immobile/{slug}/", f"<html><body><main><h1>Annuncio</h1><p>{testo}</p></main></body></html>")
    assert giro(cartella) == 1
    assert len(llm.corpi) == 4
    assert len(tg.messaggi) == 1
    m = tg.messaggi[0]
    assert "€ 690/mese · 70 mq · 3 locali · Spilamberto (San Vito)" in m
    assert "Trilocale con giardino privato e garage — contratto 4+4" in m and "disponibile dicembre" in m
    arc = Archivio(cartella / "stato" / "radar.db")
    motivi = dict(arc.db.execute("SELECT url, scartato FROM annunci WHERE stato='scartato'").fetchall())
    arc.chiudi()
    assert motivi == {server.url("/c/immobile/villetta-1102/"): "vendita",
                      server.url("/c/immobile/notizie-casa-1103/"): "non e' un annuncio",
                      server.url("/c/immobile/bilocale-vignola-1104/"): "fuori zona (Vignola)"}
    assert json.loads(json.dumps(llm.corpi[0]["response_format"]))["json_schema"]["name"] == "dettagli_annuncio"


def test_sito_rifatto_non_manda_raffiche(cartella, server):
    tg = FintoTelegram(server)
    vecchi = [(f"bilocale-numero-{i}", f"Bilocale {i}", f"{75 + i} mq · € {500 + i * 10}/mese") for i in range(6)]
    server.pagina("/d/affitti/", lista("d", vecchi))
    aggiungi_agenzia(cartella, "Agenzia Delta", "Modena", server.url("/d/affitti/"))
    giro(cartella)
    nuovi = [(f"immobile-bilocale-{i}-codice", f"Bilocale {i}", f"{75 + i} mq · € {500 + i * 10}/mese") for i in range(6)]
    server.pagina("/d/affitti/", lista("d", nuovi))
    assert giro(cartella) == 0 and tg.messaggi == []


def test_fonte_in_errore_non_manda_messaggi(cartella, server):
    tg = FintoTelegram(server)
    aggiungi_agenzia(cartella, "Agenzia Spenta", "Modena", "http://127.0.0.1:9/affitti/")
    assert giro(cartella) == 0 and tg.messaggi == []
    arc = Archivio(cartella / "stato" / "radar.db")
    assert arc.problemi(0) and arc.problemi(0)[0]["nome"] == "Agenzia Spenta"
    arc.chiudi()


def test_prima_lettura_vuota_non_manda_raffiche(cartella, server):
    tg = FintoTelegram(server)
    scrivi_locale(cartella, LOCALE.format(dalle="", alle=""))
    # il sito per ora non mostra annunci (JavaScript senza browser, captcha...): quando si leggono, sono vecchi
    server.pagina("/e/affitti/", lista("e", []))
    aggiungi_agenzia(cartella, "Agenzia Epsilon", "Modena", server.url("/e/affitti/"))
    assert giro(cartella) == 0
    vecchi = [(f"bilocale-epsilon-{i}", f"Bilocale {i}", f"{75 + i} mq · € {500 + i * 10}/mese") for i in range(6)]
    server.pagina("/e/affitti/", lista("e", vecchi))
    assert giro(cartella) == 0 and tg.messaggi == []
    # un'agenzia davvero senza affitti che ne pubblica uno: arriva
    server.pagina("/f/affitti/", lista("f", []))
    aggiungi_agenzia(cartella, "Agenzia Zeta", "Formigine", server.url("/f/affitti/"))
    assert giro(cartella) == 0
    server.pagina("/f/affitti/", lista("f", [("bilocale-zeta-1", "Bilocale a Formigine",
                                              "Formigine · 70 mq · € 600/mese")]))
    assert giro(cartella) == 1 and len(tg.messaggi) == 1 and "Bilocale a Formigine" in tg.messaggi[0]


def test_errore_in_una_fonte_non_ferma_il_controllo(cartella, server, monkeypatch):
    tg = FintoTelegram(server)
    scrivi_locale(cartella, LOCALE.format(dalle="", alle=""))
    server.pagina("/g/affitti/", lista("g", [("bilocale-gamma-1", "Bilocale", "70 mq · € 600/mese")]))
    aggiungi_agenzia(cartella, "Agenzia Rotta", "Modena", server.url("/g/affitti/"), pattern="immobile/(")
    a = [("bilocale-centro-1001", "Bilocale in centro a Formigine", "Formigine · 70 mq · € 600/mese")]
    server.pagina("/a/affitti/", lista("a", a))
    aggiungi_agenzia(cartella, "Agenzia Alfa", "Formigine", server.url("/a/affitti/", host="localhost"))
    assert giro(cartella) == 0
    a += [("trilocale-nuovo-1002", "Trilocale a Formigine", "Formigine · 85 mq · € 650/mese"),
          ("bilocale-guasto-1003", "Bilocale a Formigine", "Formigine · 90 mq · € 520/mese")]
    server.pagina("/a/affitti/", lista("a", a))
    vera = doppioni.cerca

    def cerca(annuncio, *altro):
        if "guasto" in annuncio.url:
            raise RuntimeError("errore finto")
        return vera(annuncio, *altro)

    monkeypatch.setattr(doppioni, "cerca", cerca)
    assert giro(cartella) == 1 and len(tg.messaggi) == 1 and "Trilocale a Formigine" in tg.messaggi[0]
    arc = Archivio(cartella / "stato" / "radar.db")
    assert not arc.visto(server.url("/a/immobile/bilocale-guasto-1003", host="localhost"))   # si riprova dopo
    note = {p["nome"]: p["nota"] for p in arc.problemi(0)}
    arc.chiudi()
    assert note["Agenzia Rotta"].startswith("errore del radar") and "Agenzia Alfa" in note


def test_verifica_rifiuta_un_pattern_sbagliato(cartella):
    assert verifica(cartella) == 0
    aggiungi_agenzia(cartella, "Agenzia Rotta", "Modena", "https://rotta.example/affitti/", pattern="immobile/(")
    assert verifica(cartella) == 1
    fonti = [Fonte("agenzia", "Buona", "", "https://a.it/", pattern=r"immobile/\d+"),
             Fonte("agenzia", "Rotta", "", "https://b.it/", pattern="immobile/(")]
    assert [x.split(" (")[0] for x in pattern_sbagliati(fonti)] == ["Rotta"]
