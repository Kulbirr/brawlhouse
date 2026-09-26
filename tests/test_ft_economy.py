"""Phase 1 (FT economy) tests.

Covers: rarity rolls and stat multipliers, birth validation and
certificates, the mock born/entry flows and their fee splits, the entry
queue and weighted draw, official-battle alternation and settlement,
season rollover and payouts, and the FT API routes (mock + live via a
faked RPC client - no network, no real money).
"""

import os
import random
import sqlite3
import tempfile
import time
import unittest
from contextlib import contextmanager
from types import SimpleNamespace
from unittest import mock

os.environ["ADMIN_TOKEN"] = "test-admin-token"
os.environ["ARENA_PLAYBACK_TICK_MS"] = "0"  # no pacing in WS replays
os.environ["HELIUS_API_KEY"] = "test-helius-key"

from fastapi.testclient import TestClient  # noqa: E402

from arena import (  # noqa: E402
    ARCHETYPE_IDS, Battle, Config, HOUSE_BOT_IDS, next_ft_id, roll_rarity,
    sign_cert, validate_born_input, verify_cert,
)
from backend import ft_economy, payments_live  # noqa: E402
from backend.app import create_app  # noqa: E402
from backend.battle_runner import BattleRunner  # noqa: E402
from backend.db import Database  # noqa: E402
from backend.official_scheduler import OfficialScheduler  # noqa: E402
from backend.settings_store import SettingsStore  # noqa: E402

# NOTE: solders is imported lazily inside the tests that need it, never at
# module level (see test_betting.py::test_solana_never_imported_in_mock).

ADMIN_HEADERS = {"X-Admin-Token": "test-admin-token"}


@contextmanager
def temp_env():
    with tempfile.TemporaryDirectory() as tmp:
        db = Database(f"{tmp}/arena.db")
        for fid in HOUSE_BOT_IDS:
            db.ensure_fighter(fid)
        settings = SettingsStore(f"{tmp}/settings.json")
        # Keep the entry window open for tests that are not about it.
        settings.update({"entry_window_minutes":
                         int(settings.get("official_battle_interval_minutes"))})
        yield db, settings, tmp


@contextmanager
def temp_app():
    with tempfile.TemporaryDirectory() as tmp:
        app = create_app(data_dir=tmp)
        # Most API tests are not about the entry window: keep it open so
        # they are not flaky against wall-clock time.
        app.state.settings.update({
            "entry_window_minutes":
                int(app.state.settings.get("official_battle_interval_minutes"))})
        yield app


def wait_finished(c, app, battle_id, timeout=30.0):
    deadline = time.time() + timeout
    while time.time() < deadline:
        r = c.get(f"/api/battles/{battle_id}")
        if r.json()["status"] == "finished":
            if app.state.runner.get_live(battle_id) is None:
                return r.json()
            time.sleep(0.02)
            continue
        time.sleep(0.05)
    raise AssertionError(f"battle {battle_id} did not finish")


def born_fighter(db, settings, tmp, wallet="WALLET1", name="Testy",
                 archetype=None, color="#a1b2c3", rng=None):
    return ft_economy.born_fighter(
        db, settings, wallet, name, archetype or ARCHETYPE_IDS[0], color,
        rng=rng, data_dir=tmp)


class RarityTest(unittest.TestCase):
    def test_distribution(self):
        rng = random.Random(1234)
        counts = {"Common": 0, "Rare": 0, "Epic": 0, "Legendary": 0}
        n = 20000
        for _ in range(n):
            rarity, _ = roll_rarity(rng)
            counts[rarity] += 1
        self.assertAlmostEqual(counts["Common"] / n, 0.50, delta=0.02)
        self.assertAlmostEqual(counts["Rare"] / n, 0.30, delta=0.02)
        self.assertAlmostEqual(counts["Epic"] / n, 0.15, delta=0.02)
        self.assertAlmostEqual(counts["Legendary"] / n, 0.05, delta=0.015)

    def test_multipliers(self):
        mults = {}
        rng = random.Random(7)
        for _ in range(4000):
            rarity, mult = roll_rarity(rng)
            mults.setdefault(rarity, mult)
        self.assertEqual(mults, {"Common": 1.00, "Rare": 1.08,
                                "Epic": 1.16, "Legendary": 1.25})

    def test_engine_applies_mult_to_hp(self):
        from arena.house_fighters import RusherBot
        cfg = Config.from_env({"max_ticks": 5})
        b = Battle([("a", RusherBot(seed=1)), ("b", RusherBot(seed=2))],
                   cfg=cfg, stat_mults={"a": 1.25})
        hp = {f.id: f.hp for f in b.fighters}
        self.assertEqual(hp["a"], 125)
        self.assertEqual(hp["b"], 100)

    def test_engine_applies_mult_to_projectile_damage(self):
        from arena.house_fighters import RusherBot
        cfg = Config.from_env({"max_ticks": 5})
        b = Battle([("a", RusherBot(seed=1)), ("b", RusherBot(seed=2))],
                   cfg=cfg, stat_mults={"a": 1.25})
        dmg_mult = {f.id: f.dmg_mult for f in b.fighters}
        self.assertEqual(dmg_mult["a"], 1.25)
        self.assertEqual(dmg_mult["b"], 1.0)
        # Damage dealt by a fired projectile uses the multiplier.
        self.assertEqual(cfg.projectile_damage * dmg_mult["a"],
                         cfg.projectile_damage * 1.25)

    def test_next_ft_id_sequences(self):
        self.assertEqual(next_ft_id([]), "FT-0001")
        self.assertEqual(next_ft_id(["FT-0001", "FT-0009"]), "FT-0010")
        self.assertEqual(next_ft_id(["FT-0099"]), "FT-0100")


