/* Real-SOL payment flow: unsigned-tx initiate + wallet sign + onchain confirm.
 *
 * LIVE HIRE
 *   POST /api/battles/{id}/hire        -> { hire_id, payment_status: "pending",
 *                                            transaction_base64, wallet,
 *                                            treasury_wallet, amount_sol }
 *   (wallet signs + sends the tx)      ->
 *   POST /api/battles/{id}/hire/confirm { hire_id, signature } -> hire row
 *
 * LIVE BET: same pattern with bet_id:
 *   POST /api/battles/{id}/bets        -> { bet_id, payment_status: "pending",
 *                                            transaction_base64, ... }
 *   POST /api/battles/{id}/bets/confirm { bet_id, signature } -> bet row
 *
 * MOCK MODE (hiring_live / betting_live off): the initiate endpoints return
 * the hire/bet row directly with NO transaction_base64. Live vs mock is
 * detected by the presence of transaction_base64 in the initiate response.
 *
 * The server never sees a private key. Verification happens onchain via the
 * backend's own RPC; the frontend only needs an RPC endpoint to broadcast
 * the user-signed transaction. Override with VITE_SOLANA_RPC_URL if needed.
 */

import { Buffer } from 'buffer';
import { Connection, Transaction } from '@solana/web3.js';
import { api, ApiError, type BetRow } from './api';

export const SOLANA_RPC_URL =
  import.meta.env.VITE_SOLANA_RPC_URL || 'https://api.mainnet-beta.solana.com';

let _connection: Connection | null = null;
export function getConnection(): Connection {
  if (!_connection) _connection = new Connection(SOLANA_RPC_URL, 'confirmed');
  return _connection;
}

export type SignTransactionFn = (tx: Transaction) => Promise<Transaction>;

/** Payment lifecycle shown in the UI. */
export type PaymentPhase =
  | 'idle'
  | 'initiating'
  | 'awaiting-signature' // wallet popup is open
  | 'broadcasting'
  | 'confirming' // "verifying onchain…"
  | 'done'
  | 'error';

export interface PaymentStatus {
  phase: PaymentPhase;
  message: string; // user-facing line for the current phase
}

export const PHASE_MESSAGE: Record<PaymentPhase, string> = {
  idle: '',
  initiating: 'Preparing payment…',
  'awaiting-signature': 'Waiting for signature. Approve the transfer in your wallet…',
  broadcasting: 'Sending transaction…',
  confirming: 'Verifying onchain…',
  done: 'Confirmed onchain.',
  error: '',
};

/** Live vs mock detection: live initiate responses carry transaction_base64. */
export function isLiveInitiate(res: unknown): res is {
  transaction_base64: string;
  [key: string]: unknown;
} {
  return !!res && typeof (res as Record<string, unknown>).transaction_base64 === 'string';
}

function friendlyError(e: unknown): string {
  if (e instanceof ApiError) {
    if (e.status === 503) return `Payments unavailable: ${e.message2}`;
    if (e.status === 409) return e.message2; // replayed / already paid
    return e.message2;
  }
  const msg = e instanceof Error ? e.message : String(e);
  // wallet-adapter / Phantom user rejection
  if (/rejected|denied|cancelled|user declined/i.test(msg)) {
    return 'Signature rejected in wallet. No SOL moved.';
  }
  return msg || 'Payment failed.';
}

/**
 * Sign a base64 unsigned transaction with the connected wallet and broadcast it.
 * Returns the transaction signature.
 */
export async function signAndSend(
  signTransaction: SignTransactionFn,
  transactionBase64: string,
  connection?: Connection,
): Promise<string> {
  const conn = connection || getConnection();
  const tx = Transaction.from(Buffer.from(transactionBase64, 'base64'));
  const signed = await signTransaction(tx);
  const raw = signed.serialize();
  const signature = await conn.sendRawTransaction(raw, {
    skipPreflight: false,
    preflightCommitment: 'confirmed',
  });
  return signature;
}

export interface LivePaymentResult {
  live: boolean;
  signature: string | null;
  bet?: BetRow;
}

export interface PaymentHooks {
  setPhase: (phase: PaymentPhase, message?: string) => void;
}

/**
 * Full live bet flow: initiate -> sign -> broadcast -> confirm.
 * Mock mode returns the bet row directly (no wallet interaction).
 */
