/* Canvas combat renderer: interpolated snapshots + juice.
 *
 * Juice: glowing projectile trails, muzzle flashes, hit sparks, floating
 * damage numbers, explosion bursts + shockwave rings + screen shake on
 * elimination, animated pulsing neon grid, per-fighter glow. Particles
 * are capped for performance.
 */

import type { WsFighter, WsSnapshot } from '../lib/api';
import { regIdOf, fighterColor } from '../lib/format';

const LOGICAL = 760;
const MAX_PARTICLES = 700;

interface Particle {
  x: number; y: number;
  vx: number; vy: number;
  born: number; life: number;
  color: string; size: number;
  ring?: boolean; // shockwave ring instead of dot
}

interface FloatText {
  x: number; y: number;
  text: string;
  born: number; life: number;
  color: string;
}

interface Trail {
  pts: { x: number; y: number }[];
}

export interface RendererEvents {
  onEliminate?: (f: WsFighter, tick: number) => void;
}

export class ArenaRenderer {
  private ctx: CanvasRenderingContext2D;
  private dpr = 1;
  private raf = 0;
  private lastT = 0;
  private events: RendererEvents;

  private prev: WsSnapshot | null = null;
  private cur: WsSnapshot | null = null;
  private prevT = 0;
  private curT = 0;
  private arenaSize = 1000;
  private hpMax = 100;

  private particles: Particle[] = [];
  private texts: FloatText[] = [];
  private trails = new Map<string, Trail>();
  private prevHp = new Map<string, number>();
  private dashPrev = new Map<string, number>();
  private shake = 0;

  // winner spotlight: registry id of the victor, gliding to arena center
  private spotId: string | null = null;
  private spotX: number | null = null;
  private spotY: number | null = null;

  /** Crown the winner: their bot glides to the middle of the arena under
   * a spotlight ring. Pass null to clear. */
  spotlight(id: string | null) {
    this.spotId = id ? regIdOf(id) : null;
    this.spotX = null;
    this.spotY = null;
  }

  constructor(canvas: HTMLCanvasElement, events?: RendererEvents) {
    this.events = events || {};
    const ctx = canvas.getContext('2d');
    if (!ctx) throw new Error('no 2d context');
    this.ctx = ctx;
    this.dpr = Math.min(2, window.devicePixelRatio || 1);
    canvas.width = LOGICAL * this.dpr;
    canvas.height = LOGICAL * this.dpr;
  }

  start() {
    const step = (now: number) => {
      this.raf = requestAnimationFrame(step);
      this.draw(now);
    };
    this.raf = requestAnimationFrame(step);
  }

  stop() {
    cancelAnimationFrame(this.raf);
  }

  reset() {
    this.prev = null; this.cur = null;
    this.prevT = 0; this.curT = 0;
    this.particles = [];
    this.texts = [];
    this.trails.clear();
    this.prevHp.clear();
    this.dashPrev.clear();
    this.arenaSize = 1000;
    this.hpMax = 100;
    this.shake = 0;
    this.spotId = null;
    this.spotX = null;
    this.spotY = null;
  }

