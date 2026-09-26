# FT Economy Spec — Agent Arena V2

Status: design approved by owner, 2026-09-26. Not yet built.
Scope: V1 (house fighters, hire, bet, watch) stays untouched. This spec adds the player-fighter economy on top.

Terminology: fighters are **born**, never "minted". They are **FTs** (Fighter Tokens), not NFTs.
Every FT has a unique key (`FT-0001`, …) and is tradeable on our own marketplace.

---

## 1. Being born

- Fixed **born fee** of **0.1 SOL** (admin-configurable). Same wallet flow as hires: user signs, server verifies, then the fighter is created.
- On birth, the fighter gets a **random rarity** (lottery, fixed price for everyone):
  - Common 50% → stat multiplier 1.00x
  - Rare 30% → 1.08x
  - Epic 15% → 1.16x
  - Legendary 5% → 1.25x
- Multiplier applies to HP and projectile damage only, and is deliberately bounded so upsets stay possible. A Legendary is favored, never invincible.
- At birth the owner picks: **name**, **archetype** (one of the five house AI styles — rusher, sniper, defender, opportunist, hit-and-run), and **colors**.
- A **birth announcement** goes out on the public feed: "A new fighter has been born — FT-0042 'WRECKER' (Epic)". The rarity reveal is the moment.
- Each FT gets a **signed ownership certificate**: the server cryptographically signs (ft_id, owner_wallet). Ownership is verifiable even outside our site, without a blockchain.
- Born fee split (configurable): **50% season pool, 50% treasury**.

## 2. Fighter registry

- New `fighters` table, DB-backed:
  - `id` (FT-XXXX for player fighters; house ids unchanged), `name`, `owner_wallet` (NULL = house),
    `archetype`, `colors`, `rarity`, `stat_mult`, `born_at`, `owner_cert`,
    `status` (active / rented / listed / fighting), `rent_price_sol`, `sale_price_sol`
- Engine change: per-fighter stat multipliers applied at spawn. House fighters keep 1.00x.
- Bot behavior is reused as-is: an FT is a skin + stats over one of the five proven house AIs. No custom code ever runs.

## 3. Official battles (the schedule)

The arena runs itself. No human presses a button.

- Every **10 minutes** (configurable) the server runs one **official battle**, alternating between two modes:
  - **Duel** — 1v1.
  - **Royale** — 4-fighter free-for-all, last one standing wins.
- **Entry window**: 5 minutes before each battle, owners tap "Enter next battle" on a FT.
  Entry fee **0.02 SOL** (configurable). Split: **80% battle prize pool, 20% season pool**.
  The fee stops queue flooding and funds the prizes.
- **The draw**: random from the queue, with longest-waiting priority so nobody waits forever.
  Duel draws 2 fighters, Royale draws 4.
- **Short queue**: if fewer than 2 player FTs enter, house fighters fill the empty slots.
  House fighters never take prize money — their share stays in the treasury.
- **Winner's owner takes the battle prize pool.** Wins/losses update on every FT's record.
- **Only official battles count** toward win earnings and the season leaderboard.
  User-created battles ("+ New battle") remain as exhibitions: watchable, chat open, no money, no points.

## 4. Rentals

- Owner lists a FT for rent at a price they choose.
- Renter pays → the FT auto-enters the **next** official battle under the renter's name.
- Split: **90% owner, 10% treasury**. If the fighter wins, the **renter** takes the win earnings
  (they took the risk); the owner keeps the rental fee regardless.
- While rented (until that battle resolves), the FT can't be entered, sold, or re-listed by the owner.

## 5. Marketplace

- Owner lists a FT at a price they choose. Buyer signs a SOL payment; server verifies
  (same pattern as hires/bets); ownership flips to the buyer on confirmation.
