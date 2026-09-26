"""Phase 5 betting tests (FastAPI TestClient + direct settlement math).

Parimutuel math is tested against HAND-COMPUTED expectations, not against
a re-implementation of the formula.
"""

import json
import os
import sys
import tempfile
import time
import unittest
from contextlib import contextmanager
from datetime import datetime, timezone

os.environ["ADMIN_TOKEN"] = "test-admin-token"
os.environ["ARENA_BETTING_WINDOW_SEC"] = "0"  # no betting-window wait in tests
os.environ["ARENA_PLAYBACK_TICK_MS"] = "0"  # no pacing in WS replays

from fastapi.testclient import TestClient  # noqa: E402

from backend.app import create_app  # noqa: E402
from backend.betting import compute_settlement, settle_battle  # noqa: E402

ADMIN_HEADERS = {"X-Admin-Token": "test-admin-token"}


@contextmanager
def temp_app():
    with tempfile.TemporaryDirectory() as tmp:
        yield create_app(data_dir=tmp)


def wait_finished(c, app, battle_id, timeout=30.0):
    deadline = time.time() + timeout
    while time.time() < deadline:
        r = c.get(f"/api/battles/{battle_id}")
        assert r.status_code == 200
        if r.json()["status"] == "finished":
            while time.time() < deadline:
                # background thread must finish ALL db writes (incl. the
                # phase 5 settlement) before the temp dir is cleaned up
                if app.state.runner.get_live(battle_id) is None:
                    return r.json()
                time.sleep(0.02)
            break
        time.sleep(0.05)
    raise AssertionError(f"battle {battle_id} did not finish in {timeout}s")


def make_battle(c, **kw):
    body = {"fighter_ids": ["iron-1", "hawk-2"], "seed": 42}
    body.update(kw)
    r = c.post("/api/battles", json=body)
    assert r.status_code == 202, r.text
    return r.json()["id"]


def fake_battle_row(db, battle_id, engine_ids=("iron-1", "hawk-2")):
    db.create_battle({
        "id": battle_id,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "status": "finished",
        "seed": 1,
        "exhibition": 0,
        "fighter_ids": json.dumps(list(engine_ids)),
        "registry_ids": json.dumps([e.split("#")[0] for e in engine_ids]),
        "playback_speed": 1.0,
        "hire_fee_sol": 0.10,
    })


