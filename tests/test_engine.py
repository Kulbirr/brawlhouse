"""Engine tests (stdlib unittest, no dependencies)."""

import json
import unittest

from arena.bot_api import FighterBot, NullBot, validate_action
from arena.config import Config
from arena.engine import Battle, simulate
from arena.bots import BUILTIN_BOTS


def make_cfg(**overrides):
    base = {
        "seed": 42,
        "tick_rate": 10,
        "max_ticks": 1200,
        "bot_timeout_ms": 200,
    }
    base.update(overrides)
    return Config.from_env(base)


class DeterministicChaser(FighterBot):
    """Chaser with no randomness at all (for the determinism test)."""

    name = "det-chaser"

    def decide(self, state):
        import math
        me = state["fighter"]
        foes = [e for e in state["enemies"] if e["alive"]]
        if not foes:
            return {"move": [0.0, 0.0], "aim": 0.0,
                    "fire": False, "shield": False, "dash": False}
        e = min(foes, key=lambda x: (x["x"] - me["x"]) ** 2 + (x["y"] - me["y"]) ** 2)
        aim = math.atan2(e["y"] - me["y"], e["x"] - me["x"])
        dist = math.hypot(e["x"] - me["x"], e["y"] - me["y"])
        speed = min(120.0, dist * 0.8)
        return {"move": [math.cos(aim) * speed, math.sin(aim) * speed],
                "aim": aim, "fire": dist < 700,
                "shield": False, "dash": False}


class ExplodingBot(FighterBot):
    name = "exploding"

    def decide(self, state):
        raise RuntimeError("boom")


class GarbageBot(FighterBot):
    """Returns malformed actions of every kind."""

    name = "garbage"

    def __init__(self):
        self.n = 0

    def decide(self, state):
        self.n += 1
        return [
            None,
            {"move": "not-a-vector"},
            {"move": [float("inf"), float("nan")], "aim": float("nan")},
            {"move": [10**9, 10**9], "fire": "yes"},
            {"unexpected": "keys", "dash": 1},
        ][self.n % 5]


class TestFightCompletes(unittest.TestCase):
    def test_two_bots_produce_a_winner(self):
        cfg = make_cfg()
        bots = [("chaser-1", BUILTIN_BOTS["chaser"](42, 0)),
                ("spinner-2", BUILTIN_BOTS["spinner"](42, 1))]
        result, _ = simulate(bots, cfg)
        self.assertFalse(result["draw"])
        self.assertIn(result["winner"], ("chaser-1", "spinner-2"))
        self.assertEqual(len(result["elimination_order"]), 1)
        self.assertLessEqual(result["ticks"], cfg.max_ticks)
        self.assertGreater(result["ticks"], 0)

    def test_symmetric_fight_can_end_in_mutual_elimination(self):
        # Two identical chasers mirror each other and die on the same tick.
        cfg = make_cfg()
        bots = [("chaser-1", BUILTIN_BOTS["chaser"](42, 0)),
                ("chaser-2", BUILTIN_BOTS["chaser"](42, 1))]
        result, _ = simulate(bots, cfg)
        self.assertTrue(result["draw"])
        self.assertEqual(result["reason"], "mutual_elimination")
        self.assertEqual(len(result["elimination_order"]), 2)

    def test_eight_fighter_royale_completes(self):
        cfg = make_cfg()
        ids = ["chaser", "spinner", "fleer", "random",
               "chaser", "spinner", "fleer", "random"]
        bots = [(f"{b}-{i}", BUILTIN_BOTS[b](42, i)) for i, b in enumerate(ids)]
        result, _ = simulate(bots, cfg)
        # Either a winner or a max-ticks draw; never a crash, never >1 alive undecided.
        self.assertLessEqual(result["ticks"], cfg.max_ticks)
        self.assertTrue(result["winner"] or result["draw"])

    def test_max_ticks_highest_hp_wins(self):
        # NullBots never damage each other -> max ticks -> tie -> draw.
        cfg = make_cfg(max_ticks=50)
        bots = [("a", NullBot()), ("b", NullBot())]
        result, _ = simulate(bots, cfg)
        self.assertEqual(result["ticks"], 50)
        self.assertTrue(result["draw"])
        self.assertEqual(result["reason"], "hp_tie_at_max_ticks")


class TestDeterminism(unittest.TestCase):
    def run_fight(self):
        cfg = make_cfg()
        bots = [("a", DeterministicChaser()), ("b", DeterministicChaser())]
        return simulate(bots, cfg, snapshot_every=1)

    def test_same_seed_identical_outcome(self):
        result1, snaps1 = self.run_fight()
        result2, snaps2 = self.run_fight()
        self.assertEqual(result1, result2)
        self.assertEqual(snaps1, snaps2)

    def test_snapshots_are_json_serializable(self):
        _, snaps = self.run_fight()
        json.dumps(snaps)  # must not raise

    def test_different_seed_may_differ(self):
        # random-vs-random used to be the vehicle here, but two wandering
        # random bots essentially never hit each other, so the assertion
        # hinged on a single lucky 8-damage hit. Chaser-vs-random keeps the
        # intent (seeds must affect outcomes) with a real fight.
        cfg1 = make_cfg(seed=1)
        cfg2 = make_cfg(seed=2)
        bots1 = [("c", BUILTIN_BOTS["chaser"](1, 0)),
                 ("r", BUILTIN_BOTS["random"](1, 1))]
        bots2 = [("c", BUILTIN_BOTS["chaser"](2, 0)),
                 ("r", BUILTIN_BOTS["random"](2, 1))]
        r1, _ = simulate(bots1, cfg1)
        r2, _ = simulate(bots2, cfg2)
        # Not a strict requirement, but with random bots seeds should matter.
        self.assertNotEqual(
            (r1["winner"], r1["ticks"]), (r2["winner"], r2["ticks"]))


