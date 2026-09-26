/* Shared Phase 1 (FT economy) UI bits: rarity badge, countdown, FT card. */

import { useEffect, useState, type CSSProperties } from 'react';
import type { FtFighter, FtRarity } from '../lib/api';
import { fmtSol } from '../lib/format';
import { fighterGlow } from '../pages/FightersPage';

const RARITY_COLORS: Record<FtRarity, string> = {
  Common: '#9aa39a',
  Rare: '#62d989',
  Epic: '#b98aff',
  Legendary: '#ffb02e',
};

export function RarityBadge({ rarity }: { rarity: FtRarity | null }) {
  if (!rarity) return null;
  return (
    <span
      className="rarity-badge"
      style={{ '--rarity-color': RARITY_COLORS[rarity] } as CSSProperties}
    >
      {rarity.toUpperCase()}
    </span>
  );
}

/** Live countdown to an ISO timestamp, e.g. "04:32". */
export function Countdown({ to, onDone }: { to: string; onDone?: () => void }) {
  const [now, setNow] = useState(() => Date.now());
  useEffect(() => {
    const t = setInterval(() => setNow(Date.now()), 1000);
    return () => clearInterval(t);
  }, []);
  const target = new Date(to).getTime();
  const left = Math.max(0, Math.floor((target - now) / 1000));
  useEffect(() => {
    if (left === 0 && onDone) onDone();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [left === 0]);
  const m = Math.floor(left / 60);
  const s = left % 60;
  return (
    <span className="countdown">
      {String(m).padStart(2, '0')}:{String(s).padStart(2, '0')}
    </span>
  );
}

export function shortWallet(w: string | null): string {
  if (!w) return '—';
  return w.length > 10 ? `${w.slice(0, 4)}…${w.slice(-4)}` : w;
}

export function FtCard({
  f,
  index,
  action,
}: {
  f: FtFighter;
  index: number;
  action?: React.ReactNode;
}) {
  const color = f.colors?.primary || fighterGlow(f.archetype || f.id);
  return (
    <article
      className="fighter-card"
      style={{ '--fighter-color': color } as CSSProperties}
    >
      <div
        className={`fighter-art fighter-art-${(index % 5) + 1}`}
        aria-hidden="true"
      >
        <span>{f.id.replace('FT-', '')}</span>
        <i />
      </div>
      <div className="fighter-card-heading">
        <h2>{f.name || f.id}</h2>
        <RarityBadge rarity={f.rarity} />
      </div>
      <div className="ft-meta">
        <span className="ft-id">{f.id}</span>
        <span className={`ft-status ft-status-${f.status}`}>{f.status.toUpperCase()}</span>
      </div>
      <div className="fighter-record">
        <div>
          <span>W</span>
          <strong>{f.wins}</strong>
        </div>
        <div>
          <span>L</span>
          <strong>{f.losses}</strong>
        </div>
        <div>
          <span>D</span>
          <strong>{f.draws}</strong>
        </div>
        <div className="win-rate">
          <span>STAT MULT</span>
          <strong>{f.stat_mult ? `${f.stat_mult.toFixed(2)}x` : '—'}</strong>
        </div>
      </div>
      <div className="fighter-price">
        <span>OWNER</span>
        <strong title={f.owner_wallet || ''}>{shortWallet(f.owner_wallet)}</strong>
      </div>
      {f.owner_cert && (
        <div className="ft-cert" title={f.owner_cert}>
          CERT <code>{f.owner_cert.slice(0, 18)}…</code>
        </div>
      )}
      {action}
    </article>
  );
}

export function statLine(f: FtFighter): string {
  return `${f.wins}W ${f.losses}L ${f.draws}D`;
}

export { fmtSol };
