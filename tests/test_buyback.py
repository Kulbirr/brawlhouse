"""Phase 6 buyback bot tests.

Covers: (a) mock full cycle - seed fee events -> run -> burn event recorded
with correct sol_spent (= buyback_pct of new fees), dry_run=true, zero
network; (b) second run allocates nothing (no double-spend); (c)
buyback_enabled=false -> no-op, fees untouched; (d) team_pct accounting;
(e) live mode with missing config -> loud failure, never silent mock.
Also: the public treasury endpoints' shape, and that buyback_enabled is
never exposed publicly.
"""

import os
import sys
import tempfile
import unittest
import urllib.request
from contextlib import contextmanager

os.environ["ADMIN_TOKEN"] = "test-admin-token"
os.environ["ARENA_BETTING_WINDOW_SEC"] = "0"  # no betting-window wait in tests

import bot.buyback as bb  # noqa: E402
from backend.db import Database  # noqa: E402
from backend.settings_store import PRIVATE_FIELDS, SettingsStore  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402
from backend.app import create_app  # noqa: E402

ADMIN_HEADERS = {"X-Admin-Token": "test-admin-token"}


@contextmanager
def temp_pair(**settings_updates):
    """Fresh temp (Database, SettingsStore) pair."""
    with tempfile.TemporaryDirectory() as tmp:
        db = Database(os.path.join(tmp, "arena.db"))
        settings = SettingsStore(os.path.join(tmp, "settings.json"))
        if settings_updates:
            settings.update(settings_updates)
        yield db, settings


def no_network(test_case):
    """Guard: the mock path must perform zero network calls."""
    real_urlopen = urllib.request.urlopen

    def blocked(*args, **kwargs):
        raise AssertionError("network call attempted in mock mode")

    urllib.request.urlopen = blocked
    test_case.addCleanup(setattr, urllib.request, "urlopen", real_urlopen)


def seed_fees(db):
    db.record_fee_event("b1", "betting", 2.0)
    db.record_fee_event("b1", "hire", 0.5)


class TestMockCycle(unittest.TestCase):
    def test_mock_full_cycle(self):
        with temp_pair() as (db, settings):
            no_network(self)
            seed_fees(db)  # 2.5 SOL total
            result = bb.run_cycle(settings, db)
            self.assertEqual(result["status"], "burned")
            self.assertTrue(result["dry_run"])
            # buyback_pct default 50% -> 1.25 SOL
            self.assertAlmostEqual(result["sol_spent"], 1.25, places=9)
            self.assertTrue(result["buy_tx"].startswith("mock_"))
            self.assertTrue(result["burn_tx"].startswith("mock_"))
            # tokens = 1.25 SOL * mock rate (default 10000.0)
            self.assertAlmostEqual(
                result["tokens_burned"], 1.25 * 10000.0, places=6)

            burns = db.list_burn_events()
            self.assertEqual(len(burns), 1)
            row = burns[0]
            self.assertAlmostEqual(row["sol_spent"], 1.25, places=9)
            self.assertTrue(row["dry_run"])
            self.assertTrue(row["buy_tx"].startswith("mock_"))
            self.assertTrue(row["burn_tx"].startswith("mock_"))

            # mock mode must never import the live-only deps
            self.assertNotIn("solana", sys.modules)
            self.assertNotIn("solders", sys.modules)

    def test_no_double_spend(self):
        with temp_pair() as (db, settings):
            no_network(self)
            seed_fees(db)
            self.assertEqual(bb.run_cycle(settings, db)["status"], "burned")
            self.assertEqual(db.burn_totals()["burn_count"], 1)
            # second run: fees already allocated -> no-op
            result = bb.run_cycle(settings, db)
            self.assertEqual(result["status"], "no_fees")
            self.assertEqual(db.burn_totals()["burn_count"], 1)
            self.assertEqual(db.unallocated_fees_total(), 0.0)

    def test_paused_toggle_noop(self):
        with temp_pair() as (db, settings):
            no_network(self)
            settings.update({"buyback_enabled": False})
            seed_fees(db)
            result = bb.run_cycle(settings, db)
            self.assertEqual(result["status"], "paused")
            # fees untouched: still fully unallocated, no burn row
            self.assertEqual(db.burn_totals()["burn_count"], 0)
            self.assertAlmostEqual(db.unallocated_fees_total(), 2.5, places=9)
            # re-enabling picks the accumulated fees up on the next run
            settings.update({"buyback_enabled": True})
            result = bb.run_cycle(settings, db)
            self.assertEqual(result["status"], "burned")
            self.assertAlmostEqual(result["sol_spent"], 1.25, places=9)

    def test_team_pct_accounting(self):
        with temp_pair(buyback_pct=70.0, team_pct=20.0) as (db, settings):
            no_network(self)
            seed_fees(db)  # 2.5 SOL
            result = bb.run_cycle(settings, db)
            self.assertEqual(result["status"], "burned")
            self.assertAlmostEqual(result["sol_spent"], 1.75, places=9)
            totals = db.team_totals()
            self.assertEqual(totals["allocation_count"], 1)
            self.assertAlmostEqual(totals["total_team_sol"], 0.5, places=9)
            # 2.5 - 1.75 - 0.5 = 0.25 stays unallocated in the treasury
            self.assertAlmostEqual(db.unallocated_fees_total(), 0.0, places=9)

    def test_custom_mock_rate(self):
        with temp_pair(buyback_mock_rate=42.0) as (db, settings):
            no_network(self)
            seed_fees(db)
            result = bb.run_cycle(settings, db)
            self.assertAlmostEqual(result["tokens_burned"], 1.25 * 42.0,
                                   places=6)