class TestFaultTolerance(unittest.TestCase):
    def test_exploding_bot_does_not_crash_fight(self):
        cfg = make_cfg()
        bots = [("boom", ExplodingBot()),
                ("chaser-1", BUILTIN_BOTS["chaser"](42, 0))]
        result, _ = simulate(bots, cfg)
        # exploding bot stands still and gets shot; fight still resolves
        self.assertIn(result["winner"], ("boom", "chaser-1", None))
        self.assertTrue(any("benched" in n for n in result["notes"]))

    def test_garbage_actions_are_clamped(self):
        cfg = make_cfg(max_ticks=60)
        bots = [("garbage", GarbageBot()),
                ("null-1", NullBot())]
        result, _ = simulate(bots, cfg)  # must not raise
        self.assertEqual(result["ticks"], 60)

    def test_validate_action_clamps_speed(self):
        a = validate_action({"move": [10000, 0], "fire": 1}, max_speed=120.0)
        import math
        self.assertAlmostEqual(math.hypot(*a["move"]), 120.0)
        self.assertTrue(a["fire"])
        self.assertEqual(validate_action(None, 120.0)["move"], [0.0, 0.0])
        self.assertEqual(validate_action({"move": [float("nan"), 1]},
                                         120.0)["move"], [0.0, 1.0])

    def test_slow_bot_times_out_and_gets_benched(self):
        from arena.bots import SlowBot
        cfg = make_cfg(bot_timeout_ms=50, max_ticks=30)
        bots = [("slow", SlowBot(delay=5.0)), ("null-1", NullBot())]
        result, _ = simulate(bots, cfg)
        self.assertEqual(result["ticks"], 30)
        self.assertTrue(any("benched" in n for n in result["notes"]))

    def test_firing_drops_shield_for_the_tick(self):
        """Loosing a shot drops the shield for the rest of the tick.

        Regression test for the AEGIS-4 duel dominance (94% win rate):
        firing through a permanently-up shield while blocking everything
        made it nearly unbeatable 1v1. Now every shot is a moment of
        vulnerability: a shielder that keeps firing takes hull damage.
        """

        class TurtleBot(FighterBot):
            name = "turtle"

            def decide(self, state):
                return {"move": [0.0, 0.0], "aim": 0.0,
                        "fire": True, "shield": True, "dash": False}

        class GunnerBot(FighterBot):
            name = "gunner"

            def decide(self, state):
                import math
                me = state["fighter"]
                foes = [e for e in state["enemies"] if e["alive"]]
                e = foes[0]
                aim = math.atan2(e["y"] - me["y"], e["x"] - me["x"])
                return {"move": [0.0, 0.0], "aim": aim,
                        "fire": True, "shield": False, "dash": False}

        cfg = make_cfg(max_ticks=200)
        battle = Battle([("turtle-1", TurtleBot()),
                         ("gunner-2", GunnerBot())], cfg)
        # 250 apart: projectiles (speed 50) take exactly 5 ticks to cross,
        # the fire cooldown. Both bots fire on ticks 0, 5, 10, ... so the
        # gunner's shots always land on a tick where the turtle just fired
        # and dropped its shield -> hull damage, deterministically.
        battle.fighters[0].x, battle.fighters[0].y = 100.0, 100.0
        battle.fighters[1].x, battle.fighters[1].y = 100.0, 350.0
        battle.step()
        self.assertFalse(battle.fighters[0].shield_active,
                         "firing must drop the shield for the tick")
        for _ in range(199):
            battle.step()
        snap = battle.snapshot()
        turtle = next(f for f in snap["fighters"] if f["id"] == "turtle-1")
        self.assertLess(turtle["hp"], 100.0,
                        "a shielder that keeps firing must take hull damage")

    def test_unshielded_fighter_still_fires(self):
        """Control for the shield rule: shield down -> aimed shots land."""

        class GunnerBot(FighterBot):
            name = "gunner"

            def decide(self, state):
                import math
                me = state["fighter"]
                foes = [e for e in state["enemies"] if e["alive"]]
                e = min(foes, key=lambda x: (x["x"] - me["x"]) ** 2
                       + (x["y"] - me["y"]) ** 2)
                aim = math.atan2(e["y"] - me["y"], e["x"] - me["x"])
                return {"move": [0.0, 0.0], "aim": aim,
                        "fire": True, "shield": False, "dash": False}

        cfg = make_cfg(max_ticks=120)
        battle = Battle([("gunner-1", GunnerBot()),
                         ("null-2", NullBot())], cfg)
        for _ in range(120):
            battle.step()
        null = next(f for f in battle.snapshot()["fighters"]
                    if f["id"] == "null-2")
        self.assertLess(null["hp"], 100.0)


if __name__ == "__main__":
    unittest.main()
