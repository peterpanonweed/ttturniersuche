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
import requests, re
from html.parser import HTMLParser

app = Flask(__name__)
CORS(app)  # erlaubt Anfragen aus dem Browser

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
    # Sicherheit: nur click-TT Domains erlaubt
    allowed = ["click-tt.de", "mytischtennis.de"]
    if not any(d in url for d in allowed):
        return jsonify({"error": "Domain nicht erlaubt"}), 403
    try:
        resp = requests.get(url, headers=HEADERS, timeout=10)
        resp.encoding = "utf-8"
        parser = TurnierParser()
        parser.feed(resp.text)
        return jsonify({"tournaments": parser.tournaments, "url": url})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/websearch")
def websearch():
    q = request.args.get("q", "").strip()
    if not q:
        return jsonify({"error": "Kein Suchbegriff"}), 400
    try:
        search_url = "https://www.google.com/search?q=" + requests.utils.quote(q) + "&num=10&hl=de"
        resp = requests.get(search_url, headers=HEADERS, timeout=10)
        html = resp.text
        results = []
        import re as _re
        titles = _re.findall(r'<h3[^>]*>(.*?)</h3>', html)
        links = _re.findall(r'href="/url\?q=(https?://[^&"]+)', html)
        for title, link in zip(titles[:8], links[:8]):
            clean = _re.sub(r'<[^>]+>', '', title).strip()
            if not clean or len(clean) < 8:
                continue
            if any(x in link for x in ['google.', 'youtube.', 'facebook.', 'wikipedia.']):
                continue
            results.append({
                "name": clean,
                "link": link,
                "ort": "Siehe Link",
                "datum": "2026",
                "offenFor": "Freie Meldung",
                "altersklasse": "",
                "verband": "Freier Veranstalter",
                "external": True,
            })
        return jsonify({"results": results, "query": q})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/health")
def health():
    return jsonify({"status": "ok", "service": "TT-Turniersuche Proxy v1.1"})


if __name__ == "__main__":
    print("TT-Turniersuche Proxy läuft auf http://localhost:5000")
    print("Stoppen mit Ctrl+C")
    app.run(host="127.0.0.1", port=5000, debug=False)
