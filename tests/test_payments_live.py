"""Live (real-SOL) payment flow tests.

Covers unsigned transaction construction, mocked-RPC verification
(success / wrong amount / wrong recipient / failed tx / replayed
signature), admin settings validation, and the end-to-end live
hire/bet flow via the API.

All chain interactions are local (throwaway solders keypairs) or a
faked RPC client - no network calls, no real money moves.
"""

import base64
import os
import tempfile
import time
import unittest
from contextlib import contextmanager
from types import SimpleNamespace
from unittest import mock

os.environ["ADMIN_TOKEN"] = "test-admin-token"
os.environ["ARENA_BETTING_WINDOW_SEC"] = "0"  # no betting-window wait in tests
os.environ["ARENA_PLAYBACK_TICK_MS"] = "0"  # no pacing in WS replays
os.environ["HELIUS_API_KEY"] = "test-helius-key"

from fastapi.testclient import TestClient  # noqa: E402

from backend import payments_live  # noqa: E402
from backend.app import create_app  # noqa: E402
from backend.db import Database  # noqa: E402
from backend.settings_store import SettingsStore  # noqa: E402

# NOTE: solders is imported lazily inside the tests that need it, never at
# module level: test_betting.py::test_solana_never_imported_in_mock asserts
# the chain libraries are absent from sys.modules in mock mode, and this
# module is collected before it runs.


def _keypair():
    from solders.keypair import Keypair  # noqa: E402

    return Keypair()

ADMIN_HEADERS = {"X-Admin-Token": "test-admin-token"}
SYSTEM_PROGRAM = "11111111111111111111111111111111"


@contextmanager
def temp_app():
    with tempfile.TemporaryDirectory() as tmp:
        yield create_app(data_dir=tmp)


@contextmanager
def betting_window(seconds=3):
    """Battles need a real 'open' window for bet/hire API tests.

    The module disables the window (ARENA_BETTING_WINDOW_SEC=0) for speed,
    but the API only accepts bets and hires while a battle is 'open', so
    placement tests opt back into a short window here. settings.get reads
    the env var on every call, so flipping it works even for an app that
    is already running."""
    key = "ARENA_BETTING_WINDOW_SEC"
    saved = os.environ.get(key)
    os.environ[key] = str(seconds)
    try:
        yield
    finally:
        if saved is None:
            os.environ.pop(key, None)
        else:
            os.environ[key] = saved


class StubSettings:
    """Minimal settings stand-in for payments_live unit tests."""

    def __init__(self, values):
        self._values = values

    def get(self, key):
        return self._values[key]


# --------------------------------------------------------------------------
# fakes


class FakeRpc:
    """Pretends to be a solana-py Client."""

    def __init__(self, tx=None, slot=900_000_000):
        self._tx = tx
        self._slot = slot

    def get_latest_blockhash(self):
        return SimpleNamespace(
            value=SimpleNamespace(
                blockhash="11111111111111111111111111111111"))

    def get_slot(self):
        return SimpleNamespace(value=self._slot)

    def get_transaction(self, sig, encoding=None,
                        max_supported_transaction_version=None):
        return SimpleNamespace(value=self._tx)


def fake_verified_tx(wallet, treasury, amount_lamports, slot=900_000_000,
                     err=None, extra_ixs=()):
    ixs = [SimpleNamespace(parsed={
        "type": "transfer",
        "info": {"source": wallet, "destination": treasury,
                 "lamports": amount_lamports}})]
    ixs.extend(extra_ixs)
    return SimpleNamespace(
        slot=slot,
        meta=SimpleNamespace(err=err, inner_instructions=[]),
        transaction=SimpleNamespace(
            message=SimpleNamespace(instructions=ixs)))


def throwaway_pubkey():
    return str(_keypair().pubkey())


def throwaway_sig():
    return str(_keypair().sign_message(b"confirm"))


def live_settings(tmp, treasury):
    return StubSettings({
        "treasury_wallet": treasury,
        "hiring_live": True,
        "betting_live": True,
        "hire_fee_sol": 0.10,
        "max_bet_sol": 10.0,
    })


# --------------------------------------------------------------------------
# pubkey validation


