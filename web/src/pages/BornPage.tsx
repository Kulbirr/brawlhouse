/* Born page: create a new FT (fighter born, not minted). Name + archetype
 * + color, pay the born fee (mock or wallet-signed), rarity is rolled at
 * payment. Birth feed below. */

import { useCallback, useEffect, useState, type CSSProperties } from 'react';
import { useWallet } from '@solana/wallet-adapter-react';
import { WalletMultiButton } from '@solana/wallet-adapter-react-ui';
import { api, type BirthEvent, type BornResult, type FtFighter } from '../lib/api';
import { useApp } from '../lib/store';
import { fmtSol } from '../lib/format';
import { bornFighter, PHASE_MESSAGE, type PaymentPhase } from '../lib/payments';
import { FtCard, RarityBadge, shortWallet } from '../components/ft';

const SWATCHES = ['#B6FF2E', '#62D989', '#FF9E45', '#D2FF38', '#B98AFF', '#FF5D5D', '#5DC8FF', '#FFFFFF'];

export default function BornPage() {
  const { settings, fighters } = useApp();
  const { connected, publicKey, signTransaction } = useWallet();
  const [name, setName] = useState('');
  const [archetype, setArchetype] = useState('');
  const [color, setColor] = useState(SWATCHES[0]);
  const [phase, setPhase] = useState<PaymentPhase>('idle');
  const [msg, setMsg] = useState('');
  const [born, setBorn] = useState<FtFighter | null>(null);
  const [bornInfo, setBornInfo] = useState<BornResult | null>(null);
  const [births, setBirths] = useState<BirthEvent[]>([]);

  const fee = Number(settings?.born_fee_sol ?? 0.1);

  const loadBirths = useCallback(async () => {
    try {
      const d = await api<{ births: BirthEvent[] }>('/api/ft/births?limit=12');
      setBirths(d.births || []);
    } catch {
      /* keep stale */
    }
  }, []);

  useEffect(() => {
    loadBirths();
    const t = setInterval(loadBirths, 15000);
    return () => clearInterval(t);
  }, [loadBirths]);

  useEffect(() => {
    if (!archetype && fighters.length) setArchetype(fighters[0].id);
  }, [fighters, archetype]);

  const hooks = {
    setPhase: (p: PaymentPhase, m?: string) => {
      setPhase(p);
      setMsg(m ?? PHASE_MESSAGE[p] ?? '');
    },
  };

  const submit = async () => {
    if (!connected || !publicKey || !signTransaction) return;
    setBorn(null);
    setBornInfo(null);
    try {
      const res = await bornFighter(
        publicKey.toBase58(),
        name,
        archetype,
        color,
        (tx) => signTransaction(tx),
        hooks,
      );
      if (res.fighter) {
        setBorn(res.fighter);
        setBornInfo(res.result);
      }
      setName('');
      loadBirths();
    } catch {
      /* message already set by the flow */
    }
  };

  const busy = phase === 'initiating' || phase === 'awaiting-signature' || phase === 'broadcasting' || phase === 'confirming';
  const canBorn = connected && name.trim().length > 0 && archetype && !busy;

  return (
    <main className="section-page">
      <div className="section-heading">
        <h1>BORN A FIGHTER</h1>
        <span>{fmtSol(fee)} SOL BORN FEE</span>
      </div>

      <div className="born-layout">
        <section className="panel born-form">
          <h3>NEW FT</h3>
          {!connected ? (
            <div className="born-connect">
              <p className="muted">Connect a wallet to born your fighter.</p>
              <WalletMultiButton />
            </div>
          ) : (
            <>
              <label className="field">
                <span>Fighter name (max 24 chars)</span>
                <input
                  value={name}
                  maxLength={24}
                  onChange={(e) => setName(e.target.value)}
                  placeholder="e.g. NIGHTFANG"
                />
              </label>
              <div className="field">
                <span>Archetype</span>
                <div className="picker-row">
                  {fighters.map((f) => (
                    <button
                      key={f.id}
                      type="button"
                      className={`picker-item${archetype === f.id ? ' selected' : ''}`}
                      onClick={() => setArchetype(f.id)}
                    >
                      {f.name}
                    </button>
                  ))}
                </div>
              </div>
              <div className="field">
                <span>Color</span>
                <div className="picker-row">
                  {SWATCHES.map((c) => (
                    <button
                      key={c}
                      type="button"
                      aria-label={c}
                      className={`swatch${color === c ? ' selected' : ''}`}
                      style={{ background: c } as CSSProperties}
                      onClick={() => setColor(c)}
                    />
                  ))}
                </div>
              </div>
              <div className="born-fee-line">
                <span>Born fee</span>
                <strong>{fmtSol(fee)} SOL</strong>
              </div>
              <p className="muted small">
                Rarity is rolled when you pay — Common 50%, Rare 30%, Epic 15%,
                Legendary 5% (up to 1.25x HP and damage). Half the fee feeds the
                season prize pool.
              </p>
              <button className="btn" disabled={!canBorn} onClick={submit}>
                {busy ? 'BORN IN PROGRESS…' : `BORN FIGHTER — ${fmtSol(fee)} SOL`}
              </button>
              {msg && <p className={`phase-msg phase-${phase}`}>{msg}</p>}
            </>
          )}
        </section>

        {born && (
          <section className="panel born-result">
            <h3>BORN</h3>
            <div className="fighter-grid single">
              <FtCard f={born} index={0} />
            </div>
            {bornInfo && (
              <div className="born-splits">
                <div>
                  <span>RARITY</span>
                  <RarityBadge rarity={born.rarity} />
                </div>
                <div>
                  <span>SEASON POOL SHARE</span>
                  <strong>{fmtSol(bornInfo.season_share_sol ?? 0)} SOL</strong>
                </div>
                <div>
                  <span>OWNER</span>
                  <strong>{shortWallet(born.owner_wallet)}</strong>
                </div>
              </div>
            )}
          </section>
        )}
      </div>

      <div className="section-heading" style={{ marginTop: 32 }}>
        <h1>BIRTH FEED</h1>
        <span>LATEST FTs BORN</span>
      </div>
      {!births.length ? (
        <p className="muted">No fighters born yet — be the first.</p>
      ) : (
        <div className="table-panel">
          <div className="table-scroll">
            <table className="arena-table">
              <thead>
                <tr>
                  <th>FT</th>
                  <th>Name</th>
                  <th>Rarity</th>
                  <th>Owner</th>
                  <th>Born</th>
                </tr>
              </thead>
              <tbody>
                {births.map((b) => (
                  <tr key={b.id}>
                    <td>{b.fighter_id}</td>
                    <td>{b.name}</td>
                    <td>
                      <RarityBadge rarity={b.rarity} />
                    </td>
                    <td title={b.owner_wallet}>{shortWallet(b.owner_wallet)}</td>
                    <td className="muted small">
                      {new Date(b.created_at).toLocaleString()}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </div>
      )}
    </main>
  );
}
