"""
Bot API contract (stable interface for phase 2 house fighter bots).

A bot subclasses FighterBot and implements decide(state) -> action.
The engine NEVER trusts bot output: every action is validated and clamped
server-side before being applied.
"""

import math

# Safe default action: stand still, no fire, no shield, no dash.
SAFE_ACTION = {"move": [0.0, 0.0], "aim": 0.0, "fire": False, "shield": False, "dash": False}


class FighterBot:
    """Base class for all fighter bots.

    Subclass and implement decide(). The engine calls decide() once per tick
    with a state dict and expects an action dict back.
    """

    name = "bot"

    def decide(self, state: dict) -> dict:
        """Return this tick's action given the current state.

        Input state schema (all values JSON-serializable):
        {
            "tick": int,                       # current tick number (0-based)
            "fighter": {                       # your own fighter
                "id": str,
                "x": float, "y": float,         # position in arena units
                "vx": float, "vy": float,       # velocity in units/second
                "heading": float,               # body facing, radians
                "hp": float,
                "fire_cooldown": int,           # ticks until you can fire again (0 = ready)
                "dash_cooldown": int,           # ticks until you can dash again (0 = ready)
                "shield_energy": float,         # 0..shield_max_energy
                "shield_active": bool,          # was shield up last tick
            },
            "enemies": [                        # other fighters (alive and dead)
                {"id": str, "x": float, "y": float,
                 "vx": float, "vy": float, "heading": float,
                 "hp": float, "alive": bool},
                ...
            ],
            "projectiles": [                    # all live projectiles
                {"id": int, "x": float, "y": float,
                 "vx": float, "vy": float,
                 "owner": str, "damage": float, "life": int},  # life = ticks remaining
                ...
            ],
            "arena": {"size": float, "tick_rate": int},
            "alive_count": int,
        }

        Output action schema (missing/invalid fields fall back to safe defaults):
        {
            "move": [x, y],   # desired velocity vector, units/second; clamped to max_speed
            "aim": float,     # turret angle in radians; NaN/inf ignored
            "fire": bool,     # attempt to fire (subject to cooldown)
            "shield": bool,   # raise shield (drains energy; needs energy > 0)
            "dash": bool,     # dash burst (subject to cooldown)
        }
        """
        raise NotImplementedError


class NullBot(FighterBot):
    """Does nothing. Used as the safe fallback when a bot misbehaves."""

    name = "null"

    def decide(self, state: dict) -> dict:
        return {"move": [0.0, 0.0], "aim": 0.0, "fire": False, "shield": False, "dash": False}


def _is_finite_number(value) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)


def validate_action(action, max_speed: float) -> dict:
    """Validate and clamp a bot's action dict. Never raises on bad input."""
    if not isinstance(action, dict):
        return dict(SAFE_ACTION)

    # move: [x, y] velocity vector, clamped to max_speed magnitude.
    move = action.get("move", [0.0, 0.0])
    try:
        mx, my = float(move[0]), float(move[1])
        if not math.isfinite(mx):
            mx = 0.0
        if not math.isfinite(my):
            my = 0.0
    except (TypeError, ValueError, IndexError):
        mx, my = 0.0, 0.0
    speed = math.hypot(mx, my)
    if speed > max_speed > 0:
        scale = max_speed / speed
        mx, my = mx * scale, my * scale

    # aim: finite float radians, else 0.
    aim = action.get("aim", 0.0)
    if not _is_finite_number(aim):
        aim = 0.0
    else:
        aim = float(aim)

    return {
        "move": [mx, my],
        "aim": aim,
        "fire": bool(action.get("fire", False)),
        "shield": bool(action.get("shield", False)),
        "dash": bool(action.get("dash", False)),
    }
