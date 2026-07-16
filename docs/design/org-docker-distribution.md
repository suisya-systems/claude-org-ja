# 組織一式の Docker image 配布 — 設計

> ステータス: **設計 + PoC 骨格**（task org-docker-image-design-001）。実装前 Codex design review（Blocker 4 / Major 7 / Minor 3 / Nit 3）を全件織り込み済み。PoC 実体は [`docker/`](../../docker/) 配下（Dockerfile / compose.yaml / entrypoint.sh / Dockerfile.dockerignore / README.md ほか）。
>
> 対象読者: image をビルド・運用する人間、および docker/ を保守するワーカー。
>
> 一次入力:
> - 実装前 Codex design review 全文（knowledge 側 `tmp/codex-review-org-docker-image-design-001.md`、ja repo 外）
> - [`.claude/skills/org-start/SKILL.md`](../../.claude/skills/org-start/SKILL.md)（起動契約）/ [`.claude/skills/org-setup/SKILL.md`](../../.claude/skills/org-setup/SKILL.md)（設定配置）
> - [`docs/contracts/backend-interface-contract.md`](../contracts/backend-interface-contract.md) Surface 8（broker auth & delivery / push 一次）
> - knowledge/curated/broker.md・herdr.md（ja 運用リポジトリ側）、runtime CHANGELOG 0.1.33–0.1.36

---

## 1. 目的とスコープ

**目的**: claude-org-ja の組織一式（Claude Code CLI・claude-org-runtime venv・broker daemon・tmux / herdr 両ターミナルバックエンド・スキル群・dashboard）をセットアップ済み Docker image として配布可能にする。ターゲットは x86_64 に加えて **ARM64（Raspberry Pi 5 16GB）** のマルチアーキ。

**スコープ**:
- image に焼き込むもの / 焼き込まないものの境界の確定（§3・§6）
- コンテナ内プロセス監督の契約化（§5）
- 永続化境界（volume 設計、§6）
- 初回起動導線（認証は image から分離し、初回に人間が対話で通す。§10）
- マルチアーキビルド（§11）
- PoC: ローカルでビルド・起動確認できる骨格（§12）

**非スコープ**:
- 認証の完全自動化（Claude / gh / Codex / Slack / Google MCP のトークン発行は人間の対話が前提）
- dashboard の認証機構の実装（公開境界の方針決定のみ。§7.4）
- Kubernetes 等 Docker Compose 以外のオーケストレータ対応
- CI での image 自動 publish（tag 運用の設計のみ。§7.7）

## 2. 前提事実（調査確定分）

設計判断の根拠となる、調査で確定した事実。

