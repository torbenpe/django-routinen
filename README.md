# django-routinen

Django-App mit einer Kachel für wiederkehrende Aufgaben ohne festen Termin (Pflanzen gießen, Filter reinigen …).
Gleitendes Intervall: Die nächste Fälligkeit zählt immer ab der letzten Erledigung.

Das Paket enthält nur Code. Die Routinen selbst liegen auf der jeweiligen Maschine in `routinen.json`
und werden nirgends abgeglichen.

Voraussetzungen im Projekt: Django ≥ 5.2, Python ≥ 3.12, [HTMX](https://htmx.org) 2, CSRF-Middleware.
Keine Datenbank, keine Migrationen, keine Inline-Skripte oder -Styles (läuft mit strikter CSP).

## Einbauen

```toml
# pyproject.toml – Version festnageln; das Archiv braucht kein git im Docker-Image
dependencies = ["django-routinen @ https://github.com/torbenpe/django-routinen/archive/refs/tags/v0.1.0.tar.gz"]
```

```python
# settings.py
INSTALLED_APPS = [..., "django.contrib.staticfiles", "routinen"]
DATA_DIR = ...                 # Ordner, in dem routinen.json liegt – nicht im Repo

# urls.py
path("tiles/routinen/", include("routinen.urls"))
```

```django
{% load static routinen %}
<script src="{% static 'routinen/collapse.js' %}"></script>
<link rel="stylesheet" href="{% static 'routinen/routinen.css' %}">
…
{% routinen_tile %}
```

`collapse.js` blockierend im `<head>` laden, damit die eingeklappte Kachel nicht aufblitzt. Hat das Projekt schon
ein Skript für `data-collapse`, dieses weglassen (sonst schaltet jeder Klick zweimal).

| Einstellung | Standard | |
|---|---|---|
| `ROUTINEN_FILE` | `DATA_DIR/routinen.json` | Pfad der Datei; `ROUTINEN_FILE` oder `DATA_DIR` muss gesetzt sein |
| `ROUTINEN_POLL_SECONDS` | `600` | Abstand, in dem sich die Kachel neu lädt |
| `ROUTINEN_HOME_URL` | `"/"` | Ziel nach dem Absenden ohne HTMX (Pfad oder URL-Name) |

Die Datei wird unter einem Thread-Lock geschrieben: gunicorn mit **einem** Worker (plus Threads) betreiben.
Die Kachel hat keinen Zugriffsschutz; den muss das Projekt mitbringen.

## `routinen.json`

```json
{"routinen": [{"id": "pflanzen", "name": "Pflanzen gießen", "intervall": 4, "toleranz": 1, "zuletzt": "2026-11-17"}]}
```

- Auch von Hand bearbeitbar. `id` darf fehlen (wird aus dem Namen gebildet), `toleranz` auch (0);
  `zuletzt: null` = noch nie erledigt = fällig. Unbekannte Felder bleiben erhalten.
- Gelb ab `intervall` Tagen nach `zuletzt`, rot nach weiteren `toleranz` Tagen.
- Ist die Datei fehlerhaft, zeigt die Kachel den Fehler und schreibt nichts. Fehlt sie: leere Liste.

## Entwickeln

```sh
uv run python -m django test tests --settings tests.settings --pythonpath .
```

Neue Version: `version` in `pyproject.toml` erhöhen, committen, `git tag vX.Y.Z`, pushen.
In den Projekten den Tag in der URL ändern, `uv lock`, neu bauen.
