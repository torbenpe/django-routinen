import json
import tempfile
from datetime import date
from pathlib import Path

from django.core.exceptions import ImproperlyConfigured
from django.template import Context, Template
from django.test import SimpleTestCase, override_settings
from django.urls import reverse

from routinen import core


def setUpModule():
    global _data_dir, _data_override
    _data_dir = tempfile.TemporaryDirectory()
    _data_override = override_settings(DATA_DIR=Path(_data_dir.name))
    _data_override.enable()


def tearDownModule():
    _data_override.disable()
    _data_dir.cleanup()


TODAY = date(2026, 11, 23)


class StoreTestCase(SimpleTestCase):
    """Hilfen für Tests mit routinen.json im Testordner."""

    def setUp(self):
        self.path = core.store_path()
        self.addCleanup(self.path.unlink, missing_ok=True)

    def write(self, data):
        self.path.write_text(data if isinstance(data, str) else json.dumps(data), encoding="utf-8")

    def read(self):
        return json.loads(self.path.read_text(encoding="utf-8"))

    def sample(self):
        self.write({"version": 1, "routinen": [
            {"id": "aufgabe-a", "name": "Aufgabe A", "intervall": 4, "toleranz": 1, "zuletzt": "2026-11-17"},
            {"id": "aufgabe-b", "name": "Aufgabe B lang", "intervall": 14, "toleranz": 4, "zuletzt": "2026-11-08"},
            {"id": "aufgabe-c", "name": "Prüfung für Öl", "intervall": 14, "toleranz": 7, "zuletzt": "2026-11-18", "notiz": "eigene Notiz"},
            {"id": "aufgabe-d", "name": "Aufgabe D", "intervall": 7, "toleranz": 3, "zuletzt": "2026-11-16"},
        ]})


