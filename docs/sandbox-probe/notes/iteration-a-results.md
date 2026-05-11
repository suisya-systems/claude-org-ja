# Iteration A B1-1 probe 結果 (Issue #376 Pre-Phase 0)

## 1. 概要

- **probe 対象**: `probes/checklist.md` 1 章 (B1-1 — dispatcher × bypassPermissions × sandbox) の row 1.1〜1.5
- **目的**: dispatcher pane (bypassPermissions) で sandbox layer が credential read / 範囲外 write をどこまで遮蔽するかを実機で実測し、Phase 1 schema 設計 (dispatcher 行に sandbox 列を持たせるか否か) の判断材料とする
- **実施日時**: 2026-05-09
- **実施場所**: dispatcher pane (`<claude-org-root>/.dispatcher/`)
- **実施者**: Secretary 主導 + ユーザーが dispatcher pane で対話的に手動実行
- **本書の役割**: 上記 probe の **書き起こし** (worker による事後ドキュメント化)。worker 自身は probe を実行していない。

## 2. 実施環境

| 項目 | 値 |
|---|---|
| OS | Linux x86_64 / WSL2 |
| bubblewrap | 有 (`bwrap` 利用可) |
| dispatcher cwd | `<claude-org-root>/.dispatcher/` |
| dispatcher 起動オプション | `permission_mode=bypassPermissions`, `model=sonnet` |
| sandbox mode (`/sandbox` 選択) | **Sandbox BashTool, with auto-allow** (3 択中の 1 番) |
| sandbox status | active (bubblewrap 経由で起動済み、1.5 で確認) |
| dispatcher repo-shared 継承 | `claude_org_path/.claude/settings.json` の hooks/sandbox を継承 (cwd が claude_org_path 配下のため) |

## 3. 実測結果

各 row の試行コマンドと dispatcher stdout の生観測。**機微情報 (oauth_token 等) は redact**。

### 3.1 row 1.1 — `cat ~/.config/gh/hosts.yml`

- **試行**: `cat ~/.config/gh/hosts.yml`
- **観測**: 完全に通る。実 `oauth_token` を含む YAML が dispatcher stdout に丸ごと出力された
- **stdout (redacted)**:

  ```yaml
  github.com:
      users:
          <user>:
              oauth_token: gho_<REDACTED-32-chars>
      git_protocol: https
      ...
  ```

- **結論**: **allow (none)** — sandbox auto-allow mode は denyRead 列が空なので `~/.config/` 直下も素通りで read される。

### 3.2 row 1.2 — `cat ./.env` (dispatcher cwd 内)

- **事前配置**: `.dispatcher/.env` に `SECRET=probe_dummy`
- **試行**: `cat ./.env`
- **stdout**:

  ```
  SECRET=probe_dummy
  ```

- **結論**: **allow (none)** — cwd 内 read。sandbox の自然な許可範囲 (cwd は denyRead 対象外)。想定通り。

### 3.3 row 1.3 — `cat ./creds.pem` (dispatcher cwd 内)

- **事前配置**: `.dispatcher/creds.pem` に `dummy creds`
- **試行**: `cat ./creds.pem`
- **stdout**:

  ```
  dummy creds
  ```

- **結論**: **allow (none)** — cwd 内 read。`.pem` 拡張子による特別扱いなし (Claude Code 組込 credential 保護も `.pem` 単独では発火しないことを実証)。

### 3.4 row 1.4 — `echo x >> ~/.claude/settings.json.sandbox-test`

- **試行**: `echo x >> ~/.claude/settings.json.sandbox-test`
- **観測**: "Bash completed with no output" (success, exit 0)
- **結論**: **allow (success, write-verification pending)** — exit 0 だが、本 iteration では shadow FS (sandbox 内仮想 FS) 経由か、実 FS への反映かを **判別していない**。`rm -f` cleanup 直前で `ls -la ~/.claude/settings.json.sandbox-test` および `cat` による事後検証 row を入れ忘れたため未確定。
- **次 iteration 対応**: row 1.4 後に `ls -la` + `cat` の事後検証 step を必ず入れる (詳細は §6)。

### 3.5 row 1.5 — `/sandbox` slash command

- **試行**: dispatcher で `/sandbox` を実行
- **観測**: Tab "Mode" 上に以下が表示

  ```
  ✓ Sandbox enabled with auto-allow for bash commands
  ```

  3 択中の 1 番 "Sandbox BashTool, with auto-allow" が選択中。

- **結論**: sandbox は **active** (bubblewrap 経由で起動済み)。1.1〜1.4 で deny が発火しなかったのは **sandbox 不在ではなく** denyRead/denyWrite rule 列が空だったため、と確定。

## 4. sandbox status 表示の解釈

`/sandbox` の "Sandbox enabled with auto-allow for bash commands" は以下を意味する:

