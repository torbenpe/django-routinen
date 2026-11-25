"""Routinen mit gleitendem Intervall: Kachel und Speicher.

Alles steht in einer Datei `routinen.json` (Ort: siehe `store_path`), die man auch von Hand bearbeiten kann:

    {"routinen": [{"id": "pflanzen", "name": "Pflanzen gießen", "intervall": 4, "toleranz": 1, "zuletzt": "2026-11-17"}]}

Gleitend heißt: Die nächste Fälligkeit zählt immer ab der letzten Erledigung (zuletzt + intervall).
Ab dann gelb, nach weiteren `toleranz` Tagen rot. `id` darf von Hand fehlen (wird aus dem Namen gebildet),
`toleranz` ebenso (0), `zuletzt` darf null sein (noch nie erledigt = fällig).
"""

import json
import os
import re
import threading
from datetime import date, timedelta
from pathlib import Path

from django.conf import settings
from django.core.exceptions import ImproperlyConfigured
from django.utils import timezone

FILE_NAME = "routinen.json"
NAME_MAX = 80
DAYS_MAX = 3650
NEW_DEFAULTS = {"intervall": 7, "toleranz": 2}
POLL_SECONDS = 600
WEEKDAYS = ("Mo", "Di", "Mi", "Do", "Fr", "Sa", "So")
MONTHS = ("Jan", "Feb", "März", "Apr", "Mai", "Juni", "Juli", "Aug", "Sept", "Okt", "Nov", "Dez")

_lock = threading.Lock()  # gunicorn läuft mit Threads – Lesen, Ändern, Schreiben nie gleichzeitig


class StoreError(Exception):
    """routinen.json ist nicht lesbar. Dann wird auch nicht geschrieben, damit Handänderungen erhalten bleiben."""


def store_path():
    """`ROUTINEN_FILE`, sonst `DATA_DIR/routinen.json`. Bewusst kein Standardpfad im Paket."""
    path = getattr(settings, "ROUTINEN_FILE", None)
    if path:
        return Path(path)
    data_dir = getattr(settings, "DATA_DIR", None)
    if not data_dir:
        raise ImproperlyConfigured("ROUTINEN_FILE oder DATA_DIR muss gesetzt sein")
    return Path(data_dir) / FILE_NAME


def date_label(day):
    return f"{WEEKDAYS[day.weekday()]}, {day.day}. {MONTHS[day.month - 1]}"


def slug(name):
    text = name.lower()
    for umlaut, ascii_ in (("ä", "ae"), ("ö", "oe"), ("ü", "ue"), ("ß", "ss")):
        text = text.replace(umlaut, ascii_)
    return re.sub(r"[^a-z0-9]+", "-", text).strip("-") or "routine"


def unique_id(name, taken):
    base = candidate = slug(name)
    n = 2
    while candidate in taken:
        candidate, n = f"{base}-{n}", n + 1
    return candidate


def _check(entry, index):
    if not isinstance(entry, dict):
        raise StoreError(f"{FILE_NAME}: Eintrag {index + 1} ist kein Objekt")
    name = entry.get("name")
    if not isinstance(name, str) or not name.strip():
        raise StoreError(f"{FILE_NAME}: Eintrag {index + 1} hat keinen „name“")
    where = f"{FILE_NAME}: „{name}“"
    if "id" in entry and not (isinstance(entry["id"], str) and re.fullmatch(r"[a-z0-9_-]+", entry["id"])):
        raise StoreError(f"{where}: „id“ nur aus a–z, 0–9 und -")
    for key, minimum in (("intervall", 1), ("toleranz", 0)):
        value = entry.get(key, 0 if key == "toleranz" else None)
        if isinstance(value, bool) or not isinstance(value, int) or not minimum <= value <= DAYS_MAX:
            raise StoreError(f"{where}: „{key}“ muss eine ganze Zahl ab {minimum} sein")
    if entry.get("zuletzt") is not None:
        try:
            date.fromisoformat(entry["zuletzt"])
        except (TypeError, ValueError):
            raise StoreError(f"{where}: „zuletzt“ muss ein Datum wie 2026-11-23 sein") from None


