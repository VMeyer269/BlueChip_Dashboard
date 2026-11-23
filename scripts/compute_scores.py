"""
compute_scores.py – Berechnet den Kauf-Score (0–100) aus data/raw_data.json.

Vier gleich gewichtete Teile à 25 Punkte:

1. Momentum (25)   = 12,5 × Perzentil(1M-Rendite) + 12,5 × Perzentil(1J-Rendite)
                     Perzentil = Rang innerhalb der 50 Aktien, 0 = schwächste, 1 = stärkste.
2. Bewertung (25)  = 25 × clamp(1,5 − KGV / Branchen-KGV)
                     Branchen-KGV = Median der KGVs derselben Branche in der Auswahl
                     (Median statt Mittelwert, damit Ausreißer wie KGV 400 den Vergleich
                     nicht verzerren). KGV halb so hoch wie die Branche -> 25 Punkte,
                     gleich hoch -> 12,5, 1,5-fach oder höher -> 0.
                     Branchen mit < 3 KGV-Werten -> Vergleich mit dem Median aller Aktien.
                     Verlust (EPS <= 0) -> 0 Punkte. Kein KGV verfügbar -> 12,5 (neutral).
3. Analysten (25)  = Konsens 12,5 × clamp((3 − Empfehlungsmittel) / 2)
                       (1 = Strong Buy -> 12,5; 2 = Buy -> 6,25; ≥ 3 = Hold/Sell -> 0)
                     + Kursziel 12,5 × clamp((Upside + 5 %) / 35 %)
                       (−5 % oder weniger -> 0; +30 % oder mehr -> 12,5)
                     Fehlende Werte -> jeweils halbe Punkte (neutral).
4. Nachrichten (25) = 12,5 × (1 + Ø Stimmung), Stimmung je Schlagzeile +1 / 0 / −1
                     (Lexikon-Verfahren, siehe sentiment()). Keine News -> 12,5.

Aufruf:  python scripts/compute_scores.py
"""
from __future__ import annotations

import json
import re
import statistics as st
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
IN_FILE = ROOT / "data" / "raw_data.json"
OUT_FILE = ROOT / "data" / "scored_data.json"

MIN_SECTOR_PEERS = 3

# ------------------------------------------------------------------ Nachrichten-Stimmung
# Bewusst einfaches, nachvollziehbares Wortlisten-Verfahren (englisch + deutsch).
POSITIVE = {
    "beat", "beats", "surge", "surges", "surged", "soar", "soars", "soared", "rally", "rallies",
    "jump", "jumps", "jumped", "gain", "gains", "rise", "rises", "rising", "record", "upgrade",
    "upgrades", "upgraded", "outperform", "outperforms", "bullish", "strong", "stronger", "strength",
    "growth", "profit", "profits", "raises", "raised", "boost", "boosts", "tops", "higher", "wins",
    "win", "won", "approval", "approved", "approves", "partnership", "expands", "expansion",
    "breakthrough", "rebound", "rebounds", "recover", "recovers", "recovery", "optimistic",
    "buyback", "upbeat", "climbs", "climb", "climbed", "accelerate", "accelerates", "boom",
    "best", "top", "winner", "winners", "upside", "momentum", "lifts", "lift", "positive",
    "exceeds", "exceed", "highs", "all-time", "confident", "launches", "deal", "secures",
    "pops", "popped", "skyrockets", "outpaces", "good",
    "steigt", "steigen", "gewinn", "gewinne", "rekord", "hochgestuft", "kaufempfehlung",
    "erholung", "wachstum", "übertrifft", "stark", "starke", "kursziel erhöht", "auftrag",
}
NEGATIVE = {
    "miss", "misses", "missed", "fall", "falls", "fell", "falling", "drop", "drops", "dropped",
    "plunge", "plunges", "plunged", "slump", "slumps", "sink", "sinks", "tumble", "tumbles",
    "decline", "declines", "declined", "downgrade", "downgrades", "downgraded", "cut", "cuts",
    "lawsuit", "sued", "sues", "probe", "investigation", "fine", "fined", "recall", "loss",
    "losses", "weak", "weaker", "weakness", "warning", "warns", "warned", "bearish", "sell-off",
    "selloff", "crash", "crashes", "fears", "fear", "concern", "concerns", "layoffs", "layoff",
    "slides", "slide", "slid", "lower", "delay", "delays", "delayed", "halt", "halts", "ban",
    "bans", "tariff", "tariffs", "strike", "scandal", "fraud", "bankruptcy", "slowdown",
    "struggle", "struggles", "struggling", "worst", "risk", "risks", "pressure", "underperform",
    "plummet", "plummets", "bubble", "selling", "sell", "dumps", "dump", "worries", "worry",
    "headwinds", "lose", "loses", "losing", "down", "negative", "volatile", "uncertainty",
    "slip", "slips", "slipped", "sinking", "tumbling", "slashes", "slashed", "cautious", "overvalued",
    "fällt", "fallen", "verlust", "verluste", "herabgestuft", "warnung", "einbruch",
    "schwach", "schwache", "klage", "kursziel gesenkt", "stellenabbau", "gewinnwarnung",
}
RAISE_NEG = {"question", "questions", "concern", "concerns", "risk", "risks", "doubt", "doubts", "fears", "alarm"}
NEGATORS = {"not", "no", "never", "without", "fails", "failed", "nicht", "kein", "keine"}
WORD_RE = re.compile(r"[a-zäöüß][a-zäöüß\-']*", re.IGNORECASE)
CLAUSE_RE = re.compile(r"[:;,.!?|]|\s[–—-]\s")