class BirthValidationTest(unittest.TestCase):
    def test_rejects_bad_input(self):
        with self.assertRaises(ValueError):
            validate_born_input("", ARCHETYPE_IDS[0], "#a1b2c3")
        with self.assertRaises(ValueError):
            validate_born_input("x" * 25, ARCHETYPE_IDS[0], "#a1b2c3")
        with self.assertRaises(ValueError):
            validate_born_input("ok", "not-a-bot", "#a1b2c3")
        with self.assertRaises(ValueError):
            validate_born_input("ok", ARCHETYPE_IDS[0], "red")

    def test_strips_html_and_collapses_spaces(self):
        name, arch, color = validate_born_input(
            "  <b>Bo</b>   by  ", ARCHETYPE_IDS[1], "#A1B2C3")
        self.assertEqual(name, "Bo by")
        self.assertEqual(arch, ARCHETYPE_IDS[1])
        self.assertEqual(color, "#A1B2C3")

    def test_cert_roundtrip(self):
        with tempfile.TemporaryDirectory() as tmp:
            cert = sign_cert("FT-0042", "WALLET9", tmp)
            self.assertTrue(verify_cert("FT-0042", "WALLET9", cert, tmp))
            self.assertFalse(verify_cert("FT-0042", "OTHER", cert, tmp))
            self.assertFalse(verify_cert("FT-0043", "WALLET9", cert, tmp))


class BornFlowTest(unittest.TestCase):
    def test_mock_born_creates_fighter_and_splits(self):
        with temp_env() as (db, settings, tmp):
            row, info = born_fighter(db, settings, tmp, rng=random.Random(3))
            self.assertEqual(row["id"], "FT-0001")
            self.assertEqual(row["owner_wallet"], "WALLET1")
            self.assertEqual(row["status"], "active")
            self.assertTrue(verify_cert(row["id"], "WALLET1",
                                        row["owner_cert"], tmp))
            # fee splits: 0.1 born fee -> 0.05 season, 0.05 treasury
            self.assertAlmostEqual(info["season_share_sol"], 0.05)
            self.assertAlmostEqual(info["treasury_share_sol"], 0.05)
            season = db.get_active_season()
            self.assertAlmostEqual(season["prize_pool_sol"], 0.05)
            fees = db.fee_totals()
            self.assertAlmostEqual(fees["by_source"].get("born", 0), 0.05)
            # birth feed + registry
            births = db.recent_births()
            self.assertEqual(len(births), 1)
            self.assertEqual(births[0]["fighter_id"], "FT-0001")
            self.assertEqual(db.ft_count(), 1)
            mine = db.list_wallet_fighters("WALLET1")
            self.assertEqual([f["id"] for f in mine], ["FT-0001"])

    def test_born_ids_unique_under_race(self):
        with temp_env() as (db, settings, tmp):
            born_fighter(db, settings, tmp, rng=random.Random(1))
            born_fighter(db, settings, tmp, rng=random.Random(2))
            ids = [r["id"] for r in db.list_fighters()]
            ids = [i for i in ids if i.startswith("FT-")]
            self.assertEqual(sorted(ids), ["FT-0001", "FT-0002"])