  pushSnapshot(snap: WsSnapshot) {
    if (!snap || !Array.isArray(snap.fighters)) return;
    const now = performance.now();

    if (this.cur) {
      const prevById = new Map(this.cur.fighters.map((f) => [f.id, f]));
      for (const f of snap.fighters) {
        const p = prevById.get(f.id);
        // elimination -> explosion + shake + event
        if (p && p.alive && !f.alive) {
          this.explode(f);
          this.shake = Math.min(16, this.shake + 11);
          this.events.onEliminate?.(f, snap.tick);
        }
        // hp drop -> hit sparks + damage number
        const php = this.prevHp.get(f.id);
        if (php !== undefined && f.hp < php && f.alive) {
          const dmg = Math.round(php - f.hp);
          if (dmg > 0) {
            this.hitSparks(f, dmg);
            this.texts.push({
              x: f.x + (Math.random() - 0.5) * 20,
              y: f.y - 22,
              text: `-${dmg}`,
              born: now, life: 800,
              color: '#ff8fa3',
            });
          }
        }
        // dash -> streak particles
        const pd = this.dashPrev.get(f.id) || 0;
        if (f.dash_cooldown > pd) this.dashStreak(f);
        this.dashPrev.set(f.id, f.dash_cooldown);
      }
      // new projectiles -> muzzle flash
      const prevProj = new Set((this.cur.projectiles || []).map((p) => p.id));
      for (const p of snap.projectiles || []) {
        if (!prevProj.has(p.id) && typeof p.x === 'number') {
          this.muzzleFlash(p.x, p.y, p.owner);
        }
      }
    } else {
      for (const f of snap.fighters) {
        this.dashPrev.set(f.id, f.dash_cooldown);
      }
    }

    for (const f of snap.fighters) {
      this.prevHp.set(f.id, f.hp);
      if (typeof f.x === 'number') {
        this.arenaSize = Math.max(this.arenaSize, Math.abs(f.x), Math.abs(f.y));
      }
      if (typeof f.hp === 'number') this.hpMax = Math.max(this.hpMax, f.hp);
    }
    for (const p of snap.projectiles || []) {
      if (typeof p.x === 'number') {
        this.arenaSize = Math.max(this.arenaSize, Math.abs(p.x), Math.abs(p.y));
      }
      // maintain trail
      let t = this.trails.get(p.id);
      if (!t) { t = { pts: [] }; this.trails.set(p.id, t); }
      t.pts.push({ x: p.x, y: p.y });
      if (t.pts.length > 7) t.pts.shift();
    }
    // drop trails for dead projectiles
    const liveIds = new Set((snap.projectiles || []).map((p) => p.id));
    for (const id of this.trails.keys()) {
      if (!liveIds.has(id)) this.trails.delete(id);
    }

    this.prev = this.cur;
    this.prevT = this.curT;
    this.cur = snap;
    this.curT = now;
  }

  /* ---------------- particles ---------------- */

  private addParticle(p: Particle) {
    if (this.particles.length >= MAX_PARTICLES) this.particles.shift();
    this.particles.push(p);
  }

  private explode(f: WsFighter) {
    const color = fighterColor(regIdOf(f.id));
    const now = performance.now();
    for (let i = 0; i < 42; i++) {
      const ang = Math.random() * Math.PI * 2;
      const sp = 60 + Math.random() * 260;
      this.addParticle({
        x: f.x, y: f.y,
        vx: Math.cos(ang) * sp, vy: Math.sin(ang) * sp,
        born: now, life: 500 + Math.random() * 600,
        color: i % 4 === 0 ? '#ffffff' : color,
        size: 2 + Math.random() * 4,
      });
    }
    // shockwave ring
    this.addParticle({
      x: f.x, y: f.y, vx: 0, vy: 0,
      born: now, life: 420, color, size: 6, ring: true,
    });
  }

  private hitSparks(f: WsFighter, dmg: number) {
    const color = fighterColor(regIdOf(f.id));
    const now = performance.now();
    const n = Math.min(14, 4 + Math.floor(dmg / 3));
    for (let i = 0; i < n; i++) {
      const ang = Math.random() * Math.PI * 2;
      const sp = 40 + Math.random() * 160;
      this.addParticle({
        x: f.x + (Math.random() - 0.5) * 14,
        y: f.y + (Math.random() - 0.5) * 14,
        vx: Math.cos(ang) * sp, vy: Math.sin(ang) * sp,
        born: now, life: 220 + Math.random() * 200,
        color: i % 3 === 0 ? '#ffffff' : color,
        size: 1.5 + Math.random() * 2.5,
      });
    }
  }

  private muzzleFlash(x: number, y: number, owner: string) {
    const color = fighterColor(regIdOf(owner));
    const now = performance.now();
    for (let i = 0; i < 5; i++) {
      this.addParticle({
        x: x + (Math.random() - 0.5) * 8,
        y: y + (Math.random() - 0.5) * 8,
        vx: (Math.random() - 0.5) * 60, vy: (Math.random() - 0.5) * 60,
        born: now, life: 120 + Math.random() * 80,
        color: i === 0 ? '#ffffff' : color,
        size: 2 + Math.random() * 2,
      });
    }
  }

  private dashStreak(f: WsFighter) {
    const now = performance.now();
    for (let i = 0; i < 8; i++) {
      this.addParticle({
        x: f.x + (Math.random() - 0.5) * 16,
        y: f.y + (Math.random() - 0.5) * 16,
        vx: (Math.random() - 0.5) * 40, vy: (Math.random() - 0.5) * 40,
        born: now, life: 220 + Math.random() * 120,
        color: '#9be7ff', size: 1.5 + Math.random() * 2,
      });
    }
  }