- Split: **95% seller, 5% treasury**.
- A listed FT can't fight, be rented, or be entered until delisted or sold.
- Rarity, record, and name travel with the FT — that history is what gives it market value.
- Wash-trading to yourself is pointless by design: the 5% cut makes it cost money for zero gain.

## 6. Season (15 days)

- The **season leaderboard** ranks FTs by wins in official battles (tiebreak: win rate, min 3 battles).
- The **season pool** builds from: 50% of born fees + 20% of battle entry fees (configurable).
- At the end of each 15-day season the pool is split across the **top 3** fighters:
  **60% / 25% / 15%** to their owners' wallets. Board resets, new season starts.
- Season page shows: leaderboard, live pool size, countdown to season end.
- A season worker (same pattern as the buyback bot) handles the snapshot, payout, and rollover.

## 7. Sparring ground (friend fights)

A separate area from the Arena. Two friends, two modes.

- **Challenge flow**: A picks their FT + friend's FT key + mode → B sees it in a challenge inbox →
  B accepts within 10 minutes → both present → fight starts live. Expiry cancels silently.
- **Friendly match**: free. No stakes, no earnings, no season points. Bragging rights only.
- **Real match**: both owners stake the **same** amount of SOL. Both deposits must confirm before
  the fight starts; if one side never pays, the challenge cancels. Winner's owner takes **95%**,
  **5%** to treasury. On a draw, both stakes are refunded in full.
- Sparring never touches the official system: no season points, no win earnings, no farming surface.
- **Legal flag**: wagered friend matches are peer-to-peer gambling and join the pre-launch legal review,
  alongside betting and the birth lottery.

## 8. Economics summary

Every number below is admin-configurable. Nothing is hardcoded.

| Flow | Amount (default) | Split |
|---|---|---|
| Born fee | 0.1 SOL | 50% season pool / 50% treasury |
| Battle entry fee | 0.02 SOL | 80% battle prize pool / 20% season pool |
| Battle prize | pool | 100% to winner's owner |
| Rental | owner-set | 90% owner / 10% treasury |
| Marketplace sale | seller-set | 95% seller / 5% treasury |
| Real-match wager | agreed stake | 95% winner / 5% treasury (draw = refund) |
| Season prize | pool | top 3 split 60% / 25% / 15% to owners' wallets |

Hard rules:
- Every prize is paid **only from revenue actually received**. Pools can never go negative.
- Money flows **only** through official battles, rentals, marketplace, and real matches.
- Exhibitions and friendly sparring move zero money, always.

## 9. Anti-exploit rules

- Official-vs-exhibition split is the main defense: hand-picked fights can never earn.
- Entry fees make queue-spamming unprofitable.
- Rented/listed/fighting FTs are state-locked against double use.
- Strength is bounded (max 1.25x) and identical physics otherwise — no purchasable dominance.
- All payment paths run in **mock mode** first, same as V1, until explicitly enabled live.

## 10. Build phases

- **Phase 1 — Core loop**: DB fighter registry, engine stat multipliers, born flow + birth feed,
  battle scheduler + entry queue + draw, entry fees, win earnings, season + season worker.
- **Phase 2 — Economy**: rentals, marketplace (list / buy / transfer), ownership certificates.
- **Phase 3 — Sparring**: challenge inbox, lobby, friendly + real matches, wager escrow/payouts.
- Frontend per phase: Born page, My Fighters, queue entry, Marketplace, Season page, Sparring ground.
- Tests per phase, mirroring V1's suite (engine, API, payments, settlement).

## 11. Owner decisions (locked 2026-09-26)

1. Born fee **0.1 SOL**, battle entry fee **0.02 SOL**.
2. Official battles alternate **Duel (1v1)** and **Royale (4-fighter FFA)**.
3. Season prize splits **top 3: 60% / 25% / 15%**.

## 12. Parked (explicitly out of scope)

- Tournaments (revisit after seasons prove out)
- Bring-your-own-agent (V2+)
- On-chain NFTs (FTs + our marketplace replace this)
