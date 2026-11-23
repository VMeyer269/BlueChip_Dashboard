"""
fetch_data.py – Lädt die Rohdaten für das Blue-Chip-Dashboard.

Ablauf
  1. Aktuelle Indexmitglieder holen (S&P 500 und DAX 40, Quelle: Wikipedia-Listen)
  2. Marktkapitalisierung aller Mitglieder über yfinance ermitteln
  3. Top 30 US-Werte und Top 20 DAX-Werte nach Marktkapitalisierung auswählen
     (mehrere Aktiengattungen eines Unternehmens, z. B. GOOGL/GOOG, zählen nur einmal)
  4. Pro Aktie Kennzahlen, Kurshistorie und die 3 neuesten Schlagzeilen laden
  5. Alles als data/raw_data.json speichern

Aufruf:  python scripts/fetch_data.py
"""
from __future__ import annotations

import io
import json
import re
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pandas as pd
import requests
import yfinance as yf

ROOT = Path(__file__).resolve().parent.parent
OUT_FILE = ROOT / "data" / "raw_data.json"

N_US = 30
N_DE = 20
US_CANDIDATES = 45          # Vorauswahl per Screener, danach genaue Marktkap. via .info
NEWS_PER_STOCK = 3

# Wikipedia verlangt einen aussagekräftigen User-Agent (Robot Policy)
HEADERS = {"User-Agent": "BlueChipDashboard/1.0 (private research script) python-requests"}
CACHE_FILE = ROOT / "data" / "constituents_cache.json"
SP500_URL = "https://en.wikipedia.org/wiki/List_of_S%26P_500_companies"
DAX_URL = "https://en.wikipedia.org/wiki/DAX"


def log(msg: str) -> None:
    print(f"[{datetime.now():%H:%M:%S}] {msg}", flush=True)


def retry(fn, tries: int = 4, wait: float = 2.0):
    """Führt fn() aus und wiederholt bei Fehlern (z. B. Rate-Limits) mit wachsender Pause."""
    last = None
    for i in range(tries):
        try:
            return fn()
        except Exception as exc:  # noqa: BLE001
            last = exc
            time.sleep(wait * (i + 1))
    raise last


# --------------------------------------------------------------------------- Universum
def read_tables(url: str) -> list[pd.DataFrame]:
    def _get():
        r = requests.get(url, headers=HEADERS, timeout=30)
        r.raise_for_status()
        return r.text
    return pd.read_html(io.StringIO(retry(_get, tries=3, wait=5)))


def xetra_symbol(s: str) -> str:
    """Wikipedia-Ticker -> Yahoo-Xetra-Symbol (z. B. 'SAP' -> 'SAP.DE', 'AIR.PA' -> 'AIR.DE')."""
    s = s.strip().upper()
    return s.split(".")[0] + ".DE"


def load_constituents() -> tuple[pd.DataFrame, pd.DataFrame, str]:
    """Holt die Indexlisten live; bei Fehler wird der letzte erfolgreiche Abruf (Cache) genutzt."""
    try:
        sp, dax = get_sp500(), get_dax()
        CACHE_FILE.parent.mkdir(parents=True, exist_ok=True)
        CACHE_FILE.write_text(json.dumps({
            "fetched_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "sp500": sp.astype(str).to_dict("records"),
            "dax": dax.astype(str).to_dict("records"),
        }, ensure_ascii=False, indent=1), encoding="utf-8")
        return sp, dax, "live"
    except Exception as exc:  # noqa: BLE001
        if not CACHE_FILE.exists():
            raise
        c = json.loads(CACHE_FILE.read_text(encoding="utf-8"))
        log(f"  ! Live-Abruf fehlgeschlagen ({exc}) – nutze Cache vom {c['fetched_at']}")
        return pd.DataFrame(c["sp500"]), pd.DataFrame(c["dax"]), f"cache {c['fetched_at']}"