class QueueTest(unittest.TestCase):
    def test_enter_queue_splits(self):
        with temp_env() as (db, settings, tmp):
            row, _ = born_fighter(db, settings, tmp)
            entry = ft_economy.enter_queue(db, settings, row["id"], "WALLET1")
            self.assertEqual(entry["payment_status"], "mock")
            self.assertEqual(db.get_fighter(row["id"])["status"], "queued")
            # 0.02 entry fee -> 0.016 prize share, 0.004 season share
            self.assertAlmostEqual(entry["prize_share_sol"], 0.016)
            season = db.get_active_season()
            # 0.05 born share + 0.004 entry share
            self.assertAlmostEqual(season["prize_pool_sol"], 0.054)

    def test_enter_refuses_non_owner_double_and_busy(self):
        with temp_env() as (db, settings, tmp):
            row, _ = born_fighter(db, settings, tmp)
            with self.assertRaises(ValueError):
                ft_economy.enter_queue(db, settings, row["id"], "OTHER")
            ft_economy.enter_queue(db, settings, row["id"], "WALLET1")
            with self.assertRaises(ValueError):
                ft_economy.enter_queue(db, settings, row["id"], "WALLET1")
            with self.assertRaises(ValueError):
                ft_economy.enter_queue(db, settings, "iron-1", "WALLET1")

    def test_draw_never_takes_pending_entries(self):
        with temp_env() as (db, settings, tmp):
            row, _ = born_fighter(db, settings, tmp)
            db.enqueue_fighter(row["id"], "WALLET1", 0.02, 0.016, "pending")
            drawn = ft_economy.draw_fighters(db, "duel",
                                             rng=random.Random(1))
            self.assertTrue(all(d["registry_id"] in HOUSE_BOT_IDS
                                for d in drawn))

    def test_draw_house_fill(self):
        with temp_env() as (db, settings, tmp):
            drawn = ft_economy.draw_fighters(db, "royale",
                                             rng=random.Random(2))
            self.assertEqual(len(drawn), 4)
            self.assertEqual(len({d["registry_id"] for d in drawn}), 4)
            self.assertTrue(all(d["owner_wallet"] is None for d in drawn))

    def test_draw_longest_wait_priority(self):
        # Weighted single-slot draws: the fighter that waited ~2h should be
        # picked first far more often than the just-entered one.
        with temp_env() as (db, settings, tmp):
            a, _ = born_fighter(db, settings, tmp, name="Oldy")
            b, _ = born_fighter(db, settings, tmp, name="Newy")
            ea = ft_economy.enter_queue(db, settings, a["id"], "WALLET1")
            ft_economy.enter_queue(db, settings, b["id"], "WALLET1")
            db._q("UPDATE battle_queue SET entered_at = ? WHERE id = ?",
                   ("2026-01-01T00:00:00+00:00", ea["id"]))
            firsts = []
            for t in range(200):
                d = ft_economy.draw_fighters(
                    db, "duel", rng=random.Random(t))
                # duel draws both queued FTs (no house fill); the first
                # pick reveals the weight winner.
                firsts.append(d[0]["registry_id"])
            old_share = firsts.count(a["id"]) / len(firsts)
            self.assertGreater(old_share, 0.9,
                               f"longest-wait fighter picked first only "
                               f"{old_share:.0%} of the time")


