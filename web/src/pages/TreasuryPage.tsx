/* Treasury: the buyback machine. Reference layout, real ledger numbers. */

import { useCallback, useEffect, useState } from 'react';
import { api, type TreasuryStats, type BurnRecord } from '../lib/api';
import BuybackBotVisual from '../components/BuybackBotVisual';
import { fmtSol, fmtTime, shortAddr } from '../lib/format';
import { useApp } from '../lib/store';

const LIMIT = 25;

function useCountUp(target: number, duration = 900): number {
  const [v, setV] = useState(0);
  useEffect(() => {
    let raf = 0;
    const t0 = performance.now();
    const step = (now: number) => {
      const f = Math.min(1, (now - t0) / duration);
      setV(target * (1 - Math.pow(1 - f, 3)));
      if (f < 1) raf = requestAnimationFrame(step);
    };
    raf = requestAnimationFrame(step);
    return () => cancelAnimationFrame(raf);
  }, [target, duration]);
  return v;
}

interface BuybackStatus {
  running: boolean;
  interval_minutes: number;
  buyback_live: boolean;
  buyback_enabled: boolean;
  last_run_at: string | null;
  last_status: string | null;
  last_detail: string;
  cycles: number;
}

export default function TreasuryPage() {
  const { settings } = useApp();
  const [stats, setStats] = useState<TreasuryStats | null>(null);
  const [burns, setBurns] = useState<BurnRecord[]>([]);
  const [bot, setBot] = useState<BuybackStatus | null>(null);
  const [offset, setOffset] = useState(0);
  const [hasMore, setHasMore] = useState(false);
  const [error, setError] = useState('');

  const ticker = settings?.token_ticker || 'BRAWL';
  const mint = (settings?.token_mint || '').trim();
  const animatedBurned = useCountUp(stats?.total_burned_tokens || 0);

  const load = useCallback(async (off: number) => {
    try {
      if (off === 0) {
        const s = await api<TreasuryStats>('/api/treasury/stats');
        setStats(s);
        try {
          setBot(await api<BuybackStatus>('/api/treasury/buyback-status'));
        } catch {
          /* bot status optional */
        }
      }
      const d = await api<{ burns: BurnRecord[] }>(
        `/api/treasury/burns?limit=${LIMIT}&offset=${off}`,
      );
      setHasMore((d.burns || []).length === LIMIT);
      setBurns((prev) => (off === 0 ? d.burns || [] : [...prev, ...(d.burns || [])]));
      setOffset(off);
    } catch (e) {
      if (off === 0) setError(e instanceof Error ? e.message : 'Could not load treasury.');
    }
  }, []);

  useEffect(() => { void load(0); }, [load]);

  const totalRounds = stats?.burn_count ?? 0;

  return (
    <main className="section-page">
      <div className="section-heading">
        <h1>TREASURY</h1>
        <span>{stats?.simulated ? 'SIMULATED LEDGER' : 'PUBLIC LEDGER'}</span>
      </div>

      {error && <p style={{ color: 'var(--red)' }}>{error}</p>}

      <div className="treasury-stats">
        <article className="treasury-stat">
          <span>TREASURY BALANCE</span>
          <strong>{fmtSol(stats?.treasury_balance_sol)}<small>SOL</small></strong>
          <i>FEES ACCUMULATE HERE</i>
        </article>
        <article className="treasury-stat">
          <span>FEES COLLECTED</span>
          <strong>{fmtSol(stats?.total_fees_sol)}<small>SOL</small></strong>
          <i>FROM HIRE + BETS</i>
        </article>
        <article className="treasury-stat">
          <span>TOKENS BURNED</span>
          <strong>{Math.round(animatedBurned).toLocaleString('en-US')}<small>${ticker}</small></strong>
          <i>PERMANENTLY REMOVED</i>
        </article>
        <article className="treasury-stat">
          <span>BUYBACK ROUNDS</span>
          <strong>{totalRounds}<small>ROUNDS</small></strong>
          <i>COMPLETED ROUNDS</i>
        </article>
      </div>

      <div className="treasury-banner">
        <div className="burn-emblem">
          <span>BURN</span>
        </div>
        <div>
          <strong>BUYBACK MACHINE</strong>
          <span>Platform fees route to scheduled ${ticker} buybacks and burns.</span>
          {mint && (
            <span className="muted small" style={{ display: 'flex', alignItems: 'center', gap: 8, marginTop: 6 }}>
              <span>CA: <code style={{ color: 'var(--text)' }}>{shortAddr(mint, 6)}</code></span>
              <button
                className="table-action"
                style={{ padding: '2px 8px', fontSize: 11 }}
                onClick={() => { void navigator.clipboard.writeText(mint); }}
              >
                COPY
              </button>
              <a
                href={`https://solscan.io/token/${mint}`}
                target="_blank"
                rel="noreferrer"
                className="table-action"
                style={{ padding: '2px 8px', fontSize: 11, textDecoration: 'none' }}
              >
                SOLSCAN
              </a>
            </span>
          )}
          {bot && (
            <span className="muted small" style={{ display: 'block', marginTop: 4 }}>
              {bot.running ? 'Bot running' : 'Bot stopped'}
              {' · every '}
              {bot.interval_minutes} min
              {bot.last_run_at
                ? ` · last cycle ${bot.last_status || ''}${bot.last_detail ? ` (${bot.last_detail})` : ''}`
                : ' · no cycles yet'}
              {!bot.buyback_live && ' · simulated (live mode off)'}
            </span>
          )}
        </div>
        <span className="machine-state">
          <i /> {bot ? (bot.running ? (bot.buyback_live ? 'LIVE' : 'RUNNING (SIM)') : 'STOPPED') : 'SCHEDULED'}
        </span>
      </div>

      <BuybackBotVisual ticker={ticker} />

      <div className="data-panel table-panel">
        <div className="table-headline">
          <span>BURN HISTORY</span>
          <span>ON-CHAIN ACTIVITY</span>
        </div>
        <div className="table-scroll">
          {burns.length === 0 ? (
            <p className="muted" style={{ padding: '18px 14px' }}>
              No buyback rounds recorded yet.
            </p>
          ) : (
            <table className="arena-table">
              <thead>
                <tr>
                  <th>ROUND / DATE</th>
                  <th>SOL SPENT</th>
                  <th>TOKENS BURNED</th>
                  <th>BUY TX</th>
                  <th>BURN TX</th>
                  <th>MODE</th>
                </tr>
              </thead>
              <tbody>
                {burns.map((b, i) => (
                  <tr key={`${b.created_at}-${i}`}>
                    <td>
                      <strong className="round-id">
                        #{String(Math.max(1, totalRounds - offset - i)).padStart(3, '0')}
                      </strong>
                      <span className="date-cell">{fmtTime(b.created_at)}</span>
                    </td>
                    <td>{fmtSol(b.sol_spent)} SOL</td>
                    <td className="burn-value">
                      {Number(b.tokens_burned).toLocaleString('en-US')} ${ticker}
                    </td>
                    <td>
                      <span className="tx-link" title={b.buy_tx || ''}>
                        {b.buy_tx ? shortAddr(b.buy_tx, 6) : '-'}
                      </span>
                    </td>
                    <td>
                      <span className="tx-link" title={b.burn_tx || ''}>
                        {b.burn_tx ? shortAddr(b.burn_tx, 6) : '-'}
                      </span>
                    </td>
                    <td>
                      {b.dry_run ? (
                        <span className="mock-badge">MOCK</span>
                      ) : (
                        <span className="finished-badge">LIVE</span>
                      )}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
        </div>
      </div>
      {hasMore && (
        <div style={{ textAlign: 'center', marginTop: 16 }}>
          <button className="table-action" onClick={() => load(offset + LIMIT)}>
            LOAD MORE <span>↓</span>
          </button>
        </div>
      )}
      <div className="section-footnote">
        {stats?.simulated
          ? 'PRACTICE MODE: THESE SOL AMOUNTS ARE SIMULATED. NO REAL FUNDS MOVE UNTIL LIVE PAYMENTS ARE ENABLED IN THE ADMIN CONSOLE.'
          : 'PUBLIC LEDGER VALUES'}{' '}
        <i /> TOKEN USE IS NOT REQUIRED TO FIGHT OR BET
      </div>
    </main>
  );
}
