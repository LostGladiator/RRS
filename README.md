# RSS Release Feed

Dieses Repository enthaelt ein lauffaehiges Grundgeruest fuer einen eigenen RSS-Feed, der GitHub-Releases beobachtet, filtert und in ein einheitliches deutsches Nachrichtenformat bringt.

## Zielbild

1. Ein Scheduler startet den Feed-Generator einmal pro Tag.
2. Der Generator liest `config/projects.toml`.
3. Pro Projekt werden offizielle GitHub-Releases ueber die GitHub API gelesen.
4. Drafts und Pre-Releases werden standardmaessig ausgeschlossen.
5. Relevante Releases werden nach Projektdefinition gefiltert.
6. Der Text wird mit einem standardisierten Template gekuerzt und formuliert.
7. Das Ergebnis wird als RSS-Datei nach `public/feed.xml` geschrieben.
8. GitHub Pages veroeffentlicht `public/` unter `https://lostgladiator.github.io/RRS/`.

Aktuell ist die AI-Stufe bewusst als definierte Schnittstelle vorbereitet. Der Generator enthaelt einen lokalen Fallback, damit der Feed ohne API-Key sofort funktioniert. Sobald Provider, Modell und Secrets feststehen, kann ein externes AI-Kommando eingebunden werden.

## Dateien

- `config/projects.toml`: Feed-Einstellungen und Projektdefinitionen.
- `scripts/github_release_rss.py`: Generator fuer GitHub-Releases nach RSS 2.0.
- `prompts/release-summary.md`: Prompt-Vorlage fuer die spaetere AI-Kuerzung.
- `templates/rss-item.md`: Standardformat fuer Feed-Nachrichten.
- `automation/rss-feed.cron`: Beispiel fuer einen taeglichen Cronjob.
- `automation/github-actions.example.yml`: Beispiel fuer GitHub Actions.
- `.github/workflows/publish-feed.yml`: produktiver GitHub Actions Workflow fuer GitHub Pages.
- `examples/neuvector-release.sample.json`: Lokale Beispieldaten zum Testen.
- `public/feed.xml`: Generierte RSS-Datei.
- `public/index.html`: Kleine Startseite mit Feed-Link.

## Online Betreiben

Die Ziel-URL fuer den RSS-Feed ist:

```text
https://lostgladiator.github.io/RRS/feed.xml
```

Einmalige Einrichtung in GitHub:

1. Repository zu GitHub pushen.
2. In GitHub `Settings` -> `Pages` oeffnen.
3. Bei `Build and deployment` als Source `GitHub Actions` auswaehlen.
4. Unter `Actions` den Workflow `Publish RSS feed` einmal manuell starten.
5. Danach die Feed-URL im RSS-Reader am Handy abonnieren.

Der Workflow laeuft danach taeglich um `04:15 UTC`. Das entspricht waehrend der Sommerzeit `06:15 Europe/Rome`; im Winter ist es `05:15 Europe/Rome`.

Lokaler Push:

```bash
git push origin main
```

Falls GitHub bei HTTPS nach Login fragt und der Push lokal nicht interaktiv funktioniert:

```bash
gh auth login
git push origin main
```

Alternativ kann der Remote auf SSH umgestellt werden, wenn ein GitHub-SSH-Key eingerichtet ist:

```bash
git remote set-url origin git@github.com:LostGladiator/RRS.git
git push origin main
```

## Lokal Ausfuehren

Voraussetzung ist Python 3.11 oder neuer, weil die Konfiguration mit `tomllib` aus der Standardbibliothek gelesen wird.

```bash
python3 scripts/github_release_rss.py --config config/projects.toml
```

Mit den lokalen Beispieldaten:

```bash
python3 scripts/github_release_rss.py --config config/projects.toml --fixture examples/neuvector-release.sample.json --ignore-lookback
```

Nur anzeigen, was verarbeitet wuerde:

```bash
python3 scripts/github_release_rss.py --config config/projects.toml --dry-run
```

Optional kann ein GitHub Token gesetzt werden, damit die API-Rate-Limits hoeher sind:

```bash
export GITHUB_TOKEN=...
```

## Projektdefinition

Jedes Projekt in `config/projects.toml` definiert:

- `owner` und `repo`: GitHub Repository.
- `include_prereleases`: ob GitHub Pre-Releases erlaubt sind.
- `include_drafts`: ob Draft-Releases erlaubt sind.
- `lookback_days`: wie weit Releases fuer den Feed aufgenommen werden. Fuer RSS sollte dieser Wert deutlich groesser sein als der taegliche Scheduler-Intervall, weil der Feed sonst an releasefreien Tagen leer werden kann.
- `include_keywords`: falls gesetzt, muss mindestens ein Begriff vorkommen.
- `exclude_keywords`: Begriffe, die ein Release ausschliessen.
- `interests`: fachliche Themen, die in der Zusammenfassung beruecksichtigt werden sollen.
- `severity_keywords`: Begriffe, die als besonders relevant markiert werden.

Fuer SUSE NeuVector sind initial diese Quellen konfiguriert:

- `neuvector/neuvector`: Core-Repository mit offiziellen Releases.
- `neuvector/neuvector-helm`: Helm-Charts fuer Kubernetes/Rancher/OpenShift Deployments.

SUSE nennt ausserdem `neuvector/manager` und `neuvector/docs` als Projektbestandteile. Diese sind noch nicht aktiv geschaltet, weil fuer den RSS-Feed zuerst geklaert werden sollte, ob dort Releases oder Dokumentationsaenderungen als Feed-Nachrichten gewuenscht sind.

## Was Noch Definiert Werden Muss

1. AI-Provider: OpenAI, Azure OpenAI, lokales Modell oder ein bestehender interner Service.
2. AI-Geheimnisse: API-Key, Endpoint, Modellname und Kosten-/Rate-Limit-Vorgaben.
3. Freigabeprozess: vollautomatisch publizieren oder vor dem Schreiben menschlich pruefen.
4. Inhaltliche Filter: welche Release-Arten sind wichtig, z. B. Security Fixes, CVEs, Helm-Chart-Aenderungen, Breaking Changes, Upgrade-Hinweise.
5. Sprache und Ton: aktuell Deutsch, sachlich und technisch kompakt.
6. Feed-Retention: aktuell werden maximal 30 Items geschrieben. RSS-Reader deduplizieren ueber die stabile GitHub Release URL als GUID.

## Quellen Fuer Die Initiale NeuVector-Auswahl

- SUSE beschreibt NeuVector als Open-Source Container-Security-Plattform und verweist auf die GitHub-Repositories `neuvector/neuvector`, `neuvector/manager` und `neuvector/docs`.
- GitHub fuehrt offizielle Releases fuer `neuvector/neuvector`.
- Das Repository `neuvector/neuvector-helm` enthaelt Helm-Charts fuer NeuVector und hat eigene Releases.
