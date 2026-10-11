from motore.analisi import analizza, importi, leggi_numeri, RE_MQ, scarta
from motore.config import carica_config
from motore.estrazione import canonico, estrai, pagina_immobiliare, sembra_annuncio
from motore.fonti import Fonte

from conftest import RADICE

CFG = carica_config(RADICE)
PORTALE = Fonte("portale", "Immobiliare.it", "Modena", "https://www.immobiliare.it/x")
AGENZIA = Fonte("agenzia", "X", "Modena", "https://ag.it/affitti/")


def test_importi():
    assert importi("€ 1.100/mese") == [1100]
    assert importi("canone 850 € al mese") == [850]
    assert importi("1.250,00 €") == [1250]
    assert importi("Euro 700 mensili") == [700]
    assert importi("€ 189.000") == [189000]
    assert importi("€ 1 200") == [1200]


def test_mq_e_locali():
    assert RE_MQ.search("appartamento di 75 mq").group(1) == "75"
    assert RE_MQ.search("85m²").group(1) == "85"
    n = leggi_numeri("Trilocale 3 locali 90 mq € 650/mese", "https://x.it/a", CFG)
    assert (n["prezzo"], n["mq"], n["locali"], n["vendita"]) == (650, 90, 3, False)
    assert leggi_numeri("Appartamento 90 mq € 189.000", "https://x.it/a", CFG)["vendita"] is True


def test_canonico_e_pagine():
    assert canonico("https://Sito.it/immobile/x-123/?utm_source=a&id=5#foto") == "https://sito.it/immobile/x-123?id=5"
    base = "https://www.immobiliare.it/affitto-case/modena/?criterio=dataModifica&ordine=desc"
    assert pagina_immobiliare(base, 2) == base + "&pag=2"
    assert pagina_immobiliare("https://www.immobiliare.it/affitto-case/modena/", 1) == \
        "https://www.immobiliare.it/affitto-case/modena/"


def test_portali():
    html = '''<ul><li><a href="https://www.immobiliare.it/annunci/131929004/" title="Bilocale via Emilia Centro, Modena">
    Bilocale via Emilia Centro, Modena</a><div>€ 650/mese · 2 locali · 60 m²</div></li>
    <li><a href="/annunci/133148816/">Trilocale corso Canalgrande</a><div>€ 900/mese · 80 m²</div></li>
    <li><a href="https://www.immobiliare.it/agenzie-immobiliari/7447/affittook-modena/">AFFITTOok</a></li></ul>'''
    t = estrai(html, "https://www.immobiliare.it/affitto-case/modena/?criterio=dataModifica&ordine=desc")
    assert sorted(t) == ["immobiliare.it:131929004", "immobiliare.it:133148816"]
    url, testo, titolo = t["immobiliare.it:131929004"]
    a = analizza("immobiliare.it:131929004", url, testo, titolo, PORTALE, CFG)
    assert (a.prezzo, a.mq, a.locali, a.fonte, a.comune, a.origine_luogo) == (650, 60, 2, "Immobiliare.it", "Modena",
                                                                              "portale")
    casa = '<div><a href="/immobili/54910429/">Bilocale</a> <span>768 €</span></div><a href="/agenzie/x-1/">ag</a>'
    assert list(estrai(casa, "https://www.casa.it/affitto/residenziale/modena/")) == ["casa.it:54910429"]
    sub = ('<a href="https://www.subito.it/appartamenti/modena-tre-camere-da-letto-modena-657895485.htm">x</a>'
           '<a href="https://www.subito.it/annunci-emilia-romagna/affitto/appartamenti/modena/modena/?o=2">2</a>')
    assert list(estrai(sub, "https://www.subito.it/annunci-emilia-romagna/affitto/appartamenti/modena/modena/")) == \
        ["subito.it:657895485"]
    wiki = '<div><a href="https://www.wikicasa.it/annuncio/30732894">Bilocale via Canalino</a> €900/mese</div>'
    assert list(estrai(wiki, "https://www.wikicasa.it/affitto-case/modena/")) == ["wikicasa.it:30732894"]


def test_link_delle_agenzie():
    no = ["https://ag.it/contatti/", "https://ag.it/privacy-policy/", "https://ag.it/blog/come-affittare-casa-2024/",
          "https://ag.it/wp-content/uploads/2024/05/foto-1234.jpg", "https://altro.it/immobile/x-1234/",
          "https://ag.it/immobili/", "https://ag.it/affitti/?page=2"]
    si = ["https://ag.it/immobile/bilocale-centro-1234/", "https://www.ag.it/scheda.php?id=4521",
          "https://ag.it/affitto-appartamento-modena-mo--p713", "https://ag.it/property/trilocale-arredato-sacca/"]
    for u in no:
        assert not sembra_annuncio(u, "https://ag.it/affitti/"), u
    for u in si:
        assert sembra_annuncio(u, "https://ag.it/affitti/"), u


def test_tipi_esclusi():
    def esito(url, testo):
        return scarta(analizza("u", url, testo, testo, AGENZIA, CFG), CFG)
    assert esito("https://ag.it/immobile/trilocale-1", "Trilocale con garage € 650/mese 70 mq") == ""
    assert esito("https://ag.it/immobile/garage-1", "Garage in affitto € 90/mese") == "tipo garage"
    sub = Fonte("portale", "Subito.it", "Modena", "https://www.subito.it/x")
    a = analizza("u", "https://www.subito.it/uffici-locali-commerciali/ufficio-modena-123456789.htm", "Ufficio € 500",
                 "Ufficio", sub, CFG)
    assert scarta(a, CFG) != ""
