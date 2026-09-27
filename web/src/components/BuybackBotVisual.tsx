/* Buyback bot theater: a little bot that sleeps between cycles, wakes up
   when a cycle runs, and walks the pipeline: treasury -> swap -> burn
   furnace, then goes back to sleep. Pure visualization; the real cycle
   already happened server-side. */

import { useEffect, useRef, useState } from 'react';
import { api } from '../lib/api';

interface BuybackStatus {
  running: boolean;
  interval_minutes: number;
  buyback_live: boolean;
  last_run_at: string | null;
  last_status: string | null;
  last_detail: string;
  cycles: number;
}

type Stage = 'sleeping' | 'wake' | 'treasury' | 'buy' | 'burn';

const STAGE_MS: Record<Exclude<Stage, 'sleeping'>, number> = {
  wake: 2500,
  treasury: 3200,
  buy: 3200,
  burn: 4200,
};

const STAGE_POS: Record<Stage, number> = {
  sleeping: 6,
  wake: 6,
  treasury: 36,
  buy: 64,
  burn: 92,
};

function fmtCountdown(ms: number): string {
  if (ms < 0) ms = 0;
  const s = Math.floor(ms / 1000);
  const m = Math.floor(s / 60);
  return `${String(m).padStart(2, '0')}:${String(s % 60).padStart(2, '0')}`;
}