class PubkeyValidationTest(unittest.TestCase):
    def test_accepts_real_pubkeys(self):
        self.assertTrue(
            payments_live.is_valid_solana_pubkey(throwaway_pubkey()))
        # the system program id (32 zero bytes) is a valid pubkey
        self.assertTrue(
            payments_live.is_valid_solana_pubkey(SYSTEM_PROGRAM))

    def test_rejects_garbage(self):
        for bad in ("", None, "abc", "not a pubkey!!!",
                    "x" * 44,  # decodes to >32 bytes
                    123):
            self.assertFalse(
                payments_live.is_valid_solana_pubkey(bad), bad)


# --------------------------------------------------------------------------
# unsigned transaction construction


class UnsignedTxTest(unittest.TestCase):
    def setUp(self):
        self.user = throwaway_pubkey()
        self.treasury = throwaway_pubkey()
        self.built = payments_live.build_unsigned_transfer(
            self.user, self.treasury, 100_000_000, client=FakeRpc())

    def test_shape(self):
        self.assertEqual(
            sorted(self.built.keys()),
            ["amount_lamports", "blockhash", "from", "to",
             "unsigned_tx_base64"])
        self.assertEqual(self.built["amount_lamports"], 100_000_000)
        self.assertEqual(self.built["from"], self.user)
        self.assertEqual(self.built["to"], self.treasury)

    def test_is_a_system_transfer_from_user_to_treasury(self):
        from solders.transaction import Transaction  # noqa: E402

        raw = base64.b64decode(self.built["unsigned_tx_base64"])
        tx = Transaction.from_bytes(raw)
        msg = tx.message
        keys = msg.account_keys
        # fee payer is the user's wallet
        self.assertEqual(str(keys[0]), self.user)
        self.assertEqual(len(msg.instructions), 1)
        ix = msg.instructions[0]
        self.assertEqual(str(keys[ix.program_id_index]), SYSTEM_PROGRAM)
        # System transfer: u32 tag 2, then u64 lamports little-endian
        data = bytes(ix.data)
        self.assertEqual(int.from_bytes(data[0:4], "little"), 2)
        self.assertEqual(int.from_bytes(data[4:12], "little"), 100_000_000)
        self.assertEqual(str(keys[ix.accounts[0]]), self.user)
        self.assertEqual(str(keys[ix.accounts[1]]), self.treasury)
        self.assertEqual(str(msg.recent_blockhash), self.built["blockhash"])

    def test_server_signed_nothing(self):
        from solders.signature import Signature  # noqa: E402
        from solders.transaction import Transaction  # noqa: E402

        raw = base64.b64decode(self.built["unsigned_tx_base64"])
        tx = Transaction.from_bytes(raw)
        self.assertEqual(len(tx.signatures), 1)
        self.assertEqual(tx.signatures[0], Signature.default())

    def test_invalid_inputs_rejected(self):
        with self.assertRaises(ValueError):
            payments_live.build_unsigned_transfer(
                "nope", self.treasury, 1_000, client=FakeRpc())
        with self.assertRaises(ValueError):
            payments_live.build_unsigned_transfer(
                self.user, self.treasury, 0, client=FakeRpc())
        with self.assertRaises(ValueError):
            payments_live.build_unsigned_transfer(
                self.user, self.treasury, -5, client=FakeRpc())

    def test_rpc_failure_raises_rpc_error(self):
        class Boom(FakeRpc):
            def get_latest_blockhash(self):
                raise RuntimeError("down")

        with self.assertRaises(payments_live.RpcError):
            payments_live.build_unsigned_transfer(
                self.user, self.treasury, 1_000, client=Boom())


# --------------------------------------------------------------------------
# verification with a mocked RPC


class VerifyTransferTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.db = Database(os.path.join(self.tmp.name, "t.db"))
        self.user = throwaway_pubkey()
        self.treasury = throwaway_pubkey()
        self.settings = live_settings(self.tmp.name, self.treasury)
        self.sig = throwaway_sig()

    def _verify(self, tx, expected_lamports=100_000_000, sig=None,
                settings=None):
        client = FakeRpc(tx=tx)
        return payments_live.verify_transfer(
            self.db, settings or self.settings, kind="bet", ref_id=7,
            battle_id="b1", wallet=self.user,
            expected_lamports=expected_lamports,
            signature=sig or self.sig, client=client)

    def test_success(self):
        tx = fake_verified_tx(self.user, self.treasury, 100_000_000)
        out = self._verify(tx)
        self.assertEqual(out["signature"], self.sig)
        self.assertEqual(out["lamports"], 100_000_000)
        self.assertTrue(self.db.tx_claimed(self.sig))

    def test_overpay_accepted(self):
        tx = fake_verified_tx(self.user, self.treasury, 200_000_000)
        out = self._verify(tx)
        self.assertEqual(out["lamports"], 200_000_000)

    def test_wrong_amount_rejected(self):
        tx = fake_verified_tx(self.user, self.treasury, 50_000_000)
        with self.assertRaises(ValueError):
            self._verify(tx)
        self.assertFalse(self.db.tx_claimed(self.sig))

    def test_wrong_recipient_rejected(self):
        tx = fake_verified_tx(self.user, throwaway_pubkey(), 100_000_000)
        with self.assertRaises(ValueError):
            self._verify(tx)

    def test_wrong_source_rejected(self):
        tx = fake_verified_tx(throwaway_pubkey(), self.treasury, 100_000_000)
        with self.assertRaises(ValueError):
            self._verify(tx)

    def test_failed_tx_rejected(self):
        tx = fake_verified_tx(self.user, self.treasury, 100_000_000,
                              err={"InstructionError": [0, "Custom"]})
        with self.assertRaises(ValueError):
            self._verify(tx)

    def test_missing_tx_rejected(self):
        with self.assertRaises(ValueError):
            self._verify(None)

    def test_stale_tx_rejected(self):
        tx = fake_verified_tx(self.user, self.treasury, 100_000_000,
                              slot=900_000_000 - payments_live.SLOT_WINDOW - 1)
        with self.assertRaises(ValueError):
            self._verify(tx)

    def test_bad_signature_rejected(self):
        tx = fake_verified_tx(self.user, self.treasury, 100_000_000)
        with self.assertRaises(ValueError):
            self._verify(tx, sig="not-a-signature")

    def test_replay_rejected(self):
        tx = fake_verified_tx(self.user, self.treasury, 100_000_000)
        self._verify(tx)  # first claim succeeds
        # same signature can never confirm a second hire/bet
        with self.assertRaises(payments_live.ReplayDetected):
            self._verify(tx)

    def test_preclaimed_signature_rejected(self):
        self.db.claim_tx(self.sig, "hire", 3, "b1", self.user, 100_000_000)
        tx = fake_verified_tx(self.user, self.treasury, 100_000_000)
        with self.assertRaises(payments_live.ReplayDetected):
            self._verify(tx)

    def test_missing_config_rejected(self):
        bad = StubSettings({"treasury_wallet": "", "hiring_live": True})
        tx = fake_verified_tx(self.user, self.treasury, 100_000_000)
        with self.assertRaises(payments_live.PaymentNotConfigured):
            self._verify(tx, settings=bad)


# --------------------------------------------------------------------------
# admin settings API


class LiveSettingsApiTest(unittest.TestCase):
    def test_treasury_wallet_validation_and_visibility(self):
        with temp_app() as app:
            with TestClient(app) as c:
                # public by default
                self.assertEqual(c.get("/api/settings").json()
                                 ["treasury_wallet"], "")
                # invalid pubkey rejected
                r = c.put("/api/admin/settings",
                          json={"treasury_wallet": "not-a-pubkey"},
                          headers=ADMIN_HEADERS)
                self.assertEqual(r.status_code, 400, r.text)
                self.assertEqual(c.get("/api/settings").json()
                                 ["treasury_wallet"], "")
                # valid pubkey accepted and publicly visible
                treasury = throwaway_pubkey()
                r = c.put("/api/admin/settings",
                          json={"treasury_wallet": treasury},
                          headers=ADMIN_HEADERS)
                self.assertEqual(r.status_code, 200, r.text)
                self.assertEqual(c.get("/api/settings").json()
                                 ["treasury_wallet"], treasury)
                # clearing works
                r = c.put("/api/admin/settings",
                          json={"treasury_wallet": ""},
                          headers=ADMIN_HEADERS)
                self.assertEqual(r.status_code, 200, r.text)
                self.assertEqual(c.get("/api/settings").json()
                                 ["treasury_wallet"], "")

    def test_live_flags_and_max_bet_public(self):
        with temp_app() as app:
            with TestClient(app) as c:
                s = c.get("/api/settings").json()
                self.assertIn("hiring_live", s)
                self.assertIn("max_bet_sol", s)
                r = c.put("/api/admin/settings",
                          json={"max_bet_sol": -5},
                          headers=ADMIN_HEADERS)
                self.assertEqual(r.status_code, 400, r.text)

    def test_max_bet_enforced(self):
        with betting_window(3), temp_app() as app:
            with TestClient(app) as c:
                r = c.put("/api/admin/settings",
                          json={"max_bet_sol": 2.0},
                          headers=ADMIN_HEADERS)
                self.assertEqual(r.status_code, 200, r.text)
                battle_id = make_battle(c)
                r = c.post(f"/api/battles/{battle_id}/bets", json={
                    "fighter_id": "hawk-2", "wallet": "WalletAAA",
                    "amount_sol": 2.5})
                self.assertEqual(r.status_code, 400, r.text)
                r = c.post(f"/api/battles/{battle_id}/bets", json={
                    "fighter_id": "hawk-2", "wallet": "WalletAAA",
                    "amount_sol": 2.0})
                self.assertEqual(r.status_code, 201, r.text)
                wait_finished(c, app, battle_id)


