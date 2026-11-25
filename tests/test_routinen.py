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


class RoutinesTests(SimpleTestCase):
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
