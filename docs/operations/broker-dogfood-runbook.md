# broker dogfood 運用 runbook

`claude-org-runtime broker serve` は、renga-peers の代替となる **pure-backend 輸送層 (`org-broker`)** の daemon である。localhost の HTTP MCP サーバー + queue store + ナッジ配達を 1 プロセスで提供し、terminal adapter (tmux / WezTerm) を介して子ペインへナッジを注入する。本ドキュメントは Epic #6 Issue G(#515) の **本番 ja を `ORG_TRANSPORT=broker` で実走する前段**として、broker daemon の起動・停止・ライフサイクル・切戻しを運用手順に落としたものである。

設計 SoT は transport-lab `docs/design/ja-migration-plan.md` §5（ja 統合シーム）/ §5.5（併存・切戻し）/ §8 Issue G（dogfood ゲート）。契約面の正本は [`docs/contracts/backend-interface-contract.md`](../contracts/backend-interface-contract.md) Surface 8（broker auth & delivery、提案・批准待ち）。輸送両系の窓口運用差は [`CLAUDE.md`](../../CLAUDE.md)「輸送層（transport）両系」、spawn 儀式は [`.dispatcher/references/spawn-flow.md`](../../.dispatcher/references/spawn-flow.md) 3-3b を参照。

> **スコープと不可触制約**: 本 runbook は「実走を可能にする手順書」であり、**本番 ja の broker 実走（org-start ハイジャック）は後日のトラック 3（ユーザー hands-on）で行う**。本書の手順はすべて **テスト用 state-dir（`.state/broker/` ではない別ディレクトリ）** で daemon を起動・停止し、本番 `.state/` を汚さない前提で書く。**既定 `renga` は削除せず opt-in fallback として常時有効**（切戻しの安全装置）。

> **検証ステータス（2026-06-11、runtime 0.1.17 / tmux 3.2a / WSL2）**: 本書の起動・停止・ライフサイクル・dry-run の各コマンドは worker worktree 環境で実機検証済み。生ログの要点は各節に埋め込む。

---

## 1. 役割と前提

- **入力 / 制御**:
  - 環境変数 `ORG_TRANSPORT`（`renga` | `broker`、未設定 = 既定 `renga`）。daemon 自体は flag を読まないが、ja 側の生成器（§4）が flag に従って broker 面 allowlist を出す。
  - CLI 引数（`--port` / `--host` / `--state-dir` / `--backend` / `--no-nudge`、§2.1）。
- **出力 / 副作用**:
  - localhost HTTP MCP エンドポイント（既定 `http://127.0.0.1:48720/mcp`）。
  - queue store + JSONL journal（`<state-dir>/queue.jsonl`、既定 state-dir = `.state/broker`）。
  - 子ペインへのナッジ注入（terminal adapter 経由、`--no-nudge` で無効化）。
- **依存方向（一方向）**: `broker → terminal / dispatcher.choose_split`。**claude-org-ja は broker を import しない**（flag 既定 renga で不活性）。
- **CLI 名の注意（重要）**: 起動コマンドは **`claude-org-runtime broker serve`**（top-level CLI のサブコマンド）。`claude-org-runtime-broker` は CLI の `prog` 名（`--help` のヘッダ表記）であって **console_script は存在しない**。`python -m claude_org_runtime.broker serve` でも等価に起動できる。

```
$ claude-org-runtime broker --help
usage: claude-org-runtime broker [-h] {serve} ...
    serve     org-broker daemon を localhost で起動する (Ctrl+C で停止)。
```

---

## 2. broker daemon 起動の実機確認

### 2.1 `serve` のオプション

```
$ claude-org-runtime broker serve --help
usage: claude-org-runtime broker serve [-h] [--port PORT] [--host HOST]
                                       [--state-dir STATE_DIR]
                                       [--backend {wezterm,tmux}] [--no-nudge]
```