class BettingApiTest(unittest.TestCase):
    # ------------------------------------------------------------ placement
    def test_place_bet_contract(self):
        with temp_app() as app:
            with TestClient(app) as c:
                battle_id = make_battle(c)
                r = c.post(f"/api/battles/{battle_id}/bets", json={
                    "fighter_id": "hawk-2", "wallet": "WalletAAA",
                    "amount_sol": 1.5})
                self.assertEqual(r.status_code, 201, r.text)
                bet = r.json()
                # EXACT contract shape
                self.assertEqual(
                    sorted(bet.keys()),
                    ["amount_sol", "battle_id", "created_at", "fighter_id",
                     "id", "payment_status", "wallet"])
                self.assertEqual(bet["battle_id"], battle_id)
                self.assertEqual(bet["fighter_id"], "hawk-2")
                self.assertEqual(bet["wallet"], "WalletAAA")
                self.assertEqual(bet["amount_sol"], 1.5)
                self.assertTrue(bet["created_at"])
                # mock mode by default: no real money moves
                self.assertEqual(bet["payment_status"], "mock")
                wait_finished(c, app, battle_id)

    def test_place_bet_validation(self):
        with temp_app() as app:
            with TestClient(app) as c:
                battle_id = make_battle(c)
                # unknown battle -> 404
                r = c.post("/api/battles/nope/bets", json={
                    "fighter_id": "hawk-2", "wallet": "W", "amount_sol": 1})
                self.assertEqual(r.status_code, 404)
                # unknown fighter -> 400
                r = c.post(f"/api/battles/{battle_id}/bets", json={
                    "fighter_id": "nope-9", "wallet": "W", "amount_sol": 1})
                self.assertEqual(r.status_code, 400)
                # registry id is not an engine id -> 400 (contract is
                # engine ids only); covered implicitly: "hawk-2" IS the
                # engine id here, so use a genuinely absent one above.
                # zero / negative amount -> 400 (not 422)
                for bad in (0, -1.5):
                    r = c.post(f"/api/battles/{battle_id}/bets", json={
                        "fighter_id": "hawk-2", "wallet": "W",
                        "amount_sol": bad})
                    self.assertEqual(r.status_code, 400, bad)
                # empty wallet -> 400
                r = c.post(f"/api/battles/{battle_id}/bets", json={
                    "fighter_id": "hawk-2", "wallet": "  ",
                    "amount_sol": 1})
                self.assertEqual(r.status_code, 400)
                wait_finished(c, app, battle_id)

    def test_exhibition_bet_refused(self):
        with temp_app() as app:
            with TestClient(app) as c:
                battle_id = make_battle(c, exhibition=True)
                r = c.post(f"/api/battles/{battle_id}/bets", json={
                    "fighter_id": "hawk-2", "wallet": "W", "amount_sol": 1})
                self.assertEqual(r.status_code, 400)
                wait_finished(c, app, battle_id)

    def test_finished_battle_bet_refused(self):
        with temp_app() as app:
            with TestClient(app) as c:
                c.put("/api/admin/settings", json={"fight_max_ticks": 30},
                      headers=ADMIN_HEADERS)
                battle_id = make_battle(c)
                wait_finished(c, app, battle_id)
                r = c.post(f"/api/battles/{battle_id}/bets", json={
                    "fighter_id": "hawk-2", "wallet": "W", "amount_sol": 1})
                self.assertEqual(r.status_code, 400)

    def test_same_wallet_may_bet_repeatedly(self):
        # Unlike hires (409 on duplicate wallet), bets are parimutuel:
        # one wallet can place several bets.
        with temp_app() as app:
            with TestClient(app) as c:
                battle_id = make_battle(c)
                for fid, amt in (("hawk-2", 1.0), ("iron-1", 2.0)):
                    r = c.post(f"/api/battles/{battle_id}/bets", json={
                        "fighter_id": fid, "wallet": "WalletAAA",
                        "amount_sol": amt})
                    self.assertEqual(r.status_code, 201, r.text)
                r = c.get(f"/api/battles/{battle_id}/bets",
                          params={"wallet": "WalletAAA"})
                self.assertEqual(len(r.json()["bets"]), 2)
                wait_finished(c, app, battle_id)

    def test_duplicate_hire_rule_unchanged(self):
        with temp_app() as app:
            with TestClient(app) as c:
                battle_id = make_battle(c)
                r = c.post(f"/api/battles/{battle_id}/hire",
                           json={"fighter_id": "iron-1", "wallet": "W1"})
                self.assertEqual(r.status_code, 201)
                r = c.post(f"/api/battles/{battle_id}/hire",
                           json={"fighter_id": "hawk-2", "wallet": "W1"})
                self.assertEqual(r.status_code, 409)
                wait_finished(c, app, battle_id)

    # ----------------------------------------------------------------- pool
    def test_pool_math_and_live_house_cut(self):
        with temp_app() as app:
            with TestClient(app) as c:
                battle_id = make_battle(c)
                # unknown battle -> 404
                r = c.get("/api/battles/nope/pool")
                self.assertEqual(r.status_code, 404)

                bets = [("hawk-2", "WA", 1.0), ("hawk-2", "WB", 2.5),
                        ("iron-1", "WC", 0.5)]
                for fid, w, amt in bets:
                    r = c.post(f"/api/battles/{battle_id}/bets", json={
                        "fighter_id": fid, "wallet": w, "amount_sol": amt})
                    self.assertEqual(r.status_code, 201)
                r = c.get(f"/api/battles/{battle_id}/pool")
                self.assertEqual(r.status_code, 200)
                pool = r.json()
                self.assertEqual(pool["battle_id"], battle_id)
                self.assertEqual(pool["pools"], {"iron-1": 0.5,
                                                 "hawk-2": 3.5})
                self.assertEqual(pool["total_sol"], 4.0)
                self.assertEqual(pool["bet_count"], 3)
                # house_cut_pct read live from settings (default 5.0)
                self.assertEqual(pool["house_cut_pct"], 5.0)
                c.put("/api/admin/settings",
                      json={"betting_house_cut_pct": 12.5},
                      headers=ADMIN_HEADERS)
                r = c.get(f"/api/battles/{battle_id}/pool")
                self.assertEqual(r.json()["house_cut_pct"], 12.5)
                wait_finished(c, app, battle_id)

    def test_bets_list_and_wallet_filter(self):
        with temp_app() as app:
            with TestClient(app) as c:
                battle_id = make_battle(c)
                r = c.get("/api/battles/nope/bets")
                self.assertEqual(r.status_code, 404)
                c.post(f"/api/battles/{battle_id}/bets", json={
                    "fighter_id": "hawk-2", "wallet": "WA", "amount_sol": 1})
                c.post(f"/api/battles/{battle_id}/bets", json={
                    "fighter_id": "iron-1", "wallet": "WB", "amount_sol": 2})
                r = c.get(f"/api/battles/{battle_id}/bets")
                self.assertEqual(len(r.json()["bets"]), 2)
                r = c.get(f"/api/battles/{battle_id}/bets",
                          params={"wallet": "WA"})
                bets = r.json()["bets"]
                self.assertEqual(len(bets), 1)
                self.assertEqual(bets[0]["wallet"], "WA")
                wait_finished(c, app, battle_id)


