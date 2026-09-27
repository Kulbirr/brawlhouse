/* Arena page: 3-column degen-terminal layout.
 * LEFT: battle queue + live bets. CENTER: live battle canvas with HP overlay,
 * event ticker, stat cards, sportsbook. RIGHT: recent eliminations, hire,
 * live chat. Full-width combat log strip below.
 *
 * The canvas combat rendering (ArenaRenderer) is untouched: this page only
 * reads snapshot messages for the HP overlay, damage/shield detection and
 * the kill feed.
 */

import { useCallback, useEffect, useRef, useState } from 'react';
import { useParams, useNavigate } from 'react-router-dom';
import { useWallet } from '@solana/wallet-adapter-react';
import {
  api,
  type BattleSummary,
  type WsMessage,
  type WsFighter,
  type PoolInfo,
  type ChatMessage,
} from '../lib/api';
import { regIdOf, fmtSol, fighterColor } from '../lib/format';
import { Countdown } from '../components/ft';
import { useApp } from '../lib/store';
import { ArenaRenderer } from '../arena/renderer';
import BattleQueue from '../components/BattleQueue';
import LiveBets from '../components/LiveBets';
import RecentEliminations from '../components/RecentEliminations';
import BettingPanel from '../components/BettingPanel';
import ChatPanel from '../components/ChatPanel';

interface CombatRow {
  id: number;
  clock: string;
  text: string;
  tag: string;
  kind: 'dmg' | 'elim' | 'shield' | 'hire' | 'start' | 'end' | 'pool';
}

interface ElimEvent {
  victim: string;
  killer: string | null;
  tick: number | null;
}

let rowSeq = 0;

const fmtClock = (since: number): string => {
  const s = Math.max(0, Math.floor((Date.now() - since) / 1000));
  return `${String(Math.floor(s / 60)).padStart(2, '0')}:${String(s % 60).padStart(2, '0')}`;
};

