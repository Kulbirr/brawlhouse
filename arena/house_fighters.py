"""
House fighter bots (phase 2).

Five distinct AI fighters subclassing FighterBot. Pure code, zero LLM/API
calls; decide() is O(enemies + projectiles) arithmetic and runs far under
the 50ms per-tick budget.

All per-bot tunables live as class attributes (never magic numbers inline),
so phase 7 can tune balance without touching logic.

The FIGHTERS registry at the bottom holds the public metadata
(id, name, tagline, description, bot_class). Names/taglines/descriptions
live ONLY in the registry, never inside bot logic: phase 3 (backend) loads
this registry to list and spawn fighters, and phase 7 (admin panel) will
rename/rebrand fighters through it without code changes. Codenames are
placeholder-style (e.g. "IRON-1"); the final product brand comes later.
"""

import math
import random

from .bot_api import FighterBot
from .config import Config


# ---------------------------------------------------------------------------
# shared helpers
# ---------------------------------------------------------------------------

def _clamp_speed(vec, max_speed):
    x, y = vec
    s = math.hypot(x, y)
    if s > max_speed > 0:
        k = max_speed / s
        return [x * k, y * k]
    return [x, y]


def _alive_enemies(state):
    return [e for e in state["enemies"] if e["alive"]]


def _nearest(me, enemies):
    """Return (closest alive enemy, distance). (None, inf) if no enemies."""
    best, best_d2 = None, float("inf")
    for e in enemies:
        d2 = (e["x"] - me["x"]) ** 2 + (e["y"] - me["y"]) ** 2
        if d2 < best_d2:
            best_d2, best = d2, e
    if best is None:
        return None, float("inf")
    return best, math.sqrt(best_d2)


def _lead_aim(me, target, proj_speed):
    """Aim angle that leads the target by projectile travel time.

    Returns (angle_radians, current_distance).
    """
    dx = target["x"] - me["x"]
    dy = target["y"] - me["y"]
    dist = math.hypot(dx, dy) or 1e-6
    lead = dist / proj_speed if proj_speed > 0 else 0.0
    return math.atan2(dy + target["vy"] * lead,
                      dx + target["vx"] * lead), dist


def _incoming_projectiles(state, me, range_):
    """Enemy projectiles within range_ that are travelling toward me."""
    px, py = me["x"], me["y"]
    incoming = []
    for p in state["projectiles"]:
        if p["owner"] == me["id"]:
            continue
        dx, dy = px - p["x"], py - p["y"]
        if math.hypot(dx, dy) < range_ and (p["vx"] * dx + p["vy"] * dy) > 0:
            incoming.append(p)
    return incoming


class _HouseBot(FighterBot):
    """Shared base for house fighters.

    Loads physics constants from the env-driven config (the same source a
    default Battle uses) so aim-leading and speed planning stay consistent
    with the simulation. Keeps a deterministic per-bot tick counter for
    state machines; any randomness comes from a constructor-seeded RNG, so
    same seed -> identical behaviour.
    """

    WALL_MARGIN = 90.0  # start steering to center within this of a wall

    def __init__(self, seed: int = 0):
        self.rng = random.Random(seed)
        cfg = Config.from_env()
        self.proj_speed = cfg.projectile_speed
        self.max_speed = cfg.max_speed
        # Start the internal tick counter at a seed-derived offset so two
        # identical bots in one fight don't flip/orbit in lockstep (which
        # produced symmetric 1200-tick mirror draws). Deterministic: same
        # seed -> same offset -> identical replays.
        self.tick = seed % 32

    # -- movement helpers ------------------------------------------------
    def _wall_steer(self, state, move):
        """Blend in a center-ward push when close to a wall. Never raises."""
        me = state["fighter"]
        size = state["arena"]["size"]
        m = self.WALL_MARGIN
        sx = sy = 0.0
        if me["x"] < m:
            sx = (m - me["x"]) / m
        elif me["x"] > size - m:
            sx = -((me["x"] - (size - m))) / m
        if me["y"] < m:
            sy = (m - me["y"]) / m
        elif me["y"] > size - m:
            sy = -((me["y"] - (size - m))) / m
        if sx or sy:
            move = [move[0] + sx * self.max_speed,
                    move[1] + sy * self.max_speed]
            move = _clamp_speed(move, self.max_speed)
        return move

    def _idle_action(self):
        return {"move": [0.0, 0.0], "aim": 0.0, "fire": False,
                "shield": False, "dash": False}


