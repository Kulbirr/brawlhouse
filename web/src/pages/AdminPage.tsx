/* Admin console (in-house only): token-gated settings, fighter branding,
 * treasury ledger, buyback kill switch. Reference group layout, real API. */

import { useCallback, useEffect, useState } from 'react';
import type { Fighter } from '../lib/api';

const TOKEN_KEY = 'aa_admin_token';
const SCARY_PHRASE = 'GO LIVE';

const FLOAT_FIELDS = [
  'hire_fee_sol', 'betting_house_cut_pct', 'buyback_pct', 'team_pct',
  'buyback_mock_rate', 'buyback_hot_sol_cap', 'max_bet_sol',
  'born_fee_sol', 'born_season_pct', 'entry_fee_sol', 'entry_prize_pct',
  'entry_season_pct', 'season_prize_1_pct', 'season_prize_2_pct',
  'season_prize_3_pct',
];
const INT_FIELDS = ['buyback_interval_minutes', 'official_battle_interval_minutes',
  'entry_window_minutes', 'season_length_days'];
const BOOL_FIELDS = [
  'betting_live', 'hiring_live', 'buyback_enabled', 'buyback_live', 'team_sweep_enabled',
  'born_live', 'entry_live', 'payouts_live',
];

interface FieldDef {
  key: string;
  label: string;
  type: 'text' | 'number' | 'checkbox';
  unit?: string;
  hint?: string;
}

const ECONOMICS: FieldDef[] = [
  {
    key: 'hire_fee_sol',
    label: 'Base hire fee',
    type: 'number',
    unit: 'SOL',
    hint: 'Overall price level only. Each fighter\u2019s actual price is set automatically from its win rate (0.5x\u20132x of this base) \u2014 not set here.',
  },
  { key: 'betting_house_cut_pct', label: 'House cut', type: 'number', unit: '%' },
  { key: 'buyback_pct', label: 'Buyback share', type: 'number', unit: '%' },
  { key: 'team_pct', label: 'Team share', type: 'number', unit: '%' },
];
const PAYMENTS: FieldDef[] = [
  { key: 'treasury_wallet', label: 'Treasury wallet', type: 'text', unit: 'PUBKEY' },
  { key: 'hiring_live', label: 'Live hiring', type: 'checkbox', unit: 'REAL SOL' },
  { key: 'betting_live', label: 'Live betting', type: 'checkbox', unit: 'REAL SOL' },
  { key: 'max_bet_sol', label: 'Max bet', type: 'number', unit: 'SOL' },
];
const ENGINE: FieldDef[] = [
  { key: 'buyback_interval_minutes', label: 'Buyback interval', type: 'number', unit: 'MINUTES' },
  { key: 'buyback_hot_sol_cap', label: 'Hot wallet SOL cap', type: 'number', unit: 'SOL' },
  { key: 'buyback_mock_rate', label: 'Mock rate', type: 'number', unit: 'TOKENS/SOL' },
  { key: 'buyback_live', label: 'Live buybacks', type: 'checkbox', unit: 'REAL SWAPS' },
  { key: 'team_sweep_enabled', label: 'Team sweep enabled', type: 'checkbox' },
];
const IDENTITY: FieldDef[] = [
  { key: 'project_name', label: 'Project name', type: 'text' },
  { key: 'token_ticker', label: 'Token ticker', type: 'text' },
  { key: 'token_mint', label: 'Token mint', type: 'text' },
];
/* Phase 1 (FT economy): every number the owner locked is configurable here. */
const FT_BORN: FieldDef[] = [
  { key: 'born_fee_sol', label: 'Born fee', type: 'number', unit: 'SOL' },
  { key: 'born_season_pct', label: 'Born fee to season pool', type: 'number', unit: '%',
    hint: 'Rest goes to the treasury.' },
  { key: 'born_live', label: 'Live born payments', type: 'checkbox', unit: 'REAL SOL' },
];
const FT_BATTLES: FieldDef[] = [
  { key: 'entry_fee_sol', label: 'Battle entry fee', type: 'number', unit: 'SOL' },
  { key: 'entry_prize_pct', label: 'Entry fee to battle pool', type: 'number', unit: '%',
    hint: 'Must sum with season share to 100.' },
  { key: 'entry_season_pct', label: 'Entry fee to season pool', type: 'number', unit: '%',
    hint: 'Must sum with battle share to 100.' },
  { key: 'official_battle_interval_minutes', label: 'Official battle interval', type: 'number', unit: 'MINUTES',
    hint: 'Battles alternate Duel / Royale.' },
  { key: 'entry_window_minutes', label: 'Entry window', type: 'number', unit: 'MINUTES',
    hint: 'How long before each battle entries open. Must fit inside the interval.' },
  { key: 'entry_live', label: 'Live entry payments', type: 'checkbox', unit: 'REAL SOL' },
];
const FT_SEASON: FieldDef[] = [
  { key: 'season_length_days', label: 'Season length', type: 'number', unit: 'DAYS' },
  { key: 'season_prize_1_pct', label: '1st place share', type: 'number', unit: '%',
    hint: 'Top-3 shares must total 100.' },
  { key: 'season_prize_2_pct', label: '2nd place share', type: 'number', unit: '%' },
  { key: 'season_prize_3_pct', label: '3rd place share', type: 'number', unit: '%' },
  { key: 'payouts_live', label: 'Live prize payouts', type: 'checkbox', unit: 'REAL SOL',
    hint: 'In-house only. Off = payouts recorded as mock, nothing moves.' },
];