def _read():
    """Ganzer Dateiinhalt; unbekannte Felder bleiben erhalten. Fehlt die Datei, ist die Liste leer."""
    try:
        text = store_path().read_text(encoding="utf-8")
    except FileNotFoundError:
        return {"routinen": []}
    except OSError as exc:
        raise StoreError(f"{FILE_NAME} nicht lesbar ({exc.strerror})") from None
    try:
        data = json.loads(text)
    except json.JSONDecodeError as exc:
        raise StoreError(f"{FILE_NAME} fehlerhaft: Zeile {exc.lineno}, Spalte {exc.colno}") from None
    entries = data.get("routinen") if isinstance(data, dict) else None
    if not isinstance(entries, list):
        raise StoreError(f"{FILE_NAME}: Liste „routinen“ fehlt")
    for index, entry in enumerate(entries):
        _check(entry, index)
    taken = set()
    for entry in entries:
        if "id" in entry:
            if entry["id"] in taken:
                raise StoreError(f"{FILE_NAME}: „id“ {entry['id']} kommt doppelt vor")
            taken.add(entry["id"])
    for entry in entries:  # von Hand ohne id angelegt: wird beim nächsten Schreiben gespeichert
        if "id" not in entry:
            entry["id"] = unique_id(entry["name"], taken)
            taken.add(entry["id"])
    return data


def load():
    return _read()["routinen"]


def _write(data):
    path = store_path()
    tmp = path.with_name(path.name + ".tmp")
    with open(tmp, "w", encoding="utf-8") as file:
        json.dump(data, file, ensure_ascii=False, indent=2)
        file.write("\n")
    os.replace(tmp, path)


def _update(change):
    """Lesen, ändern, schreiben direkt nacheinander – was vorher von Hand geändert wurde, bleibt erhalten."""
    with _lock:
        data = _read()
        result = change(data["routinen"])
        _write(data)
        return result


def _find(entries, routine_id):
    for entry in entries:
        if entry["id"] == routine_id:
            return entry
    raise KeyError(routine_id)


def mark_done(routine_id, today=None):
    day = (today or timezone.localdate()).isoformat()
    _update(lambda entries: _find(entries, routine_id).update(zuletzt=day))


def save_entry(routine_id, fields):
    """`routine_id` None legt neu an. Gibt die id zurück."""
    def change(entries):
        if routine_id:
            entry = _find(entries, routine_id)
        else:
            entry = {"id": unique_id(fields["name"], {e["id"] for e in entries})}
            entries.append(entry)
        entry.update(
            name=fields["name"],
            intervall=fields["intervall"],
            toleranz=fields["toleranz"],
            zuletzt=fields["zuletzt"].isoformat() if fields["zuletzt"] else None,
        )
        return entry["id"]
    return _update(change)


def delete_entry(routine_id):
    _update(lambda entries: entries.remove(_find(entries, routine_id)))


def parse_form(data, today=None):
    """Formular → (Felder, Fehler je Feld)."""
    today = today or timezone.localdate()
    errors = {}
    name = data.get("name", "").strip()
    if not name:
        errors["name"] = "fehlt"
    elif len(name) > NAME_MAX:
        errors["name"] = f"höchstens {NAME_MAX} Zeichen"

    def days(key, minimum):
        try:
            value = int(data.get(key, ""))
        except ValueError:
            errors[key] = "ganze Zahl"
            return None
        if not minimum <= value <= DAYS_MAX:
            errors[key] = f"{minimum}–{DAYS_MAX}"
        return value

    intervall, toleranz = days("intervall", 1), days("toleranz", 0)
    zuletzt = None
    raw = data.get("zuletzt", "").strip()
    if raw:
        try:
            zuletzt = date.fromisoformat(raw)
        except ValueError:
            errors["zuletzt"] = "kein Datum"
        else:
            if zuletzt > today:
                errors["zuletzt"] = "liegt in der Zukunft"
    return {"name": name, "intervall": intervall, "toleranz": toleranz, "zuletzt": zuletzt}, errors


