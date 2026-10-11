import xml.dom.minidom
from pathlib import Path

from motore import avvio
from motore.avvio import _xml_windows
from motore.config import Percorsi, carica_config, carica_segreti, leggi_env, scrivi_env


def test_config_locale_sovrascrive(tmp_path):
    (tmp_path / "config.toml").write_text('[filtri]\nprezzo_max = 700\nmq_min = 55\n[notifiche]\nsilenzio_dalle = "22:00"\n',
                                          encoding="utf-8")
    (tmp_path / "config.local.toml").write_text('[notifiche]\nsilenzio_dalle = "23:00"\n', encoding="utf-8")
    cfg = carica_config(tmp_path)
    assert cfg["filtri"]["prezzo_max"] == 700 and cfg["filtri"]["mq_min"] == 55
    assert cfg["notifiche"]["silenzio_dalle"] == "23:00"
    assert cfg["notifiche"]["max_messaggi_singoli"] == 15          # valore predefinito


def test_config_della_prima_versione(tmp_path):
    (tmp_path / "config.toml").write_text('[telegram]\ntoken = "1:abc"\nchat_id = "99"\n'
                                          '[esecuzione]\nmax_messaggi_singoli = 9\n'
                                          '[filtri]\nzone_fuori_area_azione = "segnala"\n', encoding="utf-8")
    cfg = carica_config(tmp_path)
    s = carica_segreti(Percorsi(tmp_path), cfg)
    assert (s["TELEGRAM_TOKEN"], s["TELEGRAM_CHAT_ID"]) == ("1:abc", "99")
    assert cfg["notifiche"]["max_messaggi_singoli"] == 9 and cfg["filtri"]["fuori_zona"] == "segnala"


def test_file_env(tmp_path, monkeypatch):
    (tmp_path / ".env.example").write_text("# commento\nTELEGRAM_TOKEN=\nOPENROUTER_API_KEY=\n", encoding="utf-8")
    p = Percorsi(tmp_path)
    scrivi_env(p, {"TELEGRAM_TOKEN": "123:abc", "TELEGRAM_CHAT_ID": "42"})
    testo = (tmp_path / ".env").read_text(encoding="utf-8")
    assert "# commento" in testo and "TELEGRAM_TOKEN=123:abc" in testo and "TELEGRAM_CHAT_ID=42" in testo
    (tmp_path / ".env").write_text(testo + 'OPENROUTER_API_KEY="sk-or-v1-x"  # chiave\nexport LLM_CONFIG=llm.json\n',
                                   encoding="utf-8")
    env = leggi_env(tmp_path / ".env")
    assert env["OPENROUTER_API_KEY"] == "sk-or-v1-x" and env["LLM_CONFIG"] == "llm.json"
    monkeypatch.setenv("OPENROUTER_API_KEY", "dal-sistema")
    assert carica_segreti(p, carica_config(tmp_path))["OPENROUTER_API_KEY"] == "dal-sistema"


def test_vecchio_archivio_spostato(tmp_path):
    (tmp_path / "radar.db").write_bytes(b"")
    p = Percorsi(tmp_path)
    p.prepara()
    assert p.db.exists() and not (tmp_path / "radar.db").exists()


def test_attivita_di_windows():
    x = _xml_windows(r"C:\Users\Zoë\RentAHouseEngine\.venv\Scripts\pythonw.exe",
                     Path(r"C:\Users\Zoë\RentAHouseEngine\radar.py"), Path(r"C:\Users\Zoë\RentAHouseEngine"), 30)
    dom = xml.dom.minidom.parseString(x.replace('encoding="UTF-16"', ""))
    assert dom.getElementsByTagName("Interval")[0].firstChild.data == "PT30M"
    assert dom.getElementsByTagName("Arguments")[0].firstChild.data.endswith('radar.py" --servizio')
    assert dom.getElementsByTagName("DisallowStartIfOnBatteries")[0].firstChild.data == "false"


def test_l_avvio_automatico_si_installa_solo_con_uv(tmp_path, capsys):
    # tmp_path non ha un .venv: il comando non e' stato lanciato con uv, quindi non tocca ne' cron ne' Windows
    assert avvio.installa(tmp_path) == 1
    assert "uv run python radar.py --installa-avvio" in capsys.readouterr().out
