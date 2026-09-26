/* Fighter roster — reference-style cards, fed by the real registry API. */

import type { CSSProperties } from 'react';
import { useApp } from '../lib/store';
import { winRate, fighterColor, fmtSol } from '../lib/format';

/* House-fighter glow colors from the engine registry (fallback: deterministic). */
const KNOWN_COLORS: Record<string, string> = {
  'iron-1': '#B6FF2E',
  'hawk-2': '#62D989',
  'aegis-4': '#FF9E45',
  'jackal-5': '#D2FF38',
  'wasp-6': '#B98AFF',
};

export function fighterGlow(regId: string): string {
  return KNOWN_COLORS[regId] || fighterColor(regId);
}

export default function FightersPage() {
  const { fighters, settings } = useApp();

  return (
    <main className="section-page">
      <div className="section-heading">
        <h1>FIGHTER ROSTER</h1>
        <span>{String(fighters.length).padStart(2, '0')} HOUSE FIGHTERS</span>
      </div>
      {!fighters.length ? (
        <p className="muted">Could not load fighters.</p>
      ) : (
        <div className="fighter-grid">
          {fighters.map((f, index) => {
            const wr = winRate(f);
            const color = fighterGlow(f.id);
            return (
              <article
                className="fighter-card"
                key={f.id}
                style={{ '--fighter-color': color } as CSSProperties}
              >
                <div
                  className={`fighter-art fighter-art-${(index % 5) + 1}`}
                  aria-hidden="true"
                >
                  <span>{String(index + 1).padStart(2, '0')}</span>
                  <i />
                </div>
                <div className="fighter-card-heading">
                  <h2>{f.name}</h2>
                  <span>ACTIVE</span>
                </div>
                <strong className="fighter-tagline">{f.tagline}</strong>
                <p>{f.description}</p>
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
                    <span>WIN RATE</span>
                    <strong>{wr === null ? '—' : `${wr.toFixed(1)}%`}</strong>
                  </div>
                </div>
                <div className="fighter-rate-track">
                  <i style={{ width: `${wr === null ? 0 : wr}%` }} />
                </div>
                <div className="fighter-price">
                  <span>HIRE PRICE</span>
                  <strong>{fmtSol(f.hire_fee_sol)} SOL</strong>
                </div>
              </article>
            );
          })}
        </div>
      )}
      <div className="section-footnote">
        HOUSE ROSTER ONLY <i /> RECORDS FROM {settings?.project_name || 'THE ARENA'}
      </div>
    </main>
  );
}
