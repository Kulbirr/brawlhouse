/* Top navigation bar + ticker tape + footer shell.
   Header matches the arena-ui reference design (site-header / brand-lockup /
   main-nav / header-actions). Admin is intentionally absent from the nav:
   it is reachable only via direct URL. */

import { Link, NavLink } from 'react-router-dom';
import { WalletMultiButton } from '@solana/wallet-adapter-react-ui';
import { useApp } from '../lib/store';
import TickerTape from './TickerTape';
import NotificationBell from './NotificationBell';

const NAV = [
  { to: '/arena', label: 'Arena' },
  { to: '/born', label: 'Born' },
  { to: '/my-fighters', label: 'My Fighters' },
  { to: '/queue', label: 'Queue' },
  { to: '/season', label: 'Season' },
  { to: '/fighters', label: 'Fighters' },
  { to: '/leaderboard', label: 'Leaderboard' },
  { to: '/battles', label: 'Battles' },
  { to: '/treasury', label: 'Treasury' },
  { to: '/guide', label: 'Guide' },
];

export default function Layout({ children }: { children: React.ReactNode }) {
  const { settings } = useApp();
  const name = settings?.project_name || 'BRAWLHOUSE';
  const ticker = settings?.token_ticker || 'BRAWL';
  const live = Boolean(settings && (settings.hiring_live || settings.betting_live));
  const mint = (settings?.token_mint || '').trim();
  // "BRAWLHOUSE" renders fully bright (no dash tail)
  const dash = name.indexOf('-');
  const brandHead = dash > 0 ? name.slice(0, dash) : name;
  const brandTail = dash > 0 ? name.slice(dash) : '';

  return (
    <>
      <header className="site-header">
        <Link className="brand-lockup" to="/arena" aria-label={`${name} home`}>
          <span className="brand-mark">B</span>
          <span className="brand-name">
            {brandHead}
            <span>{brandTail}</span>
            <small>${ticker}</small>
          </span>
        </Link>
        <nav className="main-nav" aria-label="Main navigation">
          {NAV.map((item) => (
            <NavLink
              key={item.to}
              to={item.to}
              className={({ isActive }) => `nav-link${isActive ? ' is-active' : ''}`}
            >
              {item.label}
            </NavLink>
          ))}
        </nav>
        <div className="header-actions">
          {mint && (
            <button
              className="ca-chip mono"
              title={`Copy full CA: ${mint}`}
              onClick={() => { void navigator.clipboard.writeText(mint); }}
            >
              CA {mint.slice(0, 4)}...{mint.slice(-4)}
            </button>
          )}
          <span className={`preview-flag${live ? ' live-flag' : ''}`}>
            {live ? 'LIVE' : 'SIMULATION'}
          </span>
          <NotificationBell />
          <WalletMultiButton />
        </div>
      </header>
      <TickerTape />
      <main className="page">{children}</main>
      <footer className="footer">
        <span>{name}</span>
        <span>//</span>
        <span>
          fees fund <b>${ticker}</b> buybacks + burns
        </span>
      </footer>
    </>
  );
}