# ---------------------------------------------------------------------------
# 1. Rusher
# ---------------------------------------------------------------------------

class RusherBot(_HouseBot):
    """IRON-1. Charges the nearest enemy, circle-strafes up close, fires
    nonstop, and dashes in to close gaps. Pure aggression."""

    name = "iron-1"

    STRAFE_RANGE = 95.0       # inside this, strafe instead of charging in
    STRAFE_FLIP_TICKS = 22    # reverse strafe direction this often
    STRAFE_SPEED = 0.95       # strafe speed as fraction of max
    INWARD_DRIFT = 0.35       # how hard it still pushes in while strafing
    CHARGE_SPEED = 1.0
    WEAVE_AMP = 0.25          # was 0.5: halved. A juggernaut should be EASY
                              # to hit — its defense is reaching you, not
                              # dodging. (0.5 made predictive aim whiff and
                              # let it out-trade snipers at range.)
    WEAVE_FREQ = 0.30         # weave oscillation, radians per tick
    FIRE_RANGE = 700.0
    DASH_MIN_DIST = 200.0     # dash to close gaps wider than this
    SHIELD_REACT_RANGE = 140.0  # brawler's guard: last-second blocks only
    RETREAT_HP = 35.0         # below this HP, break off instead of brawling
    RETREAT_RADIUS = 250.0    # ...when the enemy is this close
    WALL_MARGIN = 90.0

    def __init__(self, seed: int = 0):
        super().__init__(seed)
        self._strafe_dir = 1 if self.rng.random() < 0.5 else -1

    def decide(self, state):
        self.tick += 1
        me = state["fighter"]
        target, _ = _nearest(me, _alive_enemies(state))
        if target is None:
            return self._idle_action()
        aim, dist = _lead_aim(me, target, self.proj_speed)
        ang = math.atan2(target["y"] - me["y"], target["x"] - me["x"])

        if dist < self.STRAFE_RANGE:
            if self.tick % self.STRAFE_FLIP_TICKS == 0:
                self._strafe_dir *= -1
            move = [
                (math.cos(ang) * self.INWARD_DRIFT
                 - math.sin(ang) * self._strafe_dir * self.STRAFE_SPEED),
                (math.sin(ang) * self.INWARD_DRIFT
                 + math.cos(ang) * self._strafe_dir * self.STRAFE_SPEED),
            ]
            move = _clamp_speed([c * self.max_speed for c in move],
                                self.max_speed)
        else:
            # charge in with a lateral weave so predictive aim keeps missing
            weave = math.sin(self.tick * self.WEAVE_FREQ) * self.WEAVE_AMP
            move = [
                (math.cos(ang) * self.CHARGE_SPEED
                 - math.sin(ang) * weave),
                (math.sin(ang) * self.CHARGE_SPEED
                 + math.cos(ang) * weave),
            ]
            move = _clamp_speed([c * self.max_speed for c in move],
                                self.max_speed)

        # hurt badly with others still in the fight: break off and let
        # them weaken each other (in a duel there is nowhere to hide)
        if (me["hp"] < self.RETREAT_HP and dist < self.RETREAT_RADIUS
                and state["alive_count"] > 2):
            move = [-math.cos(ang) * self.max_speed,
                    -math.sin(ang) * self.max_speed]

        move = self._wall_steer(state, move)
        return {
            "move": move,
            "aim": aim,
            "fire": dist < self.FIRE_RANGE,
            "shield": bool(_incoming_projectiles(
                state, me, self.SHIELD_REACT_RANGE)),
            "dash": dist > self.DASH_MIN_DIST and me["dash_cooldown"] == 0,
        }


# ---------------------------------------------------------------------------
# 2. Sniper / keeper
# ---------------------------------------------------------------------------

