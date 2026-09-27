/* Bell icon in the header with unread count; dropdown shows the
   player's notification feed (hire results, fighter results). Polls
   every 10s. Opening the panel marks everything read. */

import { useCallback, useEffect, useRef, useState } from 'react';
import { useWallet } from '@solana/wallet-adapter-react';
import { api, type NotificationRow } from '../lib/api';

function timeAgo(iso: string): string {
  const s = Math.max(0, (Date.now() - new Date(iso).getTime()) / 1000);
  if (s < 60) return `${Math.floor(s)}s ago`;
  if (s < 3600) return `${Math.floor(s / 60)}m ago`;
  if (s < 86400) return `${Math.floor(s / 3600)}h ago`;
  return `${Math.floor(s / 86400)}d ago`;
}

export default function NotificationBell() {
  const { publicKey } = useWallet();
  const wallet = publicKey?.toBase58() ?? '';
  const [notes, setNotes] = useState<NotificationRow[]>([]);
  const [unread, setUnread] = useState(0);
  const [open, setOpen] = useState(false);
  const ref = useRef<HTMLDivElement>(null);

  const load = useCallback(async () => {
    if (!wallet) {
      setNotes([]);
      setUnread(0);
      return;
    }
    try {
      const d = await api<{
        notifications: NotificationRow[];
        unread: number;
      }>(`/api/notifications?wallet=${encodeURIComponent(wallet)}`);
      setNotes(d.notifications || []);
      setUnread(d.unread || 0);
    } catch {
      /* keep stale */
    }
  }, [wallet]);

  useEffect(() => {
    load();
    const t = setInterval(load, 10000);
    return () => clearInterval(t);
  }, [load]);

  // Close on outside click.
  useEffect(() => {
    if (!open) return;
    const onDown = (e: MouseEvent) => {
      if (ref.current && !ref.current.contains(e.target as Node)) setOpen(false);
    };
    document.addEventListener('mousedown', onDown);
    return () => document.removeEventListener('mousedown', onDown);
  }, [open ]);

  async function toggle() {
    const next = !open;
    setOpen(next);
    if (next && wallet && unread > 0) {
      try {
        await api('/api/notifications/read', {
          method: 'POST',
          body: JSON.stringify({ wallet }),
        });
        setUnread(0);
        setNotes((ns) =>
          ns.map((n) => (n.read_at ? n : { ...n, read_at: new Date().toISOString() })),
        );
      } catch {
        /* keep stale */
      }
    }
  }

  if (!wallet) return null;

  return (
    <div ref={ref} style={{ position: 'relative' }}>
      <button
        onClick={toggle}
        aria-label="Notifications"
        title="Notifications"
        style={{
          position: 'relative',
          background: 'none',
          border: '1px solid var(--line)',
          borderRadius: 8,
          padding: '6px 10px',
          cursor: 'pointer',
          color: 'var(--text)',
          fontSize: 16,
        }}
      >
        🔔
        {unread > 0 && (
          <span
            style={{
              position: 'absolute',
              top: -6,
              right: -6,
              background: '#ff3b5c',
              color: '#fff',
              borderRadius: 10,
              fontSize: 10,
              minWidth: 18,
              height: 18,
              display: 'flex',
              alignItems: 'center',
              justifyContent: 'center',
              padding: '0 4px',
              fontWeight: 700,
            }}
          >
            {unread > 99 ? '99+' : unread}
          </span>
        )}
      </button>
      {open && (
        <div
          style={{
            position: 'absolute',
            right: 0,
            top: 'calc(100% + 8px)',
            width: 340,
            maxHeight: 420,
            overflowY: 'auto',
            background: 'var(--panel)',
            border: '1px solid var(--line)',
            borderRadius: 12,
            zIndex: 100,
            boxShadow: '0 12px 32px rgba(0,0,0,0.5)',
          }}
        >
          <div
            style={{
              padding: '12px 16px',
              borderBottom: '1px solid var(--line)',
              fontWeight: 700,
              fontSize: 13,
            }}
          >
            NOTIFICATIONS
          </div>
          {notes.length === 0 ? (
            <p className="muted small" style={{ padding: '16px' }}>
              Nothing yet. Hire a bot or enter a fighter and results will land
              here.
            </p>
          ) : (
            notes.map((n) => (
              <div
                key={n.id}
                style={{
                  padding: '10px 16px',
                  borderBottom: '1px solid var(--line)',
                  opacity: n.read_at ? 0.65 : 1,
                }}
              >
                <div style={{ fontSize: 13, fontWeight: 600 }}>{n.title}</div>
                <div className="muted small" style={{ marginTop: 2 }}>
                  {n.message}
                </div>
                <div
                  className="muted small"
                  style={{ marginTop: 4, fontSize: 11 }}
                >
                  {timeAgo(n.created_at)}
                </div>
              </div>
            ))
          )}
        </div>
      )}
    </div>
  );
}
