/* Leaderboard: real career records in the reference table layout. */

import { useApp } from '../lib/store';
import { winRate } from '../lib/format';
import { fighterGlow } from './FightersPage';

export default function LeaderboardPage() {
  const { fighters } = useApp();
  const rows = [...fighters].sort((a, b) => {
    if (b.wins !== a.wins) return b.wins - a.wins;
    const wa = winRate(a), wb = winRate(b);
    return (wb ?? -1) - (wa ?? -1);
  });

  return (
    <main className="section-page">
      <div className="section-heading">
        <h1>LEADERBOARD</h1>
        <span>CAREER RECORDS</span>
      </div>
      <div className="data-panel table-panel">
        <div className="table-headline">
          <span>ALL-TIME STANDINGS</span>
          <span>UPDATED LIVE</span>
        </div>
        <div className="table-scroll">
          <table className="arena-table">
            <thead>
              <tr>
                <th>RANK</th>
                <th>FIGHTER</th>
                <th>W</th>
                <th>L</th>
                <th>D</th>
                <th>WIN RATE</th>
                <th>FIGHTS</th>
              </tr>
            </thead>
            <tbody>
              {rows.map((f, i) => {
                const wr = winRate(f);
                const color = fighterGlow(f.id);
                return (
                  <tr key={f.id}>
                    <td className="rank-cell">{String(i + 1).padStart(2, '0')}</td>
                    <td>
                      <div className="table-fighter">
                        <i style={{ backgroundColor: color, color }} />
                        <strong>{f.name}</strong>
                        <span>{f.tagline}</span>
                      </div>
                    </td>
                    <td>{f.wins}</td>
                    <td>{f.losses}</td>
                    <td>{f.draws}</td>
                    <td>
                      <div className="table-rate">
                        <span>{wr === null ? '—' : `${wr.toFixed(1)}%`}</span>
                        <i>
                          <b style={{ width: `${wr === null ? 0 : wr}%`, backgroundColor: color }} />
                        </i>
                      </div>
                    </td>
                    <td>{f.wins + f.losses + f.draws}</td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      </div>
      <div className="section-footnote">
        HOUSE ROSTER ONLY <i /> RANKED BY WINS ACROSS ALL RECORDED BATTLES
      </div>
    </main>
  );
}
