# PSD Headless – technischer Projektstand

Zuletzt aktualisiert: **2026-10-07**

## Ziel

PSD Headless ist eine eigenständige Python-Bild-Engine und ein
konfigurierbarer PSD-Template-Renderer. Das Projekt arbeitet ohne Photoshop auf
macOS, Linux und Windows. Vorlagen, Schriften und Ausgaben gehören nicht ins
Repository.

Copyright © 2026 Luca-Gene Bonzel. Die Nutzung ist nur nach der
[`Private Use Only License`](../LICENSE) gestattet.

## Architektur

```text
pshl/
  document.py          einfache Dokument- und Ebenen-API
  blend.py             Blend-Modi und Alpha-Compositing
  text.py              Pillow-Text-Rendering
  io_psd.py            einfacher PSD-Import
  template_renderer.py JSON-gesteuerter Template-Renderer
  cli.py               Kommandozeile
  api.py               FastAPI-Dienst
examples/
  template.example.json neutrale Template-Konfiguration
  job.example.json      neutraler Beispielauftrag
tests/
```

Der Template-Renderer liest zwei Dinge getrennt:

1. eine nutzereigene PSD-Datei;
2. eine JSON-Datei, die Textfeldnamen auf PSD-Ebenennamen und Artboards auf
   Ausgabedateien abbildet.

Die Ziel-Ebenen werden beim Compositing ausgelassen. Ersatztext wird mit Pillow
in deren Bounding Box gerendert. Anschließend werden die konfigurierten
Top-Level-Artboards zugeschnitten und atomar als PNG gespeichert.

## Funktioniert

- Dokumente, Bild-, Text- und Füllebenen
- 18 Blend-Modi, Deckkraft und Sichtbarkeit
- Bewegung, Weichzeichnung und Dokumentgröße
- PNG-, JPG- und TIFF-Export der einfachen Engine
- rekursive Suche nach sichtbaren Text-Platzhaltern in PSD-Gruppen
- frei konfigurierbare Feld- und Ebenennamen
- automatische Großschreibung, Längenlimit, Umbruch und Schriftanpassung
- mehrere Vorkommen eines Platzhalters
- frei konfigurierbare Artboard-Exporte
- JSON-Manifest und atomare NAS-taugliche Ausgabe
- CLI, FastAPI und Docker

## Grenzen

- kein PSD-Export
- keine semantische Bearbeitung vorhandener Photoshop-Textebenen; der
  Renderer ersetzt sie im gerasterten Ergebnis
- noch kein allgemeiner Foto-Platzhalter-Workflow
- keine externe Job-Queue für mehrere parallele Worker
- der einfache `open_psd()`-Import erhält nicht alle Photoshop-Funktionen

## Konventionen

- Code-Kommentare und Projektdokumentation auf Deutsch
- keine kunden-, marken- oder vorlagenspezifischen Namen im Repository
- keine PSDs, PSBs, Schriften oder Renderausgaben committen
- neue Template-Spezifika ausschließlich in externen JSON-Dateien ablegen
- Ausgabedateien dürfen keine Pfadanteile enthalten
- Blend-Mathematik arbeitet mit `float32` im Bereich `[0, 1]`

## Roadmap

1. Bildplatzhalter mit `cover`/`contain`, Masken und Clipping
2. Validierungsbefehl für neue Vorlagen
3. weitere Textausrichtung und typografische Optionen
4. allgemeines Job-Verzeichnis und optionale Queue
5. Einstellungsebenen und weitere Transformationsfunktionen
6. zusätzliche automatisierte Integrationsvorlagen

## Änderungsprotokoll

- **2026-10-07** – Renderer vollständig neutralisiert und auf eine generische
  JSON-Konfiguration umgestellt. Private-Use-Only-Lizenz von Luca-Gene Bonzel,
  neutrale Beispiele und sichere Ausgabepfade ergänzt.
- **2026-10-05** – Engine-Grundgerüst, Blend-Modi, Text, PSD-Lesen, CLI,
  FastAPI, Artboard-Export, Docker und Tests angelegt.
