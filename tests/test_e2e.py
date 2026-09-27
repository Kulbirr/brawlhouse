"""Phase 8: full end-to-end money-loop test.

One repeatable script that exercises the ENTIRE v1 money loop headlessly,
against an isolated temp data dir (the real ``data/`` dir is never touched):

  admin settings -> battle create -> hires (2 wallets) -> bets (3 wallets)
  -> battle finish -> parimutuel settlement (hand-computed expectations)
  -> treasury fee ledger -> buyback cycle in-process (mock) -> public
  treasury dashboard -> frontend serving + admin-page privacy.

The parimutuel expectations are computed in this file from the known stakes
and the recorded winner, NOT by reusing backend.betting's math. Lamport
rounding (9 decimals) is applied exactly as the product spec documents.
"""

import os
import sys
import tempfile
import time
import unittest
import urllib.request
from contextlib import contextmanager

os.environ["ADMIN_TOKEN"] = "test-admin-token"
os.environ["ARENA_BETTING_WINDOW_SEC"] = "0"  # no betting-window wait in tests
os.environ["ARENA_PLAYBACK_TICK_MS"] = "0"  # no WS pacing in tests

import bot.buyback as bb  # noqa: E402
from backend.app import create_app  # noqa: E402
from backend.settings_store import PRIVATE_FIELDS  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

ADMIN_HEADERS = {"X-Admin-Token": "test-admin-token"}
PRIVATE = sorted(PRIVATE_FIELDS)

# ---------------------------------------------------------------- fixtures
# Fixed seed: engine run of iron-1,hawk-2,aegis-4,jackal-5 gives a
# deterministic non-draw result (winner hawk-2, 551 ticks). The test pins
# the winner as a determinism regression net and computes payouts from the
# recorded winner dynamically.
FIGHTERS_4 = ["iron-1", "hawk-2", "aegis-4", "jackal-5"]
SEED = 7
EXPECTED_WINNER = "hawk-2"
EXPECTED_ELIMINATION_ORDER = ["jackal-5", "iron-1", "aegis-4"]

HIRE_WALLET_1 = "E2E-HIRE-W1"
HIRE_WALLET_2 = "E2E-HIRE-W2"
HIRE_FEE = 0.1

# 3 bettor wallets, known stakes, across 2 fighters. Total = 6.0 SOL.
STAKES = [
    ("E2E-BET-WA", "hawk-2", 2.0),
    ("E2E-BET-WB", "hawk-2", 1.0),
    ("E2E-BET-WC", "iron-1", 3.0),
]
HOUSE_CUT_PCT = 10.0

BUYBACK_PCT = 50.0
TEAM_PCT = 50.0


def _r9(x):
    """Lamport rounding, per the product spec (independent copy)."""
    return round(float(x), 9)


@contextmanager
def betting_window(seconds=3):
    """Battles need a real 'open' window for bet/hire API tests.

    The module disables the window (ARENA_BETTING_WINDOW_SEC=0) for speed,
    but the API only accepts bets and hires while a battle is 'open', so
    placement tests opt back into a short window here."""
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


@contextmanager
def temp_app():
    with tempfile.TemporaryDirectory() as tmp:
        yield create_app(data_dir=tmp)


def wait_finished(c, app, battle_id, timeout=60.0):
    deadline = time.time() + timeout
    while time.time() < deadline:
        r = c.get(f"/api/battles/{battle_id}", params={"snapshots": False})
        assert r.status_code == 200, r.text
        if r.json()["status"] == "finished":
            while time.time() < deadline:
                # the background thread must finish ALL db writes (incl.
                # settlement) before the temp dir is cleaned up
                if app.state.runner.get_live(battle_id) is None:
                    return c.get(f"/api/battles/{battle_id}",
                                 params={"snapshots": False}).json()
                time.sleep(0.02)
            break
        time.sleep(0.05)
    raise AssertionError(f"battle {battle_id} did not finish in {timeout}s")