def make_battle(c, **kw):
    body = {"fighter_ids": ["iron-1", "hawk-2"], "seed": 42}
    body.update(kw)
    r = c.post("/api/battles", json=body)
    assert r.status_code == 202, r.text
    return r.json()["id"]


def wait_finished(c, app, battle_id, timeout=30.0):
    deadline = time.time() + timeout
    while time.time() < deadline:
        r = c.get(f"/api/battles/{battle_id}")
        assert r.status_code == 200
        if r.json()["status"] == "finished":
            while time.time() < deadline:
                if app.state.runner.get_live(battle_id) is None:
                    return r.json()
                time.sleep(0.02)
            break
        time.sleep(0.05)
    raise AssertionError(f"battle {battle_id} did not finish in {timeout}s")


# --------------------------------------------------------------------------
# live hire flow end-to-end (RPC faked)


class LiveHireFlowTest(unittest.TestCase):
    def setUp(self):
        self._ctx = temp_app()
        self.app = self._ctx.__enter__()
        self.addCleanup(self._ctx.__exit__, None, None, None)
        self.c = TestClient(self.app)
        self.c.__enter__()
        self.addCleanup(self.c.__exit__, None, None, None)

        self.treasury = throwaway_pubkey()
        self.user = throwaway_pubkey()
        self.db = self.app.state.db
        self._set_live()

    def _set_live(self):
        r = self.c.put("/api/admin/settings",
                       json={"treasury_wallet": self.treasury,
                             "hiring_live": True,
                             "hire_fee_sol": 0.10},
                       headers=ADMIN_HEADERS)
        assert r.status_code == 200, r.text

    def _battle(self):
        # an open battle row with no engine behind it (stays open: the
        # hireable/bettable pre-fight state)
        battle_id = f"battle-{self._testMethodName}"
        self.db.create_battle({
            "id": battle_id,
            "created_at": "2026-09-26T00:00:00+00:00",
            "status": "open",
            "seed": 1,
            "exhibition": 0,
            "fighter_ids": '["iron-1", "hawk-2"]',
            "registry_ids": '["iron", "hawk"]',
            "playback_speed": 1.0,
            "hire_fee_sol": 0.10,
        })
        return battle_id

    def _patch_rpc(self, tx):
        return mock.patch.object(
            payments_live, "_rpc_client", lambda api_key: FakeRpc(tx=tx))

    def test_live_hire_full_flow(self):
        battle_id = self._battle()
        tx = fake_verified_tx(self.user, self.treasury, 100_000_000)
        with self._patch_rpc(tx):
            # 1. initiate: pending hire + unsigned tx, no fee recorded yet
            r = self.c.post(f"/api/battles/{battle_id}/hire", json={
                "fighter_id": "hawk-2", "wallet": self.user})
            self.assertEqual(r.status_code, 201, r.text)
            hire = r.json()
            # EXACT live-initiate contract shape
            self.assertEqual(
                sorted(hire.keys()),
                ["amount_sol", "hire_id", "payment_status", "transaction_base64",
                 "treasury_wallet", "wallet"])
            self.assertEqual(hire["payment_status"], "pending")
            self.assertEqual(hire["wallet"], self.user)
            self.assertEqual(hire["treasury_wallet"], self.treasury)
            self.assertEqual(hire["amount_sol"], 0.10)
            self.assertTrue(hire["transaction_base64"])
            self.assertEqual(self.db.fee_totals()["hire_sol"], 0.0)
            # 2. confirm: verified onchain -> confirmed, fee recorded
            r = self.c.post(f"/api/battles/{battle_id}/hire/confirm",
                            json={"hire_id": hire["hire_id"],
                                  "signature": throwaway_sig()})
            self.assertEqual(r.status_code, 200, r.text)
            confirmed = r.json()
            self.assertEqual(confirmed["payment_status"], "confirmed")
            self.assertEqual(confirmed["id"], hire["hire_id"])
            self.assertEqual(self.db.fee_totals()["hire_sol"], 0.10)
            # 3. confirm again -> 409
            r = self.c.post(f"/api/battles/{battle_id}/hire/confirm",
                            json={"hire_id": hire["hire_id"],
                                  "signature": throwaway_sig()})
            self.assertEqual(r.status_code, 409, r.text)
            # 4. hire again -> 409
            r = self.c.post(f"/api/battles/{battle_id}/hire", json={
                "fighter_id": "hawk-2", "wallet": self.user})
            self.assertEqual(r.status_code, 409, r.text)

    def test_confirm_wrong_amount_stays_pending(self):
        battle_id = self._battle()
        tx = fake_verified_tx(self.user, self.treasury, 50_000_000)
        with self._patch_rpc(tx):
            r = self.c.post(f"/api/battles/{battle_id}/hire", json={
                "fighter_id": "hawk-2", "wallet": self.user})
            self.assertEqual(r.status_code, 201, r.text)
            hire = r.json()
            r = self.c.post(f"/api/battles/{battle_id}/hire/confirm",
                            json={"hire_id": hire["hire_id"],
                                  "signature": throwaway_sig()})
            self.assertEqual(r.status_code, 400, r.text)
            self.assertEqual(self.db.get_hire(hire["hire_id"])["payment_status"],
                             "pending")

    def test_signature_replay_across_hires(self):
        battle_id = self._battle()
        user2 = throwaway_pubkey()
        tx = fake_verified_tx(self.user, self.treasury, 100_000_000)
        with self._patch_rpc(tx):
            r = self.c.post(f"/api/battles/{battle_id}/hire", json={
                "fighter_id": "hawk-2", "wallet": self.user})
            hire1 = r.json()
            sig = throwaway_sig()
            r = self.c.post(f"/api/battles/{battle_id}/hire/confirm",
                            json={"hire_id": hire1["hire_id"], "signature": sig})
            self.assertEqual(r.status_code, 200, r.text)
            # a DIFFERENT hire cannot be confirmed with the same signature
            # (the fake RPC returns the same tx for any query)
            r = self.c.post(f"/api/battles/{battle_id}/hire", json={
                "fighter_id": "iron-1", "wallet": user2})
            hire2 = r.json()
            r = self.c.post(f"/api/battles/{battle_id}/hire/confirm",
                            json={"hire_id": hire2["hire_id"], "signature": sig})
            self.assertEqual(r.status_code, 409, r.text)

    def test_pending_hire_retry_returns_fresh_tx(self):
        battle_id = self._battle()
        with self._patch_rpc(None):
            r = self.c.post(f"/api/battles/{battle_id}/hire", json={
                "fighter_id": "hawk-2", "wallet": self.user})
            self.assertEqual(r.status_code, 201, r.text)
            hire1 = r.json()
            # same wallet again while pending: same row, fresh unsigned tx
            r = self.c.post(f"/api/battles/{battle_id}/hire", json={
                "fighter_id": "hawk-2", "wallet": self.user})
            self.assertEqual(r.status_code, 201, r.text)
            self.assertEqual(r.json()["hire_id"], hire1["hire_id"])
            self.assertEqual(
                len(self.db.list_hires(battle_id)), 1)

    def test_live_hire_needs_config(self):
        battle_id = self._battle()
        r = self.c.put("/api/admin/settings",
                       json={"treasury_wallet": ""},
                       headers=ADMIN_HEADERS)
        self.assertEqual(r.status_code, 200, r.text)
        r = self.c.post(f"/api/battles/{battle_id}/hire", json={
            "fighter_id": "hawk-2", "wallet": self.user})
        self.assertEqual(r.status_code, 503, r.text)
        self.assertEqual(self.db.list_hires(battle_id), [])

    def test_live_hire_rejects_bad_wallet(self):
        battle_id = self._battle()
        with self._patch_rpc(None):
            r = self.c.post(f"/api/battles/{battle_id}/hire", json={
                "fighter_id": "hawk-2", "wallet": "not-a-pubkey"})
            self.assertEqual(r.status_code, 400, r.text)


