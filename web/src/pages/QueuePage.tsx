/* Queue: the next scheduled official battle — countdown, mode, and the
 * fighters already queued. Enter from My Fighters, or hire a house
 * fighter if you don't own one. */

import { useCallback, useEffect, useState } from 'react';
import { Link } from 'react-router-dom';
import { useWallet } from '@solana/wallet-adapter-react';
import { WalletMultiButton } from '@solana/wallet-adapter-react-ui';
import {
  api,
  type NextBattle,
  type QueueRow,
  type HireOption,
  type HouseHireRow,
} from '../lib/api';
import { useApp } from '../lib/store';
import { fmtSol } from '../lib/format';
import { Countdown, shortWallet } from '../components/ft';

export default function QueuePage() {
  const { settings } = useApp();
  const { publicKey } = useWallet();
  const [next, setNext] = useState<NextBattle | null>(null);
  const [queue, setQueue] = useState<QueueRow[]>([]);
  const [hireOptions, setHireOptions] = useState<HireOption[]>([]);
  const [hires, setHires] = useState<HouseHireRow[]>([]);
  const [hiring, setHiring] = useState<string | null>(null);
  const [hireMsg, setHireMsg] = useState<string | null>(null);

  const load = useCallback(async () => {
    try {
      const d = await api<{ queued: QueueRow[]; next_battle: NextBattle }>('/api/ft/queue');
      setQueue(d.queued || []);
      setNext(d.next_battle);
    } catch {
      /* keep stale */
    }
    try {
      const h = await api<{ fighters: HireOption[] }>('/api/ft/hire-options');
      setHireOptions(h.fighters || []);
    } catch {
      /* keep stale */
    }
    try {
      const hh = await api<{ hires: HouseHireRow[] }>('/api/ft/hires');
      setHires(hh.hires || []);
    } catch {
      /* keep stale */
    }
  }, []);

  useEffect(() => {
    load();
    const t = setInterval(load, 3000);
    return () => clearInterval(t);
  }, [load]);

  const entryFee = Number(settings?.entry_fee_sol ?? 0.02);
  const myWallet = publicKey?.toBase58() ?? '';
  const iHaveQueued = myWallet !== '' && queue.some((q) => q.owner_wallet === myWallet);
  const iHaveHired = myWallet !== '' && hires.some((h) => h.hirer_wallet === myWallet);
  const canHire = next?.entry_open && myWallet !== '' && !iHaveQueued && !iHaveHired;

  async function doHire(botId: string) {
    if (!myWallet) return;
    setHiring(botId);
    setHireMsg(null);
    try {
      const r = await api<{ hire: { house_bot_id: string } }>('/api/ft/hire', {
        method: 'POST',
        body: JSON.stringify({ house_bot_id: botId, wallet: myWallet }),
      });
      setHireMsg(`${r.hire.house_bot_id} fights for you in the next battle. Win and the prize is yours.`);
      await load();
    } catch (e) {
      setHireMsg(e instanceof Error ? e.message : 'Hire failed');
    } finally {
      setHiring(null);
    }
  }

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
            <span>HIRE A HOUSE FIGHTER</span>
            <span>{hires.length} HIRED</span>
          </div>
          <p className="muted small" style={{ padding: '12px 16px 0' }}>
            No fighter of your own? Hire a house bot — it enters the next
            battle under your wallet, and if it wins, the prize is yours.
            House bots only; player-owned fighters can't be hired.
          </p>
          {!myWallet ? (
            <div style={{ padding: '12px 16px' }}>
              <WalletMultiButton />
            </div>
          ) : iHaveQueued ? (
            <p className="muted small" style={{ padding: '12px 16px' }}>
              You already have a fighter queued — hire is for players without one.
            </p>
          ) : iHaveHired ? (
            <div style={{ padding: '12px 16px' }}>
              {hires
                .filter((h) => h.hirer_wallet === myWallet)
                .map((h) => (
                  <p key={h.id} className="muted small" style={{ margin: '0 0 8px' }}>
                    You've hired <strong style={{ color: '#fff' }}>{h.name}</strong>{' '}
                    ({h.house_bot_id}) for {h.fee_sol} SOL — it fights under
                    your wallet in the next battle. Win and the prize is yours.
                    Good luck.
                  </p>
                ))}
            </div>
          ) : !next?.entry_open ? (
            <p className="muted small" style={{ padding: '12px 16px' }}>
              Hiring opens with the entry window, {next?.entry_window_minutes ?? 2} minutes
              before each battle.
            </p>
          ) : (
            <div className="table-scroll">
              <table className="arena-table">
                <thead>
                  <tr>
                    <th>Fighter</th>
                    <th>Record</th>
                    <th>Hire fee</th>
                    <th></th>
                  </tr>
                </thead>
                <tbody>
                  {hireOptions.map((f) => (
                    <tr key={f.id}>
                      <td>
                        <strong>{f.name}</strong>
                        <div className="muted small">{f.tagline}</div>
                        {hires.filter((h) => h.house_bot_id === f.id).map((h) => (
                          <div key={h.id} className="muted small">
                            Hired by {shortWallet(h.hirer_wallet)}
                          </div>
                        ))}
                      </td>
                      <td className="muted small">
                        {f.wins}W–{f.losses}L{f.draws ? `–${f.draws}D` : ''}
                      </td>
                      <td>{fmtSol(f.hire_fee_sol)} SOL</td>
                      <td>
                        {f.hired ? (
                          <span className="muted small">HIRED</span>
                        ) : (
                          <button
                            className="btn small"
                            disabled={!canHire || hiring !== null}
                            onClick={() => doHire(f.id)}
                          >
                            {hiring === f.id ? 'HIRING…' : 'HIRE'}
                          </button>
                        )}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
          {hireMsg && (
            <p className="small" style={{ padding: '8px 16px' }}>{hireMsg}</p>
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
