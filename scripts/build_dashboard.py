"""
build_dashboard.py – Erzeugt dashboard.html aus templates/dashboard_template.html
und data/scored_data.json. Die Daten werden in die HTML-Datei eingebettet,
damit sie per Doppelklick ohne Server funktioniert.

Aufruf:  python scripts/build_dashboard.py
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
TEMPLATE = ROOT / "templates" / "dashboard_template.html"
DATA = ROOT / "data" / "scored_data.json"
OUT = ROOT / "dashboard.html"
PLACEHOLDER = "/*__DATA__*/"


def main() -> int:
    data = json.loads(DATA.read_text(encoding="utf-8"))
    # Für das Dashboard unnötige Felder entfernen (hält die Datei klein)
    for s in data["stocks"]:
        for n in s.get("news", []):
            n.pop("summary", None)
    payload = json.dumps(data, ensure_ascii=False, separators=(",", ":"))
    payload = payload.replace("</", "<\\/")  # verhindert vorzeitiges Schließen des <script>-Tags
    html = TEMPLATE.read_text(encoding="utf-8")
    if PLACEHOLDER not in html:
        raise SystemExit(f"Platzhalter {PLACEHOLDER} fehlt in {TEMPLATE}")
    OUT.write_text(html.replace(PLACEHOLDER, payload), encoding="utf-8")
    print(f"Dashboard geschrieben: {OUT} ({OUT.stat().st_size / 1024:.0f} KB)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
