/* Queue: the next scheduled official battle — countdown, mode, and the
 * fighters already queued. Enter from My Fighters. */

import { useCallback, useEffect, useState } from 'react';
import { Link } from 'react-router-dom';
import { api, type NextBattle, type QueueRow } from '../lib/api';
import { useApp } from '../lib/store';
import { fmtSol } from '../lib/format';
import { Countdown, shortWallet } from '../components/ft';

export default function QueuePage() {
  const { settings } = useApp();
  const [next, setNext] = useState<NextBattle | null>(null);
  const [queue, setQueue] = useState<QueueRow[]>([]);

  const load = useCallback(async () => {
    try {
      const d = await api<{ queued: QueueRow[]; next_battle: NextBattle }>('/api/ft/queue');
      setQueue(d.queued || []);
      setNext(d.next_battle);
    } catch {
      /* keep stale */
    }
  }, []);

  useEffect(() => {
    load();
    const t = setInterval(load, 10000);
    return () => clearInterval(t);
  }, [load]);

  const entryFee = Number(settings?.entry_fee_sol ?? 0.02);

  return (
    <main className="section-page">
      <div className="section-heading">
        <h1>NEXT OFFICIAL BATTLE</h1>
        <span>{next ? `${next.mode.toUpperCase()} — ${next.mode_size} FIGHTERS` : '…'}</span>
      </div>

      <div className="queue-layout">
        <section className="panel next-battle-card">
          <h3>SCHEDULED</h3>
          {next ? (
            <>
              <div className="next-battle-countdown">
                <Countdown to={next.starts_at} onDone={load} />
              </div>
              <div className="next-battle-meta">
                <div>
                  <span>MODE</span>
                  <strong>{next.mode === 'duel' ? 'DUEL (1v1)' : 'ROYALE (4-WAY)'}</strong>
                </div>
                <div>
                  <span>ENTRY FEE</span>
                  <strong>{fmtSol(entryFee)} SOL</strong>
                </div>
                <div>
                  <span>QUEUED</span>
                  <strong>{next.queued_count}</strong>
                </div>
                <div>
                  <span>ENTRY WINDOW</span>
                  <strong className={next.entry_open ? 'window-open' : 'window-closed'}>
                    {next.entry_open
                      ? `OPEN — ${next.entry_window_minutes} MIN`
                      : 'CLOSED'}
                  </strong>
                </div>
              </div>
              <p className="muted small">
                Entries open {next.entry_window_minutes} minutes before each
                battle. The draw is weighted random with longest-wait priority
                — the longer your fighter waits, the better its odds. Short
                queues are filled with house fighters. Entry fee: 80% to the
                battle prize pool, 20% to the season pool.
              </p>
              <Link className="btn" to="/my-fighters">
                ENTER FROM MY FIGHTERS
              </Link>
              {!next.entry_open && (
                <p className="muted small" style={{ marginTop: 8 }}>
                  Entry window opens {next.entry_window_minutes} minutes
                  before the battle. Fighters you enter stay queued for the
                  draw.
                </p>
              )}
            </>
          ) : (
            <p className="muted">Loading schedule…</p>
          )}
        </section>

        <section className="table-panel">
          <div className="table-headline">
            <span>QUEUED FIGHTERS</span>
            <span>{String(queue.length).padStart(2, '0')} WAITING</span>
          </div>
          {!queue.length ? (
            <p className="muted" style={{ padding: '16px' }}>
              Queue is empty — this battle will be fought by house fighters.
            </p>
          ) : (
            <div className="table-scroll">
              <table className="arena-table">
                <thead>
                  <tr>
                    <th>FT</th>
                    <th>Name</th>
                    <th>Owner</th>
                    <th>Waiting</th>
                  </tr>
                </thead>
                <tbody>
                  {queue.map((q) => (
                    <tr key={q.id}>
                      <td>{q.fighter_id}</td>
                      <td>{q.fighter_name || '—'}</td>
                      <td title={q.owner_wallet}>{shortWallet(q.owner_wallet)}</td>
                      <td className="muted small">
                        {new Date(q.entered_at).toLocaleTimeString()}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </section>
      </div>
    </main>
  );
}