| オプション | 既定 | 意味 |
|---|---|---|
| `--port` | `48720`（`DEFAULT_PORT`） | localhost bind ポート。`0` で ephemeral（OS 採番、起動ログの `listening on` に実ポートが出る）。 |
| `--host` | `127.0.0.1` | bind host。設計上 localhost 専用。 |
| `--state-dir` | `.state/broker`（`DEFAULT_STATE_DIR`、CWD 相対） | `queue.jsonl` の書込先。**検証時は必ず別ディレクトリを渡す**（§2.3 / §7）。 |
| `--backend` | OS 自動選択（POSIX=`tmux` / Windows=`wezterm`） | terminal adapter。`VALID_BACKENDS = (wezterm, tmux)`。`--no-nudge` 時は無視。 |
| `--no-nudge` | （無効） | terminal adapter を生成せずナッジ配達を切る（**queue のみ**）。backend 非依存で疎通だけ見たいときに使う。 |

`serve` は前景でブロックする（`Ctrl+C` / `SIGINT` で停止）。起動時に手動検証用の token を 1 本発行し、`--mcp-config` に渡す JSON を標準出力に表示する:

```
org-broker listening on http://127.0.0.1:48803/mcp
queue store: /<state-dir>/queue.jsonl
manual test token: <token>
mcp-config: {"mcpServers": {"org-broker": {"type": "http", "url": "...", "headers": {"Authorization": "Bearer <token>"}}}}
```

### 2.2 起動 / 停止コマンド（本番形）

本番 ja での起動（トラック 3、ユーザー hands-on）の形は次のとおり。**本節はコマンド形の提示で、本書の検証では §2.3 のテスト用 state-dir 版だけを実行する**。

```bash
# 起動（既定 state-dir = .state/broker、tmux backend 自動選択）
claude-org-runtime broker serve

# 停止: 前景の serve に Ctrl+C（SIGINT）。journal に broker_stopped が 1 行残る。
# バックグラウンド起動した場合は PID へ SIGINT を送る:
#   kill -INT <pid>
```

### 2.3 テスト用 state-dir での起動→疎通→停止（本番 `.state` 不可侵の実証手順）

検証は **本番 `.state/broker/` を絶対に触らない**。一時ディレクトリを `--state-dir` に渡し、`queue.jsonl` がそのテストパスにのみ作られることを確認する。

> **cwd drift 注意（必須）**: `--state-dir` の既定は **CWD 相対** `.state/broker`。worker worktree と canonical な claude-org root では `.state/` が別物なので、相対パスを直接叩くと「どの `.state` を見ているか」が曖昧になり、誤った不可侵チェック / 本番 `.state` 汚染を招く。本書では **canonical root を絶対パス変数 `CANON_ROOT` で固定し、テスト state-dir も repo 外の絶対パス変数 `TEST_STATE` で固定**して、相対 `.state/broker` を素手で叩かない。

```bash
# 0) 前提変数を固定（相対パスを素手で叩かない）
CANON_ROOT=/home/happy_ryo/work/org/claude-org-ja   # 本番 .state/broker を持つ canonical root（環境に合わせる）
TEST_STATE=/tmp/claude/broker-smoke-A               # テスト用 state-dir（必ず repo 外の絶対パス）

# 1) テスト用 state-dir を用意（親ディレクトリ作成 + 既存ログ混入を避けるため未使用パスを使う）
mkdir -p "$TEST_STATE"
test -e "$TEST_STATE/queue.jsonl" && echo "WARN: 既存 queue.jsonl あり。別パスを使うか退避してから検証する" 

# 2) 起動（--no-nudge で backend 非依存に疎通だけ確認。-u で stdout を即時 flush）
python3 -u -m claude_org_runtime.broker serve \
    --state-dir "$TEST_STATE" --port 48799 --no-nudge
```

別ターミナル（または driver スクリプト）から、起動ログに出た token で HTTP MCP を叩く:

| ステップ | 期待 |
|---|---|
| `initialize` | `serverInfo = {"name": "org-broker", "version": "0.1.0"}` + `Mcp-Session-Id` ヘッダ採番 |
| `tools/list`（worker token） | `["check_messages", "list_peers", "send_message", "set_summary"]` の **messaging 4 面のみ**（tier gating、§3.4） |
| `tools/call send_message`（自分宛） | `{"ok": true, "delivered_to": "manual-test"}` |
| `tools/call check_messages` | 直前に送った `hello broker` を at-most-once で drain |