const SCARY: Record<string, { title: string; text: string }> = {
  betting_live: {
    title: 'Enable LIVE betting?',
    text: 'REAL MONEY WARNING: with live betting, bettors must send SOL to the treasury wallet before each bet. Only enable after the owner checklist is done: treasury wallet set, Helius key set, and the betting flow legally reviewed.',
  },
  hiring_live: {
    title: 'Enable LIVE hiring?',
    text: 'REAL MONEY WARNING: with live hiring, users must send the hire fee in SOL to the treasury wallet and sign in their wallet. Only enable after the owner checklist is done: treasury wallet set and the flow legally reviewed.',
  },
  buyback_live: {
    title: 'Enable LIVE buybacks?',
    text: 'REAL MONEY WARNING: with live buybacks, the bot will swap SOL for the token via the Jupiter API and burn the bought tokens on-chain. Only enable after the owner checklist is done: token launched and token_mint set, treasury funded under the hot SOL cap, Helius key set.',
  },
  born_live: {
    title: 'Enable LIVE born payments?',
    text: 'REAL MONEY WARNING: with live born payments, users must send the born fee in SOL to the treasury wallet and sign in their wallet before each fighter is born. Only enable after the owner checklist is done: treasury wallet set, Helius key set.',
  },
  entry_live: {
    title: 'Enable LIVE entry payments?',
    text: 'REAL MONEY WARNING: with live entry payments, owners must send the battle entry fee in SOL to the treasury wallet and sign in their wallet before each queue entry. Only enable after the owner checklist is done: treasury wallet set, Helius key set.',
  },
  payouts_live: {
    title: 'Enable LIVE prize payouts?',
    text: 'REAL MONEY WARNING: with live payouts, battle prizes and season prizes are sent in SOL from the treasury hot wallet to winners automatically. Only enable after the owner checklist is done: treasury hot wallet funded, TREASURY_PRIVATE_KEY set, Helius key set. This is in-house only and never shown publicly.',
  },
};

type SettingsMap = Record<string, unknown>;

function token(): string {
  return sessionStorage.getItem(TOKEN_KEY) || '';
}

async function adminApi(path: string, opts?: RequestInit): Promise<unknown> {
  const res = await fetch(path, {
    ...opts,
    headers: { 'X-Admin-Token': token(), ...(opts?.headers || {}) },
  });
  if (res.status === 401) {
    sessionStorage.removeItem(TOKEN_KEY);
    throw new Error('__unauthorized__');
  }
  if (res.status === 503) {
    throw new Error('Admin not configured on this server: set ADMIN_TOKEN in the server .env');
  }
  let body: unknown = null;
  try { body = await res.json(); } catch { /* non-JSON */ }
  if (!res.ok) {
    const msg = (body as { detail?: string } | null)?.detail || `HTTP ${res.status}`;
    throw new Error(String(msg));
  }
  return body;
}

