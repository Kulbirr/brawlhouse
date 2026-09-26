"""
Core simulation: fixed-timestep 2D top-down battle royale.

- Square arena with walls (fighters are clamped, projectiles die on walls).
- Fixed tick loop; all physics uses cfg.dt so behaviour scales with tick_rate.
- Per-tick snapshots are plain JSON-serializable dicts (phase 3 streams these).
- Bot faults (exceptions, timeouts) are caught per tick; the fighter falls back
  to a safe stand-still action and, after too many violations, is benched
  (replaced by NullBot) so a fight can never crash.
- Determinism: same seed + same bots + same decisions -> bit-identical result.
  (Determinism holds for bots that are themselves deterministic and do not
  hit the wall-clock timeout path.)
"""

import math
import random
import threading

from .bot_api import FighterBot, NullBot, SAFE_ACTION, validate_action
from .config import Config


def _r3(v: float) -> float:
    return round(float(v), 3)


class _Fighter:
    __slots__ = (
        "id", "bot", "x", "y", "vx", "vy", "heading", "hp",
        "fire_cd", "dash_cd", "shield_energy", "shield_active",
        "alive", "violations", "benched", "kills", "stat_mult", "dmg_mult",
        "_pending_aim", "_pending_fire",
    )

    def __init__(self, fid: str, bot: FighterBot, x: float, y: float,
                 heading: float, hp: float, shield_energy: float,
                 stat_mult: float = 1.0):
        self.id = fid
        self.bot = bot
        self.x = x
        self.y = y
        self.vx = 0.0
        self.vy = 0.0
        self.heading = heading
        self.hp = hp
        self.stat_mult = stat_mult  # FT rarity multiplier (1.0 = house)
        self.dmg_mult = stat_mult
        self.fire_cd = 0
        self.dash_cd = 0
        self.shield_energy = shield_energy
        self.shield_active = False
        self.alive = True
        self.violations = 0
        self.benched = False
        self.kills = 0
        self._pending_aim = 0.0
        self._pending_fire = False


class _Projectile:
    __slots__ = ("id", "owner", "x", "y", "vx", "vy", "damage", "life")

    def __init__(self, pid: int, owner: str, x: float, y: float,
                 vx: float, vy: float, damage: float, life: int):
        self.id = pid
        self.owner = owner
        self.x = x
        self.y = y
        self.vx = vx
        self.vy = vy
        self.damage = damage
        self.life = life


