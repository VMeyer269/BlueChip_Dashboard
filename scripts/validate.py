"""
validate.py – Prüft Rohdaten, Scores und Dashboard auf Vollständigkeit und Plausibilität.

Prüfungen
  A  Auswahl: genau 30 US + 20 DE, keine Duplikate, Auswahl = Top-N der gespeicherten Rangliste
  B  Vollständigkeit: alle Pflichtfelder pro Aktie, je 3 Schlagzeilen mit Titel, Link, Datum
  C  Plausibilität: Kurs innerhalb 52W-Spanne, Kursziel-Abstand, Dividendenrendite, KGV usw.
  D  Scores: unabhängig nachgerechnet (eigene Implementierung) und mit scored_data.json verglichen
  E  Dashboard: dashboard.html enthält alle 50 Aktien mit identischen Scores

Exit-Code 0 = keine Fehler (Warnungen möglich), 1 = Fehler gefunden.
Aufruf:  python scripts/validate.py
"""
from __future__ import annotations

import json
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
RAW = ROOT / "data" / "raw_data.json"
SCORED = ROOT / "data" / "scored_data.json"
HTML = ROOT / "dashboard.html"

errors: list[str] = []
warnings: list[str] = []


def err(msg):
    errors.append(msg)


def warn(msg):
    warnings.append(msg)


# ------------------------------------------------------------------ A Auswahl
def check_selection(raw):
    st = raw["stocks"]
    us = [s for s in st if s["country"] == "US"]
    de = [s for s in st if s["country"] == "DE"]
    if len(st) != 50:
        err(f"A: {len(st)} statt 50 Aktien")
    if len(us) != 30 or len(de) != 20:
        err(f"A: {len(us)} US / {len(de)} DE statt 30/20")
    syms = [s["symbol"] for s in st]
    if len(set(syms)) != len(syms):
        err("A: doppelte Symbole")
    names = [s["name"].lower() for s in st]
    if len(set(names)) != len(names):
        err("A: doppelte Unternehmensnamen (mehrere Aktiengattungen?)")
    m = raw["meta"]
    for key, sel, n in (("ranking_us", us, 30), ("ranking_de", de, 20)):
        ranking = m.get(key)
        if not ranking:
            err(f"A: Rangliste {key} fehlt in meta")
            continue
        expected = [r["symbol"] for r in ranking[:n]]
        got = [s["symbol"] for s in sel]
        if sorted(expected) != sorted(got):
            err(f"A: {key} – Auswahl weicht von Top {n} ab: {set(expected) ^ set(got)}")
        caps = [r["marketCap"] for r in ranking]
        if caps != sorted(caps, reverse=True):
            err(f"A: {key} nicht absteigend sortiert")
        fallback = [r["symbol"] for r in ranking[: n + 5] if r.get("mcap_source") != "info"]
        if fallback:
            warn(f"A: {key} – Marktkap. per Schnellwert statt .info: {fallback}")
        if len(ranking) > n:
            gap = ranking[n - 1]["marketCap"] / ranking[n]["marketCap"] - 1
            print(f"  {key}: Platz {n} {ranking[n-1]['symbol']} ({ranking[n-1]['marketCap']/1e9:.0f} Mrd.) "
                  f"vs. Platz {n+1} {ranking[n]['symbol']} ({ranking[n]['marketCap']/1e9:.0f} Mrd.), Abstand {gap*100:.1f} %")


# ------------------------------------------------------------------ B Vollständigkeit
REQUIRED = ["price", "chg_1d", "chg_1m", "chg_1y", "market_cap", "high_52w", "low_52w",
            "recommendation_key", "recommendation_mean", "target_mean"]


