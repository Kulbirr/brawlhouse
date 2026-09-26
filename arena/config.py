"""
Arena configuration: every tunable lives here, driven by environment variables
with sane defaults. No project names, token names, or tickers anywhere.
"""

import os
from dataclasses import dataclass, field


def _get_float(name: str, default: float) -> float:
    try:
        return float(os.environ.get(name, default))
    except (TypeError, ValueError):
        return default


def _get_int(name: str, default: int) -> int:
    try:
        return int(os.environ.get(name, default))
    except (TypeError, ValueError):
        return default


@dataclass
class Config:
    # Arena
    arena_size: float = 1000.0          # square arena, units (x and y in [0, size])
    tick_rate: int = 10                # simulation ticks per second
    max_ticks: int = 1200              # fight length cap (120s at 10 tps)
    seed: int | None = None            # None = nondeterministic

    # Fighter
    fighter_hp: float = 100.0
    fighter_radius: float = 12.0
    max_speed: float = 120.0           # units/second for normal movement
    spawn_radius_frac: float = 0.35    # spawn circle radius as fraction of arena size

    # Projectile
    projectile_speed: float = 500.0    # units/second
    projectile_damage: float = 8.0
    projectile_lifetime: int = 40      # ticks
    projectile_radius: float = 2.0

    # Fire
    fire_cooldown_ticks: int = 5        # ticks between shots

    # Shield
    shield_max_energy: float = 100.0
    shield_drain: float = 2.0          # energy drained per tick while active
    shield_regen: float = 1.0          # energy regenerated per tick while inactive
    shield_hit_cost: float = 15.0      # extra energy cost per blocked hit

    # Dash
    dash_speed: float = 600.0          # units/second burst velocity during dash tick
    dash_cooldown_ticks: int = 30      # ticks between dashes

    # Bot supervision
    bot_timeout_ms: int = 50           # per-tick decide() budget
    bot_max_violations: int = 3        # timeouts/exceptions before bot is benched

    @classmethod
    def from_env(cls, overrides: dict | None = None) -> "Config":
        env = os.environ.get
        cfg = cls(
            arena_size=_get_float("ARENA_SIZE", 1000.0),
            tick_rate=_get_int("TICK_RATE", 10),
            max_ticks=_get_int("MAX_TICKS", 1200),
            seed=_get_int("SEED", -1) if env("SEED") not in (None, "") else None,
            fighter_hp=_get_float("FIGHTER_HP", 100.0),
            fighter_radius=_get_float("FIGHTER_RADIUS", 12.0),
            max_speed=_get_float("MAX_SPEED", 120.0),
            spawn_radius_frac=_get_float("SPAWN_RADIUS_FRAC", 0.35),
            projectile_speed=_get_float("PROJECTILE_SPEED", 500.0),
            projectile_damage=_get_float("PROJECTILE_DAMAGE", 8.0),
            projectile_lifetime=_get_int("PROJECTILE_LIFETIME", 40),
            projectile_radius=_get_float("PROJECTILE_RADIUS", 2.0),
            fire_cooldown_ticks=_get_int("FIRE_COOLDOWN_TICKS", 5),
            shield_max_energy=_get_float("SHIELD_MAX_ENERGY", 100.0),
            shield_drain=_get_float("SHIELD_DRAIN", 2.0),
            shield_regen=_get_float("SHIELD_REGEN", 1.0),
            shield_hit_cost=_get_float("SHIELD_HIT_COST", 15.0),
            dash_speed=_get_float("DASH_SPEED", 600.0),
            dash_cooldown_ticks=_get_int("DASH_COOLDOWN_TICKS", 30),
            bot_timeout_ms=_get_int("BOT_TIMEOUT_MS", 50),
            bot_max_violations=_get_int("BOT_MAX_VIOLATIONS", 3),
        )
        if overrides:
            for key, value in overrides.items():
                if hasattr(cfg, key):
                    setattr(cfg, key, value)
                else:
                    raise ValueError(f"Unknown config key: {key}")
        return cfg

    @property
    def dt(self) -> float:
        return 1.0 / self.tick_rate
