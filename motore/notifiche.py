"""Messaggi Telegram: solo quando ci sono annunci nuovi, mai nell'orario di silenzio.

Gli annunci trovati di notte restano in coda nell'archivio e partono alla fine del silenzio.
Se Telegram non risponde restano in coda e si riprova al controllo successivo.
Le fonti che non funzionano vengono citate in fondo a un messaggio con novita', al massimo una volta
a settimana; da sole solo se per giorni non c'e' stato nessun messaggio da accodare.
"""

import json
import logging
import re
import time
from datetime import datetime, timedelta

import requests

from .analisi import Annuncio
from .archivio import quando

log = logging.getLogger("radar")
LIMITE = 4000


def esc(t) -> str:
    return str(t or "").replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def esc_attr(t: str) -> str:
    return esc(t).replace('"', "&quot;")


def semplice(testo: str) -> str:
    """Il messaggio senza HTML: per lo schermo e per quando Telegram rifiuta la formattazione."""
    return re.sub(r"<[^>]+>", "", testo).replace("&lt;", "<").replace("&gt;", ">").replace("&quot;", '"') \
        .replace("&amp;", "&")


class Telegram:
    def __init__(self, segreti: dict[str, str], finto: bool = False):
        self.token = (segreti.get("TELEGRAM_TOKEN") or "").strip()
        self.chat = (segreti.get("TELEGRAM_CHAT_ID") or "").strip()
        self.base = (segreti.get("TELEGRAM_API_URL") or "https://api.telegram.org").rstrip("/")
        self.finto = finto or not (self.token and self.chat)
        self.inviati: list[str] = []
        self.rifiutato = False          # l'ultimo invio e' fallito per il testo stesso, non per chat, rete o token
        if self.finto and not finto:
            log.info("Telegram non configurato su questo PC: i messaggi vengono solo mostrati a schermo")

    def invia(self, testo: str, anteprima: bool = True, url_anteprima: str = "", html: bool = True) -> bool:
        """anteprima: foto e titolo del link sotto al messaggio (di url_anteprima, se indicato).
        html=False: manda il testo senza formattazione."""
        testo = testo[:LIMITE] if html else semplice(testo)[:LIMITE]
        opzioni = {"is_disabled": not anteprima}
        if anteprima and url_anteprima:
            opzioni["url"] = url_anteprima
        self.rifiutato = False
        if self.finto:
            self.inviati.append(testo)
            print("\n--- messaggio ---\n" + semplice(testo) + "\n-----------------")
            return True
        dati = {"chat_id": self.chat, "text": testo, "link_preview_options": json.dumps(opzioni)}
        if html:
            dati["parse_mode"] = "HTML"
        for _ in range(3):
            try:
                r = requests.post(f"{self.base}/bot{self.token}/sendMessage", timeout=20, data=dati)
                if r.status_code == 429:
                    try:
                        attesa = int(r.json().get("parameters", {}).get("retry_after", 5))
                    except ValueError:
                        attesa = 5
                    time.sleep(attesa + 1)
                    continue
                if r.ok:
                    self.inviati.append(testo)
                    time.sleep(1.1)
                    return True
                log.error("Telegram ha risposto %s: %s", r.status_code, r.text[:200])
                # 400 "can't parse entities", "message is too long"...: colpa del testo; "chat not found" no
                self.rifiutato = r.status_code == 400 and "chat" not in r.text.lower()
                return False
            except requests.RequestException as ex:
                log.error("Telegram non raggiungibile (%s)", type(ex).__name__)
                time.sleep(3)
        return False


# ---------------------------------------------------------------------- orario di silenzio
def _ora(testo: str) -> tuple[int, int] | None:
    m = re.fullmatch(r"\s*(\d{1,2})[:.](\d{2})\s*", testo or "")
    if not m or int(m.group(1)) > 23 or int(m.group(2)) > 59:
        return None
    return int(m.group(1)), int(m.group(2))


def in_silenzio(adesso: datetime, dalle: str, alle: str) -> bool:
    """True se 'adesso' cade nell'orario di silenzio (anche a cavallo della mezzanotte, es. 22:00-08:00)."""
    a, b = _ora(dalle), _ora(alle)
    if not a or not b or a == b:
        return False
    t = (adesso.hour, adesso.minute)
    if a < b:
        return a <= t < b
    return t >= a or t < b


# ---------------------------------------------------------------------- testo dei messaggi
def euro(n: int) -> str:
    return f"{n:,}".replace(",", ".")


def riga_dati(a: Annuncio) -> str:
    pezzi = []
    if a.prezzo:
        pezzi.append(f"€ {euro(a.prezzo)}/mese" + (f" + {euro(a.spese)} spese" if a.spese else ""))
    else:
        pezzi.append("prezzo non indicato")
    pezzi.append(f"{a.mq} mq" if a.mq else "mq non indicati")
    if a.locali:
        pezzi.append(f"{a.locali} locali")
    if a.stanza:
        pezzi.append("stanza")
    return " · ".join(pezzi)


