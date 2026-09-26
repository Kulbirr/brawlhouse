/* Hire a fighter: one row per fighter in the battle with Hire / Hired
 * states. Requires a connected wallet. Uses the existing payForHire flow
 * from payments.ts (mock or live sign-and-confirm). */

import { useState } from 'react';
import { useWallet } from '@solana/wallet-adapter-react';
import { WalletMultiButton } from '@solana/wallet-adapter-react-ui';
import type { BattleSummary, HireRow } from '../lib/api';
import { fmtSol, regIdOf, fighterColor } from '../lib/format';
import { useApp } from '../lib/store';
import { payForHire, PHASE_MESSAGE, type PaymentPhase } from '../lib/payments';

export default function HirePanel({
  battle,
  done,
  hires,
  onHired,
}: {
  battle: BattleSummary | null;
  done: boolean;
  hires: HireRow[];
  onHired: () => void;
}) {
  const { settings, fighterName, fighters } = useApp();
  const { connected, publicKey, signTransaction } = useWallet();
  const [busyFid, setBusyFid] = useState<string | null>(null);
  const [phase, setPhase] = useState<PaymentPhase>('idle');
  const [msg, setMsg] = useState('');

  if (!battle) return <p className="muted small">Select a battle to hire a fighter.</p>;

  const baseFee = settings?.hire_fee_sol ?? 0;
  const feeByReg = new Map(fighters.map((f) => [f.id, f.hire_fee_sol]));
  const feeFor = (regId: string) => feeByReg.get(regId) ?? baseFee;
  const live = battle.status === 'running' && !done;
  const hiredSet = new Set(hires.map((h) => h.fighter_id));

  const hire = async (fid: string) => {
    if (!connected || !publicKey || !signTransaction || busyFid) return;
    setBusyFid(fid);
    setPhase('idle');
    setMsg('');
    try {
      const res = await payForHire(
        battle.id,
        fid,
        publicKey.toBase58(),
        (tx) => signTransaction(tx),
        {
          setPhase: (p, m) => {
            setPhase(p);
            setMsg(m ?? PHASE_MESSAGE[p] ?? '');
          },
        },
      );
      const row = res.hire as HireRow | undefined;
      if (res.live) {
        setMsg(`Hired ${fighterName(regIdOf(fid))} — paid ${fmtSol(row?.fee_sol ?? feeFor(regIdOf(fid)))} SOL.`);
      } else {
        setMsg(`Hired ${fighterName(regIdOf(fid))} (simulation — no real payment).`);
      }
      onHired();
    } catch {
      /* the payment flow already surfaced the message via setPhase */
    } finally {
      setBusyFid(null);
    }
  };

  return (
    <div>
      <div className="muted small" style={{ marginBottom: 10 }}>
        Prices follow form — each fighter costs its own strength-based price
        (base <b style={{ color: 'var(--lime)' }}>{fmtSol(baseFee)} SOL</b>).
        {settings?.hiring_live ? (
          <span style={{ color: 'var(--red)' }}> LIVE — real SOL.</span>
        ) : (
          <span> (simulation)</span>
        )}
      </div>
      <div className="hire-list">
        {battle.fighter_ids.map((eid) => {
          const hired = hiredSet.has(eid);
          const busy = busyFid === eid;
          const price = feeFor(regIdOf(eid));
          return (
            <div className="hire-row" key={eid}>
              <span
                className="hire-dot"
                style={{ background: fighterColor(regIdOf(eid)) }}
              />
              <span className="hire-name">
                {fighterName(regIdOf(eid))}
                {eid.includes('#') ? <span className="muted"> ({eid})</span> : ''}
              </span>
              <span className="hire-fee mono">{fmtSol(price)} SOL</span>
              {hired ? (
                <span className="hire-state hired">Hired</span>
              ) : !connected ? (
                <span className="hire-state idle">Hire</span>
              ) : (
                <button
                  className="btn btn-small"
                  disabled={!live || busy}
                  onClick={() => void hire(eid)}
                >
                  {busy ? PHASE_MESSAGE[phase] || 'Working…' : 'Hire'}
                </button>
              )}
            </div>
          );
        })}
      </div>
      {!connected && (
        <div className="connect-gate" style={{ marginTop: 10 }}>
          <p>Connect your wallet to hire a fighter.</p>
          <WalletMultiButton />
        </div>
      )}
      {msg && (
        <p
          className="small"
          style={{
            color: phase === 'error' ? 'var(--red)' : 'var(--muted)',
            marginTop: 10,
          }}
        >
          {msg}
        </p>
      )}
    </div>
  );
}
