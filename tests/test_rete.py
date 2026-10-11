from motore import rete
from motore.rete import Scaricatore

from conftest import config_di_prova


def scaricatore(pausa: float = 0) -> Scaricatore:
    cfg = config_di_prova()
    cfg["esecuzione"].update(pausa_stesso_sito_sec=pausa, timeout_sec=5, usa_browser_per_js=False)
    return Scaricatore(cfg)


def test_robots_txt_vieta_una_pagina_e_non_la_chiede(server):
    server.pagina("/robots.txt", "User-agent: *\nDisallow: /privato/\n", tipo="text/plain")
    server.pagina("/privato/elenco", "<html>riservato</html>")
    server.pagina("/affitti/", "<html>ok</html>")
    s = scaricatore()
    assert s.scarica(server.url("/privato/elenco")) == (None, "vietato da robots.txt")
    assert s.scarica(server.url("/affitti/")) == ("<html>ok</html>", "")
    assert not any(percorso.startswith("/privato") for _, percorso in server.richieste)


def test_sito_senza_robots_txt_si_puo_leggere(server):
    server.pagina("/affitti/", "<html>ok</html>")          # /robots.txt risponde 404: nessuna regola
    assert scaricatore().scarica(server.url("/affitti/")) == ("<html>ok</html>", "")


def test_robots_txt_in_errore_non_si_legge_il_sito(server):
    server.pagina("/robots.txt", "errore", stato=500, tipo="text/plain")
    server.pagina("/affitti/", "<html>ok</html>")
    html, motivo = scaricatore().scarica(server.url("/affitti/"))
    assert html is None and "robots.txt non disponibile" in motivo
    assert not any(percorso == "/affitti/" for _, percorso in server.richieste)


def test_sito_che_rifiuta_le_richieste_automatiche(server):
    server.pagina("/affitti/", "negato", stato=403)
    html, motivo = scaricatore().scarica(server.url("/affitti/"))
    assert html is None and motivo.startswith("HTTP 403")


def test_pausa_tra_due_richieste_allo_stesso_sito_non_tra_siti_diversi(server, monkeypatch):
    attese = []
    monkeypatch.setattr(rete.time, "sleep", attese.append)       # non aspetta davvero: registra quanto aspetterebbe
    server.pagina("/a/", "<html>a</html>")
    server.pagina("/b/", "<html>b</html>")
    s = scaricatore(pausa=2)
    assert s.scarica(server.url("/a/"))[0] and attese == []      # il primo accesso al sito parte subito
    assert s.scarica(server.url("/b/"))[0]                       # stesso sito: aspetta (quasi) tutta la pausa
    assert len(attese) == 1 and 1.5 <= attese[0] <= 2
    s._attendi("altro-sito.example")                             # un sito mai visitato: nessuna attesa
    assert len(attese) == 1