def sentiment(title: str) -> dict:
    """Bewertet eine Schlagzeile. Rückgabe: label (positiv/neutral/negativ), value (+1/0/−1), Treffer."""
    text = title.lower()
    pos_hits, neg_hits = [], []
    # Teilsätze getrennt auswerten, damit eine Verneinung nicht über Satzzeichen hinweg wirkt
    for clause in CLAUSE_RE.split(text):
        words = WORD_RE.findall(clause)
        # "X slips despite profit beat": der Teil nach "despite/trotz" ist Nebensache und zählt nicht
        for conj in ("despite", "trotz"):
            if conj in words:
                words = words[:words.index(conj)]
        for i, w in enumerate(words):
            negated = any(p in NEGATORS for p in words[max(0, i - 3):i])
            # "raises questions / concerns / risks" ist negativ, nicht positiv
            if w in ("raises", "raised", "raise") and any(x in RAISE_NEG for x in words[i + 1:i + 4]):
                neg_hits.append(f"{w} …{next(x for x in words[i + 1:i + 4] if x in RAISE_NEG)}")
                continue
            if w in RAISE_NEG and any(x in ("raises", "raised", "raise") for x in words[max(0, i - 3):i]):
                continue  # bereits beim "raises" gezählt
            if w in POSITIVE:
                (neg_hits if negated else pos_hits).append(("nicht " if negated else "") + w)
            elif w in NEGATIVE:
                (pos_hits if negated else neg_hits).append(("nicht " if negated else "") + w)
    for phrase in ("kursziel erhöht", "kursziel gesenkt", "price target raised", "price target cut"):
        if phrase in text:
            (pos_hits if ("erhöht" in phrase or "raised" in phrase) else neg_hits).append(phrase)
    net = len(pos_hits) - len(neg_hits)
    if title.strip().endswith("?"):        # Frage-Schlagzeilen ("Is X a buy?") sind unsicher
        net = net / 2
    if net >= 1:
        label, value = "positiv", 1
    elif net <= -1:
        label, value = "negativ", -1
    else:
        label, value = "neutral", 0
    return {"label": label, "value": value, "pos_hits": pos_hits, "neg_hits": neg_hits}


# ------------------------------------------------------------------ Hilfsfunktionen
def clamp(x: float, lo: float = 0.0, hi: float = 1.0) -> float:
    return max(lo, min(hi, x))


def percentiles(values: dict[str, float | None]) -> dict[str, float | None]:
    """Perzentil-Rang 0..1 (Durchschnittsrang bei Gleichstand); None bleibt None."""
    valid = sorted((v, k) for k, v in values.items() if v is not None)
    n = len(valid)
    out: dict[str, float | None] = {k: None for k in values}
    i = 0
    while i < n:
        j = i
        while j + 1 < n and valid[j + 1][0] == valid[i][0]:
            j += 1
        rank = (i + j) / 2
        for t in range(i, j + 1):
            out[valid[t][1]] = rank / (n - 1) if n > 1 else 0.5
        i = j + 1
    return out


def r1(x):
    return None if x is None else round(x, 1)


