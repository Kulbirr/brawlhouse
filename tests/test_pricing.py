"""Strength-based hire pricing tests.

Hire prices are derived from recorded win rates, not admin-set per fighter:
    price = base_fee * clamp(win_rate / avg_win_rate, 0.5, 2.0)
Fighters with no history price at exactly the base fee.
"""

import os
import tempfile
import unittest
from contextlib import contextmanager

os.environ["ADMIN_TOKEN"] = "test-admin-token"
os.environ["ARENA_BETTING_WINDOW_SEC"] = "0"  # no betting-window wait in tests
os.environ["ARENA_PLAYBACK_TICK_MS"] = "0"

from fastapi.testclient import TestClient  # noqa: E402

from backend.app import create_app  # noqa: E402
from backend.db import Database  # noqa: E402


@contextmanager
def temp_app():
    with tempfile.TemporaryDirectory() as tmp:
        yield create_app(data_dir=tmp), Database(f"{tmp}/arena.db")


@contextmanager
def betting_window(seconds=3):
    """Battles need a real 'open' window for bet/hire API tests.

    The module disables the window (ARENA_BETTING_WINDOW_SEC=0) for speed,
    but the API only accepts bets and hires while a battle is 'open', so
    placement tests opt back into a short window here. settings.get reads
    the env var on every call, so flipping it works even for an app that
    is already running."""
    key = "ARENA_BETTING_WINDOW_SEC"
    saved = os.environ.get(key)
    os.environ[key] = str(seconds)
    try:
        yield
    finally:
        if saved is None:
            os.environ.pop(key, None)
        else:
            os.environ[key] = saved


def base_fee(c):
    return c.get("/api/settings").json()["hire_fee_sol"]


class PricingTest(unittest.TestCase):
    def test_no_history_all_price_at_base(self):
        with temp_app() as (app, _db):
            with TestClient(app) as c:
                base = base_fee(c)
                fighters = c.get("/api/fighters").json()["fighters"]
                self.assertEqual(len(fighters), 5)
                for f in fighters:
                    self.assertIn("hire_fee_sol", f)
                    self.assertEqual(f["hire_fee_sol"], base)

    def test_stronger_fighter_costs_more(self):
        with temp_app() as (app, db):
            # iron-1: dominant (8-2), hawk-2: weak (1-9), rest: no history
            for _ in range(8):
                db.record_result("iron-1", "win")
            for _ in range(2):
                db.record_result("iron-1", "loss")
            db.record_result("hawk-2", "win")
            for _ in range(9):
                db.record_result("hawk-2", "loss")
            with TestClient(app) as c:
                base = base_fee(c)
                fees = {f["id"]: f["hire_fee_sol"]
                        for f in c.get("/api/fighters").json()["fighters"]}
                # win rates: iron-1 0.8, hawk-2 0.1, avg of known = 0.45
                # iron-1: 0.8/0.45 = 1.78x ; hawk-2: 0.1/0.45 = 0.5x (clamped)
                self.assertGreater(fees["iron-1"], base)
                self.assertLess(fees["hawk-2"], base)
                self.assertAlmostEqual(fees["iron-1"], round(base * (0.8 / 0.45), 4))
                self.assertAlmostEqual(fees["hawk-2"], round(base * 0.5, 4))
                # fighters with no history stay at base
                self.assertEqual(fees["aegis-4"], base)

    def test_multiplier_clamped(self):
        with temp_app() as (app, db):
            # one dominant fighter against four weak ones: raw ratio would be
            # 3.57x, but the multiplier caps at 2x.
            for _ in range(100):
                db.record_result("iron-1", "win")
            for fid in ("hawk-2", "aegis-4", "jackal-5", "wasp-6"):
                db.record_result(fid, "win")
                for _ in range(9):
                    db.record_result(fid, "loss")
            with TestClient(app) as c:
                base = base_fee(c)
                fees = {f["id"]: f["hire_fee_sol"]
                        for f in c.get("/api/fighters").json()["fighters"]}
                self.assertAlmostEqual(fees["iron-1"], round(base * 2.0, 4))
                # weak fighters: 0.1 / 0.28 = 0.357x would go below the floor
                for fid in ("hawk-2", "aegis-4", "jackal-5", "wasp-6"):
                    self.assertGreaterEqual(fees[fid], round(base * 0.5, 4))

    def test_hire_charges_fighter_specific_fee(self):
        with betting_window(3), temp_app() as (app, db):
            for _ in range(9):
                db.record_result("iron-1", "win")
            db.record_result("iron-1", "loss")
            db.record_result("hawk-2", "win")
            for _ in range(9):
                db.record_result("hawk-2", "loss")
            with TestClient(app) as c:
                r = c.post("/api/battles", json={
                    "fighter_ids": ["iron-1", "hawk-2"]})
                self.assertEqual(r.status_code, 202)
                battle_id = r.json()["id"]
                fees = {f["id"]: f["hire_fee_sol"]
                        for f in c.get("/api/fighters").json()["fighters"]}
                strong = c.post(
                    f"/api/battles/{battle_id}/hire",
                    json={"fighter_id": "iron-1", "wallet": "WalletAAA"}).json()
                weak = c.post(
                    f"/api/battles/{battle_id}/hire",
                    json={"fighter_id": "hawk-2", "wallet": "WalletBBB"}).json()
                self.assertEqual(strong["fee_sol"], fees["iron-1"])
                self.assertEqual(weak["fee_sol"], fees["hawk-2"])
                self.assertGreater(strong["fee_sol"], weak["fee_sol"])

    def test_single_fighter_view_carries_price(self):
        with temp_app() as (app, _db):
            with TestClient(app) as c:
                base = base_fee(c)
                f = c.get("/api/fighters/wasp-6").json()
                self.assertEqual(f["hire_fee_sol"], base)


if __name__ == "__main__":
    unittest.main()