def get_sp500() -> pd.DataFrame:
    df = read_tables(SP500_URL)[0]
    df = df.rename(columns={"Symbol": "symbol", "Security": "name", "CIK": "cik"})
    df["symbol"] = df["symbol"].str.replace(".", "-", regex=False)  # BRK.B -> BRK-B
    return df[["symbol", "name", "cik"]]


def get_dax() -> pd.DataFrame:
    for t in read_tables(DAX_URL):
        if "Ticker" in t.columns and "Company" in t.columns and len(t) >= 35:
            t = t.rename(columns={"Ticker": "symbol", "Company": "name"})
            # Originalticker merken (z. B. AIR.PA) – Fallback für fehlende Analystendaten
            t["alt_symbol"] = t["symbol"].astype(str).str.strip().str.upper()
            t["symbol"] = t["symbol"].astype(str).apply(xetra_symbol)
            t["cik"] = t["symbol"]
            return t[["symbol", "name", "cik", "alt_symbol"]]
    raise RuntimeError("DAX-Tabelle auf Wikipedia nicht gefunden")


def fast_market_cap(symbol: str) -> float | None:
    try:
        return retry(lambda: float(yf.Ticker(symbol).fast_info["marketCap"]), tries=3)
    except Exception:  # noqa: BLE001
        return None


def fast_caps(symbols: list[str]) -> list[float | None]:
    """Marktkapitalisierung per fast_info, Fehlschläge werden einzeln mit Pause wiederholt."""
    with ThreadPoolExecutor(max_workers=6) as ex:
        caps = dict(zip(symbols, ex.map(fast_market_cap, symbols)))
    missing = [s for s, v in caps.items() if v is None]
    if missing:
        log(f"  {len(missing)} Titel ohne Schnellwert – wiederhole einzeln …")
        time.sleep(15)
        for s in missing:
            caps[s] = fast_market_cap(s)
            time.sleep(0.5)
        still = [s for s, v in caps.items() if v is None]
        if still:
            log(f"  ! weiterhin ohne Wert: {', '.join(still)}")
    return [caps[s] for s in symbols]


def us_screener_caps() -> dict[str, float]:
    """Alle US-Aktien > 50 Mrd. USD Marktkap. über den Yahoo-Screener (1 Anfrage statt ~500)."""
    try:
        from yfinance import EquityQuery as Q
        q = Q("and", [Q("eq", ["region", "us"]), Q("gt", ["intradaymarketcap", 5e10])])
        res = retry(lambda: yf.screen(q, sortField="intradaymarketcap", sortAsc=False, size=250))
        return {x["symbol"]: float(x["marketCap"]) for x in res.get("quotes", []) if x.get("marketCap")}
    except Exception as exc:  # noqa: BLE001
        log(f"  ! Screener fehlgeschlagen: {exc}")
        return {}


def full_info(symbol: str, tries: int = 4, wait: float = 6.0, quiet: bool = False) -> dict:
    def _get():
        info = yf.Ticker(symbol).info
        if not info or ("marketCap" not in info and "regularMarketPrice" not in info):
            raise RuntimeError(f"leere info für {symbol}")
        return info
    try:
        return retry(_get, tries=tries, wait=wait)
    except Exception as exc:  # noqa: BLE001
        if not quiet:
            log(f"  ! info fehlgeschlagen für {symbol}: {str(exc)[:80]}")
        return {}


def fetch_infos(symbols: list[str]) -> dict[str, dict]:
    """Lädt .info für alle Symbole: erst moderat parallel, dann Fehlschläge einzeln mit langen Pausen."""
    with ThreadPoolExecutor(max_workers=3) as ex:
        infos = dict(zip(symbols, ex.map(lambda s: full_info(s, quiet=True), symbols)))
    for round_no in (1, 2):
        failed = [s for s, i in infos.items() if not i]
        if not failed:
            break
        log(f"  Wiederholung {round_no}: {len(failed)} Titel ohne Daten (Rate-Limit) – lade einzeln nach …")
        time.sleep(20 * round_no)
        for s in failed:
            infos[s] = full_info(s, tries=4, wait=10.0 * round_no, quiet=round_no == 1)
            time.sleep(1.0)
    return infos