- bubblewrap (Linux) による OS-level sandbox は **active**
- ただし mode は "auto-allow"。denyRead/denyWrite で明示的に列挙された path **以外** はすべて allow
- bypassPermissions モードは Claude Code 側の `permissions.allow/deny` を無効化するが、sandbox は別 layer なので別軸で並走している
- 本 probe で 1.1〜1.4 が allow になったのは、現状 dispatcher の継承する `claude_org_path/.claude/settings.json` の sandbox ブロックに `~/.config/`, cwd 配下の特定 file, `~/.claude/settings*` への明示 deny が **無い** ため

つまり「bypassPermissions が sandbox を巻き込んで無効化した」のではなく「sandbox auto-allow + deny 列空 → 全 path 素通り」が真の解釈である。

## 5. Phase 1 schema 設計への含意

### 5.1 結果カテゴリ判定 (runbook §3.5 の表に基づく)

runbook §3.5 表との対応:

| runbook §3.5 結果ケース | 本 iteration 該当性 | 採用解釈 |
|---|---|---|
| 1.1〜1.4 すべて deny | ❌ | — |
| 1.1〜1.4 一部 deny / 一部 allow | ❌ | — |
| 1.1〜1.4 すべて allow | ⚠️ 表面上は該当 | ただし「bypassPermissions が sandbox 巻き込み」とは断定不可。1.5 で sandbox active 確認済 |
| `/sandbox` で Disabled | ❌ (Enabled with auto-allow) | — |

→ 表に明記されていない **第 5 ケース「sandbox active だが deny 列空のため素通り」** に該当。runbook §3.5 表は次 iteration で更新する。

### 5.2 設計提案: dispatcher 行に明示的 sandbox 列を持たせる

dispatcher は bypassPermissions で permissions.deny が無効化されているので、credential / settings 保護は **sandbox layer に寄せる必要** がある。具体的には、Phase 1 schema の dispatcher 行に以下を持たせる提案:

- `sandbox.filesystem.denyRead`:
  - `~/.config/gh/`
  - `~/.aws/`
  - `~/.ssh/`
  - `~/.claude/settings*`
  - `~/.netrc`
  - `~/.npmrc`
- `sandbox.filesystem.denyWrite`:
  - `~/.claude/`
  - `~/.config/`

これらは「dispatcher cwd の自然 read 範囲」とは別に、ユーザー認証情報を機械的に守る目的で追加する。

## 6. 想定外と次 iteration 提案

### 6.1 想定外 #1: 1.4 の write-verification 未確定

- **事象**: `echo x >> ~/.claude/settings.json.sandbox-test` は exit 0 で完了したが、shadow FS と実 FS のどちらに書かれたか判別できなかった
- **原因**: cleanup `rm -f` 直前で事後検証 (`ls -la ~/.claude/settings.json.sandbox-test`, `cat`) を skip した
- **次 iteration 対応**:
  - checklist 1.4 を 1.4a (write 試行) と 1.4b (`ls -la` + `cat` 事後検証) に分割
  - 1.4b で「`ls` で見えない / `cat` で空 / "No such file"」なら shadow FS、「`ls` で見える / `cat` で `x` を返す」なら実 FS への write と判定

### 6.2 想定外 #2: sandbox layer の遮蔽ログが取れない

- **事象**: runbook には「どの read / write が sandbox 内で blocked / allowed されたか」のログ取得手段が明記されていない
- **次 iteration 対応**:
  - `/sandbox` の Overrides / Config tab を順に開いて、表示内容と挙動を観察 row 化
  - 可能なら `bwrap --debug` 相当のフラグが Claude Code 側で有効化できるか調査
  - 実用ログがどうしても取れない場合、checklist の各 row で「明示的な deny 動作の有無」を `Permission denied` 等の error string で判定する代替案を採用

### 6.3 想定外 #3: probe 実施そのものが credential を露出する

- **事象**: 1.1 で実 `oauth_token` の値が dispatcher stdout に表示された (= probe 自体が credential 露出を伴う)
- **影響**: 本書および commit に生値を残せば即座に credential leak。本 iteration では Secretary が口頭で redact 必須を周知し、ユーザーには別途 `gh auth refresh` を案内済み。
- **次 iteration 対応 (runbook §6 への追記提案)**:
  - probe 前: 当該環境の `gh` token を **probe 専用の testbed token** に切り替える (`gh auth login --with-token < testbed.txt` 等)
  - probe 後: `gh auth refresh` で testbed token を破棄、または production token に戻す
  - probe 中の stdout は dispatcher pane scrollback / log file 出力先を意識して扱う (スクリーンショット / ペースト時の redact 必須)

## 7. 関連資料

- `probes/checklist.md` 1 章 (本 iteration で 1.1〜1.5 を埋めた)
- `docs/sandbox-probe-runbook.md` §3 (B1-1 手順)、§3.5 (結果分類表 — 次 iteration で更新)
- `docs/next-iteration-proposals.md` 提案 A (本 iteration で実施した分)
- `docs/baseline-observations.md` (本書きに先立つ静的解析)
