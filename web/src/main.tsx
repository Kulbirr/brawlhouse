import { StrictMode } from 'react';
import { createRoot } from 'react-dom/client';
import { Buffer } from 'buffer';
import './index.css';
import './sections.css';
import App from './App.tsx';

// @solana/web3.js (v1) expects the node Buffer global.
if (!(window as unknown as { Buffer: unknown }).Buffer) {
  (window as unknown as { Buffer: unknown }).Buffer = Buffer;
}

createRoot(document.getElementById('root')!).render(
  <StrictMode>
    <App />
  </StrictMode>,
);
