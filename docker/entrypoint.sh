#!/usr/bin/env bash
# claude-org-ja コンテナの entrypoint（設計: docs/design/org-docker-distribution.md §5–§8）。
# tini (PID1) の子として root で開始し、one-time 所有権修復 → gosu org で降格 →
# 残骸 reconcile → broker daemon + dashboard 起動 → SIGTERM を trap して畳む。
set -euo pipefail

ORG_HOME=/home/org
REPO=/workspace/claude-org-ja
WORKERS=/workspace/workers
STATE="${REPO}/.state"
VENV=/opt/org-venv

log() { echo "[entrypoint] $*"; }

# ---------------------------------------------------------------------------
# 段 2: one-time 所有権修復（root。設計 §8）
# named volume / bind mount の初回のみ chown -R し、マーカーで冪等化する。
# ---------------------------------------------------------------------------
if [ "$(id -u)" = "0" ]; then
    want="$(id -u org):$(id -g org)"
    for d in "${ORG_HOME}" "${STATE}" "${WORKERS}"; do
        mkdir -p "$d"
        # マーカーには UID:GID を記録し、ORG_UID を変えて rebuild した場合にも
        # 再修復が走るようにする
        if [ ! -e "$d/.org-owned" ] || [ "$(cat "$d/.org-owned" 2>/dev/null)" != "${want}" ]; then
            log "one-time chown (${want}): $d"
            chown -R org:org "$d"
            echo "${want}" > "$d/.org-owned" && chown org:org "$d/.org-owned"
        fi
    done
    exec gosu org "$0" "$@"
fi

# ---- 以降はすべて org ユーザー ----
cd "${REPO}"

cmd="${1:-daemon}"
if [ "${cmd}" = "shell" ]; then
    exec /bin/bash
fi
if [ "${cmd}" != "daemon" ]; then
    echo "usage: entrypoint.sh [daemon|shell]" >&2
    exit 2
fi

# ---------------------------------------------------------------------------
# 段 3a: 残骸 reconciliation（設計 §7.1。docker restart では /tmp が残るため
# tmpfs を当てにせず明示削除する）
# ---------------------------------------------------------------------------
log "reconcile: purging transient state"
rm -rf "${STATE}/broker"
rm -f  "${STATE}/dashboard.pid" "${STATE}/attention_pane.json" \
       "${STATE}/secretary_queue_watcher.json"
find "${STATE}" -maxdepth 1 -name '*.log' -delete 2>/dev/null || true
rm -f /tmp/tmux-*/"${ORG_BROKER_SOCKET:-claude-org-broker}" 2>/dev/null || true
rm -f /tmp/herdr-*.sock 2>/dev/null || true
rm -f "${ORG_HOME}/.config/herdr/herdr-server.log" 2>/dev/null || true

# ---------------------------------------------------------------------------
# 段 3b: ロール別 settings.local.json の永続 symlink（設計 §6.1）
# 実体は org_state volume 内 role-config/ に置き、コンテナ再作成を生き残らせる。
# ---------------------------------------------------------------------------
mkdir -p "${STATE}/role-config"
link_role_config() { # $1=repo 内パス $2=role 名
    local target="${STATE}/role-config/$2.settings.local.json"
    local link="${REPO}/$1"
    mkdir -p "$(dirname "${link}")"
    if [ ! -L "${link}" ]; then
        # image 層に生ファイルが紛れていた場合は退避せず捨てる（焼き込み禁止物）
        rm -f "${link}"
        ln -s "${target}" "${link}"
    fi
}
link_role_config ".claude/settings.local.json" "secretary"
link_role_config ".dispatcher/.claude/settings.local.json" "dispatcher"
link_role_config ".curator/.claude/settings.local.json" "curator"
if [ ! -f "${STATE}/role-config/secretary.settings.local.json" ]; then
    log "NOTE: role settings not generated yet — run 'org-shell --setup' (org_setup_prune.py --all) on first use"
fi

# ---------------------------------------------------------------------------
# 段 3c: fresh volume なら state.db を構築（設計 §5 段 3）
# ---------------------------------------------------------------------------
if [ ! -f "${STATE}/state.db" ]; then
    log "state.db not found — rebuilding from repo"
    "${VENV}/bin/python" -m tools.state_db.importer \
        --db "${STATE}/state.db" --root . --rebuild --no-strict
fi

# ---------------------------------------------------------------------------
# 段 3d: Pi 5 等向け worker 並列数の反映（設計 §11。PoC の手当て — env override
# 機構の根治は設計 §13-1）
# ---------------------------------------------------------------------------
if [ -n "${ORG_MAX_WORKERS:-}" ]; then
    sed -i -E "s/^max_concurrent_workers: .*/max_concurrent_workers: ${ORG_MAX_WORKERS}/" \
        registry/org-config.md
fi

# ---------------------------------------------------------------------------
# 段 4: broker daemon（設計 §5 段 4。失敗は fail-fast）
# ---------------------------------------------------------------------------
log "starting broker daemon (backend=${ORG_BACKEND:-tmux}, port=${ORG_BROKER_PORT:-48720})"
"${VENV}/bin/python" -u -m claude_org_runtime.broker serve \
    --backend "${ORG_BACKEND:-tmux}" \
    --state-dir "${STATE}/broker" \
    --host 127.0.0.1 \
    --port "${ORG_BROKER_PORT:-48720}" \
    --root-cwd "${REPO}" \
    &
BROKER_PID=$!
sleep 2
if ! kill -0 "${BROKER_PID}" 2>/dev/null; then
    log "FATAL: broker daemon failed to start"
    exit 1
fi

# ---------------------------------------------------------------------------
# 段 5: dashboard（設計 §5 段 5。失敗は警告のみ）+ opt-in socat 公開（設計 §7.4）
# ---------------------------------------------------------------------------
DASH_PID=""
SOCAT_PID=""
python3 dashboard/server.py &
DASH_PID=$!
sleep 1
if ! kill -0 "${DASH_PID}" 2>/dev/null; then
    log "WARN: dashboard failed to start (continuing without it)"
    DASH_PID=""
elif [ "${ORG_DASHBOARD_EXPOSE:-0}" = "1" ]; then
    # dashboard/server.py は 8099 が塞がっていると 8100/8101 へフォールバックするが、
    # fresh コンテナ内では 8099 が最初に空いている前提（8099 固定転送。設計 §7.4）
    socat TCP-LISTEN:18099,bind=0.0.0.0,fork,reuseaddr TCP:127.0.0.1:8099 &
    SOCAT_PID=$!
fi

# ---------------------------------------------------------------------------
# 停止契約（設計 §5）: SIGTERM で dashboard → daemon → tmux server の順に畳む
# ---------------------------------------------------------------------------
shutdown() {
    log "SIGTERM: shutting down"
    [ -n "${SOCAT_PID}" ] && kill "${SOCAT_PID}" 2>/dev/null || true
    [ -n "${DASH_PID}" ] && kill "${DASH_PID}" 2>/dev/null || true
    kill "${BROKER_PID}" 2>/dev/null || true
    wait "${BROKER_PID}" 2>/dev/null || true
    tmux -L "${ORG_BROKER_SOCKET:-claude-org-broker}" kill-server 2>/dev/null || true
    exit 0
}
trap shutdown TERM INT

log "org infra is up — attach with: docker exec -it <container> org-shell"
# broker daemon をコンテナの寿命の基準にする（死んだら terminate）。
# wait の非ゼロ終了で set -e に即殺されないよう || true で受ける
wait "${BROKER_PID}" || true
log "broker daemon exited — terminating container"
exit 1