def check_completeness(raw):
    for s in raw["stocks"]:
        sym = s["symbol"]
        for f in REQUIRED:
            if s.get(f) is None:
                err(f"B: {sym} – Feld '{f}' fehlt")
        if s.get("pe_trailing") is None:
            eps = s.get("eps_trailing")
            if eps is not None and eps <= 0:
                warn(f"B: {sym} – kein KGV, da Verlust (EPS {eps}) – im Score berücksichtigt")
            else:
                err(f"B: {sym} – KGV fehlt ohne erkennbaren Grund")
        if s.get("dividend_yield_pct") is None:
            warn(f"B: {sym} – keine Dividendenrendite (zahlt evtl. keine Dividende)")
        if s.get("sector") in (None, "", "Unbekannt"):
            err(f"B: {sym} – Branche fehlt")
        news = s.get("news", [])
        if len(news) != 3:
            err(f"B: {sym} – {len(news)} statt 3 Schlagzeilen")
        for n in news:
            if not (n.get("title") and n.get("url", "").startswith("http") and n.get("date")):
                err(f"B: {sym} – Schlagzeile unvollständig: {n}")
        dates = [n["date"] for n in news]
        if dates != sorted(dates, reverse=True):
            err(f"B: {sym} – Schlagzeilen nicht nach Datum sortiert")
        if len(s.get("history", {}).get("weekly_close", [])) < 40:
            warn(f"B: {sym} – kurze Kurshistorie")


# ------------------------------------------------------------------ C Plausibilität
def check_plausibility(raw):
    gen = datetime.fromisoformat(raw["meta"]["generated_at"])
    for s in raw["stocks"]:
        sym, p = s["symbol"], s.get("price")
        if p is None:
            continue
        lo, hi = s.get("low_52w"), s.get("high_52w")
        if lo and hi and not (lo * 0.97 <= p <= hi * 1.03):
            warn(f"C: {sym} – Kurs {p} außerhalb 52W-Spanne {lo}–{hi}")
        if s.get("target_mean"):
            up = s["target_mean"] / p - 1
            if not -0.6 < up < 1.5:
                err(f"C: {sym} – unplausibler Kursziel-Abstand {up:.0%} (Währungsfehler?)")
        dy = s.get("dividend_yield_pct")
        if dy is not None and not 0 <= dy <= 15:
            err(f"C: {sym} – unplausible Dividendenrendite {dy} %")
        pe = s.get("pe_trailing")
        if pe is not None and not 0 < pe < 1000:
            err(f"C: {sym} – unplausibles KGV {pe}")
        if s.get("market_cap") and s["market_cap"] < 1e10:
            err(f"C: {sym} – Marktkap. < 10 Mrd. – kein Blue Chip?")
        wc = s.get("history", {}).get("weekly_close", [])
        if wc and abs(wc[-1] / p - 1) > 0.08:
            warn(f"C: {sym} – letzter Wochenschluss {wc[-1]} weicht >8 % vom Kurs {p} ab")
        last = s.get("history", {}).get("last_date")
        if last and (gen.date() - datetime.fromisoformat(last).date()).days > 5:
            err(f"C: {sym} – Kurshistorie veraltet (letzter Tag {last})")
        for n in s.get("news", []):
            age = (gen - datetime.fromisoformat(n["date"].replace("Z", "+00:00"))).days
            if age > 60:
                warn(f"C: {sym} – Schlagzeile {age} Tage alt: {n['title'][:50]}")
        if s["country"] == "DE" and s.get("currency") != "EUR":
            err(f"C: {sym} – Währung {s.get('currency')} statt EUR")
        if s["country"] == "US" and s.get("currency") != "USD":
            err(f"C: {sym} – Währung {s.get('currency')} statt USD")


# ------------------------------------------------------------------ D Scores (Zweitimplementierung)
def pct_rank(values: dict):
    """Perzentil 0..1 per Zählmethode: (Anzahl kleiner + 0,5 × Anzahl gleich ohne sich selbst) / (n − 1)."""
    vals = [v for v in values.values() if v is not None]
    n = len(vals)
    out = {}
    for k, v in values.items():
        if v is None:
            out[k] = None
            continue
        less = sum(1 for x in vals if x < v)
        equal = sum(1 for x in vals if x == v) - 1
        out[k] = (less + equal / 2) / (n - 1)
    return out