1. **起動契約**: `/org-start` は (0) `ORG_TRANSPORT` 判定 → MCP 疎通 → identity 検証 → workers_dir 確認 → 並列 Block A(dispatcher spawn)/B(state.db)/C(dashboard)/C2(runtime drift)/C3(queue watcher) → Block D 合流、の順で進む（[`.claude/skills/org-start/SKILL.md`](../../.claude/skills/org-start/SKILL.md)）。dispatcher / worker の spawn は broker daemon がペイン（tmux detached session / herdr pane）として起動し、folder-trust と dev-channel の 2 段承認プロンプトを orchestrator が `send_keys(enter=true)` で機械承認する。
2. **設定配置**: org-setup は `~/.claude/settings.json`（ユーザー共通）とロール別 `settings.local.json`（`.claude/` / `.dispatcher/.claude/` / `.curator/.claude/`、worker はワーカーディレクトリに動的生成）を配置する。Claude Code は起動ディレクトリの `.claude/` しか読まない。
3. **workers_dir**: `registry/org-config.md` の `workers_dir: ../workers`（repo からの相対）。repo を `/workspace/claude-org-ja` に置けば `/workspace/workers` に自然解決する。
4. **.state/ の永続 / 揮発**: 永続 = `state.db`（唯一の SoT）・`workers/`・`pending_decisions.json`・`notes/`・`attention.json`・dispatcher カーソル類。揮発 = `dashboard.pid` / `dashboard.log`・`broker/`（daemon.json の pid/port、admin.token、queue.jsonl、secretary-mcp.json、herdr_*）・`pr-watch-*.log`・pid sidecar 類。
5. **broker daemon**: `claude-org-runtime broker serve --port(既定 48720) --host(既定 127.0.0.1) --state-dir --backend {wezterm,tmux,herdr}`。上位コマンド `claude-org-runtime org up` は daemon 確保（健全なら再利用）+ secretary TUI 起動。tmux backend は専用 socket `claude-org-broker`（`ORG_BROKER_SOCKET` で上書き可）。channel sidecar は `python -m claude_org_runtime.broker.channel_sidecar`（env 駆動、secretary-mcp.json 等の生成 mcp-config が注入）。
6. **herdr の現状**: false-reap（runtime #114）は **runtime 0.1.33（2026-07-03）で解消済み**（PR #115: spawn-then-move + `pane.get` 権威 liveness）。0.1.34 で workspace レイアウトポリシー、0.1.36 で `delegate-plan` の herdr pane id（`w<N>:p<N>`）対応も完了。現行 pin 0.1.36 + herdr 0.7.3 で herdr backend の org 実走を阻む既知 Blocker はない（実機 daemon が `--backend herdr` で稼働実績あり）。herdr は Rust 製 static-pie 単体バイナリで、x86_64 / aarch64 両ターゲットの配布がある（https://herdr.dev、update manifest `latest.json`）。
7. **Claude Code CLI**: npm ではなくネイティブインストーラ（`~/.local/bin/claude` → versions ディレクトリへの symlink）。認証スキップには `~/.claude/.credentials.json` **と** `~/.claude.json`（HOME 直下）の両方の永続化が必要。
8. **Anthropic 公式 devcontainer**: 非 root ユーザー・`/home/node/.claude` の named volume・`--cap-add NET_ADMIN/NET_RAW`（egress firewall 用）という構成の先例。
9. **コンテナ内 bubblewrap**: Claude Code の Bash sandbox（bwrap）は user namespace 作成を使うため、Docker 既定 seccomp プロファイルでブロックされる。実務上 `--security-opt seccomp=unconfined`（または unshare/clone 許可のカスタムプロファイル）が必要。Ubuntu 24.04+ の AppArmor userns 制限・`kernel.unprivileged_userns_clone=0` ホストという既知の失敗パターンがある。
10. **Raspberry Pi 5**: 既定カーネルが 16KB page size で、jemalloc 系バイナリ（Rust 製等）が「unsupported system page size」でクラッシュする既知問題。回避は `kernel=kernel8.img`（4KB）切替。

## 3. image に含めないもの（チェックリスト）

配布事故防止のため最初に列挙する（[`docker/README.md`](../../docker/README.md) 冒頭にも同一チェックリストを置く）。

| 分類 | 対象 | 遮断機構 |
|---|---|---|
| 組織運用状態 | `.state/**`（state.db、workers、broker トークン類） | `.dockerignore` + secret-scan stage（§7.3） |
| 個人 brief | `CLAUDE.local.md`、`.env*`、`tmp/` | 同上 |
| ロール別ローカル設定 | `**/settings.local.json`（`.override`、`.bak` 含む） | 同上 |
| 生ナレッジ | `knowledge/raw/**`（curated は git 追跡物なので焼き込み可） | 同上 |
| ローカル venv / worktree | `.venv/`、`.worktrees/` | 同上 |
| Claude 認証 | `~/.claude/.credentials.json`、`~/.claude.json` | build context 外（HOME は context に入らない）+ 初回導線で volume に生成 |
| gh / Codex / Google 認証 | `~/.config/gh/`、`~/.codex/`、`~/.config/gogcli/` | 同上 |
| Slack MCP OAuth | Claude Code 側 credential ストア内 | 同上 |

## 4. 全体アーキテクチャ

**単一コンテナ**（`org` サービス 1 つ）に組織一式を収める。dispatcher / worker は「コンテナ」ではなく broker daemon が管理する**コンテナ内ペイン**（tmux detached session または herdr pane）なので、コンテナを分ける必然性がなく、分けると tmux socket / broker HTTP / spawn 儀式がコンテナ境界をまたいで壊れる。

