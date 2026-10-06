import copy
import json

from motore.analisi import Annuncio
from motore.archivio import Archivio
from motore.config import carica_config
from motore.dettagli import applica_llm, leggi_pagina, numero_nel_testo
from motore.llm import ClienteLLM, carica_modello, leggi_json, prova

from conftest import RADICE, FintoOpenRouter

CFG = carica_config(RADICE)


def segreti(server, modello="fornitore/modello-prova"):
    return {"OPENROUTER_API_KEY": "sk-or-prova", "OPENROUTER_BASE_URL": server.url("/api/v1"),
            "OPENROUTER_MODEL": modello}


def test_file_del_modello(tmp_path):
    (tmp_path / "llm.toml").write_text('modello = "a/b"\nmodelli_riserva = ["c/d"]\ntemperatura = 0.2\n', encoding="utf-8")
    c = carica_modello(tmp_path, CFG, {})
    assert (c["modello"], c["riserva"], c["temperatura"]) == ("a/b", ["c/d"], 0.2)
    (tmp_path / "modello.json").write_text(json.dumps({"model": "x/y", "temperature": 0.1, "max_tokens": 500,
                                                       "provider": {"sort": "price"}}), encoding="utf-8")
    c = carica_modello(tmp_path, CFG, {"LLM_CONFIG": "modello.json"})
    assert (c["modello"], c["max_token"], c["provider"]) == ("x/y", 500, {"sort": "price"})
    (tmp_path / "annidato.toml").write_text('[openrouter]\nmodels = ["p/q", "r/s"]\n', encoding="utf-8")
    c = carica_modello(tmp_path, CFG, {"LLM_CONFIG": "annidato.toml"})
    assert (c["modello"], c["riserva"]) == ("p/q", ["r/s"])
    assert carica_modello(tmp_path, CFG, {"OPENROUTER_MODEL": "z/z"})["modello"] == "z/z"


def test_senza_chiave_o_modello_resta_spento(tmp_path):
    arc = Archivio(tmp_path / "r.db")
    assert "OPENROUTER_API_KEY" in ClienteLLM(CFG, {}, arc, tmp_path).stato()
    c = ClienteLLM(CFG, {"OPENROUTER_API_KEY": "k"}, arc, tmp_path)
    assert not c.attivo and "modello" in c.motivo


def test_risposta_strutturata_e_cache(tmp_path, server):
    finto = FintoOpenRouter(server)
    arc = Archivio(tmp_path / "r.db")
    c = ClienteLLM(CFG, segreti(server), arc, tmp_path)
    assert prova(c).startswith("OK: fornitore/modello-prova")
    corpo = finto.corpi[0]
    assert corpo["model"] == "fornitore/modello-prova"
    assert corpo["response_format"]["type"] == "json_schema" and corpo["provider"]["require_parameters"] is True
    assert corpo["headers"]["Authorization"] == "Bearer sk-or-prova"
    schema = {"type": "object", "properties": {"ok": {"type": "boolean"}}, "required": ["ok"]}
    assert c.chiedi_json("t", "s", "stessa domanda", schema) == {"ok": True, "saluto": "ciao a tutti"}
    assert c.chiedi_json("t", "s", "stessa domanda", schema) == {"ok": True, "saluto": "ciao a tutti"}
    assert len(finto.corpi) == 2                      # la seconda volta risponde la cache
    assert arc.uso_llm(__import__("datetime").date.today().isoformat())[0] == 2


def test_modello_senza_json_schema(tmp_path, server):
    finto = FintoOpenRouter(server)
    finto.rifiuta_schema = True
    arc = Archivio(tmp_path / "r.db")
    c = ClienteLLM(CFG, segreti(server), arc, tmp_path)
    assert c.chiedi_json("t", "s", "u", {"type": "object"}) == {"ok": True, "saluto": "ciao a tutti"}
    assert [x.get("response_format", {}).get("type") for x in finto.corpi] == ["json_schema", "json_object"]
    assert ClienteLLM(CFG, segreti(server), arc, tmp_path).modo == "oggetto"   # se lo ricorda


def test_chiave_sbagliata_e_limiti(tmp_path, server):
    finto = FintoOpenRouter(server)
    arc = Archivio(tmp_path / "r.db")
    finto.errore = (401, json.dumps({"error": {"message": "No auth credentials found"}}))
    c = ClienteLLM(CFG, segreti(server), arc, tmp_path)
    assert c.chiedi_json("t", "s", "u", {}) is None and not c.attivo
    finto.errore = None
    cfg = copy.deepcopy(CFG)
    cfg["llm"]["max_chiamate_per_controllo"] = 1
    c = ClienteLLM(cfg, segreti(server), arc, tmp_path)
    assert c.chiedi_json("t", "s", "u1", {}) is not None
    assert c.chiedi_json("t", "s", "u2", {}) is None   # limite per controllo raggiunto


def test_json_tra_testo():
    assert leggi_json('```json\n{"a": 1}\n```') == {"a": 1}
    assert leggi_json('Ecco: {"a": 2} fine') == {"a": 2}
    assert leggi_json("niente") is None


def test_numeri_inventati_rifiutati():
    testo = "Bilocale in affitto. Canone mensile € 1.100. Superficie 75 mq. Spese condominiali 60 euro."
    assert numero_nel_testo(1100, testo) and numero_nel_testo(75, testo)
    assert not numero_nel_testo(950, testo) and not numero_nel_testo(110, testo)
    a = Annuncio(uid="u", url="https://ag.it/1", fonte="Ag", tipo_fonte="agenzia", comune_fonte="", titolo="T", testo="")
    applica_llm(a, {"e_annuncio_singolo": True, "contratto": "affitto", "tipologia": "appartamento",
                    "prezzo_mensile_euro": 950, "spese_mensili_euro": 60, "mq": 75, "locali": 2,
                    "comune": "Baggiovara", "frazione_o_zona": None, "indirizzo": "Via Giardini 3",
                    "arredato": "sì", "disponibile_da": "novembre", "note": None, "riassunto": "Bilocale luminoso"},
                testo, CFG)
    assert a.prezzo is None                  # 950 non c'e' nella pagina
    assert (a.mq, a.spese, a.locali, a.comune, a.origine_luogo, a.arredato) == (75, 60, 2, "Modena", "dettaglio", "sì")


def test_lettura_della_pagina():
    html = """<html><head><title>Bilocale in affitto - Agenzia Rossi Sassuolo</title>
    <meta property="og:title" content="Bilocale arredato a Fiorano Modenese">
    <script type="application/ld+json">{"@type":"RealEstateAgent","name":"Rossi",
      "address":{"addressLocality":"Sassuolo"}}</script>
    <script type="application/ld+json">{"@type":"Apartment","floorSize":{"value":"62"},"numberOfRooms":2,
      "address":{"addressLocality":"Fiorano Modenese","streetAddress":"Via Vittorio Veneto 5"},
      "offers":{"price":"640"}}</script></head>
    <body><nav>Home Affitti Vendite</nav><main><h1>Bilocale arredato</h1><p>Canone 640 euro al mese.</p></main>
    <footer>Agenzia Rossi, via Radici 1, Sassuolo</footer></body></html>"""
    testo, meta = leggi_pagina(html)
    assert "Canone 640" in testo and "via Radici" not in testo and "Home Affitti" not in testo
    assert meta["strutturati"] == {"prezzo": 640, "mq": 62, "locali": 2, "comune": "Fiorano Modenese",
                                   "indirizzo": "Via Vittorio Veneto 5"}
    assert meta["og_titolo"] == "Bilocale arredato a Fiorano Modenese"