def _days(n):
    return "1 Tag" if n == 1 else f"{n} Tagen"


def build_item(entry, today):
    intervall, toleranz = entry["intervall"], entry.get("toleranz", 0)
    zuletzt = date.fromisoformat(entry["zuletzt"]) if entry.get("zuletzt") else None
    if zuletzt is None:
        return {"id": entry["id"], "name": entry["name"], "kind": "due", "label": "noch nie erledigt",
                "ratio": 1.0, "fill": "100%", "due_in": 0}
    age = max(0, (today - zuletzt).days)  # Datum in der Zukunft (von Hand) zählt wie heute
    due_in = intervall - age
    if age > intervall + toleranz:
        kind, label = "over", f"überfällig seit {_days(-due_in)}"
    elif age >= intervall:
        kind, label = "due", "heute fällig" if due_in == 0 else f"fällig seit {_days(-due_in)}"
    else:
        kind, label = "ok", "morgen" if due_in == 1 else f"in {_days(due_in)}"
    return {
        "id": entry["id"],
        "name": entry["name"],
        "kind": kind,
        "label": label,
        "ratio": age / intervall,
        "fill": f"{min(age / (intervall * 1.5), 1) * 100:.1f}%",  # Ziel liegt bei 2/3 der Balkenlänge
        "due_in": due_in,
    }


STATUS_LABELS = {"red": "überfällig", "amber": "fällig", "green": "alles erledigt", "unknown": "keine Daten"}


def get_tile(show_all=False, edit=None, form=None, errors=None, today=None):
    today = today or timezone.localdate()
    tile = {
        "status": "unknown", "status_label": STATUS_LABELS["unknown"], "summary": "", "error": None,
        "items": [], "has_entries": False, "count_all": 0, "show_all": show_all,
        "edit": None, "today_iso": today.isoformat(), "poll_seconds": getattr(settings, "ROUTINEN_POLL_SECONDS", POLL_SECONDS),
    }
    try:
        entries = load()
    except StoreError as exc:
        tile["error"] = str(exc)
        return tile

    items = [build_item(entry, today) for entry in entries]
    due = sorted((i for i in items if i["kind"] != "ok"), key=lambda i: -i["ratio"])
    ok = sorted((i for i in items if i["kind"] == "ok"), key=lambda i: i["due_in"])
    status = "red" if any(i["kind"] == "over" for i in due) else "amber" if due else "green"
    tile.update(
        status=status,
        status_label=STATUS_LABELS[status],
        summary=f"{len(due)} fällig" if due else ("alles erledigt" if items else ""),
        items=due + ok if show_all else due,
        has_entries=bool(items),
        count_all=len(items),
    )

    if edit:
        entry = next((e for e in entries if e["id"] == edit), None)
        if entry is None and edit != "neu":
            return tile
        if form is not None:
            values = form
        elif entry:
            values = {"name": entry["name"], "intervall": entry["intervall"],
                      "toleranz": entry.get("toleranz", 0), "zuletzt": entry.get("zuletzt") or ""}
        else:
            values = {"name": "", **NEW_DEFAULTS, "zuletzt": today.isoformat()}
        hint = ""
        if entry and entry.get("zuletzt"):
            due_day = date.fromisoformat(entry["zuletzt"]) + timedelta(days=entry["intervall"])
            red_day = due_day + timedelta(days=entry.get("toleranz", 0) + 1)
            hint = f"Fällig {date_label(due_day)} · rot ab {date_label(red_day)}"
        tile["edit"] = {
            "id": entry["id"] if entry else "",
            "title": "Routine bearbeiten" if entry else "Neue Routine",
            "values": values,
            "errors": errors or {},
            "hint": hint,
        }
    return tile
