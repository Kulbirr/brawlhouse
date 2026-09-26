"""
Built-in trivial bots for the CLI runner, smoke tests, and examples.

These exist so `python -m arena sim` works out of the box. Phase 2 house
bots should subclass FighterBot directly (see bot_api.py), not these.
"""

import math
import random

from .bot_api import FighterBot, NullBot

MAX_SPEED_FALLBACK = 120.0


def _nearest_enemy(state: dict) -> dict | None:
    me = state["fighter"]
    best = None
    best_d2 = float("inf")
    for e in state["enemies"]:
        if not e["alive"]:
            continue
        d2 = (e["x"] - me["x"]) ** 2 + (e["y"] - me["y"]) ** 2
        if d2 < best_d2:
            best_d2 = d2
            best = e
    return best


def _angle_to(me: dict, target: dict) -> float:
    return math.atan2(target["y"] - me["y"], target["x"] - me["x"])


class SpinnerBot(FighterBot):
    """Rotates in place, firing constantly."""

    name = "spinner"

    def __init__(self):
        self.phase = 0.0

    def decide(self, state: dict) -> dict:
        self.phase += 0.35
        return {"move": [0.0, 0.0], "aim": self.phase,
                "fire": True, "shield": False, "dash": False}


class ChaserBot(FighterBot):
    """Moves toward the nearest enemy and fires when roughly aimed."""

    name = "chaser"

    def decide(self, state: dict) -> dict:
        me = state["fighter"]
        enemy = _nearest_enemy(state)
        if enemy is None:
            return {"move": [0.0, 0.0], "aim": 0.0,
                    "fire": False, "shield": False, "dash": False}
        aim = _angle_to(me, enemy)
        dist = math.hypot(enemy["x"] - me["x"], enemy["y"] - me["y"])
        speed = min(120.0, dist * 0.8)
        move = [math.cos(aim) * speed, math.sin(aim) * speed]
        # Back off a little when very close so we don't hug the enemy.
        if dist < 120:
            move = [-move[0] * 0.5, -move[1] * 0.5]
        return {"move": move, "aim": aim, "fire": dist < 700,
                "shield": False, "dash": False}


class FleerBot(FighterBot):
    """Runs away from the nearest enemy and nearest projectile."""

    name = "fleer"

    def decide(self, state: dict) -> dict:
        me = state["fighter"]
        fx, fy = 0.0, 0.0
        enemy = _nearest_enemy(state)
        if enemy is not None:
            d = math.hypot(enemy["x"] - me["x"], enemy["y"] - me["y"]) or 1.0
            fx += (me["x"] - enemy["x"]) / d
            fy += (me["y"] - enemy["y"]) / d
        for p in state["projectiles"]:
            d = math.hypot(p["x"] - me["x"], p["y"] - me["y"]) or 1.0
            if d < 250:
                w = (250 - d) / 250
                fx += (me["x"] - p["x"]) / d * w
                fy += (me["y"] - p["y"]) / d * w
        mag = math.hypot(fx, fy) or 1.0
        speed = min(120.0, 120.0 * mag)
        move = [fx / mag * speed, fy / mag * speed]
        aim = _angle_to(me, enemy) if enemy else 0.0
        return {"move": move, "aim": aim, "fire": enemy is not None,
                "shield": any(math.hypot(p["x"] - me["x"], p["y"] - me["y"]) < 180
                              for p in state["projectiles"]),
                "dash": False}


class RandomBot(FighterBot):
    """Wanders randomly, firing sporadically. Seeded for determinism."""

    name = "random"

    def __init__(self, seed: int = 0):
        self.rng = random.Random(seed)

    def decide(self, state: dict) -> dict:
        angle = self.rng.uniform(0, 2 * math.pi)
        speed = self.rng.uniform(0, 120.0)
        return {"move": [math.cos(angle) * speed, math.sin(angle) * speed],
                "aim": self.rng.uniform(0, 2 * math.pi),
                "fire": self.rng.random() < 0.3,
                "shield": self.rng.random() < 0.1,
                "dash": self.rng.random() < 0.05}


class ExplodingBot(FighterBot):
    """Raises every tick. Used to test engine fault tolerance."""

    name = "exploding"

    def decide(self, state: dict) -> dict:
        raise RuntimeError("boom")


class SlowBot(FighterBot):
    """Sleeps past the timeout every tick. Used to test timeout handling."""

    name = "slow"

    def __init__(self, delay: float = 5.0):
        self.delay = delay

    def decide(self, state: dict) -> dict:
        import time
        time.sleep(self.delay)
        return {"move": [0.0, 0.0], "aim": 0.0,
                "fire": False, "shield": False, "dash": False}


# Registry for the CLI: id -> factory(seed, index) -> FighterBot.
BUILTIN_BOTS = {
    "spinner": lambda seed, i: SpinnerBot(),
    "chaser": lambda seed, i: ChaserBot(),
    "fleer": lambda seed, i: FleerBot(),
    "random": lambda seed, i: RandomBot(seed * 1000 + i + 7),
    "null": lambda seed, i: NullBot(),
    "exploding": lambda seed, i: ExplodingBot(),
    "slow": lambda seed, i: SlowBot(),
}
