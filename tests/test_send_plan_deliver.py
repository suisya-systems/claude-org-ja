"""send_plan.json carries ``deliver="user_turn"`` under broker transport.

Since Claude Code 2.1.325 a channel-delivered DELEGATE is held by the
dispatcher as "not from your user"; the secretary must send operational
instructions as a user turn (broker ``send_message(deliver="user_turn")``).
renga has no such argument in this repo's usage, so its manifest stays as is.
"""
from __future__ import annotations

import json
import os
import tempfile
import unittest
from contextlib import contextmanager
from pathlib import Path

from tools import gen_delegate_payload as gdp
from tools import transport as t

MAX_BODY_BYTES = 4096  # claude_org_runtime.broker.user_turn.MAX_BODY_BYTES


class _Plan:
    def __init__(self, body: str):
        self.delegate_body = body

    def to_summary_dict(self) -> dict:
        return {"task_id": "demo", "pattern": "B"}


@contextmanager
def _transport(value):
    prev = os.environ.get(t.ENV_KEY)
    try:
        if value is None:
            os.environ.pop(t.ENV_KEY, None)
        else:
            os.environ[t.ENV_KEY] = value
        yield
    finally:
        if prev is None:
            os.environ.pop(t.ENV_KEY, None)
        else:
            os.environ[t.ENV_KEY] = prev


def _write(body: str) -> dict:
    with tempfile.TemporaryDirectory() as d:
        out = Path(d) / "send_plan.json"
        gdp._write_send_plan(_Plan(body), out_path=out)
        return json.loads(out.read_text(encoding="utf-8"))


class SendPlanDeliver(unittest.TestCase):
    def test_broker_manifest_has_user_turn(self):
        with _transport("broker"):
            self.assertEqual(_write("DELEGATE: x")["deliver"], "user_turn")

    def test_default_transport_is_broker(self):
        with _transport(None):
            self.assertEqual(_write("DELEGATE: x")["deliver"], "user_turn")

    def test_renga_manifest_has_no_deliver(self):
        with _transport("renga"):
            self.assertNotIn("deliver", _write("DELEGATE: x"))

    def test_broker_hint_mentions_deliver(self):
        with _transport("broker"):
            self.assertIn("deliver", gdp._next_step_hint())
        with _transport("renga"):
            self.assertNotIn("deliver", gdp._next_step_hint())

    def test_broker_body_limit_is_utf8_bytes(self):
        ok = "あ" * (MAX_BODY_BYTES // 3)  # 4095 bytes
        over = ok + "いろ"  # 4101 bytes
        with _transport("broker"):
            _write(ok)
            with self.assertRaises(ValueError) as cm:
                _write(over)
            self.assertIn(str(MAX_BODY_BYTES), str(cm.exception))

    def test_renga_has_no_body_limit(self):
        with _transport("renga"):
            _write("あ" * 3000)


if __name__ == "__main__":
    unittest.main()
