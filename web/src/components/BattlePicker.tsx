/* Battle picker: live battles + recent finished, links to the arena. */

import { Link } from 'react-router-dom';
import type { BattleSummary } from '../lib/api';
import { regIdOf } from '../lib/format';
import { useApp } from '../lib/store';

function PickerItem({ b, selectedId }: { b: BattleSummary; selectedId: string | null }) {
  const { fighterName } = useApp();
  const names = b.fighter_ids.map((id) => fighterName(regIdOf(id))).join(' vs ');
  return (
    <Link
      to={`/arena/${b.id}`}
      className={`picker-item${b.id === selectedId ? ' selected' : ''}`}
      style={{ textDecoration: 'none', display: 'block' }}
    >
      <div className="row1">
        <span>{names}</span>
        <span className={`badge ${b.status === 'running' ? 'live' : 'done'}`}>
          {b.status === 'running' ? 'LIVE' : 'DONE'}
        </span>
      </div>
      <div className="row2">
        {b.status === 'running' ? 'in progress' : `${b.ticks ?? '?'} ticks`}
        {b.winner ? ` · winner ${fighterName(regIdOf(b.winner))}` : ''}
        {b.exhibition ? ' · exhibition' : ''}
      </div>
    </Link>
  );
}

export default function BattlePicker({
  battles,
  selectedId,
}: {
  battles: BattleSummary[];
  selectedId: string | null;
}) {
  const live = battles.filter((b) => b.status === 'running');
  const recent = battles.filter((b) => b.status === 'finished').slice(0, 10);
  return (
    <div>
      <div className="section-label">Live now</div>
      <div className="picker-list">
        {live.length ? (
          live.map((b) => <PickerItem key={b.id} b={b} selectedId={selectedId} />)
        ) : (
          <p className="muted small">Nothing live right now.</p>
        )}
      </div>
      <div className="section-label">Recent battles</div>
      <div className="picker-list">
        {recent.length ? (
          recent.map((b) => <PickerItem key={b.id} b={b} selectedId={selectedId} />)
        ) : (
          <p className="muted small">No finished battles yet.</p>
        )}
      </div>
    </div>
  );
}