def select_universe() -> tuple[list[dict], list[dict], dict]:
    log("Lade Indexmitglieder von Wikipedia …")
    sp, dax, source_state = load_constituents()
    log(f"  S&P 500: {len(sp)} Titel, DAX: {len(dax)} Titel (Quelle: {source_state})")

    sp["mcap_fast"] = None
    screener = us_screener_caps()
    if screener:
        log(f"  Yahoo-Screener: {len(screener)} US-Werte > 50 Mrd. USD geladen")
        sp["mcap_fast"] = sp["symbol"].map(screener)
    else:
        log("Screener nicht verfügbar – Einzelabfrage der Marktkapitalisierung (S&P 500) …")
        sp["mcap_fast"] = fast_caps(list(sp["symbol"]))
    dax["mcap_fast"] = fast_caps(list(dax["symbol"]))
    cand = sp.dropna(subset=["mcap_fast"]).sort_values("mcap_fast", ascending=False).head(US_CANDIDATES)

    log(f"Genaue Daten für {len(cand)} US-Kandidaten und {len(dax)} DAX-Titel …")
    infos = fetch_infos(list(cand["symbol"]) + list(dax["symbol"]))
    still_missing = [s for s, i in infos.items() if not i]
    if still_missing:
        log(f"  ! Ohne .info (Ranking per Schnellwert): {', '.join(still_missing)}")

    def rank(df: pd.DataFrame) -> list[dict]:
        """Sortierte Rangliste nach Marktkap., eine Aktiengattung pro Unternehmen."""
        rows = []
        for _, r in df.iterrows():
            info = infos.get(r["symbol"], {})
            # .info liefert die Unternehmens-Marktkap. (alle Aktiengattungen); Fallback: Schnellwert
            mcap = info.get("marketCap") or (None if pd.isna(r["mcap_fast"]) else float(r["mcap_fast"]))
            if not mcap:
                log(f"  ! {r['symbol']} ohne Marktkapitalisierung – nicht rangierbar")
                continue
            alt = r.get("alt_symbol")
            rows.append({"symbol": r["symbol"], "wiki_name": r["name"], "cik": str(r["cik"]),
                         "alt_symbol": alt if isinstance(alt, str) and alt != r["symbol"] else None,
                         "marketCap": float(mcap), "mcap_source": "info" if info.get("marketCap") else "fast"})
        rows.sort(key=lambda x: x["marketCap"], reverse=True)
        seen, out = set(), []
        for row in rows:
            if row["cik"] in seen:
                continue
            seen.add(row["cik"])
            out.append(row)
        return out

    rank_us, rank_de = rank(cand), rank(dax)
    us, de = rank_us[:N_US], rank_de[:N_DE]
    log(f"  Auswahl: {len(us)} US, {len(de)} DE")
    if len(us) < N_US or len(de) < N_DE:
        raise SystemExit(f"Abbruch: nur {len(us)} US / {len(de)} DE Titel ermittelt – bitte später erneut starten.")
    meta = {
        "sp500_members": int(len(sp)),
        "dax_members": int(len(dax)),
        "constituents_source": source_state,
        "sources": {"sp500": SP500_URL, "dax": DAX_URL},
        "us_ranking_source": "yahoo_screener" if screener else "fast_info",
        # vollständige Ranglisten zur Nachprüfung der Auswahl (validate.py)
        "ranking_us": [{k: r[k] for k in ("symbol", "wiki_name", "marketCap", "mcap_source")} for r in rank_us],
        "ranking_de": [{k: r[k] for k in ("symbol", "wiki_name", "marketCap", "mcap_source")} for r in rank_de],
    }
    return [dict(x, country="US", index="S&P 500") for x in us], \
           [dict(x, country="DE", index="DAX") for x in de], {"infos": infos, **meta}