```text
tini (PID1)
 └─ entrypoint.sh (root → 所有権修復 → gosu org)
     ├─ [reconcile] 残骸掃除（§7.1）
     ├─ broker daemon: claude-org-runtime broker serve --backend ${ORG_BACKEND} ...（常駐）
     ├─ dashboard: python3 dashboard/server.py（常駐、§7.4）
     └─ wait ループ（SIGTERM trap → 順次停止）

docker exec -it claude-org org-shell   ← 人間の一次導線（対話面）
 └─ tmux セッション内で claude-org-runtime org up（daemon 再利用）→ secretary TUI
     └─ /org-start（dispatcher spawn、folder-trust 承認は exec した人間の TTY に出る）
```

- **transport は `ORG_TRANSPORT=broker` 固定を既定**とし、README / compose / Dockerfile の `ENV` を一致させる（Minor 対応。renga はホスト対話前提のツールで、コンテナ配布の対象外。切戻しが必要なユーザーはホスト運用に戻る）。
- **SSH daemon は同梱しない**（既定無効ではなく**非同梱**）。一次導線は `docker exec` + `tmux attach`。リモート利用は「ホストへ SSH → docker exec」の 2 段で足り、コンテナ内 sshd は鍵管理・ポート公開の攻撃面だけ増やす（Minor 対応）。

## 5. Blocker 1: コンテナ内プロセス監督の契約

**誰が何をどの順で起動するか**を固定する。

| 段 | 主体 | 起動するもの | 失敗時 |
|---|---|---|---|
| 1 | tini (PID1) | entrypoint.sh | コンテナ終了 |
| 2 | entrypoint (root) | volume 所有権の one-time 修復（§8） | fail-fast（エラー明示） |
| 3 | entrypoint (gosu org) | 残骸 reconciliation（§7.1）→ `.state/state.db` 不在なら `python -m tools.state_db.importer --rebuild --no-strict` | fail-fast |
| 4 | entrypoint (org) | broker daemon（`broker serve --backend ${ORG_BACKEND} --state-dir .state/broker --port ${ORG_BROKER_PORT}`） | fail-fast（daemon なしでは組織が成立しない） |
| 5 | entrypoint (org) | dashboard（`python3 dashboard/server.py`） | 警告のみで続行（可観測性の喪失であり機能停止ではない） |
| 6 | 人間（`docker exec`） | `org-shell` → tmux 内で `claude-org-runtime org up`（healthy daemon を再利用し secretary TUI のみ起動）→ `/org-start` | 対話的に解決 |
| 7 | secretary / dispatcher | dispatcher / worker ペイン spawn（従来どおり broker 経由） | 従来のエラー分岐（`no_backend` 等） |

設計上の要点:

- **daemon 先行・TUI 後置**。folder-trust / dev-channel の承認プロンプトが必要なのは Claude Code セッション（secretary・dispatcher・worker）であり、これらはすべて「人間が exec している TTY」または「orchestrator が send_keys で機械承認するペイン」に出る。entrypoint（非対話）が Claude Code を起動しないことで、**「docker exec した時点で初回導線が止まらない」を構造的に保証**する（プロンプトに答える者がいない場所でプロンプトを出さない）。
- **channel sidecar と tmux server は entrypoint が起動しない**（契約の明示）。channel sidecar（`org-broker-channel`）は各ペインの spawn 儀式に内包される（daemon 生成の mcp-config が Claude Code の dev-channel として load され、per-session の子プロセスとして起動・機械承認される）。tmux server は broker daemon が初回 pane spawn 時に専用 socket（`claude-org-broker`）で暗黙に立てる。herdr backend の場合の herdr server も同様に daemon 側の管轄。supervisor（entrypoint）が直接起動するのは §5 表のとおり daemon と dashboard のみ。
- `ORG_TRANSPORT` 判定は従来どおり `/org-start` Step 0 が行う。image は `ENV ORG_TRANSPORT=broker` を与えるだけで、判定ロジック自体には触れない。
- **folder-trust の事前緩和はしない**。`hasTrustDialogAccepted` 等の設定注入は Claude Code の内部実装依存で壊れやすく、既存の機械承認経路（spawn-flow 3-2/3-3b）がコンテナ内でもそのまま動くため不要。
- 停止契約: entrypoint が SIGTERM を trap し、(a) dashboard 停止 → (b) broker daemon 停止（ペイン reap を含む `org down` 相当）→ (c) tmux server kill の順で畳む。tini が SIGKILL までの猶予でゾンビを reap する。

## 6. Blocker 2: 永続化境界（volume 設計）

