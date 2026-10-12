# PSD Headless

[![Tests](https://github.com/Bonesisis/PSD_Headless/actions/workflows/ci.yml/badge.svg)](https://github.com/Bonesisis/PSD_Headless/actions/workflows/ci.yml)

Lokale PSD-Textverarbeitung mit Batch-CLI, Browseroberfläche und HTTP-API.
Windows, macOS und Linux · Python 3.11–3.13 · MIT · Luca-Gene Bonzel

## Was Version 1.0 kann – und was nicht

**Kein Photoshop-Ersatz und keine Garantie für pixelidentische Typografie.**
Die Engine verwendet psd-tools und Pillow, nicht die Photoshop-Rendering-Engine.

- Findet echte Textebenen auch in verschachtelten Gruppen. Bei gleichen Namen
  den vollständigen Pfad verwenden, etwa `Card/Content/Title`.
- Übernimmt einfache, einheitliche Schriftstile: Schriftname, Größe, RGB-Farbe,
  Links-/Mitte-/Rechtsausrichtung; optionales Verkleinern mit `shrink`.
- Fehlende Fonts und fehlende Glyphen sind Fehler. Keine stille Ersatzschrift.
- Punkttext wird innerhalb seiner bisherigen Ebenen-Bounds neu gesetzt.
  **Absatztext benötigt eine explizite `styles.box`**, weil Glyphen-Bounds nicht
  zuverlässig die ursprüngliche Absatz-Textbox beschreiben.
- PNG/JPG behalten Canvas-Abmessungen, DPI und vorhandenes ICC-Profil.
  JPG flacht Transparenz auf `background` ab (Standard Weiß).
- PSD behält Gruppen und nicht ersetzte Ebenen. **Ersetzte Texte werden zu
  Pixelebenen**, nicht zu weiter editierbaren Photoshop-Textebenen.
- Unterstützt RGB mit 8 Bit. Gemischte Textformatierung, verzerrter Text,
  Schriftsonderformatierung und Masken/Effekte am Textziel werden abgelehnt.
  Die Wiedergabe anderer komplexer Ebenen hängt von psd-tools ab.
- Eingebettete Smart-Object-Texte werden von `inspect` angezeigt, aber nicht
  innerhalb des Smart Objects bearbeitet. Explizite Rasterplatzhalter sind möglich.
- Keine automatische Fotomontage, keine automatische Artboard-Einzelausgabe.
  Die Ausgabe umfasst den gesamten Canvas.

Vor produktivem Einsatz jede neue Vorlage mit einem Referenzbild visuell prüfen.
Für uneingeschränkte PSD-Kompatibilität und editierbare Texte ist eine andere
Rendering-Engine erforderlich. Der Workflow ist abgesichert, nicht die vollständige
Photoshop-Kompatibilität. [Details zu psd-tools](https://psd-tools.readthedocs.io/en/latest/).

## Installation

Repository klonen und in den Projektordner wechseln. Python 3.13 empfohlen.
Vorlagen und Schriften bleiben lokal; es werden keine Kundendaten mitgeliefert.

### Windows / PowerShell

```powershell
git clone https://github.com/Bonesisis/PSD_Headless.git
cd PSD_Headless
powershell -ExecutionPolicy Bypass -File .\setup_windows.ps1
.\start_server_windows.ps1 --templates 'C:\PSD Vorlagen' --output 'C:\PSD Ergebnisse'
```

`ExecutionPolicy Bypass` gilt nur für den gestarteten Prozess, nicht systemweit.
Alternativ ohne Skripte: `py -3 -m venv .venv`, dann
`.\.venv\Scripts\python.exe -m pip install -e '.[dev]'`.

### macOS

```sh
git clone https://github.com/Bonesisis/PSD_Headless.git
cd PSD_Headless
bash setup.sh
bash start_server.sh --templates "$HOME/PSD Vorlagen" --output "$HOME/PSD Ergebnisse"
```

### Linux

```sh
git clone https://github.com/Bonesisis/PSD_Headless.git
cd PSD_Headless
bash setup.sh
bash start_server.sh --templates /srv/templates --output /srv/output
```

Auf Systemen ohne venv-Unterstützung zuerst das Python-venv-Paket des Betriebssystems
installieren. Für reine Laufzeitinstallationen genügt `python -m pip install .`.

## Kleine Bedienoberfläche

Nach dem Serverstart [http://127.0.0.1:8000](http://127.0.0.1:8000) öffnen.
Vorlagenordner und Ausgabeordner eintragen, optional eine Schriftdatei im
Vorlagenordner angeben, eine JSON/YAML/CSV-Datei laden oder JSON direkt bearbeiten.
`Nur prüfen` validiert ohne Bildausgabe; `Batch starten` verarbeitet die Aufträge.
Der Status zeigt erledigte Aufträge, Fehler und Warnungen. Der JSON-Report wird
im Ausgabeordner gespeichert und kann zusätzlich heruntergeladen werden.

Ordner werden als Pfade eingegeben, nicht im lokalen Dateiauswahldialog gewählt:
gemeint ist immer das Dateisystem **des Servers**, nicht das des Browsers.
Der Server verarbeitet einen Batch gleichzeitig. Die letzten 20 Batch-Status bleiben
bis zum Neustart verfügbar; Reports auf dem Datenträger bleiben erhalten.
Beim Schließen des Browsers läuft der Server weiter. Es gibt keine persistente
Job-Warteschlange, Pause/Fortsetzen oder automatische Wiederholung nach Serverabsturz.

## CLI und Job-Dateien

Die folgenden Beispiele verwenden `python`; nach Installation entweder die venv
aktivieren oder den vollständigen Pfad zum venv-Python verwenden.

```sh
python -m pshl inspect "assets/templates/template.psd"
python -m pshl batch --jobs examples/job.example.json --templates assets/templates --output out --dry-run
python -m pshl batch --jobs examples/job.example.json --templates assets/templates --output out --workers 2
python -m pshl render --jobs examples/job.example.json --templates assets/templates --output out
```

`render` akzeptiert genau einen Auftrag. `batch` nimmt eine Liste, `{ "jobs": [...] }`
oder einen Einzelauftrag. JSON-Beispiel:

```json
{
  "template": "template.psd",
  "output": "campaign/result.png",
  "layers": {"Card/Content/Title": "Ein neuer Anfang"},
  "fonts": {"ExactPostScriptName": "fonts/font.ttf"},
  "shrink": true,
  "styles": {"Card/Content/Title": {"box": [15, 20, 225, 70], "min_size": 10}}
}
```

Der Schriftname ist ein Platzhalter: mit `inspect` den echten PostScript-Namen
ermitteln. Ohne Mapping wird exakt nach diesem Namen in Systemfontordnern gesucht.
`font` im Job oder `--font` ist eine **bewusste Schriftüberschreibung**. Für TTC-
Sammlungen wird keine automatische Auswahl unterstützt; einzelne TTF/OTF nutzen.

`styles` ist optional für einfache Punkttexte. `box` sind Canvas-Pixelkoordinaten
`[links, oben, rechts, unten]`. Weitere explizite Overrides: `size` in Pixeln,
`color` als Hexfarbe, `align` (`left`, `center`, `right`), `spacing`, `font`, `min_size`.
Für Raster-/Smart-Object-Platzhalter sind `size`, `color`, `box` und eine Schrift
notwendig; der gesamte Ziel-Layer wird ersetzt. Keine automatische Rekonstruktion.

`format`: `png`, `jpg`, `jpeg`, `psd`; Standard ist die Dateiendung von `output`.
`background`: JPG-Hintergrundfarbe. Fehlendes ICC-Profil wird nicht erfunden;
ohne DPI-Metadaten werden 72 dpi verwendet.

YAML:

```yaml
jobs:
  - template: template.psd
    output: result.jpg
    layers:
      Title: Neuer Titel
    font: fonts/font.ttf
    shrink: true
```

CSV (Objektfelder enthalten korrekt gequotetes JSON):

```csv
template,output,layers,font,shrink
template.psd,result.png,"{""Title"":""Neuer Titel""}",fonts/font.ttf,true
```

Relative Vorlagen-/Font-Pfade beziehen sich auf `--templates`, relative Ausgaben
auf `--output`, **nicht** auf den Speicherort der Job-Datei. Absolute CLI-Pfade sind
erlaubt. API-Pfade müssen innerhalb der konfigurierten Ordner liegen.

### Zuverlässigkeit und Reports

- Bestehende Dateien werden nie überschrieben, auch keine bestehenden Reports.
- Doppelte Ziele und Ziele, die Vorlagen überschreiben würden, werden abgelehnt.
- Ein fehlerhafter Eintrag stoppt andere Jobs nicht. Eine syntaktisch kaputte
  JSON/YAML-Datei kann jedoch nicht als Batch eingelesen werden.
- Standardmäßig ein Worker; `--workers 1..32` aktiviert begrenzte Prozessparallelität.
  Jeder Worker lädt eine PSD; bei großen Dateien RAM-Bedarf beachten.
- Keine feste Jobanzahlgrenze in der CLI; Jobliste und Report liegen im RAM.
  Die Oberfläche/API begrenzt Anfragen auf 5 MiB.
- `--dry-run` prüft Fonts, Ebenen und Textlayout, rendert keinen PSD-Canvas und
  schreibt keine Bilder. Der JSON-Prüfreport wird trotzdem gespeichert.
- Report: `total`, `success`, `checked`, `errors`, `warnings`, Laufzeit und Ergebnisse
  mit nullbasiertem `index`. Optional `--report neuer-report.json`.
- Exitcodes: `0` erfolgreich, `1` mindestens ein Jobfehler, `2` Aufruf-/Dateifehler.
- Ausgabe wird erst lokal im Zielordner fertig geschrieben und dann umbenannt.
  Ein harter Prozess-/Netzausfall kann temporäre Dateien oder leere Reservierungen
  hinterlassen; solche Dateien nach Prüfung manuell entfernen. Kein automatischer Retry.

## NAS / UNC

Unter Windows werden UNC-Pfade direkt von pathlib verwendet:

```powershell
.\render_windows.ps1 --jobs '.\jobs.json' --templates '\\server\share\Vorlagen' --output '\\server\share\Ergebnisse' --workers 2
```

Im JSON müssen Backslashes escaped werden: `"\\\\server\\share\\Ergebnisse\\bild.png"`.
PowerShell-Pfade in einfache Anführungszeichen setzen. Leerzeichen und Umlaute
sind erlaubt. Der angemeldete Benutzer bzw. das Dienstkonto braucht Lese-/Schreibrechte.
Keine Passwörter in Job-Dateien. Windows-Dienste sehen häufig keine gemappten
Laufwerksbuchstaben; UNC verwenden. Vorher eine kleine Ausgabe auf dem Ziel testen.

macOS/Linux: SMB-Freigabe zuerst über das Betriebssystem mounten, dann etwa
`/Volumes/share/output` oder `/mnt/share/output` verwenden. Windows-Pfadsyntax
wird dort ausdrücklich abgelehnt. Netzwerkabbrüche sind Jobfehler; Quelldateien
werden nie verändert. Atomare Umbenennung hängt auch vom NAS/SMB-Server ab.

## API / Server

Standard: nur Loopback. `GET /health`, `GET /api/config`,
`POST /api/batches`, `GET /api/batches/{id}`, `GET /api/batches/{id}/report`.
Jeder Batch-/Report-Zugriff benötigt den Header `X-PSHL-Token`.
Lokal liefert `/api/config` einen zufälligen Sitzungstoken; die UI verwendet ihn.
Beispiel-Payload: `{ "jobs": [...], "workers": 2, "dry_run": false }`.
Alternativ `content` und `extension` für eine komplette Job-Datei.

Netzwerkbetrieb erfordert `PSHL_API_KEY` (zufälliges Secret), explizite
`PSHL_ALLOWED_HOSTS`, TLS-Reverse-Proxy und Zugriffskontrolle. Bei gesetztem API-Key
liefert `/api/config` **keinen** Token; die UI fragt ihn ab und Root-Ordner sind gesperrt.
Keine CORS-Freigabe. Diese Anwendung ist für vertrauenswürdige Bediener und
vertrauenswürdige PSDs gedacht, **kein sicherer öffentlicher Upload-Dienst**.
Das Parsen von untrusted PSDs gehört in separat isolierte Systeme mit Ressourcenlimits.

`.env.example` nach `.env` kopieren und lokal anpassen. CLI-Flags haben Vorrang;
die `.env` wird nur aus dem aktuellen Arbeitsordner gelesen. Nicht committen.

## Docker / Docker Desktop

Linux-Container, Python 3.13, nicht privilegierter Benutzer (UID 10001).
Auf Linux Ausgabeordner für diese UID schreibbar machen. Vorlagen nur lesbar mounten.
Mit einem zufälligen `PSHL_API_KEY` und den Ordnern in `.env`:

```sh
docker compose up --build -d
docker compose logs -f
```

Oder veröffentlichtes Image herunterladen: `docker pull ghcr.io/bonesisis/psd_headless:1.0.0`.
Die Compose-Datei bindet Port 8000 nur an localhost. Die Schriften gehören in den
Vorlagenordner, nicht ins Image. Im Browser sind die Pfade `/templates` und `/output`.

Windows: Docker Desktop auf **Linux containers / WSL 2** stellen. Für `.env`
lokale Hostpfade wie `PSHL_TEMPLATE_DIR=C:/PSD Vorlagen` verwenden und den Ordner
für Docker freigeben. NAS-Freigaben sind nicht automatisch im Linux-Container
verfügbar: einen für Docker zugänglichen Mount einrichten oder native PowerShell
mit UNC verwenden. Direkte UNC-Bind-Mounts werden nicht pauschal zugesichert.

**Validierung:** Linux-Docker-Build und Smoke-Test laufen in CI. Ein echter
Docker-Desktop-Test auf einem Windows-Host steht noch aus. Dort prüfen:
`docker compose config`, `docker compose up --build`, Health-Endpunkt, Dry-Run,
einen Renderjob, Ausgabe-/Reportrechte sowie NAS-Verbindung und Wiederanlauf.
Windows-native Tests ersetzen diesen Host-Test nicht.

## Tests und Entwicklung

```sh
python -m pip install -e '.[dev]'
python -m pytest -q
python -m ruff check .
python -m ruff format --check .
python -m build
```

CI prüft Ubuntu, Windows und macOS mit Python 3.11 und 3.13. Tests erzeugen kleine
PSDs mit echten Type-Layer-Metadaten und eine eigene synthetische Testschrift.
Keine Originalvorlagen, Markenbilder oder lizenzierte Fonts in Tests oder Releases.
Jeweils 110 erfolgreiche Jobs plus Fehlerfälle werden seriell und parallel geprüft.
Ein Windows-Test verwendet eine temporäre lokale SMB-Freigabe für UNC.
Arbeitsregeln für Coding-Agenten stehen in [AGENTS.md](AGENTS.md).

## Fehlerbehebung

| Meldung | Abhilfe |
| --- | --- |
| Schrift fehlt | Exakte Schrift installieren oder bewusste `fonts`-Zuordnung/`font` angeben. |
| 0 / mehrere Ebenentreffer | `inspect` ausführen, vollständigen Gruppenpfad übernehmen. |
| Absatztext benötigt Box | Original-Textbox in Canvas-Koordinaten als `styles.box` angeben. |
| Text passt nicht | Text kürzen, Box bewusst erweitern oder `shrink: true` setzen. |
| Ausgabe existiert | Neuen Dateinamen verwenden; keine automatische Überschreibung. |
| Zugriff verweigert | Rechte des ausführenden Kontos auf NAS/Ordner prüfen. |
| 401 / 403 / ungültiger Host | Token, Origin und PSHL_ALLOWED_HOSTS prüfen; keine Wildcards. |
| Abweichende Grafik | Vorlagenkomplexität prüfen; kein vollwertiger Photoshop-Renderer. |

## Veröffentlichung und Lizenz

[Quellcode](https://github.com/Bonesisis/PSD_Headless) ·
[Releases](https://github.com/Bonesisis/PSD_Headless/releases) ·
[Dokumentation](https://bonesisis.github.io/PSD_Headless/)

CI muss vor einem Release grün sein. Der Tag `v1.0.0` veröffentlicht das GHCR-Image;
GitHub Pages wird aus der README auf `main` gebaut. Der erste GHCR-Push kann ein
privates Paket erzeugen; Paket-Sichtbarkeit separat auf öffentlich setzen.

MIT-Lizenz, Copyright © 2026 Luca-Gene Bonzel. Im Gegensatz zur früheren
Privatnutzungslizenz erlaubt MIT auch berufliche und kommerzielle Nutzung.
Schriften und Vorlagen brauchen ihre eigenen Nutzungsrechte.