停止は serve に `SIGINT` を送る。**clean shutdown なら exit code 0**。

**`.state` 不可侵の確認（必須）**: 検証後に本番 `.state/broker/` が生成されていないことを確認する。queue は渡したテストパスにのみ書かれる。

```bash
# queue は TEST_STATE 配下にだけ存在する
ls "$TEST_STATE/queue.jsonl"
# 本番側（canonical root の絶対パス）が未生成であること。相対 .state/broker は使わない
test -e "$CANON_ROOT/.state/broker" && echo "NG: 本番 .state/broker が汚れた" || echo "OK: 本番 .state は不変"
# 現在の worktree 直下にも検証ゴミが落ちていないこと（CWD 相対既定の取り違え防止）
test -e "$PWD/.state/broker" && echo "NG: worktree 直下に .state/broker が生成" || echo "OK: worktree 直下も不変"
```

> **検証ログ（2026-06-11、実機）**: `--no-nudge` / `--backend tmux` の両系で `initialize → tools/list → send_message → check_messages` 往復が成功し、`SIGINT` で **exit 0**。`tools/list` は worker tier で messaging 4 面のみ。本番 `.state/broker/` は未生成（テストパスにのみ `queue.jsonl`）。tmux backend は live tmux server なしでも adapter が遅延生成され、起動・停止が成立する（ナッジを実際に注入する子ペインが無いので messaging probe は skip）。

---

## 3. start / stop / token / queue ライフサイクル

broker の内部状態遷移は `claude_org_runtime/broker/` の `server` / `store` / `tokens` / `surface` に分かれる。運用上把握すべき流れは次の 4 つ。

### 3.1 token 発行（`tokens.py`）

- spawn 時に **per-agent token** を 1 本発行する（`issue_token`、`secrets.token_urlsafe(32)`）。token ↔ `AgentBind`（`agent_id` / `name` / `role` / `auth_role` / `pane_id` / `cwd` / `kind`）。
- **`role`（表示専用、`set_pane_identity` で可変）と `auth_role`（不変の権限 tier、発行時確定）を分離**している。tier gating は `auth_role` のみで決め、表示 role の自己申告では昇格できない。spawn 子の `auth_role` は呼出元 tier で上限を切る（`capped_auth_role`）。
- `mcp_config_for(token)` が `--mcp-config` に渡す JSON を生成する（token を static header `Authorization: Bearer <token>` に埋める。env 参照 `${VAR}` は使わない）。
- journal: `token_issued`。

### 3.2 登録（`server.py` の HTTP handler）

- 子ペインの Claude / Codex が `initialize`（MCP）に到達した時点で `AgentBind.registered = True` になる（`registered_at` 記録）。**登録済み bind のみが配送先**になる（未接続 / DELETE 済み client への配送を防ぐ）。
- journal: `agent_registered`。

### 3.3 queue store + ナッジ配達（`store.py` / `server.py`）

- `send_message`（`enqueue`）は **token 由来の帰属**で entry を作る（自己申告不可）。宛先の registered 確認と queue append を**同一ロックスコープ**で原子的に行い、その後にロック外で `_journal` と `_trigger_nudge` を呼ぶ（queue 永続化と PTY 注入を結合させない / 非再入 Lock の二重取得デッドロック回避）。
- ナッジ配達は **定型 1 行のみ PTY 経由**で注入し、本文は通さない（受信側は `check_messages` で pull 取得 = push→pull モデル）。adapter 不通や対象未着のときは `nudge_defer_interval`（既定 2.0s）× `nudge_defer_max_tries`（既定 30）まで再試行する。
- `check_messages`（`drain`）は **at-most-once** で queue を空にして返す。
- journal: `message_enqueued` → `nudge_sent` / `nudge_deferred` / `nudge_failed` → `queue_drained`。

### 3.4 tier gating（`surface.py`）

公開面は `auth_role` で**構造的に**変わる（default-deny allowlist）。`tools/list` に出ないツールは呼んでも `[tool_not_authorized]` で弾かれる（allowlist は二重防御の片側）。

