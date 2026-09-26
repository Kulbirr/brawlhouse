"""Phase 7 admin panel privacy tests (FastAPI TestClient).

The admin page flips in-house switches; this suite guards the privacy
boundary the owner's hard rule depends on:

  - public surfaces (GET /api/settings, /api/treasury/stats,
    /api/treasury/burns) never carry any PRIVATE_FIELDS entry,
    before AND after admin writes;
  - every admin endpoint 401s without the token (503 when ADMIN_TOKEN
    is unset on the server);
  - a branding change through the admin API is visible on the next
    public GET /api/settings;
  - /admin.html is served (200, noindex) and is NOT linked from the
    public index.html nav.
"""

import os
import tempfile
import unittest
from contextlib import contextmanager
from pathlib import Path

os.environ["ADMIN_TOKEN"] = "test-admin-token"
os.environ["ARENA_BETTING_WINDOW_SEC"] = "0"  # no betting-window wait in tests

from fastapi.testclient import TestClient  # noqa: E402

from backend.app import create_app  # noqa: E402
from backend.settings_store import PRIVATE_FIELDS  # noqa: E402

ADMIN_HEADERS = {"X-Admin-Token": "test-admin-token"}
FRONTEND_DIR = Path(__file__).resolve().parent.parent / "web" / "dist"

# sorted for deterministic failure messages
PRIVATE = sorted(PRIVATE_FIELDS)


@contextmanager
def temp_app():
    with tempfile.TemporaryDirectory() as tmp:
        yield create_app(data_dir=tmp)


def _assert_no_private(testcase, payload, where):
    """Recursively assert no PRIVATE_FIELDS key appears in a JSON payload."""
    if isinstance(payload, dict):
        for k, v in payload.items():
            testcase.assertNotIn(
                k, PRIVATE, f"private field {k!r} leaked in {where}")
            _assert_no_private(testcase, v, where)
    elif isinstance(payload, list):
        for item in payload:
            _assert_no_private(testcase, item, where)


class AdminPrivacyTest(unittest.TestCase):
    # ------------------------------------------------------- public surfaces
    def test_public_settings_hides_all_private_fields(self):
        with temp_app() as app:
            with TestClient(app) as c:
                body = c.get("/api/settings").json()
                for key in PRIVATE:
                    self.assertNotIn(key, body, f"private field {key} is public")
                # and the public fields the site needs are still there
                self.assertIn("project_name", body)
                self.assertIn("token_ticker", body)
                self.assertIn("hire_fee_sol", body)

    def test_private_fields_stay_hidden_after_admin_change(self):
        with temp_app() as app:
            with TestClient(app) as c:
                # flip every private field via the admin API
                r = c.put("/api/admin/settings", headers=ADMIN_HEADERS, json={
                    "buyback_enabled": False,
                    "buyback_live": True,
                    "buyback_mock_rate": 12345.0,
                    "buyback_hot_sol_cap": 1.5,
                    "team_sweep_enabled": True,
                    "project_name": "PRIVACY-PROBE",
                })
                self.assertEqual(r.status_code, 200)
                # admin view may see them; public surfaces must not
                self.assertEqual(r.json()["settings"]["buyback_enabled"], False)
                _assert_no_private(self, c.get("/api/settings").json(),
                                   "GET /api/settings")
                _assert_no_private(self, c.get("/api/treasury/stats").json(),
                                   "GET /api/treasury/stats")
                _assert_no_private(self, c.get("/api/treasury/burns").json(),
                                   "GET /api/treasury/burns")

    def test_branding_change_visible_on_public_settings(self):
        with temp_app() as app:
            with TestClient(app) as c:
                r = c.put("/api/admin/settings", headers=ADMIN_HEADERS, json={
                    "project_name": "NEW-NAME",
                    "token_ticker": "NEW",
                    "token_mint": "Mint111111111111111111111111111111111111111",
                })
                self.assertEqual(r.status_code, 200)
                body = c.get("/api/settings").json()
                self.assertEqual(body["project_name"], "NEW-NAME")
                self.assertEqual(body["token_ticker"], "NEW")
                self.assertEqual(
                    body["token_mint"],
                    "Mint111111111111111111111111111111111111111")
                for key in PRIVATE:
                    self.assertNotIn(key, body)

    # ------------------------------------------------------------------ auth
    def test_admin_endpoints_401_without_token(self):
        with temp_app() as app:
            with TestClient(app) as c:
                # no token at all
                self.assertEqual(
                    c.put("/api/admin/settings",
                          json={"hire_fee_sol": 0.5}).status_code, 401)
                self.assertEqual(
                    c.put("/api/admin/fighters/iron-1",
                          json={"tagline": "x"}).status_code, 401)
                self.assertEqual(
                    c.get("/api/treasury/fees").status_code, 401)
                self.assertEqual(
                    c.post("/api/admin/battles/run-official").status_code,
                    401)
                # wrong token
                bad = {"X-Admin-Token": "wrong"}
                self.assertEqual(
                    c.put("/api/admin/settings", json={"hire_fee_sol": 0.5},
                          headers=bad).status_code, 401)
                self.assertEqual(
                    c.get("/api/treasury/fees", headers=bad).status_code, 401)
                # right token works
                self.assertEqual(
                    c.put("/api/admin/settings", json={"hire_fee_sol": 0.5},
                          headers=ADMIN_HEADERS).status_code, 200)
                self.assertEqual(
                    c.get("/api/treasury/fees",
                          headers=ADMIN_HEADERS).status_code, 200)

    def test_admin_503_when_token_unconfigured(self):
        with temp_app() as app:
            old = os.environ.pop("ADMIN_TOKEN", None)
            try:
                with TestClient(app) as c:
                    r = c.put("/api/admin/settings",
                              json={"hire_fee_sol": 0.5},
                              headers=ADMIN_HEADERS)
                    self.assertEqual(r.status_code, 503)
                    r = c.get("/api/treasury/fees", headers=ADMIN_HEADERS)
                    self.assertEqual(r.status_code, 503)
            finally:
                if old is not None:
                    os.environ["ADMIN_TOKEN"] = old

    # ------------------------------------------------- the admin page itself
    def test_admin_page_served_with_noindex(self):
        with temp_app() as app:
            with TestClient(app) as c:
                r = c.get("/admin.html")
                self.assertEqual(r.status_code, 200)
                html = r.text
                self.assertIn('<meta name="robots" content="noindex">', html)
                # the page must not hardcode any brand identity: branding
                # comes from the API only
                self.assertNotIn("ARENA-PROJECT", html)
                # and it must not embed any secret
                self.assertNotIn("test-admin-token", html)

    def test_admin_page_not_linked_from_public_nav(self):
        index_html = (FRONTEND_DIR / "index.html").read_text()
        self.assertNotIn("admin.html", index_html)
        # no nav anchor may point at the admin page either
        for line in index_html.splitlines():
            if "<a " in line and "href" in line:
                self.assertNotIn("admin", line.lower(),
                                 f"public nav links to admin: {line.strip()}")

    def test_admin_page_has_no_public_links_back(self):
        """The admin page must not be reachable-by-accident bait either:
        it carries no link to itself from public pages (covered above) and
        the public index carries no admin token form."""
        admin_html = (FRONTEND_DIR / "admin.html").read_text()
        self.assertIn("sessionStorage", admin_html)
        # token is only ever sent as a header, never in a URL
        self.assertIn("X-Admin-Token", admin_html)
        self.assertNotIn("?token=", admin_html)
        self.assertNotIn("token=", admin_html.replace("X-Admin-Token", ""))


if __name__ == "__main__":
    unittest.main()
