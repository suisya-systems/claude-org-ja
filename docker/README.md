# claude-org-ja Docker 配布（PoC）

組織一式（Claude Code CLI / claude-org-runtime / broker daemon / tmux・herdr 両バックエンド / スキル群 / dashboard）をセットアップ済み image として配布する。設計の正本は [`docs/design/org-docker-distribution.md`](../docs/design/org-docker-distribution.md)。

## ⚠️ この image に含まれないもの（配布前チェックリスト）

以下は**絶対に image に焼き込まれない**（[`docker/Dockerfile.dockerignore`](./Dockerfile.dockerignore) + build 時 secret-scan の二層で遮断。1 つでも混入したら build が失敗する）:

- [ ] `.state/**` — state.db・worker 状態・broker トークン
- [ ] `CLAUDE.local.md`・`.env*`・`tmp/` — 個人 brief / ローカル秘匿物
- [ ] `**/settings.local.json`（`.override` / `.bak` 含む）— ロール別ローカル設定
- [ ] `knowledge/raw/**` — 組織運用の生ログ
- [ ] `.venv/`・`.worktrees/`
- [ ] `.git`（reflog / stash 経由の秘匿物持ち込み経路のため。image 内 repo は git repo ではなく「実行体」で、コンテナ内での ja 自己編集は workers への fresh clone で行う）
- [ ] Claude / gh / Codex / Slack / Google の認証情報一切（HOME 配下のため build context に構造上入らない。初回起動時に volume へ生成される）

## クイックスタート

```bash
# 1. build（repo root が context。単一アーキ・ローカル）
docker compose -f docker/compose.yaml build

# 2. infra 起動（broker daemon + dashboard）
docker compose -f docker/compose.yaml up -d

# 3. 初回のみ: 認証セットアップ（Claude /login → gh auth login → codex login →
#    org_setup_prune.py。すべて org_home volume に永続化）
docker exec -it claude-org org-shell --setup

# 4. 通常導線: secretary TUI（tmux セッション内で org up）→ /org-start
docker exec -it claude-org org-shell
```

デタッチは `Ctrl-b d`、再接続は `docker exec -it claude-org org-shell`。`Ctrl-b` は tmux prefix の既定値なので、変更している場合は設定した prefix に読み替える。

> **ホスト側の端末が renga の場合は先に回避策が要る**: renga の org サイドバーは既定で有効で `Ctrl+B`（= tmux prefix `Ctrl-b` と同じ物理入力）を消費するため、`Ctrl-b d` がコンテナ内の tmux まで届かない。回避策と原因は [`docs/operations/dispatcher-view.md`](../docs/operations/dispatcher-view.md) の「外側フレームが renga の場合」を参照。
>
> ただし prefix 変更で回避する場合、**この導線の socket は `org-shell`** であり broker socket ではない（[`docker/org-shell.sh`](org-shell.sh):39, :49-54）。参照先の例は broker socket 向けなので、コンテナ内で次のように読み替える:
>
> ```bash
> tmux -L org-shell set -g prefix C-a
> tmux -L org-shell bind C-a send-prefix
> ```

## 環境変数（compose）

| 変数 | 既定 | 意味 |
|---|---|---|
| `ORG_TRANSPORT` | `broker`（固定） | コンテナ配布では broker のみサポート（renga はホスト対話前提のため対象外） |
| `ORG_BACKEND` | `tmux` | `tmux` \| `herdr`。切替は `docker compose up -d --force-recreate`（daemon 立て直し） |
| `ORG_MAX_WORKERS` | `3` | worker 並列上限。Raspberry Pi 5 16GB 基準の控えめ既定。潤沢なホストでは 8 まで |
| `ORG_DASHBOARD_EXPOSE` | `1` | dashboard をホスト loopback に公開するか |
| `ORG_BROKER_PORT` | `48720` | broker daemon の listen port（コンテナ内 127.0.0.1） |
| `ORG_UID` / `ORG_GID` | `1000` | build arg。host bind mount を使う場合 `ORG_UID=$(id -u)` で rebuild |