| auth_role tier | 公開面 |
|---|---|
| worker / curator / 未知 | messaging 4（`send_message` / `check_messages` / `list_peers` / `set_summary`） |
| dispatcher | messaging 4 + ops（`list_panes` / `inspect_pane` / `send_keys` / `poll_events` / `close_pane` / `set_pane_identity` / `spawn_claude_pane` / `spawn_codex_pane`） |
| secretary | dispatcher の面 + `spawn_pane`（secretary 専用） |

> `new_tab` / `focus_pane` は broker surface に**無い**（意図的除外）。初期 surface = 移植 12 面 + `spawn_codex_pane` = 13 面。

### 3.5 停止 / 失効

- daemon 停止: `stop()` が HTTP server を shutdown + close し、journal に `broker_stopped` を残す。
- session 終了（MCP `DELETE`）: 当該 bind の `session_id` を失効させ、`registered = False` に落とす（切断済み client を `list_peers` / 配送先に残さない）。journal: `session_closed`。
- pane クローズ（`close_pane`）: adapter で kill 後、registry pop と token revoke を 1 ロックスコープで原子的に行う。journal: `pane_closed` + event `pane_exited`。

### 3.6 journal イベント一覧（`queue.jsonl`）

`<state-dir>/queue.jsonl` に 1 行 1 JSON で追記される。運用での観測点:

```
broker_started → token_issued → agent_registered → message_enqueued
  → nudge_sent / nudge_deferred / nudge_failed → queue_drained
  → session_closed / pane_closed → broker_stopped
```

> **検証ログ（実機、messaging 往復）**: `broker_started → token_issued → agent_registered → message_enqueued(chars=12) → queue_drained(count=1) → broker_stopped` を 1 サイクルで確認。

### 3.7 broker 追加エラーコード

renga コードに加え broker は次を返しうる。窓口 / ディスパッチャーは未知コードを default-branch で escalate に流す（[`CLAUDE.md`](../../CLAUDE.md)「エラー分岐」）。

| コード | 契機 |
|---|---|
| `[token_invalid]` | Bearer token が bind 表に無い / revoked（HTTP 401、JSON-RPC -32001） |
| `[session_invalid]` | `initialize` 前に他メソッドを呼んだ |
| `[tool_not_authorized]` | auth_role tier の公開面外のツールを呼んだ |
| `[no_backend]` | terminal adapter 不在（`--no-nudge` 起動）で pane 操作を呼んだ（= adapter_unavailable） |
| `[nudge_failed]` | ナッジ注入が defer 上限まで届かなかった |
| `[peer_not_found]` | `send_message` の宛先が registered な bind に無い |
| `[name_taken]` | pane name の重複 |

---

## 4. `ORG_TRANSPORT=broker` での settings 再生成 dry-run

Epic #6 D/E で入った **transport descriptor 駆動の生成器**を `ORG_TRANSPORT=broker` で dry-run し、broker 面 allowlist が出ることを確認する。**実ファイルは書かない**。

### 4.1 単一 SoT（descriptor）

ja 側の transport アクセサ [`tools/transport.py`](../../tools/transport.py) は runtime の transport surface descriptor（`claude_org_runtime.transport`）を唯一の SoT として consume する（ハードコードしない）。解決順は **explicit 引数 > `ORG_TRANSPORT` env > 既定 `renga`**。allowlist 生成は `claude_org_runtime.settings.generator.transport_allowlist(role, transport=...)` 経由。

### 4.2 role 別 allowlist の dry-run

```bash
# 既定 renga（無設定）と broker の射影を role 別に比較（read-only、書込み無し）
for role in worker curator dispatcher secretary; do
  echo "--- $role renga(default) ---"
  python3 -c "from claude_org_runtime.settings.generator import transport_allowlist as t; print(t('$role'))"
  echo "--- $role broker ---"
  ORG_TRANSPORT=broker python3 -c "from claude_org_runtime.settings.generator import transport_allowlist as t; print(t('$role'))"
done
```

