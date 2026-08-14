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
| `ORG_SANDBOX_CANARY` | `enforce` | 起動時の Bash sandbox 実起動テスト。`enforce`（失敗で起動しない）\| `warn`（警告のみ）\| `off`。下の「Bash sandbox の実効状態」 |
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
  -t ghcr.io/suisya-systems/claude-org-ja:$(git describe --always)-r0.1.42 \
  --push .
```

- image tag 規約は `<repo-ref>-r<runtime-version>`（設計 §7.7）。runtime を更新したいときは**起動時 upgrade ではなく rebuild**。コンテナ内で runtime drift 警告（org-start Block C2）が出たら「新しい tag に pull / rebuild」が正しい対処。
- 単一アーキだけをビルドするときは `--platform linux/arm64` と `-t …-r<runtime>-arm64`（tag 末尾に arch）にする。マルチアーキ manifest が無いぶん arch は tag が示す（設計 §7.7）。
- **Raspberry Pi 5 の注意**: 既定カーネルは 16KB page size で、Rust 製バイナリ（herdr、Claude Code 同梱 ripgrep）がクラッシュする既知問題がある。**ただしこの image については実機実測で切替不要と確定済み**（2026-08-14、Pi 5 + kernel `6.18.34+rpt-rpi-2712` / `PAGESIZE` = 16384 で herdr 0.7.4 の TUI 起動と bundled ripgrep 14.1.1 の実検索がいずれも成功。設計 §12 A1）。他の Rust バイナリを持ち込んで「unsupported system page size」等で落ちた場合のみ、`/boot/firmware/config.txt` に `kernel=kernel8.img` を追記して 4KB カーネルに切り替える（設計 §11）。

## 公開済み image を pull して使う（Raspberry Pi 5 導線）

配布先は GHCR の **private** パッケージ [`ghcr.io/suisya-systems/claude-org-ja`](https://github.com/orgs/suisya-systems/packages/container/package/claude-org-ja)。image には Claude Code CLI / herdr / gh といった第三者バイナリが焼き込まれており再配布条件を確認していないため、**public にはしない**。

| tag | arch | runtime | digest |
|---|---|---|---|
| `v1.1.0-97-g6603478-r0.1.42-arm64` | linux/arm64 | 0.1.42 | `sha256:2a5272b0a7ca0dd01eae7ffa1b1238de14929f9499a8e7f3f67c1a189214844d` |

実機には **`read:packages` だけ**を持つトークンを置く（push 権限 `write:packages` は実機に持ち込まない）。GHCR は fine-grained PAT を受け付けず、**classic PAT のみ**を受け付ける（[GitHub Docs: Working with the Container registry](https://docs.github.com/en/packages/working-with-a-github-packages-registry/working-with-the-container-registry) — "GitHub Packages only supports authentication using a personal access token (classic)"）。

事前準備（母艦側で 1 回、順に）:

1. **package へのアクセス付与**（token 発行より先）。この package は repository に link されていない（`gh api /orgs/suisya-systems/packages/container/claude-org-ja` の `repository` が `null`）ため、権限は repository ではなく **package 単位**で決まる。**publish した本人と組織の owner ロールは自動で admin**（[GitHub Docs: Configuring a package's access control and visibility](https://docs.github.com/en/packages/learn-github-packages/configuring-a-packages-access-control-and-visibility) — "When you publish a package, you automatically get admin permissions to the package. If you publish a package to an organization, anyone with the `owner` role in the organization also gets admin permissions"）なので、**実機に置く token が publish 者本人（または org owner）のものならこの手順は不要**。別アカウントの token を使う場合のみ、package landing page → **Package settings** → **Manage access** → **Invite teams or people** で read を付与する。付与漏れは `docker login` は成功するのに pull だけ `denied` になる形で出る。
2. **token 発行**。GitHub の Settings → Developer settings → Personal access tokens (classic) で **`read:packages` のみ**にチェックした token を発行する。

Pi 5 側の前提（Docker 未導入なら先に 1 回。Raspberry Pi OS 64-bit は `/etc/os-release` が `ID=debian` を名乗るので Debian 手順がそのまま使える。[Docker Docs: Install Docker Engine on Debian](https://docs.docker.com/engine/install/debian/)）:

```bash
sudo apt-get update && sudo apt-get install -y ca-certificates curl
sudo install -m 0755 -d /etc/apt/keyrings
sudo curl -fsSL https://download.docker.com/linux/debian/gpg -o /etc/apt/keyrings/docker.asc
sudo chmod a+r /etc/apt/keyrings/docker.asc
# suite は決め打ちせず /etc/os-release から取る（bookworm 機と trixie 機が混在するため）
CODENAME="$(. /etc/os-release && echo "$VERSION_CODENAME")"
echo "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.asc] https://download.docker.com/linux/debian $CODENAME stable" \
  | sudo tee /etc/apt/sources.list.d/docker.list > /dev/null
