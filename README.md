# Blue-Chip Dashboard – Nutzungsanleitung

> **Demo – keine Anlageberatung.** Der Kauf-Score ist ein vereinfachtes Rechenmodell und keine Kauf- oder Verkaufsempfehlung.

Interaktives Dashboard für 50 Blue-Chip-Aktien: die **30 größten S&P-500-Werte** und die **20 größten DAX-Werte** nach Marktkapitalisierung. Jede Aktie bekommt einen transparenten **Kauf-Score von 0 bis 100**.

## Dashboard öffnen

Doppelklick auf **`dashboard.html`**. Die Datei funktioniert ohne Server und ohne Internet, alle Daten sind eingebettet. Nur die Nachrichten-Links brauchen eine Internetverbindung.

## Daten aktualisieren

Einmalig die Abhängigkeiten installieren (Python 3.10 oder neuer):

```bash
pip install -r requirements.txt
```

Danach jederzeit die komplette Pipeline starten (dauert etwa 20 bis 60 Sekunden):

```bash
python run_all.py
```

Das Skript lädt die aktuellen Indexmitglieder und Kurse, berechnet die Scores, erzeugt `dashboard.html` neu und prüft das Ergebnis. Am Ende steht `Ergebnis: 0 Fehler`, wenn alles stimmt.

Nur neu rechnen und bauen, ohne neue Daten zu laden (z. B. nach Änderungen an Score oder Design):

```bash
python run_all.py --skip-fetch
```

Die Schritte lassen sich auch einzeln ausführen, z. B. `python scripts/validate.py`.

## Bedienung

| Bereich | Was es kann |
|---|---|
| **KPI-Kacheln** | Ø-Score (inkl. Median, US/DE-Schnitt), Top 3, Flop 3, Anteil US/DE nach Anzahl und Marktkapitalisierung. Ein Klick auf einen Namen öffnet die Details. |
| **Tabelle** | Klick auf eine Spaltenüberschrift sortiert, ein zweiter Klick dreht die Richtung um. Filtern geht über Suche, Land (Alle/US/DE), Branche und Mindest-Score. Der farbige Balken zeigt die vier Score-Teile. |
| **Details** | Klick oder Enter auf eine Zeile öffnet sie: Score-Aufschlüsselung mit Rechenweg, Kennzahlen, Lage in der 52-Wochen-Spanne, 12-Monats-Kursverlauf und die 3 neuesten Schlagzeilen mit Stimmung und Signalwörtern. Schließen mit Esc, × oder Klick daneben. |
| **Scatter-Plot** | Score (y) gegen KGV (x, logarithmisch), eingefärbt nach Branche. Hover zeigt Details, ein Klick öffnet die Aktie, ein Klick auf einen Legendeneintrag hebt die Branche hervor. Die Tabellenfilter dimmen ausgeblendete Punkte. |
| **Handy** | Die Tabelle zeigt dort nur Name, Land und Score, alle anderen Werte stehen in der Detailansicht (Tippen auf die Zeile). |

## So wird der Kauf-Score berechnet

Vier gleich gewichtete Teile mit je 25 Punkten:

| Teil | Berechnung |
|---|---|
| **Momentum** | 12,5 × Perzentil-Rang der 1-Monats-Rendite + 12,5 × Perzentil-Rang der 1-Jahres-Rendite, jeweils im Vergleich zu allen 50 Aktien (beste = 12,5, schlechteste = 0). |
| **Bewertung** | 25 × clamp(1,5 − KGV / Branchen-KGV). Das Branchen-KGV ist der **Median** der KGVs derselben Branche in der Auswahl, damit Ausreißer wie Teslas KGV über 400 nicht verzerren. Halb so teuer wie die Branche ergibt 25 Punkte, gleich teuer 12,5, ab dem 1,5-Fachen 0. Branchen mit weniger als 3 KGV-Werten werden mit dem Median aller Aktien verglichen. Verlust (EPS ≤ 0) ergibt 0 Punkte. |
| **Analysten** | Konsens: 12,5 × clamp((3 − Empfehlungsmittel) / 2), d. h. 1 = Starker Kauf ergibt 12,5, 2 = Kaufen 6,25, ab 3 = Halten 0. Kursziel: 12,5 × clamp((Upside + 5 %) / 35 %), d. h. −5 % ergibt 0, +30 % oder mehr 12,5. |
| **Nachrichten** | Jede der 3 neuesten Schlagzeilen wird per Wortliste als positiv (+1), neutral (0) oder negativ (−1) eingestuft. Punkte = 12,5 × (1 + Durchschnitt). |

Fehlende Werte werden neutral (halbe Punkte) gewertet und in der Detailansicht als Hinweis angezeigt.

## Grenzen (bitte beachten)

- **Datenquelle:** Yahoo Finance über yfinance, inoffiziell. Werte können verzögert, lückenhaft oder fehlerhaft sein.
- **Branchen:** Die Einteilung stammt von Yahoo und ist grob. Zum Beispiel stehen Visa/Mastercard mit Banken in „Finanzen“, deshalb wirken Zahlungsnetzwerke dort teuer.
- **Stimmungsanalyse:** einfaches Wortlisten-Verfahren (Englisch und Deutsch), das Ironie und Kontext nicht erkennt. Die erkannten Signalwörter stehen in der Detailansicht, damit jede Einstufung nachvollziehbar bleibt.
- **Auswahl:** Mehrere Aktiengattungen eines Unternehmens (z. B. GOOGL/GOOG) zählen nur einmal. Knappe Grenzfälle werden beim Prüfen ausgegeben, z. B. Platz 20 vs. 21 im DAX.
- **Airbus:** Die Xetra-Notierung (AIR.DE) hat bei Yahoo keine Analystendaten. Sie werden automatisch von der Heimatbörse Paris (AIR.PA, ebenfalls EUR) übernommen.

## Häufige Probleme

| Meldung | Lösung |
|---|---|
| `Too Many Requests` / Rate-Limit | Das Skript wiederholt automatisch. Bleibt es dabei, ein paar Minuten warten und neu starten. |
| `Abbruch: nur … Titel ermittelt` | Yahoo war nicht erreichbar oder hat gedrosselt. Später erneut starten, das alte Dashboard bleibt unverändert. |
| Wikipedia nicht erreichbar | Das Skript nutzt automatisch die zuletzt gespeicherte Mitgliederliste (`data/constituents_cache.json`). |
| `validate.py` meldet Fehler | Die Meldung nennt Aktie und Feld. Meist hilft ein erneuter Lauf von `python run_all.py`. |
