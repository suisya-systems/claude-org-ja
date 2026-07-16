#!/usr/bin/env bash
# 人間の一次導線（設計: docs/design/org-docker-distribution.md §5 段 6 / §10）。
#   docker exec -it claude-org org-shell            # 通常: org up → secretary TUI
#   docker exec -it claude-org org-shell --setup    # 初回: 認証 + org-setup ガイド
#   docker exec -it claude-org org-shell --attach   # 既存 tmux セッションに attach
set -euo pipefail

# docker exec は root で入るため、対話面は必ず org に自己降格する（設計 §8）
if [ "$(id -u)" = "0" ]; then
    exec gosu org "$0" "$@"
fi

REPO=/workspace/claude-org-ja
VENV=/opt/org-venv
SESSION=org-secretary
cd "${REPO}"

mode="${1:-up}"

case "${mode}" in
--setup)
    cat <<'EOS'
=== claude-org-ja 初回セットアップ（設計 §10） ===
以下を順に実行してください（すべて org_home volume に永続化されます）:

  1. claude                # 起動して /login（Claude OAuth）→ 終了
  2. gh auth login
  3. codex login           # 任意（Codex ゲートを使う場合）
  4. Slack / Google MCP 接続  # 任意（Slack は Claude Code 内の plugin 接続、
                             #  Google は gog セットアップ → ~/.config/gogcli/）
  5. python tools/org_setup_prune.py --all
     python tools/org_setup_prune.py --user-common-sandbox
  6. exit → docker exec -it <container> org-shell   # 通常導線へ

EOS
    exec /bin/bash
    ;;
--attach)
    exec tmux -L org-shell attach -t "${SESSION}"
    ;;
up)
    # 認証未了なら通常導線に入れない（fail-fast + 案内。設計 §10）
    if [ ! -f "${HOME}/.claude/.credentials.json" ]; then
        echo "Claude 認証が見つかりません。先に初回セットアップを実行してください:" >&2
        echo "  docker exec -it <container> org-shell --setup" >&2
        exit 1
    fi
    # secretary TUI は detach 耐性のため人間側 tmux セッション内で起動する
    # （broker backend の tmux socket claude-org-broker とは別 socket）。
    # org up は entrypoint が立てた healthy daemon を再利用し TUI のみ起動する。
    if tmux -L org-shell has-session -t "${SESSION}" 2>/dev/null; then
        exec tmux -L org-shell attach -t "${SESSION}"
    fi
    exec tmux -L org-shell new-session -s "${SESSION}" \
        "${VENV}/bin/claude-org-runtime org up \
            --state-dir ${REPO}/.state/broker \
            --backend ${ORG_BACKEND:-tmux} \
            --root-cwd ${REPO}"
    ;;
*)
    echo "usage: org-shell [--setup|--attach]" >&2
    exit 2
    ;;
esac
