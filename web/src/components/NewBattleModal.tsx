/* New battle modal: fighter checkboxes, seed, exhibition flag. */

import { useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { api, type BattleSummary } from '../lib/api';
import { useApp } from '../lib/store';

export default function NewBattleModal({ onClose }: { onClose: () => void }) {
  const { fighters } = useApp();
  const navigate = useNavigate();
  const [checked, setChecked] = useState<Set<string>>(
    () => new Set(fighters.map((f) => f.id)),
  );
  const [seed, setSeed] = useState('');
  const [exhibition, setExhibition] = useState(false);
  const [error, setError] = useState('');
  const [busy, setBusy] = useState(false);

  const toggle = (id: string) => {
    setChecked((prev) => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });
  };

  const start = async () => {
    const ids = [...checked];
    if (ids.length < 2) { setError('Pick at least 2 fighters.'); return; }
    if (ids.length > 8) { setError('Pick at most 8 fighters.'); return; }
    setBusy(true);
    setError('');
    try {
      const body: Record<string, unknown> = { fighter_ids: ids, exhibition };
      if (seed.trim() !== '') body.seed = Number(seed);
      const b = await api<BattleSummary>('/api/battles', {
        method: 'POST',
        body: JSON.stringify(body),
      });
      onClose();
      navigate(`/arena/${b.id}`);
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Could not start battle.');
      setBusy(false);
    }
  };

  return (
    <div className="modal-backdrop" onClick={(e) => e.target === e.currentTarget && onClose()}>
      <div className="modal" role="dialog" aria-modal="true">
        <h2>
          New <span className="accent">battle</span>
        </h2>
        <div>
          {fighters.map((f) => (
            <label className="checkbox-row" key={f.id}>
              <input
                type="checkbox"
                checked={checked.has(f.id)}
                onChange={() => toggle(f.id)}
              />
              {f.name}
            </label>
          ))}
        </div>
        <label className="field">
          <span>Seed (optional, blank = random)</span>
          <input
            type="number"
            placeholder="random"
            value={seed}
            onChange={(e) => setSeed(e.target.value)}
          />
        </label>
        <label className="checkbox-row">
          <input
            type="checkbox"
            checked={exhibition}
            onChange={(e) => setExhibition(e.target.checked)}
          />
          Exhibition (demo fight, no betting)
        </label>
        {error && <p style={{ color: 'var(--red)', fontSize: 13 }}>{error}</p>}
        <div className="btn-row">
          <button className="btn btn-small btn-ghost" onClick={onClose}>
            Cancel
          </button>
          <button className="btn btn-small" onClick={start} disabled={busy}>
            {busy ? 'Starting…' : 'Start battle'}
          </button>
        </div>
      </div>
    </div>
  );
}