def luogo(a: Annuncio) -> str:
    if not a.comune:
        return ""
    if a.frazione and a.frazione.lower() != a.comune.lower():
        return f"{a.comune} ({a.frazione})"
    return a.comune


def solo_sul_sito(a: Annuncio, altre: list[tuple[str, str, str]]) -> bool:
    return a.tipo_fonte == "agenzia" and a.prezzo is not None and \
        not any(f == "Immobiliare.it" for f, _, _ in altre)


def _altre(altre: list[tuple[str, str, str]]) -> str:
    nomi = []
    for fonte, _, url in altre:
        if fonte not in [n for n, _ in nomi]:
            nomi.append((fonte, url))
    return ", ".join(f'<a href="{esc_attr(u)}">{esc(f)}</a>' for f, u in nomi[:4])


def messaggio(a: Annuncio, altre: list[tuple[str, str, str]], adesso: datetime) -> str:
    sorgente = esc(a.fonte) + (" (sito dell'agenzia)" if a.tipo_fonte == "agenzia" else "")
    righe = [f"<b>Nuovo affitto</b> · {sorgente}", f"<b>{esc(a.titolo[:150])}</b>"]
    dati = riga_dati(a)
    posto = luogo(a)
    righe.append(dati + (f" · {esc(posto)}" if posto else ""))
    extra = [x for x in (("arredato" if a.arredato == "sì" else ("parzialmente arredato" if a.arredato ==
                                                                   "parzialmente" else "")),
                         (f"disponibile {a.disponibile}" if a.disponibile else ""), a.indirizzo) if x]
    if extra:
        righe.append(esc(" · ".join(extra)))
    if a.riassunto or a.note:
        righe.append("<i>" + esc(" — ".join(x for x in (a.riassunto, a.note) if x)) + "</i>")
    if solo_sul_sito(a, altre):
        righe.append("<b>Non ancora su Immobiliare.it</b>")
    if altre:
        righe.append("Anche su: " + _altre(altre))
    if a.zona_avviso or a.area == "fuori":
        righe.append(f"Attenzione: forse fuori zona ({esc(a.zona_avviso or a.comune)})")
    if a.nota_doppione:
        righe.append(esc(a.nota_doppione))
    trovato = quando(a.trovato_il)
    if trovato and adesso - trovato > timedelta(minutes=45):
        giorno = "" if trovato.date() == adesso.date() else trovato.strftime("il %d/%m ")
        righe.append(f"Trovato {giorno}alle {trovato.strftime('%H:%M')}")
    righe.append(a.url)
    return "\n".join(righe)


def riepilogo(lista: list[tuple[Annuncio, list[tuple[str, str, str]]]], notte: bool) -> list[tuple[str, list[str]]]:
    """Un unico elenco (diviso in piu' messaggi se e' lungo): [(testo, uid inclusi)]."""
    titolo = f"<b>{len(lista)} nuovi affitti</b>" + (" trovati stanotte" if notte else "")
    blocchi: list[tuple[str, list[str]]] = []
    corrente, uids = [titolo], []
    for a, altre in lista:
        posto = luogo(a)
        extra = " · <b>solo sul sito dell'agenzia</b>" if solo_sul_sito(a, altre) else ""
        riga = (f"• <b>{esc(riga_dati(a))}</b>{(' · ' + esc(posto)) if posto else ''}\n"
                f"  {esc(a.titolo[:80])} — {esc(a.fonte)}{extra} · <a href=\"{esc_attr(a.url)}\">apri</a>")
        if sum(len(x) + 1 for x in corrente) + len(riga) > 3500:
            blocchi.append(("\n".join(corrente), uids))
            corrente, uids = [], []
        corrente.append(riga)
        uids.append(a.uid)
    blocchi.append(("\n".join(corrente), uids))
    return blocchi


# ---------------------------------------------------------------------- fonti che non funzionano
def guasti_da_dire(archivio, cfg: dict, adesso: datetime) -> list[dict]:
    n = cfg["notifiche"]
    if not n.get("avvisi_guasti", True):
        return []
    ogni = timedelta(days=float(n.get("avvisi_guasti_ogni_giorni", 7)))
    ultimo = quando(archivio.get("ultimo_avviso_guasti"))
    if ultimo and adesso - ultimo < ogni:
        return []
    out = [p for p in archivio.problemi(float(n.get("guasto_dopo_ore", 24)))
           if not quando(p["segnalato_il"]) or adesso - quando(p["segnalato_il"]) >= ogni]
    fallito = archivio.get("aggiornamento_fallito")
    if fallito:
        out.append({"url": "", "nome": "aggiornamento automatico", "nota": fallito, "dal": "", "segnalato_il": ""})
    return out