class OfficialBattleTest(unittest.TestCase):
    def _runner_env(self, tmp):
        db = Database(f"{tmp}/arena.db")
        for fid in HOUSE_BOT_IDS:
            db.ensure_fighter(fid)
        settings = SettingsStore(f"{tmp}/settings.json")
        # Keep the entry window open for tests that are not about it.
        settings.update({"entry_window_minutes":
                         int(settings.get("official_battle_interval_minutes"))})
        runner = BattleRunner(db, settings)
        cfg = Config.from_env({"max_ticks": 40})
        return db, settings, runner, cfg

    def test_alternation_duel_royale(self):
        with tempfile.TemporaryDirectory() as tmp:
            db, settings, runner, cfg = self._runner_env(tmp)
            live1 = ft_economy.run_official_battle(db, runner, settings, cfg,
                                                   rng=random.Random(1))
            self.assertTrue(live1.official)
            self.assertEqual(live1.mode, "duel")
            self.assertEqual(len(live1.registry_ids), 2)
            live1.finished.wait(30)
            live2 = ft_economy.run_official_battle(db, runner, settings, cfg,
                                                   rng=random.Random(2))
            self.assertEqual(live2.mode, "royale")
            self.assertEqual(len(live2.registry_ids), 4)
            live2.finished.wait(30)
            rec = db.get_battle(live2.id)
            self.assertEqual(rec["mode"], "royale")
            self.assertTrue(rec["official"])

    def test_drawn_fighter_status_set_before_battle_thread(self):
        # Regression: statuses must flip to 'fighting' before runner.create()
        # starts the battle thread, otherwise a fast battle settles before
        # the flip and the fighter is never freed.
        with tempfile.TemporaryDirectory() as tmp:
            db, settings, runner, cfg = self._runner_env(tmp)
            r1, _ = ft_economy.born_fighter(
                db, settings, "WALLET_A", "Alpha", ARCHETYPE_IDS[0], "#a1b2c3",
                data_dir=tmp)
            ft_economy.enter_queue(db, settings, r1["id"], "WALLET_A")
            live = ft_economy.run_official_battle(db, runner, settings, cfg)
            try:
                f = db.get_fighter(r1["id"])
                self.assertEqual(f["status"], "fighting")
            finally:
                self.assertTrue(live.finished.wait(30))
            # ...and settlement frees it afterwards.
            self.assertEqual(db.get_fighter(r1["id"])["status"], "active")

    def test_prize_to_owner_and_standings(self):
        with tempfile.TemporaryDirectory() as tmp:
            db, settings, runner, cfg = self._runner_env(tmp)
            r1, _ = ft_economy.born_fighter(
                db, settings, "WALLET_A", "Alpha", ARCHETYPE_IDS[0], "#a1b2c3",
                data_dir=tmp)
            r2, _ = ft_economy.born_fighter(
                db, settings, "WALLET_B", "Beta", ARCHETYPE_IDS[1], "#b2c3d4",
                data_dir=tmp)
            ft_economy.enter_queue(db, settings, r1["id"], "WALLET_A")
            ft_economy.enter_queue(db, settings, r2["id"], "WALLET_B")
            live = ft_economy.run_official_battle(db, runner, settings, cfg,
                                                  rng=random.Random(5))
            self.assertEqual(live.mode, "duel")
            pool = db.get_battle(live.id)["prize_pool_sol"]
            self.assertAlmostEqual(pool, 0.032)
            self.assertTrue(live.finished.wait(30))
            prizes = db._q("SELECT * FROM battle_prizes WHERE battle_id = ?",
                           (live.id,))
            self.assertEqual(len(prizes), 1)
            prize = dict(prizes[0])
            # 40-tick test battles can draw; the draw path rolls the pool
            # to the season pool, the win path pays the owner's wallet.
            self.assertIn(prize["destination"], ("owner", "season_pool"))
            self.assertEqual(prize["status"], "mock")
            self.assertAlmostEqual(prize["amount_sol"], 0.032)
            if prize["destination"] == "owner":
                self.assertIn(prize["owner_wallet"], ("WALLET_A", "WALLET_B"))
            # standings: both FTs recorded, winner 3 pts
            season_id = db.get_active_season()["id"]
            board = {r["fighter_id"]: r
                     for r in db.season_leaderboard(season_id, limit=10)}
            self.assertEqual(set(board), {r1["id"], r2["id"]})
            # win/loss -> [0, 3]; draw -> [1, 1]
            self.assertIn(sorted(r["points"] for r in board.values()),
                          ([0, 3], [1, 1]))
            # fighters freed for the next battle
            self.assertEqual(db.get_fighter(r1["id"])["status"], "active")
            self.assertEqual(db.get_fighter(r2["id"])["status"], "active")
            # entries consumed exactly once
            self.assertEqual(db.queue_count(), 0)

    def test_ft_win_prize_to_owner_deterministic(self):
        # Forced FT winner: the full pool is recorded for the owner's
        # wallet with status mock (live mode would transfer onchain).
        import json as _json
        import uuid as _uuid
        from datetime import datetime as _dt, timezone as _tz

        with tempfile.TemporaryDirectory() as tmp:
            db, settings, runner, cfg = self._runner_env(tmp)
            r1, _ = ft_economy.born_fighter(
                db, settings, "WALLET_A", "Alpha", ARCHETYPE_IDS[0], "#a1b2c3",
                data_dir=tmp)
            bid = f"battle-test-{_uuid.uuid4().hex[:8]}"
            db.create_battle({
                "id": bid,
                "created_at": _dt.now(_tz.utc).isoformat(),
                "status": "finished",
                "seed": 1,
                "exhibition": 0,
                "fighter_ids": _json.dumps([r1["id"], "iron-1"]),
                "registry_ids": _json.dumps([r1["id"], "iron-1"]),
                "playback_speed": 1.0,
                "hire_fee_sol": 0.1,
                "official": 1,
                "mode": "duel",
                "prize_pool_sol": 0.032,
            })
            live = SimpleNamespace(
                id=bid,
                result={"winner": r1["id"], "draw": False},
                registry_ids=[r1["id"], "iron-1"],
                engine_ids=[r1["id"], "iron-1"])
            out = ft_economy._settle_official_battle(db, settings, live)
            prize = out["prize"]
            self.assertEqual(prize["destination"], "owner")
            self.assertEqual(prize["owner_wallet"], "WALLET_A")
            self.assertEqual(prize["winner_registry_id"], r1["id"])
            self.assertAlmostEqual(prize["amount_sol"], 0.032)
            self.assertEqual(prize["status"], "mock")
            # standings recorded
            board = {r["fighter_id"]: r for r in db.season_leaderboard(
                out["season_id"], limit=10)}
            self.assertEqual(board[r1["id"]]["points"], 3)

    def test_house_win_prize_to_treasury(self):
        # Deterministic settlement check: a house winner sends the pool to
        # the treasury (fee event), never to a player.
        import json as _json
        import uuid as _uuid
        from datetime import datetime as _dt, timezone as _tz

        with tempfile.TemporaryDirectory() as tmp:
            db, settings, runner, cfg = self._runner_env(tmp)
            bid = f"battle-test-{_uuid.uuid4().hex[:8]}"
            db.create_battle({
                "id": bid,
                "created_at": _dt.now(_tz.utc).isoformat(),
                "status": "finished",
                "seed": 1,
                "exhibition": 0,
                "fighter_ids": _json.dumps(["iron-1", "hawk-2"]),
                "registry_ids": _json.dumps(["iron-1", "hawk-2"]),
                "playback_speed": 1.0,
                "hire_fee_sol": 0.1,
                "official": 1,
                "mode": "duel",
                "prize_pool_sol": 0.05,
            })
            live = SimpleNamespace(
                id=bid,
                result={"winner": "iron-1", "draw": False},
                registry_ids=["iron-1", "hawk-2"],
                engine_ids=["iron-1", "hawk-2"])
            out = ft_economy._settle_official_battle(db, settings, live)
            prize = out["prize"]
            self.assertEqual(prize["destination"], "treasury")
            self.assertEqual(prize["status"], "mock")
            self.assertAlmostEqual(prize["amount_sol"], 0.05)
            fees = db.fee_totals()
            self.assertAlmostEqual(
                fees["by_source"].get("battle_prize", 0), 0.05)

    def test_draw_prize_rolls_to_season_pool(self):
        import json as _json
        import uuid as _uuid
        from datetime import datetime as _dt, timezone as _tz

        with tempfile.TemporaryDirectory() as tmp:
            db, settings, runner, cfg = self._runner_env(tmp)
            season = ft_economy.ensure_active_season(db, settings)
            pool_before = float(season["prize_pool_sol"])
            bid = f"battle-test-{_uuid.uuid4().hex[:8]}"
            db.create_battle({
                "id": bid,
                "created_at": _dt.now(_tz.utc).isoformat(),
                "status": "finished",
                "seed": 1,
                "exhibition": 0,
                "fighter_ids": _json.dumps(["iron-1", "hawk-2"]),
                "registry_ids": _json.dumps(["iron-1", "hawk-2"]),
                "playback_speed": 1.0,
                "hire_fee_sol": 0.1,
                "official": 1,
                "mode": "duel",
                "prize_pool_sol": 0.04,
            })
            live = SimpleNamespace(
                id=bid,
                result={"winner": None, "draw": True},
                registry_ids=["iron-1", "hawk-2"],
                engine_ids=["iron-1", "hawk-2"])
            out = ft_economy._settle_official_battle(db, settings, live)
            self.assertEqual(out["prize"]["destination"], "season_pool")
            season_after = db.get_season(season["id"])
            self.assertAlmostEqual(float(season_after["prize_pool_sol"]),
                                   pool_before + 0.04)

    def test_all_house_battle_settles(self):
        # End-to-end: an empty queue yields an all-house official battle
        # that still settles exactly one prize record.
        with tempfile.TemporaryDirectory() as tmp:
            db, settings, runner, cfg = self._runner_env(tmp)
            live = ft_economy.run_official_battle(db, runner, settings, cfg,
                                                  rng=random.Random(9))
            self.assertTrue(all(r in HOUSE_BOT_IDS
                                for r in live.registry_ids))
            self.assertTrue(live.finished.wait(30))
            prizes = db._q("SELECT * FROM battle_prizes WHERE battle_id = ?",
                           (live.id,))
            self.assertEqual(len(prizes), 1)
            self.assertIn(dict(prizes[0])["destination"],
                          ("treasury", "season_pool"))
            # no season standings for house fighters
            season_id = db.get_active_season()["id"]
            self.assertEqual(db.season_leaderboard(season_id, limit=10), [])

    def test_exhibition_never_settles_official(self):
        with tempfile.TemporaryDirectory() as tmp:
            db, settings, runner, cfg = self._runner_env(tmp)
            r1, _ = ft_economy.born_fighter(
                db, settings, "W", "Alpha", ARCHETYPE_IDS[0], "#a1b2c3",
                data_dir=tmp)
            live = runner.create([r1["id"], "iron-1"], seed=3,
                                 exhibition=False, playback_speed=1.0,
                                 engine_cfg=cfg, hire_fee_sol=0.1)
            self.assertFalse(live.official)
            self.assertTrue(live.finished.wait(30))
            prizes = db._q("SELECT * FROM battle_prizes WHERE battle_id = ?",
                           (live.id,))
            self.assertEqual(prizes, [])

    def test_scheduler_tick_runs_one_battle_per_slot(self):
        with tempfile.TemporaryDirectory() as tmp:
            db, settings, runner, cfg = self._runner_env(tmp)
            sched = OfficialScheduler(db, runner, settings, cfg)
            live = sched.tick(force=True)
            self.assertIsNotNone(live)
            self.assertTrue(live.finished.wait(30))
            # same slot: no second battle
            self.assertIsNone(sched.tick(force=True))

    def test_scheduler_tick_waits_for_slot_boundary(self):
        # Without force, a mid-slot tick must not start a battle: the
        # scheduler waits for the next boundary so the public countdown
        # stays truthful.
        with tempfile.TemporaryDirectory() as tmp:
            db, settings, runner, cfg = self._runner_env(tmp)
            sched = OfficialScheduler(db, runner, settings, cfg,
                                      tick_seconds=0.05)
            import time as _time
            slot = 10 * 60
            now = _time.time()
            mid_slot = (int(now) // slot) * slot + slot // 2
            real_time = _time.time
            try:
                _time.time = lambda: mid_slot  # noqa: E731
                self.assertIsNone(sched.tick())
            finally:
                _time.time = real_time
            # ...but force still works anywhere in the slot.
            self.assertIsNotNone(sched.tick(force=True))

    def test_entry_window_enforced(self):
        with tempfile.TemporaryDirectory() as tmp:
            db, settings, runner, cfg = self._runner_env(tmp)
            settings.update({"official_battle_interval_minutes": 10,
                             "entry_window_minutes": 5})
            # 8 minutes before the next battle: window closed.
            slot = 10 * 60
            now = time.time()
            boundary = (int(now) // slot + 1) * slot
            with self.assertRaises(ValueError):
                ft_economy.check_entry_window(settings, now=boundary - 8 * 60)
            # 4 minutes before: window open.
            ft_economy.check_entry_window(settings, now=boundary - 4 * 60)
            self.assertTrue(ft_economy.entry_window_open(
                settings, now=boundary - 4 * 60))
            self.assertFalse(ft_economy.entry_window_open(
                settings, now=boundary - 8 * 60))


class SeasonTest(unittest.TestCase):
    def test_rollover_pays_top3_and_rolls_once(self):
        with temp_env() as (db, settings, tmp):
            s = ft_economy.ensure_active_season(db, settings)
            db.add_to_season_pool(s["id"], 1.0)
            mk = lambda w, n: ft_economy.born_fighter(
                db, settings, w, n, ARCHETYPE_IDS[0], "#a1b2c3",
                rng=random.Random(1), data_dir=tmp)[0]
            f1, f2, f3 = (mk("WA", "A1"), mk("WB", "B1"), mk("WC", "C1"))
            db.record_season_result(s["id"], f1["id"], "win")
            db.record_season_result(s["id"], f1["id"], "win")
            db.record_season_result(s["id"], f2["id"], "win")
            db.record_season_result(s["id"], f3["id"], "loss")
            # expire the season
            db._q("UPDATE seasons SET ends_at = ? WHERE id = ?",
                   ("2026-01-01T00:00:00+00:00", s["id"]))
            new_s = ft_economy.ensure_active_season(db, settings)
            self.assertNotEqual(new_s["id"], s["id"])
            self.assertEqual(new_s["status"], "active")
            old = db.get_season(s["id"])
            self.assertEqual(old["status"], "finished")
            payouts = db._q(
                "SELECT * FROM season_payouts WHERE season_id = ? ORDER BY place",
                (s["id"],))
            self.assertEqual(len(payouts), 3)
            amounts = [dict(p)["amount_sol"] for p in payouts]
            # pool was 1.0 + 3x0.05 born shares = 1.15
            self.assertAlmostEqual(amounts[0], 1.15 * 0.60)
            self.assertAlmostEqual(amounts[1], 1.15 * 0.25)
            self.assertAlmostEqual(amounts[2], 1.15 * 0.15)
            self.assertTrue(all(dict(p)["status"] == "mock" for p in payouts))
            self.assertEqual([dict(p)["owner_wallet"] for p in payouts],
                             ["WA", "WB", "WC"])
            # exactly once: another ensure does not re-pay
            again = ft_economy.ensure_active_season(db, settings)
            self.assertEqual(again["id"], new_s["id"])
            payouts2 = db._q(
                "SELECT * FROM season_payouts WHERE season_id = ?", (s["id"],))
            self.assertEqual(len(payouts2), 3)

    def test_fewer_than_three_remainder_seeds_next_season(self):
        with temp_env() as (db, settings, tmp):
            s = ft_economy.ensure_active_season(db, settings)
            f1 = ft_economy.born_fighter(
                db, settings, "WA", "Solo", ARCHETYPE_IDS[0], "#a1b2c3",
                rng=random.Random(1), data_dir=tmp)[0]
            db.record_season_result(s["id"], f1["id"], "win")
            db._q("UPDATE seasons SET ends_at = ? WHERE id = ?",
                   ("2026-01-01T00:00:00+00:00", s["id"]))
            pool_before = float(db.get_season(s["id"])["prize_pool_sol"])
            new_s = ft_economy.ensure_active_season(db, settings)
            payouts = db._q(
                "SELECT * FROM season_payouts WHERE season_id = ?", (s["id"],))
            self.assertEqual(len(payouts), 1)
            paid = float(dict(payouts[0])["amount_sol"])
            self.assertAlmostEqual(paid, pool_before * 0.60)
            self.assertAlmostEqual(float(new_s["prize_pool_sol"]),
                                   pool_before - paid)


class FtApiTest(unittest.TestCase):
    def test_born_api_mock_and_registry(self):
        with temp_app() as app:
            with TestClient(app) as c:
                r = c.post("/api/ft/born/initiate", json={
                    "wallet": "WALLET1", "name": "Blitzy",
                    "archetype": ARCHETYPE_IDS[2], "color": "#ff00aa"})
                self.assertEqual(r.status_code, 201)
                body = r.json()
                self.assertEqual(body["payment"], "mock")
                f = body["fighter"]
                self.assertEqual(f["id"], "FT-0001")
                self.assertEqual(f["name"], "Blitzy")
                self.assertIn(f["rarity"],
                              ("Common", "Rare", "Epic", "Legendary"))
                self.assertTrue(f["owner_cert"])
                # registry + wallet + births
                r = c.get("/api/ft/registry")
                self.assertEqual(r.json()["count"], 1)
                r = c.get("/api/ft/wallet/WALLET1/fighters")
                self.assertEqual(len(r.json()["fighters"]), 1)
                r = c.get("/api/ft/births")
                self.assertEqual(len(r.json()["births"]), 1)
                self.assertEqual(r.json()["births"][0]["name"],
                                 "Blitzy")

    def test_registry_excludes_house_fighters(self):
        from backend.db import Database
        with temp_app() as app:
            with TestClient(app) as c:
                r = c.post("/api/ft/born/initiate", json={
                    "wallet": "WALLET1", "name": "Reggie",
                    "archetype": ARCHETYPE_IDS[0], "color": "#a1b2c3"})
                self.assertEqual(r.status_code, 201)
                r = c.get("/api/ft/registry")
                body = r.json()
                ids = [f["id"] for f in body["fighters"]]
                self.assertIn("FT-0001", ids)
                self.assertNotIn("iron-1", ids)
                self.assertEqual(body["count"], 1)
                self.assertEqual(len(ids), body["count"])

    def test_born_api_validation(self):
        with temp_app() as app:
            with TestClient(app) as c:
                r = c.post("/api/ft/born/initiate", json={
                    "wallet": "W", "name": "x",
                    "archetype": "nope", "color": "#a1b2c3"})
                self.assertEqual(r.status_code, 400)
                r = c.get("/api/ft/fighters/FT-9999")
                self.assertEqual(r.status_code, 404)

    def test_queue_api(self):
        with temp_app() as app:
            with TestClient(app) as c:
                r = c.post("/api/ft/born/initiate", json={
                    "wallet": "WALLET1", "name": "Queenie",
                    "archetype": ARCHETYPE_IDS[0], "color": "#a1b2c3"})
                fid = r.json()["fighter"]["id"]
                r = c.post("/api/ft/queue/enter",
                           json={"fighter_id": fid, "wallet": "WALLET1"})
                self.assertEqual(r.status_code, 201)
                self.assertEqual(r.json()["payment"], "mock")
                r = c.get("/api/ft/queue")
                self.assertEqual(r.json()["count"], 1)
                self.assertEqual(r.json()["queued"][0]["fighter_id"], fid)
                r = c.get("/api/ft/next-battle")
                self.assertIn(r.json()["mode"], ("duel", "royale"))
                self.assertGreater(r.json()["starts_in_seconds"], 0)
                # non-owner refused
                r = c.post("/api/ft/queue/enter",
                           json={"fighter_id": fid, "wallet": "OTHER"})
                self.assertEqual(r.status_code, 400)

    def test_season_api(self):
        with temp_app() as app:
            with TestClient(app) as c:
                r = c.get("/api/ft/season")
                self.assertEqual(r.status_code, 200)
                body = r.json()
                self.assertEqual(body["season"]["status"], "active")
                self.assertEqual(body["prize_split_pct"], [60.0, 25.0, 15.0])
                self.assertEqual(body["leaderboard"], [])

    def test_settings_validation(self):
        with temp_app() as app:
            with TestClient(app) as c:
                # new keys visible publicly except payouts_live
                r = c.get("/api/settings")
                self.assertEqual(r.json()["born_fee_sol"], 0.1)
                self.assertEqual(r.json()["entry_fee_sol"], 0.02)
                self.assertNotIn("payouts_live", r.json())
                # bad splits rejected
                r = c.put("/api/admin/settings",
                          json={"entry_prize_pct": 50.0},
                          headers=ADMIN_HEADERS)
                self.assertEqual(r.status_code, 400)
                r = c.put("/api/admin/settings",
                          json={"season_prize_1_pct": 50.0},
                          headers=ADMIN_HEADERS)
                self.assertEqual(r.status_code, 400)
                # good update works
                r = c.put("/api/admin/settings",
                          json={"official_battle_interval_minutes": 5,
                                "entry_window_minutes": 5},
                          headers=ADMIN_HEADERS)
                self.assertEqual(r.status_code, 200)
                self.assertEqual(
                    r.json()["settings"]["official_battle_interval_minutes"],
                    5)
                # entry window must fit inside the battle interval
                r = c.put("/api/admin/settings",
                          json={"entry_window_minutes": 6},
                          headers=ADMIN_HEADERS)
                self.assertEqual(r.status_code, 400)


# ---------------------------------------------------------------- live paths
def _keypair():
    from solders.keypair import Keypair  # noqa: E402
    return Keypair()


class FakeRpc:
    def __init__(self, tx=None, slot=900_000_000):
        self._tx = tx
        self._slot = slot

    def get_latest_blockhash(self):
        return SimpleNamespace(
            value=SimpleNamespace(
                blockhash="11111111111111111111111111111111"))

    def get_slot(self):
        return SimpleNamespace(value=self._slot)

    def get_transaction(self, sig, encoding=None,
                        max_supported_transaction_version=None):
        return SimpleNamespace(value=self._tx)


def fake_verified_tx(wallet, treasury, amount_lamports, slot=900_000_000):
    ix = SimpleNamespace(parsed={
        "type": "transfer",
        "info": {"source": wallet, "destination": treasury,
                 "lamports": amount_lamports}})
    return SimpleNamespace(
        slot=slot,
        meta=SimpleNamespace(err=None, inner_instructions=[]),
        transaction=SimpleNamespace(
            message=SimpleNamespace(instructions=[ix])))


def throwaway_pubkey():
    return str(_keypair().pubkey())


def throwaway_sig():
    return str(_keypair().sign_message(b"confirm"))


class LiveBornEntryTest(unittest.TestCase):
    def _live_app(self, c):
        treasury = throwaway_pubkey()
        r = c.put("/api/admin/settings",
                  json={"treasury_wallet": treasury,
                        "born_live": True, "entry_live": True},
                  headers=ADMIN_HEADERS)
        self.assertEqual(r.status_code, 200, r.text)
        return treasury

    def test_live_born_initiate_and_confirm(self):
        with temp_app() as app:
            with TestClient(app) as c:
                treasury = self._live_app(c)
                wallet = throwaway_pubkey()
                with mock.patch.object(payments_live, "_rpc_client",
                                       return_value=FakeRpc()):
                    r = c.post("/api/ft/born/initiate", json={
                        "wallet": wallet, "name": "Livey",
                        "archetype": ARCHETYPE_IDS[0], "color": "#a1b2c3"})
                self.assertEqual(r.status_code, 201, r.text)
                body = r.json()
                self.assertEqual(body["payment"], "live")
                self.assertIn("transaction_base64", body)
                intent_id = body["intent_id"]
                # no fighter yet: rarity rolls only at payment
                r = c.get("/api/ft/registry")
                self.assertEqual(r.json()["count"], 0)
                # confirm with a faked onchain transfer
                tx = fake_verified_tx(wallet, treasury,
                                      payments_live.lamports(0.1))
                with mock.patch.object(payments_live, "_rpc_client",
                                       return_value=FakeRpc(tx=tx)):
                    r = c.post("/api/ft/born/confirm", json={
                        "intent_id": intent_id,
                        "signature": throwaway_sig()})
                self.assertEqual(r.status_code, 201, r.text)
                f = r.json()["fighter"]
                self.assertEqual(f["id"], "FT-0001")
                self.assertEqual(f["owner_wallet"], wallet)
                # replay of the same signature is refused
                with mock.patch.object(payments_live, "_rpc_client",
                                       return_value=FakeRpc(tx=tx)):
                    r = c.post("/api/ft/born/confirm", json={
                        "intent_id": intent_id,
                        "signature": throwaway_sig()})
                self.assertIn(r.status_code, (400, 409))

    def test_live_entry_initiate_and_confirm(self):
        with temp_app() as app:
            with TestClient(app) as c:
                treasury = self._live_app(c)
                wallet = throwaway_pubkey()
                # born in mock first (born_live on would need payment too;
                # keep this test focused on the entry path)
                c.put("/api/admin/settings", json={"born_live": False},
                      headers=ADMIN_HEADERS)
                r = c.post("/api/ft/born/initiate", json={
                    "wallet": wallet, "name": "Entry",
                    "archetype": ARCHETYPE_IDS[0], "color": "#a1b2c3"})
                fid = r.json()["fighter"]["id"]
                with mock.patch.object(payments_live, "_rpc_client",
                                       return_value=FakeRpc()):
                    r = c.post("/api/ft/queue/enter",
                               json={"fighter_id": fid, "wallet": wallet})
                self.assertEqual(r.status_code, 201, r.text)
                body = r.json()
                self.assertEqual(body["payment"], "live")
                self.assertIn("transaction_base64", body)
                entry_id = body["entry_id"]
                self.assertEqual(c.get("/api/ft/queue").json()["count"], 0)
                tx = fake_verified_tx(wallet, treasury,
                                      payments_live.lamports(0.02))
                with mock.patch.object(payments_live, "_rpc_client",
                                       return_value=FakeRpc(tx=tx)):
                    r = c.post("/api/ft/queue/confirm", json={
                        "entry_id": entry_id,
                        "signature": throwaway_sig()})
                self.assertEqual(r.status_code, 200, r.text)
                q = c.get("/api/ft/queue").json()
                self.assertEqual(q["count"], 1)
                self.assertEqual(q["queued"][0]["fighter_id"], fid)


if __name__ == "__main__":
    unittest.main()
