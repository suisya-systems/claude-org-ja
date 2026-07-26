#!/usr/bin/env bash
# Hook fixture (NOT a test suite): always blocks and logs stdin.
#
# settings.json の PreToolUse に一時的に差し込んで「hook 配線そのものが発火するか」を
# 手動確認するための最小 hook。テストランナー (tests/run-all.sh) の収集対象ではないため、
# test-*.sh ではなく fixture-*.sh を名乗る（tests/run-all.sh の収集漏れ検出ガードが
# test に見える命名のファイルを未収集として fail させる）。

echo "[$(date)]  fixture-always-block.sh called" >> /tmp/hook-test.log
cat >> /tmp/hook-test.log
echo "" >> /tmp/hook-test.log

echo "ブロック: テスト用の常時ブロック" >&2
exit 2
