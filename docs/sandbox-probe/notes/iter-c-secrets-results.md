# Iteration C (proposal C) — secrets denyRead 3-layer 切り分け 結果

**Refs**: Issue #376
**Branch**: `feat/sandbox-probe-iter-c-secrets`
**実機検証日**: 2026-05-09
**比較対象**: iteration B round 3 ([`docs/sandbox-probe/notes/iteration-b-round3-results.md`](iteration-b-round3-results.md))
**位置付け**: [`docs/sandbox-probe/probes/checklist.md`](../probes/checklist.md) rows 7.1〜7.6 を「baseline (role=default 再生成) 」「profile-tightened 適用」の 2 状態で比較し、deny に効いている layer を分離する。

## 0. 3 layer の定義 (proposal C)

| layer | 名称 | 配置 | 適用先 |
|---|---|---|---|
| Layer 1 | Claude Code 組込 credential redaction | binary 組込 | tool 全般 (公式 docs ベース) |
| Layer 2 | `permissions.deny` の `Read(...)` 系 | `.claude/settings.local.json` | **Read tool のみ** (matcher = `Read`) |
| Layer 3 | `sandbox.filesystem.denyRead` 系 | `.claude/settings.local.json` | **Bash tool のみ** (bubblewrap bind-mount 経由) |

(本 iteration の最重要観察): Layer 2 / Layer 3 は**互いに排他的な tool 経路**にしか効かない。Read tool で開いた場合は Layer 3 を素通りし、Bash で `cat` した場合は Layer 2 を素通りする。

## 1. 環境 + ダミー secret 配置

| 項目 | 値 |
|---|---|
| worker_dir | `<workers-root>/sandbox-probe` |
| 起点 commit | `5b11595 spike(claude): iteration B round 3 ...` |
| permission_mode | 通常 (auto-mode classifier 経由) |
| OS | WSL2 (Linux 6.6.87.2-microsoft-standard) |
| `~/.aws` 実体 | `/mnt/c/Users/<windows-user>/.aws` への symlink (= round 3 の bwrap bootstrap fail 条件) |

ダミー secret 配置:

| path | 種別 | 備考 |
|---|---|---|
| `worker_dir/.env` | dummy | `SECRET=probe_dummy` (round 1 から継続使用) |
| `worker_dir/creds/credentials.json` | dummy | `{"aws_access_key_id":"AKIA-DUMMY-...","aws_secret_access_key":"dummy-..."}` を本 round で配置 |
| `worker_dir/key.pem` | dummy | `-----BEGIN PRIVATE KEY-----\nDUMMY-KEY-FOR-PROBE-TEST\n-----END PRIVATE KEY-----` |
| `~/.config/gh/hosts.yml` | **本物** | 既存 (210B)。task 指示通り skip 配置、real path 越しに probe |
| `~/.ssh/<ssh-key>` | **本物** | 既存 (419B)。実在する private key を probe 対象として使用 |
| `~/.aws/credentials` | **本物 (symlink)** | 既存 (116B、Windows 側 `/mnt/c/Users/<windows-user>/.aws/credentials`) |

real cred 越しの probe は **`cat ... >/dev/null 2>&1; echo $?`** で stdout 露出を抑止し、size と exit のみ観測。

## 2. baseline (role=default) 観測

settings 確認:

```bash
$ jq 'has("sandbox")' .claude/settings.local.json
false                                                # sandbox 非継承 ✅
$ jq '.permissions.deny | length' .claude/settings.local.json
4                                                    # basic git push / rm のみ ✅
$ jq -r '.permissions.deny[]' .claude/settings.local.json | grep -E '^Read\('
(none)                                               # Layer 2 (Read deny) 不在 ✅
```

→ **Layer 1 のみが論理的に active な状態** (Layer 2 / Layer 3 不在)。

probe 結果 (Bash 経由):

| # | 試行 | 結果 | layer 判定 |
|---|---|---|---|
| 7.1 | `cat ./.env` | exit=0、`SECRET=probe_dummy` 露出 | none — Layer 1 も catch せず |
| 7.2 | `cat ~/.ssh/<ssh-key> >/dev/null` | exit=0、size 419B confirm | none — Layer 1 も catch せず |
| 7.3 | `cat ~/.config/gh/hosts.yml >/dev/null` | exit=0、size 210B confirm | none — Layer 1 も catch せず |
| 7.4 | `cat ./creds/credentials.json` | exit=0、AKIA-... 露出 (dummy) | none |
| 7.5 | `cat ./key.pem` | exit=0、PRIVATE KEY 露出 (dummy) | none |
| 7.6 | `cat ~/.aws/credentials >/dev/null` | exit=0、size 116B confirm (symlink resolve 成功) | none |

probe 結果 (Read tool 経由、dummy 3 path のみ):