class TestLiveModeGating(unittest.TestCase):
    def _clear_env(self):
        saved = {}
        for key in ("TREASURY_WALLET", "TREASURY_PRIVATE_KEY",
                    "HELIUS_API_KEY", "TEAM_WALLET"):
            saved[key] = os.environ.pop(key, None)
        return saved

    def _restore_env(self, saved):
        for key, val in saved.items():
            if val is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = val

    def test_live_missing_config_fails_loudly(self):
        saved = self._clear_env()
        self.addCleanup(self._restore_env, saved)
        with temp_pair(buyback_live=True) as (db, settings):
            seed_fees(db)
            with self.assertRaises(bb.BuybackError) as ctx:
                bb.run_cycle(settings, db)
            self.assertIn("missing from .env", str(ctx.exception))
            # never silently mocked: no burn row, fees still unallocated
            self.assertEqual(db.burn_totals()["burn_count"], 0)
            self.assertAlmostEqual(db.unallocated_fees_total(), 2.5, places=9)

    def test_live_empty_token_mint_refused(self):
        saved = self._clear_env()
        self.addCleanup(self._restore_env, saved)
        os.environ["TREASURY_WALLET"] = "x" * 44
        os.environ["TREASURY_PRIVATE_KEY"] = "y" * 88
        os.environ["HELIUS_API_KEY"] = "z"
        with temp_pair(buyback_live=True) as (db, settings):
            # token_mint is empty by default -> clean refusal, no guess
            self.assertEqual(settings.get("token_mint"), "")
            seed_fees(db)
            with self.assertRaises(bb.BuybackError) as ctx:
                bb.run_cycle(settings, db)
            self.assertIn("token_mint", str(ctx.exception))
            self.assertEqual(db.burn_totals()["burn_count"], 0)

    def test_live_team_sweep_needs_team_wallet(self):
        saved = self._clear_env()
        self.addCleanup(self._restore_env, saved)
        os.environ["TREASURY_WALLET"] = "x" * 44
        os.environ["TREASURY_PRIVATE_KEY"] = "y" * 88
        os.environ["HELIUS_API_KEY"] = "z"
        with temp_pair(buyback_live=True, team_sweep_enabled=True) as (db, settings):
            with self.assertRaises(bb.BuybackError) as ctx:
                bb._live_config(settings)
            self.assertIn("TEAM_WALLET", str(ctx.exception))