  /* ---------------- drawing ---------------- */

  private draw(now: number) {
    const ctx = this.ctx;
    const L = LOGICAL;
    ctx.setTransform(this.dpr, 0, 0, this.dpr, 0, 0);

    // screen shake
    if (this.shake > 0.3) {
      const sx = (Math.random() - 0.5) * this.shake;
      const sy = (Math.random() - 0.5) * this.shake;
      ctx.translate(sx, sy);
      this.shake *= 0.88;
    } else {
      this.shake = 0;
    }

    // background
    const bg = ctx.createRadialGradient(L / 2, L / 2, 60, L / 2, L / 2, L * 0.75);
    bg.addColorStop(0, '#0a0d13');
    bg.addColorStop(1, '#040508');
    ctx.fillStyle = '#040508';
    ctx.fillRect(-20, -20, L + 40, L + 40);
    ctx.fillStyle = bg;
    ctx.fillRect(-20, -20, L + 40, L + 40);

    const k = L / this.arenaSize;
    const X = (x: number) => x * k;
    const Y = (y: number) => y * k;
    this.drawGrid(ctx, X, Y, k, now);

    if (!this.cur) return;

    let a = 1;
    if (this.prev && this.curT > this.prevT) {
      a = Math.min(1, Math.max(0, (now - this.curT) / Math.max(1, this.curT - this.prevT)));
    }
    const lerp = (p: number, c: number) => p + (c - p) * a;

    const prevP = new Map((this.prev?.projectiles || []).map((p) => [p.id, p]));
    for (const p of this.cur.projectiles || []) {
      const q = prevP.get(p.id);
      const x = q ? lerp(q.x, p.x) : p.x;
      const y = q ? lerp(q.y, p.y) : p.y;
      this.drawProjectile(ctx, X, Y, x, y, p);
    }

    const prevF = new Map((this.prev?.fighters || []).map((f) => [f.id, f]));
    for (const f of this.cur.fighters) {
      const q = prevF.get(f.id);
      let fx = q ? lerp(q.x, f.x) : f.x;
      let fy = q ? lerp(q.y, f.y) : f.y;
      if (this.spotId && regIdOf(f.id) === this.spotId && f.alive) {
        // winner's walk: glide to the middle of the arena
        const cx = this.arenaSize / 2;
        const cy = this.arenaSize / 2;
        if (this.spotX === null || this.spotY === null) {
          this.spotX = fx;
          this.spotY = fy;
        }
        this.spotX += (cx - this.spotX) * 0.06;
        this.spotY += (cy - this.spotY) * 0.06;
        fx = this.spotX;
        fy = this.spotY;
      }
      this.drawFighter(ctx, X, Y, k, fx, fy, f);
    }

    // spotlight ring under the winner
    if (this.spotId && this.spotX !== null && this.spotY !== null) {
      const wf = this.cur.fighters.find((f) => regIdOf(f.id) === this.spotId);
      const color = wf ? fighterColor(regIdOf(wf.id)) : '#b6ff2e';
      const pulse = 0.5 + 0.5 * Math.sin(now / 280);
      const sx = X(this.spotX);
      const sy = Y(this.spotY);
      ctx.save();
      const glow = ctx.createRadialGradient(sx, sy, 4, sx, sy, 90 * k + 46 * pulse);
      glow.addColorStop(0, 'rgba(182,255,46,0.20)');
      glow.addColorStop(1, 'rgba(182,255,46,0)');
      ctx.fillStyle = glow;
      ctx.beginPath();
      ctx.arc(sx, sy, 90 * k + 46 * pulse, 0, Math.PI * 2);
      ctx.fill();
      ctx.strokeStyle = color;
      ctx.lineWidth = 3;
      ctx.shadowColor = color;
      ctx.shadowBlur = 16 + 14 * pulse;
      ctx.beginPath();
      ctx.arc(sx, sy, 34 * k + 10 * pulse, 0, Math.PI * 2);
      ctx.stroke();
      ctx.restore();
    }

    this.updateParticles(ctx, X, Y, now);
    this.drawTexts(ctx, X, Y, now);
  }

