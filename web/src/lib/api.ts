/* API client + shared backend types. Mirrors the FastAPI contract. */

export interface Settings {
  project_name: string;
  token_ticker: string;
  token_mint: string;
  hire_fee_sol: number;
  treasury_wallet: string;
  hiring_live: boolean;
  max_bet_sol: number;
  betting_house_cut_pct: number;
  betting_live: boolean;
  buyback_pct: number;
  team_pct: number;
  buyback_interval_minutes: number;
  [key: string]: unknown;
}

export interface Fighter {
  id: string;
  name: string;
  tagline: string;
  description: string;
  wins: number;
  losses: number;
  draws: number;
  hire_fee_sol: number;
}

export interface BattleSummary {
  id: string;
  created_at: string;
  status: 'open' | 'running' | 'finished';
  seed: number | null;
  exhibition: boolean;
  official: boolean;
  mode: string | null;
  prize_pool_sol: number;
  fighter_ids: string[];
  registry_ids: string[];
  playback_speed: number;
  hire_fee_sol: number;
  winner: string | null;
  reason: string | null;
  ticks: number | null;
  duration_ms: number | null;
}

/* ---- Phase 1 (FT economy) ---- */

export type FtRarity = 'Common' | 'Rare' | 'Epic' | 'Legendary';

export interface FtFighter {
  id: string;
  name: string | null;
  owner_wallet: string | null;
  archetype: string | null;
  colors: { primary?: string };
  rarity: FtRarity | null;
  stat_mult: number | null;
  owner_cert: string | null;
  status: 'active' | 'queued' | 'fighting' | 'listed' | 'rented' | string;
  born_at: string | null;
  wins: number;
  losses: number;
  draws: number;
}

export interface BornResult {
  payment: 'mock' | 'live';
  fighter?: FtFighter;
  rarity?: FtRarity;
  stat_mult?: number;
  season_share_sol?: number;
  treasury_share_sol?: number;
  season_id?: number;
  intent_id?: number;
  transaction_base64?: string;
  amount_sol?: number;
}

export interface BirthEvent {
  id: number;
  fighter_id: string;
  name: string;
  rarity: FtRarity;
  owner_wallet: string;
  created_at: string;
}

export interface QueueRow {
  id: number;
  fighter_id: string;
  fighter_name: string | null;
  owner_wallet: string;
  entered_at: string;
  entry_fee_sol: number;
}

export interface NextBattle {
  starts_at: string;
  starts_in_seconds: number;
  mode: 'duel' | 'royale';
  mode_size: number;
  interval_minutes: number;
  entry_window_minutes: number;
  entry_open: boolean;
  queued_count: number;
}

export interface LeaderboardRow {
  rank: number;
  fighter_id: string;
  name: string | null;
  owner_wallet: string | null;
  rarity: FtRarity | null;
  wins: number;
  losses: number;
  draws: number;
  points: number;
}

export interface SeasonInfo {
  id: number;
  started_at: string;
  ends_at: string;
  status: string;
  prize_pool_sol: number;
  finished_at: string | null;
  created_at: string;
}

export interface SeasonPayout {
  id: number;
  season_id: number;
  place: number;
  fighter_id: string;
  owner_wallet: string;
  amount_sol: number;
  status: string;
  paid_tx: string | null;
  created_at: string;
}

export interface HireRow {
  id: number;
  battle_id: string;
  fighter_id: string;
  wallet: string;
  fee_sol: number;
  payment_status: string;
  created_at: string;
}

export interface BetRow {
  id: number;
  battle_id: string;
  fighter_id: string;
  wallet: string;
  amount_sol: number;
  created_at: string;
  payment_status: string;
}

export interface PoolInfo {
  battle_id: string;
  pools: Record<string, number>;
  total_sol: number;
  bet_count: number;
  house_cut_pct: number;
}

export interface TreasuryStats {
  treasury_balance_sol: number;
  total_fees_sol: number;
  total_burned_tokens: number;
  burn_count: number;
  token_ticker: string;
  project_name: string;
}

export interface BurnRecord {
  created_at: string;
  sol_spent: number;
  tokens_bought: number;
  tokens_burned: number;
  buy_tx: string | null;
  burn_tx: string | null;
  dry_run: boolean;
}

/* ---- websocket message shapes ---- */

export interface WsInfo {
  type: 'info';
  status: 'live' | 'replay';
  battle_id: string;
  fighter_ids: string[];
  battle_status?: 'open' | 'running' | 'finished';
}

export interface WsFighter {
  id: string;
  x: number;
  y: number;
  heading: number;
  hp: number;
  alive: boolean;
  benched: boolean;
  shield_active: boolean;
  shield_energy: number;
  dash_cooldown: number;
  kills: number;
}

export interface WsProjectile {
  id: string;
  x: number;
  y: number;
  vx: number;
  vy: number;
  owner: string;
}

export interface WsSnapshot {
  type: 'snapshot';
  tick: number;
  fighters: WsFighter[];
  projectiles: WsProjectile[];
}

export interface WsDone {
  type: 'done';
  result: {
    winner: string | null;
    draw: boolean;
    reason: string;
    ticks: number;
    elimination_order: string[];
    kills: Record<string, number>;
  };
}

export interface ChatMessage {
  id: number;
  battle_id: string;
  wallet: string;
  message: string;
  created_at: string;
}

export interface WsChat extends ChatMessage {
  type: 'chat';
}

export type WsMessage = WsInfo | WsSnapshot | WsDone | WsChat;

/* ---- fetch ---- */

export class ApiError extends Error {
  status: number;
  body: string;
  constructor(status: number, body: string) {
    super(`API ${status}: ${body}`);
    this.status = status;
    this.body = body;
  }
  /** Human-readable message from a JSON error body, falling back to raw text. */
  get message2(): string {
    try {
      const o = JSON.parse(this.body);
      if (o && typeof o === 'object' && o.detail) return String(o.detail);
    } catch {
      /* not JSON */
    }
    return this.body || `Request failed (${this.status})`;
  }
}

export async function api<T = unknown>(path: string, opts?: RequestInit): Promise<T> {
  const res = await fetch(path, {
    ...opts,
    headers: { 'Content-Type': 'application/json', ...(opts?.headers || {}) },
  });
  const text = await res.text();
  let data: unknown = null;
  try {
    data = text ? JSON.parse(text) : null;
  } catch {
    data = text;
  }
  if (!res.ok) {
    throw new ApiError(
      res.status,
      data && typeof data === 'object' ? JSON.stringify(data) : String(data),
    );
  }
  return data as T;
}