sudo apt-get update
sudo apt-get install -y docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin
sudo systemctl enable --now docker
sudo usermod -aG docker "$USER"   # 反映は再ログイン後
```

Pi 5 で叩くコマンド列:

```bash
# 1. GHCR ログイン（token をシェル履歴に残さないため read -rs で受ける）
read -rs CR_PAT   # 発行した classic PAT を貼り付けて Enter（エコーなし）
echo "$CR_PAT" | docker login ghcr.io -u <github-username> --password-stdin
unset CR_PAT

# 2. repo を取得（compose.yaml / entrypoint 群が要る。repo は public なので認証不要）
git clone https://github.com/suisya-systems/claude-org-ja.git
cd claude-org-ja

# 3. image を pull（ビルドはしない。tag は上表のもの）
export ORG_IMAGE_TAG=v1.1.0-97-g6603478-r0.1.42-arm64
docker compose -f docker/compose.yaml pull

# 4. 起動（--no-build で「pull した image 以外は使わない」を明示）
docker compose -f docker/compose.yaml up -d --no-build
docker compose -f docker/compose.yaml ps

# 5. 初回のみ: 認証セットアップ（Claude /login → gh auth login → …）
docker exec -it claude-org org-shell --setup

# 6. 通常導線
docker exec -it claude-org org-shell
```

`ORG_IMAGE_TAG` を省くと compose は `:local`（未 pull）を見に行くので、**手順 3-4 では毎回 export しておくこと**（`.env` に書いてもよい）。

**手順 4 でコンテナが起動しない場合**（arm64 実機での最初の関門は Bash sandbox canary。設計 §12 A1）:

```bash
# canary を warn に落として起動し、単体実行で原因を切り分ける。
# 単体実行側は -e で enforce に戻すこと: docker exec はコンテナの環境変数
# （warn）を継承し、warn の canary は失敗しても exit 0 を返すため（docker/sandbox-canary.sh:68-73）
ORG_SANDBOX_CANARY=warn docker compose -f docker/compose.yaml up -d --no-build
docker exec -e ORG_SANDBOX_CANARY=enforce claude-org org-sandbox-canary; echo "exit=$?"
docker compose -f docker/compose.yaml logs --tail=50
```

`bwrap: ... Invalid argument`（EINVAL）は QEMU 特有の症状で実機では出ない想定、`No permissions to create new namespace`（EPERM）なら seccomp / userns 側の設定を疑う（設計 §12 S1 / A2-q）。切り分けが済んだら `ORG_SANDBOX_CANARY` を外して `enforce` 既定に戻す。

## セキュリティ境界の要点

- コンテナ内プロセスはすべて非 root（`org`、UID 1000 既定）。root は PID1 の tini と、one-time chown を行う entrypoint 冒頭のみ。`docker exec` は root で入るが、一次導線 `org-shell` が即座に org へ自己降格する。
- compose は `seccomp=unconfined` を付ける。Claude Code の Bash sandbox（bubblewrap）が user namespace を作るのに必要で、「コンテナ境界の seccomp を緩めて内側の bwrap sandbox を生かす」トレードオフ（設計 §7.5、実測は §12 S0-S6）。代わりに `cap_drop: ALL`（最小 cap のみ戻す）と `no-new-privileges` で絞る。
  - この対価に見合う内側 sandbox が実際に成立していることは、**image への `bubblewrap` 同梱**・**`sandbox.failIfUnavailable: true`**・**起動時 canary** の 3 点で担保する（下の「Bash sandbox の実効状態」）。
- `cap_drop: ALL` は sandbox の妨げにならない。capability を 1 つも与えない状態（`CapBnd` 全ビット 0）でも bwrap は起動する（非特権 user namespace だけで足りるため。設計 §12 S5 実測）。
- Claude Code CLI / herdr / runtime venv はすべて volume 外（`/opt`）に焼き込み。**更新はどれも image rebuild**（起動時自己更新なし）。
- この compose の project network に他コンテナを同居させない（socat がコンテナ内 0.0.0.0 で受けるため、同一 network からは無認証で dashboard に到達できる）。
- SSH daemon は同梱しない。リモート利用は「ホストへ SSH → docker exec」。

## Bash sandbox の実効状態（重要）

Claude Code の Bash sandbox は Linux では **bubblewrap（`bwrap`）＋非特権 user namespace** で成立する（[公式 docs](https://code.claude.com/docs/en/sandboxing)）。コンテナではこれが 2 段階で壊れうるので、この image は 2 段それぞれに歯止めを置いている。

| 壊れ方 | 症状 | 歯止め |
|---|---|---|
| 1. 依存が無い（`bwrap` 未同梱） | Claude Code が警告を出して**非 sandbox で続行**する | `bubblewrap` を image に同梱 + `sandbox.failIfUnavailable: true` で起動拒否 |
| 2. `bwrap` は在るが userns を作れない | Claude Code は**警告を出さず起動する**（設計 §12 S6-d） | 起動時 canary（`org-sandbox-canary`）が実起動テストで検出しコンテナを起動させない |

**1. 依存。** `bubblewrap` は image に同梱済み（`socat` も同梱、`ripgrep` は Claude Code 同梱で充足）。加えてコンテナ既定として `/etc/claude-code/managed-settings.json` に次を焼いてある:

```json
{ "sandbox": { "enabled": true, "failIfUnavailable": true } }
```

これにより「依存が欠けたら警告して非 sandbox で続行」ではなく**起動拒否**（`Error: sandbox required but unavailable: … refusing to start without a working sandbox.`、exit 1）になる。managed settings は最上位スコープで、`enabled` / `failIfUnavailable` のような boolean キーはユーザー／プロジェクト設定で上書きできない（[settings 優先順位](https://code.claude.com/docs/en/settings) / [sandboxing "Keep developers from widening the policy"](https://code.claude.com/docs/en/sandboxing)）。repo の `.claude/settings.json` は `failIfUnavailable: false` のままだが、コンテナ内ではこの managed 値が勝つ。**ホスト運用の設定は変えていない** — コンテナ限定の既定である。

なお `sandbox.allowUnsandboxedCommands: false` は**この用途には効かない** — あれは per-command の `dangerouslyDisableSandbox` 再試行を殺す設定で、依存欠落のゲートではない（設計 §12 S6-c 実測）。この image では設定していない。

**2. 機能性（canary）。** 落とし穴はこちらで、**`bwrap` が存在しさえすれば Claude Code は起動時チェックを通してしまう**。user namespace の作成が外側で禁じられていても警告は出ず、`failIfUnavailable: true` でも止まらない（設計 §12 S6-d 実測。起動時チェックは bwrap の**実在**だけを見て**機能性**を見ない）。典型的な原因は `seccomp=unconfined` が効いていないこと:

```
bwrap: No permissions to create new namespace, likely because the kernel does not allow non-privileged user namespaces.
```

そこで entrypoint の段 3e で `claude-org-runtime sandbox doctor` の live canary を回し、**bwrap が実際に user namespace を作れることを確かめてからでないと組織を起動しない**（既定 `ORG_SANDBOX_CANARY=enforce`）。canary は Claude Code と同じ実行主体（`org`、UID 1000）で走る。

```bash
# 手元で回す（root で exec しても内部で org に自己降格するので -u は不要）
docker exec claude-org org-sandbox-canary