class SniperBot(_HouseBot):
    """HAWK-2. Keeps its distance, leads targets with predictive aim, and
    fires carefully from range. Kites away when crowded, dashes out of
    trouble, shields against incoming fire."""

    name = "hawk-2"

    KEEP_MIN = 380.0          # closer than this -> kite away
    KEEP_MAX = 560.0          # farther than this -> close in
    ENDGAME_ALIVE = 2         # with this few fighters left...
    ENDGAME_KEEP_MIN = 300.0  # ...keep sniping, don't brawl: closing in on
    ENDGAME_KEEP_MAX = 480.0  # a rusher to "finish the fight" is suicide.
                              # (Used to close to 180-320 and trade point
                              # blank — it lost every duel to IRON-1.)
    KITE_SPEED = 1.0
    APPROACH_SPEED = 0.75
    ORBIT_SPEED = 0.45        # sideways drift while in the comfort band
    ORBIT_FLIP_TICKS = 22     # flip often: steady orbits are easy to lead
    FIRE_RANGE = 820.0
    DASH_PANIC_RANGE = 160.0  # dash away when an enemy gets this close
    SHIELD_REACT_RANGE = 140.0  # last-second blocks: keeps energy in reserve
    WALL_MARGIN = 120.0

    def __init__(self, seed: int = 0):
        super().__init__(seed)
        self._orbit_dir = 1 if self.rng.random() < 0.5 else -1

    def decide(self, state):
        self.tick += 1
        me = state["fighter"]
        target, _ = _nearest(me, _alive_enemies(state))
        if target is None:
            return self._idle_action()
        aim, dist = _lead_aim(me, target, self.proj_speed)
        ang = math.atan2(target["y"] - me["y"], target["x"] - me["x"])

        # endgame: few fighters left -> stop kiting, close in and finish
        if state["alive_count"] <= self.ENDGAME_ALIVE:
            keep_min, keep_max = self.ENDGAME_KEEP_MIN, self.ENDGAME_KEEP_MAX
        else:
            keep_min, keep_max = self.KEEP_MIN, self.KEEP_MAX

        if dist < keep_min:
            # Kite away in an ARC, not a straight line: pure straight-line
            # kiting backs into walls/corners where the rusher catches it.
            # Blending retreat with the orbit direction circles the arena.
            move = [(-math.cos(ang) * 0.75
                     - math.sin(ang) * self._orbit_dir * 0.65)
                    * self.max_speed * self.KITE_SPEED,
                    (-math.sin(ang) * 0.75
                     + math.cos(ang) * self._orbit_dir * 0.65)
                    * self.max_speed * self.KITE_SPEED]
        elif dist > keep_max:
            move = [math.cos(ang) * self.max_speed * self.APPROACH_SPEED,
                    math.sin(ang) * self.max_speed * self.APPROACH_SPEED]
        else:
            if self.tick % self.ORBIT_FLIP_TICKS == 0:
                self._orbit_dir *= -1
            move = [-math.sin(ang) * self._orbit_dir
                    * self.max_speed * self.ORBIT_SPEED,
                    math.cos(ang) * self._orbit_dir
                    * self.max_speed * self.ORBIT_SPEED]

        move = self._wall_steer(state, move)
        shield = bool(_incoming_projectiles(state, me,
                                            self.SHIELD_REACT_RANGE))
        return {
            "move": move,
            "aim": aim,
            "fire": dist < self.FIRE_RANGE,
            "shield": shield,
            "dash": (dist < self.DASH_PANIC_RANGE
                     and me["dash_cooldown"] == 0),
        }


# ---------------------------------------------------------------------------
# 3. Defender
# ---------------------------------------------------------------------------

