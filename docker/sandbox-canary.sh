#!/usr/bin/env bash
# Bash sandbox の実起動テスト（canary）。
# 設計: docs/design/org-docker-distribution.md §7.5 / §12 S6-d。
#
# 何を測るか: 「bwrap が在るか」ではなく「bwrap が実際に user namespace を作れるか」。
# Claude Code の起動時チェックは依存の**実在**しか見ず**機能性**を見ないため、
# `sandbox.failIfUnavailable: true` でも「bwrap は在るが userns を作れない」条件
# （seccomp=unconfined が効いていない等 = 設計 §12 S1 相当）を素通りする。
# その条件を検出できるのは runtime の live canary だけなので、コンテナの起動経路に
# 本スクリプトを噛ませて fail-closed にする。
#
# 実行主体: Claude Code は org（UID 1000）で走る。image の既定ユーザーは root なので
# root で測ると実行主体と違う条件を測ることになる。ここでは呼び出し側の規律に頼らず
# 冒頭で自ら gosu org へ降格する（`docker exec` の -u 付け忘れを構造的に潰す）。
#
# ORG_SANDBOX_CANARY:
#   enforce（既定） 失敗したら非ゼロで抜ける（entrypoint が起動を中止する）
#   warn            失敗しても 0 で抜ける（警告のみ。デバッグ用シェルを取るため）
#   off             実行しない
# warn / off にしても Claude Code 側の `sandbox.failIfUnavailable: true`
# （/etc/claude-code/managed-settings.json）は生きているので、依存欠落は依然
# Claude Code の起動拒否で止まる。この escape hatch が緩めるのは
# 「コンテナが起動するか」だけで、sandbox 保証そのものではない。
set -uo pipefail

# docker exec は root で入るため、必ず org に自己降格してから測る
if [ "$(id -u)" = "0" ] && command -v gosu >/dev/null 2>&1; then
    exec gosu org "$0" "$@"
fi

MODE="${ORG_SANDBOX_CANARY:-enforce}"
SETTINGS="${ORG_SANDBOX_CANARY_SETTINGS:-/opt/org-sandbox/canary-settings.json}"
DOCTOR="${ORG_VENV:-/opt/org-venv}/bin/claude-org-runtime"

log() { echo "[sandbox-canary] $*"; }
err() { echo "[sandbox-canary] $*" >&2; }

case "${MODE}" in
off)
    log "skipped (ORG_SANDBOX_CANARY=off) - the Bash sandbox is NOT verified"
    exit 0
    ;;
enforce | warn) ;;
*)
    # 設定ミスは fail-closed に倒す（typo で黙って無検査にしない）
    err "FATAL: invalid ORG_SANDBOX_CANARY='${MODE}' (expected: enforce|warn|off)"
    exit 2
    ;;
esac

# 失敗の共通出口。enforce なら非ゼロ、warn なら 0（警告のみ）で抜ける。
fail() {
    err "FAIL: $*"
    err ""
    err "  Claude Code の Bash sandbox はこのコンテナで機能しません。"
    err "  Claude Code 自身はこの条件を検出できません（起動時チェックは bwrap の"
    err "  実在のみを見て機能性を見ないため、failIfUnavailable:true でも素通りする）。"
    err "  よくある原因:"
    err "    - compose の security_opt: [seccomp=unconfined] が効いていない"
    err "      （素の docker run / security_opt を落とす環境で起動した）"
    err "    - rootless Docker ホストでネスト userns が作れない（設計 §12 S3）"
    err "    - Ubuntu 24.04+ ホストの AppArmor 制限（設計 §12 S4）:"
    err "      sysctl kernel.apparmor_restrict_unprivileged_userns が 1 なら要対処"
    err "  手元で再現する:"
    err "    docker exec <container> org-sandbox-canary"
    err "  検査を外して起動だけしたい場合（sandbox は無保証になる）:"
    err "    ORG_SANDBOX_CANARY=warn docker compose up -d --force-recreate"
    if [ "${MODE}" = "enforce" ]; then
        exit 1
    fi
    err ""
    err "WARN mode: continuing WITHOUT a working Bash sandbox."
    exit 0
}

# 実行主体をログに残す。sandbox の成否は実行主体で変わりうるので、後から
# 「何を測ったか」が判別できる形にしておく（Claude Code は org で走る）。
log "probing as $(id -un) (uid $(id -u))"

if ! command -v bwrap >/dev/null 2>&1; then
    fail "bwrap not found on PATH (this image is built with bubblewrap; a missing binary means the image is not the shipped one)"
fi

if [ ! -x "${DOCTOR}" ]; then
    fail "claude-org-runtime not found at ${DOCTOR}"
fi

report="$("${DOCTOR}" sandbox doctor --settings "${SETTINGS}" --no-merge-scopes --json 2>&1)"
rc=$?

# rc=2 は settings が読めない / 壊れている（診断が成立していない）
if [ "${rc}" = "2" ]; then
    fail "sandbox doctor could not read ${SETTINGS}: ${report}"
fi

status="$(printf '%s' "${report}" | jq -r '.canary.status // "missing"' 2>/dev/null)"
detail="$(printf '%s' "${report}" | jq -r '.canary.detail // ""' 2>/dev/null)"
if [ -z "${status}" ] || [ "${status}" = "missing" ]; then
    fail "could not parse sandbox doctor output: ${report}"
fi

# ここが本スクリプトの要点。doctor は canary を回せなかった場合 status=skipped を
# 返し、report.ok は true・exit 0 になる（bwrap 不在、あるいは deny に絶対パスが
# 1 つも無い場合。出荷 image の .claude/settings.json は deny が全て相対パスなので、
# 素の settings を渡すと後者に落ちて「常に緑のゲート」になる）。
# skipped は「合格」ではなく「未判定」なので pass と別扱いで落とす。
case "${status}" in
pass) ;;
skipped)
    fail "canary did not run (status=skipped): ${detail}
       skipped は合格ではなく未判定。${SETTINGS} に「実在する絶対パス」の
       deny が最低 1 件必要（相対パス deny だけでは probe が 0 件になる）"
    ;;
*)
    fail "bwrap could not start (status=${status}): ${detail}"
    ;;
esac

# canary が pass でも静的解析（deny パスの絶対 symlink 検査）が落ちていれば
# doctor は exit 1 を返す。canary 単体を見て通すと取りこぼすので rc も見る。
if [ "${rc}" != "0" ]; then
    fail "sandbox doctor reported failing deny targets (exit ${rc}): ${report}"
fi

log "pass - ${detail}"
exit 0