named volume は**用途を名前に刻んで** 3 本 + repo 内シンボリックリンク束で構成する。

| volume 名 | mount 先 | 中身 | 分類 |
|---|---|---|---|
| `org_home` | `/home/org` | `~/.claude`（認証 + ユーザー共通設定）、`~/.claude.json`、`~/.config/gh`、`~/.codex`、`~/.config/gogcli`、shell history | **認証 + ユーザー設定** |
| `org_state` | `/workspace/claude-org-ja/.state` | state.db、workers/、pending_decisions.json、notes/、attention.json、**role-config/（ロール別 settings.local.json の実体、§6.1）** | **組織運用状態** |
| `org_workers` | `/workspace/workers` | ワーカー worktree・成果物 | **作業成果物** |

**認証 volume と設定 volume の分離方針（明示）**: 認証（`~/.claude/.credentials.json` 等）とユーザー共通設定（`~/.claude/settings.json`）は**同一 volume（`org_home`）に同居**させる。理由は 2 つ。(1) `~/.claude.json` が HOME 直下必須で、`~/.claude` だけを volume 化すると fresh install 扱いになり毎回ログインを要求される（調査事実 §2-7）。ファイル単位 mount や symlink 分離は Claude Code の更新で壊れやすい。(2) `~/.claude/settings.json` は org-setup が additive merge する「個人設定と同居するファイル」であり、認証と分けて image に焼くと個人設定の混入（Codex 指摘の逆方向事故）を招く。一方、**ロール別 settings.local.json は org_state 側**に置く（§6.1）— これが「認証」と「組織設定」の分離線である。

### 6.1 ロール別 settings.local.json の永続化

ロール別設定（`.claude/settings.local.json`、`.dispatcher/.claude/settings.local.json`、`.curator/.claude/settings.local.json`）は gitignore 対象のため image に焼かれず、repo ディレクトリ（image 層 + コンテナ ephemeral 層）に生で置くと **コンテナ再作成で消える**。対策として entrypoint が以下の symlink を張る:

```text
.claude/settings.local.json            → .state/role-config/secretary.settings.local.json
.dispatcher/.claude/settings.local.json → .state/role-config/dispatcher.settings.local.json
.curator/.claude/settings.local.json    → .state/role-config/curator.settings.local.json
```

実体は `org_state` volume 内の `role-config/` に置かれ、再作成後も生き残る。初回起動時に実体が無ければ entrypoint が `python tools/org_setup_prune.py --all` 相当の生成を促す（自動生成はせず、初回導線 §10 の 1 ステップとする — org-setup は対話確認を含むため）。worker の settings.local.json は org-delegate が `org_workers` volume 内に動的生成するので追加対応不要。

### 6.2 .state 内の永続 / 揮発分離（Major 対応）

`org_state` volume 内でも、**プロセス寿命に紐づくものは entrypoint が起動ごとに破棄**する（§7.1）。永続: `state.db`・`workers/`・`pending_decisions.json`・`notes/`・`attention.json`・`role-config/`・dispatcher カーソル類。破棄: `broker/`（daemon.json の pid/port が必ず stale 化する。admin.token / secretary-mcp.json / queue.jsonl は daemon が再生成）・`dashboard.pid`・`*.log`・`attention_pane.json` 等の pid sidecar 類。

queue.jsonl の破棄は「コンテナ再起動をまたぐ未配達メッセージの喪失」を意味するが、コンテナ再起動 = 全ペイン（送信者・受信者）の死でありメッセージの宛先セッション自体が消えているため、配達保証を失うものではない（restart 後は `/org-start` からの再ブリーフィングが正路）。

## 7. Codex 指摘への対応（Blocker 3–4 / Major 全件）

### 7.1 Major 1: tmux socket / broker state の残骸 reconciliation

entrypoint 段 3 で毎起動時に実行する:

1. `.state/broker/` を丸ごと削除（daemon.json の stale pid/port、失効 admin.token、旧 secretary-mcp.json、herdr_generation / herdr_sweep.lock を一掃）。過去 dogfood で `.state/` に使い捨て state-dir が堆積した実例への根治でもある（state-dir はコンテナでは `.state/broker` 固定、`ORG_BROKER_STATE_DIR` で明示上書きのみ許可）。
2. tmux socket（`/tmp/tmux-*/claude-org-broker`）と herdr socket / server log の残骸削除。`/tmp` は tmpfs 前提だが、`docker restart`（同一コンテナ再起動）では `/tmp` が残るため明示削除が必要。
3. `.state/dashboard.pid`・`*.log`・pid sidecar 類の削除。
4. `state.db` 不在（fresh volume）なら importer で再構築。