  private drawGrid(
    ctx: CanvasRenderingContext2D,
    X: (x: number) => number, Y: (y: number) => number,
    k: number, now: number,
  ) {
    // arena boundary — neon lime
    ctx.strokeStyle = 'rgba(182, 255, 46, 0.5)';
    ctx.lineWidth = 2;
    ctx.shadowColor = '#b6ff2e';
    ctx.shadowBlur = 12;
    ctx.strokeRect(X(0), Y(0), this.arenaSize * k, this.arenaSize * k);
    ctx.shadowBlur = 0;
    // pulsing inner grid
    const pulse = 0.05 + 0.035 * (0.5 + 0.5 * Math.sin(now / 900));
    ctx.strokeStyle = `rgba(120, 160, 190, ${pulse.toFixed(3)})`;
    ctx.lineWidth = 1;
    ctx.beginPath();
    for (let g = 100; g < this.arenaSize; g += 100) {
      ctx.moveTo(X(g), Y(0)); ctx.lineTo(X(g), Y(this.arenaSize));
      ctx.moveTo(X(0), Y(g)); ctx.lineTo(X(this.arenaSize), Y(g));
    }
    ctx.stroke();
  }

  private drawProjectile(
    ctx: CanvasRenderingContext2D,
    X: (x: number) => number, Y: (y: number) => number,
    x: number, y: number,
    p: { id: string; vx: number; vy: number; owner: string },
  ) {
    if (typeof x !== 'number' || typeof y !== 'number') return;
    const color = fighterColor(regIdOf(p.owner));
    ctx.save();
    ctx.lineCap = 'round';
    // trail
    const trail = this.trails.get(p.id);
    if (trail && trail.pts.length > 1) {
      for (let i = 1; i < trail.pts.length; i++) {
        const t0 = trail.pts[i - 1], t1 = trail.pts[i];
        const frac = i / trail.pts.length;
        ctx.strokeStyle = color;
        ctx.globalAlpha = frac * 0.55;
        ctx.lineWidth = 1 + frac * 2.5;
        ctx.shadowColor = color;
        ctx.shadowBlur = 6;
        ctx.beginPath();
        ctx.moveTo(X(t0.x), Y(t0.y));
        ctx.lineTo(X(t1.x), Y(t1.y));
        ctx.stroke();
      }
      ctx.globalAlpha = 1;
    }
    // head
    ctx.strokeStyle = '#ffffff';
    ctx.lineWidth = 2.5;
    ctx.shadowColor = color;
    ctx.shadowBlur = 12;
    const tx = x - (p.vx || 0) * 0.035;
    const ty = y - (p.vy || 0) * 0.035;
    ctx.beginPath();
    ctx.moveTo(X(tx), Y(ty));
    ctx.lineTo(X(x), Y(y));
    ctx.stroke();
    ctx.fillStyle = '#ffffff';
    ctx.beginPath();
    ctx.arc(X(x), Y(y), 2.4, 0, Math.PI * 2);
    ctx.fill();
    ctx.restore();
  }

