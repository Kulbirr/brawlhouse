/* My Fighters: every FT owned by the connected wallet, with record,
 * rarity, ownership certificate, and queue entry. */

import { useCallback, useEffect, useState } from 'react';
import { useWallet } from '@solana/wallet-adapter-react';
import { WalletMultiButton } from '@solana/wallet-adapter-react-ui';
import { api, type FtFighter, type NextBattle } from '../lib/api';
import { enterBattleQueue, PHASE_MESSAGE, type PaymentPhase } from '../lib/payments';
import { FtCard } from '../components/ft';

export default function MyFightersPage() {
  const { connected, publicKey, signTransaction } = useWallet();
  const [fighters, setFighters] = useState<FtFighter[]>([]);
  const [busyId, setBusyId] = useState<string | null>(null);
  const [phase, setPhase] = useState<PaymentPhase>('idle');
  const [msg, setMsg] = useState('');
  const [entryOpen, setEntryOpen] = useState(true);
  const [windowMin, setWindowMin] = useState(5);

  const load = useCallback(async () => {
    if (!publicKey) {
      setFighters([]);
      return;
    }
    try {
      const d = await api<{ fighters: FtFighter[] }>(
        `/api/ft/wallet/${publicKey.toBase58()}/fighters`,
      );
      setFighters(d.fighters || []);
    } catch {
      /* keep stale */
    }
    try {
      const q = await api<{ next_battle: NextBattle }>('/api/ft/queue');
      setEntryOpen(q.next_battle.entry_open);
      setWindowMin(q.next_battle.entry_window_minutes);
    } catch {
      /* keep stale */
    }
  }, [publicKey]);

  useEffect(() => {
    load();
  }, [load]);

  const enter = async (f: FtFighter) => {
    if (!connected || !publicKey || !signTransaction || busyId) return;
    setBusyId(f.id);
    try {
      await enterBattleQueue(f.id, publicKey.toBase58(), (tx) => signTransaction(tx), {
        setPhase: (p, m) => {
          setPhase(p);
          setMsg(m ?? PHASE_MESSAGE[p] ?? '');
        },
      });
      setMsg(`${f.name || f.id} entered the next official battle.`);
      await load();
    } catch {
      /* message already set */
    } finally {
      setBusyId(null);
    }
  };

  return (
    <main className="section-page">
      <div className="section-heading">
        <h1>MY FIGHTERS</h1>
        <span>{String(fighters.length).padStart(2, '0')} FTs OWNED</span>
      </div>
      {!connected ? (
        <div className="panel born-connect">
          <p className="muted">Connect a wallet to see your fighters.</p>
          <WalletMultiButton />
        </div>
      ) : !fighters.length ? (
        <div className="panel">
          <p className="muted">
            No fighters yet. <a href="#/born">Born your first FT</a> to enter
            official battles and earn prize money.
          </p>
        </div>
      ) : (
        <div className="fighter-grid">
          {fighters.map((f, i) => (
            <FtCard
              key={f.id}
              f={f}
              index={i}
              action={
                f.status === 'active' ? (
                  entryOpen ? (
                    <button
                      className="btn"
                      disabled={busyId === f.id}
                      onClick={() => enter(f)}
                    >
                      {busyId === f.id ? 'ENTERING…' : 'ENTER NEXT BATTLE'}
                    </button>
                  ) : (
                    <p className="muted small ft-status-note">
                      Entry window opens {windowMin} minutes before the next
                      battle.
                    </p>
                  )
                ) : (
                  <p className="muted small ft-status-note">
                    {f.status === 'queued'
                      ? 'Queued for the next official battle.'
                      : f.status === 'fighting'
                        ? 'In a battle right now.'
                        : `Status: ${f.status}`}
                  </p>
                )
              }
            />
          ))}
        </div>
      )}
      {msg && <p className={`phase-msg phase-${phase}`}>{msg}</p>}
      <div className="section-footnote">
        ONLY OFFICIAL BATTLES PAY PRIZES <i /> SPARRING EARNS NOTHING
      </div>
    </main>
  );
}