class RoutinesTests(StoreTestCase):
    def test_states_and_order(self):
        self.sample()
        tile = core.get_tile(today=TODAY)
        self.assertEqual(tile["status"], "red")
        self.assertEqual([(i["id"], i["kind"]) for i in tile["items"]], [("aufgabe-a", "over"), ("aufgabe-b", "due"), ("aufgabe-d", "due")])
        self.assertEqual(tile["items"][0]["label"], "überfällig seit 2 Tagen")
        self.assertEqual(tile["items"][1]["label"], "fällig seit 1 Tag")
        self.assertEqual(tile["items"][2]["label"], "heute fällig")
        self.assertEqual(tile["summary"], "3 fällig")

    def test_show_all_includes_ok_entries(self):
        self.sample()
        tile = core.get_tile(show_all=True, today=TODAY)
        self.assertEqual(len(tile["items"]), 4)
        self.assertEqual(tile["items"][-1]["label"], "in 9 Tagen")

    def test_never_done_is_due(self):
        self.write({"routinen": [{"name": "Neue Aufgabe", "intervall": 90, "zuletzt": None}]})
        (item,) = core.get_tile(today=TODAY)["items"]
        self.assertEqual((item["id"], item["kind"], item["label"]), ("neue-aufgabe", "due", "noch nie erledigt"))

    def test_missing_file_is_empty(self):
        tile = core.get_tile(today=TODAY)
        self.assertEqual((tile["status"], tile["items"], tile["has_entries"]), ("green", [], False))

    def test_broken_file_shows_error_and_is_not_overwritten(self):
        self.write('{"routinen": [')
        tile = core.get_tile(today=TODAY)
        self.assertIn("fehlerhaft", tile["error"])
        self.assertEqual(tile["status"], "unknown")
        with self.assertRaises(core.StoreError):
            core.mark_done("aufgabe-a")
        self.assertEqual(self.path.read_text(encoding="utf-8"), '{"routinen": [')

    def test_invalid_entry(self):
        self.write({"routinen": [{"name": "X", "intervall": 0}]})
        self.assertIn("intervall", core.get_tile(today=TODAY)["error"])

    def test_done_view_sets_today_and_keeps_other_fields(self):
        self.sample()
        response = self.client.post(reverse("routinen:done", args=["aufgabe-c"]), HTTP_HX_REQUEST="true")
        self.assertEqual(response.status_code, 200)
        data = self.read()
        self.assertEqual(data["version"], 1)
        entry = data["routinen"][2]
        self.assertEqual(entry["zuletzt"], core.timezone.localdate().isoformat())
        self.assertEqual(entry["notiz"], "eigene Notiz")

    def test_done_without_htmx_redirects(self):
        self.sample()
        response = self.client.post(reverse("routinen:done", args=["aufgabe-c"]))
        self.assertRedirects(response, "/", fetch_redirect_response=False)

    def test_done_unknown_is_404(self):
        self.sample()
        self.assertEqual(self.client.post(reverse("routinen:done", args=["gibts-nicht"])).status_code, 404)

    def test_save_new(self):
        self.sample()
        self.client.post(reverse("routinen:save"), {
            "id": "", "name": "Prüfung für Öl", "intervall": "21", "toleranz": "3", "zuletzt": "2026-11-01",
        }, HTTP_HX_REQUEST="true")
        new = self.read()["routinen"][-1]
        self.assertEqual(new, {"id": "pruefung-fuer-oel", "name": "Prüfung für Öl", "intervall": 21, "toleranz": 3, "zuletzt": "2026-11-01"})

    def test_save_existing(self):
        self.sample()
        self.client.post(reverse("routinen:save"), {
            "id": "aufgabe-b", "name": "Aufgabe B", "intervall": "10", "toleranz": "2", "zuletzt": "",
        }, HTTP_HX_REQUEST="true")
        entry = self.read()["routinen"][1]
        self.assertEqual((entry["name"], entry["intervall"], entry["zuletzt"]), ("Aufgabe B", 10, None))

    def test_save_invalid_shows_form_and_writes_nothing(self):
        self.sample()
        before = self.path.read_text(encoding="utf-8")
        response = self.client.post(reverse("routinen:save"), {
            "id": "aufgabe-b", "name": "", "intervall": "0", "toleranz": "2", "zuletzt": "",
        }, HTTP_HX_REQUEST="true")
        self.assertContains(response, 'aria-invalid="true"')
        self.assertContains(response, "1–3650")
        self.assertEqual(self.path.read_text(encoding="utf-8"), before)

    def test_delete(self):
        self.sample()
        self.client.post(reverse("routinen:delete", args=["aufgabe-d"]), HTTP_HX_REQUEST="true")
        self.assertEqual([e["id"] for e in self.read()["routinen"]], ["aufgabe-a", "aufgabe-b", "aufgabe-c"])

    def test_edit_form_and_no_polling(self):
        self.sample()
        response = self.client.get(reverse("routinen:tile"), {"edit": "aufgabe-b"})
        self.assertContains(response, 'value="Aufgabe B lang"')
        self.assertNotContains(response, "hx-trigger")

    def test_tile_renders_rows(self):
        self.sample()
        response = self.client.get(reverse("routinen:tile"))
        self.assertContains(response, "Aufgabe A bearbeiten")
        self.assertContains(response, 'data-collapse="routines"')
        self.assertContains(response, "hx-trigger")

    def test_parse_form_rejects_future_date(self):
        _, errors = core.parse_form({"name": "X", "intervall": "3", "toleranz": "0", "zuletzt": "2026-12-01"}, today=TODAY)
        self.assertEqual(errors, {"zuletzt": "liegt in der Zukunft"})

    def test_template_tag_renders_tile_with_csrf_token(self):
        self.sample()
        html = Template("{% load routinen %}{% routinen_tile %}").render(Context({"csrf_token": "abc123"}))
        self.assertIn("rt-tile", html)
        self.assertIn('value="abc123"', html)

    @override_settings(ROUTINEN_HOME_URL="/start/")
    def test_home_url_setting(self):
        self.sample()
        response = self.client.post(reverse("routinen:done", args=["aufgabe-c"]))
        self.assertRedirects(response, "/start/", fetch_redirect_response=False)

    def test_file_setting_beats_data_dir(self):
        with override_settings(ROUTINEN_FILE="/x/andere.json"):
            self.assertEqual(core.store_path(), Path("/x/andere.json"))

    @override_settings(DATA_DIR=None)
    def test_missing_path_is_a_clear_error(self):
        with self.assertRaises(ImproperlyConfigured):
            core.store_path()

    def test_poll_seconds_default_and_override(self):
        self.assertEqual(core.get_tile()["poll_seconds"], 600)
        with override_settings(ROUTINEN_POLL_SECONDS=30):
            self.assertEqual(core.get_tile()["poll_seconds"], 30)


