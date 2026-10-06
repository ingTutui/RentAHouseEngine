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
