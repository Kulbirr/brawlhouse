/* Sportsbook-style betting panel: parimutuel pool, odds buttons,
 * animated pool bar, wallet-gated betting via payments.ts. */

import { useCallback, useEffect, useState } from 'react';
import { useWallet } from '@solana/wallet-adapter-react';
import { WalletMultiButton } from '@solana/wallet-adapter-react-ui';
import { api, type BattleSummary, type PoolInfo, type BetRow } from '../lib/api';
import { fmtSol, regIdOf, shortAddr, fighterColor } from '../lib/format';
import { useApp } from '../lib/store';
import { payForBet, PHASE_MESSAGE, type PaymentPhase } from '../lib/payments';

export default function BettingPanel({
  battle,
  done,
}: {
  battle: BattleSummary | null;
  done: boolean;
}) {
  const { settings, fighterName } = useApp();
  const { connected, publicKey, signTransaction } = useWallet();
  const [pool, setPool] = useState<PoolInfo | null>(null);
  const [selected, setSelected] = useState<string | null>(null);
  const [amount, setAmount] = useState('0.10');
  const [phase, setPhase] = useState<PaymentPhase>('idle');
  const [msg, setMsg] = useState('');
  const [myBets, setMyBets] = useState<BetRow[]>([]);

  const loadPool = useCallback(async () => {
    if (!battle) return;
    try {
      const p = await api<PoolInfo>(
        `/api/battles/${encodeURIComponent(battle.id)}/pool`,
      );
      setPool(p);
    } catch {
      /* pool unavailable */
    }
  }, [battle]);

  const loadMine = useCallback(async () => {
    if (!battle || !publicKey) { setMyBets([]); return; }
    try {
      const d = await api<{ bets: BetRow[] }>(
        `/api/battles/${encodeURIComponent(battle.id)}/bets?wallet=${encodeURIComponent(publicKey.toBase58())}`,
      );
      setMyBets(d.bets || []);
    } catch {
      /* ignore */
    }
  }, [battle, publicKey]);

  useEffect(() => {
    setPool(null);
    setSelected(null);
    if (battle && !battle.exhibition) {
      loadPool();
      const t = setInterval(loadPool, 5000);
      return () => clearInterval(t);
    }
  }, [battle, loadPool]);

  useEffect(() => { loadMine(); }, [loadMine, pool]);

  if (!battle) return <p className="muted small">Select a battle to see betting.</p>;
  if (battle.exhibition) {
    return <div className="exhibition-note">Exhibition — no betting on this battle.</div>;
  }

  const total = pool?.total_sol || 0;
  const n = battle.fighter_ids.length;
  const sel = selected && battle.fighter_ids.includes(selected) ? selected : battle.fighter_ids[0];
  /* Bets are only accepted while the battle is 'open' (betting window
   * before the engine runs) — never once the fight has started. */
  const open = battle.status === 'open' && !done;
  const busy = phase === 'initiating' || phase === 'awaiting-signature' ||
    phase === 'broadcasting' || phase === 'confirming';
  const maxBet = settings?.max_bet_sol;

  const setPhaseMsg = (p: PaymentPhase, m?: string) => {
    setPhase(p);
    setMsg(m ?? PHASE_MESSAGE[p]);
  };

  const placeBet = async () => {
    if (!connected || !publicKey || !signTransaction || !sel) return;
    const amt = Number(amount);
    if (!(amt > 0)) { setPhaseMsg('error', 'Enter an amount greater than 0.'); return; }
    if (maxBet && amt > maxBet) {
      setPhaseMsg('error', `Bet exceeds the max of ${fmtSol(maxBet)} SOL.`);
      return;
    }
    setPhaseMsg('idle');
    try {
      const res = await payForBet(
        battle.id, sel, publicKey.toBase58(), amt,
        (tx) => signTransaction(tx),
        { setPhase: setPhaseMsg },
      );
      if (res.live) {
        setMsg(`Bet placed: ${fmtSol(amt)} SOL on ${fighterName(regIdOf(sel))}. Tx ${shortAddr(res.signature, 6)}`);
      } else {
        setMsg(`Bet placed: ${fmtSol(amt)} SOL on ${fighterName(regIdOf(sel))} (simulation).`);
      }
      await loadPool();
      await loadMine();
    } catch {
      /* message already set */
    }
  };

  const maxShare = Math.max(0.001, ...battle.fighter_ids.map((fid) => (pool?.pools[fid] || 0)));

  return (
    <div>
      <div className="pool-head">
        <span>
          POOL <b>{fmtSol(total)} SOL</b>
        </span>
        <span>
          {pool?.bet_count ?? 0} bets · cut {pool?.house_cut_pct ?? settings?.betting_house_cut_pct ?? 5}%
          {settings?.betting_live ? <span style={{ color: 'var(--red)' }}> · LIVE</span> : ' · sim'}
        </span>
      </div>
      <div className="pool-bar">
        <div style={{ width: `${Math.min(100, (total / Math.max(total, 5)) * 100)}%` }} />
      </div>
      <div style={{ marginTop: 10 }}>
        {battle.fighter_ids.map((fid) => {
          const p = pool?.pools[fid] || 0;
          const share = total > 0 ? p / total : 1 / n;
          const mult = p > 0 && total > 0 ? total / p : 0;
          const color = fighterColor(regIdOf(fid));
          return (
            <button
              key={fid}
              className={`bet-option${fid === sel ? ' selected' : ''}`}
              onClick={() => setSelected(fid)}
            >
              <span className="bet-dot" style={{ background: color, color }} />
              <span>{fighterName(regIdOf(fid))}</span>
              <span className="odds">
                {mult > 0 ? `×${mult.toFixed(2)}` : '—'}{' '}
                <span className="muted">({(share * 100).toFixed(1)}%)</span>
              </span>
              <span
                style={{
                  position: 'absolute',
                  left: 0, bottom: 0, top: 0,
                  width: `${(p / maxShare) * 100}%`,
                  background: 'rgba(182,255,46,0.06)',
                  borderRadius: 8,
                  pointerEvents: 'none',
                }}
              />
            </button>
          );
        })}
      </div>
      <label className="field">
        <span>Amount (SOL){maxBet ? ` — max ${fmtSol(maxBet)}` : ''}</span>
        <input
          type="number"
          min="0.01"
          step="0.01"
          value={amount}
          onChange={(e) => setAmount(e.target.value)}
        />
      </label>
      {!connected ? (
        <div className="connect-gate">
          <p>Connect your wallet to place a bet.</p>
          <WalletMultiButton />
        </div>
      ) : (
        <button className="btn" onClick={placeBet} disabled={!open || busy} style={{ width: '100%' }}>
          {!open ? 'Betting closed' : busy ? PHASE_MESSAGE[phase] || 'Working…' : 'Place bet'}
        </button>
      )}
      {msg && (
        <p className="small" style={{ color: phase === 'error' ? 'var(--red)' : 'var(--muted)', marginTop: 10 }}>
          {msg}
        </p>
      )}
      <div className="section-label">My bets</div>
      {!connected ? (
        <p className="muted small">Connect your wallet to see your bets.</p>
      ) : myBets.length ? (
        myBets.map((b) => (
          <div className="bet-row" key={b.id}>
            <span>{fighterName(regIdOf(b.fighter_id))}</span>
            <span><b>{fmtSol(b.amount_sol)} SOL</b></span>
          </div>
        ))
      ) : (
        <p className="muted small">No bets from this wallet yet.</p>
      )}
    </div>
  );
}