### 7.2 Major 3: workers_dir のコンテナ用固定パス

repo を `/workspace/claude-org-ja` に配置することで、`registry/org-config.md` の `workers_dir: ../workers` が **`/workspace/workers` に自然解決**する。config の書き換えは行わず、「repo の絶対配置」を image の契約（LABEL とREADME に明記）として固定する。`org_workers` volume をそこに mount する。

### 7.3 Blocker 3: build context への秘匿物混入防止

三層で防ぐ:

1. **`.dockerignore`**（[`docker/Dockerfile.dockerignore`](../../docker/Dockerfile.dockerignore)。BuildKit の per-Dockerfile ignore 機構により、`docker build -f docker/Dockerfile` で自動適用される）: §3 のチェックリスト全項目 + `.git` を除外。
2. **secret-scan build stage（fail-fast）**: Dockerfile の第 1 stage が build context を受け取り、(a) 禁止パス（`.git`、`.state`、`CLAUDE.local.md`、`settings.local.json*`、`.env*`、`knowledge/raw` の実ファイル、`.worktrees`、`.venv`、`tmp` 等 — **ignore の除外集合と対で保守する二層目**）の存在、(b) 高シグナルなトークンパターン（`sk-ant-`、`ghp_`、`github_pat_`、`xox[bp]-`、`BEGIN ... PRIVATE KEY`）の grep、のいずれか検出で **build を失敗させる**。grep は `-l`（ファイル名のみ出力）で行い、**遮断機構自身が build ログへトークン本体を転写する二次漏洩を防ぐ**。runtime stage は scan 済み stage から COPY するため、scan を通らないコンテンツは image に入らない。
3. **HOME 非包含**: 認証類はすべて HOME 配下にあり build context（repo root）に構造上含まれない。

**`.git` は image に含めない**。git 履歴（reflog / stash / dangling object）はローカル環境で一度でも commit された秘匿物の持ち込み経路になり、パターン grep では実用的に検査できないため、ignore（一層目）と禁止パス検査（二層目）の両方で遮断する。帰結として image 内の repo は「実行体」であり git repo ではない — **コンテナ内での ja 自己編集タスクは、他プロジェクトと同様に workers ディレクトリへの fresh clone で行う**（org は新規 URL プロジェクトの基底 clone 配置をサポート済み）。

### 7.4 Major 4: dashboard の公開境界

- `dashboard/server.py` は `localhost` bind ハードコード（コンテナ外から不可視）。**この bind は変更しない**（認証なしサーバーを bind 変更で外に向ける改修はしない）。
- 公開は **socat による opt-in 転送**で行う: `ORG_DASHBOARD_EXPOSE=1`（compose 既定）のとき entrypoint が `socat TCP-LISTEN:18099,bind=0.0.0.0,fork → 127.0.0.1:8099` を起動し、compose は `127.0.0.1:8099:18099` で **ホストの loopback にのみ** publish する。server.py は 8099 が塞がると 8100/8101 へフォールバックするが、fresh コンテナ内では 8099 が必ず先に空くため転送先は 8099 固定とする（フォールバックが起きた場合は転送が空振りする — dashboard 喪失は §5 の契約どおり警告のみで組織は続行）。
- **認証なし LAN 公開は不可**を README に明記。LAN に出したい場合は認証付きリバースプロキシをユーザー責任で前置する（本設計の非スコープ）。
- socat はコンテナ内 `0.0.0.0` で受けるため、**同一 Docker network 上の他コンテナからは認証なしで到達できる**。compose の project network に org 以外のサービスを同居させないことを compose 内コメントと README に明記する。

### 7.5 Major 5: Claude Code sandbox（bubblewrap）の検証項目化

コンテナ内 bwrap は「動くか」ではなく以下のマトリクスで検証項目化する（PoC では未実施、§12 の検証チェックリストに収載）:

| # | 検証項目 | 期待 |
|---|---|---|
| S1 | Docker 既定 seccomp + 既定 capabilities で bwrap 実行 | **失敗する**（userns 作成が seccomp でブロック）ことの確認と、エラーが「sandbox 不可」と明示診断されること |
| S2 | `security_opt: [seccomp=unconfined]`（compose 既定）で bwrap 実行 | 成功 |
| S3 | rootless Docker ホストで S2 | ネスト userns がホストカーネル設定に依存。成否と診断メッセージを記録 |
| S4 | Ubuntu 24.04+ ホスト（AppArmor userns 制限）で S2 | `apparmor=unconfined` の要否を記録 |
| S5 | `--cap-drop ALL` との併用 | bwrap は非特権 userns 前提なので cap 追加なしで動くことの確認 |
| S6 | sandbox 不可環境でのフォールバック | Claude Code が sandbox 無効で安全側に劣化する（黙って全権にならない）ことの確認 |

compose 既定は `seccomp=unconfined` を**付ける**（Claude Code の Bash sandbox は org の防御層の一部であり、これを殺す既定はコンテナ境界を 1 枚に薄くする。unconfined の弱化は「コンテナ境界の seccomp を緩めて内側の bwrap を生かす」トレードオフであり、README で明示する。カスタム seccomp プロファイル（unshare/clone のみ許可）への置換は追補課題）。

### 7.6 Major 6: worker への Docker 提供は既定無効

- image に Docker CLI / dind は同梱しない。`/var/run/docker.sock` の mount は compose 本体に**書かない**。
- 必要なユーザーだけが [`docker/compose.docker-optin.yaml`](../../docker/compose.docker-optin.yaml) を `-f` 重ねで明示適用する（host socket mount = 実質ホスト root 権限、という警告コメントをファイル内に置く）。DinD は Pi 5 の storage / memory で重く、採らない。

### 7.7 Major 7: runtime pin と image rebuild 運用

- **起動時 pip upgrade はしない**。venv は build 時に `claude-org-runtime==<pin>` で焼き固める。
- **Claude Code CLI は volume の外（`/opt/claude-home`）に焼く**。ネイティブインストーラは `$HOME/.local` 配下に実体を置くため、素直に org ユーザーで入れると `org_home` volume に抱き込まれ、(a) rebuild しても既存 volume の旧 CLI が新 image を恒久マスクする、(b) `/home/org` を host bind mount にすると CLI 自体が消える。build 時に `HOME=/opt/claude-home` でインストールし PATH で解決することで、CLI も herdr / runtime と同じ「更新 = rebuild」の契約（本節）に載せる。`DISABLE_AUTOUPDATER=1` で起動時自己更新も止める。
- image tag 規約: `<repo-ref>-r<runtime-version>`（arch はマルチアーキ manifest が担うため tag に含めず、単アーキビルド時のみ `-<arch>` を付ける）。例: `ghcr.io/suisya-systems/claude-org-ja:v2026.07.17-r0.1.36`。同内容を OCI LABEL（`org.claude-org.repo-ref` / `org.claude-org.runtime-version`）にも刻む。
- コンテナ内の `check_runtime_version.py` drift 検出（org-start Block C2）が exit 2/3 を返した場合の案内文言は「pip upgrade せよ」ではなく「**新しい image tag に rebuild / pull せよ**」に読み替える（コンテナ内では PyPI 到達も期待できない。この読み替えは README に記載し、skill prose の改修は追補課題）。

### 7.8 Blocker 4 と Nit は §8（UID/GID）・各ファイル内コメント・[`docker/README.md`](../../docker/README.md) で対応

Nit 対応の所在: volume 名 = 用途付き（§6 表）。tini = §5 表段 1 + Dockerfile `ENTRYPOINT ["tini","--"]` 明記。README 冒頭チェックリスト = §3 と同一物。

## 8. Blocker 4: UID/GID と volume 所有権