export async function payForBet(
  battleId: string,
  fighterId: string,
  walletAddress: string,
  amountSol: number,
  signTransaction: SignTransactionFn,
  hooks: PaymentHooks,
): Promise<LivePaymentResult> {
  hooks.setPhase('initiating');
  let init: Record<string, unknown>;
  try {
    init = await api<Record<string, unknown>>(
      `/api/battles/${encodeURIComponent(battleId)}/bets`,
      {
        method: 'POST',
        body: JSON.stringify({ fighter_id: fighterId, wallet: walletAddress, amount_sol: amountSol }),
      },
    );
  } catch (e) {
    hooks.setPhase('error', friendlyError(e));
    throw e;
  }

  if (!isLiveInitiate(init)) {
    hooks.setPhase('done');
    return { live: false, signature: null, bet: init as unknown as BetRow };
  }

  const betId = init.bet_id as number;
  hooks.setPhase('awaiting-signature');
  let signature: string;
  try {
    signature = await signAndSend(signTransaction, init.transaction_base64);
  } catch (e) {
    hooks.setPhase('error', friendlyError(e));
    throw new Error(friendlyError(e));
  }

  hooks.setPhase('confirming');
  try {
    const row = await api<BetRow>(
      `/api/battles/${encodeURIComponent(battleId)}/bets/confirm`,
      { method: 'POST', body: JSON.stringify({ bet_id: betId, signature }) },
    );
    hooks.setPhase('done');
    return { live: true, signature, bet: row };
  } catch (e) {
    hooks.setPhase('error', friendlyError(e));
    throw new Error(friendlyError(e));
  }
}

/* ------------------------------------------------------------------ FT */

/**
 * Full born flow: initiate -> sign -> broadcast -> confirm.
 * Mock mode (born_live off) returns the born FT directly: the initiate
 * response carries payment="mock" and the fighter, no wallet interaction.
 */
export async function bornFighter(
  walletAddress: string,
  name: string,
  archetype: string,
  color: string,
  signTransaction: SignTransactionFn,
  hooks: PaymentHooks,
): Promise<{ live: boolean; signature: string | null; fighter?: import('./api').FtFighter; result: import('./api').BornResult }> {
  hooks.setPhase('initiating');
  let init: import('./api').BornResult;
  try {
    init = await api<import('./api').BornResult>('/api/ft/born/initiate', {
      method: 'POST',
      body: JSON.stringify({ wallet: walletAddress, name, archetype, color }),
    });
  } catch (e) {
    hooks.setPhase('error', friendlyError(e));
    throw e;
  }

  if (init.payment === 'mock' && init.fighter) {
    hooks.setPhase('done');
    return { live: false, signature: null, fighter: init.fighter, result: init };
  }

  const intentId = init.intent_id as number;
  hooks.setPhase('awaiting-signature');
  let signature: string;
  try {
    signature = await signAndSend(signTransaction, init.transaction_base64 as string);
  } catch (e) {
    hooks.setPhase('error', friendlyError(e));
    throw new Error(friendlyError(e));
  }

  hooks.setPhase('confirming');
  try {
    const done = await api<import('./api').BornResult>('/api/ft/born/confirm', {
      method: 'POST',
      body: JSON.stringify({ intent_id: intentId, signature }),
    });
    hooks.setPhase('done');
    return { live: true, signature, fighter: done.fighter, result: done };
  } catch (e) {
    hooks.setPhase('error', friendlyError(e));
    throw new Error(friendlyError(e));
  }
}

/**
 * Full queue-entry flow: initiate -> sign -> broadcast -> confirm.
 * Mock mode (entry_live off) queues the fighter directly.
 */
export async function enterBattleQueue(
  fighterId: string,
  walletAddress: string,
  signTransaction: SignTransactionFn,
  hooks: PaymentHooks,
): Promise<{ live: boolean; signature: string | null }> {
  hooks.setPhase('initiating');
  let init: Record<string, unknown>;
  try {
    init = await api<Record<string, unknown>>('/api/ft/queue/enter', {
      method: 'POST',
      body: JSON.stringify({ fighter_id: fighterId, wallet: walletAddress }),
    });
  } catch (e) {
    hooks.setPhase('error', friendlyError(e));
    throw e;
  }

  if (!isLiveInitiate(init)) {
    hooks.setPhase('done');
    return { live: false, signature: null };
  }

  const entryId = init.entry_id as number;
  hooks.setPhase('awaiting-signature');
  let signature: string;
  try {
    signature = await signAndSend(signTransaction, init.transaction_base64);
  } catch (e) {
    hooks.setPhase('error', friendlyError(e));
    throw new Error(friendlyError(e));
  }

  hooks.setPhase('confirming');
  try {
    await api('/api/ft/queue/confirm', {
      method: 'POST',
      body: JSON.stringify({ entry_id: entryId, signature }),
    });
    hooks.setPhase('done');
    return { live: true, signature };
  } catch (e) {
    hooks.setPhase('error', friendlyError(e));
    throw new Error(friendlyError(e));
  }
}