# --------------------------------------------------------------------------- Details
def pct_change_since(close: pd.Series, days: int) -> float | None:
    """Veränderung in % vom letzten Schlusskurs am/vor (heute - days) bis zum letzten Kurs."""
    if close.empty:
        return None
    last_date = close.index[-1]
    past = close[close.index <= last_date - pd.Timedelta(days=days)]
    if past.empty:
        return None
    return round((close.iloc[-1] / past.iloc[-1] - 1) * 100, 2)


def get_history(symbol: str) -> dict:
    hist = retry(lambda: yf.Ticker(symbol).history(period="13mo", interval="1d", auto_adjust=True))
    close = hist["Close"].dropna()
    if close.empty:
        return {}
    close.index = close.index.tz_localize(None)
    weekly = close.resample("W-FRI").last().dropna()
    return {
        "last_close": round(float(close.iloc[-1]), 4),
        "last_date": close.index[-1].strftime("%Y-%m-%d"),
        "chg_1d": round((close.iloc[-1] / close.iloc[-2] - 1) * 100, 2) if len(close) > 1 else None,
        "chg_1m": pct_change_since(close, 30),
        "chg_1y": pct_change_since(close, 365),
        "spark": [round(float(v), 2) for v in weekly.iloc[-53:]],
    }


def parse_news(items: list) -> list[dict]:
    out = []
    for it in items or []:
        c = it.get("content", it)  # yfinance >= 0.2.50 liefert {"content": {...}}
        title = c.get("title")
        url = (c.get("canonicalUrl") or {}).get("url") or (c.get("clickThroughUrl") or {}).get("url") or c.get("link")
        date = c.get("pubDate") or c.get("displayTime")
        if not date and it.get("providerPublishTime"):
            date = datetime.fromtimestamp(it["providerPublishTime"], tz=timezone.utc).isoformat()
        if title and url and date:
            out.append({
                "title": title.strip(),
                "url": url,
                "date": date,
                "publisher": (c.get("provider") or {}).get("displayName") or it.get("publisher") or "",
                "summary": (c.get("summary") or "")[:400],
            })
    out.sort(key=lambda x: x["date"], reverse=True)
    # Duplikate (gleiche Überschrift) entfernen
    seen, uniq = set(), []
    for n in out:
        if n["title"].lower() not in seen:
            seen.add(n["title"].lower())
            uniq.append(n)
    return uniq


# Suchbegriffe, an denen erkannt wird, ob eine Schlagzeile das Unternehmen direkt nennt.
# (Yahoo liefert pro Ticker auch Artikel über Wettbewerber oder den Gesamtmarkt.)
NEWS_ALIASES = {
    "AMZN": ["Amazon", "AWS"], "GOOGL": ["Alphabet", "Google", "YouTube", "Waymo"], "META": ["Meta", "Facebook", "Instagram", "WhatsApp"],
    "BRK-B": ["Berkshire", "Buffett"], "LLY": ["Lilly"], "JPM": ["JPMorgan", "JP Morgan", "Jamie Dimon"],
    "AMD": ["AMD", "Advanced Micro"], "JNJ": ["Johnson & Johnson", "J&J"], "XOM": ["Exxon"],
    "KO": ["Coca-Cola", "Coke"], "PG": ["Procter", "P&G"], "HD": ["Home Depot"],
    "BAC": ["Bank of America", "BofA"], "GE": ["GE Aerospace", "GE", "General Electric"],
    "MRK": ["Merck & Co", "Merck"], "MRK.DE": ["Merck KGaA", "Germany's Merck", "Germany’s Merck", "MKGAF"],
    "SIE.DE": ["Siemens AG", "Siemens"], "ENR.DE": ["Siemens Energy"], "SHL.DE": ["Healthineers"],
    "DTE.DE": ["Telekom", "T-Mobile"], "MUV2.DE": ["Munich Re", "Münchener Rück", "Muenchener Rueck", "Münchener Rückversicherung"],
    "DBK.DE": ["Deutsche Bank"], "DB1.DE": ["Deutsche Börse", "Deutsche Boerse"], "EOAN.DE": ["E.ON", "EON"],
    "HNR1.DE": ["Hannover Re", "Hannover Rück", "Hannover Rueck"], "MBG.DE": ["Mercedes"],
    "BAS.DE": ["BASF"], "CBK.DE": ["Commerzbank"], "ALV.DE": ["Allianz"], "AIR.DE": ["Airbus"],
    "HEN3.DE": ["Henkel"], "RHM.DE": ["Rheinmetall"], "VOW3.DE": ["Volkswagen", "VW"], "BMW.DE": ["BMW"],
}
# Begriffe, die trotz Treffer auf ein anderes Unternehmen hindeuten
NEWS_EXCLUDE = {"MRK": ["KGaA", "Germany"], "SIE.DE": ["Healthineers", "Siemens Energy"]}
GENERIC = {"inc", "inc.", "corp", "corporation", "company", "co", "co.", "the", "group", "holdings", "ag", "se",
           "plc", "ltd", "sa", "nv", "aktiengesellschaft", "&", "and", "platforms", "technologies", "systems"}


