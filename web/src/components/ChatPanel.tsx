/* Right column bottom: live battle chat. Sending requires a connected
 * wallet; messages are labeled by truncated wallet address. Live updates
 * arrive over the battle websocket; GET polling is the backup. */

import { useCallback, useEffect, useRef, useState } from 'react';
import { useWallet } from '@solana/wallet-adapter-react';
import { WalletMultiButton } from '@solana/wallet-adapter-react-ui';
import { api, ApiError, type ChatMessage } from '../lib/api';
import { shortAddr } from '../lib/format';

function mergeById(prev: ChatMessage[], next: ChatMessage[]): ChatMessage[] {
  const seen = new Set(prev.map((m) => m.id));
  const add = next.filter((m) => !seen.has(m.id));
  if (!add.length) return prev;
  return [...prev, ...add]
    .sort((a, b) => a.id - b.id)
    .slice(-120);
}

export default function ChatPanel({
  battleId,
  wsChat,
}: {
  battleId: string | null;
  wsChat: { msg: ChatMessage; seq: number } | null;
}) {
  const { connected, publicKey } = useWallet();
  const [messages, setMessages] = useState<ChatMessage[]>([]);
  const [input, setInput] = useState('');
  const [sending, setSending] = useState(false);
  const [err, setErr] = useState('');
  const feedRef = useRef<HTMLDivElement>(null);

  const load = useCallback(async () => {
    if (!battleId) {
      setMessages([]);
      return;
    }
    try {
      const d = await api<{ messages: ChatMessage[] }>(
        `/api/battles/${encodeURIComponent(battleId)}/chat`,
      );
      setMessages((prev) => mergeById(prev, d.messages || []));
    } catch {
      /* ignore */
    }
  }, [battleId]);

  /* fresh history on battle change + slow backup poll */
  useEffect(() => {
    setMessages([]);
    setErr('');
    load();
    if (!battleId) return;
    const t = setInterval(load, 15000);
    return () => clearInterval(t);
  }, [battleId, load]);

  /* live messages from the battle websocket */
  useEffect(() => {
    if (wsChat) {
      setMessages((prev) => mergeById(prev, [wsChat.msg]));
    }
  }, [wsChat]);

  /* keep the feed pinned to the bottom */
  useEffect(() => {
    const el = feedRef.current;
    if (el) el.scrollTop = el.scrollHeight;
  }, [messages]);

  const send = async () => {
    if (!battleId || !connected || !publicKey || sending) return;
    const text = input.trim();
    if (!text) return;
    setSending(true);
    setErr('');
    try {
      const row = await api<ChatMessage>(
        `/api/battles/${encodeURIComponent(battleId)}/chat`,
        {
          method: 'POST',
          body: JSON.stringify({
            wallet: publicKey.toBase58(),
            message: text,
          }),
        },
      );
      setMessages((prev) => mergeById(prev, [row]));
      setInput('');
    } catch (e) {
      if (e instanceof ApiError && e.status === 429) {
        setErr('Slow down. One message per 2 seconds.');
      } else {
        setErr(e instanceof ApiError ? e.message2 : 'Failed to send.');
      }
    } finally {
      setSending(false);
    }
  };

  return (
    <div className="panel">
      <h3>
        Live chat
        <span className="panel-count">{messages.length}</span>
      </h3>
      {!battleId ? (
        <p className="muted small">Select a battle to join its chat.</p>
      ) : (
        <>
          <div className="chat-feed" ref={feedRef}>
            {messages.length ? (
              messages.map((m) => (
                <div className="chat-msg" key={m.id}>
                  <span
                    className="chat-wallet mono"
                    title={m.wallet}
                  >
                    {shortAddr(m.wallet)}
                  </span>
                  <span className="chat-text">{m.message}</span>
                </div>
              ))
            ) : (
              <p className="muted small">No messages yet. Say something.</p>
            )}
          </div>
          {connected ? (
            <div className="chat-input-row">
              <input
                className="chat-input"
                type="text"
                maxLength={200}
                placeholder="Message as connected wallet…"
                value={input}
                onChange={(e) => setInput(e.target.value)}
                onKeyDown={(e) => {
                  if (e.key === 'Enter') void send();
                }}
                disabled={sending}
              />
              <button
                className="btn btn-small"
                onClick={() => void send()}
                disabled={sending || !input.trim()}
              >
                Send
              </button>
            </div>
          ) : (
            <div className="connect-gate">
              <p>Connect your wallet to chat.</p>
              <WalletMultiButton />
            </div>
          )}
          {err && (
            <p className="small" style={{ color: 'var(--red)', marginTop: 8 }}>
              {err}
            </p>
          )}
        </>
      )}
    </div>
  );
}