class Battle:
    """One battle-royale fight between 2-8 fighters."""

    def __init__(self, bots: list[tuple[str, FighterBot]], cfg: Config | None = None,
                 stat_mults: dict[str, float] | None = None):
        if not 2 <= len(bots) <= 8:
            raise ValueError(f"Battle needs 2-8 fighters, got {len(bots)}")
        self.cfg = cfg or Config.from_env()
        # Phase 1 (FT economy): per-fighter stat multipliers from rarity.
        # Maps fighter id -> multiplier on HP and projectile damage.
        # Missing ids default to 1.0 (house fighters).
        self.stat_mults = dict(stat_mults or {})
        self.rng = random.Random(self.cfg.seed)
        self.tick = 0
        self.elimination_order: list[str] = []
        self.notes: list[str] = []
        self._projectile_id = 0
        self.projectiles: list[_Projectile] = []
        self.fighters: list[_Fighter] = []
        self._spawn_fighters(bots)

    # ------------------------------------------------------------------ setup
    def _spawn_fighters(self, bots):
        n = len(bots)
        radius = self.cfg.arena_size * self.cfg.spawn_radius_frac
        cx = cy = self.cfg.arena_size / 2.0
        angle_offset = self.rng.uniform(0, 2 * math.pi)
        for i, (fid, bot) in enumerate(bots):
            angle = angle_offset + 2 * math.pi * i / n
            x = cx + radius * math.cos(angle)
            y = cy + radius * math.sin(angle)
            heading = math.atan2(cy - y, cx - x)  # face center
            mult = float(self.stat_mults.get(fid, 1.0) or 1.0)
            self.fighters.append(_Fighter(
                fid, bot, x, y, heading,
                hp=self.cfg.fighter_hp * mult,
                shield_energy=self.cfg.shield_max_energy,
                stat_mult=mult,
            ))

    # ------------------------------------------------------------------ state
    def _fighter_state(self, f: _Fighter) -> dict:
        return {
            "tick": self.tick,
            "fighter": {
                "id": f.id, "x": f.x, "y": f.y, "vx": f.vx, "vy": f.vy,
                "heading": f.heading, "hp": f.hp,
                "fire_cooldown": f.fire_cd, "dash_cooldown": f.dash_cd,
                "shield_energy": f.shield_energy, "shield_active": f.shield_active,
            },
            "enemies": [
                {"id": e.id, "x": e.x, "y": e.y, "vx": e.vx, "vy": e.vy,
                 "heading": e.heading, "hp": e.hp, "alive": e.alive}
                for e in self.fighters if e is not f
            ],
            "projectiles": [
                {"id": p.id, "x": p.x, "y": p.y, "vx": p.vx, "vy": p.vy,
                 "owner": p.owner, "damage": p.damage, "life": p.life}
                for p in self.projectiles
            ],
            "arena": {"size": self.cfg.arena_size, "tick_rate": self.cfg.tick_rate},
            "alive_count": sum(1 for e in self.fighters if e.alive),
        }

    def snapshot(self) -> dict:
        """JSON-serializable snapshot of the current tick (what phase 3 streams)."""
        return {
            "tick": self.tick,
            "fighters": [
                {"id": f.id, "x": _r3(f.x), "y": _r3(f.y),
                 "vx": _r3(f.vx), "vy": _r3(f.vy), "heading": _r3(f.heading),
                 "hp": _r3(f.hp), "alive": f.alive,
                 "fire_cooldown": f.fire_cd, "dash_cooldown": f.dash_cd,
                 "shield_energy": _r3(f.shield_energy),
                 "shield_active": f.shield_active, "kills": f.kills,
                 "benched": f.benched, "stat_mult": f.stat_mult}
                for f in self.fighters
            ],
            "projectiles": [
                {"id": p.id, "owner": p.owner, "x": _r3(p.x), "y": _r3(p.y),
                 "vx": _r3(p.vx), "vy": _r3(p.vy),
                 "damage": _r3(p.damage), "life": p.life}
                for p in self.projectiles
            ],
        }

    # ------------------------------------------------------------------ tick
    def _decide_all(self) -> dict[str, dict]:
        """Collect one action per alive fighter, catching faults. Never raises.

        Each bot runs in a daemon thread with a per-tick wall-clock budget.
        Exceptions and timeouts count as violations and get the safe default
        action; too many violations benches the bot (replaced by NullBot).
        """
        cfg = self.cfg
        timeout = cfg.bot_timeout_ms / 1000.0
        results: dict[str, dict] = {}
        faulted: set[str] = set()

        def worker(f: _Fighter, state: dict) -> None:
            try:
                results[f.id] = f.bot.decide(state)
            except Exception:
                faulted.add(f.id)

        threads: list[tuple[_Fighter, threading.Thread]] = []
        for f in self.fighters:
            if not f.alive:
                continue
            t = threading.Thread(target=worker, args=(f, self._fighter_state(f)),
                                 daemon=True, name=f"arena-bot-{f.id}")
            t.start()
            threads.append((f, t))

        actions: dict[str, dict] = {}
        for f, t in threads:
            t.join(timeout=timeout)
            if t.is_alive() or f.id in faulted or f.id not in results:
                # Too slow or crashed: violation + safe default, never crash.
                f.violations += 1
                actions[f.id] = dict(SAFE_ACTION)
                if f.violations >= cfg.bot_max_violations and not f.benched:
                    f.benched = True
                    f.bot = NullBot()
                    self.notes.append(
                        f"bot {f.id} benched at tick {self.tick} "
                        f"({f.violations} violations)"
                    )
            else:
                actions[f.id] = results[f.id]
        return actions

    # ------------------------------------------------------------------ combat
    def _find_hit(self, p: _Projectile) -> _Fighter | None:
        """First alive fighter (other than the owner) within hit radius."""
        hit_radius = self.cfg.fighter_radius + self.cfg.projectile_radius
        for f in self.fighters:
            if not f.alive or f.id == p.owner:
                continue
            if math.hypot(f.x - p.x, f.y - p.y) <= hit_radius:
                return f
        return None

    def _resolve_hit(self, p: _Projectile, target: _Fighter) -> None:
        """Apply a projectile hit: shield block or damage + elimination."""
        cfg = self.cfg
        if target.shield_active and target.shield_energy > 0:
            target.shield_energy = max(
                0.0, target.shield_energy - cfg.shield_hit_cost)
            if target.shield_energy <= 0:
                target.shield_active = False
        else:
            target.hp -= p.damage
            if target.hp <= 0:
                target.hp = 0.0
                target.alive = False
                self.elimination_order.append(target.id)
                owner = next(
                    (x for x in self.fighters if x.id == p.owner), None)
                if owner is not None:
                    owner.kills += 1
        # projectile is consumed on any hit

    def step(self) -> None:
        """Advance the simulation by exactly one tick."""
        cfg = self.cfg
        dt = cfg.dt
        actions = self._decide_all()

        # 1. Movement / dash / shield intent / fire intent.
        for f in self.fighters:
            if not f.alive:
                continue
            a = validate_action(actions.get(f.id), cfg.max_speed)
            mx, my = a["move"]
            if a["dash"] and f.dash_cd <= 0:
                mag = math.hypot(mx, my)
                if mag > 1e-9:
                    dx, dy = mx / mag, my / mag
                else:
                    dx, dy = math.cos(f.heading), math.sin(f.heading)
                f.vx, f.vy = dx * cfg.dash_speed, dy * cfg.dash_speed
                f.dash_cd = cfg.dash_cooldown_ticks
            else:
                f.vx, f.vy = mx, my
            # Body heading follows movement; turret aim is separate.
            if math.hypot(f.vx, f.vy) > 1e-9:
                f.heading = math.atan2(f.vy, f.vx)
            # Shield.
            want_shield = a["shield"] and f.shield_energy > 0
            f.shield_active = want_shield
            if want_shield:
                f.shield_energy = max(0.0, f.shield_energy - cfg.shield_drain)
            else:
                f.shield_energy = min(cfg.shield_max_energy,
                                      f.shield_energy + cfg.shield_regen)
            f._pending_aim = a["aim"]
            f._pending_fire = a["fire"]

        # 2. Integrate positions, clamp to walls.
        r = cfg.fighter_radius
        for f in self.fighters:
            if not f.alive:
                continue
            f.x = min(max(f.x + f.vx * dt, r), cfg.arena_size - r)
            f.y = min(max(f.y + f.vy * dt, r), cfg.arena_size - r)

        # 3. Firing.
        for f in self.fighters:
            if not f.alive:
                continue
            if f._pending_fire and f.fire_cd <= 0:
                aim = f._pending_aim
                nx = f.x + math.cos(aim) * (r + cfg.projectile_radius + 1.0)
                ny = f.y + math.sin(aim) * (r + cfg.projectile_radius + 1.0)
                self._projectile_id += 1
                p = _Projectile(
                    self._projectile_id, f.id, nx, ny,
                    math.cos(aim) * cfg.projectile_speed,
                    math.sin(aim) * cfg.projectile_speed,
                    cfg.projectile_damage * f.dmg_mult, cfg.projectile_lifetime,
                )
                # Point-blank: a target closer than the spawn offset would
                # otherwise never be hit (the shot spawns past it and the
                # per-tick hit check runs after movement). Resolve it now.
                target = self._find_hit(p)
                if target is not None:
                    self._resolve_hit(p, target)
                else:
                    self.projectiles.append(p)
                f.fire_cd = cfg.fire_cooldown_ticks

        # 4. Projectiles: move, expire, hit walls, hit fighters.
        for p in self.projectiles:
            p.x += p.vx * dt
            p.y += p.vy * dt
            p.life -= 1
        survivors: list[_Projectile] = []
        for p in self.projectiles:
            if p.life <= 0:
                continue
            if not (0 <= p.x <= cfg.arena_size and 0 <= p.y <= cfg.arena_size):
                continue  # died on wall
            target = self._find_hit(p)
            if target is None:
                survivors.append(p)
                continue
            self._resolve_hit(p, target)
            # projectile is consumed on any hit
        self.projectiles = survivors

        # 5. Cooldowns.
        for f in self.fighters:
            if f.fire_cd > 0:
                f.fire_cd -= 1
            if f.dash_cd > 0:
                f.dash_cd -= 1

        self.tick += 1

    # ------------------------------------------------------------------ run
    def alive_fighters(self) -> list[_Fighter]:
        return [f for f in self.fighters if f.alive]

    def is_over(self) -> bool:
        return len(self.alive_fighters()) <= 1 or self.tick >= self.cfg.max_ticks

    def result(self) -> dict:
        alive = self.alive_fighters()
        winner = None
        draw = False
        reason = ""
        if len(alive) == 1:
            winner = alive[0].id
            reason = "last_fighter_standing"
        elif len(alive) == 0:
            draw = True
            reason = "mutual_elimination"
        else:
            best_hp = max(f.hp for f in alive)
            best = [f.id for f in alive if f.hp == best_hp]
            if len(best) == 1:
                winner = best[0]
                reason = "highest_hp_at_max_ticks"
            else:
                draw = True
                reason = "hp_tie_at_max_ticks"
        return {
            "winner": winner,
            "draw": draw,
            "reason": reason,
            "ticks": self.tick,
            "elimination_order": list(self.elimination_order),
            "kills": {f.id: f.kills for f in self.fighters},
            "final_hp": {f.id: round(f.hp, 3) for f in self.fighters},
            "notes": list(self.notes),
        }

    def run(self, snapshot_every: int = 0) -> tuple[dict, list[dict]]:
        """Run to completion. Returns (result, snapshots)."""
        snapshots: list[dict] = []
        if snapshot_every > 0:
            snapshots.append(self.snapshot())
        while not self.is_over():
            self.step()
            if snapshot_every > 0 and self.tick % snapshot_every == 0:
                snapshots.append(self.snapshot())
        return self.result(), snapshots


def simulate(bots: list[tuple[str, FighterBot]], cfg: Config | None = None,
             snapshot_every: int = 0) -> tuple[dict, list[dict]]:
    """Headless helper: run a full fight, return (result, snapshots)."""
    return Battle(bots, cfg).run(snapshot_every=snapshot_every)
