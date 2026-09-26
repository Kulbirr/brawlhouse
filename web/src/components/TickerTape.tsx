/* Scrolling results ticker: recent finished battles. */

import { useEffect, useState } from 'react';
import { Link } from 'react-router-dom';
import { api, type BattleSummary } from '../lib/api';
import { regIdOf } from '../lib/format';
import { useApp } from '../lib/store';

export default function TickerTape() {
  const { fighterName } = useApp();
  const [items, setItems] = useState<BattleSummary[]>([]);

  useEffect(() => {
    (async () => {
      try {
        const d = await api<{ battles: BattleSummary[] }>(
          '/api/battles?status=finished&limit=12',
        );
        setItems(d.battles || []);
      } catch {
        /* ticker is decorative */
      }
    })();
  }, []);

  if (!items.length) return null;

  const renderItems = () =>
    items.map((b) => {
      const names = b.fighter_ids.map((id) => fighterName(regIdOf(id))).join(' vs ');
      const winner = b.winner ? fighterName(regIdOf(b.winner)) : 'DRAW';
      return (
        <span className="ticker-item" key={b.id}>
          <Link to={`/arena/${b.id}`} style={{ textDecoration: 'none', color: 'inherit' }}>
            <b>{winner}</b>
            <span className="sep">//</span>
            {names}
            <span className="sep">//</span>
            {b.ticks ?? '?'} ticks
          </Link>
        </span>
      );
    });

  return (
    <div className="ticker-tape">
      <div className="ticker-inner">
        {renderItems()}
        {renderItems()}
      </div>
    </div>
  );
}