# 実運用の settings スコープまで含めた診断（deny パスが bwrap で bind できるか）
docker exec claude-org org-shell --sandbox-check
```

| `ORG_SANDBOX_CANARY` | 挙動 |
|---|---|
| `enforce`（既定） | canary 失敗でコンテナを起動しない（fail-closed） |
| `warn` | 警告を出して起動を続ける。デバッグ用シェルを取るための escape hatch |
| `off` | canary を回さない |

`warn` / `off` にしても **1. の `failIfUnavailable: true` は生きている**ので、依存欠落は依然 Claude Code の起動拒否で止まる。この escape hatch が緩めるのは「コンテナが起動するか」だけで、sandbox 保証そのものではない。

> **`skipped` は合格ではない。** `sandbox doctor` の canary は、deny に「実在する絶対パス」が 1 つも無いと probe 対象 0 件で `status: skipped` / `ok: true` / exit 0 を返す。出荷 image の `.claude/settings.json` は deny が全て相対パスなので、**素の settings をそのまま渡すと canary が走らないまま常に緑になる**。起動時 canary はこれを避けるため専用の settings（[`docker/sandbox-canary-settings.json`](./sandbox-canary-settings.json)、image 内 `/opt/org-sandbox/canary-settings.json`）を使い、`skipped` を `pass` と別扱いで失敗として扱う。手で `sandbox doctor` を回すときも同じ落とし穴に注意すること。

**compose を経由しない起動に注意。** `seccomp=unconfined` は compose の `security_opt` で付いている。素の `docker run` や、`security_opt` を落とす環境（一部のオーケストレータ / PaaS）で起動すると Docker 既定 seccomp が userns 作成をブロックする。この構成は Claude Code 自身には検出できないが、起動時 canary が捕まえてコンテナを起動させない。

## トラブルシュート

| 症状 | 対処 |
|---|---|
| `org-shell` が「Claude 認証が見つかりません」 | `org-shell --setup` から初回セットアップ |
| broker `no_backend` | `ORG_BACKEND` の値と daemon.json の backend 一致を確認。herdr の場合は `herdr --version` がコンテナ内で動くか確認 |
| コンテナが `Bash sandbox canary failed` で起動しない | 意図どおりの fail-closed。bwrap が userns を作れていない。compose の `seccomp=unconfined` が効いているか、rootless Docker / Ubuntu 24.04 AppArmor 制限でないかを確認（設計 §7.5）。Ubuntu 24.04+ ホストでは `sysctl kernel.apparmor_restrict_unprivileged_userns` が `1` を返すなら要対処（`0` または key 不在なら無関係）。原因調査のため一時的に起動したいなら `ORG_SANDBOX_CANARY=warn` |
| Claude Code が `sandbox required but unavailable` で起動しない | `failIfUnavailable: true`（managed settings）が効いている。sandbox 依存が欠けた image を使っている可能性が高い（正規 image には `bubblewrap` が同梱されている）。`docker exec claude-org bwrap --version` で確認 |
| sandbox が効いている確証がほしい | `docker exec claude-org org-sandbox-canary` を回す（上の「Bash sandbox の実効状態」）。**警告が出ないことは根拠にならない** — bwrap が在るが機能しない構成では Claude Code の起動時チェックが素通りする（設計 §12 S6-d）。canary が `skipped` の場合も合格ではなく未判定 |
| `docker restart` 後に古い pane が見える | entrypoint の reconcile が `.state/broker` を毎起動で破棄する設計。見えるなら reconcile ログを確認 |
| Pi 5 で herdr / ripgrep が即死 | **まず page size を疑わないこと**。この image の herdr / bundled ripgrep は 16KB カーネルでの動作を実測確認済み（設計 §12 A1）。`herdr` が `No such device or address` で panic するのは PTY 不在（`docker exec` に `-it` を付け忘れ）が典型。持ち込んだ別の Rust バイナリが「unsupported system page size」で落ちる場合に限り 4KB カーネルへ切替（上記） |
