#!/usr/bin/env python3
"""Remind the secretary to verify a user's premise before agreeing with it.

Issue #1192. The secretary agreed with an owner's premise ("the threshold
should assume a 256K window") without opening the record it had already
read, which showed a 746,933-token dispatcher conversation. citation-or-abstain
in CLAUDE.md asks for a source on factual propositions but did not name the
moment of agreeing with one the user stated.

Run by the secretary's UserPromptSubmit hook
(``.hooks/remind-verify-premise.sh``): reads the hook JSON on stdin and, when
the user's prompt carries a number with a unit, a ``key=value`` setting, or a
claim word about behavior, prints one reminder line (UserPromptSubmit stdout
goes into the secretary's context). It never blocks: a false positive only
adds a line, so the rule stays deliberately simple. Always exits 0.
"""
from __future__ import annotations

import json
import re
import sys

# Numbers with a unit: "256K", "9 分", "80%", "746,933 トークン".
_NUMBER_WITH_UNIT = re.compile(
    r"\d[\d,.]*\s*(?:[KMGkmg]\b|%|％|秒|分|時間|日|週|トークン|tokens?\b|[KMG]B\b|ms\b|倍|行)"
)
# Settings: "ORG_TRANSPORT=broker", "timeout: 900".
_SETTING = re.compile(r"\b[A-Za-z_][\w.-]*\s*(?:=|:)\s*[\w\"'./-]")
# Claim words about how something is or behaves.
_CLAIM_WORD = re.compile(
    r"はず|のでは|じゃない[？?]|ですよね|だよね|でしょ|前提|仕様|既定|デフォルト|閾値|上限|下限|設定値"
)

REMINDER = (
    "VERIFY_BEFORE_AGREE: ユーザーの文に数値・設定値・挙動の主張がある。"
    "同意・反論を書く前に、記録・コード・docs を 1 回読むか検索し、"
    "出典を付けて返す（CLAUDE.md citation-or-abstain）。"
)


def has_premise(prompt: str) -> bool:
    # Peer messages and background task notices injected into the session
    # are not the user's premises.
    if "<channel source=" in prompt or "<task-notification>" in prompt:
        return False
    return any(p.search(prompt) for p in (_NUMBER_WITH_UNIT, _SETTING, _CLAIM_WORD))


def main() -> int:
    try:
        payload = json.load(sys.stdin)
        prompt = payload.get("prompt") if isinstance(payload, dict) else None
        if isinstance(prompt, str) and has_premise(prompt):
            print(REMINDER)
    except Exception:
        pass
    return 0


if __name__ == "__main__":
    sys.exit(main())
