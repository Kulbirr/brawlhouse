"""Phase 3 backend tests (FastAPI TestClient)."""

import json
import os
import tempfile
import time
import unittest
from contextlib import contextmanager

os.environ["ADMIN_TOKEN"] = "test-admin-token"
os.environ["ARENA_BETTING_WINDOW_SEC"] = "0"  # no betting-window wait in tests
os.environ["ARENA_PLAYBACK_TICK_MS"] = "0"  # no pacing in WS replays

from fastapi.testclient import TestClient  # noqa: E402

from backend.app import create_app  # noqa: E402

ADMIN_HEADERS = {"X-Admin-Token": "test-admin-token"}


@contextmanager
def temp_app():
    with tempfile.TemporaryDirectory() as tmp:
        yield create_app(data_dir=tmp)


class BackendTest(unittest.TestCase):
    # ------------------------------------------------------------- settings
    def test_settings_public_hides_private(self):
        with temp_app() as app:
            with TestClient(app) as c:
                r = c.get("/api/settings")
                self.assertEqual(r.status_code, 200)
                body = r.json()
                self.assertEqual(body["project_name"], "BRAWLHOUSE")
                self.assertEqual(body["token_ticker"], "BRAWL")
                self.assertIn("hire_fee_sol", body)
                self.assertNotIn("buyback_enabled", body)  # in-house only

    def test_admin_settings_update_and_auth(self):
        with temp_app() as app:
            with TestClient(app) as c:
                # no token -> 401
                r = c.put("/api/admin/settings", json={"hire_fee_sol": 0.5})
                self.assertEqual(r.status_code, 401)
                # wrong token -> 401
                r = c.put("/api/admin/settings",
                          json={"hire_fee_sol": 0.5},
                          headers={"X-Admin-Token": "nope"})
                self.assertEqual(r.status_code, 401)
                # unknown key -> 400
                r = c.put("/api/admin/settings", json={"bogus": 1},
                          headers=ADMIN_HEADERS)
                self.assertEqual(r.status_code, 400)
                # valid update
                r = c.put("/api/admin/settings",
                          json={"hire_fee_sol": 0.5, "project_name": "TEST-PROJECT",
                                "buyback_enabled": False},
                          headers=ADMIN_HEADERS)
                self.assertEqual(r.status_code, 200)
                # public endpoint reflects it (private field still hidden)
                r = c.get("/api/settings")
                body = r.json()
                self.assertEqual(body["hire_fee_sol"], 0.5)
                self.assertEqual(body["project_name"], "TEST-PROJECT")
                self.assertNotIn("buyback_enabled", body)

    def test_admin_unconfigured_without_token(self):
        with temp_app() as app:
            old = os.environ.pop("ADMIN_TOKEN", None)
            try:
                with TestClient(app) as c:
                    r = c.put("/api/admin/settings", json={"hire_fee_sol": 0.5},
                              headers=ADMIN_HEADERS)
                    self.assertEqual(r.status_code, 503)
            finally:
                if old is not None:
                    os.environ["ADMIN_TOKEN"] = old

    # ------------------------------------------------------------- fighters
    def test_fighters_list_and_detail(self):
        with temp_app() as app:
            with TestClient(app) as c:
                r = c.get("/api/fighters")
                self.assertEqual(r.status_code, 200)
                fighters = r.json()["fighters"]
                self.assertEqual(len(fighters), 5)
                ids = {f["id"] for f in fighters}
                self.assertIn("iron-1", ids)
                for f in fighters:
                    self.assertIn("name", f)
                    self.assertIn("tagline", f)
                    self.assertIn("description", f)
                    self.assertEqual((f["wins"], f["losses"], f["draws"]), (0, 0, 0))
                r = c.get("/api/fighters/iron-1")
                self.assertEqual(r.status_code, 200)
                self.assertEqual(r.json()["id"], "iron-1")
                r = c.get("/api/fighters/nope")
                self.assertEqual(r.status_code, 404)

    def test_fighter_override(self):
        with temp_app() as app:
            with TestClient(app) as c:
                r = c.put("/api/admin/fighters/iron-1",
                          json={"tagline": "New test tagline"},
                          headers=ADMIN_HEADERS)
                self.assertEqual(r.status_code, 200)
                self.assertEqual(r.json()["tagline"], "New test tagline")
                # name falls back to registry default
                self.assertEqual(r.json()["name"], "IRON-1")
                r = c.get("/api/fighters/iron-1")
                self.assertEqual(r.json()["tagline"], "New test tagline")

    # -------------------------------------------------------------- battles
    def wait_finished(self, c, app, battle_id, timeout=30.0):
        deadline = time.time() + timeout
        while time.time() < deadline:
            r = c.get(f"/api/battles/{battle_id}")
            self.assertEqual(r.status_code, 200)
            if r.json()["status"] == "finished":
                # Also wait for the background thread to finish ALL db writes
                # (snapshots + records) before the temp data dir is cleaned up.
                while time.time() < deadline:
                    if app.state.runner.get_live(battle_id) is None:
                        return r.json()
                    time.sleep(0.02)
                break
            time.sleep(0.05)
        self.fail(f"battle {battle_id} did not finish in {timeout}s")

    def test_battle_create_result_flow(self):
        with temp_app() as app:
            with TestClient(app) as c:
                r = c.post("/api/battles", json={
                    "fighter_ids": ["iron-1", "hawk-2"],
                    "seed": 42,
                    "exhibition": False,
                })
                self.assertEqual(r.status_code, 202)
                battle_id = r.json()["id"]
                self.assertEqual(r.json()["status"], "running")
                self.assertFalse(r.json()["exhibition"])

                detail = self.wait_finished(c, app, battle_id)
                result = detail["result"]
                self.assertIn(result["winner"], ("iron-1", "hawk-2"))
                self.assertGreater(result["ticks"], 0)
                self.assertIn("elimination_order", result)
                self.assertIn("snapshots", detail)
                snaps = detail["snapshots"]
                self.assertGreater(len(snaps), 0)
                # EXACT Phase 1 snapshot field format
                first = snaps[0]
                self.assertIn("tick", first)
                self.assertIn("fighters", first)
                self.assertIn("projectiles", first)
                ff = first["fighters"][0]
                for field in ("id", "x", "y", "vx", "vy", "heading", "hp",
                              "alive", "fire_cooldown", "dash_cooldown",
                              "shield_energy", "shield_active", "kills", "benched"):
                    self.assertIn(field, ff)

                # fighter records updated
                r = c.get("/api/fighters")
                total = sum(f["wins"] + f["losses"] + f["draws"]
                            for f in r.json()["fighters"])
                self.assertEqual(total, 2)
                winner_row = next(f for f in r.json()["fighters"]
                                  if f["id"] == result["winner"])
                self.assertEqual(winner_row["wins"], 1)

                # list + filters
                r = c.get("/api/battles", params={"status": "finished"})
                self.assertTrue(any(b["id"] == battle_id
                                    for b in r.json()["battles"]))
                r = c.get("/api/battles", params={"exhibition": "true"})
                self.assertFalse(any(b["id"] == battle_id
                                     for b in r.json()["battles"]))

    def test_battle_validation(self):
        with temp_app() as app:
            with TestClient(app) as c:
                r = c.post("/api/battles", json={"fighter_ids": ["iron-1"]})
                self.assertEqual(r.status_code, 422)  # pydantic min 2
                r = c.post("/api/battles",
                           json={"fighter_ids": ["iron-1", "not-a-bot"]})
                self.assertEqual(r.status_code, 400)
                r = c.post("/api/battles",
                           json={"fighter_ids": ["iron-1"] * 9})
                self.assertEqual(r.status_code, 422)  # pydantic max 8

    def test_exhibition_flag(self):
        with temp_app() as app:
            with TestClient(app) as c:
                r = c.post("/api/battles", json={
                    "fighter_ids": ["iron-1", "hawk-2"],
                    "seed": 7, "exhibition": True,
                })
                self.assertEqual(r.status_code, 202)
                battle_id = r.json()["id"]
                self.assertTrue(r.json()["exhibition"])
                self.wait_finished(c, app, battle_id)
                r = c.get("/api/battles", params={"exhibition": "true"})
                self.assertTrue(any(b["id"] == battle_id
                                    for b in r.json()["battles"]))

    def test_battle_uses_settings_max_ticks(self):
        with temp_app() as app:
            with TestClient(app) as c:
                c.put("/api/admin/settings", json={"fight_max_ticks": 50},
                      headers=ADMIN_HEADERS)
                r = c.post("/api/battles", json={
                    "fighter_ids": ["aegis-4", "aegis-4"], "seed": 1})
                self.assertEqual(r.status_code, 202)
                detail = self.wait_finished(c, app, r.json()["id"])
                self.assertLessEqual(detail["result"]["ticks"], 50)

    # ---------------------------------------------------------------- hires
    def test_hire_and_duplicate_rejected(self):
        with temp_app() as app:
            with TestClient(app) as c:
                r = c.post("/api/battles", json={
                    "fighter_ids": ["iron-1", "hawk-2"], "seed": 42})
                battle_id = r.json()["id"]

                r = c.post(f"/api/battles/{battle_id}/hire",
                           json={"fighter_id": "iron-1", "wallet": "Wallet111"})
                self.assertEqual(r.status_code, 201)
                hire = r.json()
                self.assertEqual(hire["fighter_id"], "iron-1")
                self.assertEqual(hire["wallet"], "Wallet111")
                self.assertEqual(hire["payment_status"], "mock")
                fee = c.get("/api/settings").json()["hire_fee_sol"]
                self.assertEqual(hire["fee_sol"], fee)

                # duplicate wallet in same battle -> 409
                r = c.post(f"/api/battles/{battle_id}/hire",
                           json={"fighter_id": "hawk-2", "wallet": "Wallet111"})
                self.assertEqual(r.status_code, 409)
                # different wallet is fine
                r = c.post(f"/api/battles/{battle_id}/hire",
                           json={"fighter_id": "hawk-2", "wallet": "Wallet222"})
                self.assertEqual(r.status_code, 201)

                r = c.get(f"/api/battles/{battle_id}/hires")
                self.assertEqual(len(r.json()["hires"]), 2)

                # unknown battle / unknown fighter
                r = c.post("/api/battles/nope/hire",
                           json={"fighter_id": "iron-1", "wallet": "W"})
                self.assertEqual(r.status_code, 404)
                r = c.post(f"/api/battles/{battle_id}/hire",
                           json={"fighter_id": "nope", "wallet": "W"})
                self.assertEqual(r.status_code, 400)

    # ------------------------------------------------------------ websocket
    def test_ws_replay_finished_battle(self):
        with temp_app() as app:
            with TestClient(app) as c:
                r = c.post("/api/battles", json={
                    "fighter_ids": ["iron-1", "hawk-2"], "seed": 42})
                battle_id = r.json()["id"]
                detail = self.wait_finished(c, app, battle_id)

                seen_snapshots = 0
                result = None
                with c.websocket_connect(f"/ws/battles/{battle_id}") as ws:
                    info = ws.receive_json()
                    self.assertEqual(info["type"], "info")
                    self.assertEqual(info["status"], "replay")
                    while True:
                        msg = ws.receive_json()
                        if msg["type"] == "snapshot":
                            seen_snapshots += 1
                            self.assertIn("tick", msg)
                            self.assertIn("fighters", msg)
                            self.assertIn("projectiles", msg)
                        elif msg["type"] == "done":
                            result = msg["result"]
                            break
                self.assertEqual(seen_snapshots, len(detail["snapshots"]))
                self.assertEqual(result["winner"], detail["result"]["winner"])

    def test_ws_unknown_battle(self):
        with temp_app() as app:
            with TestClient(app) as c:
                try:
                    with c.websocket_connect("/ws/battles/nope"):
                        self.fail("should not connect")
                except Exception:
                    pass  # 4404 close is the expected outcome


if __name__ == "__main__":
    unittest.main()