class DefenderBot(_HouseBot):
    """AEGIS-4. Holds ground near the arena center, raises its shield
    against incoming fire or point-blank attackers, and punishes anyone
    who comes in by dropping the shield to fire back, then guarding again.
    Every shot is a moment of vulnerability: the engine drops the shield
    for the rest of the tick when it fires."""

    name = "aegis-4"

    LEASH_RADIUS = 240.0      # never chases beyond this from the anchor
    CRUISE_SPEED = 0.55       # speed when returning to the anchor
    HOLD_SPEED = 0.25         # repositioning speed while holding ground
    CROWD_RANGE = 200.0       # yield ground slowly when crowded this close
    FIRE_RANGE = 540.0
    SHIELD_PROJ_RANGE = 140.0  # shield up against projectiles inside this
    MELEE_SHIELD_RANGE = 110.0  # shield up when an enemy is this close
    SHIELD_MIN_ENERGY = 25.0   # below this, drop the shield and punch back:
                               # turtling on fumes means never firing, so go
                               # down swinging and let it recharge
    DASH_ESCAPE_RANGE = 150.0
    WALL_MARGIN = 90.0

    def decide(self, state):
        self.tick += 1
        me = state["fighter"]
        size = state["arena"]["size"]
        ax = ay = size / 2.0  # anchor: arena center
        target, _ = _nearest(me, _alive_enemies(state))

        dxa, dya = ax - me["x"], ay - me["y"]
        da = math.hypot(dxa, dya) or 1e-6
        if target is not None:
            aim, dist = _lead_aim(me, target, self.proj_speed)
            ang = math.atan2(target["y"] - me["y"], target["x"] - me["x"])
        else:
            aim, dist, ang = 0.0, float("inf"), 0.0

        if da > self.LEASH_RADIUS:
            move = [dxa / da * self.max_speed * self.CRUISE_SPEED,
                    dya / da * self.max_speed * self.CRUISE_SPEED]
        elif target is not None and dist < self.CROWD_RANGE:
            move = [-math.cos(ang) * self.max_speed * self.HOLD_SPEED,
                    -math.sin(ang) * self.max_speed * self.HOLD_SPEED]
        else:
            move = [dxa / da * self.max_speed * self.HOLD_SPEED,
                    dya / da * self.max_speed * self.HOLD_SPEED]

        move = self._wall_steer(state, move)

        shield = False
        # Counter-punch cycling: only turtle while the shield has real
        # charge. On fumes, drop it and return fire instead of blocking
        # forever without shooting (a shielded fighter cannot fire).
        if target is not None and me["shield_energy"] > self.SHIELD_MIN_ENERGY:
            if _incoming_projectiles(state, me, self.SHIELD_PROJ_RANGE):
                shield = True
            elif dist < self.MELEE_SHIELD_RANGE:
                shield = True

        return {
            "move": move,
            "aim": aim,
            "fire": target is not None and dist < self.FIRE_RANGE,
            "shield": shield,
            "dash": (target is not None
                     and dist < self.DASH_ESCAPE_RANGE
                     and me["dash_cooldown"] == 0),
        }


# ---------------------------------------------------------------------------
# 4. Opportunist
# ---------------------------------------------------------------------------

