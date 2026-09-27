#!/usr/bin/env python3
"""House-fighter balance benchmark.

Runs duel matchup matrices and 5-way royale sampling across multiple seeds
and prints win-rate tables. Use after any engine or bot change to catch
dominance regressions.

Usage:  .venv/bin/python scripts/balance_benchmark.py [--duels N] [--royales N]
"""
import argparse
import collections
import itertools
import sys

sys.path.insert(0, ".")

from arena.config import Config
from arena.engine import simulate
from arena.house_fighters import HOUSE_BOT_IDS, make_house_bot


def base(winner_id: str) -> str:
    return winner_id.rsplit("-", 1)[0] if winner_id else ""


def run_duels(cfg, per_matchup: int):
    wins = collections.Counter()
    games = collections.Counter()
    draws = 0
    matrix = {}
    for a, b in itertools.combinations(HOUSE_BOT_IDS, 2):
        for s in range(per_matchup):
            bots = [
                (f"{bid}-{i + 1}", make_house_bot(bid, 1000 + s, i))
                for i, bid in enumerate([a, b])
            ]
            r, _ = simulate(bots, cfg)
            games[a] += 1
            games[b] += 1
            if r["draw"]:
                draws += 1
            else:
                wins[base(r["winner"])] += 1
                matrix[(a, b)] = matrix.get((a, b), 0) + (r["winner"] or "").startswith(a)
    return wins, games, draws, matrix


def run_royales(cfg, n: int):
    """4-fighter royale (the official format): all 5-choose-4 combos."""
    wins = collections.Counter()
    games = collections.Counter()
    draws = 0
    per_combo = max(1, n // 5)
    for combo in itertools.combinations(HOUSE_BOT_IDS, 4):
        for s in range(per_combo):
            bots = [
                (f"{bid}-{i + 1}", make_house_bot(bid, 5000 + s, i))
                for i, bid in enumerate(combo)
            ]
            r, _ = simulate(bots, cfg)
            for bid in combo:
                games[bid] += 1
            if r["draw"]:
                draws += 1
            else:
                wins[base(r["winner"])] += 1
    return wins, games, draws


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--duels", type=int, default=12, help="games per duel matchup")
    ap.add_argument("--royales", type=int, default=60,
                    help="4-way royale games (split across 5 combos)")
    args = ap.parse_args()

    cfg = Config.from_env()
    print("== DUELS ==")
    wins, games, draws, _ = run_duels(cfg, args.duels)
    for bid in HOUSE_BOT_IDS:
        pct = wins[bid] / games[bid] * 100 if games[bid] else 0
        print(f"{bid:10s} {wins[bid]:3d}/{games[bid]:3d} ({pct:4.0f}%)")
    print(f"draws: {draws}")
    worst = max(wins[bid] / games[bid] for bid in HOUSE_BOT_IDS)
    best = min(wins[bid] / games[bid] for bid in HOUSE_BOT_IDS)
    print(f"duel spread: {best*100:.0f}% - {worst*100:.0f}%")

    print("\n== ROYALE (4-way, official format) ==")
    wins2, games2, draws2 = run_royales(cfg, args.royales)
    for bid in HOUSE_BOT_IDS:
        pct = wins2[bid] / games2[bid] * 100 if games2[bid] else 0
        print(f"{bid:10s} {wins2[bid]:3d}/{games2[bid]:3d} ({pct:4.0f}%)")
    print(f"draws: {draws2}")

    # Regression flags: warn on extreme dominance/irrelevance.
    problems = []
    for bid in HOUSE_BOT_IDS:
        d = wins[bid] / games[bid]
        if d >= 0.85:
            problems.append(f"{bid} dominates duels ({d*100:.0f}%)")
        if d <= 0.05:
            problems.append(f"{bid} irrelevant in duels ({d*100:.0f}%)")
        r = wins2[bid] / games2[bid] if games2[bid] else 0
        if r >= 0.55:
            problems.append(f"{bid} dominates royales ({r*100:.0f}%)")
        if r == 0:
            problems.append(f"{bid} never wins royales")
    if problems:
        print("\nWARNINGS:")
        for p in problems:
            print(f"  ! {p}")
    else:
        print("\nNo extreme dominance detected.")


if __name__ == "__main__":
    main()
