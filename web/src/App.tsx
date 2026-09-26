import { useMemo } from 'react';
import { HashRouter, Routes, Route, Navigate } from 'react-router-dom';
import { ConnectionProvider, WalletProvider } from '@solana/wallet-adapter-react';
import { WalletModalProvider } from '@solana/wallet-adapter-react-ui';
import { PhantomWalletAdapter, SolflareWalletAdapter } from '@solana/wallet-adapter-wallets';
import '@solana/wallet-adapter-react-ui/styles.css';

import { AppProvider, useApp } from './lib/store';
import { SOLANA_RPC_URL } from './lib/payments';
import Layout from './components/Layout';
import ArenaPage from './pages/ArenaPage';
import BornPage from './pages/BornPage';
import MyFightersPage from './pages/MyFightersPage';
import QueuePage from './pages/QueuePage';
import SeasonPage from './pages/SeasonPage';
import FightersPage from './pages/FightersPage';
import LeaderboardPage from './pages/LeaderboardPage';
import BattlesPage from './pages/BattlesPage';
import TreasuryPage from './pages/TreasuryPage';
import AdminPage from './pages/AdminPage';

function BootError() {
  const { settingsError } = useApp();
  if (!settingsError) return null;
  return (
    <div className="page">
      <div className="panel" style={{ borderColor: 'var(--red)' }}>
        <h3 style={{ color: 'var(--red)' }}>Backend unreachable</h3>
        <p className="muted small">
          Could not reach the API — is the backend running? ({settingsError})
        </p>
      </div>
    </div>
  );
}

export default function App() {
  const wallets = useMemo(
    () => [new PhantomWalletAdapter(), new SolflareWalletAdapter()],
    [],
  );

  return (
    <ConnectionProvider endpoint={SOLANA_RPC_URL}>
      <WalletProvider wallets={wallets} autoConnect>
        <WalletModalProvider>
          <AppProvider>
            <HashRouter>
              <Layout>
                <BootError />
                <Routes>
                  <Route path="/" element={<Navigate to="/arena" replace />} />
                  <Route path="/arena" element={<ArenaPage />} />
                  <Route path="/arena/:battleId" element={<ArenaPage />} />
                  <Route path="/born" element={<BornPage />} />
                  <Route path="/my-fighters" element={<MyFightersPage />} />
                  <Route path="/queue" element={<QueuePage />} />
                  <Route path="/season" element={<SeasonPage />} />
                  <Route path="/fighters" element={<FightersPage />} />
                  <Route path="/leaderboard" element={<LeaderboardPage />} />
                  <Route path="/battles" element={<BattlesPage />} />
                  <Route path="/treasury" element={<TreasuryPage />} />
                  <Route path="/admin" element={<AdminPage />} />
                  <Route path="*" element={<Navigate to="/arena" replace />} />
                </Routes>
              </Layout>
            </HashRouter>
          </AppProvider>
        </WalletModalProvider>
      </WalletProvider>
    </ConnectionProvider>
  );
}