def news_aliases(symbol: str, name: str) -> list[str]:
    if symbol in NEWS_ALIASES:
        return NEWS_ALIASES[symbol]
    words = [w.strip(",.").removesuffix(".com") for w in name.split() if w.lower().strip(",.") not in GENERIC]
    return [words[0]] if words else [symbol]


def mentions(title: str, symbol: str, aliases: list[str]) -> bool:
    if any(x.lower() in title.lower() for x in NEWS_EXCLUDE.get(symbol, [])):
        return False
    root = symbol.split(".")[0]
    pats = [rf"(?<![\w]){re.escape(a)}(?![\w])" for a in aliases] + [rf"[(:]{re.escape(root)}\)"]
    return any(re.search(p, title, flags=0 if len(a) <= 3 else re.IGNORECASE)
               for p, a in zip(pats, aliases + [root]))


def get_news(symbol: str, name: str) -> list[dict]:
    """Die 3 neuesten Schlagzeilen – bevorzugt solche, die das Unternehmen direkt nennen."""
    try:
        items = parse_news(retry(lambda: yf.Ticker(symbol).get_news(count=20)))
        if len(items) < 10:  # zweite Quelle ergänzen
            more = parse_news(retry(lambda: yf.Ticker(symbol).news))
            titles = {n["title"].lower() for n in items}
            items = sorted(items + [n for n in more if n["title"].lower() not in titles],
                           key=lambda x: x["date"], reverse=True)
        aliases = news_aliases(symbol, name)
        for n in items:
            n["relevant"] = mentions(n["title"], symbol, aliases) or mentions(n.get("summary", "")[:200], symbol, aliases)
        chosen = [n for n in items if n["relevant"]][:NEWS_PER_STOCK]
        if len(chosen) < NEWS_PER_STOCK:                       # mit neuesten übrigen auffüllen
            chosen += [n for n in items if not n["relevant"]][:NEWS_PER_STOCK - len(chosen)]
        return sorted(chosen, key=lambda x: x["date"], reverse=True)
    except Exception as exc:  # noqa: BLE001
        log(f"  ! News fehlgeschlagen für {symbol}: {exc}")
        return []


def num(v):
    try:
        if v is None:
            return None
        f = float(v)
        return None if f != f else f  # NaN -> None
    except (TypeError, ValueError):
        return None