  private drawFighter(
    ctx: CanvasRenderingContext2D,
    X: (x: number) => number, Y: (y: number) => number,
    k: number, x: number, y: number, f: WsFighter,
  ) {
    if (!f.alive) return; // eliminated: the explosion told the story
    const reg = regIdOf(f.id);
    const color = fighterColor(reg);
    const r = Math.max(7, 13 * k);
    const cx = X(x), cy = Y(y);

    ctx.save();
    if (f.benched) ctx.globalAlpha = 0.45;
    ctx.shadowColor = color;
    ctx.shadowBlur = 18;
    ctx.fillStyle = f.benched ? '#6b7280' : color;
    ctx.beginPath();
    ctx.arc(cx, cy, r, 0, Math.PI * 2);
    ctx.fill();
    ctx.shadowBlur = 0;

    // inner core
    ctx.fillStyle = 'rgba(5,7,11,0.85)';
    ctx.beginPath();
    ctx.arc(cx, cy, r * 0.45, 0, Math.PI * 2);
    ctx.fill();

    // heading nose
    ctx.strokeStyle = 'rgba(5,7,11,0.9)';
    ctx.lineWidth = 3;
    ctx.beginPath();
    ctx.moveTo(cx, cy);
    ctx.lineTo(cx + Math.cos(f.heading || 0) * (r + 5), cy + Math.sin(f.heading || 0) * (r + 5));
    ctx.stroke();

    // shield ring + energy arc
    if (f.shield_active) {
      ctx.strokeStyle = '#4cc9f0';
      ctx.lineWidth = 2.5;
      ctx.shadowColor = '#4cc9f0';
      ctx.shadowBlur = 10;
      ctx.beginPath();
      ctx.arc(cx, cy, r + 6, 0, Math.PI * 2);
      ctx.stroke();
      ctx.shadowBlur = 0;
      const frac = Math.max(0, Math.min(1, (f.shield_energy || 0) / 100));
      ctx.strokeStyle = '#9be7ff';
      ctx.lineWidth = 3.5;
      ctx.beginPath();
      ctx.arc(cx, cy, r + 10, -Math.PI / 2, -Math.PI / 2 + frac * Math.PI * 2);
      ctx.stroke();
    }
    ctx.restore();

    // name label
    ctx.fillStyle = f.benched ? '#9aa3b5' : '#eef2f7';
    ctx.font = '600 12px "JetBrains Mono", monospace';
    ctx.textAlign = 'center';
    ctx.textBaseline = 'bottom';
    const label = (this.nameOf ? this.nameOf(reg) : reg) + (f.benched ? ' (benched)' : '');
    ctx.fillText(label, cx, cy - r - 18);

    // HP bar
    const w = 48, bx = cx - w / 2, by = cy - r - 14;
    const frac = Math.max(0, Math.min(1, f.hp / this.hpMax));
    ctx.fillStyle = 'rgba(0,0,0,0.65)';
    ctx.fillRect(bx - 1, by - 1, w + 2, 7);
    ctx.fillStyle = frac > 0.5 ? '#3ddc84' : frac > 0.25 ? '#ffb020' : '#ff3b5c';
    ctx.shadowColor = ctx.fillStyle as string;
    ctx.shadowBlur = 6;
    ctx.fillRect(bx, by, w * frac, 5);
    ctx.shadowBlur = 0;

    // kills badge
    if (f.kills > 0) {
      ctx.fillStyle = '#ffb020';
      ctx.font = '700 10px "JetBrains Mono", monospace';
      ctx.textBaseline = 'top';
      ctx.fillText(`K${f.kills}`, cx, cy + r + 5);
    }
  }

  /** Optional name resolver (registry id -> display name), set by the page. */
  nameOf: ((regId: string) => string) | null = null;

  private updateParticles(
    ctx: CanvasRenderingContext2D,
    X: (x: number) => number, Y: (y: number) => number,
    now: number,
  ) {
    const dt = Math.min(0.05, (now - (this.lastT || now)) / 1000);
    this.lastT = now;
    this.particles = this.particles.filter((p) => now - p.born < p.life);
    for (const p of this.particles) {
      p.x += p.vx * dt;
      p.y += p.vy * dt;
      p.vx *= 0.96; p.vy *= 0.96;
      const t = 1 - (now - p.born) / p.life;
      ctx.save();
      ctx.globalAlpha = Math.max(0, t);
      if (p.ring) {
        const rad = p.size + (1 - t) * 90;
        ctx.strokeStyle = p.color;
        ctx.lineWidth = 3 * t + 1;
        ctx.shadowColor = p.color;
        ctx.shadowBlur = 14;
        ctx.beginPath();
        ctx.arc(X(p.x), Y(p.y), rad * (760 / this.arenaSize) * 0.12 + 4, 0, Math.PI * 2);
        ctx.stroke();
      } else {
        ctx.fillStyle = p.color;
        ctx.shadowColor = p.color;
        ctx.shadowBlur = 8;
        ctx.beginPath();
        ctx.arc(X(p.x), Y(p.y), p.size, 0, Math.PI * 2);
        ctx.fill();
      }
      ctx.restore();
    }
  }

  private drawTexts(
    ctx: CanvasRenderingContext2D,
    X: (x: number) => number, Y: (y: number) => number,
    now: number,
  ) {
    this.texts = this.texts.filter((t) => now - t.born < t.life);
    for (const t of this.texts) {
      const frac = (now - t.born) / t.life;
      ctx.save();
      ctx.globalAlpha = Math.max(0, 1 - frac);
      ctx.fillStyle = t.color;
      ctx.font = '700 13px "JetBrains Mono", monospace';
      ctx.textAlign = 'center';
      ctx.shadowColor = t.color;
      ctx.shadowBlur = 8;
      ctx.fillText(t.text, X(t.x), Y(t.y) - frac * 26);
      ctx.restore();
    }
  }
}