class TestTreasuryEndpoints(unittest.TestCase):
    def test_stats_and_burns(self):
        with tempfile.TemporaryDirectory() as tmp:
            app = create_app(data_dir=tmp)
            db = app.state.db
            settings = app.state.settings
            seed_fees(db)
            c = TestClient(app)
            r = c.get("/api/treasury/stats")
            self.assertEqual(r.status_code, 200)
            stats = r.json()
            self.assertEqual(
                set(stats.keys()),
                {"treasury_balance_sol", "total_fees_sol",
                 "total_burned_tokens", "burn_count", "token_ticker",
                 "project_name"})
            self.assertNotIn("buyback_enabled", stats)
            self.assertAlmostEqual(stats["total_fees_sol"], 2.5, places=9)
            self.assertAlmostEqual(stats["treasury_balance_sol"], 2.5,
                                   places=9)
            self.assertEqual(stats["burn_count"], 0)

            # run a mock round, then stats reflect it
            res = bb.run_cycle(settings, db)
            self.assertEqual(res["status"], "burned")
            stats = c.get("/api/treasury/stats").json()
            self.assertEqual(stats["burn_count"], 1)
            self.assertAlmostEqual(stats["total_burned_tokens"],
                                   1.25 * 10000.0, places=6)
            self.assertAlmostEqual(stats["treasury_balance_sol"], 0.0,
                                   places=9)
            self.assertEqual(stats["token_ticker"], settings.get("token_ticker"))
            self.assertEqual(stats["project_name"], settings.get("project_name"))

            # burn history, paginated
            r = c.get("/api/treasury/burns?limit=25&offset=0")
            self.assertEqual(r.status_code, 200)
            body = r.json()
            self.assertEqual(body["total"], 1)
            self.assertEqual(len(body["burns"]), 1)
            row = body["burns"][0]
            for key in ("created_at", "sol_spent", "tokens_bought",
                        "tokens_burned", "buy_tx", "burn_tx", "dry_run"):
                self.assertIn(key, row)
            r = c.get("/api/treasury/burns?limit=25&offset=5")
            self.assertEqual(r.json()["burns"], [])

    def test_public_settings_hides_inhouse_fields(self):
        with tempfile.TemporaryDirectory() as tmp:
            app = create_app(data_dir=tmp)
            c = TestClient(app)
            public = c.get("/api/settings").json()
            for hidden in ("buyback_enabled", "buyback_live",
                           "buyback_hot_sol_cap", "team_sweep_enabled",
                           "buyback_mock_rate"):
                self.assertNotIn(hidden, public)
                self.assertIn(hidden, PRIVATE_FIELDS)
            # admin endpoint can still set them (for phase 7)
            r = c.put("/api/admin/settings", json={"buyback_enabled": False},
                      headers=ADMIN_HEADERS)
            self.assertEqual(r.status_code, 200)
            self.assertFalse(
                r.json()["settings"]["buyback_enabled"])
            self.assertNotIn("buyback_enabled",
                             c.get("/api/settings").json())

    def test_treasury_view_has_no_hardcoded_names(self):
        # The old static frontend/ prototype was removed; the live React
        # app under web/src must still brand through settings, not
        # literals.
        root = os.path.join(os.path.dirname(__file__), "..", "web", "src")
        tsx_files = []
        for dirpath, _dirnames, filenames in os.walk(root):
            for fn in filenames:
                if fn.endswith((".tsx", ".ts")):
                    tsx_files.append(os.path.join(dirpath, fn))
        self.assertTrue(tsx_files, "web/src must contain React sources")
        treasury = [f for f in tsx_files if "Treasury" in f]
        self.assertTrue(treasury, "a Treasury page source must exist")
        blob = "\n".join(open(f).read() for f in treasury)
        # treasury data comes from the backend APIs...
        for token in ("/api/treasury/stats", "/api/treasury/burns"):
            self.assertIn(token, blob)
        # ...branding flows through settings (loaded from GET /api/settings
        # at boot), never as literals in the treasury code.
        self.assertIn("token_ticker", blob)
        all_src = "\n".join(open(f).read() for f in tsx_files)
        for literal in ("$TKN", "ARENA-PROJECT"):
            self.assertNotIn(literal, all_src)


if __name__ == "__main__":
    unittest.main()
