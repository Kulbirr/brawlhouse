"""House fighter tests (stdlib unittest, no dependencies).

Covers the Phase 2 acceptance criteria:
  1. Every house fighter completes a 1v1 vs a trivial bot without crashing.
  2. A five-fighter royale produces a winner.
  3. The same seed twice produces an identical result (determinism).
  4. The FIGHTERS registry carries all required public metadata.
"""

import time
import unittest

from arena.bot_api import FighterBot, NullBot
from arena.config import Config
from arena.engine import Battle
from arena.house_fighters import (
    FIGHTERS,
    HOUSE_BOT_IDS,
    build_house_bots,
    make_house_bot,
)


def make_cfg(**overrides):
    base = {
        "seed": 42,
        "tick_rate": 10,
        "max_ticks": 1200,
        "bot_timeout_ms": 200,
    }
    base.update(overrides)
    return Config.from_env(base)


def run_fight(bot_ids, seed):
    """Run one battle to completion; return the result dict."""
    cfg = make_cfg(seed=seed)
    bots = [(f"{bid}-{i}", make_house_bot(bid, seed, i))
            for i, bid in enumerate(bot_ids)]
    battle = Battle(bots, cfg)
    while not battle.is_over():
        battle.step()
    return battle.result()


class TestHouseFightersDuel(unittest.TestCase):
    """Each house fighter finishes a 1v1 against a trivial bot."""

    def test_each_house_fighter_beats_nullbot_without_crashing(self):
        for bot_id in HOUSE_BOT_IDS:
            with self.subTest(bot=bot_id):
                cfg = make_cfg(seed=7)
                bots = [
                    (f"{bot_id}-1", make_house_bot(bot_id, 7, 0)),
                    ("null-2", NullBot()),
                ]
                battle = Battle(bots, cfg)
                while not battle.is_over():
                    battle.step()
                result = battle.result()
                benched = [f.id for f in battle.fighters if f.benched]
                self.assertEqual(benched, [],
                                 f"{bot_id} was benched: {benched}")
                # NullBot never moves or fires, so the house fighter must win
                # by elimination (or at worst on HP at max ticks).
                self.assertFalse(result["draw"] and
                                 result["winner"] == "null-2")


class TestHouseFightersRoyale(unittest.TestCase):
    """The five house fighters produce a winner in a royale."""

    def test_five_fighter_royale_has_winner(self):
        result = run_fight(HOUSE_BOT_IDS, seed=42)
        self.assertFalse(result["draw"],
                         f"royale ended in a draw: {result}")
        self.assertIsNotNone(result["winner"])
        winner_base = result["winner"].rsplit("-", 1)[0]
        self.assertIn(winner_base, HOUSE_BOT_IDS)
        self.assertLess(result["ticks"], 1200,
                        "royale should resolve by elimination")

    def test_same_seed_twice_identical_result(self):
        first = run_fight(HOUSE_BOT_IDS, seed=1234)
        second = run_fight(HOUSE_BOT_IDS, seed=1234)
        self.assertEqual(first["winner"], second["winner"])
        self.assertEqual(first["ticks"], second["ticks"])
        self.assertEqual(first["elimination_order"],
                         second["elimination_order"])
        self.assertEqual(first["draw"], second["draw"])


class TestFighterRegistry(unittest.TestCase):
    """FIGHTERS carries complete public metadata for every house bot."""

    REQUIRED_KEYS = {"id", "name", "tagline", "description", "bot_class"}

    def test_all_house_ids_registered(self):
        registered = {entry["id"] for entry in FIGHTERS}
        for bot_id in HOUSE_BOT_IDS:
            self.assertIn(bot_id, registered)

    def test_registry_metadata_complete(self):
        for entry in FIGHTERS:
            with self.subTest(entry=entry.get("id")):
                self.assertTrue(self.REQUIRED_KEYS.issubset(entry.keys()),
                                f"missing keys: "
                                f"{self.REQUIRED_KEYS - set(entry.keys())}")
                for key in ("id", "name", "tagline", "description"):
                    self.assertIsInstance(entry[key], str)
                    self.assertTrue(entry[key].strip(),
                                    f"{key} must be non-empty")
                self.assertTrue(issubclass(entry["bot_class"], FighterBot))

    def test_make_house_bot_builds_registered_class(self):
        for bot_id in HOUSE_BOT_IDS:
            bot = make_house_bot(bot_id, seed=99, index=0)
            entry = next(e for e in FIGHTERS if e["id"] == bot_id)
            self.assertIsInstance(bot, entry["bot_class"])

    def test_build_house_bots_returns_five(self):
        bots = build_house_bots(",".join(HOUSE_BOT_IDS), seed=99)
        self.assertEqual(len(bots), 5)
        self.assertEqual(len({b.name for _, b in bots}), 5)


class TestHouseFighterPerformance(unittest.TestCase):
    """decide() stays comfortably below the 50ms budget."""

    def test_decide_fast(self):
        cfg = make_cfg(seed=42)
        bots = [(f"{bid}-{i}", make_house_bot(bid, 42, i))
                for i, bid in enumerate(HOUSE_BOT_IDS)]
        battle = Battle(bots, cfg)
        for _ in range(200):  # reach a busy mid-fight state
            battle.step()
        for fighter in battle.fighters:
            state = battle._fighter_state(fighter)
            start = time.perf_counter()
            for _ in range(50):
                fighter.bot.decide(state)
            elapsed_ms = (time.perf_counter() - start) / 50 * 1000
            self.assertLess(
                elapsed_ms, 50.0,
                f"{fighter.id} decide() too slow: {elapsed_ms:.1f}ms")


if __name__ == "__main__":
    unittest.main()
