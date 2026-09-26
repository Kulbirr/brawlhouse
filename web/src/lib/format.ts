/* Formatting + fighter identity helpers. */

export const regIdOf = (engineId: string): string => String(engineId).split('#')[0];

export const fmtSol = (n: number | null | undefined): string =>
  String(Math.round(Number(n || 0) * 10000) / 10000);

export const fmtTime = (iso: string | null | undefined): string => {
  if (!iso) return '';
  const d = new Date(iso);
  return isNaN(d.getTime()) ? String(iso) : d.toLocaleString();
};

export const shortAddr = (a: string | null | undefined, n = 4): string => {
  const s = String(a || '');
  return s.length > n * 2 + 3 ? `${s.slice(0, n)}…${s.slice(-n)}` : s;
};

/** "12m ago" style relative time for an ISO timestamp. */
export const timeAgo = (iso: string | null | undefined): string => {
  if (!iso) return '';
  const t = new Date(iso).getTime();
  if (isNaN(t)) return '';
  const s = Math.max(0, Math.floor((Date.now() - t) / 1000));
  if (s < 60) return `${s}s ago`;
  const m = Math.floor(s / 60);
  if (m < 60) return `${m}m ago`;
  const h = Math.floor(m / 60);
  if (h < 24) return `${h}h ago`;
  return `${Math.floor(h / 24)}d ago`;
};

/** "04:37" elapsed mm:ss for an ISO timestamp. */
export const elapsedStr = (iso: string | null | undefined): string => {
  if (!iso) return '';
  const t = new Date(iso).getTime();
  if (isNaN(t)) return '';
  const s = Math.max(0, Math.floor((Date.now() - t) / 1000));
  const mm = String(Math.floor(s / 60)).padStart(2, '0');
  const ss = String(s % 60).padStart(2, '0');
  return `${mm}:${ss}`;
};

/* Deterministic per-fighter color (FNV-1a hash -> hue). Same as the old UI. */
export function fighterColor(regId: string): string {
  let h = 2166136261 >>> 0;
  const s = String(regId);
  for (let i = 0; i < s.length; i++) {
    h ^= s.charCodeAt(i);
    h = Math.imul(h, 16777619);
  }
  return `hsl(${h % 360}, 85%, 58%)`;
}

export function winRate(f: { wins: number; losses: number; draws: number }): number | null {
  const total = f.wins + f.losses + f.draws;
  return total > 0 ? (f.wins / total) * 100 : null;
}

/** Avatar image path for a registry fighter id (public/img/*.png). */
export function avatarSrc(regId: string): string {
  return `/img/${regId}.png`;
}
