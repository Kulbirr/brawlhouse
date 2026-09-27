/* Season: the 15-day championship. Prize pool, countdown, leaderboard
 * (top 3 split 60/25/15), and recent payouts. */

import { useCallback, useEffect, useState } from 'react';
import {
  api,
  type LeaderboardRow,
  type SeasonInfo,
  type SeasonPayout,
} from '../lib/api';
import { fmtSol } from '../lib/format';
import { RarityBadge, shortWallet } from '../components/ft';

interface SeasonBody {
  season: SeasonInfo;
  season_length_days: number;
  prize_split_pct: number[];
  leaderboard: LeaderboardRow[];
  recent_payouts: SeasonPayout[];
}

function endsIn(iso: string): string {
  const ms = Math.max(0, new Date(iso).getTime() - Date.now());
  const d = Math.floor(ms / 86400000);
  const h = Math.floor((ms % 86400000) / 3600000);
  const m = Math.floor((ms % 3600000) / 60000);
  return `${d}d ${h}h ${m}m`;
}

export default function SeasonPage() {
  const [data, setData] = useState<SeasonBody | null>(null);

  const load = useCallback(async () => {
    try {
      setData(await api<SeasonBody>('/api/ft/season'));
    } catch {
      /* keep stale */
    }
  }, []);

  useEffect(() => {
    load();
    const t = setInterval(load, 30000);
    return () => clearInterval(t);
  }, [load]);

  const [split1, split2, split3] = data?.prize_split_pct ?? [60, 25, 15];

  return (
    <main className="section-page">
      <div className="section-heading">
        <h1>SEASON</h1>
        <span>{data ? `${data.season_length_days}-DAY CHAMPIONSHIP` : '…'}</span>
      </div>

      <div className="season-layout">
        <section className="panel season-pool-card">
          <h3>PRIZE POOL</h3>
          <div className="season-pool">
            {data ? `${fmtSol(data.season.prize_pool_sol)} SOL` : '…'}
          </div>
          <div className="season-meta">
            <div>
              <span>SEASON #{data?.season.id ?? '-'}</span>
              <strong>{data ? `ENDS IN ${endsIn(data.season.ends_at)}` : '-'}</strong>
            </div>
            <div>
              <span>TOP-3 SPLIT</span>
              <strong>
                {split1}% / {split2}% / {split3}%
              </strong>
            </div>
          </div>
          <p className="muted small">
            The pool grows from born fees and battle entry fees. At season end
            the top 3 fighters' owners are paid out automatically; only
            official battles count toward the standings.
          </p>
        </section>

        <section className="table-panel">
          <div className="table-headline">
            <span>LEADERBOARD</span>
            <span>WIN = 3 PTS · DRAW = 1 PT</span>
          </div>
          {!data?.leaderboard.length ? (
            <p className="muted" style={{ padding: '16px' }}>
              No official battles fought yet this season.
            </p>
          ) : (
            <div className="table-scroll">
              <table className="arena-table">
                <thead>
                  <tr>
                    <th>#</th>
                    <th>Fighter</th>
                    <th>Rarity</th>
                    <th>Record</th>
                    <th>Pts</th>
                    <th>Owner</th>
                  </tr>
                </thead>
                <tbody>
                  {data.leaderboard.map((r) => (
                    <tr key={r.fighter_id} className={r.rank <= 3 ? 'in-payout' : ''}>
                      <td>{r.rank}</td>
                      <td>
                        {r.name || r.fighter_id}
                        <span className="muted small"> {r.fighter_id}</span>
                      </td>
                      <td>
                        <RarityBadge rarity={r.rarity} />
                      </td>
                      <td>
                        {r.wins}W {r.losses}L {r.draws}D
                      </td>
                      <td>
                        <strong>{r.points}</strong>
                      </td>
                      <td title={r.owner_wallet || ''}>{shortWallet(r.owner_wallet)}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </section>
      </div>

      <div className="section-heading" style={{ marginTop: 32 }}>
        <h1>SEASON PAYOUTS</h1>
        <span>RECENT PRIZE PAYMENTS</span>
      </div>
      {!data?.recent_payouts.length ? (
        <p className="muted">No payouts yet.</p>
      ) : (
        <div className="table-panel">
          <div className="table-scroll">
            <table className="arena-table">
              <thead>
                <tr>
                  <th>Season</th>
                  <th>Place</th>
                  <th>Fighter</th>
                  <th>Amount</th>
                  <th>Status</th>
                </tr>
              </thead>
              <tbody>
                {data.recent_payouts.map((p) => (
                  <tr key={p.id}>
                    <td>#{p.season_id}</td>
                    <td>#{p.place}</td>
                    <td title={p.owner_wallet}>{p.fighter_id}</td>
                    <td>{fmtSol(p.amount_sol)} SOL</td>
                    <td>
                      <span className={`payout-status payout-${p.status}`}>
                        {p.status.toUpperCase()}
                      </span>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </div>
      )}
    </main>
  );
}