# ------------------------------------------------------------------ Score
def compute(stocks: list[dict]) -> list[dict]:
    p1m = percentiles({s["symbol"]: s.get("chg_1m") for s in stocks})
    p1y = percentiles({s["symbol"]: s.get("chg_1y") for s in stocks})

    def valid_pe(s):
        pe, eps = s.get("pe_trailing"), s.get("eps_trailing")
        return pe if pe is not None and pe > 0 and (eps is None or eps > 0) else None

    all_pes = [valid_pe(s) for s in stocks if valid_pe(s) is not None]
    overall_median = st.median(all_pes)
    sector_pes: dict[str, list[float]] = {}
    for s in stocks:
        if valid_pe(s) is not None:
            sector_pes.setdefault(s["sector"], []).append(valid_pe(s))
    sector_stats = {
        sec: {"median_pe": round(st.median(v), 2), "mean_pe": round(st.mean(v), 2), "n": len(v)}
        for sec, v in sector_pes.items()
    }

    for s in stocks:
        sym = s["symbol"]
        notes: list[str] = []

        # 1) Momentum
        m1 = 12.5 * p1m[sym] if p1m[sym] is not None else 6.25
        m2 = 12.5 * p1y[sym] if p1y[sym] is not None else 6.25
        if p1m[sym] is None or p1y[sym] is None:
            notes.append("Kurshistorie unvollständig – Momentum teilweise neutral")
        momentum = m1 + m2

        # 2) Bewertung
        pe = valid_pe(s)
        stats = sector_stats.get(s["sector"])
        if stats and stats["n"] >= MIN_SECTOR_PEERS:
            bench, bench_label = stats["median_pe"], f"Branchen-Median ({stats['n']} Werte)"
        else:
            bench, bench_label = overall_median, "Median aller 50 (Branche zu klein)"
        rel = None
        if s.get("eps_trailing") is not None and s["eps_trailing"] <= 0:
            valuation = 0.0
            notes.append("Verlust im letzten Jahr – kein sinnvolles KGV, 0 Bewertungspunkte")
        elif pe is None:
            valuation = 12.5
            notes.append("KGV fehlt – Bewertung neutral (12,5)")
        else:
            rel = pe / bench
            valuation = 25 * clamp(1.5 - rel)

        # 3) Analysten
        rm = s.get("recommendation_mean")
        cons = 12.5 * clamp((3 - rm) / 2) if rm is not None else 6.25
        upside = None
        if s.get("target_mean") and s.get("price"):
            upside = s["target_mean"] / s["price"] - 1
            tgt = 12.5 * clamp((upside + 0.05) / 0.35)
        else:
            tgt = 6.25
            notes.append("Kein Kursziel – Kursziel-Teil neutral")
        if rm is None:
            notes.append("Kein Analysten-Konsens – Konsens-Teil neutral")
        analysts = cons + tgt

        # 4) Nachrichten
        for n in s.get("news", []):
            n["sentiment"] = sentiment(n["title"])
        vals = [n["sentiment"]["value"] for n in s.get("news", [])]
        avg = st.mean(vals) if vals else 0.0
        news_pts = 12.5 * (1 + avg)
        if not vals:
            notes.append("Keine Schlagzeilen – Stimmung neutral")

        total = momentum + valuation + analysts + news_pts
        s["score"] = {
            "total": round(total, 1),
            "momentum": round(momentum, 1),
            "valuation": round(valuation, 1),
            "analysts": round(analysts, 1),
            "news": round(news_pts, 1),
            "detail": {
                "momentum_1m_pct_rank": r1(None if p1m[sym] is None else p1m[sym] * 100),
                "momentum_1y_pct_rank": r1(None if p1y[sym] is None else p1y[sym] * 100),
                "momentum_1m_pts": round(m1, 1),
                "momentum_1y_pts": round(m2, 1),
                "pe_used": None if pe is None else round(pe, 2),
                "pe_benchmark": round(bench, 2),
                "pe_benchmark_label": bench_label,
                "pe_relative": None if rel is None else round(rel, 2),
                "consensus_pts": round(cons, 1),
                "upside_pct": None if upside is None else round(upside * 100, 1),
                "target_pts": round(tgt, 1),
                "news_avg": round(avg, 2),
                "news_counts": {
                    "positiv": vals.count(1), "neutral": vals.count(0), "negativ": vals.count(-1)
                },
            },
            "notes": notes,
        }
    return sector_stats


def main() -> int:
    data = json.loads(IN_FILE.read_text(encoding="utf-8"))
    stocks = data["stocks"]
    sector_stats = compute(stocks)
    stocks.sort(key=lambda s: s["score"]["total"], reverse=True)
    for i, s in enumerate(stocks, 1):
        s["score"]["rank"] = i
    data["meta"]["sector_stats"] = sector_stats
    data["meta"]["score_method"] = {
        "momentum": "12,5 × Perzentil 1M + 12,5 × Perzentil 1J (Rang unter allen 50)",
        "valuation": "25 × clamp(1,5 − KGV/Branchen-Median-KGV); Verlust = 0; fehlend = 12,5",
        "analysts": "12,5 × clamp((3 − Konsens)/2) + 12,5 × clamp((Upside + 5 %)/35 %)",
        "news": "12,5 × (1 + Ø Stimmung der 3 Schlagzeilen, je +1/0/−1)",
    }
    OUT_FILE.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"Scores berechnet für {len(stocks)} Aktien -> {OUT_FILE}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
