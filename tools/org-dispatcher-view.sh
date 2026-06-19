#!/usr/bin/env bash
# org-dispatcher-view.sh — dispatcher の「自己修復する read-only ビュー」
# ============================================================================
# 目的:
#   窓口（secretary）の隣のペインでこれを 1 回起動しておくと、control plane
#   （dispatcher のペイン）を常に視界に保てる。dispatcher が restart したり
#   auto-compact fork で tmux セッション名が変わっても、本スクリプトが自動で
#   再探索・再 attach し直すので、手動 attach の貼り直しが不要になる（= 自己修復）。
#
# 仕組み（純 tmux 役割解決 — broker MCP / HTTP には一切依存しない）:
#   broker(tmux) backend では各ペインが独立した detached tmux session
#   （`claude-org-broker-{pid}-{seq}`）として存在する。dispatcher のペインは
#   **cwd が dispatcher ディレクトリ（パス basename が `.dispatcher`）** で起動する
#   一方、worker のペインは cwd が worktree ディレクトリ（`.worktrees/...`）、
#   secretary は logical pane で broker socket に出現しない。よって
#   「pane の path basename が `.dispatcher` のセッション」を選べば、worker や
#   secretary を誤選択せず dispatcher だけを役割解決できる。
#
# 使い方:
#   tools/org-dispatcher-view.sh          # read-only attach（既定・安全）
#   tools/org-dispatcher-view.sh --rw     # 読み書き attach（誤打鍵注意）
#   tools/org-dispatcher-view.sh --help   # この usage
#
# 環境変数:
#   ORG_BROKER_SOCKET   broker の tmux socket 名（既定: claude-org-broker。
#                       runtime の BROKER_SOCKET 既定に一致）
#
# スコープ外（重要）:
#   - 本スクリプトは **broker の tmux backend 専用**。broker の Windows backend は
#     **wezterm**（tmux ではない）ので本スクリプトは適用外。wezterm 版の同等品は
#     follow-up とする（本スクリプトでは実装しない）。
#   - broker daemon の HTTP / MCP は一切叩かない（純 tmux で役割解決する）。
#   - renga フレーム（単一画面タイリング）では「detached session へ attach し直す」
#     概念が写像しないので適用外。renga では画面そのものを直接見ればよい。
#
# 注意:
#   `tmux` は zsh + oh-my-zsh の tmux プラグインで alias 化けするため、本スクリプト内の
#   全 tmux 呼び出しは実体パス `/usr/bin/tmux` を使う。
#
#   終了方法（重要）: attach 中の Ctrl-C は **tmux クライアント / dispatcher ペイン側** に
#   渡るため、本ビューワーの SIGINT trap には届かない（--rw では dispatcher へ ^C を送って
#   しまう）。本ビューワーを止めるには、まず `Ctrl-b d` で **detach** してから、再探索プロンプト
#   に戻ったところで Ctrl-C を押す（detach 中 / degraded 中 / socket 不通中の sleep ループでは
#   Ctrl-C が trap に届きクリーンに終了する）。busy-loop は各分岐の sleep で防いでいる。
# ============================================================================

set -u

# --- 設定 -------------------------------------------------------------------
TMUX_BIN="/usr/bin/tmux"
SOCKET="${ORG_BROKER_SOCKET:-claude-org-broker}"
# dispatcher を識別する path basename（pane の cwd の末尾要素）。
DISPATCHER_DIR_BASENAME=".dispatcher"
# 再探索の sleep（degraded / socket 不通時）。busy-loop 防止。
RETRY_SLEEP=2

# --- 純ロジック: dispatcher セッションを役割解決する -------------------------
# 標準入力に tmux list-panes の出力（1 行 = `<session>\t<path>`）を食わせ、
# path basename が `.dispatcher` のセッション名を「最初の 1 つ」だけ stdout へ印字する。
# 複数ヒット時は 1 件目を採用し、警告を stderr に出す。1 件も無ければ何も印字せず 1 を返す。
#
# tmux 依存を持たない pure な関数なので、固定文字列を流し込んで単体テストできる。
# 第 1 引数 = dispatcher を識別する basename（省略時は $DISPATCHER_DIR_BASENAME）。
resolve_dispatcher_session() {
	local want="${1:-$DISPATCHER_DIR_BASENAME}"
	local session path base
	local found=""
	local hits=0
	# IFS=tab で 2 列に分割。path は空文字（pane_current_path が不安定で空のとき）も
	# あり得るので、その行は skip する（呼び出し側で current/start のフォールバックを
	# 合成して渡す前提）。
	while IFS="$(printf '\t')" read -r session path; do
		[ -n "$session" ] || continue
		[ -n "$path" ] || continue
		# 末尾スラッシュを剥がしてから basename を取る（`/foo/.dispatcher/` 対策）。
		path="${path%/}"
		base="${path##*/}"
		if [ "$base" = "$want" ]; then
			hits=$((hits + 1))
			if [ -z "$found" ]; then
				found="$session"
			fi
		fi
	done

	if [ "$hits" -gt 1 ]; then
		# 同一 broker socket 上に複数 org / 複数 .dispatcher ペインがある状況。
		# 仕様どおり 1 件目を採用するが、目的外の dispatcher へ attach しうるので強めに警告する。
		printf '警告: dispatcher 候補が %d 件見つかりました（複数 org が同一 socket に同居？）。最初の 1 つ (%s) を採用します。意図したものか確認してください。\n' \
			"$hits" "$found" >&2
	fi

	if [ -n "$found" ]; then
		printf '%s\n' "$found"
		return 0
	fi
	return 1
}