| role | renga（既定） | broker（`ORG_TRANSPORT=broker`） |
|---|---|---|
| worker / curator | `mcp__renga-peers__*` 14 面 | `mcp__org-broker__*` messaging 4 |
| dispatcher | `mcp__renga-peers__*` 14 面 | messaging 4 + ops 8（`spawn_pane` を含まない） |
| secretary | `mcp__renga-peers__*` 14 面 | messaging 4 + ops + `spawn_pane` + `spawn_codex_pane`（13） |

> renga 既定は全ロール同一 surface（14 面）を allowlist で絞るモデル。broker は role tier を**構造的に**遮断するため、allowlist は二重防御の片側になる（安全側）。

### 4.3 `~/.claude/settings.json` の user_common allowlist 再生成 dry-run

[`tools/org_setup_prune.py`](../../tools/org_setup_prune.py) `--user-common-allowlist` は user_common（`~/.claude/settings.json`）の MCP `permissions.allow` を active transport へ射影する。**検証では実 `~/.claude/settings.json` を触らないよう `--user-common-settings-path` でテスト用パスに向け、`--dry-run` を付ける**。

```bash
# テスト用 settings（renga エントリ入り）を用意して dry-run
TEST_SET=/tmp/claude/usercommon-settings.json   # 実 ~/.claude/settings.json ではない

# renga messaging エントリ入りのテスト settings を作る（空/不存在だと drop renga の期待出力にならない）
mkdir -p "$(dirname "$TEST_SET")"
cat > "$TEST_SET" <<'JSON'
{
  "permissions": {
    "allow": [
      "Bash(git status:*)",
      "mcp__renga-peers__send_message",
      "mcp__renga-peers__check_messages",
      "mcp__renga-peers__list_peers",
      "mcp__renga-peers__set_summary"
    ]
  }
}
JSON

# 既定 renga: strict no-op（ファイルは一切触らない）
python3 tools/org_setup_prune.py --user-common-allowlist --dry-run \
    --user-common-settings-path "$TEST_SET"

# broker: renga-peers を drop、org-broker messaging tier を保証（dry-run は表示のみ）
ORG_TRANSPORT=broker python3 tools/org_setup_prune.py --user-common-allowlist --dry-run \
    --user-common-settings-path "$TEST_SET"
```

期待出力:

```
# renga（既定）
[org_setup_prune] user_common allowlist: transport=renga (既定); no-op — ~/.claude/settings.json は不変 ...

# broker
=== user_common allowlist (transport=broker): /tmp/claude/usercommon-settings.json ===
  - mcp__renga-peers__send_message      （以下 renga messaging を drop）
  + mcp__org-broker__send_message       （以下 org-broker messaging を add）
  ...
```

> **検証ログ（実機）**: 既定 renga は strict no-op（テストファイル 1 byte も不変）。`ORG_TRANSPORT=broker` で renga messaging 4 → org-broker messaging 4 の差分を dry-run 表示。**`--dry-run` のため実書込みゼロ**（テストファイル内容の不変を確認済み）。`Bash(...)` 等の非 MCP エントリは順序を保って残る。

---

## 5. 切戻し 5 条件の具体コマンド化（SoT §5.5）

`ORG_TRANSPORT=broker` → `renga` への完全な切戻しは、flag 戻しだけでは**実行中の broker-spawned ペインが即座には復帰しない**（`--mcp-config` / pull 前提の prose を抱えたまま）。SoT §5.5 の **5 完了条件**を順に実行する。

> **前提変数（cwd drift 回避）**: 以下のコマンドは相対 `.state/broker` を素手で叩かない。daemon が `serve --state-dir` で実際に使った state-dir を絶対パス変数で固定し、canonical root も明示する。本番反映（トラック 3）では `BROKER_STATE` が本番 `.state/broker` を指す。
>
> ```bash
> CANON_ROOT=/home/happy_ryo/work/org/claude-org-ja   # canonical root（環境に合わせる）
> BROKER_STATE="$CANON_ROOT/.state/broker"            # daemon が serve 時に渡した --state-dir
> ```

### (1) flag 戻し

