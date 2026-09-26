/* Battle history — reference table layout, real battle records, paginated. */

import { useCallback, useEffect, useState } from 'react';
import { Link } from 'react-router-dom';
import { api, type BattleSummary } from '../lib/api';
import { regIdOf, fmtTime } from '../lib/format';
import { useApp } from '../lib/store';

const LIMIT = 25;

export default function BattlesPage() {
  const { fighterName } = useApp();
  const [battles, setBattles] = useState<BattleSummary[]>([]);
  const [offset, setOffset] = useState(0);
  const [hasMore, setHasMore] = useState(false);
  const [loading, setLoading] = useState(true);

  const load = useCallback(async (off: number) => {
    setLoading(true);
    try {
      const d = await api<{ battles: BattleSummary[] }>(
        `/api/battles?limit=${LIMIT}&offset=${off}`,
      );
      setHasMore((d.battles || []).length === LIMIT);
      setBattles((prev) => (off === 0 ? d.battles || [] : [...prev, ...(d.battles || [])]));
      setOffset(off);
    } catch {
      /* ignore */
    }
    setLoading(false);
  }, []);

  useEffect(() => { void load(0); }, [load]);

  return (
    <main className="section-page">
      <div className="section-heading">
        <h1>BATTLE HISTORY</h1>
        <span>RECENT BOUTS</span>
      </div>
      <div className="data-panel table-panel">
        <div className="table-headline">
          <span>COMPLETED BATTLES</span>
          <span>SELECT A REPLAY</span>
        </div>
        <div className="table-scroll">
          {loading && offset === 0 ? (
            <p className="muted" style={{ padding: '18px 14px' }}>Loading battles…</p>
          ) : battles.length === 0 ? (
            <p className="muted" style={{ padding: '18px 14px' }}>
              No battles yet. Start one from the Arena.
            </p>
          ) : (
            <table className="arena-table">
              <thead>
                <tr>
                  <th>DATE</th>
                  <th>MATCHUP</th>
                  <th>STATUS</th>
                  <th>TICKS</th>
                  <th>WINNER</th>
                  <th />
                </tr>
              </thead>
              <tbody>
                {battles.map((b) => {
                  const names = b.fighter_ids.map((id) => fighterName(regIdOf(id))).join(' vs ');
                  return (
                    <tr key={b.id}>
                      <td className="date-cell">{fmtTime(b.created_at)}</td>
                      <td>
                        <div className="table-fighter">
                          <strong>{names}</strong>
                        </div>
                      </td>
                      <td>
                        {b.status === 'running' ? (
                          <span className="live-badge">LIVE</span>
                        ) : (
                          <span className="finished-badge">FINISHED</span>
                        )}
                        {b.exhibition && <span className="exhibition-badge">EXHIBITION</span>}
                      </td>
                      <td>{b.ticks ?? '—'}</td>
                      <td className="winner-cell">
                        {b.winner ? fighterName(regIdOf(b.winner)) : 'DRAW'}
                      </td>
                      <td>
                        <Link className="table-action" to={`/arena/${b.id}`}>
                          WATCH REPLAY <span>→</span>
                        </Link>
                      </td>
                    </tr>
                  );
                })}
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
        REPLAYS STREAM FROM STORED SNAPSHOTS <i /> BETTING IS CLOSED ON FINISHED BATTLES
      </div>
    </main>
  );
}
