# Code-Übersicht

## Struktur

```
BlueChip_Dashboard/
├── dashboard.html               ← Ergebnis: eigenständiges Dashboard (Daten eingebettet)
├── run_all.py                   ← startet die komplette Pipeline
├── requirements.txt
├── README.md                    ← Nutzungsanleitung
├── CODEBASE.md                  ← diese Datei
├── scripts/
│   ├── fetch_data.py            ← 1. Daten laden
│   ├── compute_scores.py        ← 2. Kauf-Score berechnen
│   ├── build_dashboard.py       ← 3. HTML erzeugen
│   └── validate.py              ← 4. Prüfen
├── templates/
│   └── dashboard_template.html  ← HTML/CSS/JS-Vorlage mit Platzhalter /*__DATA__*/
└── data/
    ├── raw_data.json            ← Rohdaten (unverändert von yfinance, plus Auswahl-Ranglisten)
    ├── scored_data.json         ← Rohdaten + Score + Stimmung je Schlagzeile
    ├── constituents_cache.json  ← letzte Indexmitgliederliste (Fallback)
    └── fetch_log.txt            ← Protokoll des letzten Ladevorgangs (falls über run_all umgeleitet)
```

## Datenfluss

```
Wikipedia (S&P 500, DAX)  ─┐
Yahoo-Screener / yfinance ─┼─► fetch_data.py ─► raw_data.json ─► compute_scores.py ─► scored_data.json
                           ┘                                                              │
                         templates/dashboard_template.html ─► build_dashboard.py ◄────────┘
                                                                     │
                                                                     ▼
                                                              dashboard.html ◄── validate.py prüft alles
```

Rohdaten, Berechnung und Darstellung sind getrennt: Das Dashboard rechnet nichts selbst aus, es zeigt nur `scored_data.json` an.

## Die Skripte

### `scripts/fetch_data.py`
- **Universum:** `load_constituents()` liest die S&P-500- und DAX-Tabellen von Wikipedia (mit Cache-Fallback). DAX-Ticker werden auf Xetra gemappt (`xetra_symbol`, z. B. `AIR.PA` → `AIR.DE`).
- **Ranking:** Für die USA lädt `us_screener_caps()` per Yahoo-Screener alle US-Werte über 50 Mrd. USD in einer Anfrage. Ohne Screener greift der Fallback `fast_caps()` mit Einzelabfragen. Die besten 45 Kandidaten und alle 40 DAX-Werte erhalten über `fetch_infos()` die genaue Unternehmens-Marktkapitalisierung, bei Rate-Limits mit gedrosselten Wiederholungsrunden. `rank()` sortiert und lässt pro Unternehmen nur eine Aktiengattung zu (Abgleich über die SEC-CIK).
- **Details:** `build_record()` sammelt pro Aktie Kennzahlen aus `.info`, die Historie von 13 Monaten (`get_history()`, berechnet 1T/1M/1J und Wochenschlusskurse) und die News (`get_news()`). Fehlen Analystendaten, wird die Heimatbörse abgefragt (Feld `analyst_source`).
- **News-Relevanz:** `get_news()` holt bis zu 20 Artikel und bevorzugt die neuesten, die das Unternehmen per Alias nennen (`NEWS_ALIASES`, `mentions()`). Notfalls wird aufgefüllt, solche Artikel tragen `relevant: false`.
- **Robustheit:** `retry()` mit wachsender Pause. Unvollständige Datensätze werden einzeln nachgeladen. Bei weniger als 30/20 Titeln bricht das Skript ab, statt ein falsches Dashboard zu erzeugen.

### `scripts/compute_scores.py`
- `compute()` berechnet die vier Teilscores (Formeln im Modul-Docstring und in der README) und legt pro Aktie `score.detail` mit allen Zwischenwerten an: Perzentile, Branchen-KGV, Upside, Stimmungszählung. Diese Werte zeigt das Dashboard als Rechenweg.
- `sentiment()` ist das Wortlisten-Verfahren: `POSITIVE`/`NEGATIVE` (Englisch und Deutsch), Verneinung bis 3 Wörter zurück (begrenzt auf den Teilsatz), Sonderregeln für „despite/trotz“ und „raises questions/risks“, Frage-Schlagzeilen zählen halb.
- `percentiles()` vergibt Ränge mit Durchschnittsrang bei Gleichstand.

### `scripts/build_dashboard.py`
Ersetzt `/*__DATA__*/` in der Vorlage durch das JSON aus `scored_data.json` (mit `</` escaped) und schreibt `dashboard.html`.

### `scripts/validate.py`
Unabhängige Prüfung, Exit-Code 1 bei Fehlern:
- **A Auswahl:** 30 US / 20 DE, keine Duplikate, Auswahl entspricht den Top-N der gespeicherten Rangliste, Abstand zum ersten Nicht-Ausgewählten.
- **B Vollständigkeit:** Pflichtfelder, je 3 Schlagzeilen mit Link und Datum, Kurshistorie, Anteil der Schlagzeilen mit direktem Bezug.
- **C Plausibilität:** Kurs in der 52W-Spanne, Kursziel-Abstand, Dividende, KGV, Währung, Aktualität.
- **D Scores:** Zweitimplementierung der Formeln mit anderer Perzentil-Methode, Vergleich auf ±0,15 Punkte, Summen- und Rangprüfung.
- **E Dashboard:** eingebettete Daten = `scored_data.json`, Hinweistext vorhanden, keine externen Ressourcen.
- **F Stimmung:** feste Testfälle für bekannte Problemmuster (Verneinung, „despite“ usw.).

### `templates/dashboard_template.html`
Vanilla HTML/CSS/JS ohne Bibliotheken, damit es offline läuft.
- **Design-Tokens** als CSS-Variablen in `:root` (dunkles Theme, Farben für Score-Teile und Branchen). Branchenfarben werden nach Größe vergeben: die 7 größten Branchen bekommen feste Farben, der Rest landet unter „Sonstige“.
- **Wichtige Funktionen:** `renderKPIs()`, `renderTable()` (Spalten deklarativ in `COLS`, Sortierung und Filter über `state`), `renderScatter()` (eigenes SVG, log-Achse, Tooltip, Label-Kollisionsprüfung), `renderLegend()`, `openDetail()` (Dialog mit Score-Balken, Kennzahlen, Sparkline, News).
- **Responsiv:** Die Klassen `hide-md` (unter 1000 px) und `hide-sm` (unter 640 px) blenden Tabellenspalten aus. Auf dem Handy wird der Dialog zum Vollbild.
- Externe Texte (Schlagzeilen, Namen) werden über `esc()` HTML-escaped, Links öffnen mit `rel="noopener noreferrer"`.

## Typische Anpassungen

| Ziel | Stelle |
|---|---|
| Anzahl Aktien ändern | `N_US`, `N_DE` in `fetch_data.py` (und die Prüfwerte in `validate.py`) |
| Score-Gewichtung oder -Formeln | `compute()` in `compute_scores.py` und die Zweitimplementierung `recompute()` in `validate.py` |
| Stimmungs-Wortlisten | `POSITIVE`, `NEGATIVE`, `NEGATORS` in `compute_scores.py`, neue Fälle in `SENTIMENT_CASES` (`validate.py`) |
| News-Zuordnung verbessern | `NEWS_ALIASES` / `NEWS_EXCLUDE` in `fetch_data.py` |
| Design, Texte, Spalten | `templates/dashboard_template.html`, danach `python run_all.py --skip-fetch` |