def build_record(base: dict, info: dict) -> dict:
    sym = base["symbol"]
    if not info:
        info = full_info(sym)
    hist = {}
    try:
        hist = get_history(sym)
    except Exception as exc:  # noqa: BLE001
        log(f"  ! Historie fehlgeschlagen für {sym}: {exc}")
    news = get_news(sym, info.get("longName") or info.get("shortName") or base["wiki_name"])

    price = num(info.get("currentPrice")) or num(info.get("regularMarketPrice")) or hist.get("last_close")

    # Analystendaten fehlen bei manchen Xetra-Zweitlistings (z. B. Airbus) -> Heimatbörse nutzen,
    # sofern dort in derselben Währung notiert wird.
    analyst_source = sym
    analyst_keys = ("recommendationKey", "recommendationMean", "numberOfAnalystOpinions",
                    "targetMeanPrice", "targetHighPrice", "targetLowPrice")
    if base.get("alt_symbol") and (info.get("recommendationMean") is None or info.get("targetMeanPrice") is None):
        alt = full_info(base["alt_symbol"])
        if alt.get("currency") == info.get("currency") and alt.get("targetMeanPrice"):
            info = {**info, **{k: alt.get(k) for k in analyst_keys}}
            analyst_source = base["alt_symbol"]
            log(f"  {sym}: Analystendaten von {analyst_source} übernommen")

    return {
        "symbol": sym,
        "name": info.get("longName") or info.get("shortName") or base["wiki_name"],
        "country": base["country"],
        "index": base["index"],
        "sector": info.get("sector") or "Unbekannt",
        "industry": info.get("industry") or "",
        "currency": info.get("currency") or ("EUR" if base["country"] == "DE" else "USD"),
        "price": price,
        "chg_1d": num(info.get("regularMarketChangePercent")) if hist.get("chg_1d") is None else hist["chg_1d"],
        "chg_1m": hist.get("chg_1m"),
        "chg_1y": hist.get("chg_1y"),
        "market_cap": num(info.get("marketCap")),
        "pe_trailing": num(info.get("trailingPE")),
        "pe_forward": num(info.get("forwardPE")),
        "eps_trailing": num(info.get("trailingEps")),
        # yfinance >= 0.2.54 liefert dividendYield bereits in Prozent (0.45 = 0,45 %)
        "dividend_yield_pct": num(info.get("dividendYield")),
        "high_52w": num(info.get("fiftyTwoWeekHigh")),
        "low_52w": num(info.get("fiftyTwoWeekLow")),
        "recommendation_key": info.get("recommendationKey"),
        "recommendation_mean": num(info.get("recommendationMean")),
        "analyst_count": info.get("numberOfAnalystOpinions"),
        "target_mean": num(info.get("targetMeanPrice")),
        "target_high": num(info.get("targetHighPrice")),
        "target_low": num(info.get("targetLowPrice")),
        "analyst_source": analyst_source,
        "history": {"last_date": hist.get("last_date"), "weekly_close": hist.get("spark", [])},
        "news": news,
    }


def main() -> int:
    t0 = time.time()
    us, de, meta = select_universe()
    infos = meta.pop("infos")
    selection = us + de

    log(f"Lade Details, Historie und News für {len(selection)} Aktien …")
    with ThreadPoolExecutor(max_workers=3) as ex:
        records = list(ex.map(lambda b: build_record(b, infos.get(b["symbol"], {})), selection))

    # Nachladen, falls einzelne Titel durch Rate-Limits unvollständig sind
    for i, rec in enumerate(records):
        if rec["price"] is None or rec["chg_1y"] is None or len(rec["news"]) < NEWS_PER_STOCK \
                or rec["market_cap"] is None:
            log(f"  Unvollständig, lade erneut: {rec['symbol']}")
            time.sleep(3)
            records[i] = build_record(selection[i], full_info(rec["symbol"], tries=4, wait=10))

    eurusd = None
    try:
        eurusd = float(yf.Ticker("EURUSD=X").fast_info["lastPrice"])
    except Exception:  # noqa: BLE001
        pass

    payload = {
        "meta": {
            "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "generator": "scripts/fetch_data.py",
            "yfinance_version": yf.__version__,
            "eurusd": eurusd,
            "selection_rule": f"Top {N_US} S&P-500-Werte und Top {N_DE} DAX-Werte nach Marktkapitalisierung "
                              "(eine Aktiengattung je Unternehmen)",
            **meta,
        },
        "stocks": records,
    }
    OUT_FILE.parent.mkdir(parents=True, exist_ok=True)
    OUT_FILE.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    log(f"Fertig: {OUT_FILE} ({len(records)} Aktien, {time.time() - t0:.0f}s)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