class BettingWindowTest(unittest.TestCase):
    """A new battle sits in 'open' status for betting_window_sec so
    spectators get a real window to bet before the engine runs (it
    completes in milliseconds, so without the wait there is effectively
    no time to bet)."""

    def test_open_then_bet_then_finish(self):
        # The module disables the window via env for the other tests;
        # drop it here so the admin-set value takes effect.
        env_key = "ARENA_BETTING_WINDOW_SEC"
        saved = os.environ.pop(env_key, None)
        try:
            with temp_app() as app:
                with TestClient(app) as c:
                    r = c.put("/api/admin/settings",
                              json={"betting_window_sec": 2},
                              headers=ADMIN_HEADERS)
                    assert r.status_code == 200, r.text
                    battle_id = make_battle(c)
                    # Engine has not started: battle is open for betting.
                    r = c.get(f"/api/battles/{battle_id}")
                    self.assertEqual(r.json()["status"], "open")
                    # A bet placed during the window is accepted.
                    r = c.post(f"/api/battles/{battle_id}/bets", json={
                        "fighter_id": "hawk-2", "wallet": "WA",
                        "amount_sol": 1.0})
                    self.assertEqual(r.status_code, 201, r.text)
                    # After the window the engine runs, the battle finishes,
                    # and the bet settles.
                    detail = wait_finished(c, app, battle_id, timeout=30.0)
                    self.assertEqual(detail["status"], "finished")
                    s = app.state.db.get_settlement(battle_id)
                    self.assertIsNotNone(s, "settlement row must exist")
                    self.assertIn(s["status"],
                                  ("settled", "refunded_draw",
                                   "refunded_no_winner_bets"))
        finally:
            if saved is not None:
                os.environ[env_key] = saved