def assert_no_private(testcase, payload, where):
    """Recursively assert no PRIVATE_FIELDS key appears in a public payload."""
    if isinstance(payload, dict):
        for k, v in payload.items():
            testcase.assertNotIn(
                k, PRIVATE, f"private field {k!r} leaked in {where}")
            assert_no_private(testcase, v, where)
    elif isinstance(payload, list):
        for item in payload:
            assert_no_private(testcase, item, where)


class EndToEndMoneyLoopTest(unittest.TestCase):
    """The whole v1 money loop, in order, in one test."""

    def test_full_money_loop(self):
        trace = {}
        with betting_window(5), temp_app() as app:
            db = app.state.db
            settings = app.state.settings
            with TestClient(app) as c:
                # -------------------------------------------------- 1. admin
                r = c.put("/api/admin/settings", headers=ADMIN_HEADERS, json={
                    "project_name": "E2E-TEST-PROJECT",
                    "token_ticker": "E2E",
                    "token_mint": "",
                    "hire_fee_sol": 0.1,
                    "betting_house_cut_pct": 10.0,
                    "buyback_pct": 50.0,
                    "team_pct": 50.0,
                    "buyback_enabled": True,
                })
                self.assertEqual(r.status_code, 200, r.text)

                public = c.get("/api/settings").json()
                self.assertEqual(public["project_name"], "E2E-TEST-PROJECT")
                self.assertEqual(public["token_ticker"], "E2E")
                self.assertEqual(public["token_mint"], "")
                self.assertEqual(public["hire_fee_sol"], HIRE_FEE)
                self.assertEqual(public["betting_house_cut_pct"],
                                 HOUSE_CUT_PCT)
                self.assertEqual(public["buyback_pct"], BUYBACK_PCT)
                self.assertEqual(public["team_pct"], TEAM_PCT)
                assert_no_private(self, public, "GET /api/settings")

                # ------------------------------------------------ 2. battle
                r = c.post("/api/battles", json={
                    "fighter_ids": FIGHTERS_4, "seed": SEED})
                self.assertEqual(r.status_code, 202, r.text)
                created = r.json()
                battle_id = created["id"]
                engine_ids = created["fighter_ids"]
                self.assertEqual(engine_ids, FIGHTERS_4)  # unique: no #n suffix
                self.assertEqual(created["status"], "open")
                trace["battle_id"] = battle_id
                assert_no_private(self, created, "POST /api/battles")

                # -------------------------------------------------- 3. hires
                hires = []
                for wallet, fid in ((HIRE_WALLET_1, "iron-1"),
                                    (HIRE_WALLET_2, "hawk-2")):
                    r = c.post(f"/api/battles/{battle_id}/hire", json={
                        "fighter_id": fid, "wallet": wallet})
                    self.assertEqual(r.status_code, 201, r.text)
                    hire = r.json()
                    self.assertEqual(
                        sorted(hire.keys()),
                        ["battle_id", "created_at", "fee_sol", "fighter_id",
                         "id", "payment_status", "wallet"])
                    self.assertEqual(hire["battle_id"], battle_id)
                    self.assertEqual(hire["fighter_id"], fid)
                    self.assertEqual(hire["wallet"], wallet)
                    # fee comes from settings, mock payment recorded
                    self.assertEqual(hire["fee_sol"], HIRE_FEE)
                    self.assertEqual(hire["payment_status"], "mock")
                    hires.append(hire)
                    assert_no_private(self, hire, "POST /api/battles//hire")
                trace["hires"] = [
                    (h["wallet"], h["fighter_id"], h["fee_sol"]) for h in hires]

                # duplicate hire from the same wallet -> 409
                r = c.post(f"/api/battles/{battle_id}/hire", json={
                    "fighter_id": "aegis-4", "wallet": HIRE_WALLET_1})
                self.assertEqual(r.status_code, 409, r.text)

                r = c.get(f"/api/battles/{battle_id}/hires")
                self.assertEqual(r.status_code, 200)
                self.assertEqual(len(r.json()["hires"]), 2)
                assert_no_private(self, r.json(), "GET /api/battles//hires")

                # --------------------------------------------------- 4. bets
                bets = []
                for wallet, fid, amt in STAKES:
                    r = c.post(f"/api/battles/{battle_id}/bets", json={
                        "fighter_id": fid, "wallet": wallet,
                        "amount_sol": amt})
                    self.assertEqual(r.status_code, 201, r.text)
                    bet = r.json()
                    self.assertEqual(
                        sorted(bet.keys()),
                        ["amount_sol", "battle_id", "created_at",
                         "fighter_id", "id", "payment_status", "wallet"])
                    self.assertEqual(bet["battle_id"], battle_id)
                    self.assertEqual(bet["fighter_id"], fid)
                    self.assertEqual(bet["wallet"], wallet)
                    self.assertEqual(bet["amount_sol"], amt)
                    self.assertEqual(bet["payment_status"], "mock")
                    bets.append(bet)
                    assert_no_private(self, bet, "POST /api/battles//bets")
                trace["bets"] = [
                    (b["wallet"], b["fighter_id"], b["amount_sol"])
                    for b in bets]

                # pool totals must match the known stakes exactly
                r = c.get(f"/api/battles/{battle_id}/pool")
                self.assertEqual(r.status_code, 200)
                pool = r.json()
                self.assertEqual(pool["battle_id"], battle_id)
                self.assertEqual(pool["pools"], {
                    "iron-1": 3.0, "hawk-2": 3.0,
                    "aegis-4": 0.0, "jackal-5": 0.0})
                self.assertEqual(pool["total_sol"], 6.0)
                self.assertEqual(pool["bet_count"], 3)
                self.assertEqual(pool["house_cut_pct"], HOUSE_CUT_PCT)
                assert_no_private(self, pool, "GET /api/battles//pool")

                r = c.get(f"/api/battles/{battle_id}/bets")
                self.assertEqual(len(r.json()["bets"]), 3)
                assert_no_private(self, r.json(), "GET /api/battles//bets")

                # records before the fight (to diff afterwards)
                before = {f["id"]: (f["wins"], f["losses"], f["draws"])
                          for f in c.get("/api/fighters").json()["fighters"]}

                # ------------------------------------------- 5. battle finish
                detail = wait_finished(c, app, battle_id)
                assert_no_private(self, detail, "GET /api/battles/{id}")
                result = detail["result"]
                self.assertIsNotNone(result)
                # determinism pin: fixed seed must give the same fight
                self.assertEqual(result["winner"], EXPECTED_WINNER)
                self.assertFalse(result["draw"])
                self.assertEqual(result["reason"], "last_fighter_standing")
                self.assertEqual(result["elimination_order"],
                                 EXPECTED_ELIMINATION_ORDER)
                winner = result["winner"]
                trace["winner"] = winner
                trace["elimination_order"] = result["elimination_order"]
                trace["ticks"] = result["ticks"]

                # fighter W/L records updated: winner +1 win, rest +1 loss
                after = {f["id"]: (f["wins"], f["losses"], f["draws"])
                         for f in c.get("/api/fighters").json()["fighters"]}
                assert_no_private(self, c.get("/api/fighters").json(),
                                  "GET /api/fighters")
                for fid in FIGHTERS_4:
                    bw, bl, bd = before[fid]
                    aw, al, ad = after[fid]
                    if fid == winner:
                        self.assertEqual((aw, al, ad), (bw + 1, bl, bd), fid)
                    else:
                        self.assertEqual((aw, al, ad), (bw, bl + 1, bd), fid)

                # ---------------- settlement: HAND-COMPUTED expectations ---
                # Known stakes: WA 2.0 + WB 1.0 on hawk-2 (winner),
                # WC 3.0 on iron-1 (loser). Total 6.0, cut 10% -> 0.6,
                # payout pool 5.4, winner stake 3.0.
                total = _r9(sum(amt for _, _, amt in STAKES))
                self.assertEqual(total, 6.0)
                expected_cut = _r9(total * HOUSE_CUT_PCT / 100.0)
                expected_pool = _r9(total - expected_cut)
                self.assertEqual(expected_cut, 0.6)
                self.assertEqual(expected_pool, 5.4)
                winner_stake = _r9(sum(
                    amt for _, fid, amt in STAKES if fid == winner))
                self.assertEqual(winner_stake, 3.0)
                expected_payout = {}
                for bet in bets:
                    if bet["fighter_id"] == winner:
                        expected_payout[bet["id"]] = _r9(
                            expected_pool * (_r9(bet["amount_sol"])
                                             / winner_stake))
                    else:
                        expected_payout[bet["id"]] = 0.0
                trace["expected"] = {
                    "total": total, "house_cut": expected_cut,
                    "payout_pool": expected_pool,
                    "payouts": dict(expected_payout)}

                s = db.get_settlement(battle_id)
                self.assertIsNotNone(s, "settlement row must exist")
                self.assertEqual(s["status"], "settled")
                self.assertEqual(s["winner"], winner)
                self.assertEqual(s["bet_count"], 3)
                self.assertEqual(s["total_bets_sol"], total)
                self.assertEqual(s["house_cut_sol"], expected_cut)
                self.assertEqual(s["payout_pool_sol"], expected_pool)

                payouts = {p["bet_id"]: p
                           for p in db.list_payouts(battle_id)}
                self.assertEqual(len(payouts), 3)
                trace["actual_payouts"] = {}
                for bet in bets:
                    p = payouts[bet["id"]]
                    exp = expected_payout[bet["id"]]
                    self.assertEqual(p["payout_sol"], exp,
                                     f"bet {bet['id']} ({bet['wallet']})")
                    if bet["fighter_id"] == winner:
                        self.assertEqual(p["outcome"], "win")
                    else:
                        # losers get exactly 0
                        self.assertEqual(p["outcome"], "lose")
                        self.assertEqual(p["payout_sol"], 0.0)
                    trace["actual_payouts"][bet["wallet"]] = (
                        p["outcome"], p["payout_sol"])
                # ledger invariant: payouts + house cut == total stakes
                self.assertAlmostEqual(
                    sum(p["payout_sol"] for p in payouts.values())
                    + expected_cut, total, places=6)

                # treasury fee events: the betting cut AND both hire fees
                fees = c.get("/api/treasury/fees", headers=ADMIN_HEADERS)
                self.assertEqual(fees.status_code, 200)
                totals = fees.json()["totals"]
                self.assertEqual(totals["hire_sol"], 2 * HIRE_FEE)
                self.assertEqual(totals["betting_sol"], expected_cut)
                self.assertEqual(totals["total_sol"],
                                 2 * HIRE_FEE + expected_cut)
                events = fees.json()["events"]
                by_source = {}
                for e in events:
                    by_source.setdefault(e["source"], []).append(e)
                self.assertEqual(len(by_source["hire"]), 2)
                self.assertEqual(len(by_source["betting"]), 1)
                for e in by_source["hire"]:
                    self.assertEqual(e["amount_sol"], HIRE_FEE)
                    self.assertEqual(e["battle_id"], battle_id)
                self.assertEqual(by_source["betting"][0]["amount_sol"],
                                 expected_cut)
                trace["fee_events"] = {
                    "hire": [e["amount_sol"] for e in by_source["hire"]],
                    "betting": [e["amount_sol"]
                                for e in by_source["betting"]]}

                # --------------------------------------- 6. buyback (mock)
                new_fees = _r9(2 * HIRE_FEE + expected_cut)
                self.assertEqual(new_fees, 0.8)
                expected_sol_spent = _r9(new_fees * BUYBACK_PCT / 100.0)
                self.assertEqual(expected_sol_spent, 0.4)
                expected_tokens = expected_sol_spent * settings.get(
                    "buyback_mock_rate")

                real_urlopen = urllib.request.urlopen

                def blocked(*a, **k):
                    raise AssertionError(
                        "network call attempted in mock mode")

                urllib.request.urlopen = blocked
                try:
                    result1 = bb.run_cycle(settings, db)
                finally:
                    urllib.request.urlopen = real_urlopen
                self.assertEqual(result1["status"], "burned")
                self.assertTrue(result1["dry_run"])
                self.assertEqual(result1["sol_spent"], expected_sol_spent)
                self.assertTrue(result1["buy_tx"].startswith("mock_"))
                self.assertTrue(result1["burn_tx"].startswith("mock_"))
                self.assertEqual(result1["tokens_burned"], expected_tokens)
                # no chain libs pulled in by the mock path
                self.assertNotIn("solana", sys.modules)
                self.assertNotIn("solders", sys.modules)

                burns = db.list_burn_events()
                self.assertEqual(len(burns), 1)
                row = burns[0]
                self.assertEqual(row["sol_spent"], expected_sol_spent)
                self.assertTrue(row["dry_run"])
                self.assertEqual(row["tokens_burned"], expected_tokens)
                trace["burn_row"] = {
                    "id": row["id"], "sol_spent": row["sol_spent"],
                    "tokens_burned": row["tokens_burned"],
                    "dry_run": bool(row["dry_run"]),
                    "buy_tx": row["buy_tx"], "burn_tx": row["burn_tx"]}

                # fee events consumed exactly once
                self.assertEqual(db.unallocated_fees_total(), 0.0)
                team = db.team_totals()
                self.assertEqual(team["allocation_count"], 1)
                self.assertEqual(team["total_team_sol"],
                                 _r9(new_fees * TEAM_PCT / 100.0))

                # second run is a no-op: no double-spend
                result2 = bb.run_cycle(settings, db)
                self.assertEqual(result2["status"], "no_fees")
                self.assertEqual(db.burn_totals()["burn_count"], 1)

                # --------------------------------- 7. public treasury views
                stats = c.get("/api/treasury/stats").json()
                self.assertEqual(
                    set(stats.keys()),
                    {"treasury_balance_sol", "total_fees_sol",
                     "total_burned_tokens", "burn_count", "token_ticker",
                     "project_name", "simulated"})
                self.assertEqual(stats["total_fees_sol"], new_fees)
                self.assertEqual(stats["treasury_balance_sol"], 0.0)
                self.assertEqual(stats["burn_count"], 1)
                self.assertEqual(stats["total_burned_tokens"],
                                 expected_tokens)
                self.assertEqual(stats["token_ticker"], "E2E")
                self.assertEqual(stats["project_name"], "E2E-TEST-PROJECT")
                assert_no_private(self, stats, "GET /api/treasury/stats")

                burns_resp = c.get("/api/treasury/burns").json()
                self.assertEqual(burns_resp["total"], 1)
                self.assertEqual(len(burns_resp["burns"]), 1)
                brow = burns_resp["burns"][0]
                self.assertEqual(brow["sol_spent"], expected_sol_spent)
                self.assertTrue(brow["dry_run"])
                assert_no_private(self, burns_resp,
                                  "GET /api/treasury/burns")

                # ---------------------------------- 8. frontend + admin page
                r = c.get("/")
                self.assertEqual(r.status_code, 200)
                self.assertIn("text/html", r.headers["content-type"])
                self.assertNotIn("admin.html", r.text,
                                 "admin page must stay unlinked from /")

                r = c.get("/admin.html")
                self.assertEqual(r.status_code, 200)
                self.assertIn('<meta name="robots" content="noindex">',
                              r.text)

        print("\n[E2E money-loop trace]")
        for k, v in trace.items():
            print(f"  {k}: {v}")


if __name__ == "__main__":
    unittest.main()