def testo_guasti(problemi: list[dict], da_solo: bool) -> str:
    fonti = [p for p in problemi if p["url"]]
    pezzi = []
    if fonti:
        nomi = ", ".join(esc(p["nome"][:40]) for p in fonti[:5]) + (f" e altre {len(fonti) - 5}" if len(fonti) > 5 else "")
        pezzi.append(f"{len(fonti)} fonti non funzionano da almeno un giorno: {nomi}")
    if any(not p["url"] for p in problemi):
        pezzi.append("l'ultimo aggiornamento dal repository non e' andato a buon fine (resta la versione precedente)")
    corpo = "; ".join(pezzi) + ". Dettagli con: python radar.py --stato"
    return f"<b>Radar Affitti</b>: {corpo[0].upper() + corpo[1:]}" if da_solo else f"\n\n<i>Da controllare: {corpo}</i>"


def _chiudi_guasti(archivio, problemi: list[dict], adesso: datetime) -> None:
    archivio.segna_segnalati([p["url"] for p in problemi if p["url"]])
    archivio.set("ultimo_avviso_guasti", adesso.strftime("%Y-%m-%d %H:%M"))
    if any(not p["url"] for p in problemi):
        archivio.set("aggiornamento_fallito", "")


# ---------------------------------------------------------------------- consegna
def consegna(archivio, tg: Telegram, cfg: dict, adesso: datetime | None = None, forza: bool = False) -> int:
    """Invia gli annunci in coda. Restituisce quanti ne sono stati segnalati."""
    n = cfg["notifiche"]
    adesso = adesso or datetime.now()
    coda = archivio.in_coda()
    if not forza and in_silenzio(adesso, n.get("silenzio_dalle", ""), n.get("silenzio_alle", "")):
        if coda:
            log.info("Orario di silenzio: %d annunci in coda, partono alle %s", len(coda), n.get("silenzio_alle"))
        return 0
    problemi = guasti_da_dire(archivio, cfg, adesso)
    if not coda:
        if problemi:
            riferimento = quando(archivio.get("ultimo_invio")) or quando(archivio.get("primo_avvio"))
            if riferimento and adesso - riferimento >= timedelta(days=float(n.get("guasti_da_soli_dopo_giorni", 3))):
                if tg.invia(testo_guasti(problemi, da_solo=True), anteprima=False):
                    _chiudi_guasti(archivio, problemi, adesso)
        return 0
    lista = [(a, archivio.altre_fonti(archivio.gruppo_di(a.uid), a.uid)) for a in coda]
    lista.sort(key=lambda x: (0 if solo_sul_sito(*x) else 1))  # prima il vantaggio: solo sul sito dell'agenzia
    notte = bool(n.get("silenzio_dalle")) and all(
        (quando(a.trovato_il) and in_silenzio(quando(a.trovato_il), n.get("silenzio_dalle", ""),
                                              n.get("silenzio_alle", ""))) for a, _ in lista)
    if len(lista) > int(n.get("max_messaggi_singoli", 15)):
        messaggi = [(t, u, False, "") for t, u in riepilogo(lista, notte)]
    else:
        messaggi = [(messaggio(a, altre, adesso), [a.uid], bool(n.get("anteprima_link", True)), a.url)
                    for a, altre in lista]
    coda_guasti = testo_guasti(problemi, da_solo=False) if problemi else ""
    inviati = 0
    for i, (testo, uids, anteprima, url) in enumerate(messaggi):
        ultimo = i == len(messaggi) - 1
        if ultimo and coda_guasti and len(testo) + len(coda_guasti) <= LIMITE:
            testo += coda_guasti
        else:
            ultimo = False
        ok = tg.invia(testo, anteprima, url)
        if not ok and tg.rifiutato:
            # Telegram rifiuta proprio questo testo (di solito la formattazione): riprova senza HTML
            ok = tg.invia(testo, anteprima, url, html=False)
            if not ok and tg.rifiutato:
                # rifiutato comunque: esce dalla coda, altrimenti bloccherebbe per sempre tutti gli altri
                log.error("Telegram rifiuta il messaggio anche senza formattazione: %d annunci tolti dalla coda "
                          "(stato 'scartato', vedi --esporta)", len(uids))
                archivio.segna_rifiutati(uids, "Telegram rifiuta il messaggio")
                continue
        if not ok:
            log.warning("Telegram non ha accettato il messaggio: %d annunci restano in coda",
                        sum(len(m[1]) for m in messaggi[i:]))
            break
        archivio.segna_inviati(uids)
        inviati += len(uids)
        if ultimo and coda_guasti:
            _chiudi_guasti(archivio, problemi, adesso)
    if inviati:
        archivio.set("ultimo_invio", adesso.strftime("%Y-%m-%d %H:%M"))
    return inviati