class OpportunistBot(_HouseBot):
    """JACKAL-5. Hunts the weakest fighter, prefers targets already busy
    fighting someone else, gives the strongest enemy a wide berth, and
    rushes in to finish off low-HP victims."""

    name = "jackal-5"

    THIRD_PARTY_RANGE = 280.0  # an enemy this close to another is "busy"
    THIRD_PARTY_BONUS = 45.0   # effective-hp discount for busy targets
    AVOID_RADIUS = 260.0
    AVOID_WEIGHT = 1.0
    HOLD_RANGE = 420.0         # stay out of the brawl; let others bleed
    HOLD_SPEED = 0.85          # strafe fast: slow strafes are easy to lead
    STRAFE_FLIP_TICKS = 18     # flip often, stay unpredictable
    STRAFE_WEAVE_AMP = 0.35    # plus a lateral weave while strafing
    STRAFE_WEAVE_FREQ = 0.35
    APPROACH_SPEED = 1.0
    RETREAT_SPEED = 0.7
    FINISH_HP = 30.0           # at/below this the target is a candidate
                               # for a finishing rush...
    FINISH_ISOLATION_RADIUS = 250.0  # ...but only rush isolated victims;
                               # otherwise keep plinking from range
    ENDGAME_ALIVE = 2          # in a duel, stop circling and fight:
    ENDGAME_HOLD_RANGE = 150.0 # close in hard; point-blank volume beats
                               # even a good shield
    ENDGAME_HOLD_SPEED = 0.95
    ENDGAME_FINISH_HP = 50.0
    STICKY_MARGIN = 25.0       # keep the current target unless another scores
                               # this much better (focus fire secures kills)
    FIRE_RANGE = 660.0
    DASH_STRIKE_MIN = 140.0
    DASH_STRIKE_MAX = 420.0
    SHIELD_REACT_RANGE = 140.0  # last-second blocks: keeps energy in reserve
    WALL_MARGIN = 90.0

    def __init__(self, seed: int = 0):
        super().__init__(seed)
        self._strafe_dir = 1 if self.rng.random() < 0.5 else -1
        self._target_id = None

    def _pick_target(self, enemies):
        best, best_score, strongest = None, float("inf"), None
        scores = {}
        for e in enemies:
            if strongest is None or e["hp"] > strongest["hp"]:
                strongest = e
            busy = any(
                o is not e
                and math.hypot(o["x"] - e["x"], o["y"] - e["y"])
                < self.THIRD_PARTY_RANGE
                for o in enemies
            )
            score = e["hp"] - (self.THIRD_PARTY_BONUS if busy else 0.0)
            scores[e["id"]] = score
            if score < best_score:
                best, best_score = e, score
        # target stickiness: don't flip-flop between near-equal targets
        if self._target_id in scores:
            if scores[self._target_id] <= best_score + self.STICKY_MARGIN:
                best = next(e for e in enemies if e["id"] == self._target_id)
        self._target_id = best["id"] if best is not None else None
        return best, strongest

    def decide(self, state):
        self.tick += 1
        me = state["fighter"]
        enemies = _alive_enemies(state)
        if not enemies:
            return self._idle_action()
        target, strongest = self._pick_target(enemies)
        aim, dist = _lead_aim(me, target, self.proj_speed)
        ang = math.atan2(target["y"] - me["y"], target["x"] - me["x"])

        # endgame duel: stop circling, close in and fight for real
        endgame = state["alive_count"] <= self.ENDGAME_ALIVE
        hold_range = self.ENDGAME_HOLD_RANGE if endgame else self.HOLD_RANGE
        hold_speed = self.ENDGAME_HOLD_SPEED if endgame else self.HOLD_SPEED
        finish_hp = self.ENDGAME_FINISH_HP if endgame else self.FINISH_HP

        finishing = target["hp"] <= finish_hp
        if finishing:
            # only rush an isolated victim; charging into a crowd is suicide,
            # so otherwise fall through and keep plinking from range
            isolated = all(
                math.hypot(o["x"] - target["x"], o["y"] - target["y"])
                >= self.FINISH_ISOLATION_RADIUS
                for o in enemies if o is not target
            )
            if not isolated:
                finishing = False
        if finishing:
            move = [math.cos(ang) * self.max_speed,
                    math.sin(ang) * self.max_speed]
        elif dist > hold_range:
            move = [math.cos(ang) * self.max_speed * self.APPROACH_SPEED,
                    math.sin(ang) * self.max_speed * self.APPROACH_SPEED]
        elif dist < hold_range * 0.55:
            move = [-math.cos(ang) * self.max_speed * self.RETREAT_SPEED,
                    -math.sin(ang) * self.max_speed * self.RETREAT_SPEED]
        else:
            if self.tick % self.STRAFE_FLIP_TICKS == 0:
                self._strafe_dir *= -1
            # strafe perpendicular with a radial weave: hard to lead
            weave = (math.sin(self.tick * self.STRAFE_WEAVE_FREQ)
                     * self.STRAFE_WEAVE_AMP)
            move = [
                (-math.sin(ang) * self._strafe_dir * hold_speed
                 + math.cos(ang) * weave),
                (math.cos(ang) * self._strafe_dir * hold_speed
                 + math.sin(ang) * weave),
            ]
            move = _clamp_speed([c * self.max_speed for c in move],
                                self.max_speed)

        # give the strongest enemy a wide berth (unless it is the target)
        if strongest is not target:
            dxs, dys = me["x"] - strongest["x"], me["y"] - strongest["y"]
            ds = math.hypot(dxs, dys) or 1e-6
            if ds < self.AVOID_RADIUS:
                w = self.AVOID_WEIGHT * (1.0 - ds / self.AVOID_RADIUS)
                move = [move[0] + dxs / ds * self.max_speed * w,
                        move[1] + dys / ds * self.max_speed * w]
                move = _clamp_speed(move, self.max_speed)

        move = self._wall_steer(state, move)
        return {
            "move": move,
            "aim": aim,
            "fire": dist < self.FIRE_RANGE,
            "shield": bool(_incoming_projectiles(
                state, me, self.SHIELD_REACT_RANGE)),
            "dash": (finishing
                     and self.DASH_STRIKE_MIN < dist < self.DASH_STRIKE_MAX
                     and me["dash_cooldown"] == 0),
        }


