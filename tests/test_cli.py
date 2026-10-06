import subprocess
import sys

from motore.cli import main

from conftest import RADICE


def test_comandi_di_consultazione_su_un_clone_appena_fatto(cartella, capsys):
    assert not (cartella / "stato").exists()
    assert main(["--cartella", str(cartella), "--stato"]) == 0
    fuori = capsys.readouterr().out
    assert "In coda per Telegram: 0" in fuori and "ultimo messaggio: mai" in fuori
    assert main(["--cartella", str(cartella), "--coda"]) == 0
    assert "0 annunci in coda" in capsys.readouterr().out
    assert main(["--cartella", str(cartella), "--esporta"]) == 0
    assert (cartella / "stato" / "annunci_visti.csv").exists()


def test_la_verifica_del_repository_passa(capsys):
    # e' lo stesso controllo che l'aggiornamento automatico fa prima di tenere una versione nuova
    assert main(["--cartella", str(RADICE), "--verifica"]) == 0
    assert "Verifica OK" in capsys.readouterr().out


def test_con_un_python_vecchio_esce_con_un_messaggio_chiaro():
    # finge Python 3.12: il programma deve fermarsi subito, senza errori incomprensibili
    codice = ("import sys, runpy; sys.version_info = (3, 12, 4, 'final', 0); "
              "runpy.run_path('radar.py', run_name='__main__')")
    r = subprocess.run([sys.executable, "-c", codice], cwd=RADICE, capture_output=True, text=True, timeout=60)
    assert r.returncode == 1
    assert "richiede Python 3.14" in r.stderr and "Python 3.12" in r.stderr
    assert "uv run python radar.py" in r.stderr and "python.org" in r.stderr   # la strada piu' semplice e l'alternativa


def test_python_in_uso_e_quello_richiesto():
    assert sys.version_info >= (3, 14), "il progetto e' scritto per Python 3.14 o piu' recente"
