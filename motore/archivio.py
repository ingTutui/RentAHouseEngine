"""Archivio SQLite (stato/radar.db): annunci visti, coda dei messaggi, salute delle fonti, uso dell'LLM.

Stati di un annuncio:
  base        letto alla prima lettura di una fonte (mai segnalato, per non riempirti di messaggi)
  scartato    non interessa (filtri, vendita, fuori zona...)
  doppione    stesso immobile di un annuncio gia' visto su un'altra fonte (o ripubblicato)
  da_inviare  in coda per Telegram (per esempio trovato di notte)
  inviato     gia' segnalato
"""
from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timedelta
from pathlib import Path
from typing import Iterable, List, Optional, Tuple

from .analisi import Annuncio
from .comuni import trova_luogo

SCHEMA = 2
STATI_NOTI = ("base", "inviato", "da_inviare", "doppione")
FMT = "%Y-%m-%d %H:%M"


def adesso() -> str:
    return datetime.now().strftime(FMT)


def quando(testo: str) -> Optional[datetime]:
    try:
        return datetime.strptime((testo or "")[:16], FMT)
    except ValueError:
        return None


class Archivio:
    def __init__(self, path: Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)      # su un clone appena fatto stato/ non esiste ancora
        self.db = sqlite3.connect(str(self.path))
        self.db.executescript("""
        CREATE TABLE IF NOT EXISTS annunci (
            uid TEXT PRIMARY KEY, url TEXT, fonte TEXT, tipo_fonte TEXT, comune TEXT, titolo TEXT,
            prezzo INTEGER, mq INTEGER, locali INTEGER, primo_visto TEXT, ultimo_visto TEXT,
            notificato INTEGER DEFAULT 0, scartato TEXT DEFAULT '');
        CREATE TABLE IF NOT EXISTS fonti (
            url TEXT PRIMARY KEY, nome TEXT, inizializzata INTEGER DEFAULT 0, ultimo_ok TEXT,
            ultimo_errore TEXT DEFAULT '', errori_consecutivi INTEGER DEFAULT 0, ultimi_link INTEGER DEFAULT 0,
            avvisato INTEGER DEFAULT 0);
        CREATE TABLE IF NOT EXISTS impostazioni (chiave TEXT PRIMARY KEY, valore TEXT);
        CREATE TABLE IF NOT EXISTS llm_uso (giorno TEXT PRIMARY KEY, chiamate INTEGER DEFAULT 0,
            token_in INTEGER DEFAULT 0, token_out INTEGER DEFAULT 0, costo REAL DEFAULT 0);
        CREATE TABLE IF NOT EXISTS llm_cache (chiave TEXT PRIMARY KEY, risposta TEXT, creato TEXT);
        """)
        self._migra()

    # ------------------------------------------------------------------ schema
    def _colonne(self, tabella: str) -> set:
        return {r[1] for r in self.db.execute(f"PRAGMA table_info({tabella})")}

    def _migra(self) -> None:
        nuove = {
            "annunci": [("stato", "TEXT DEFAULT ''"), ("gruppo", "TEXT DEFAULT ''"), ("dati", "TEXT DEFAULT ''"),
                        ("inviato_il", "TEXT DEFAULT ''"), ("priorita", "INTEGER DEFAULT 1")],
            "fonti": [("problema_dal", "TEXT DEFAULT ''"), ("segnalato_il", "TEXT DEFAULT ''"),
                      ("nota", "TEXT DEFAULT ''")],
        }
        for tabella, colonne in nuove.items():
            esistenti = self._colonne(tabella)
            for nome, tipo in colonne:
                if nome not in esistenti:
                    self.db.execute(f"ALTER TABLE {tabella} ADD COLUMN {nome} {tipo}")
        if self.get("schema", "1") != str(SCHEMA):
            # archivio della prima versione: ricava lo stato dai vecchi campi
            self.db.execute("""UPDATE annunci SET stato = CASE
                    WHEN notificato = 1 THEN 'inviato'
                    WHEN scartato = 'prima lettura' OR scartato = '' THEN 'base'
                    ELSE 'scartato' END,
                inviato_il = CASE WHEN notificato = 1 THEN primo_visto ELSE '' END
                WHERE stato = '' OR stato IS NULL""")
            self.db.execute("UPDATE annunci SET gruppo = uid WHERE gruppo = '' OR gruppo IS NULL")
            # per le agenzie la vecchia colonna 'comune' era la sede: meglio il comune citato nel titolo
            for uid, titolo in self.db.execute(
                    "SELECT uid, titolo FROM annunci WHERE tipo_fonte='agenzia' AND (dati='' OR dati IS NULL)").fetchall():
                self.db.execute("UPDATE annunci SET comune=? WHERE uid=?", (trova_luogo(titolo or "")[0], uid))
            self.db.execute("UPDATE fonti SET problema_dal = ultimo_ok WHERE avvisato = 1 AND problema_dal = ''")
            self.set("schema", str(SCHEMA))
        self.db.executescript("""
        CREATE INDEX IF NOT EXISTS ix_annunci_prezzo ON annunci(prezzo);
        CREATE INDEX IF NOT EXISTS ix_annunci_stato ON annunci(stato);
        CREATE INDEX IF NOT EXISTS ix_annunci_gruppo ON annunci(gruppo);
        """)
        self.db.commit()

    # ------------------------------------------------------------------ impostazioni
    def get(self, chiave: str, default: str = "") -> str:
        r = self.db.execute("SELECT valore FROM impostazioni WHERE chiave=?", (chiave,)).fetchone()
        return r[0] if r else default

    def set(self, chiave: str, valore: str) -> None:
        self.db.execute("INSERT INTO impostazioni VALUES(?,?) ON CONFLICT(chiave) DO UPDATE SET valore=excluded.valore",
                        (chiave, valore))
        self.db.commit()

    def commit(self) -> None:
        self.db.commit()

    def chiudi(self) -> None:
        try:
            self.db.commit()
            self.db.close()
        except sqlite3.Error:
            pass

    # ------------------------------------------------------------------ fonti
    def fonte(self, url: str) -> dict:
        r = self.db.execute("""SELECT inizializzata, errori_consecutivi, ultimi_link, problema_dal, segnalato_il, nota
                               FROM fonti WHERE url=?""", (url,)).fetchone()
        if not r:
            return {"inizializzata": False, "errori": 0, "ultimi_link": 0, "problema_dal": "", "segnalato_il": "",
                    "nota": ""}
        return {"inizializzata": bool(r[0]), "errori": r[1], "ultimi_link": r[2], "problema_dal": r[3] or "",
                "segnalato_il": r[4] or "", "nota": r[5] or ""}

    def esito_fonte(self, url: str, nome: str, errore: str, n_link: int) -> None:
        prima = self.fonte(url)
        ora = adesso()
        if errore:
            self.db.execute("""INSERT INTO fonti(url,nome,ultimo_errore,errori_consecutivi,problema_dal,nota)
                VALUES(?,?,?,1,?,?) ON CONFLICT(url) DO UPDATE SET nome=excluded.nome,
                ultimo_errore=excluded.ultimo_errore, errori_consecutivi=errori_consecutivi+1,
                problema_dal=CASE WHEN problema_dal='' OR problema_dal IS NULL THEN excluded.problema_dal
                                  ELSE problema_dal END, nota=excluded.nota""",
                            (url, nome, f"{ora} {errore}", ora, errore))
        elif n_link == 0 and (prima["ultimi_link"] > 0 or prima["problema_dal"]):
            nota = prima["nota"] if prima["problema_dal"] and "nessun annuncio" in prima["nota"] else \
                f"nessun annuncio trovato (prima ne aveva {prima['ultimi_link']}): il sito potrebbe essere cambiato"
            self.db.execute("""UPDATE fonti SET nome=?, inizializzata=1, ultimo_ok=?, errori_consecutivi=0,
                ultimi_link=0, problema_dal=CASE WHEN problema_dal='' OR problema_dal IS NULL THEN ? ELSE problema_dal END,
                nota=? WHERE url=?""", (nome, ora, ora, nota, url))
        else:
            self.db.execute("""INSERT INTO fonti(url,nome,inizializzata,ultimo_ok,errori_consecutivi,ultimi_link)
                VALUES(?,?,1,?,0,?) ON CONFLICT(url) DO UPDATE SET nome=excluded.nome, inizializzata=1,
                ultimo_ok=excluded.ultimo_ok, errori_consecutivi=0, ultimi_link=excluded.ultimi_link,
                problema_dal='', segnalato_il='', nota='', avvisato=0""", (url, nome, ora, n_link))
        self.db.commit()

    def problemi(self, ore: float) -> List[dict]:
        """Fonti che non funzionano da almeno 'ore' ore."""
        limite = datetime.now() - timedelta(hours=ore)
        out = []
        for url, nome, problema_dal, segnalato_il, nota in self.db.execute(
                "SELECT url, nome, problema_dal, segnalato_il, nota FROM fonti WHERE problema_dal != ''"):
            d = quando(problema_dal)
            if d and d <= limite:
                out.append({"url": url, "nome": nome, "dal": problema_dal, "segnalato_il": segnalato_il or "",
                            "nota": nota or ""})
        return sorted(out, key=lambda x: x["dal"])

    def segna_segnalati(self, urls: Iterable[str]) -> None:
        ora = adesso()
        self.db.executemany("UPDATE fonti SET segnalato_il=? WHERE url=?", [(ora, u) for u in urls])
        self.db.commit()

    # ------------------------------------------------------------------ annunci
    def visto(self, uid: str) -> bool:
        return self.db.execute("SELECT 1 FROM annunci WHERE uid=?", (uid,)).fetchone() is not None

    def tocca(self, uids: Iterable[str]) -> None:
        ora = adesso()
        self.db.executemany("UPDATE annunci SET ultimo_visto=? WHERE uid=?", [(ora, u) for u in uids])

    def salva(self, a: Annuncio, stato: str, motivo: str = "", gruppo: str = "", priorita: int = 1) -> None:
        ora = adesso()
        a.trovato_il = a.trovato_il or ora
        self.db.execute("""INSERT INTO annunci(uid,url,fonte,tipo_fonte,comune,titolo,prezzo,mq,locali,
            primo_visto,ultimo_visto,notificato,scartato,stato,gruppo,dati,priorita)
            VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            ON CONFLICT(uid) DO UPDATE SET ultimo_visto=excluded.ultimo_visto""",
                        (a.uid, a.url, a.fonte, a.tipo_fonte, a.comune or "", (a.titolo or "")[:200], a.prezzo,
                         a.mq, a.locali, ora, ora, 1 if stato == "inviato" else 0, motivo, stato, gruppo or a.uid,
                         json.dumps(a.come_dict(), ensure_ascii=False), priorita))

    def annuncio(self, uid: str) -> Optional[Annuncio]:
        r = self.db.execute("SELECT dati, uid, url, fonte, tipo_fonte, comune, titolo, prezzo, mq, locali "
                            "FROM annunci WHERE uid=?", (uid,)).fetchone()
        return self._da_riga(r) if r else None

    @staticmethod
    def _da_riga(r) -> Annuncio:
        dati, uid, url, fonte, tipo_fonte, comune, titolo, prezzo, mq, locali = r
        if dati:
            try:
                return Annuncio.da_dict(json.loads(dati))
            except (ValueError, TypeError):
                pass
        return Annuncio(uid=uid, url=url or "", fonte=fonte or "", tipo_fonte=tipo_fonte or "", comune_fonte="",
                        titolo=titolo or "", testo="", prezzo=prezzo, mq=mq, locali=locali, comune=comune or None)

    def candidati(self, prezzo: Optional[int], mq: Optional[int], tol_pct: float, tol_mq: int, giorni: int,
                  escludi: str) -> List[Tuple[str, str, Annuncio]]:
        """Annunci gia' noti che potrebbero essere lo stesso immobile: (uid, gruppo, annuncio)."""
        dal = (datetime.now() - timedelta(days=giorni)).strftime(FMT)
        segnaposto = ",".join("?" * len(STATI_NOTI))
        base = (f"SELECT uid, gruppo, dati, url, fonte, tipo_fonte, comune, titolo, prezzo, mq, locali FROM annunci "
                f"WHERE stato IN ({segnaposto}) AND primo_visto >= ? AND uid != ?")
        par: list = [*STATI_NOTI, dal, escludi]
        condizioni = []
        if prezzo is not None:
            delta = max(10, round(prezzo * tol_pct / 100))
            condizioni.append("(prezzo BETWEEN ? AND ?)")
            par += [prezzo - delta, prezzo + delta]
        if mq is not None:  # anche gli annunci senza prezzo con metratura simile
            condizioni.append("(prezzo IS NULL AND mq BETWEEN ? AND ?)" if prezzo is not None else "(mq BETWEEN ? AND ?)")
            par += [mq - tol_mq, mq + tol_mq]
        if not condizioni:
            return []
        q = base + " AND (" + " OR ".join(condizioni) + ")"
        out = []
        for r in self.db.execute(q + " ORDER BY primo_visto DESC LIMIT 60", par).fetchall():
            out.append((r[0], r[1] or r[0], self._da_riga((r[2],) + tuple(r[0:1]) + tuple(r[3:]))))
        return out

    def in_coda(self) -> List[Annuncio]:
        righe = self.db.execute("""SELECT dati, uid, url, fonte, tipo_fonte, comune, titolo, prezzo, mq, locali
            FROM annunci WHERE stato='da_inviare' ORDER BY priorita, primo_visto""").fetchall()
        return [self._da_riga(r) for r in righe]

    def segna_inviati(self, uids: Iterable[str]) -> None:
        ora = adesso()
        self.db.executemany("UPDATE annunci SET stato='inviato', notificato=1, inviato_il=? WHERE uid=?",
                            [(ora, u) for u in uids])
        self.db.commit()

    def altre_fonti(self, gruppo: str, escludi: str) -> List[Tuple[str, str, str]]:
        """(fonte, tipo_fonte, url) degli altri annunci dello stesso immobile."""
        return self.db.execute("SELECT fonte, tipo_fonte, url FROM annunci WHERE gruppo=? AND uid != ? "
                               "ORDER BY primo_visto", (gruppo, escludi)).fetchall()

    def gruppo_di(self, uid: str) -> str:
        r = self.db.execute("SELECT gruppo FROM annunci WHERE uid=?", (uid,)).fetchone()
        return (r[0] or uid) if r else uid

    def stato_di(self, uid: str) -> str:
        r = self.db.execute("SELECT stato FROM annunci WHERE uid=?", (uid,)).fetchone()
        return r[0] if r else ""

    def conta(self) -> dict:
        return dict(self.db.execute("SELECT stato, COUNT(*) FROM annunci GROUP BY stato").fetchall())

    # ------------------------------------------------------------------ LLM
    def uso_llm(self, giorno: str) -> Tuple[int, float]:
        r = self.db.execute("SELECT chiamate, costo FROM llm_uso WHERE giorno=?", (giorno,)).fetchone()
        return (r[0], r[1] or 0.0) if r else (0, 0.0)

    def registra_llm(self, giorno: str, token_in: int, token_out: int, costo: float) -> None:
        self.db.execute("""INSERT INTO llm_uso(giorno, chiamate, token_in, token_out, costo) VALUES(?,1,?,?,?)
            ON CONFLICT(giorno) DO UPDATE SET chiamate=chiamate+1, token_in=token_in+excluded.token_in,
            token_out=token_out+excluded.token_out, costo=costo+excluded.costo""",
                        (giorno, token_in, token_out, costo))
        self.db.commit()

    def cache_llm(self, chiave: str) -> Optional[str]:
        r = self.db.execute("SELECT risposta FROM llm_cache WHERE chiave=?", (chiave,)).fetchone()
        return r[0] if r else None

    def salva_cache_llm(self, chiave: str, risposta: str) -> None:
        self.db.execute("INSERT OR REPLACE INTO llm_cache VALUES(?,?,?)", (chiave, risposta, adesso()))
        self.db.commit()

    def pulisci(self, giorni_cache: int = 60) -> None:
        dal = (datetime.now() - timedelta(days=giorni_cache)).strftime(FMT)
        self.db.execute("DELETE FROM llm_cache WHERE creato < ?", (dal,))
        self.db.commit()