# ---------------------------------------------------------------------------
# 5. Wild card: hit-and-run dasher
# ---------------------------------------------------------------------------

class WaspBot(_HouseBot):
    """WASP-6. A hit-and-run dasher with a sting cycle: stalks its prey by
    orbiting at mid range, then dashes straight through it guns blazing,
    then breaks contact fast while still shooting over its shoulder."""

    name = "wasp-6"

    STALK_TICKS = 50       # orbit the target, pepper it from range
    STRIKE_TICKS = 12      # dash through the target, full aggression
    RETREAT_TICKS = 30     # break contact, keep shooting backwards
    STRIKE_SHIELD_RANGE = 140.0  # guard up during the dive, but only
                                 # against actual incoming fire
    ORBIT_RANGE = 420.0        # stalk from safety, outside the brawl
    ORBIT_SPEED = 0.8
    RADIAL_SPEED = 0.7     # drift toward/away from the preferred orbit range
    STRIKE_SPEED = 1.0
    RETREAT_SPEED = 1.0
    STRIKE_DASH_MIN_DIST = 130.0  # only dash in from beyond this
    STRIKE_MIN_HP = 50.0      # too hurt to dive: keep stalking instead
    FIRE_RANGE = 700.0
    WALL_MARGIN = 90.0

    def __init__(self, seed: int = 0):
        super().__init__(seed)
        self._orbit_dir = 1 if self.rng.random() < 0.5 else -1

    def decide(self, state):
        self.tick += 1
        me = state["fighter"]
        enemies = _alive_enemies(state)
        target, _ = _nearest(me, enemies)
        if target is None:
            return self._idle_action()
        aim, dist = _lead_aim(me, target, self.proj_speed)
        ang = math.atan2(target["y"] - me["y"], target["x"] - me["x"])

        cycle = self.STALK_TICKS + self.STRIKE_TICKS + self.RETREAT_TICKS
        ph = self.tick % cycle
        dash = False
        shield = False
        # dive only when healthy; the strike goes after the weakest prey,
        # not just the nearest one
        striking = (self.STALK_TICKS <= ph
                    < self.STALK_TICKS + self.STRIKE_TICKS
                    and me["hp"] >= self.STRIKE_MIN_HP)
        if striking:
            prey = min(enemies, key=lambda e: e["hp"])
            aim, pdist = _lead_aim(me, prey, self.proj_speed)
            pang = math.atan2(prey["y"] - me["y"], prey["x"] - me["x"])
            move = [math.cos(pang) * self.max_speed * self.STRIKE_SPEED,
                    math.sin(pang) * self.max_speed * self.STRIKE_SPEED]
            dash = (pdist > self.STRIKE_DASH_MIN_DIST
                    and me["dash_cooldown"] == 0)
            shield = bool(_incoming_projectiles(
                state, me, self.STRIKE_SHIELD_RANGE))
        elif ph >= self.STALK_TICKS + self.STRIKE_TICKS:
            # break contact fast, still shooting over its shoulder
            move = [-math.cos(ang) * self.max_speed * self.RETREAT_SPEED,
                    -math.sin(ang) * self.max_speed * self.RETREAT_SPEED]
        else:
            # stalk: orbit at range (also used when too hurt to dive)
            radial = max(-1.0, min(1.0,
                                   (dist - self.ORBIT_RANGE) / self.ORBIT_RANGE))
            move = [
                (math.cos(ang) * radial * self.RADIAL_SPEED
                 - math.sin(ang) * self._orbit_dir * self.ORBIT_SPEED),
                (math.sin(ang) * radial * self.RADIAL_SPEED
                 + math.cos(ang) * self._orbit_dir * self.ORBIT_SPEED),
            ]
            move = _clamp_speed([c * self.max_speed for c in move],
                                self.max_speed)

        move = self._wall_steer(state, move)
        return {
            "move": move,
            "aim": aim,
            "fire": dist < self.FIRE_RANGE,
            "shield": shield,
            "dash": dash,
        }


