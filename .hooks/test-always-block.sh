#!/usr/bin/env bash
# Minimal test hook: always blocks and logs

echo "[$(date)]  test-always-block.sh called" >> /tmp/hook-test.log
cat >> /tmp/hook-test.log
echo "" >> /tmp/hook-test.log

echo "ブロック: テスト用の常時ブロック" >&2
exit 2
