/* Left column: LIVE battle card, QUEUED (real empty state), RECENT battles. */

import { useEffect, useState } from 'react';
import { Link } from 'react-router-dom';
import type { BattleSummary } from '../lib/api';
import { regIdOf, timeAgo, elapsedStr } from '../lib/format';
import { useApp } from '../lib/store';

function LiveCard({
  b,
  selectedId,
  liveTick,
}: {
  b: BattleSummary;
  selectedId: string | null;
  liveTick: number | null;
}) {
  const { fighterName } = useApp();
  const [, setNow] = useState(Date.now());
  useEffect(() => {
    const t = setInterval(() => setNow(Date.now()), 1000);
    return () => clearInterval(t);
  }, []);
  const names = b.fighter_ids.map((id) => fighterName(regIdOf(id))).join(' vs ');
  return (
    <Link
      to={`/arena/${b.id}`}
      className={`queue-live${b.id === selectedId ? ' selected' : ''}`}
      style={{ textDecoration: 'none', display: 'block' }}
    >
      <div className="queue-live-top">
        <span className="live-dot" />
        <span className="queue-live-label">LIVE</span>
        <span className="queue-elapsed mono">{elapsedStr(b.created_at)}</span>
      </div>
      <div className="queue-names">{names}</div>
      <div className="queue-sub mono">
        {b.id === selectedId && liveTick !== null
          ? `tick ${liveTick}`
          : 'in progress'}
        {b.exhibition ? ' · exhibition' : ''}
      </div>
    </Link>
  );
}

function RecentItem({
  b,
  selectedId,
}: {
  b: BattleSummary;
  selectedId: string | null;
}) {
  const { fighterName } = useApp();
  const names = b.fighter_ids.map((id) => fighterName(regIdOf(id))).join(' vs ');
  return (
    <Link
      to={`/arena/${b.id}`}
      className={`queue-item${b.id === selectedId ? ' selected' : ''}`}
      style={{ textDecoration: 'none', display: 'block' }}
    >
      <div className="queue-item-top">
        <span className="section-label" style={{ margin: 0 }}>Recent</span>
        <span className="muted small mono">{timeAgo(b.created_at)}</span>
      </div>
      <div className="queue-names small">{names}</div>
      <div className="queue-sub">
        {b.winner ? (
          <><b style={{ color: 'var(--lime)' }}>{fighterName(regIdOf(b.winner))}</b> won</>
        ) : (
          'Draw'
        )}
        {b.exhibition ? ' · exhibition' : ''}
      </div>
    </Link>
  );
}

export default function BattleQueue({
  battles,
  selectedId,
  liveTick,
  onNewBattle,
}: {
  battles: BattleSummary[];
  selectedId: string | null;
  liveTick: number | null;
  onNewBattle: () => void;
}) {
  const live = battles.filter((b) => b.status === 'running');
  const recent = battles.filter((b) => b.status === 'finished').slice(0, 6);
  // Show the selected battle first when it's live, otherwise the newest live.
  const liveCard =
    live.find((b) => b.id === selectedId) || live[0] || null;

  return (
    <div className="panel">
      <h3>
        Battle queue
        <span className="panel-count">{battles.length}</span>
      </h3>
      {liveCard ? (
        <LiveCard b={liveCard} selectedId={selectedId} liveTick={liveTick} />
      ) : (
        <p className="muted small">Nothing live right now.</p>
      )}

      <div className="section-label">Queued</div>
      <div className="queue-empty">
        <p className="muted small" style={{ margin: '0 0 8px' }}>
          No battles queued.
        </p>
        <button className="btn btn-small btn-ghost" onClick={onNewBattle}>
          Queue a battle
        </button>
      </div>

      <div className="section-label">Recent</div>
      <div className="queue-list">
        {recent.length ? (
          recent.map((b) => (
            <RecentItem key={b.id} b={b} selectedId={selectedId} />
          ))
        ) : (
          <p className="muted small">No finished battles yet.</p>
        )}
      </div>
    </div>
  );
}