## dashboard

`http://127.0.0.1:8099`（**ホストの loopback のみ**）。dashboard は認証を持たないため **LAN へ公開してはならない**。リモートから見たい場合は `ssh -L 8099:127.0.0.1:8099 <host>` の port-forward を使う。

## ターミナルバックエンド（tmux / herdr）

- 既定は **tmux**。herdr は `ORG_BACKEND=herdr` で opt-in（image に同梱済み、`INSTALL_HERDR=0` build で除外可）。
- herdr の false-reap（runtime #114）は runtime 0.1.33 で解消済みで、herdr は正式サポート。tmux が既定なのは「コンテナ内 pure headless → 後から TUI attach」導線の実測が未了なため（設計 §9・§12 H1）。
- 稼働中バックエンドの確認: `docker exec claude-org cat .state/broker/daemon.json | jq .backend`

## worker から Docker を使う（既定無効）

ホスト Docker socket は**渡さない**のが既定。必要な場合のみ:

```bash
docker compose -f docker/compose.yaml -f docker/compose.docker-optin.yaml up -d
```

socket mount はホスト root 相当の権限付与である。オーバーレイファイル内の警告を読んでから使うこと。

## マルチアーキビルド（amd64 + arm64 / Raspberry Pi 5）

```bash
docker buildx build -f docker/Dockerfile \
  --platform linux/amd64,linux/arm64 \
  --build-arg REPO_REF="$(git describe --always)" \
  -t ghcr.io/suisya-systems/claude-org-ja:$(git describe --always)-r0.1.41 \
  --push .
```

- image tag 規約は `<repo-ref>-r<runtime-version>`（設計 §7.7）。runtime を更新したいときは**起動時 upgrade ではなく rebuild**。コンテナ内で runtime drift 警告（org-start Block C2）が出たら「新しい tag に pull / rebuild」が正しい対処。
- **Raspberry Pi 5 の注意**: 既定カーネルは 16KB page size で、Rust 製バイナリ（herdr、Claude Code 同梱 ripgrep）がクラッシュする既知問題がある。起動しない場合は `/boot/firmware/config.txt` に `kernel=kernel8.img` を追記して 4KB カーネルに切り替える（設計 §11）。

## セキュリティ境界の要点

- コンテナ内プロセスはすべて非 root（`org`、UID 1000 既定）。root は PID1 の tini と、one-time chown を行う entrypoint 冒頭のみ。`docker exec` は root で入るが、一次導線 `org-shell` が即座に org へ自己降格する。
- compose は `seccomp=unconfined` を付ける。Claude Code の Bash sandbox（bubblewrap）が user namespace を作るのに必要で、「コンテナ境界の seccomp を緩めて内側の bwrap sandbox を生かす」トレードオフ（設計 §7.5、実測は §12 S0-S6）。代わりに `cap_drop: ALL`（最小 cap のみ戻す）と `no-new-privileges` で絞る。
  - **現状この image に `bubblewrap` は入っていない**（2026-08-14 実測）。つまり seccomp を緩める対価は払っているが、内側の sandbox は動いていない。詳細と確認手順は下の「Bash sandbox の実効状態」を参照。
- `cap_drop: ALL` は sandbox の妨げにならない。capability を 1 つも与えない状態（`CapBnd` 全ビット 0）でも bwrap は起動する（非特権 user namespace だけで足りるため。設計 §12 S5 実測）。
- Claude Code CLI / herdr / runtime venv はすべて volume 外（`/opt`）に焼き込み。**更新はどれも image rebuild**（起動時自己更新なし）。
- この compose の project network に他コンテナを同居させない（socat がコンテナ内 0.0.0.0 で受けるため、同一 network からは無認証で dashboard に到達できる）。
- SSH daemon は同梱しない。リモート利用は「ホストへ SSH → docker exec」。

## Bash sandbox の実効状態（重要）