# ---------------------------------------------------------------------------
# Registry: the public metadata lives here and ONLY here.
# ---------------------------------------------------------------------------

FIGHTERS = [
    {
        "id": "iron-1",
        "name": "IRON-1",
        "tagline": "Charges headfirst and never backs down.",
        "description": (
            "A relentless rusher that sprints at the nearest enemy, "
            "circle-strafes at point-blank range, and fires nonstop. "
            "Dashes in to close gaps and end fights fast."
        ),
        "bot_class": RusherBot,
    },
    {
        "id": "hawk-2",
        "name": "HAWK-2",
        "tagline": "Death from a distance, patience as a weapon.",
        "description": (
            "A disciplined sniper that keeps a 380-560 unit comfort zone, "
            "leads targets with predictive aim, and kites away when crowded. "
            "Dashes out of trouble and shields against incoming fire."
        ),
        "bot_class": SniperBot,
    },
    {
        "id": "aegis-4",
        "name": "AEGIS-4",
        "tagline": "Come closer. It has a shield for that.",
        "description": (
            "A conservative defender that holds ground near the arena center, "
            "raises its shield against incoming projectiles and point-blank "
            "attackers, drops the shield to punish anyone who steps in, then "
            "guards again. Every shot drops the shield for a tick — firing "
            "through a permanently-up shield is not possible."
        ),
        "bot_class": DefenderBot,
    },
    {
        "id": "jackal-5",
        "name": "JACKAL-5",
        "tagline": "It does not fight fair. It fights wounded targets.",
        "description": (
            "A cunning opportunist that hunts the lowest-HP fighter, "
            "third-parties enemies already busy fighting someone else, "
            "gives the strongest fighter a wide berth, and rushes in to "
            "finish off weakened victims."
        ),
        "bot_class": OpportunistBot,
    },
    {
        "id": "wasp-6",
        "name": "WASP-6",
        "tagline": "Sting, vanish, repeat.",
        "description": (
            "A hit-and-run dasher with a sting cycle: it stalks its prey by "
            "orbiting at mid range, dashes straight through it guns blazing, "
            "then breaks contact fast while still shooting over its shoulder."
        ),
        "bot_class": WaspBot,
    },
]

HOUSE_BOT_IDS = [entry["id"] for entry in FIGHTERS]


def make_house_bot(bot_id: str, seed: int = 0, index: int = 0) -> FighterBot:
    """Instantiate one house fighter by registry id.

    seed/index mirror the CLI factory convention: same (bot_id, seed, index)
    always builds a behaviourally identical bot.
    """
    key = bot_id.strip().lower()
    for entry in FIGHTERS:
        if entry["id"] == key:
            return entry["bot_class"](seed=seed * 1000 + index + 7)
    raise KeyError(
        f"Unknown house fighter {bot_id!r}. Available: {HOUSE_BOT_IDS}")


def build_house_bots(spec: str, seed: int = 0) -> list[tuple[str, FighterBot]]:
    """Parse a comma-separated id spec into (fighter_id, bot) pairs."""
    ids = [b.strip().lower() for b in spec.split(",") if b.strip()]
    if not 2 <= len(ids) <= 8:
        raise ValueError(f"Need 2-8 bots, got {len(ids)}: {spec!r}")
    return [(f"{bid}-{i + 1}", make_house_bot(bid, seed, i))
            for i, bid in enumerate(ids)]