- image に **固定 app user `org`（UID/GID は build ARG `ORG_UID`/`ORG_GID`、既定 1000:1000）** を作成する。image の USER は root のまま（entrypoint の one-time chown に必要）とし、`docker exec` の一次導線 `org-shell` が冒頭で `gosu org` に自己降格することで、**人間の対話面は常に org** で動く（生の `docker exec` で root shell を取れることは README に明記し、通常運用では org-shell 以外を使わない）。
- entrypoint のみ root で開始し、**one-time 所有権修復**を行う: 各 volume mount 点（`/home/org`、`.state`、`/workspace/workers`）にマーカーファイル `.org-owned` が無ければ `chown -R org:org` してマーカーを置く（毎回の再帰 chown は state.db 肥大時に起動を遅くするため one-time）。その後 `gosu org` で降格して以降のプロセスをすべて org で起動する。
- host bind mount を使うユーザー向けには、ビルド時 `--build-arg ORG_UID=$(id -u)` でホスト UID に合わせる経路を README に記載する（named volume 利用時は不要）。
- root で常駐するプロセスは**ゼロ**（tini と wait 中の entrypoint シェルのみが root。broker / tmux / herdr / dashboard / Claude Code はすべて org）。

## 9. ターミナルバックエンド: tmux / herdr 両対応

- backend は **daemon 起動時の `--backend` フラグでのみ**切り替わる（env での動的切替機構は runtime に無い）。コンテナでは entrypoint が `ORG_BACKEND`（`tmux` | `herdr`、**既定 `tmux`**）を読んで daemon 起動引数に渡す。切替は「compose の環境変数を変えて `docker compose up -d --force-recreate`」= daemon 立て直しとして運用する（既存 daemon と `--backend` 不一致は runtime が拒否するため、再作成が正路）。
- **herdr は正式サポート・同梱対象**。false-reap（runtime #114）は runtime 0.1.33 で解消済み、0.1.36 で pane id / venv 継承等の周辺も整備済みであり、「未解消なら experimental」の当初前提は**失効している**（§2-6）。
- それでも**コンテナ既定を tmux にする根拠**（false-reap ではない）: (a) herdr の headless（TUI クライアント接続なし）運用は startup workspace の生成挙動が TUI 接続時と異なり、コンテナ内 pure headless → 後から `docker exec` で TUI attach という導線の実測が未了（§12 検証項目 H1）。(b) tmux は apt で全アーキ安定供給・org 実走時間が最長。herdr は `ORG_BACKEND=herdr` の 1 変数で opt-in できる。
- herdr バイナリは build 時に **pin 済み GitHub release URL から取得し、リポジトリ側に焼いた実測 sha256 で検証して**同梱する（[`docker/install-herdr.sh`](../../docker/install-herdr.sh)。既定 pin: v0.7.4、amd64/arm64 両 sha256 実測済み 2026-07-17）。update manifest（https://herdr.dev/latest.json）は実測の結果 `assets["linux-<arch>"]` に URL 文字列を返すのみで checksum を提供しないため、manifest 追従ではなく pin 方式を採る。self-update（`herdr update`）は image 不変性に反するため使わず、更新は pin 更新 + image rebuild で行う（§7.7 と同じ運用）。`INSTALL_HERDR=0` で非同梱ビルドも可能。

## 10. 初回起動導線（認証 handbook）

image は認証ゼロで出荷され、初回に人間が以下を 1 回だけ通す（すべて `org_home` volume に永続化され、以降のコンテナ再作成で再認証不要）:

1. `docker compose up -d` — infra（daemon / dashboard）が上がる。
2. `docker exec -it claude-org org-shell --setup` — 初回セットアップモード。順に:
   - `claude` 単発起動 → `/login`（Claude OAuth。`~/.claude/.credentials.json` + `~/.claude.json` が volume に生成される）
   - `gh auth login`（`~/.config/gh/`）
   - `codex login`（任意。`~/.codex/`。Codex ゲートを使わない運用ならスキップ可）
   - Slack / Google MCP の接続（任意。Claude Code 側 credential ストア / `~/.config/gogcli/`）
   - `python tools/org_setup_prune.py --all` + `--user-common-sandbox` でロール別設定を生成（実体は §6.1 の symlink 先に落ちる）
3. `docker exec -it claude-org org-shell` — 通常導線。tmux セッション内で `org up` が healthy daemon を再利用して secretary TUI を起動 → `/org-start`。

## 11. マルチアーキビルド（linux/amd64 + linux/arm64）