def median(xs):
    xs = sorted(xs)
    n = len(xs)
    return xs[n // 2] if n % 2 else (xs[n // 2 - 1] + xs[n // 2]) / 2


def lim(x):
    return min(1.0, max(0.0, x))


def recompute(raw, scored_by_sym):
    st = raw["stocks"]
    r1m = pct_rank({s["symbol"]: s["chg_1m"] for s in st})
    r1y = pct_rank({s["symbol"]: s["chg_1y"] for s in st})
    ok_pe = {s["symbol"]: s["pe_trailing"] for s in st
             if s.get("pe_trailing") and s["pe_trailing"] > 0 and (s.get("eps_trailing") is None or s["eps_trailing"] > 0)}
    by_sector = {}
    for s in st:
        if s["symbol"] in ok_pe:
            by_sector.setdefault(s["sector"], []).append(ok_pe[s["symbol"]])
    overall = median(list(ok_pe.values()))
    max_diff = 0.0
    for s in st:
        sym = s["symbol"]
        mom = (12.5 * r1m[sym] if r1m[sym] is not None else 6.25) + (12.5 * r1y[sym] if r1y[sym] is not None else 6.25)
        peers = by_sector.get(s["sector"], [])
        bench = median(peers) if len(peers) >= 3 else overall
        if s.get("eps_trailing") is not None and s["eps_trailing"] <= 0:
            val = 0.0
        elif sym not in ok_pe:
            val = 12.5
        else:
            val = 25 * lim(1.5 - ok_pe[sym] / bench)
        rm = s.get("recommendation_mean")
        ana = (12.5 * lim((3 - rm) / 2) if rm is not None else 6.25)
        ana += 12.5 * lim((s["target_mean"] / s["price"] - 1 + 0.05) / 0.35) if s.get("target_mean") and s.get("price") else 6.25
        sc = scored_by_sym[sym]["score"]
        labels = [n["sentiment"]["value"] for n in scored_by_sym[sym]["news"]]
        nws = 12.5 * (1 + (sum(labels) / len(labels) if labels else 0))
        mine = {"momentum": mom, "valuation": val, "analysts": ana, "news": nws}
        mine["total"] = sum(mine.values())
        for k, v in mine.items():
            d = abs(round(v, 1) - sc[k])
            max_diff = max(max_diff, d)
            if d > 0.15:
                err(f"D: {sym} – {k}: nachgerechnet {v:.2f}, gespeichert {sc[k]}")
        parts = sc["momentum"] + sc["valuation"] + sc["analysts"] + sc["news"]
        if abs(parts - sc["total"]) > 0.25:
            err(f"D: {sym} – Summe der Teile {parts:.1f} ≠ Gesamt {sc['total']}")
        for k in ("momentum", "valuation", "analysts", "news"):
            if not 0 <= sc[k] <= 25:
                err(f"D: {sym} – Teilscore {k}={sc[k]} außerhalb 0–25")
        for n in scored_by_sym[sym]["news"]:
            sen = n["sentiment"]
            if sen["label"] not in ("positiv", "neutral", "negativ"):
                err(f"D: {sym} – ungültiges Stimmungslabel {sen['label']}")
    return max_diff


def check_scores(raw, scored):
    by_sym = {s["symbol"]: s for s in scored["stocks"]}
    if set(by_sym) != {s["symbol"] for s in raw["stocks"]}:
        err("D: scored_data.json enthält andere Aktien als raw_data.json")
        return
    max_diff = recompute(raw, by_sym)
    ranks = sorted(s["score"]["rank"] for s in scored["stocks"])
    if ranks != list(range(1, 51)):
        err("D: Ränge nicht lückenlos 1–50")
    totals = [s["score"]["total"] for s in sorted(scored["stocks"], key=lambda x: x["score"]["rank"])]
    if totals != sorted(totals, reverse=True):
        err("D: Rang passt nicht zur Score-Reihenfolge")
    print(f"  Scores nachgerechnet – größte Abweichung {max_diff:.2f} Punkte")
    spread = max(totals) - min(totals)
    if spread < 20:
        warn(f"D: geringe Score-Spreizung ({spread:.1f} Punkte)")
    labels = [n["sentiment"]["label"] for s in scored["stocks"] for n in s["news"]]
    print(f"  Stimmung der {len(labels)} Schlagzeilen: " +
          ", ".join(f"{l} {labels.count(l)}" for l in ("positiv", "neutral", "negativ")))


# ------------------------------------------------------------------ E Dashboard
def check_html(scored):
    if not HTML.exists():
        err("E: dashboard.html fehlt")
        return
    html = HTML.read_text(encoding="utf-8")
    m = re.search(r'<script id="dashboard-data" type="application/json">(.*?)</script>', html, re.S)
    if not m:
        err("E: eingebettete Daten nicht gefunden")
        return
    emb = json.loads(m.group(1).replace("<\\/", "</"))
    if len(emb["stocks"]) != 50:
        err(f"E: Dashboard enthält {len(emb['stocks'])} Aktien")
    want = {s["symbol"]: s["score"]["total"] for s in scored["stocks"]}
    got = {s["symbol"]: s["score"]["total"] for s in emb["stocks"]}
    if want != got:
        err("E: Scores im Dashboard weichen von scored_data.json ab")
    if "Demo – keine Anlageberatung" not in html:
        err("E: Hinweis 'Demo – keine Anlageberatung' fehlt")
    if re.search(r'<script[^>]+src=|<link[^>]+href="http', html):
        warn("E: externe Ressourcen eingebunden – Offline-Nutzung eingeschränkt")


# ------------------------------------------------------------------ F Stimmungs-Testfälle
SENTIMENT_CASES = [
    ("Nvidia stock soars 30% after strong earnings", "positiv"),
    ("Home Depot shares fall as weak demand weighs on outlook", "negativ"),
    ("Bayer shares slip despite judge's approval of settlement", "negativ"),
    ("DHL The Latest Update Raises A Bigger Question", "negativ"),
    ("Germany's Merck raises guidance again on growth", "positiv"),
    ("Why did Palantir stock fall?", "neutral"),
    ("Siemens and Mercedes employ 60,000-plus Americans", "neutral"),
    ("Analysts do not expect a downgrade", "positiv"),
    ("Siemens hebt Prognose an – Aktie steigt auf Rekord", "positiv"),
    ("Not Good: Broadcom Loses $200 Billion as Chips Crash", "negativ"),
    ("Hannover Re shares slip despite Q3 profit beat as softer pricing weighs", "negativ"),
]


def check_sentiment():
    sys.path.insert(0, str(ROOT / "scripts"))
    from compute_scores import sentiment
    bad = [(t, exp, sentiment(t)["label"]) for t, exp in SENTIMENT_CASES if sentiment(t)["label"] != exp]
    for t, exp, got in bad:
        err(f"F: Stimmung '{t}' -> {got}, erwartet {exp}")
    print(f"  Stimmungs-Testfälle: {len(SENTIMENT_CASES) - len(bad)}/{len(SENTIMENT_CASES)} korrekt")


def check_news_relevance(raw):
    total = sum(len(s["news"]) for s in raw["stocks"])
    direct = sum(1 for s in raw["stocks"] for n in s["news"] if n.get("relevant"))
    print(f"  Schlagzeilen mit direktem Unternehmensbezug: {direct}/{total}")
    for s in raw["stocks"]:
        if not any(n.get("relevant") for n in s["news"]):
            warn(f"B: {s['symbol']} – keine der 3 Schlagzeilen nennt das Unternehmen direkt")


def main() -> int:
    raw = json.loads(RAW.read_text(encoding="utf-8"))
    scored = json.loads(SCORED.read_text(encoding="utf-8"))
    print(f"Prüfe Daten vom {raw['meta']['generated_at']} …")
    check_selection(raw)
    check_completeness(raw)
    check_plausibility(raw)
    check_news_relevance(raw)
    check_scores(raw, scored)
    check_sentiment()
    check_html(scored)
    for w in warnings:
        print("  WARNUNG", w)
    for e in errors:
        print("  FEHLER ", e)
    print(f"Ergebnis: {len(errors)} Fehler, {len(warnings)} Warnungen")
    return 1 if errors else 0


if __name__ == "__main__":
    sys.exit(main())