```bash
# env を renga（既定）へ戻す。次に spawn される pane から renga に向く。
unset ORG_TRANSPORT
# 永続シェル設定に書いていた場合はそこからも除去する:
#   grep -rn "ORG_TRANSPORT" ~/.bashrc ~/.zshrc ~/.profile
```

**チェック**: `python3 -c "from claude_org_runtime.transport import resolve_transport as r; print(r())"` が `renga` を返す。

### (2) 生成物の再生成（renga allowlist へ）

flag が renga に戻れば**生成器（role 別 `settings.local.json`）は恒等（bit 等価）**に戻る。生成物を実際に再生成して renga 面に戻す。

```bash
# まず dry-run で差分確認（broker 面が残っていれば renga へ戻す差分が出る）
python3 tools/org_setup_prune.py --all --dry-run

# 問題なければ適用（renga allowlist を書き戻す。.bak が残る）
python3 tools/org_setup_prune.py --all
```

**user_common（`~/.claude/settings.json`）は別扱い（重要）**: `--user-common-allowlist` は **renga モードでは完全 no-op**（renga allowlist の SoT は org-setup スキル + permissions.md であってこのツールではないため、ファイルに一切触れない）。したがって dogfood で broker を適用済み（`mcp__org-broker__*` が user_common に入っている）の場合、`--user-common-allowlist --dry-run` を renga で回しても **broker 面は戻らない**。user_common は以下のいずれかで明示的に戻す:

```bash
# 方法 A（推奨）: broker 適用時に作られた .bak を復元する
#   backup 命名は settings.json.bak.<YYYYMMDD-HHMMSS>（backup_path）
ls -t ~/.claude/settings.json.bak.* 2>/dev/null | head     # 直近の backup を確認
# cp <確認した .bak> ~/.claude/settings.json               # 内容を目視確認のうえ復元

# 方法 B: backup が無い場合は messaging 面を手動 swap（org-broker → renga-peers）
#   ~/.claude/settings.json の permissions.allow 内
#   "mcp__org-broker__{send_message,check_messages,list_peers,set_summary}" を
#   "mcp__renga-peers__..." に置換する（非 MCP エントリは触らない）
```

**チェック**: role 別 `settings.local.json` と **user_common（`~/.claude/settings.json`）の両方**に `mcp__org-broker__*` が残っていないこと。

```bash
# repo 配下の role 別 settings。glob (*/.claude/) は hidden role dir
# (.dispatcher/.claude/ / .curator/.claude/ 等) を拾わず、zsh では no-match で
# grep 自体が走らず誤って OK になる。glob を使わず repo root から再帰 grep する
# (grep -r は hidden dir も降りる)。settings*.json に限定して誤検出を避ける。
if grep -rl --include="settings*.json" "mcp__org-broker__" . 2>/dev/null | grep -q .; then
  echo "NG: repo 側に broker 面が残存:"; grep -rl --include="settings*.json" "mcp__org-broker__" . 2>/dev/null
else
  echo "OK: repo 側 broker 面なし"
fi
# user_common（ホームの settings.json）も忘れず確認する
grep -l "mcp__org-broker__" ~/.claude/settings.json 2>/dev/null && echo "NG: user_common に broker 面が残存" || echo "OK: user_common broker 面なし"
```

### (3) active な broker ペインの respawn（renga 経路で再起動）

実行中の broker-spawned ペインは flag 戻しでは復帰しない。renga 経路で suspend/resume または respawn する。

```bash
# 現状の broker ペインを把握（renga 窓口/ディスパッチャーから）
#   mcp__renga-peers__list_panes  でペイン一覧を確認
# broker token を抱えたペインを順に close → renga 経路で再 spawn（org-delegate の通常委譲フロー）
# pane control は dispatcher/secretary に閉じるため、messaging を先に renga へ戻してから pane を後追いする（§5.5 の 2 段）。
```

**チェック**: `list_peers` / `list_panes` に broker bind のペインが残っていない。

### (4) broker daemon の停止順序（残ペイン revoke → daemon stop）

**順序が重要**: 先に残ペインを revoke（close）して配送先から外し、最後に daemon を止める。