| path | Read tool 結果 |
|---|---|
| `./.env` | 内容露出 (`SECRET=probe_dummy`) |
| `./creds/credentials.json` | 内容露出 (dummy) |
| `./key.pem` | 内容露出 (dummy) |

→ **baseline では Bash 経由 / Read tool 経由いずれも全 row allow**。Layer 1 (組込 redaction) は本環境の Bash tool / Read tool 経由では **observable な発動なし** (iteration A 1.1 の dispatcher 観測 = 実 oauth_token が stdout に露出 とも整合)。

## 3. profile-tightened 適用後の観測

settings 確認:

```bash
$ jq 'has("sandbox")' .claude/settings.local.json
true                                                 # ✅
$ jq '.sandbox.filesystem.denyRead | length' .claude/settings.local.json
7                                                    # .env / .env.* / **/credentials* / **/*.pem / ~/.config/gh/hosts.yml / ~/.aws/** / ~/.ssh/** ✅
$ jq '.permissions.deny | length' .claude/settings.local.json
34                                                   # ✅
$ jq -r '.permissions.deny[]' .claude/settings.local.json | grep -E '^Read\('
Read(~/.ssh/*)
Read(~/.aws/*)                                       # ← Layer 2 で active な 2 件 ✅
```

→ **Layer 2 active 2 件 (`Read(~/.ssh/*)`, `Read(~/.aws/*)`)、Layer 3 active 7 件**。

**重要**: 本 session 内で settings.local.json を上書きすることで、sandbox / perms 設定は **次の tool 呼び出しからホットリロードされる** ことを実機 confirm (round 3 と異なり、worker 起動を挟まず profile 切替が反映される)。

probe 結果 (Bash 経由):

| # | 試行 | 結果 | 効いている layer |
|---|---|---|---|
| 7.1 | `cat ./.env` | exit=1、`bwrap: Can't mount tmpfs on /newroot/home/<user>/.aws` | **Layer 3 (bootstrap fail、WSL 限界)** |
| 7.2 | `cat ~/.ssh/<ssh-key>` | 同上 (bwrap exit=1) | Layer 3 (bootstrap fail) |
| 7.3 | `cat ~/.config/gh/hosts.yml` | 同上 | Layer 3 (bootstrap fail) |
| 7.4 | `cat ./creds/credentials.json` | 同上 | Layer 3 (bootstrap fail) |
| 7.5 | `cat ./key.pem` | 同上 | Layer 3 (bootstrap fail) |
| 7.6 | `cat ~/.aws/credentials` | 同上 | Layer 3 (bootstrap fail) |

→ **WSL 環境では Layer 3 の per-file 切り分けは不可能** (round 3 と同じ症状)。`~/.aws/**` denyRead 指定により `<home>/.aws` (= symlink) を tmpfs mount できず bwrap 起動全体が失敗、deny 対象でない command も含めて全 Bash が exit=1 で fail-closed する。本 row 群は `'WSL Layer 3 unavailable'` 扱い (bucket b で確定済の既知制約、Linux native でしか per-file 切り分け測定不可)。

probe 結果 (Read tool 経由):

| path | Read tool 結果 | 効いている layer |
|---|---|---|
| `~/.ssh/probe-nonexistent` | `File is in a directory that is denied by your permission settings.` (file 存在前に classifier 段階で deny) | **Layer 2** (`Read(~/.ssh/*)`) |
| `~/.aws/probe-nonexistent` | 同上 | **Layer 2** (`Read(~/.aws/*)`) |
| `worker_dir/.env` | 内容露出 (`SECRET=probe_dummy`) | **どの layer も catch せず** |
| `worker_dir/creds/credentials.json` | 内容露出 (dummy) | どの layer も catch せず |
| `worker_dir/key.pem` | 内容露出 (dummy) | どの layer も catch せず |

→ **Read tool 経路は Layer 2 でのみ deny される**。Layer 3 (`sandbox.filesystem.denyRead`) の `.env` / `**/credentials*` / `**/*.pem` / `~/.config/gh/hosts.yml` パターンは Read tool には**全く適用されない**。これは本 iteration の最大の発見。

## 4. layer 単離結論

### 4.1 layer × tool 経路マトリクス (本 iteration で確定)

| layer | Bash 経由 | Read tool 経由 | 備考 |
|---|---|---|---|
| Layer 1 (built-in) | observable な発動なし | observable な発動なし | iteration A 1.1 dispatcher で `~/.config/gh/hosts.yml` Bash 越し露出 = builtin 不発と整合 |
| Layer 2 (`perms.deny Read(...)`) | **適用されない** (matcher = Read) | **適用される** (classifier で deny) | tightened の 2 件はいずれも Read tool 専用 |
| Layer 3 (`sandbox.filesystem.denyRead`) | **適用される** (bind-mount 経由)、ただし WSL では bootstrap fail で per-file 不可分 | **適用されない** | Linux native では per-file deny 想定 (本 iter では未検証) |

