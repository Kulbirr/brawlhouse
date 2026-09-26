"""Arena chat: POST/GET validation, rate limiting, XSS stripping, and the
websocket broadcast shape."""

import tempfile
import time
import unittest
from contextlib import contextmanager
from pathlib import Path

from fastapi.testclient import TestClient  # noqa: E402

from backend.app import create_app  # noqa: E402


@contextmanager
def temp_app():
    with tempfile.TemporaryDirectory() as tmp:
        yield create_app(data_dir=tmp)


def _make_battle(c):
    r = c.post("/api/battles", json={
        "fighter_ids": ["iron-1", "hawk-2"], "seed": 7})
    assert r.status_code == 202, r.text
    return r.json()["id"]


class ChatApiTest(unittest.TestCase):
    def test_post_and_get_roundtrip(self):
        with temp_app() as app:
            with TestClient(app) as c:
                battle_id = _make_battle(c)
                r = c.post(f"/api/battles/{battle_id}/chat",
                           json={"wallet": "WALLET1", "message": "hello arena"})
                self.assertEqual(r.status_code, 201, r.text)
                row = r.json()
                self.assertEqual(row["battle_id"], battle_id)
                self.assertEqual(row["wallet"], "WALLET1")
                self.assertEqual(row["message"], "hello arena")
                self.assertIn("id", row)
                self.assertIn("created_at", row)

                r = c.get(f"/api/battles/{battle_id}/chat")
                self.assertEqual(r.status_code, 200)
                msgs = r.json()["messages"]
                self.assertEqual(len(msgs), 1)
                self.assertEqual(msgs[0]["message"], "hello arena")

    def test_get_oldest_first_and_limit(self):
        with temp_app() as app:
            with TestClient(app) as c:
                battle_id = _make_battle(c)
                # rate limit is per wallet: use distinct wallets to post fast
                for i in range(5):
                    r = c.post(
                        f"/api/battles/{battle_id}/chat",
                        json={"wallet": f"W{i}", "message": f"msg {i}"})
                    self.assertEqual(r.status_code, 201, r.text)
                r = c.get(f"/api/battles/{battle_id}/chat")
                msgs = r.json()["messages"]
                self.assertEqual([m["message"] for m in msgs],
                                 [f"msg {i}" for i in range(5)])
                r = c.get(f"/api/battles/{battle_id}/chat?limit=2")
                self.assertEqual(len(r.json()["messages"]), 2)

    def test_get_returns_newest_window_oldest_first(self):
        with temp_app() as app:
            with TestClient(app) as c:
                battle_id = _make_battle(c)
                # distinct wallets dodge the per-wallet rate limit
                for i in range(60):
                    r = c.post(
                        f"/api/battles/{battle_id}/chat",
                        json={"wallet": f"W{i}", "message": f"msg {i}"})
                    self.assertEqual(r.status_code, 201, r.text)
                r = c.get(f"/api/battles/{battle_id}/chat")
                msgs = r.json()["messages"]
                self.assertEqual(len(msgs), 50)
                # oldest 10 messages fall outside the newest window
                self.assertEqual(
                    [m["message"] for m in msgs],
                    [f"msg {i}" for i in range(10, 60)])
                ids = [m["id"] for m in msgs]
                self.assertEqual(ids, sorted(ids))

    def test_unknown_battle_404(self):
        with temp_app() as app:
            with TestClient(app) as c:
                r = c.post("/api/battles/nope/chat",
                           json={"wallet": "W", "message": "hi"})
                self.assertEqual(r.status_code, 404)
                r = c.get("/api/battles/nope/chat")
                self.assertEqual(r.status_code, 404)

    def test_empty_and_long_message_400(self):
        with temp_app() as app:
            with TestClient(app) as c:
                battle_id = _make_battle(c)
                r = c.post(f"/api/battles/{battle_id}/chat",
                           json={"wallet": "W", "message": "   "})
                self.assertEqual(r.status_code, 400)
                r = c.post(f"/api/battles/{battle_id}/chat",
                           json={"wallet": "W", "message": "x" * 201})
                self.assertEqual(r.status_code, 400)
                r = c.post(f"/api/battles/{battle_id}/chat",
                           json={"wallet": "", "message": "hi"})
                self.assertEqual(r.status_code, 400)

    def test_html_tags_stripped(self):
        with temp_app() as app:
            with TestClient(app) as c:
                battle_id = _make_battle(c)
                r = c.post(
                    f"/api/battles/{battle_id}/chat",
                    json={"wallet": "W",
                          "message": '<script>alert(1)</script>hi <b>there</b>'})
                self.assertEqual(r.status_code, 201, r.text)
                self.assertEqual(r.json()["message"], "alert(1)hi there")
                # a message that is only tags becomes empty -> 400
                r = c.post(f"/api/battles/{battle_id}/chat",
                           json={"wallet": "W2", "message": "<img src=x>"})
                self.assertEqual(r.status_code, 400)

    def test_rate_limit_429(self):
        with temp_app() as app:
            with TestClient(app) as c:
                battle_id = _make_battle(c)
                r = c.post(f"/api/battles/{battle_id}/chat",
                           json={"wallet": "FAST", "message": "one"})
                self.assertEqual(r.status_code, 201, r.text)
                r = c.post(f"/api/battles/{battle_id}/chat",
                           json={"wallet": "FAST", "message": "two"})
                self.assertEqual(r.status_code, 429)
                # a different wallet is not limited
                r = c.post(f"/api/battles/{battle_id}/chat",
                           json={"wallet": "OTHER", "message": "two"})
                self.assertEqual(r.status_code, 201, r.text)
                # and the window expires
                time.sleep(2.1)
                r = c.post(f"/api/battles/{battle_id}/chat",
                           json={"wallet": "FAST", "message": "three"})
                self.assertEqual(r.status_code, 201, r.text)

    def test_ws_broadcasts_chat(self):
        with temp_app() as app:
            with TestClient(app) as c:
                battle_id = _make_battle(c)
                with c.websocket_connect(f"/ws/battles/{battle_id}") as ws:
                    info = ws.receive_json()
                    self.assertEqual(info["type"], "info")
                    r = c.post(f"/api/battles/{battle_id}/chat",
                               json={"wallet": "CHATTER",
                                     "message": "gg everyone"})
                    self.assertEqual(r.status_code, 201, r.text)
                    chat = None
                    for _ in range(3000):
                        msg = ws.receive_json()
                        if msg.get("type") == "chat":
                            chat = msg
                            break
                    self.assertIsNotNone(chat, "no chat broadcast received")
                    self.assertEqual(chat["wallet"], "CHATTER")
                    self.assertEqual(chat["message"], "gg everyone")
                    self.assertIn("id", chat)
                    self.assertEqual(chat["battle_id"], battle_id)
                    self.assertIn("created_at", chat)


if __name__ == "__main__":
    unittest.main()
