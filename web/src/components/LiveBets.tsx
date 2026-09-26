/* Left column: streams recent bets on the selected battle, newest first.
 * Polls the bets endpoint; the sportsbook panel owns pool/odds. */

import { useCallback, useEffect, useState } from 'react';
import { api, type BetRow } from '../lib/api';
import { fmtSol, regIdOf, shortAddr, timeAgo, fighterColor } from '../lib/format';
import { useApp } from '../lib/store';

export default function LiveBets({ battleId }: { battleId: string | null }) {
  const { fighterName } = useApp();
  const [bets, setBets] = useState<BetRow[]>([]);

  const load = useCallback(async () => {
    if (!battleId) {
      setBets([]);
      return;
    }
    try {
      const d = await api<{ bets: BetRow[] }>(
        `/api/battles/${encodeURIComponent(battleId)}/bets`,
      );
      setBets(d.bets || []);
    } catch {
      /* ignore */
    }
  }, [battleId]);

  useEffect(() => {
    load();
    if (!battleId) return;
    const t = setInterval(load, 5000);
    return () => clearInterval(t);
  }, [battleId, load]);

  const rows = [...bets].reverse().slice(0, 8);

  return (
    <div className="panel">
      <h3>
        Live bets
        <span className="panel-count">{bets.length}</span>
      </h3>
      {!battleId ? (
        <p className="muted small">Select a battle to see its bets.</p>
      ) : rows.length ? (
        <div className="livebets-list">
          {rows.map((b) => (
            <div className="livebet-row" key={b.id}>
              <span className="mono muted small" title={b.wallet}>
                {shortAddr(b.wallet)}
              </span>
              <span
                className="livebet-fighter"
                style={{ color: fighterColor(regIdOf(b.fighter_id)) }}
              >
                {fighterName(regIdOf(b.fighter_id))}
              </span>
              <span className="livebet-amt mono">
                <b>{fmtSol(b.amount_sol)}</b> SOL
              </span>
              <span className="muted small mono livebet-time">
                {timeAgo(b.created_at)}
              </span>
            </div>
          ))}
        </div>
      ) : (
        <p className="muted small">No bets on this battle yet.</p>
      )}
    </div>
  );
}