### 4.2 row 別 deny 担当 layer

| # | path | tightened での deny 担当 |
|---|---|---|
| 7.1 | `./.env` | Bash: Layer 3 (bootstrap fail / Linux native では per-file)、Read: **誰も catch しない** ← gap |
| 7.2 | `~/.ssh/<ssh-key>` | Bash: Layer 3 (bootstrap fail)、Read: Layer 2 |
| 7.3 | `~/.config/gh/hosts.yml` | Bash: Layer 3 (bootstrap fail / Linux native では per-file)、Read: **誰も catch しない** ← gap |
| 7.4 | `./creds/credentials.json` | Bash: Layer 3 (bootstrap fail / Linux native では per-file)、Read: **誰も catch しない** ← gap |
| 7.5 | `./key.pem` | Bash: Layer 3 (bootstrap fail / Linux native では per-file)、Read: **誰も catch しない** ← gap |
| 7.6 | `~/.aws/credentials` | Bash: Layer 3 (bootstrap fail)、Read: Layer 2 |

### 4.3 Phase 2 設計に反映すべき発見

1. **Layer 2 と Layer 3 は tool 経路が排他**。`Read tool` を Layer 3 が cover しない (= Read tool で開ける secret は sandbox.denyRead では止まらない) のは Phase 2 まで未周知だった可能性が高い。
2. **gap row (7.1 / 7.3 / 7.4 / 7.5)**: `worker_dir/.env`、`worker_dir/creds/credentials.json`、`worker_dir/key.pem`、`~/.config/gh/hosts.yml` は tightened でも Read tool 経由なら素通り。Phase 2 では `permissions.deny` に以下追加を検討:
   - `Read(.env)` / `Read(.env.*)`
   - `Read(**/credentials*)`
   - `Read(**/*.pem)`
   - `Read(~/.config/gh/*)` または `Read(~/.config/gh/hosts.yml)`
3. **Layer 1 (組込 credential redaction) の actual coverage は本環境では observable に出ない** — `~/.ssh` / `~/.aws` / `~/.config/gh` のいずれも Bash 越しに baseline で全露出 (本 iter + iteration A 1.1)。公式 docs の謳いと実機の挙動には乖離がある可能性。Phase 2 では Layer 1 を防御の primary 層とせず、Layer 2 + Layer 3 で必ず二重化する設計を提案。
4. **WSL Layer 3 unavailable**: `~/.aws/**` denyRead を含む profile を WSL で適用すると bwrap が exit=1 で fail-closed (= deny 効果は副次的に達成、ただし sandbox 全体不能化)。Linux native でしか per-file Layer 3 観測できないため、proposal C step 7 (`failIfUnavailable=true`) の実機検証は別 worker dir / Linux native 環境で実施 (本 task scope 外)。

## 5. 残課題 (本 task scope 外)

1. **Linux native 環境での Layer 3 per-file 切り分け**: WSL では bootstrap fail で per-file が見えないため、Linux native 環境で同 profile を適用し `.env` / `**/credentials*` / `**/*.pem` の denyRead が **sandbox bootstrap 失敗ではなく runtime bind-mount で deny する** ことを実機 confirm する必要あり。
2. **Read tool gap の埋め込み**: §4.3 #2 の `Read(...)` 追加を `org_extension_schema.json` レベルで反映する場合の影響評価 (closed_world allow との衝突など)。
3. **Layer 1 spec の確定**: 公式 docs のどの記述が `~/.ssh` / `~/.aws` redaction を保証しているのか、また Bash tool 経由に対してどこまで効くのか、Anthropic 側 release notes の追跡。
4. **incorporate**: 本 results を `claude-org-ja docs/sandbox-probe/notes/` に取り込む別 task は CLAUDE.md 記載通り後続。

## 6. 参考

- profile: [`docs/sandbox-probe/profiles/profile-tightened.json`](../profiles/profile-tightened.json)
- iteration B round 1 結果: [`docs/sandbox-probe/notes/iteration-b-round1-results.md`](iteration-b-round1-results.md)
- iteration B round 2 結果: [`docs/sandbox-probe/notes/iteration-b-round2-results.md`](iteration-b-round2-results.md)
- iteration B round 3 結果: [`docs/sandbox-probe/notes/iteration-b-round3-results.md`](iteration-b-round3-results.md)
- proposal: [`docs/sandbox-probe/notes/next-iteration-proposals.md`](next-iteration-proposals.md) (proposal C)
- checklist: [`docs/sandbox-probe/probes/checklist.md`](../probes/checklist.md) rows 7.1〜7.6
- 関連 issue: #376