```bash
# 1) 残っている broker ペインを close（token revoke される。close_pane の journal: pane_closed）
#    renga/dispatcher から各 broker ペインを close_pane する。
# 2) すべて revoke したら daemon を停止（前景 serve に SIGINT、または）
kill -INT <broker_pid>
# 3) journal 末尾に broker_stopped が記録されることを確認
tail -n 3 "$BROKER_STATE/queue.jsonl"
```

### (5) 旧 token / queue store の破棄確認（`.state/broker/` の未読・bind 残存なし）

```bash
# 未読（enqueue されたが drain で消されていない message）が残っていないか journal を突合する。
# queue_drained は count=N を持つので「イベント件数」ではなく N の総和で比較する（複数 drain の誤判定回避）。
BROKER_STATE="${BROKER_STATE:?BROKER_STATE を先に固定する（§5 前提変数）}" \
python3 - <<'PY'
import json, os
p = os.path.join(os.environ["BROKER_STATE"], "queue.jsonl")
enq = drained_msgs = 0
try:
    for line in open(p, encoding="utf-8"):
        rec = json.loads(line); ev = rec.get("event")
        if ev == "message_enqueued": enq += 1
        if ev == "queue_drained": drained_msgs += int(rec.get("count", 0))  # N を合算
except FileNotFoundError:
    print("OK: queue.jsonl が無い（破棄済み）"); raise SystemExit
unread = enq - drained_msgs
print(f"enqueued={enq} drained_msgs={drained_msgs} unread={unread}")
print("OK: 未読なし" if unread <= 0 else f"NG: 未読 {unread} 件が残存（daemon 停止前に drain される必要）")
PY

# token / bind はプロセス内 in-memory（daemon 停止で消える。永続化されない）。
# queue store ファイルを破棄して跡を残さない（rm 不可環境では truncate / アーカイブ）:
#   mv "$BROKER_STATE" "$BROKER_STATE.archived-$(date +%Y%m%d)"   # または運用ルールに従い削除
```

> **token / bind の永続性**: `AgentBind` は daemon プロセスの in-memory のみ（journal には `token_issued` の事実は残るが token 値・bind 表は永続化されない）。daemon を停止すれば bind は消える。残るのは `queue.jsonl`（journal + 未 drain message）だけなので、(5) はこのファイルの未読突合と破棄に閉じる。

---

## 6. 課金中立 attestation の取り方

broker が spawn する全エージェントが **対話 TUI（ヘッドレス不可）**であることを実 argv で確認する。これは課金中立（API 課金が走る `claude -p` / `codex exec` 等の非対話起動をしていない）の証跡になる。

### 6.1 多層防御の構造（spawn 時の guard）

broker の課金中立は **spawn 時の default-deny allowlist** で構造的に保証されている（`surface.py`）:

- `build_claude_argv` / `build_codex_argv` が対話 TUI 用 flag のみ許可し、`_guard_interactive_claude_argv` / `_guard_interactive_codex_argv` で **allowlist 外 token（flag 後サブコマンド / bare positional / `--` / 未知 flag / headless flag）を一律拒否**する。
- claude 側 headless blacklist: `-p` / `--print` / `--headless` / `--output-format` / `--input-format` 等。codex 側はサブコマンド（`exec` / `review` / `*-server` / `apply` / `sandbox` 等）が bare positional として落ちる。
- 値を取る flag は arity を持たせ（値位置の headless flag も二段で弾く）、`argv[0]` は basename 判定（絶対パス起動を false-reject しない）。

### 6.2 実 argv 検査（runtime attestation）

本番ホスト（broker ペインが live なセッション）で、実際に走っている argv を ps で検査する。**ヘッドレス flag / サブコマンドが 1 つも無いこと**を確認する。

**broker-spawned ペインに対象を絞る**のが要点。ホスト上には本 attestation と無関係な headless 実行（CI / 手動 `claude -p` 等）が並走しうるので、全 claude/codex を無差別に grep すると false positive を拾い、逆に対象識別漏れも起きる。broker が spawn したプロセスは argv に **`--mcp-config` で broker の MCP config（`org-broker` を含む）を抱える**ので、これで母集合を絞る。