class SettlementMathTest(unittest.TestCase):
    """Hand-computed parimutuel expectations, settled directly."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.app = create_app(data_dir=self.tmp.name)
        self.db = self.app.state.db

    def tearDown(self):
        self.tmp.cleanup()

    def place(self, battle_id, fighter_id, wallet, amount):
        return self.db.create_bet(battle_id, fighter_id, wallet, amount)

    # ------------------------------------------------------- hand-computed
    def test_settled_pool_hand_computed(self):
        # Stakes: A 1.0 on iron-1 (loser), B 3.0 / C 1.0 / D 2.0 on hawk-2.
        # Winner hawk-2, house cut 10%.
        # total = 7.0; cut = 0.7; payout pool = 6.3; staked on winner = 6.0
        # B: 6.3 x 3/6 = 3.15 | C: 6.3 x 1/6 = 1.05
        # D: 6.3 x 2/6 = 2.10 | A: 0
        bid = "settle-hand"
        fake_battle_row(self.db, bid)
        bA = self.place(bid, "iron-1", "WA", 1.0)
        bB = self.place(bid, "hawk-2", "WB", 3.0)
        bC = self.place(bid, "hawk-2", "WC", 1.0)
        bD = self.place(bid, "hawk-2", "WA", 2.0)

        res = settle_battle(self.db, bid,
                            {"winner": "hawk-2", "draw": False},
                            house_cut_pct=10.0, betting_live=False)

        self.assertEqual(res["status"], "settled")
        self.assertAlmostEqual(res["total_bets_sol"], 7.0, places=9)
        self.assertAlmostEqual(res["house_cut_sol"], 0.7, places=9)
        self.assertAlmostEqual(res["payout_pool_sol"], 6.3, places=9)
        self.assertAlmostEqual(res["payouts"][bA["id"]], 0.0, places=9)
        self.assertAlmostEqual(res["payouts"][bB["id"]], 3.15, places=9)
        self.assertAlmostEqual(res["payouts"][bC["id"]], 1.05, places=9)
        self.assertAlmostEqual(res["payouts"][bD["id"]], 2.10, places=9)
        # winners' payouts + house cut == total stakes (up to rounding)
        self.assertAlmostEqual(
            sum(res["payouts"].values()) + res["house_cut_sol"],
            res["total_bets_sol"], places=6)

        # settlement row persisted
        s = self.db.get_settlement(bid)
        self.assertEqual(s["status"], "settled")
        self.assertEqual(s["winner"], "hawk-2")
        self.assertEqual(s["bet_count"], 4)
        self.assertAlmostEqual(s["house_cut_sol"], 0.7, places=9)

        # per-bet payout ledger
        payouts = {p["bet_id"]: p for p in self.db.list_payouts(bid)}
        self.assertEqual(payouts[bA["id"]]["outcome"], "lose")
        self.assertEqual(payouts[bB["id"]]["outcome"], "win")
        self.assertAlmostEqual(payouts[bD["id"]]["payout_sol"], 2.10,
                               places=9)

        # house cut landed in the treasury ledger as source='betting'
        totals = self.db.fee_totals()
        self.assertAlmostEqual(totals["betting_sol"], 0.7, places=9)

    def test_house_cut_correctness_other_pct(self):
        # total 4.0, cut 5% -> 0.2; pool 3.8; winner stake 3.0
        # sole winner bettor (3.0) gets the whole pool: 3.8
        bid = "settle-cut5"
        fake_battle_row(self.db, bid)
        bW = self.place(bid, "hawk-2", "W", 3.0)
        bL = self.place(bid, "iron-1", "W2", 1.0)
        res = settle_battle(self.db, bid,
                            {"winner": "hawk-2", "draw": False},
                            house_cut_pct=5.0, betting_live=False)
        self.assertAlmostEqual(res["house_cut_sol"], 0.2, places=9)
        self.assertAlmostEqual(res["payouts"][bW["id"]], 3.8, places=9)
        self.assertAlmostEqual(res["payouts"][bL["id"]], 0.0, places=9)

    def test_draw_full_refunds_no_cut(self):
        bid = "settle-draw"
        fake_battle_row(self.db, bid)
        b1 = self.place(bid, "hawk-2", "WA", 3.0)
        b2 = self.place(bid, "iron-1", "WB", 1.5)
        res = settle_battle(self.db, bid,
                            {"winner": None, "draw": True},
                            house_cut_pct=10.0, betting_live=False)
        self.assertEqual(res["status"], "refunded_draw")
        self.assertAlmostEqual(res["payouts"][b1["id"]], 3.0, places=9)
        self.assertAlmostEqual(res["payouts"][b2["id"]], 1.5, places=9)
        self.assertEqual(res["house_cut_sol"], 0.0)
        payouts = {p["bet_id"]: p for p in self.db.list_payouts(bid)}
        self.assertEqual(payouts[b1["id"]]["outcome"], "refund")
        # no house cut -> no treasury fee event
        self.assertEqual(self.db.fee_totals()["betting_sol"], 0.0)
        self.assertEqual(self.db.get_settlement(bid)["status"],
                         "refunded_draw")

    def test_no_winning_bets_full_refunds_no_cut(self):
        bid = "settle-nowin"
        fake_battle_row(self.db, bid)
        b1 = self.place(bid, "iron-1", "WA", 2.0)
        b2 = self.place(bid, "iron-1", "WB", 2.0)
        res = settle_battle(self.db, bid,
                            {"winner": "hawk-2", "draw": False},
                            house_cut_pct=10.0, betting_live=False)
        self.assertEqual(res["status"], "refunded_no_winner_bets")
        self.assertAlmostEqual(res["payouts"][b1["id"]], 2.0, places=9)
        self.assertAlmostEqual(res["payouts"][b2["id"]], 2.0, places=9)
        self.assertEqual(res["house_cut_sol"], 0.0)
        self.assertEqual(self.db.fee_totals()["betting_sol"], 0.0)

    def test_zero_bets_no_settlement_record(self):
        bid = "settle-empty"
        fake_battle_row(self.db, bid)
        res = settle_battle(self.db, bid,
                            {"winner": "hawk-2", "draw": False},
                            house_cut_pct=10.0, betting_live=False)
        self.assertIsNone(res)
        self.assertIsNone(self.db.get_settlement(bid))

    def test_benched_fighter_bets_lose(self):
        # A benched/crashed fighter can never be the winner: bets on it
        # lose exactly like any other losing bet.
        bid = "settle-benched"
        fake_battle_row(self.db, bid)
        bBenched = self.place(bid, "hawk-2", "WA", 2.0)  # the "crashed" one
        bWin = self.place(bid, "iron-1", "WB", 2.0)
        res = settle_battle(self.db, bid,
                            {"winner": "iron-1", "draw": False},
                            house_cut_pct=10.0, betting_live=False)
        self.assertEqual(res["status"], "settled")
        self.assertAlmostEqual(res["payouts"][bBenched["id"]], 0.0, places=9)
        self.assertEqual(res["outcomes"][bBenched["id"]], "lose")
        # winner-takes-pool math still exact: total 4.0, cut 0.4, pool 3.6
        self.assertAlmostEqual(res["payouts"][bWin["id"]], 3.6, places=9)

    def test_compute_settlement_pure_function(self):
        # no DB needed: the math is a pure function
        bets = [{"id": 1, "fighter_id": "a", "amount_sol": 1.0},
                {"id": 2, "fighter_id": "b", "amount_sol": 1.0}]
        res = compute_settlement(bets, "a", False, 0.0)
        self.assertEqual(res["status"], "settled")
        self.assertAlmostEqual(res["payouts"][1], 2.0, places=9)
        self.assertAlmostEqual(res["payouts"][2], 0.0, places=9)
        self.assertEqual(res["house_cut_sol"], 0.0)


class TreasuryLedgerTest(unittest.TestCase):
    def test_hire_fees_logged_and_totals(self):
        with temp_app() as app:
            with TestClient(app) as c:
                # admin auth enforced
                r = c.get("/api/treasury/fees")
                self.assertEqual(r.status_code, 401)
                r = c.get("/api/treasury/fees",
                          headers={"X-Admin-Token": "nope"})
                self.assertEqual(r.status_code, 401)

                battle_id = make_battle(c)
                fee = c.get("/api/settings").json()["hire_fee_sol"]
                c.post(f"/api/battles/{battle_id}/hire",
                       json={"fighter_id": "iron-1", "wallet": "H1"})
                c.post(f"/api/battles/{battle_id}/hire",
                       json={"fighter_id": "hawk-2", "wallet": "H2"})

                r = c.get("/api/treasury/fees", headers=ADMIN_HEADERS)
                self.assertEqual(r.status_code, 200)
                body = r.json()
                totals = body["totals"]
                self.assertAlmostEqual(totals["hire_sol"], 2 * fee, places=9)
                self.assertEqual(totals["betting_sol"], 0.0)
                self.assertAlmostEqual(totals["total_sol"], 2 * fee, places=9)
                events = body["events"]
                self.assertEqual(len(events), 2)
                for e in events:
                    self.assertEqual(e["source"], "hire")
                    self.assertEqual(e["battle_id"], battle_id)
                    self.assertAlmostEqual(e["amount_sol"], fee, places=9)
                wait_finished(c, app, battle_id)

    def test_betting_cut_combines_with_hire_fees(self):
        with temp_app() as app:
            with TestClient(app) as c:
                c.put("/api/admin/settings",
                      json={"betting_house_cut_pct": 10.0},
                      headers=ADMIN_HEADERS)
                # NOTE: 30 ticks here used to race the bet placement below:
                # the background battle could finish before the three bet
                # POSTs landed, flaking with 400 ("betting is closed").
                # 300 ticks keeps the test fast (~0.2s) while giving the
                # bets an unlosable head start. No assertion changed.
                c.put("/api/admin/settings", json={"fight_max_ticks": 300},
                      headers=ADMIN_HEADERS)
                battle_id = make_battle(c)
                fee = c.get("/api/settings").json()["hire_fee_sol"]
                c.post(f"/api/battles/{battle_id}/hire",
                       json={"fighter_id": "iron-1", "wallet": "H1"})
                # 5.0 total stakes -> cut will be 0.5 whatever the winner
                for w, amt in (("WA", 2.0), ("WB", 2.0), ("WC", 1.0)):
                    r = c.post(f"/api/battles/{battle_id}/bets", json={
                        "fighter_id": "hawk-2", "wallet": w,
                        "amount_sol": amt})
                    self.assertEqual(r.status_code, 201)
                wait_finished(c, app, battle_id)

                detail = c.get(f"/api/battles/{battle_id}").json()
                winner = detail["result"]["winner"]
                draw = detail["result"]["draw"]
                r = c.get("/api/treasury/fees", headers=ADMIN_HEADERS)
                totals = r.json()["totals"]
                self.assertAlmostEqual(totals["hire_sol"], fee, places=9)
                if not draw:
                    # hawk-2 may or may not have won; cut exists only if
                    # the winner had bets on it
                    s = app.state.db.get_settlement(battle_id)
                    if s["status"] == "settled":
                        self.assertAlmostEqual(totals["betting_sol"], 0.5,
                                               places=9)
                    else:
                        self.assertEqual(totals["betting_sol"], 0.0)
                else:
                    self.assertEqual(totals["betting_sol"], 0.0)
                self.assertAlmostEqual(
                    totals["total_sol"],
                    totals["hire_sol"] + totals["betting_sol"], places=9)


class SettlementHookTest(unittest.TestCase):
    """End-to-end: bets placed on a live battle settle when it finishes."""

    def test_battle_finish_triggers_settlement(self):
        with temp_app() as app:
            with TestClient(app) as c:
                c.put("/api/admin/settings",
                      json={"betting_house_cut_pct": 10.0},
                      headers=ADMIN_HEADERS)
                # 5-fighter royale: long enough to place bets before finish
                r = c.post("/api/battles", json={
                    "fighter_ids": ["iron-1", "hawk-2", "aegis-4",
                                    "jackal-5", "wasp-6"],
                    "seed": 42})
                self.assertEqual(r.status_code, 202)
                battle_id = r.json()["id"]
                engine_ids = r.json()["fighter_ids"]

                stakes = [("WA", engine_ids[0], 1.0),
                          ("WB", engine_ids[1], 2.0),
                          ("WC", engine_ids[1], 3.0)]
                for wallet, fid, amt in stakes:
                    r = c.post(f"/api/battles/{battle_id}/bets", json={
                        "fighter_id": fid, "wallet": wallet,
                        "amount_sol": amt})
                    self.assertEqual(r.status_code, 201, r.text)

                detail = wait_finished(c, app, battle_id)
                winner = detail["result"]["winner"]
                draw = detail["result"]["draw"]
                self.assertIsNotNone(
                    app.state.db.get_settlement(battle_id),
                    "settlement row must exist after battle finish")

                # hand-verify the ledger from the actual result
                total = 6.0
                s = app.state.db.get_settlement(battle_id)
                payouts = {p["wallet"]: (p["payout_sol"], p["outcome"])
                           for p in app.state.db.list_payouts(battle_id)}
                self.assertEqual(len(payouts), 3)
                if draw:
                    self.assertEqual(s["status"], "refunded_draw")
                    for wallet, fid, amt in stakes:
                        self.assertAlmostEqual(payouts[wallet][0], amt,
                                               places=9)
                        self.assertEqual(payouts[wallet][1], "refund")
                    self.assertEqual(s["house_cut_sol"], 0.0)
                else:
                    winner_stake = sum(amt for _, fid, amt in stakes
                                       if fid == winner)
                    if winner_stake > 0:
                        self.assertEqual(s["status"], "settled")
                        cut, pool = 0.6, 5.4  # 10% of 6.0
                        self.assertAlmostEqual(s["house_cut_sol"], cut,
                                               places=9)
                        for wallet, fid, amt in stakes:
                            if fid == winner:
                                exp = round(pool * (amt / winner_stake), 9)
                                self.assertAlmostEqual(payouts[wallet][0],
                                                       exp, places=9)
                                self.assertEqual(payouts[wallet][1], "win")
                            else:
                                self.assertEqual(payouts[wallet][0], 0.0)
                                self.assertEqual(payouts[wallet][1], "lose")
                        # ledger invariant: payouts + cut == total stakes
                        self.assertAlmostEqual(
                            sum(p[0] for p in payouts.values()) + cut,
                            total, places=6)
                    else:
                        self.assertEqual(
                            s["status"], "refunded_no_winner_bets")
                        for wallet, fid, amt in stakes:
                            self.assertAlmostEqual(payouts[wallet][0], amt,
                                                   places=9)


class MockModeTest(unittest.TestCase):
    def test_solana_never_imported_in_mock(self):
        with temp_app() as app:
            with TestClient(app) as c:
                battle_id = make_battle(c)
                c.post(f"/api/battles/{battle_id}/bets", json={
                    "fighter_id": "hawk-2", "wallet": "W", "amount_sol": 1})
                c.get(f"/api/battles/{battle_id}/pool")
                c.get(f"/api/battles/{battle_id}/bets")
                wait_finished(c, app, battle_id)
        # The hard rule: mock mode must never pull in the chain libraries.
        # (backend.escrow_live itself is import-safe; its solana/solders
        # imports are all function-local to the live path.)
        leaked = [m for m in sys.modules
                  if m == "solana" or m.startswith("solana.")
                  or m == "solders" or m.startswith("solders.")]
        self.assertEqual(leaked, [], f"chain libs imported in mock: {leaked}")

    def test_live_mode_without_config_is_503(self):
        # betting_live=true with no escrow config -> clear 503, not a
        # silent mock bet and not a crash.
        with temp_app() as app:
            with TestClient(app) as c:
                c.put("/api/admin/settings", json={"betting_live": True},
                      headers=ADMIN_HEADERS)
                try:
                    battle_id = make_battle(c)
                    r = c.post(f"/api/battles/{battle_id}/bets", json={
                        "fighter_id": "hawk-2", "wallet": "W",
                        "amount_sol": 1})
                    self.assertEqual(r.status_code, 503)
                    # no bet was recorded
                    r = c.get(f"/api/battles/{battle_id}/bets")
                    self.assertEqual(r.json()["bets"], [])
                finally:
                    c.put("/api/admin/settings",
                          json={"betting_live": False},
                          headers=ADMIN_HEADERS)
                wait_finished(c, app, battle_id)


if __name__ == "__main__":
    unittest.main()