# --------------------------------------------------------------------------
# live bet flow end-to-end (RPC faked)


class LiveBetFlowTest(unittest.TestCase):
    def setUp(self):
        self._ctx = temp_app()
        self.app = self._ctx.__enter__()
        self.addCleanup(self._ctx.__exit__, None, None, None)
        self.c = TestClient(self.app)
        self.c.__enter__()
        self.addCleanup(self.c.__exit__, None, None, None)

        self.treasury = throwaway_pubkey()
        self.user = throwaway_pubkey()
        self.db = self.app.state.db
        r = self.c.put("/api/admin/settings",
                       json={"treasury_wallet": self.treasury,
                             "betting_live": True,
                             "max_bet_sol": 5.0},
                       headers=ADMIN_HEADERS)
        assert r.status_code == 200, r.text

    def _battle(self):
        battle_id = f"battle-{self._testMethodName}"
        self.db.create_battle({
            "id": battle_id,
            "created_at": "2026-09-26T00:00:00+00:00",
            "status": "open",
            "seed": 1,
            "exhibition": 0,
            "fighter_ids": '["iron-1", "hawk-2"]',
            "registry_ids": '["iron", "hawk"]',
            "playback_speed": 1.0,
            "hire_fee_sol": 0.10,
        })
        return battle_id

    def _patch_rpc(self, tx):
        return mock.patch.object(
            payments_live, "_rpc_client", lambda api_key: FakeRpc(tx=tx))

    def test_pending_bet_excluded_from_pool_until_confirmed(self):
        battle_id = self._battle()
        tx = fake_verified_tx(self.user, self.treasury, 1_500_000_000)
        with self._patch_rpc(tx):
            r = self.c.post(f"/api/battles/{battle_id}/bets", json={
                "fighter_id": "hawk-2", "wallet": self.user,
                "amount_sol": 1.5})
            self.assertEqual(r.status_code, 201, r.text)
            bet = r.json()
            # EXACT live-initiate contract shape
            self.assertEqual(
                sorted(bet.keys()),
                ["amount_sol", "bet_id", "payment_status", "transaction_base64",
                 "treasury_wallet", "wallet"])
            self.assertEqual(bet["payment_status"], "pending")
            self.assertEqual(bet["treasury_wallet"], self.treasury)
            self.assertTrue(bet["transaction_base64"])
            # visible in the bet list, NOT in the pool
            self.assertEqual(
                len(self.c.get(f"/api/battles/{battle_id}/bets")
                    .json()["bets"]), 1)
            pool = self.c.get(f"/api/battles/{battle_id}/pool").json()
            self.assertEqual(pool["total_sol"], 0.0)
            self.assertEqual(pool["bet_count"], 0)
            # confirm -> counted
            r = self.c.post(f"/api/battles/{battle_id}/bets/confirm",
                            json={"bet_id": bet["bet_id"],
                                  "signature": throwaway_sig()})
            self.assertEqual(r.status_code, 200, r.text)
            self.assertEqual(r.json()["payment_status"], "confirmed")
            pool = self.c.get(f"/api/battles/{battle_id}/pool").json()
            self.assertEqual(pool["total_sol"], 1.5)
            self.assertEqual(pool["bet_count"], 1)

    def test_confirm_failed_tx_rejected(self):
        battle_id = self._battle()
        tx = fake_verified_tx(self.user, self.treasury, 1_000_000_000,
                              err={"InstructionError": [0, "Custom"]})
        with self._patch_rpc(tx):
            r = self.c.post(f"/api/battles/{battle_id}/bets", json={
                "fighter_id": "hawk-2", "wallet": self.user,
                "amount_sol": 1.0})
            bet = r.json()
            r = self.c.post(f"/api/battles/{battle_id}/bets/confirm",
                            json={"bet_id": bet["bet_id"],
                                  "signature": throwaway_sig()})
            self.assertEqual(r.status_code, 400, r.text)
            self.assertEqual(self.db.get_bet(bet["bet_id"])["payment_status"],
                             "pending")

    def test_signature_replay_across_bets(self):
        battle_id = self._battle()
        tx = fake_verified_tx(self.user, self.treasury, 1_000_000_000)
        user2 = throwaway_pubkey()
        with self._patch_rpc(tx):
            r = self.c.post(f"/api/battles/{battle_id}/bets", json={
                "fighter_id": "hawk-2", "wallet": self.user,
                "amount_sol": 1.0})
            bet1 = r.json()
            sig = throwaway_sig()
            r = self.c.post(f"/api/battles/{battle_id}/bets/confirm",
                            json={"bet_id": bet1["bet_id"], "signature": sig})
            self.assertEqual(r.status_code, 200, r.text)
            r = self.c.post(f"/api/battles/{battle_id}/bets", json={
                "fighter_id": "hawk-2", "wallet": user2,
                "amount_sol": 1.0})
            bet2 = r.json()
            r = self.c.post(f"/api/battles/{battle_id}/bets/confirm",
                            json={"bet_id": bet2["bet_id"], "signature": sig})
            self.assertEqual(r.status_code, 409, r.text)

    def test_client_amounts_not_trusted(self):
        # the server uses the stored bet amount for verification, never
        # what the confirm request claims
        battle_id = self._battle()
        tx = fake_verified_tx(self.user, self.treasury, 500_000_000)
        with self._patch_rpc(tx):
            r = self.c.post(f"/api/battles/{battle_id}/bets", json={
                "fighter_id": "hawk-2", "wallet": self.user,
                "amount_sol": 1.0})
            bet = r.json()
            r = self.c.post(f"/api/battles/{battle_id}/bets/confirm",
                            json={"bet_id": bet["bet_id"],
                                  "signature": throwaway_sig()})
            self.assertEqual(r.status_code, 400, r.text)

    def test_max_bet_enforced_in_live_mode(self):
        battle_id = self._battle()
        with self._patch_rpc(None):
            r = self.c.post(f"/api/battles/{battle_id}/bets", json={
                "fighter_id": "hawk-2", "wallet": self.user,
                "amount_sol": 9.0})
            self.assertEqual(r.status_code, 400, r.text)
            r = self.c.post(f"/api/battles/{battle_id}/bets", json={
                "fighter_id": "hawk-2", "wallet": self.user,
                "amount_sol": 5.0})
            self.assertEqual(r.status_code, 201, r.text)

    def test_confirm_on_finished_battle_rejected(self):
        battle_id = self._battle()
        tx = fake_verified_tx(self.user, self.treasury, 1_000_000_000)
        with self._patch_rpc(tx):
            r = self.c.post(f"/api/battles/{battle_id}/bets", json={
                "fighter_id": "hawk-2", "wallet": self.user,
                "amount_sol": 1.0})
            bet = r.json()
            self.db._q("UPDATE battles SET status='finished' WHERE id=?", (battle_id,))
            r = self.c.post(f"/api/battles/{battle_id}/bets/confirm",
                            json={"bet_id": bet["bet_id"],
                                  "signature": throwaway_sig()})
            self.assertEqual(r.status_code, 400, r.text)

    def test_mock_mode_response_shape_unchanged(self):
        # live flags off: the exact legacy contract holds
        r = self.c.put("/api/admin/settings",
                       json={"betting_live": False},
                       headers=ADMIN_HEADERS)
        assert r.status_code == 200, r.text
        # the mock bet needs an 'open' battle: flip the window for this
        # app (settings.get reads the env var on every call)
        os.environ["ARENA_BETTING_WINDOW_SEC"] = "3"
        try:
            battle_id = make_battle(self.c)
            r = self.c.post(f"/api/battles/{battle_id}/bets", json={
                "fighter_id": "hawk-2", "wallet": "WalletAAA",
                "amount_sol": 1.5})
            self.assertEqual(r.status_code, 201, r.text)
            self.assertEqual(
                sorted(r.json().keys()),
                ["amount_sol", "battle_id", "created_at", "fighter_id",
                 "id", "payment_status", "wallet"])
        finally:
            os.environ["ARENA_BETTING_WINDOW_SEC"] = "0"
        wait_finished(self.c, self.app, battle_id)


if __name__ == "__main__":
    unittest.main()