# --- tmux からペイン一覧を取り、role 解決まで一気にやる ----------------------
# 成功時: 解決した session 名を stdout へ。返り値 0。
# socket 不通: 返り値 2（呼び出し側で graceful retry）。
# dispatcher 不在: 返り値 1。
discover_dispatcher() {
	local raw rc
	# pane_current_path が空になる tmux 実装があるため、current が空なら start に
	# フォールバックする式を tmux 側で合成して頑健にする。
	#   #{?cond,then,else} — cond が真なら then、偽なら else
	#   #{==:a,b}          — a と b の文字列等価
	# current が "" なら start を、そうでなければ current を採用。
	raw="$(
		"$TMUX_BIN" -L "$SOCKET" list-panes -a \
			-F '#{session_name}	#{?#{==:#{pane_current_path},},#{pane_start_path},#{pane_current_path}}' \
			2>&1
	)"
	rc=$?
	if [ "$rc" -ne 0 ]; then
		# socket 不通（broker daemon 不在）等。本文を呼び出し側のログに回す。
		printf '%s\n' "$raw" >&2
		return 2
	fi
	# 'error connecting to ...' は tmux が rc!=0 にしないケースもあるので二重で弾く。
	case "$raw" in
		*"error connecting to"*)
			printf '%s\n' "$raw" >&2
			return 2
			;;
	esac
	resolve_dispatcher_session "$DISPATCHER_DIR_BASENAME" <<EOF
$raw
EOF
}

# --- usage -------------------------------------------------------------------
usage() {
	cat <<'EOF'
org-dispatcher-view.sh — dispatcher の自己修復 read-only ビュー（broker/tmux 専用）

USAGE:
  tools/org-dispatcher-view.sh [--rw]
  tools/org-dispatcher-view.sh --help

無限ループで dispatcher の broker tmux セッションを純 tmux 役割解決し、
見つかれば read-only attach する。restart / auto-compact fork でセッションが
変わっても自動で再探索・再 attach する（self-healing）。

OPTIONS:
  --rw          読み書き attach（-r を外す）。誤打鍵で control plane を壊しうるので注意。
  -h, --help    この usage を表示して終了。

ENV:
  ORG_BROKER_SOCKET   broker tmux socket 名（既定: claude-org-broker）

attach 中のキー操作 / 終了:
  Ctrl-b d      デタッチ（自分だけ抜ける。dispatcher は生き続ける）
  終了するには   まず Ctrl-b d で detach し、再探索プロンプトに戻ってから Ctrl-C を押す。
                （attach 中の Ctrl-C は tmux / dispatcher ペイン側に渡り、本ビューワーは
                 止まらない。--rw では dispatcher へ ^C を送ってしまうので特に注意）

注意:
  broker の Windows backend は wezterm（tmux でない）ため本スクリプトは tmux backend 専用。
  broker daemon の HTTP / MCP は叩かない（純 tmux 役割解決）。
EOF
}

# --- 引数パース --------------------------------------------------------------
ATTACH_RO=1 # 既定は read-only (-r)
for arg in "$@"; do
	case "$arg" in
		--rw)
			ATTACH_RO=0
			;;
		-h | --help)
			usage
			exit 0
			;;
		*)
			printf '不明な引数: %s\n\n' "$arg" >&2
			usage >&2
			exit 2
			;;
	esac
done

# --- SIGINT で綺麗に終了 -----------------------------------------------------
RUNNING=1
on_sigint() {
	RUNNING=0
	printf '\norg-dispatcher-view を終了します。\n' >&2
	exit 0
}
trap on_sigint INT

# --- メインループ ------------------------------------------------------------
if [ "$ATTACH_RO" -eq 0 ]; then
	printf '※ --rw: 読み書き attach です。dispatcher ペインへ誤入力すると control plane を壊しうるので注意。\n' >&2
fi
printf 'org-dispatcher-view 起動（socket=%s, mode=%s）。終了は detach (Ctrl-b d) 後にプロンプトで Ctrl-C。\n' \
	"$SOCKET" "$([ "$ATTACH_RO" -eq 1 ] && echo read-only || echo read-write)" >&2

while [ "$RUNNING" -eq 1 ]; do
	session="$(discover_dispatcher)"
	rc=$?

	if [ "$rc" -eq 2 ]; then
		# socket 不通（broker daemon 不在）。即死せず retry。
		printf 'broker tmux socket (%s) に繋がりません（broker daemon 未起動の可能性）。再試行中…\n' \
			"$SOCKET" >&2
		sleep "$RETRY_SLEEP"
		continue
	fi

	if [ "$rc" -ne 0 ] || [ -z "$session" ]; then
		# dispatcher が degraded（bg-pty）/ 未起動で tmux ペインが無い。
		printf 'dispatcher の tmux ペインが見つかりません（degraded / 未起動）。再探索中…\n' >&2
		sleep "$RETRY_SLEEP"
		continue
	fi

	# attach 直前のヘッダ（何に attach しているか）。
	mode_label="$([ "$ATTACH_RO" -eq 1 ] && echo 'read-only' || echo 'read-write')"
	printf '>>> dispatcher を発見: session=%s に %s attach します（抜ける: Ctrl-b d、その後プロンプトで Ctrl-C で終了）\n' \
		"$session" "$mode_label" >&2

	# read-only attach は detach / セッション死亡までブロックする。
	# 抜けたら（restart/fork でセッションが変わった）ループ先頭へ戻り自動で再解決。
	if [ "$ATTACH_RO" -eq 1 ]; then
		"$TMUX_BIN" -L "$SOCKET" attach -r -t "$session"
	else
		"$TMUX_BIN" -L "$SOCKET" attach -t "$session"
	fi

	# attach から抜けてきた。短い sleep を挟んで busy-loop を防ぎつつ再探索。
	printf '--- attach が終了しました（detach / セッション変化）。再探索します…\n' >&2
	sleep "$RETRY_SLEEP"
done