export default function BuybackBotVisual({ ticker }: { ticker: string }) {
  const [bot, setBot] = useState<BuybackStatus | null>(null);
  const [stage, setStage] = useState<Stage>('sleeping');
  const [now, setNow] = useState(() => Date.now());
  const seenCycles = useRef<number | null>(null);
  const timers = useRef<number[]>([]);

  const clearTimers = () => {
    timers.current.forEach((t) => window.clearTimeout(t));
    timers.current = [];
  };

  const playRun = () => {
    clearTimers();
    const seq: Exclude<Stage, 'sleeping'>[] = ['wake', 'treasury', 'buy', 'burn'];
    let t = 0;
    seq.forEach((st) => {
      timers.current.push(window.setTimeout(() => setStage(st), t));
      t += STAGE_MS[st];
    });
    timers.current.push(window.setTimeout(() => setStage('sleeping'), t));
  };

  useEffect(() => {
    let alive = true;
    const load = async () => {
      try {
        const s = await api<BuybackStatus>('/api/treasury/buyback-status');
        if (!alive) return;
        setBot(s);
        if (seenCycles.current === null) {
          seenCycles.current = s.cycles;
        } else if (s.cycles > seenCycles.current) {
          seenCycles.current = s.cycles;
          playRun();
        }
      } catch {
        /* offline: stay sleeping */
      }
    };
    void load();
    const poll = window.setInterval(load, 20000);
    const tick = window.setInterval(() => setNow(Date.now()), 1000);
    return () => {
      alive = false;
      window.clearInterval(poll);
      window.clearInterval(tick);
      clearTimers();
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const replay = () => {
    if (!bot || stage !== 'sleeping') return;
    playRun();
  };

  const intervalMs = (bot?.interval_minutes || 30) * 60 * 1000;
  const lastRun = bot?.last_run_at ? new Date(bot.last_run_at).getTime() : null;
  const nextRun = lastRun ? lastRun + intervalMs : now + intervalMs;
  const paused = bot?.last_status === 'paused';

  const stageText = (): string => {
    if (!bot) return 'Connecting to bot...';
    if (paused && stage === 'sleeping') return 'Paused by admin. Fees pile up untouched.';
    switch (stage) {
      case 'wake':
        return 'Waking up. Cycle time.';
      case 'treasury':
        return `Raking the treasury for fee SOL...`;
      case 'buy':
        return bot.buyback_live
          ? `Swapping SOL for $${ticker} on Jupiter...`
          : `Simulating the swap: SOL for $${ticker}...`;
      case 'burn':
        return bot.last_detail
          ? `Burning. Last run: ${bot.last_detail}.`
          : `Burning $${ticker} forever.`;
      default:
        return bot.running
          ? `Sleeping. Next run in ${fmtCountdown(nextRun - now)}.`
          : 'Bot stopped.';
    }
  };

  const botGlow = stage === 'sleeping' ? '0 0 12px rgba(182,255,46,0.15)' : '0 0 26px rgba(182,255,46,0.8)';

  const stations = [
    { label: 'DOCK', sub: 'bot sleeps here', pos: 6 },
    { label: 'TREASURY', sub: 'collect fee SOL', pos: 36 },
    { label: 'SWAP', sub: `buy $${ticker}`, pos: 64 },
    { label: 'FURNACE', sub: 'burn forever', pos: 92 },
  ];

  return (
    <div className="data-panel" style={{ marginBottom: 14, padding: '18px 20px 14px' }}>
      <style>{`
        @keyframes botZzz { 0%,100% { transform: translateY(0); opacity:.9; } 50% { transform: translateY(-6px); opacity:.4; } }
        @keyframes botFlame { 0%,100% { transform: scaleY(1); } 50% { transform: scaleY(1.35); } }
        @keyframes botPulse { 0%,100% { opacity:.5; } 50% { opacity:1; } }
      `}</style>
      <div className="table-headline" style={{ marginBottom: 14 }}>
        <span>BUYBACK BOT</span>
        <span>{bot ? (bot.buyback_live ? 'LIVE' : 'SIMULATED') : ''}</span>
      </div>

      {/* pipeline */}
      <div style={{ position: 'relative', height: 118, margin: '6px 4px 0' }}>
        {/* track line */}
        <div style={{
          position: 'absolute', left: '6%', right: '8%', top: 56, height: 2,
          background: 'rgba(182,255,46,0.18)',
        }} />
        {/* stations */}
        {stations.map((s) => (
          <div key={s.label} style={{
            position: 'absolute', left: `${s.pos}%`, top: 0, transform: 'translateX(-50%)',
            textAlign: 'center', width: 110,
          }}>
            <div style={{
              width: 34, height: 34, margin: '38px auto 6px', borderRadius: '50%',
              border: `2px solid ${stage !== 'sleeping' && STAGE_POS[stage] >= s.pos ? '#b6ff2e' : 'rgba(182,255,46,0.25)'}`,
              display: 'grid', placeItems: 'center', fontSize: 15,
              background: '#0c0e13',
              transition: 'border-color .6s',
            }}>
              {s.label === 'DOCK' ? '🛏' : s.label === 'TREASURY' ? '🏦' : s.label === 'SWAP' ? '🔁' : '🔥'}
            </div>
            <div style={{ fontSize: 10, letterSpacing: 1.5, color: 'var(--text)', fontWeight: 700 }}>{s.label}</div>
            <div style={{ fontSize: 10, color: 'var(--muted)' }}>{s.sub}</div>
          </div>
        ))}
        {/* the bot */}
        <div style={{
          position: 'absolute',
          left: `${STAGE_POS[stage]}%`,
          top: 8,
          transform: 'translateX(-50%)',
          transition: 'left 1.4s ease-in-out',
          textAlign: 'center',
          zIndex: 2,
        }}>
          <div style={{
            width: 44, height: 44, borderRadius: 12,
            background: stage === 'sleeping' ? '#151a21' : '#1d2413',
            border: '2px solid #b6ff2e',
            boxShadow: botGlow,
            display: 'grid', placeItems: 'center',
            transition: 'box-shadow .6s, background .6s',
            position: 'relative',
          }}>
            {/* eyes */}
            <div style={{ display: 'flex', gap: 7 }}>
              {stage === 'sleeping' ? (
                <span style={{ color: '#b6ff2e', fontSize: 13, letterSpacing: 2 }}>– –</span>
              ) : (
                <>
                  <span style={{ width: 7, height: 7, borderRadius: '50%', background: '#b6ff2e', animation: stage === 'burn' ? 'botPulse .5s infinite' : undefined }} />
                  <span style={{ width: 7, height: 7, borderRadius: '50%', background: '#b6ff2e', animation: stage === 'burn' ? 'botPulse .5s infinite' : undefined }} />
                </>
              )}
            </div>
            {/* zzz */}
            {stage === 'sleeping' && !paused && (
              <span style={{
                position: 'absolute', top: -18, right: -8, fontSize: 13,
                animation: 'botZzz 2.2s infinite', color: '#8b93a1',
              }}>z</span>
            )}
            {/* flame lick while burning */}
            {stage === 'burn' && (
              <span style={{
                position: 'absolute', top: -16, left: '50%', transform: 'translateX(-50%)',
                fontSize: 16, animation: 'botFlame .5s infinite', transformOrigin: 'bottom',
              }}>🔥</span>
            )}
          </div>
        </div>
      </div>

      {/* status line */}
      <div style={{
        display: 'flex', alignItems: 'center', justifyContent: 'space-between',
        marginTop: 6, gap: 12, flexWrap: 'wrap',
      }}>
        <span className="mono" style={{ fontSize: 13, color: 'var(--text)' }}>
          <span style={{
            display: 'inline-block', width: 8, height: 8, borderRadius: '50%',
            background: stage === 'sleeping' ? '#565d6a' : '#b6ff2e',
            marginRight: 8, boxShadow: stage === 'sleeping' ? undefined : '0 0 8px #b6ff2e',
          }} />
          {stageText()}
        </span>
        <button
          className="table-action"
          disabled={stage !== 'sleeping' || !bot}
          onClick={replay}
          style={{ opacity: stage !== 'sleeping' ? 0.4 : 1 }}
        >
          REPLAY LAST RUN
        </button>
      </div>
    </div>
  );
}
