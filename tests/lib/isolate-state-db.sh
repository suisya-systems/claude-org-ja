#!/usr/bin/env bash
# hook のテストが拒否の記録 (hook_denied) を本番の state.db に書かないようにする (Issue #1234)。
#
# hook は exit 2 のたびに .hooks/lib/deny-record.sh -> tools/journal_append.py で events に
# 1 行書く。STATE_DB_PATH が無いと書き先は repo の配置から本番 checkout の .state/state.db に
# 解決される (worktree から流しても同じ)。hook を呼ぶ tests/test-*.sh は hook を呼ぶ前に
# これを source し、書き先を使い捨ての DB に向ける。STATE_DB_PATH が既にあれば
# (tests/run-all.sh や呼び出し側の明示) そのまま使う。
# tests/test_hook_tests_state_db_isolation.py が source 漏れを検査する。
if [[ -z "${STATE_DB_PATH:-}" ]]; then
  # ponytail: 単体実行のたびに $TMPDIR に小さな DB が 1 つ残る。run-all.sh 経由は後片付け済み。
  STATE_DB_PATH="$(mktemp -d)/state.db" && export STATE_DB_PATH
fi
