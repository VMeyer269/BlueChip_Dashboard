"""
run_all.py – Führt die komplette Pipeline aus:
  1. scripts/fetch_data.py      Rohdaten laden          -> data/raw_data.json
  2. scripts/compute_scores.py  Kauf-Score berechnen    -> data/scored_data.json
  3. scripts/build_dashboard.py Dashboard erzeugen      -> dashboard.html
  4. scripts/validate.py        Daten und Scores prüfen

Optionen:
  --skip-fetch   vorhandene data/raw_data.json wiederverwenden (offline neu bauen)

Aufruf:  python run_all.py
"""
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
STEPS = ["fetch_data.py", "compute_scores.py", "build_dashboard.py", "validate.py"]


def main() -> int:
    steps = STEPS[1:] if "--skip-fetch" in sys.argv else STEPS
    for step in steps:
        print(f"\n=== {step} ===", flush=True)
        rc = subprocess.call([sys.executable, str(ROOT / "scripts" / step)], cwd=ROOT)
        if rc != 0:
            print(f"\n{step} beendet mit Code {rc}.")
            return rc
    print(f"\nFertig. Dashboard: {ROOT / 'dashboard.html'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
