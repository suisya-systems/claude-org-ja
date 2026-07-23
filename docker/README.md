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

デタッチは `Ctrl-b d`、再接続は `docker exec -it claude-org org-shell`。

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
  -t ghcr.io/suisya-systems/claude-org-ja:$(git describe --always)-r0.1.37 \
  --push .
```

- image tag 規約は `<repo-ref>-r<runtime-version>`（設計 §7.7）。runtime を更新したいときは**起動時 upgrade ではなく rebuild**。コンテナ内で runtime drift 警告（org-start Block C2）が出たら「新しい tag に pull / rebuild」が正しい対処。
- **Raspberry Pi 5 の注意**: 既定カーネルは 16KB page size で、Rust 製バイナリ（herdr、Claude Code 同梱 ripgrep）がクラッシュする既知問題がある。起動しない場合は `/boot/firmware/config.txt` に `kernel=kernel8.img` を追記して 4KB カーネルに切り替える（設計 §11）。

## セキュリティ境界の要点

- コンテナ内プロセスはすべて非 root（`org`、UID 1000 既定）。root は PID1 の tini と、one-time chown を行う entrypoint 冒頭のみ。`docker exec` は root で入るが、一次導線 `org-shell` が即座に org へ自己降格する。
- compose は `seccomp=unconfined` を付ける。Claude Code の Bash sandbox（bubblewrap）が user namespace を作るのに必要で、「コンテナ境界の seccomp を緩めて内側の bwrap sandbox を生かす」トレードオフ（設計 §7.5 に検証マトリクス）。代わりに `cap_drop: ALL`（最小 cap のみ戻す）と `no-new-privileges` で絞る。
- Claude Code CLI / herdr / runtime venv はすべて volume 外（`/opt`）に焼き込み。**更新はどれも image rebuild**（起動時自己更新なし）。
- この compose の project network に他コンテナを同居させない（socat がコンテナ内 0.0.0.0 で受けるため、同一 network からは無認証で dashboard に到達できる）。
- SSH daemon は同梱しない。リモート利用は「ホストへ SSH → docker exec」。

## トラブルシュート

| 症状 | 対処 |
|---|---|
| `org-shell` が「Claude 認証が見つかりません」 | `org-shell --setup` から初回セットアップ |
| broker `no_backend` | `ORG_BACKEND` の値と daemon.json の backend 一致を確認。herdr の場合は `herdr --version` がコンテナ内で動くか確認 |
| bwrap / sandbox エラー | compose の `seccomp=unconfined` が効いているか、rootless Docker / Ubuntu 24.04 AppArmor 制限でないかを確認（設計 §7.5） |
| `docker restart` 後に古い pane が見える | entrypoint の reconcile が `.state/broker` を毎起動で破棄する設計。見えるなら reconcile ログを確認 |
| Pi 5 で herdr / ripgrep が即死 | 16KB page size 問題。4KB カーネルへ切替（上記） |