- `docker buildx build --platform linux/amd64,linux/arm64` を正路とする。マルチアーキ成果物はローカル daemon に load できないため `--push` 前提（ローカル PoC は単一アーキで `--load`）。
- アーキ依存物と入手経路: Claude Code ネイティブインストーラ（arm64 Linux 対応）、gh（公式 apt repo、multi-arch）、tmux / python3 / tini / gosu / socat（debian multi-arch）、herdr（manifest から arch 別 asset、§9）、runtime venv（pure Python のため wheel 差なし）。
- CI 化する場合は QEMU エミュレーションの遅さ（数倍〜十数倍）を避けて arm64 native runner + manifest merge を推奨（設計のみ、非スコープ）。
- **Pi 5 16KB page size**: Rust 製バイナリ（herdr、Claude Code 同梱 ripgrep）が 16KB カーネルでクラッシュしうる既知問題があるため、README のトラブルシュートに `kernel=kernel8.img`（4KB）切替を記載し、§12 検証項目 A1 とする。
- **Pi 5 向け並列数既定**: `ORG_MAX_WORKERS`（既定 **3**）。Claude Code セッション 1 本あたり実測数百 MB〜1GB 級 + secretary/dispatcher 常駐分を 16GB から逆算した控えめな値。entrypoint が `registry/org-config.md` の `max_concurrent_workers` を起動時に env 値で書き換える（PoC の手当て。config の env override 機構を runtime / repo 側に持たせるのが根治で、追補課題）。

## 12. PoC の範囲と検証チェックリスト

PoC（[`docker/`](../../docker/)）は「ローカルでビルド・起動確認できる骨格」まで。認証が要る箇所は §10 の手順書化で代替する。

**PoC に含まれるもの**: Dockerfile（secret-scan stage + runtime stage、マルチアーキ対応記述）/ compose.yaml / entrypoint.sh / org-shell.sh / install-herdr.sh / Dockerfile.dockerignore / compose.docker-optin.yaml / README.md。

**検証チェックリスト**（image 完成の定義。PoC 時点の未実施項目を含む）:

| # | 項目 | PoC 状態 |
|---|---|---|
| B1 | `docker build`（amd64 単体）が通り、secret-scan stage が禁止パス混入時に fail する | **実施済み**（2026-07-17 実測: scan がテストフィクスチャのダミートークンで正しく fail → 除外調整後ビルド成功。herdr 0.7.4 / Claude Code 2.1.204 / runtime 0.1.36 同梱確認） |
| B2 | `docker compose up -d` で daemon + dashboard が起動し、`docker restart` 後に §7.1 の reconcile が残骸を掃除する | **実施済み**（2026-07-17 実測: chown → reconcile → state.db 再構築 → broker daemon（tmux backend）listen、dashboard は socat 経由でホスト loopback から到達、restart 後に reconcile 再発火・daemon.json 再生成、全常駐プロセスが org ユーザー） |
| B3 | 初回導線（§10）が手順書どおりに通り、コンテナ再作成後に再認証不要 | 手順書化のみ |
| B4 | `/org-start` → dispatcher spawn → worker 派遣 → 完了報告のフルサイクル | 未実施 |
| S1–S6 | sandbox 検証マトリクス（§7.5） | 未実施 |
| H1 | herdr headless 起動 → `docker exec` から TUI attach の導線 | 未実施 |
| H2 | herdr 配布経路の確認（manifest スキーマ・arm64 asset 実在・sha256 実測） | **実施済み**（manifest は checksum 非提供 → pin 方式に確定、§9） |
| A1 | Pi 5 実機（16KB / 4KB 両カーネル）での起動 | 未実施 |
| A2 | buildx `--platform linux/arm64` ビルド（QEMU）成功 | 未実施 |

## 13. 未解決事項（追補課題）

1. `registry/org-config.md` の env override 機構（§11 の sed 手当ての根治）。
2. dashboard の bind address 設定化 or 認証付き公開（§7.4 は socat + loopback 限定で回避）。
3. seccomp カスタムプロファイル（unconfined より狭い、unshare/clone のみ許可）の作成と配布（§7.5）。
4. `check_runtime_version.py` / org-start skill prose のコンテナ文脈対応（「rebuild せよ」文言、§7.7）。
5. `CLAUDE_CONFIG_DIR` による `~/.claude.json` の HOME 直下依存の解消可否の実バージョン検証（成立すれば §6 の volume 粒度を認証 / 設定でさらに分割できる）。
6. CI での multi-arch 自動 build / publish（arm64 native runner、§11）。