class VerwaltungTests(StoreTestCase):
    """Verwaltungsseite und Feld `beschreibung`."""

    def page(self, **params):
        return self.client.get(reverse("routinen:verwaltung"), params)

    def post_save(self, **fields):
        data = {"id": "", "name": "X", "intervall": "7", "toleranz": "0", "zuletzt": "", "zurueck": "verwaltung"}
        data.update(fields)
        return self.client.post(reverse("routinen:save"), data)

    def test_beschreibung_must_be_short_text(self):
        self.write({"routinen": [{"name": "X", "intervall": 3, "beschreibung": 5}]})
        self.assertIn("beschreibung", core.get_overview(today=TODAY)["error"])
        self.write({"routinen": [{"name": "X", "intervall": 3, "beschreibung": "a" * (core.BESCHREIBUNG_MAX + 1)}]})
        self.assertIn("beschreibung", core.get_tile(today=TODAY)["error"])

    def test_overview_lists_all_in_order_with_counts(self):
        self.sample()
        page = core.get_overview(today=TODAY)
        self.assertEqual([i["id"] for i in page["items"]], ["aufgabe-a", "aufgabe-b", "aufgabe-d", "aufgabe-c"])
        self.assertEqual(page["counts"], {"over": 1, "due": 2, "ok": 1})
        self.assertEqual((page["status"], page["count_all"]), ("red", 4))
        self.assertEqual(page["items"][0]["zuletzt_label"], "Di, 17. Nov")
        self.assertEqual(page["items"][0]["faellig_label"], "Sa, 21. Nov")

    def test_overview_search_in_name_and_description(self):
        self.write({"routinen": [
            {"name": "Filter", "intervall": 30, "beschreibung": "Küche, unter der Spüle"},
            {"name": "Pflanzen", "intervall": 4},
        ]})
        self.assertEqual([i["name"] for i in core.get_overview(q="spüle", today=TODAY)["items"]], ["Filter"])
        self.assertEqual([i["name"] for i in core.get_overview(q="PFLANZ", today=TODAY)["items"]], ["Pflanzen"])
        self.assertEqual(core.get_overview(q="gibts nicht", today=TODAY)["count_all"], 2)

    def test_overview_broken_file(self):
        self.write("{")
        response = self.page()
        self.assertContains(response, "fehlerhaft")
        self.assertNotContains(response, "rv-table")

    def test_page_renders_all_with_description(self):
        self.write({"routinen": [{"id": "filter", "name": "Filter", "intervall": 30, "zuletzt": None,
                                  "beschreibung": "Zeile 1\nZeile <2>"}]})
        response = self.page()
        self.assertContains(response, "Filter")
        self.assertContains(response, "Zeile 1<br>Zeile &lt;2&gt;")
        self.assertContains(response, "routinen/verwaltung.css")

    def test_page_edit_and_delete_confirmation(self):
        self.write({"routinen": [{"id": "filter", "name": "Filter", "intervall": 30, "beschreibung": "unten"}]})
        response = self.page(edit="filter")
        self.assertContains(response, 'value="Filter"')
        self.assertContains(response, ">unten</textarea>")
        self.assertContains(response, 'name="zurueck" value="verwaltung"')
        response = self.page(loeschen="filter")
        self.assertContains(response, "„Filter“ wirklich löschen?")
        self.assertContains(response, reverse("routinen:delete", args=["filter"]))

    def test_save_from_page_writes_description_and_redirects(self):
        self.write({"routinen": []})
        response = self.post_save(name="Filter", beschreibung="  unter der Spüle\r\nalle 30 Tage  ")
        self.assertRedirects(response, reverse("routinen:verwaltung"), fetch_redirect_response=False)
        (entry,) = self.read()["routinen"]
        self.assertEqual(entry["beschreibung"], "unter der Spüle\nalle 30 Tage")

    def test_empty_description_removes_field(self):
        self.write({"routinen": [{"id": "filter", "name": "Filter", "intervall": 30, "beschreibung": "alt"}]})
        self.post_save(id="filter", name="Filter", intervall="30", beschreibung="")
        self.assertNotIn("beschreibung", self.read()["routinen"][0])

    def test_tile_form_keeps_description(self):
        self.write({"routinen": [{"id": "filter", "name": "Filter", "intervall": 30, "beschreibung": "bleibt"}]})
        self.client.post(reverse("routinen:save"), {
            "id": "filter", "name": "Filter neu", "intervall": "20", "toleranz": "1", "zuletzt": "",
        }, HTTP_HX_REQUEST="true")
        entry = self.read()["routinen"][0]
        self.assertEqual((entry["name"], entry["beschreibung"]), ("Filter neu", "bleibt"))

    def test_save_invalid_from_page_shows_page_with_errors(self):
        self.write({"routinen": []})
        response = self.post_save(name="", beschreibung="x" * (core.BESCHREIBUNG_MAX + 1))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "rv-page")
        self.assertContains(response, f"höchstens {core.BESCHREIBUNG_MAX} Zeichen")
        self.assertEqual(self.read(), {"routinen": []})

    def test_done_and_delete_from_page_redirect_back(self):
        self.sample()
        url = reverse("routinen:verwaltung")
        response = self.client.post(reverse("routinen:done", args=["aufgabe-a"]), {"zurueck": "verwaltung"})
        self.assertRedirects(response, url, fetch_redirect_response=False)
        response = self.client.post(reverse("routinen:delete", args=["aufgabe-a"]), {"zurueck": "verwaltung"})
        self.assertRedirects(response, url, fetch_redirect_response=False)
        self.assertNotIn("aufgabe-a", [e["id"] for e in self.read()["routinen"]])

    def test_tile_links_to_page(self):
        self.sample()
        self.assertContains(self.client.get(reverse("routinen:tile")), f'href="{reverse("routinen:verwaltung")}"')