Claude Code の Bash sandbox は Linux では **bubblewrap（`bwrap`）＋非特権 user namespace** で成立する。コンテナではこれが 2 段階で壊れうるので、「有効なつもり」で走らせないために両方を確認する。

**1. 依存が揃っているか。** 現状この image に `bwrap` は同梱されていない（`socat` は同梱、`ripgrep` は Claude Code 同梱で充足）。この状態では Claude Code は起動時に次を出して**警告のうえ非 sandbox で続行**する:

```
⚠ Sandbox disabled: sandbox is enabled but dependencies are missing: bubblewrap (bwrap) not installed
  Commands will run WITHOUT sandboxing. Network and filesystem restrictions will NOT be enforced.
```

`.claude/settings.json` の `sandbox.failIfUnavailable` を `true` にすると、この条件は警告ではなく**起動拒否**になる（`Error: sandbox required but unavailable: … refusing to start without a working sandbox.`、exit 1）。なお `sandbox.allowUnsandboxedCommands: false` は**この用途には効かない** — あれは per-command の `dangerouslyDisableSandbox` 再試行を殺す設定で、依存欠落のゲートではない（設計 §12 S6 実測）。

**2. bwrap が実際に namespace を作れるか。** ここが落とし穴で、**`bwrap` が存在しさえすれば Claude Code は起動時チェックを通してしまう**。user namespace の作成が外側で禁じられていると、警告なしに「sandbox 有効のつもり」で走る（`failIfUnavailable: true` でも捕まらない。設計 §12 S6-d 実測）。典型的な原因は `seccomp=unconfined` が効いていないこと:

```
bwrap: No permissions to create new namespace, likely because the kernel does not allow non-privileged user namespaces.
```

この機能面の失敗を検出できるのは組織側の診断ツールだけなので、sandbox に依存する運用をするなら起動後に 1 回は回すこと:

```bash
# canary が pass なら sandbox は実際に起動できる。fail / exit 1 なら効いていない。
docker exec claude-org claude-org-runtime sandbox doctor \
  --settings /workspace/claude-org-ja/.claude/settings.json --verbose
```

`bwrap not found on PATH; live canary not run` と出た場合は canary が**走っていない**（= 判定していない）。`ok: true` でも sandbox が効いている証拠にはならないので、上記 1. の依存確認と併せて読む。

**compose を経由しない起動に注意。** `seccomp=unconfined` は compose の `security_opt` で付いている。素の `docker run` や、`security_opt` を落とす環境（一部のオーケストレータ / PaaS）で起動すると Docker 既定 seccomp が userns 作成をブロックし、上の 2. の無警告フォールバックに落ちる。

## トラブルシュート

| 症状 | 対処 |
|---|---|
| `org-shell` が「Claude 認証が見つかりません」 | `org-shell --setup` から初回セットアップ |
| broker `no_backend` | `ORG_BACKEND` の値と daemon.json の backend 一致を確認。herdr の場合は `herdr --version` がコンテナ内で動くか確認 |
| bwrap / sandbox エラー | compose の `seccomp=unconfined` が効いているか、rootless Docker / Ubuntu 24.04 AppArmor 制限でないかを確認（設計 §7.5）。Ubuntu 24.04+ ホストでは `sysctl kernel.apparmor_restrict_unprivileged_userns` が `1` を返すなら要対処（`0` または key 不在なら無関係） |
| sandbox が効いている確証がほしい | `sandbox doctor` の canary を見る（上の「Bash sandbox の実効状態」）。**警告が出ないことは根拠にならない** — bwrap が在るが機能しない構成では Claude Code は無警告で非 sandbox 実行に落ちる |
| `docker restart` 後に古い pane が見える | entrypoint の reconcile が `.state/broker` を毎起動で破棄する設計。見えるなら reconcile ログを確認 |
| Pi 5 で herdr / ripgrep が即死 | 16KB page size 問題。4KB カーネルへ切替（上記） |