/* Backend type-checks float fields strictly: whole numbers must serialize as "1.0". */
function encodeSettings(obj: SettingsMap): string {
  let s = JSON.stringify(obj);
  for (const k of FLOAT_FIELDS) {
    s = s.replace(new RegExp(`"${k}":(-?\\d+)([,}])`), `"$1":$2.0$3`);
  }
  return s;
}

interface FeeEvent {
  id: number;
  created_at: string;
  battle_id: string | null;
  source: string;
  amount_sol: number;
}
interface BurnRow {
  created_at: string;
  sol_spent: number;
  tokens_bought: number;
  tokens_burned: number;
  dry_run: boolean;
}

function fmtTs(ts: string | null): string {
  if (!ts) return '-';
  const d = new Date(String(ts).replace(' ', 'T') + 'Z');
  const d2 = isNaN(d.getTime()) ? new Date(ts) : d;
  return isNaN(d2.getTime()) ? String(ts) : d2.toLocaleString();
}
function fmtNum(n: unknown, digits = 9): string {
  if (n === null || n === undefined) return '-';
  const x = Number(n);
  return isFinite(x) ? x.toLocaleString('en-US', { maximumFractionDigits: digits }) : '-';
}

export default function AdminPage() {
  const [authed, setAuthed] = useState(!!token());
  const [lockError, setLockError] = useState('');
  const [tokenInput, setTokenInput] = useState('');
  const [settings, setSettings] = useState<SettingsMap>({});
  const [saved, setSaved] = useState<SettingsMap>({});
  const [envLocked, setEnvLocked] = useState<string[]>([]);
  const [status, setStatus] = useState('');
  const [statusError, setStatusError] = useState(false);
  const [fighters, setFighters] = useState<Fighter[]>([]);
  const [fighterDrafts, setFighterDrafts] = useState<Record<string, Fighter>>({});
  const [feeTotals, setFeeTotals] = useState<Record<string, number>>({});
  const [feeEvents, setFeeEvents] = useState<FeeEvent[]>([]);
  const [burns, setBurns] = useState<BurnRow[]>([]);
  const [scary, setScary] = useState<{ key: string; proceed: () => void } | null>(null);
  const [scaryInput, setScaryInput] = useState('');
  const [battleMsg, setBattleMsg] = useState('');

  const flash = (msg: string, isError = false) => {
    setStatus(msg);
    setStatusError(isError);
    setTimeout(() => setStatus(''), 4000);
  };

  const loadSettings = useCallback(async () => {
    const body = (await adminApi('/api/admin/settings', {
      method: 'PUT',
      headers: { 'Content-Type': 'application/json' },
      body: '{}',
    })) as { settings: SettingsMap; env_overridden: string[] };
    const s = body.settings || {};
    setSettings(s);
    setSaved(s);
    setEnvLocked(body.env_overridden || []);
  }, []);

  const loadFighters = useCallback(async () => {
    const res = await fetch('/api/fighters');
    if (!res.ok) throw new Error(`Could not load fighters (HTTP ${res.status})`);
    const d = (await res.json()) as { fighters: Fighter[] };
    setFighters(d.fighters || []);
    const drafts: Record<string, Fighter> = {};
    (d.fighters || []).forEach((f) => { drafts[f.id] = { ...f }; });
    setFighterDrafts(drafts);
  }, []);

  const loadTreasury = useCallback(async () => {
    const fees = (await adminApi('/api/treasury/fees?limit=50')) as {
      totals: Record<string, number>;
      events: FeeEvent[];
    };
    setFeeTotals(fees.totals || {});
    setFeeEvents(fees.events || []);
    const bres = await fetch('/api/treasury/burns?limit=25');
    const bjson = bres.ok ? ((await bres.json()) as { burns: BurnRow[] }) : { burns: [] };
    setBurns(bjson.burns || []);
  }, []);

  const boot = useCallback(async () => {
    await loadSettings();
    await loadFighters();
    await loadTreasury();
  }, [loadSettings, loadFighters, loadTreasury]);

  useEffect(() => {
    if (authed) {
      boot().catch((e: Error) => {
        if (e.message === '__unauthorized__') {
          setAuthed(false);
          setLockError('Invalid token. Enter the admin token again.');
        } else {
          setAuthed(false);
          setLockError(`Could not load admin data: ${e.message}`);
        }
      });
    }
  }, [authed, boot]);

  const unlock = (e: React.FormEvent) => {
    e.preventDefault();
    if (!tokenInput) return;
    sessionStorage.setItem(TOKEN_KEY, tokenInput);
    setTokenInput('');
    setLockError('');
    setAuthed(true);
  };

  const lock = () => {
    sessionStorage.removeItem(TOKEN_KEY);
    setAuthed(false);
  };

  const saveSettings = async (keys: string[]) => {
    const payload: SettingsMap = {};
    for (const k of keys) {
      const v = settings[k];
      if (BOOL_FIELDS.includes(k)) payload[k] = !!v;
      else if (INT_FIELDS.includes(k)) {
        const iv = parseInt(String(v), 10);
        if (!isFinite(iv) || iv < 0) throw new Error(`Invalid value for ${k}: ${v}`);
        payload[k] = iv;
      } else if (FLOAT_FIELDS.includes(k)) {
        const fv = parseFloat(String(v));
        if (!isFinite(fv) || fv < 0) throw new Error(`Invalid value for ${k}: ${v}`);
        payload[k] = fv;
      } else payload[k] = String(v ?? '');
    }
    try {
      await adminApi('/api/admin/settings', {
        method: 'PUT',
        headers: { 'Content-Type': 'application/json' },
        body: encodeSettings(payload),
      });
      await loadSettings();
      flash('Saved.');
    } catch (e) {
      if ((e as Error).message === '__unauthorized__') {
        setAuthed(false);
        setLockError('Invalid token. Enter the admin token again.');
      } else flash(`Error: ${(e as Error).message}`, true);
    }
  };

  /* Manual fallback: force the next official battle now, for when the
     scheduler is disabled or missed its slot. Same code path as a
     scheduled battle, so it counts officially. */
  const runOfficialBattle = async () => {
    setBattleMsg('Starting…');
    try {
      const r = (await adminApi('/api/admin/battles/run-official', {
        method: 'POST',
      })) as { id: string; mode: string };
      setBattleMsg(`Official ${r.mode || 'battle'} started: ${r.id}`);
    } catch (e) {
      setBattleMsg(`Could not start: ${(e as Error).message}`);
    }
  };

  const maybeScary = (key: string, proceed: () => void) => {
    if (settings[key] && !saved[key] && SCARY[key]) {
      setScary({ key, proceed });
      setScaryInput('');
    } else {
      proceed();
    }
  };

  const onBuybackToggle = async () => {
    try {
      await saveSettings(['buyback_enabled']);
      flash(
        settings.buyback_enabled
          ? 'Buybacks enabled.'
          : 'Buybacks paused. Fees keep accumulating untouched.',
      );
    } catch {
      /* saveSettings already flashed */
    }
  };

  const saveFighter = async (fid: string) => {
    const d = fighterDrafts[fid];
    if (!d) return;
    try {
      await adminApi(`/api/admin/fighters/${encodeURIComponent(fid)}`, {
        method: 'PUT',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          name: d.name.trim(),
          tagline: d.tagline.trim(),
          description: d.description.trim(),
        }),
      });
      flash(`Fighter ${fid} updated.`);
    } catch (e) {
      if ((e as Error).message === '__unauthorized__') {
        setAuthed(false);
        setLockError('Invalid token. Enter the admin token again.');
      } else flash(`Error: ${(e as Error).message}`, true);
    }
  };

  const renderField = (f: FieldDef) => {
    const locked = envLocked.includes(f.key);
    const val = settings[f.key];
    return (
      <label className="admin-field" key={f.key}>
        <span>
          {f.label}
          {f.unit && <i>{f.unit}</i>}
          {locked && (
            <span className="env-badge" title="Overridden by an environment variable: file writes will not take effect until the env var is unset.">
              {' '}ENV-LOCKED
            </span>
          )}
        </span>
        {f.hint && <small className="admin-hint">{f.hint}</small>}
        {f.type === 'checkbox' ? (
          <input
            type="checkbox"
            checked={!!val}
            onChange={(e) => setSettings((s) => ({ ...s, [f.key]: e.target.checked }))}
          />
        ) : (
          <input
            type={f.type}
            value={val === null || val === undefined ? '' : String(val)}
            onChange={(e) => setSettings((s) => ({ ...s, [f.key]: e.target.value }))}
          />
        )}
      </label>
    );
  };

  const group = (num: string, title: string, fields: FieldDef[], onSave: () => void, extra?: React.ReactNode) => (
    <section className="admin-group">
      <div className="admin-group-head">
        <h2>{title}</h2>
        <span>{num}</span>
      </div>
      {fields.map(renderField)}
      {extra}
      <button className="primary-action" onClick={onSave}>
        Save settings <span>→</span>
      </button>
    </section>
  );

  const bb = parseFloat(String(settings.buyback_pct));
  const tm = parseFloat(String(settings.team_pct));
  const sanity = (() => {
    if (!isFinite(bb) || !isFinite(tm) || bb < 0 || tm < 0) {
      return <span className="sanity-warn">Enter valid buyback/team percentages to see the split.</span>;
    }
    const rest = 1 - bb / 100 - tm / 100;
    return (
      <span>
        Of every <b>1 SOL</b> in fees: <b>{(bb / 100).toFixed(3)} SOL</b> to buybacks,{' '}
        <b>{(tm / 100).toFixed(3)} SOL</b> to team
        {rest > 0.0000005 && (
          <span className="muted">, {rest.toFixed(3)} SOL unsplit remainder stays in the treasury</span>
        )}
        {rest < -0.0000005 && (
          <span className="sanity-warn"> — splits exceed 100%; review the numbers</span>
        )}
        .
      </span>
    );
  })();

  if (!authed) {
    return (
      <main className="section-page admin-page">
        <div className="section-heading">
          <h1>ADMIN CONTROL</h1>
          <span className="private-badge">PRIVATE · IN-HOUSE</span>
        </div>
        <div className="admin-lock">
          <p className="muted small" style={{ marginTop: 0 }}>In-house only. Enter the admin token.</p>
          <form onSubmit={unlock}>
            <label className="admin-field">
              <span>Admin token</span>
              <input
                type="password"
                value={tokenInput}
                onChange={(e) => setTokenInput(e.target.value)}
                autoFocus
              />
            </label>
            {lockError && <p style={{ color: 'var(--red)', fontSize: 13 }}>{lockError}</p>}
            <button className="primary-action" type="submit" style={{ width: '100%' }}>
              Unlock <span>→</span>
            </button>
          </form>
        </div>
      </main>
    );
  }

  return (
    <main className="section-page admin-page">
      <div className="section-heading">
        <h1>ADMIN CONTROL</h1>
        <span>
          <span className="private-badge" style={{ marginRight: 8 }}>PRIVATE · IN-HOUSE</span>
          <span style={{ color: settings.buyback_enabled ? '#94ca64' : '#e06c7c' }}>
            {settings.buyback_enabled ? 'BUYBACKS ACTIVE' : 'BUYBACKS PAUSED'}
          </span>
          <button
            className="table-action"
            style={{ marginLeft: 10 }}
            onClick={() => {
              setSettings((s) => ({ ...s, buyback_enabled: !s.buyback_enabled }));
              setTimeout(onBuybackToggle, 0);
            }}
          >
            {settings.buyback_enabled ? 'PAUSE BUYBACKS' : 'RESUME BUYBACKS'} <span>→</span>
          </button>
          <button className="table-action" style={{ marginLeft: 8 }} onClick={lock}>
            LOCK <span>→</span>
          </button>
        </span>
      </div>

      <div className="admin-grid">
        {group('01', 'ECONOMICS', ECONOMICS,
          () => saveSettings(ECONOMICS.map((f) => f.key)),
          <div className="sanity-line">{sanity}</div>)}
        {group('02', 'LIVE PAYMENTS', PAYMENTS,
          () => maybeScary('betting_live', () =>
            maybeScary('hiring_live', () => saveSettings(PAYMENTS.map((f) => f.key)))))}
        {group('03', 'FIGHT ENGINE', ENGINE,
          () => maybeScary('buyback_live', () => saveSettings(ENGINE.map((f) => f.key))))}
        {group('05', 'FT BORN', FT_BORN,
          () => maybeScary('born_live', () => saveSettings(FT_BORN.map((f) => f.key))))}
        {group('06', 'FT OFFICIAL BATTLES', FT_BATTLES,
          () => maybeScary('entry_live', () => saveSettings(FT_BATTLES.map((f) => f.key))),
          <>
            <div className="admin-subhead">MANUAL FALLBACK</div>
            <p className="muted small" style={{ margin: '0 0 10px' }}>
              If the scheduler ever misses its slot, force the next official
              battle now. It draws from the FT queue exactly like a scheduled
              battle, so it counts officially.
            </p>
            <button className="primary-action" onClick={runOfficialBattle}>
              Start official battle now <span>→</span>
            </button>
            {battleMsg && (
              <p className="muted small" style={{ marginTop: 8 }}>{battleMsg}</p>
            )}
          </>)}
        {group('07', 'FT SEASON', FT_SEASON,
          () => maybeScary('payouts_live', () => saveSettings(FT_SEASON.map((f) => f.key))))}
        {group('04', 'PROJECT IDENTITY', IDENTITY,
          () => saveSettings(IDENTITY.map((f) => f.key)),
          <>
            <div className="admin-subhead">PRIVATE OPERATIONS</div>
            <label className="admin-pause">
              <span>
                <strong>Pause buybacks</strong>
                <small>In-house control · never public</small>
              </span>
              <input
                type="checkbox"
                checked={!settings.buyback_enabled}
                onChange={() => {
                  setSettings((s) => ({ ...s, buyback_enabled: !s.buyback_enabled }));
                  setTimeout(onBuybackToggle, 0);
                }}
              />
            </label>
          </>)}
      </div>

      <section className="admin-group admin-group-wide" style={{ marginTop: 11 }}>
        <div className="admin-group-head">
          <h2>FIGHTER BRANDING</h2>
          <span>05</span>
        </div>
        {fighters.map((f) => {
          const d = fighterDrafts[f.id];
          if (!d) return null;
          return (
            <div key={f.id} style={{ display: 'grid', gridTemplateColumns: '110px 1fr', gap: 12, marginBottom: 14, alignItems: 'start' }}>
              <div className="date-cell" style={{ paddingTop: 12 }}>{f.id}</div>
              <div>
                <label className="admin-field">
                  <span>Name</span>
                  <input
                    type="text"
                    maxLength={64}
                    value={d.name}
                    onChange={(e) =>
                      setFighterDrafts((p) => ({ ...p, [f.id]: { ...d, name: e.target.value } }))
                    }
                  />
                </label>
                <label className="admin-field">
                  <span>Tagline</span>
                  <input
                    type="text"
                    maxLength={160}
                    value={d.tagline}
                    onChange={(e) =>
                      setFighterDrafts((p) => ({ ...p, [f.id]: { ...d, tagline: e.target.value } }))
                    }
                  />
                </label>
                <label className="admin-field">
                  <span>Description</span>
                  <input
                    type="text"
                    maxLength={2000}
                    value={d.description}
                    onChange={(e) =>
                      setFighterDrafts((p) => ({ ...p, [f.id]: { ...d, description: e.target.value } }))
                    }
                  />
                </label>
                <div style={{ display: 'flex', gap: 10, alignItems: 'center', marginTop: 8 }}>
                  <button className="primary-action" onClick={() => saveFighter(f.id)}>
                    Save <span>→</span>
                  </button>
                  <span className="date-cell">W {f.wins} / L {f.losses} / D {f.draws}</span>
                </div>
              </div>
            </div>
          );
        })}
      </section>

      <section className="admin-group admin-group-wide" style={{ marginTop: 11 }}>
        <div className="admin-group-head">
          <h2>TREASURY LEDGER</h2>
          <span>06</span>
        </div>
        <div className="admin-subhead" style={{ paddingTop: 8 }}>
          FEE TOTALS · {fmtNum(feeTotals.total_sol, 4)} SOL TOTAL · {fmtNum(feeTotals.betting_sol, 4)} SOL BETTING · {fmtNum(feeTotals.hire_sol, 4)} SOL HIRE
        </div>
        <div className="table-scroll">
          <table className="arena-table">
            <thead>
              <tr>
                <th>ID</th>
                <th>TIME</th>
                <th>BATTLE</th>
                <th>SOURCE</th>
                <th>AMOUNT</th>
              </tr>
            </thead>
            <tbody>
              {feeEvents.length === 0 ? (
                <tr><td colSpan={5} className="date-cell">No fee events yet.</td></tr>
              ) : (
                feeEvents.map((ev) => (
                  <tr key={ev.id}>
                    <td className="rank-cell">{ev.id}</td>
                    <td className="date-cell">{fmtTs(ev.created_at)}</td>
                    <td><span className="tx-link">{ev.battle_id ? `${String(ev.battle_id).slice(0, 12)}…` : '-'}</span></td>
                    <td>{ev.source}</td>
                    <td>{fmtNum(ev.amount_sol, 4)} SOL</td>
                  </tr>
                ))
              )}
            </tbody>
          </table>
        </div>
        <div className="admin-subhead">BUYBACK ROUNDS</div>
        <div className="table-scroll">
          <table className="arena-table">
            <thead>
              <tr>
                <th>TIME</th>
                <th>SOL SPENT</th>
                <th>BOUGHT</th>
                <th>BURNED</th>
                <th>MODE</th>
              </tr>
            </thead>
            <tbody>
              {burns.length === 0 ? (
                <tr><td colSpan={5} className="date-cell">No buyback rounds recorded yet.</td></tr>
              ) : (
                burns.map((b, i) => (
                  <tr key={i}>
                    <td className="date-cell">{fmtTs(b.created_at)}</td>
                    <td>{fmtNum(b.sol_spent, 4)}</td>
                    <td>{fmtNum(b.tokens_bought, 2)}</td>
                    <td className="burn-value">{fmtNum(b.tokens_burned, 2)}</td>
                    <td>{b.dry_run ? <span className="mock-badge">MOCK</span> : <span className="finished-badge">LIVE</span>}</td>
                  </tr>
                ))
              )}
            </tbody>
          </table>
        </div>
      </section>

      {scary && (
        <div className="modal-backdrop">
          <div className="modal" role="dialog" aria-modal="true">
            <h2 style={{ color: 'var(--red)' }}>{SCARY[scary.key].title}</h2>
            <p className="small" style={{ lineHeight: 1.6 }}>{SCARY[scary.key].text}</p>
            <label className="admin-field">
              <span>Type {SCARY_PHRASE} to confirm</span>
              <input
                type="text"
                value={scaryInput}
                onChange={(e) => setScaryInput(e.target.value)}
                autoFocus
              />
            </label>
            <div className="btn-row">
              <button
                className="btn btn-small btn-ghost"
                onClick={() => { setScary(null); void loadSettings(); }}
              >
                Cancel
              </button>
              <button
                className="btn btn-small btn-danger"
                disabled={scaryInput.trim() !== SCARY_PHRASE}
                onClick={() => {
                  const fn = scary.proceed;
                  setScary(null);
                  fn();
                }}
              >
                Confirm
              </button>
            </div>
          </div>
        </div>
      )}

      <div className="admin-footer">
        <span>{status || 'SETTINGS PERSIST ON SAVE'}</span>
        {statusError && <span className="sanity-warn">{status}</span>}
      </div>
      <div className="section-footnote">
        IN-HOUSE ONLY <i /> EVERY NUMBER IS EDITABLE <i /> THE KILL SWITCH IS THE BUYBACK TOGGLE
      </div>
    </main>
  );
}
