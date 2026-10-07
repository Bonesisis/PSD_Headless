# PSD Headless

Ein konfigurierbarer Python-Renderer für PSD-Vorlagen – ohne Photoshop und ohne
grafische Benutzeroberfläche. Benannte Text-Platzhalter werden ersetzt,
Artboards als PNG exportiert und Ergebnisse optional direkt in ein gemountetes
NAS-Verzeichnis geschrieben.

Entwickelt von **Luca-Gene Bonzel**.

> **Lizenz:** Nur für private, nicht berufliche und nicht kommerzielle Nutzung.
> Nutzung für Arbeitgeber, Kunden, Organisationen, Produktionssysteme oder zum
> Geldverdienen ist nicht erlaubt. Private Änderungen und Erweiterungen sind
> erlaubt. Details stehen in der [Private Use Only License](LICENSE).

## Was das Projekt kann

- PSD- und PSB-Inhalte mit `psd-tools` zusammensetzen
- sichtbare Text-Platzhalter anhand frei wählbarer Ebenennamen ersetzen
- Text automatisch umbrechen und in den vorhandenen Bereich einpassen
- mehrere Vorkommen desselben Platzhalters gemeinsam aktualisieren
- frei benannte Artboards als einzelne PNG-Dateien exportieren
- Aufträge per CLI oder HTTP-API annehmen
- Ergebnisse atomar in lokale Ordner oder NAS-Mounts schreiben
- als Python-Anwendung oder Docker-Container laufen

Die PSD, verwendete Schriften und Renderausgaben bleiben außerhalb des
Repositories. Das Repository enthält nur neutrale Beispielkonfigurationen.

## Voraussetzungen

- Python 3.11 oder neuer
- eine eigene PSD-Vorlage mit benannten Platzhalterebenen und Artboards
- eine TrueType- oder OpenType-Schrift
- optional ein per SMB oder NFS gemountetes NAS-Verzeichnis

## Installation

```bash
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
```

## Vorlage vorbereiten

Die Beispielkonfiguration erwartet:

- eine sichtbare Ebene `TEXT_HEADLINE`
- eine sichtbare Ebene `TEXT_SUBTITLE`
- die Artboards `Square` und `Landscape`

Die Platzhalter können Pixel-, Text- oder Smart-Object-Ebenen sein. Ihre
Position und Größe bestimmen den Bereich für den neuen Text. Gleiche sichtbare
Ebenennamen dürfen in mehreren Artboards vorkommen.

Kopiere [`examples/template.example.json`](examples/template.example.json) und
passe Ebenennamen, Farben, maximale Zeilenanzahl und Ausgabedateien an:

```json
{
  "text_fields": {
    "headline": {
      "layer_name": "TEXT_HEADLINE",
      "color": "#ffffff",
      "max_lines": 2,
      "uppercase": true,
      "max_length": 120
    }
  },
  "artboards": {
    "Square": "square.png"
  }
}
```

## Einen Auftrag rendern

`--texts` akzeptiert direktes JSON oder den Pfad zu einer JSON-Datei:

```bash
.venv/bin/python -m pshl render \
  --template "/path/to/template.psd" \
  --config "examples/template.example.json" \
  --font "/path/to/BrandFont-Bold.ttf" \
  --output "/mnt/nas/psd-output" \
  --job-id "example-1234" \
  --texts "examples/job.example.json"
```

Das erzeugt beispielsweise:

```text
/mnt/nas/psd-output/example-1234/
  square.png
  landscape.png
  manifest.json
```

## HTTP-Server

```bash
.venv/bin/python -m pshl serve \
  --template "/path/to/template.psd" \
  --config "/path/to/template.json" \
  --font "/path/to/BrandFont-Bold.ttf" \
  --output "/mnt/nas/psd-output" \
  --host 0.0.0.0 \
  --port 8000
```

Auftrag senden:

```bash
curl -X POST http://server:8000/render \
  -H 'Content-Type: application/json' \
  -d '{
    "job_id": "example-1234",
    "texts": {
      "headline": "A new headline",
      "subtitle": "A longer subtitle for the second placeholder"
    }
  }'
```

Unter `http://server:8000/docs` steht eine interaktive API-Oberfläche bereit.
Pro Prozess wird absichtlich nur ein Renderauftrag gleichzeitig ausgeführt,
weil große PSD-Dateien viel Arbeitsspeicher benötigen.

## Docker

Lege deine Dateien lokal so ab:

```text
assets/templates/template.psd
assets/fonts/font.ttf
assets/config/template.json
```

Diese Ordner werden von Git ignoriert. Danach:

```bash
docker compose up --build
```

Ein NAS-Pfad kann als Ausgabe gemountet werden:

```bash
PSHL_OUTPUT_DIR=/mnt/nas/psd-output docker compose up --build
```

## Grenzen

- Die Quelldatei wird nicht als bearbeitete PSD zurückgeschrieben.
- Ersetzt werden konfigurierte Textbereiche; ein generischer Bildplatzhalter-
  Workflow ist noch nicht enthalten.
- Komplexe Photoshop-Effekte können von `psd-tools` anders gerendert werden.

## Lizenz

Copyright © 2026 Luca-Gene Bonzel.

Dieses Projekt ist **source available**, aber ausdrücklich **keine
Open-Source-Software**. Es darf privat angesehen, ausprobiert, verändert und
erweitert werden. Berufliche, institutionelle, organisatorische und
kommerzielle Nutzung sowie jede Form der Monetarisierung sind untersagt. Siehe
[`LICENSE`](LICENSE) für die verbindlichen Bedingungen.
