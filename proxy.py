#!/usr/bin/env python3
"""
TT-Turniersuche Proxy — umgeht CORS-Blockierung der click-TT Server.

INSTALLATION:
    pip install flask flask-cors requests

STARTEN:
    python3 proxy.py

Läuft dann auf http://localhost:5000
Die HTML-App (tt-turniersuche.html) verbindet sich automatisch damit.
"""

from flask import Flask, request, jsonify
from flask_cors import CORS
import requests, re, os, time, threading
from urllib.parse import urlparse
from html.parser import HTMLParser

app = Flask(__name__)
CORS(app)  # erlaubt Anfragen aus dem Browser

# Schluessel kommt aus der Umgebung. Frueher stand er im Klartext hier
# drin — in einem oeffentlichen Repo. Bei Render unter
# Settings -> Environment als SERPAPI_KEY hinterlegen.
SERPAPI_KEY = os.environ.get("SERPAPI_KEY", "")

ERLAUBTE_HOSTS = ("click-tt.de", "mytischtennis.de")

# Zwischenspeicher. Gemessen liegt der Engpass nicht im Netz, sondern in der
# Rechenleistung dieser Instanz: acht gleichzeitige Abrufe derselben Seite
# brauchen je 3 s statt 1,1 s. Mehr Gleichzeitigkeit hilft deshalb nicht,
# weniger Arbeit schon. Ein Turnierkalender aendert sich hoechstens taeglich.
KALENDER_TTL = 1800      # 30 Minuten
SUCHE_TTL = 21600        # 6 Stunden — jeder SerpAPI-Aufruf kostet Geld
_speicher = {}
_speicher_schloss = threading.Lock()


def gespeichert(schluessel, ttl):
    with _speicher_schloss:
        eintrag = _speicher.get(schluessel)
        if not eintrag:
            return None
        abgelegt, wert = eintrag
        if time.time() - abgelegt > ttl:
            _speicher.pop(schluessel, None)
            return None
        return wert


def speichern(schluessel, wert):
    with _speicher_schloss:
        if len(_speicher) > 400:     # Deckel, damit der Speicher nicht waechst
            _speicher.clear()
        _speicher[schluessel] = (time.time(), wert)


def host_erlaubt(url):
    """Prueft den geparsten Hostnamen, nicht die rohe Zeichenkette.

    Ein Test wie 'click-tt.de' in url haette auch
    https://click-tt.de.fremde-domain.example/ durchgelassen.
    """
    try:
        teile = urlparse(url)
    except ValueError:
        return False
    if teile.scheme not in ("http", "https"):
        return False
    host = (teile.hostname or "").lower().rstrip(".")
    return any(host == d or host.endswith("." + d) for d in ERLAUBTE_HOSTS)


HEADERS = {
    "User-Agent": "Mozilla/5.0 (compatible; TT-Turniersuche/1.0)",
    "Accept": "text/html,application/xhtml+xml",
    "Accept-Language": "de-DE,de;q=0.9",
}

class TurnierParser(HTMLParser):
    """Parst die click-TT Turniertabelle."""
    def __init__(self):
        super().__init__()
        self.in_table = False
        self.in_row = False
        self.in_cell = False
        self.current_row = []
        self.current_cell = ""
        self.current_link = ""
        self.tournaments = []

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag == "tr":
            self.in_row = True
            self.current_row = []
        elif tag == "td" and self.in_row:
            self.in_cell = True
            self.current_cell = ""
            self.current_link = ""
        elif tag == "a" and self.in_cell:
            href = attrs.get("href", "")
            m = re.search(r"tournament=(\d+)", href)
            if m:
                self.current_link = m.group(1)

    def handle_endtag(self, tag):
        if tag == "td" and self.in_cell:
            self.current_row.append((self.current_cell.strip(), self.current_link))
            self.in_cell = False
        elif tag == "tr" and self.in_row:
            if len(self.current_row) >= 4:
                datum  = self.current_row[0][0]
                name   = self.current_row[1][0]
                tid    = self.current_row[1][1]
                ort    = self.current_row[2][0]
                offen  = self.current_row[3][0]
                ak     = self.current_row[4][0] if len(self.current_row) > 4 else ""
                if name and datum and datum != "Termin":
                    self.tournaments.append({
                        "datum": datum, "name": name, "id": tid,
                        "ort": ort, "offenFor": offen, "altersklasse": ak
                    })
            self.in_row = False

    def handle_data(self, data):
        if self.in_cell:
            self.current_cell += data


@app.route("/fetch")
def fetch():
    url = request.args.get("url")
    if not url:
        return jsonify({"error": "Kein URL angegeben"}), 400
    if not host_erlaubt(url):
        return jsonify({"error": "Domain nicht erlaubt"}), 403

    fertig = gespeichert(("fetch", url), KALENDER_TTL)
    if fertig is not None:
        return jsonify({**fertig, "aus_speicher": True})

    try:
        # click-TT antwortet beim ersten Aufruf nach einer Ruhephase traege.
        resp = requests.get(url, headers=HEADERS, timeout=20)
        resp.encoding = "utf-8"
        parser = TurnierParser()
        parser.feed(resp.text)
        ergebnis = {"tournaments": parser.tournaments, "url": url}
        speichern(("fetch", url), ergebnis)
        return jsonify(ergebnis)
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/websearch")
def websearch():
    q = request.args.get("q", "").strip()
    if not q:
        return jsonify({"error": "Kein Suchbegriff"}), 400

    if not SERPAPI_KEY:
        return jsonify({"error": "Websuche nicht konfiguriert", "results": []}), 503

    fertig = gespeichert(("suche", q.lower()), SUCHE_TTL)
    if fertig is not None:
        return jsonify({**fertig, "aus_speicher": True})

    try:
        url = f"https://serpapi.com/search.json?q={requests.utils.quote(q)}&api_key={SERPAPI_KEY}&hl=de&gl=de&num=10"
        # SerpAPI braucht beim ersten Aufruf regelmaessig laenger als 10 s;
        # gemessen lief genau daran der erste Klick nach dem Aufwachen ins Leere.
        resp = requests.get(url, timeout=25)
        data = resp.json()

        if "organic_results" not in data:
            return jsonify({"results": [], "query": q})

        results = []
        for item in data["organic_results"]:
            results.append({
                "name": item.get("title", ""),
                "link": item.get("link", ""),
                "snippet": item.get("snippet", ""),
                "ort": "Siehe Link",
                "datum": "2026",
                "offenFor": "Freie Meldung",
                "altersklasse": "",
                "verband": "Freier Veranstalter",
                "external": True,
            })
        ergebnis = {"results": results, "query": q}
        speichern(("suche", q.lower()), ergebnis)
        return jsonify(ergebnis)
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/health")
def health():
    return jsonify({"status": "ok", "service": "TT-Turniersuche Proxy"})


if __name__ == "__main__":
    print("TT-Turniersuche Proxy läuft auf http://localhost:5000")
    print("Stoppen mit Ctrl+C")
    app.run(host="127.0.0.1", port=5000, debug=False)