```bash
# 1) broker-spawned に限定して argv を列挙（--mcp-config に org-broker を含むものだけ）
ps -eo pid,args | grep -iE "(^| )(claude|codex)( |$)" | grep -v grep \
  | grep -- "--mcp-config" | grep -i "org-broker"

# 2) 課金中立の negative check: 上で絞った broker ペインの argv に headless / exec 系が無いこと
ps -eo args | grep -iE "(^| )(claude|codex)( |$)" | grep -v grep \
  | grep -- "--mcp-config" | grep -i "org-broker" \
  | grep -nE -- "-p( |$)|--print|--headless|--output-format|--input-format| exec | review |--mcp-server" \
  && echo "NG: broker ペインに headless/exec flag を検出（課金が走る起動）" \
  || echo "OK: broker ペインに headless/exec flag なし（対話 TUI = 課金中立）"

# 3) 母集合の突合（任意・推奨）: list_panes の broker bind ペイン数と (1) の件数が一致することを確認
#    （ディスパッチャー/窓口の list_panes と pid を突合し、識別漏れ・余剰を検出する）
```

期待: 各 broker ペインの argv が `--mcp-config <broker>` / `--model` / `--permission-mode` 等の **対話 flag のみ**で構成され、negative check が `OK` を返す。

> **注意**: ps の検査は **broker ペインが live なホストセッション**で行う（PID namespace を分離した sandbox 内からは実ペインが見えない）。spawn 時の guard（§6.1）が一次防御、ps による runtime attestation が二次確認という二段で課金中立を担保する。`--mcp-config` でのフィルタは broker ペインの構造的特徴に基づく一次絞り込みであり、厳密性が要るときは (3) の `list_panes` 突合で母集合の過不足を閉じる。

---

## 7. 検証ゴミの cleanup（条件 (5) の dogfooding）

本 runbook の検証で作ったテスト state は **repo 外のテスト用ディレクトリ**に閉じており、本番 `.state/broker/` は生成しない。検証後は §5(5) の手順を**テストパスに対して**実行し、跡を残さない。

```bash
CANON_ROOT=/home/happy_ryo/work/org/claude-org-ja   # canonical root（環境に合わせる）

# 検証で使ったテスト state-dir を確認（repo 外であること）
ls -d /tmp/claude/broker-smoke-* /tmp/claude/usercommon-settings.json 2>/dev/null

# journal の未読突合（§5(5) のスクリプトを BROKER_STATE=テストパスに向けて実行）→ 問題なければ破棄
# （/tmp 配下は ephemeral。運用ルールに従いアーカイブまたは削除）

# 本番 .state/broker が未生成であることの最終確認（canonical root の絶対パス + 現 worktree 直下の両方）
test -e "$CANON_ROOT/.state/broker" && echo "NG: 本番 .state/broker が存在" || echo "OK: 本番 .state は不変"
test -e "$PWD/.state/broker" && echo "NG: worktree 直下に .state/broker が存在" || echo "OK: worktree 直下も不変"
```

---

## 8. 関連

- 設計 SoT: transport-lab `docs/design/ja-migration-plan.md` §5（統合シーム）/ §5.5（併存・切戻し）/ §8 Issue G（dogfood ゲート）
- 契約: [`docs/contracts/backend-interface-contract.md`](../contracts/backend-interface-contract.md) Surface 8（broker auth & delivery、提案・批准待ち）
- 輸送両系の窓口運用差: [`CLAUDE.md`](../../CLAUDE.md)「輸送層（transport）両系」
- spawn 儀式（dev-channel 承認 → folder-trust 承認）: [`.dispatcher/references/spawn-flow.md`](../../.dispatcher/references/spawn-flow.md) 3-3b
- transport アクセサ（ja 側単一シーム）: [`tools/transport.py`](../../tools/transport.py)
- user_common allowlist 射影: [`tools/org_setup_prune.py`](../../tools/org_setup_prune.py) `--user-common-allowlist`
- attention watcher の運用文体: [`attention-watch.md`](attention-watch.md)
