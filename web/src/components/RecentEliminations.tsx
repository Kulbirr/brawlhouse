/* Right column: recent eliminations across recent finished battles.
 * Built from real battle results (elimination_order). Killer attribution is
 * not stored by the engine, so rows show "eliminated" without a byline. */

import { useEffect, useState } from 'react';
import { Link } from 'react-router-dom';
import { api, type BattleSummary } from '../lib/api';
import { regIdOf, timeAgo, fighterColor } from '../lib/format';
import { useApp } from '../lib/store';

interface Elim {
  victim: string;
  battleId: string;
  at: string;
}

interface BattleDetail {
  result?: { elimination_order?: string[] };
}

export default function RecentEliminations({
  battles,
}: {
  battles: BattleSummary[];
}) {
  const { fighterName } = useApp();
  const [elims, setElims] = useState<Elim[]>([]);

  useEffect(() => {
    let cancelled = false;
    (async () => {
      const out: Elim[] = [];
      const finished = battles
        .filter((b) => b.status === 'finished')
        .slice(0, 6);
      for (const b of finished) {
        try {
          const d = await api<BattleDetail>(
            `/api/battles/${encodeURIComponent(b.id)}?snapshots=false`,
          );
          const order = d.result?.elimination_order || [];
          // elimination_order is oldest-first; reverse for most-recent-first
          for (const v of [...order].reverse()) {
            out.push({ victim: v, battleId: b.id, at: b.created_at });
            if (out.length >= 8) break;
          }
        } catch {
          /* skip */
        }
        if (out.length >= 8) break;
      }
      if (!cancelled) setElims(out);
    })();
    return () => {
      cancelled = true;
    };
  }, [battles]);

  return (
    <div className="panel">
      <h3>Recent eliminations</h3>
      {elims.length ? (
        <div className="elim-list">
          {elims.map((e, i) => (
            <Link
              key={`${e.battleId}-${e.victim}-${i}`}
              to={`/arena/${e.battleId}`}
              className="elim-row"
              style={{ textDecoration: 'none' }}
            >
              <span
                className="elim-dot"
                style={{ background: fighterColor(regIdOf(e.victim)) }}
              />
              <span className="elim-main">
                <b>{fighterName(regIdOf(e.victim))}</b>
                <span className="muted small"> eliminated</span>
              </span>
              <span className="muted small mono">{timeAgo(e.at)}</span>
            </Link>
          ))}
        </div>
      ) : (
        <p className="muted small">No eliminations recorded yet.</p>
      )}
    </div>
  );
}