export default function ArenaPage() {
  const { battleId } = useParams<{ battleId?: string }>();
  const navigate = useNavigate();
  const { fighterName, settings } = useApp();

  const canvasRef = useRef<HTMLCanvasElement>(null);
  const rendererRef = useRef<ArenaRenderer | null>(null);
  const wsRef = useRef<WebSocket | null>(null);
  const seqRef = useRef(0);
  const chatSeqRef = useRef(0);
  const prevSnapRef = useRef<WsFighter[] | null>(null);
  const incomingRef = useRef<{ fighters: WsFighter[] } | null>(null);
  const logStartRef = useRef<number>(Date.now());
  const poolTotalRef = useRef<number | null>(null);
  /* The WS message closure is registered once per battle; the registry may
     load later, so name lookups inside handlers must use the latest. */
  const fighterNameRef = useRef(fighterName);
  fighterNameRef.current = fighterName;

  const [battles, setBattles] = useState<BattleSummary[]>([]);
  const [battle, setBattle] = useState<BattleSummary | null>(null);
  const [live, setLive] = useState(false);
  /* Phase of the WS stream for the selected battle: 'pregame' means we
   * connected during the betting window and are waiting for the engine.
   * The backend only sends 'info' on connect, so the first snapshot is
   * what tells us the fight actually started. */
  const phaseRef = useRef<'pregame' | 'live' | 'replay'>('replay');
  /* True while the selected battle is a live stream we haven't seen end.
   * The backend marks battles finished the instant the engine bursts, but
   * the viewer still watches the snapshot backlog until 'done' arrives
   * the battle is live for this viewer, and the queue should say so. */
  const [watchingLive, setWatchingLive] = useState(false);
  const [modeLabel, setModeLabel] = useState('');
  const [tick, setTick] = useState<number | null>(null);
  const [note, setNote] = useState('');
  const [done, setDone] = useState(false);
  const { publicKey } = useWallet();
  const walletAddr = publicKey?.toBase58() ?? '';
  const walletRef = useRef(walletAddr);
  walletRef.current = walletAddr;
  const battleIdRef = useRef<string>('');
  const [winner, setWinner] = useState<{ regId: string; draw: boolean } | null>(
    null,
  );
  const [fighters, setFighters] = useState<WsFighter[]>([]);
  const [pool, setPool] = useState<PoolInfo | null>(null);
  const [poolStart, setPoolStart] = useState<number | null>(null);
  const [combatLog, setCombatLog] = useState<CombatRow[]>([]);
  const [elims, setElims] = useState<ElimEvent[]>([]);
  const [tickerItems, setTickerItems] = useState<string[]>([]);
  const [wsChat, setWsChat] = useState<{ msg: ChatMessage; seq: number } | null>(null);
  /* Betting window: battle created but engine not started yet. */
  const [pregame, setPregame] = useState(false);
  const [countdown, setCountdown] = useState<number | null>(null);
  const countdownRef = useRef<number | null>(null);
  const [empty, setEmpty] = useState(true);

  const pushCombat = useCallback(
    (text: string, tag: string, kind: CombatRow['kind']) => {
      const row: CombatRow = {
        id: ++rowSeq,
        clock: fmtClock(logStartRef.current),
        text,
        tag,
        kind,
      };
      setCombatLog((prev) => [...prev.slice(-49), row]);
    },
    [],
  );

  const pushTicker = useCallback((item: string) => {
    setTickerItems((prev) => [...prev.slice(-11), item]);
  }, []);

  /** Killer attribution: the one fighter whose kill count rose on the
   *  killing tick. Null when ambiguous (no guessing). */
  const attributeKill = useCallback((victimId: string): string | null => {
    const prev = prevSnapRef.current;
    const cur = incomingRef.current;
    if (!prev || !cur) return null;
    const prevKills = new Map(prev.map((f) => [f.id, f.kills || 0]));
    const candidates = cur.fighters.filter(
      (f) =>
        f.id !== victimId &&
        f.alive &&
        (f.kills || 0) > (prevKills.get(f.id) || 0),
    );
    return candidates.length === 1 ? candidates[0].id : null;
  }, []);

  /** Damage + shield events from snapshot diffs (renderer untouched). */
  const detectCombat = useCallback(
    (prev: WsFighter[] | null, cur: WsFighter[], tickN: number) => {
      if (!prev) return;
      const prevById = new Map(prev.map((f) => [f.id, f]));
      for (const f of cur) {
        const p = prevById.get(f.id);
        if (!p) continue;
        const name = fighterName(regIdOf(f.id));
        if (f.alive && p.hp > f.hp) {
          const dmg = Math.round(p.hp - f.hp);
          if (dmg > 0) {
            pushCombat(`${name} takes ${dmg} damage`, `-${dmg} HP`, 'dmg');
            if (dmg >= 15) {
              pushTicker(`TICK ${tickN} · ${name} takes ${dmg}dmg`);
            }
          }
        }
        if (!p.shield_active && f.shield_active && f.alive) {
          pushCombat(`${name} raised shields`, 'SHIELD', 'shield');
        }
      }
    },
    [fighterName, pushCombat, pushTicker],
  );

  const [nextStartsAt, setNextStartsAt] = useState<string | null>(null);

  const refreshPicker = useCallback(async () => {
    try {
      const d = await api<{ battles: BattleSummary[] }>('/api/battles?limit=30');
      setBattles(d.battles || []);
    } catch {
      /* ignore */
    }
    /* next official battle, for the status-bar countdown */
    try {
      const q = await api<{ next_battle: { starts_at: string } | null }>(
        '/api/ft/queue',
      );
      setNextStartsAt(q.next_battle?.starts_at || null);
    } catch {
      /* ignore */
    }
  }, []);

  const loadPool = useCallback(
    async (b: BattleSummary) => {
      if (b.exhibition) {
        setPool(null);
        return;
      }
      try {
        const p = await api<PoolInfo>(
          `/api/battles/${encodeURIComponent(b.id)}/pool`,
        );
        setPool((prev) => {
          if (prev && p.total_sol > prev.total_sol) {
            const delta = Math.round((p.total_sol - prev.total_sol) * 10000) / 10000;
            pushTicker(`Pool +${delta} SOL`);
            pushCombat(`Betting pool grows to ${p.total_sol} SOL`, 'POOL', 'pool');
          }
          return p;
        });
        setPoolStart((prev) => (prev === null ? p.total_sol : prev));
        poolTotalRef.current = p.total_sol;
      } catch {
        /* pool unavailable */
      }
    },
    [pushCombat, pushTicker],
  );

  const connect = useCallback(
    async (id: string) => {
      const seq = ++seqRef.current;
      if (wsRef.current) {
        try {
          wsRef.current.close();
        } catch {
          /* noop */
        }
        wsRef.current = null;
      }
      const r = rendererRef.current;
      if (r) r.reset();
      prevSnapRef.current = null;
      incomingRef.current = null;
      logStartRef.current = Date.now();
      poolTotalRef.current = null;
      setCombatLog([]);
      setElims([]);
      setTickerItems([]);
      setFighters([]);
      setPool(null);
      setPoolStart(null);
      setDone(false);
      setWinner(null);
      setTick(null);
      setEmpty(false);
      setNote('Connecting…');

      let meta: BattleSummary;
      try {
        meta = await api<BattleSummary>(
          `/api/battles/${encodeURIComponent(id)}?snapshots=false`,
        );
      } catch {
        if (seq !== seqRef.current) return;
        setNote('Battle not found.');
        return;
      }
      if (seq !== seqRef.current) return;
      setBattle(meta);
      battleIdRef.current = meta.id;
      phaseRef.current = 'replay';
      setWatchingLive(false);
      setLive(false);
      /* Betting window: battle is open, engine starts at the deadline. */
      if (countdownRef.current) {
        window.clearInterval(countdownRef.current);
        countdownRef.current = null;
      }
      setCountdown(null);
      if (meta.status === 'open') {
        setPregame(true);
        const windowSec = Number(settings?.betting_window_sec) || 60;
        const deadline = new Date(meta.created_at).getTime() + windowSec * 1000;
        const updateCountdown = () => {
          const remain = Math.max(0, Math.ceil((deadline - Date.now()) / 1000));
          setCountdown(remain);
          if (remain <= 0 && countdownRef.current) {
            window.clearInterval(countdownRef.current);
            countdownRef.current = null;
          }
        };
        updateCountdown();
        countdownRef.current = window.setInterval(updateCountdown, 1000);
        setModeLabel('BETS OPEN');
        setNote('Bets are open. The fight starts when the countdown hits zero.');
      } else {
        setPregame(false);
      }
      pushCombat(
        `Battle started: ${meta.fighter_ids.map((f) => fighterName(regIdOf(f))).join(' vs ')}${meta.exhibition ? ' (exhibition)' : ''}`,
        'START',
        'start',
      );
      pushTicker(
        `${meta.fighter_ids.map((f) => fighterName(regIdOf(f))).join(' vs ')}: battle started`,
      );
      void loadPool(meta);

      const proto = window.location.protocol === 'https:' ? 'wss' : 'ws';
      const ws = new WebSocket(
        `${proto}://${window.location.host}/ws/battles/${encodeURIComponent(id)}`,
      );
      wsRef.current = ws;
      ws.onmessage = (ev) => {
        if (wsRef.current !== ws) return;
        let msg: WsMessage;
        try {
          msg = JSON.parse(ev.data);
        } catch {
          return;
        }
        const rr = rendererRef.current;
        if (msg.type === 'info') {
          const isLive = msg.status === 'live';
          const isPregame = msg.battle_status === 'open';
          phaseRef.current = isPregame ? 'pregame' : isLive ? 'live' : 'replay';
          setWatchingLive(isLive);
          setLive(isLive && !isPregame);
          setPregame(isPregame);
          setModeLabel(isPregame ? 'BETS OPEN' : isLive ? 'STREAMING' : 'REPLAY');
          setNote(
            isPregame
              ? 'Bets are open. The fight starts when the countdown hits zero.'
              : isLive
                ? 'Streaming live. Ticks arrive as the engine runs them.'
                : 'Replay of a finished battle, streamed from stored snapshots.',
          );
        } else if (msg.type === 'snapshot') {
          if (phaseRef.current === 'pregame') {
            /* The betting window closed and the engine started: flip the
             * badges now. Without this the top stays stuck on "BETS OPEN"
             * and the red LIVE badge never appears for the whole fight. */
            phaseRef.current = 'live';
            setLive(true);
            setPregame(false);
            setModeLabel('STREAMING');
            setNote('Streaming live. Ticks arrive as the engine runs them.');
          }
          detectCombat(prevSnapRef.current, msg.fighters, msg.tick);
          prevSnapRef.current = msg.fighters;
          incomingRef.current = msg;
          rr?.pushSnapshot(msg);
          setFighters(msg.fighters);
          setTick(msg.tick);
        } else if (msg.type === 'chat') {
          setWsChat({ msg, seq: ++chatSeqRef.current });
        } else if (msg.type === 'done') {
          setDone(true);
          setWatchingLive(false);
          setNote('');
          // Player has now seen the battle end: reveal their notifications.
          const w = walletRef.current;
          const bid = battleIdRef.current;
          if (w && bid) {
            api('/api/notifications/release', {
              method: 'POST',
              body: JSON.stringify({ wallet: w, battle_id: bid }),
            }).catch(() => {});
          }
          const res = msg.result;
          const wReg = regIdOf(res.winner || '');
          const wName = fighterNameRef.current(wReg);
          setWinner({ regId: wReg, draw: !!res.draw });
          if (!res.draw && res.winner) rr?.spotlight(res.winner);
          pushCombat(
            res.draw ? 'Battle over: DRAW' : `Battle over: ${wName} wins`,
            'END',
            'end',
          );
          pushTicker(
            res.draw ? 'Battle over: DRAW' : `${wName} wins the battle`,
          );
          void refreshPicker();
        }
      };
      ws.onclose = () => {
        if (wsRef.current === ws) wsRef.current = null;
      };
    },
    [fighterName, loadPool, pushCombat, pushTicker, refreshPicker, detectCombat, settings],
  );

  /* renderer lifecycle: combat visuals untouched */
  useEffect(() => {
    const canvas = canvasRef.current;
    if (!canvas) return;
    const renderer = new ArenaRenderer(canvas, {
      onEliminate: (f, t) => {
        const killerId = attributeKill(f.id);
        const vName = fighterName(regIdOf(f.id));
        const kName = killerId ? fighterName(regIdOf(killerId)) : null;
        pushCombat(
          kName ? `${kName} defeated ${vName}` : `${vName} eliminated`,
          'ELIM',
          'elim',
        );
        pushTicker(
          `TICK ${t} · ${kName ? `${kName} defeats ${vName}` : `${vName} eliminated`}`,
        );
        setElims((prev) => [
          ...prev.slice(-4),
          { victim: f.id, killer: killerId, tick: t },
        ]);
      },
    });
    renderer.nameOf = (reg) => fighterName(reg);
    rendererRef.current = renderer;
    renderer.start();
    return () => {
      renderer.stop();
      rendererRef.current = null;
    };
  }, [fighterName, pushCombat, pushTicker, attributeKill]);

  /* connect when the route battle changes */
  useEffect(() => {
    void refreshPicker();
    if (battleId) {
      void connect(battleId);
    } else {
      setEmpty(true);
      setBattle(null);
      setNote('');
      setFighters([]);
      setCombatLog([]);
      setElims([]);
      setTickerItems([]);
      setPool(null);
      if (wsRef.current) {
        try {
          wsRef.current.close();
        } catch {
          /* noop */
        }
        wsRef.current = null;
      }
      rendererRef.current?.reset();
    }
    return () => {
      if (!battleId && wsRef.current) {
        try {
          wsRef.current.close();
        } catch {
          /* noop */
        }
        wsRef.current = null;
      }
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [battleId]);

  /* No manual battle creation: always show the newest open/running
     battle (falling back to the latest finished one) when the route
     has no battle selected. */
  useEffect(() => {
    if (battleId || battles.length === 0) return;
    const current =
      battles.find((b) => b.status === 'open' || b.status === 'running') ||
      battles[0];
    navigate(`/arena/${current.id}`, { replace: true });
  }, [battles, battleId, navigate]);

  /* keep queue + hires fresh while watching */
  useEffect(() => {
    const t = setInterval(() => void refreshPicker(), 15000);
    return () => clearInterval(t);
  }, [refreshPicker]);

  /* pool polling for the stat cards */
  useEffect(() => {
    if (!battle || battle.exhibition) return;
    const t = setInterval(() => void loadPool(battle), 5000);
    return () => clearInterval(t);
  }, [battle, loadPool]);

  /* Clear the betting-window countdown when leaving the page. */
  useEffect(
    () => () => {
      if (countdownRef.current) {
        window.clearInterval(countdownRef.current);
        countdownRef.current = null;
      }
    },
    [],
  );

  /* ---------------- derived ---------------- */

  const names = battle
    ? battle.fighter_ids.map((id) => fighterName(regIdOf(id))).join(' vs ')
    : '';
  const shortId = battle ? battle.id.replace(/^battle-/, '').slice(0, 12) : '';

  const leader =
    fighters.length > 0
      ? [...fighters]
          .filter((f) => f.alive)
          .sort((a, b) => b.hp - a.hp)[0] || null
      : null;

  const poolTotal = pool?.total_sol || 0;
  const poolDelta =
    pool && poolStart !== null ? Math.round((pool.total_sol - poolStart) * 10000) / 10000 : 0;
  const nFighters = battle?.fighter_ids.length || 0;
  const favEntry =
    pool && poolTotal > 0
      ? Object.entries(pool.pools).sort((a, b) => b[1] - a[1])[0]
      : null;
  const favOdds =
    favEntry && favEntry[1] > 0 ? poolTotal / favEntry[1] : 0;

  const tickerLine =
    tickerItems.length > 0
      ? tickerItems
      : ['Waiting for battle events…'];

  return (
    <div>
      <div className="arena3">
        {/* ---------------- LEFT ---------------- */}
        <div className="col-stack">
          <BattleQueue
            battles={battles}
            selectedId={battleId || null}
            liveTick={tick}
            watchingLive={watchingLive && !done}
          />
          <LiveBets battleId={battleId || null} />
        </div>

        {/* ---------------- CENTER ---------------- */}
        <div className="col-center">
          <div className="arena-statusbar">
            <h1 className="page-title" style={{ margin: 0, fontSize: 20 }}>
              {battle ? (
                <>
                  <span className="mono" style={{ fontSize: 15, color: 'var(--muted)' }}>
                    LIVE BATTLE
                  </span>{' '}
                  <span className="arena-battle-label mono">{shortId}</span>
                  {battle.exhibition && (
                    <span className="badge exh" style={{ marginLeft: 10 }}>exhibition</span>
                  )}
                </>
              ) : (
                <>Combat <span className="accent">arena</span></>
              )}
            </h1>
            {battle && (
              <>
                {live && !done && (
                  <span className="badge live">
                    <span className="live-dot" /> LIVE
                  </span>
                )}
                {modeLabel && <span className="badge done">{modeLabel}</span>}
                {tick !== null && <span className="arena-tick">tick {tick}</span>}
              </>
            )}
            {!live && !pregame && nextStartsAt && (
              <span className="badge done" style={{ marginLeft: 'auto' }}>
                NEXT BATTLE&nbsp;
                <Countdown to={nextStartsAt} />
              </span>
            )}
          </div>

          <div className="arena-canvas-wrap">
            <canvas
              ref={canvasRef}
              style={{ width: '100%', height: 'auto', display: 'block' }}
            />
            {fighters.length > 0 && !empty && !winner && (
              <div className="hp-overlay">
                {fighters.map((f) => {
                  const pct = Math.max(0, Math.min(100, f.hp));
                  const color = f.alive
                    ? fighterColor(regIdOf(f.id))
                    : 'var(--dim)';
                  return (
                    <div className="hp-chip" key={f.id}>
                      <span className="hp-name" style={{ color }}>
                        {fighterName(regIdOf(f.id))}
                      </span>
                      <div className="hp-bar">
                        <div style={{ width: `${pct}%`, background: color }} />
                      </div>
                      <span className="hp-num mono">
                        {Math.round(f.hp)} / 100
                      </span>
                    </div>
                  );
                })}
              </div>
            )}
            {empty && (
              <div className="arena-empty">
                <div>
                  <p style={{ fontSize: 18, color: 'var(--text)' }}>No battle selected.</p>
                  <p>Pick a live fight or a replay from the queue.</p>
                </div>
              </div>
            )}
            {pregame && !empty && (
              <div className="arena-empty">
                <div>
                  <p style={{ fontSize: 22, color: 'var(--lime, #b6ff2e)' }}>BETS OPEN</p>
                  <p className="mono" style={{ fontSize: 34 }}>
                    {countdown !== null ? `${countdown}s` : '-'}
                  </p>
                  <p>Place your bets. The fight starts when the countdown hits zero.</p>
                </div>
              </div>
            )}
            {winner && !empty && (
              <div
                className="winner-overlay"
                onClick={() => {
                  setWinner(null);
                  rendererRef.current?.spotlight(null);
                }}
              >
                <div className="winner-kicker">
                  {winner.draw ? 'BATTLE OVER' : 'WINNER'}
                </div>
                <div className="winner-name">
                  {winner.draw ? 'DRAW' : fighterName(winner.regId)}
                </div>
                <div className="winner-hint">TAP TO DISMISS</div>
              </div>
            )}
          </div>
          {note && <div className="arena-notes">{note}</div>}

          {/* status line + event ticker */}
          {battle && (
            <div className="arena-subline mono">
              TICK {tick ?? '-'}
              {leader ? (
                <> · <span style={{ color: fighterColor(regIdOf(leader.id)) }}>
                  {fighterName(regIdOf(leader.id))}
                </span> ADV</>
              ) : null}
            </div>
          )}
          <div className="event-ticker" title="Live battle events">
            <div className="event-ticker-inner">
              {[...tickerLine, ...tickerLine].map((t, i) => (
                <span className="ticker-item mono" key={i}>
                  {t}
                </span>
              ))}
            </div>
          </div>

          {/* stat cards */}
          <div className="stat-cards">
            <div className="stat-card">
              <span className="stat-label">Sol pool</span>
              <span className="stat-value mono">{fmtSol(poolTotal)} SOL</span>
              <span className="stat-sub mono">
                {poolDelta > 0 ? `+${fmtSol(poolDelta)} this battle` : 'no bets yet'}
              </span>
            </div>
            <div className="stat-card">
              <span className="stat-label">Winner odds</span>
              {favEntry && favOdds > 0 ? (
                <>
                  <span className="stat-value mono">
                    {favOdds.toFixed(2)}{' '}
                    <span className="stat-name">
                      {fighterName(regIdOf(favEntry[0]))}
                    </span>
                  </span>
                  <span className="stat-sub mono">
                    {battle?.fighter_ids
                      .filter((fid) => fid !== favEntry[0])
                      .map((fid) => {
                        const p = pool?.pools[fid] || 0;
                        const o = p > 0 && poolTotal > 0 ? poolTotal / p : 0;
                        return `${fighterName(regIdOf(fid))} ${o > 0 ? o.toFixed(2) : '-'}`;
                      })
                      .join(' · ')}
                  </span>
                </>
              ) : (
                <>
                  <span className="stat-value mono">-</span>
                  <span className="stat-sub">no odds yet</span>
                </>
              )}
            </div>
            <div className="stat-card">
              <span className="stat-label">Fighters</span>
              <span className="stat-value mono">
                {nFighters > 0 ? nFighters : '-'}
              </span>
              <span className="stat-sub">{names || 'no battle selected'}</span>
            </div>
            <div className="stat-card">
              <span className="stat-label">Ticks</span>
              <span className="stat-value mono">{tick ?? '-'}</span>
              <span className="stat-sub">
                {battle
                  ? battle.exhibition
                    ? 'exhibition'
                    : live && !done
                      ? 'streaming'
                      : done
                        ? 'finished'
                        : 'replay'
                  : 'no battle selected'}
              </span>
            </div>
            {(settings?.token_mint || '').trim() && (
              <div className="stat-card" style={{ minWidth: 220 }}>
                <span className="stat-label">Token CA</span>
                <span className="stat-value mono" style={{ fontSize: 11, wordBreak: 'break-all', lineHeight: 1.5, whiteSpace: 'normal', overflow: 'visible', textOverflow: 'clip' }}>
                  {(settings?.token_mint || '').trim()}
                </span>
                <span className="stat-sub mono">
                  <button
                    className="table-action"
                    style={{ padding: '2px 8px', fontSize: 10 }}
                    onClick={() => { void navigator.clipboard.writeText((settings?.token_mint || '').trim()); }}
                  >
                    COPY
                  </button>
                </span>
              </div>
            )}
          </div>

          <div className="panel">
            <h3>Betting pool</h3>
            <BettingPanel battle={battle} done={done} />
          </div>
        </div>

        {/* ---------------- RIGHT ---------------- */}
        <div className="col-stack">
          <RecentEliminations battles={battles} />
          <ChatPanel battleId={battleId || null} wsChat={wsChat} />
        </div>
      </div>

      {/* ---------------- COMBAT LOG (full width) ---------------- */}
      <div className="panel combat-log-panel">
        <div className="combat-log-head">
          <h3 style={{ margin: 0 }}>Combat log</h3>
          {live && !done && battle && (
            <span className="streaming">
              <span className="live-dot" /> STREAMING
            </span>
          )}
          <div className="latest-box">
            <span className="latest-label">Latest</span>
            {elims.length ? (
              elims.slice(-3).map((e, i) => (
                <span className="latest-elim mono" key={i}>
                  {e.killer
                    ? `${fighterName(regIdOf(e.killer))} DEFEATED ${fighterName(regIdOf(e.victim))}`
                    : `${fighterName(regIdOf(e.victim))} ELIMINATED`}
                </span>
              ))
            ) : (
              <span className="muted small">no eliminations yet</span>
            )}
          </div>
        </div>
        <div className="combat-log-rows">
          {combatLog.length ? (
            [...combatLog].slice(-9).reverse().map((r) => (
              <div className="clog-row" key={r.id}>
                <span className="mono muted clog-clock">{r.clock}</span>
                <span className="clog-text">{r.text}</span>
                <span className={`clog-tag ${r.kind}`}>{r.tag}</span>
              </div>
            ))
          ) : (
            <p className="muted small">Events appear here as the fight unfolds.</p>
          )}
        </div>
      </div>

    </div>
  );
}
