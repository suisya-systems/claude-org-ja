# Phase 3: Sandbox Bootstrap Policy Design (~/.aws/.env deny-map fragility)

**Refs**: Issue #376（epic）/ Issue #392（Phase 3 実装）。Linux runbook 関連の #380 とは別スコープ
**Branch**: `feat/sandbox-bootstrap-policy-design`
**作成日**: 2026-05-09
**スコープ**: 設計（writeup のみ）。実装は本 worker の責務外で、ユーザー判断後に後続 worker が引き継ぐ。
**先行**: [`docs/sandbox-probe/notes/iteration-b-round3-results.md`](./notes/iteration-b-round3-results.md) §6, §7.2, §8 の残課題（symlink 配下 denyRead/denyWrite で sandbox bootstrap が破綻）

## 1. 背景

iteration B round 3 で、`profile-tightened.json` の `sandbox.filesystem.denyRead` / `denyWrite` に `~/.aws/**` / `~/.ssh/**` を追加すると、WSL 環境（`~/.aws` が `/mnt/c/Users/<windows-user>/.aws` への symlink）で **sandbox 起動全体が `bwrap` exit=1 で fail する**ことが確認された。round 3 の観測では tmpfs マウント時のエラー (`Can't mount tmpfs on /newroot<home>/.aws`) が主因だったが、本セッション (worktree `feat/sandbox-bootstrap-policy-design`) では deny 対象を **個別ファイル列挙**（`<home>/.aws/.env`, `<home>/.aws/config`, `<home>/.aws/credentials`, `<home>/.aws/sso`）に絞ったプロファイルでも、別形式の bwrap エラーで全 sandboxed Bash が exit=1 する状態が再現している:

```text
$ pwd
bwrap: Can't create file at <home>/.aws/.env: No such file or directory
exit=1
```

つまり「wildcard を file-list に展開すれば回避できる」という素朴な手当ては効かない。Phase 3 では、本ファミリーの bootstrap 失敗を **どの層で / どう policy 化して** 救うかを決める必要がある。

## 2. 再現（本セッションでの実機 confirm）

| 項目 | 値 |
|---|---|
| Platform | WSL2 (Linux 6.6.87.2-microsoft-standard-WSL2) |
| `~/.aws` | symlink → `/mnt/c/Users/<windows-user>/.aws`（host 側 Windows ディレクトリ） |
| `~/.aws/.env` | host 側に存在（regular file 35 bytes、symlink 経由でアクセス可） |
| `~/.ssh` | regular directory（symlink ではない） |
| Sandbox `denyOnly` (read) | 個別ファイル列挙（`~/.aws/.env`, `~/.aws/config`, `~/.aws/credentials`, `~/.aws/sso` を含む 10 件） |
| 観測エラー | `bwrap: Can't create file at <home>/.aws/.env: No such file or directory` exit=1 |
| 影響範囲 | sandbox 越しに走る **全 Bash コマンド** が即 fail（deny 対象に関係なく） |
| 迂回 | `dangerouslyDisableSandbox: true` 必須（本 doc 執筆もすべてこの迂回下） |

`bwrap` は deny target に対し `--bind /dev/null <target>` ないし `--ro-bind <empty> <target>` 系のマウントを構成する。マウント先のファイル `<home>/.aws/.env` を sandbox namespace 内で **新規に作成しようとして** 親パス `<home>/.aws` を解決した結果、symlink 先 `/mnt/c/Users/<windows-user>/.aws` が新 namespace 内に bind-mount されておらず（`/mnt/c` は sandbox の read 許可に含まれていない）、parent unresolvable で fail する。

この挙動は wildcard 形式（`~/.aws/**`）と file-list 形式の **どちらでも症状の根は同じ**:

- wildcard 形式 → `~/.aws` をまるごと tmpfs として deny 化しようとして tmpfs mount 自体が失敗
- file-list 形式 → 個別ファイル deny 化用の bind target 作成時に parent 解決が失敗

## 3. 根本原因（確定）

直接原因は **bwrap の deny mapping が sandbox namespace 内に「実体としての parent dir」を要求するのに対し、host 側 `~/.aws` が dangling symlink（target が sandbox view 内に bind されていない）** であること。これは以下 3 因の合成として確定する:

1. **WSL における `~/.aws` symlink 慣習** — Windows 側 AWS CLI 設定を WSL から共有する目的で `/home/$USER/.aws → /mnt/c/Users/$USER/.aws` を張る運用は WSL ユーザーで一般的。本リポジトリ上の他環境（Linux native）では発生しない。
2. **bwrap の deny 機構が「target の parent が namespace 内で解決できる real directory」を要求する仕様** — `--bind` / `--ro-bind` は target が無い場合に作成を試みるが、parent が symlink で target が未 bind なら作成できず fail する。bug ではなく仕様。
3. **Claude Code sandbox runtime が deny entry の parent を pre-resolve しない** — 現行実装は profile を bwrap 引数にそのまま transcribe する。symlink 検出 / 親 dir mount 戦略はビルトインされていない。

3 因のいずれを抜いても本症状は出ない。policy としては **(2) は外部仕様で動かせない**ため、(1) または (3) のいずれか（または両方）を policy で吸収する必要がある。

### 関連する症状ではない事象（除外）

- bwrap 不在環境での fall-open（`failIfUnavailable`）→ 本症状とは別。round 3 §6 で「bootstrap failure は fall-open に変換されない」ことを確認済。`failIfUnavailable` を `true` にしても本症状は救えない（むしろ悪化）。
- credential 露出そのもの → round 2 / 3 で sandbox 越し `cat ~/.aws/.env` の stdout 出力は **本症状下でも空**（bwrap が即 fail するため stdout に何も出ない）。よって「credential を露出するか」観点では本症状は **不安全側ではなく不便側**に倒れている。問題は「sandbox 経由のあらゆる正常コマンドも巻き添えで fail する」点。

## 4. 候補 policy（5 案）

### 4.1 案 A — Profile 生成時に symlink-aware 検査して entry を skip + warn

**概要**: `claude-org-runtime settings generate` ないし profile 適用時に、`sandbox.filesystem.denyRead` / `denyWrite` の各 entry について **parent パスが host 側で symlink か** を検査する。symlink 先が sandbox の read allowlist に含まれていない場合、当該 entry を出力 profile から **除外し、構造化 warning を stderr / journal に emit** する。

**長所**:

- 実装最小（runtime 側のみ。bwrap には触らない）
- 起動失敗を起こさず、他の deny entry / 他の Bash コマンドが影響を受けない
- 「profile に書いた deny が効かないかもしれない」事実が warning として可視化される

**短所**:

- 対象 entry の deny 効果が **silent fall-open** になる（warning 見落としリスク）
- ユーザーが warning を読まずに「~/.aws/** は守られていると思いこむ」hazard
- credential 保護を sandbox layer のみに依存している profile では実効的な穴が空く

**緩和**:

- profile-tightened は `permissions.deny` の `Read(~/.aws/*)` と二重化済（[`profile-tightened.json:52-53`](./profiles/profile-tightened.json)）。Layer 2（perms.deny）が生きていれば Layer 3（sandbox denyRead）の skip は許容範囲。
- Claude Code built-in credential redaction が Layer 1 として残る。
- warning は `.state/journal.jsonl` に `sandbox_deny_skipped` イベントとして 1 行 append する規約を Phase 3 仕様に含める。

### 4.2 案 B — Bootstrap 時に bwrap 起動失敗を catch して entry を retry-prune

**概要**: sandbox bootstrap で bwrap が "Can't create file at X" / "Can't mount tmpfs on Y" 系エラーを返したら、**該当 entry を削除して bwrap を再起動**する。リトライ予算（例: 5 回）以内に成功したら起動、超過したら policy に従い fail-closed か fall-open。

**長所**:

- profile 側に手を入れずに環境差異を runtime 側で吸収
- 案 A と異なり symlink 以外の原因（権限、ファイル不存在 etc.）にも汎用的に効く
- 実機で何が deny できなかったかが retry log に残る

**短所**:

- bwrap stderr の文字列パース依存（bwrap version 上げで壊れる）
- 起動コストが retry 分倍増（最悪 5 起動）
- silent fall-open の hazard は案 A と同根

### 4.3 案 C — Profile 生成時に symlink を解決して deny 先を rewrite

**概要**: `~/.aws/.env` のような entry を profile-gen で `realpath` 解決し、`/mnt/c/Users/<windows-user>/.aws/.env` に書き換える。同時に `/mnt/c/Users/<windows-user>/.aws` を sandbox の read allowlist に **自動追加**する（さもないと bwrap がアクセスできない）。

**長所**:

- deny 効果が実際に発動する（silent fall-open しない）
- file-level / wildcard 両方に適用可能

**短所**:

- WSL 専用ロジックが runtime に入る（`/mnt/c` 検出 / Windows path 扱い）
- read allowlist 拡大により **本来 sandbox から見えなかった `/mnt/c/Users/<windows-user>/`配下が露出**する副作用（credential 以外の Windows 個人ファイルが sandbox から read 可能になりうる）
- symlink 先が複数階層／別 symlink の場合のループ対策が必要
- 「symlink を尊重しない」設計判断は profile-as-source-of-truth 原則に反する（ユーザーが `~/.aws/X` と書いたものを runtime が静かに別パスに置換する）

### 4.4 案 D — Bootstrap 前に stub directory を namespace 内に挿入

**概要**: sandbox runtime が deny entry の parent を点検し、host 側で symlink になっているなら **bwrap 引数に `--tmpfs <parent>` を先に挿入**して parent を tmpfs で覆い、その上に `--bind /dev/null <entry>` を重ねる。

**長所**:

- deny は確実に発動（tmpfs 上の deny target file は bind 可能）
- profile を書き換えない（symlink 解決を sandbox view 内で完結）
- WSL に限らず symlink 系全般に効く

**短所**:

- bwrap 引数の組み立てロジックが複雑化（mount 順序依存・既存 mount との衝突回避）
- tmpfs で parent を覆うと **deny entry 以外の host 側 file が sandbox view から消える**（例: `~/.aws/config` を deny 対象にしていなくても `~/.aws` を tmpfs で覆うと見えなくなる）。これは round 3 の wildcard 案と等価の挙動で、retire の対象だった
- 部分 tmpfs（一部だけ覆って他は通す）は bwrap で表現困難

### 4.5 案 E — Profile-as-WSL-aware: 環境検出して deny set を切り替え

**概要**: profile-gen が host 環境を検出し（WSL 判定 / `~/.aws` symlink 判定）、WSL 環境では `sandbox.filesystem.denyRead/denyWrite` から `~/.aws/**` 等を **そもそも emit しない**。代わりに `permissions.deny` の `Read(~/.aws/*)` / `Read(~/.ssh/*)` のみで Layer 2 防御に絞る。Linux native 環境では従来通り Layer 2 + Layer 3 二重化。

**長所**:

- profile が「環境ごとに正しく動く」ことを保証
- silent fall-open ではなく explicit な「この環境では Layer 3 を張らない」判断
- profile 適用時の bootstrap 失敗が原理的に発生しない（WSL では Layer 3 を張らないため）

**短所**:

- profile 出力が環境依存になり「どの設定で何が deny されているか」が読み手に分かりにくくなる（同じ profile を読む人が WSL か Linux native かで実効 deny が変わる）
- Phase 1（Issue #378）の `role_configs_schema.json` に `sandbox` field を追加する設計に「環境分岐」概念が必要になる
- WSL 上の Layer 3 が無いため、Layer 2 hook / classifier を擦り抜けるパスがあった場合の defense-in-depth が薄くなる

## 5. 比較サマリと採用案

| 観点 | 案 A: skip+warn | 案 B: retry-prune | 案 C: symlink rewrite | 案 D: tmpfs stub | 案 E: env-aware |
|---|---|---|---|---|---|
| 実装コスト | 小 | 中 | 中〜大 | 大 | 中 |
| 起動失敗の解消 | ◯ | ◯ | ◯ | ◯ | ◯ |
| Layer 3 deny 発動 | ✗（skip） | ✗（prune） | ◯ | ◯ | ✗（emit せず） |
| profile 透明性 | ◯（warning が出る） | △（実機まで分からない） | ✗（silent rewrite） | ◯ | ◯（環境注記付き） |
| WSL 以外への汎用性 | ◯ | ◯ | △ | ◯ | △（環境分岐前提） |
| 副作用 | silent fall-open hazard | retry コスト + 文字列パース | `/mnt/c` 露出 | tmpfs で parent 全 mask | 環境間で profile 実効差 |
| Layer 1+2 残存 | ◯ | ◯ | ◯ | ◯ | ◯ |

### 5.1 採用案: 案 E（環境検出による Layer 3 出し分け） + 案 A（fallback as warn-only safety net）

理由:

1. **「symlink 環境で Layer 3 を張らない」は素直な設計**であり、profile reader に対して explicit に「WSL では sandbox layer ではなく perms layer で守る」と読ませられる。silent fall-open（案 A 単体）は security review 観点で受け入れにくい。
2. **profile 透明性** — profile-gen 出力に「detected platform: wsl, layer-3 ~/.aws denylist suppressed」が機械可読 metadata として残る。journal にも 1 行記録する。
3. **Layer 1 + Layer 2 の二重化が round 2 / 3 で実機 confirm 済**（[`profile-tightened.json:52-53`](./profiles/profile-tightened.json) の `Read(~/.ssh/*)` / `Read(~/.aws/*)` と Claude Code built-in credential redaction）。Layer 3 不在の WSL でも secret 露出は防がれることが round 3 §4.3 で確認できている（`cat ~/.aws/credentials` の stdout は空のまま）。
4. **案 A を fallback として併設** — 環境検出ロジックが新たな symlink パターン（例: `~/.config/X` が symlink）を取りこぼした場合に、bootstrap 失敗を起こさず warn-only で skip する safety net として残す。ただし WSL の `~/.aws` 等は案 E で先に消えるため、案 A の出番は edge case のみ。
5. **案 C（rewrite）を不採用** — `/mnt/c` を sandbox 視野に入れる副作用が大きく、Windows 個人ファイル全般を sandbox から読めるようにする trade-off は受け入れがたい。
6. **案 D（tmpfs stub）を不採用** — round 3 で wildcard 案が tmpfs で parent 全 mask になる挙動を retire した経緯があり、それと等価の副作用（`~/.aws/config` 等 deny 対象外も見えなくなる）が再発する。
7. **案 B（retry-prune）を不採用** — 文字列パース依存と起動コスト倍増の trade-off に対し、案 A の warn-only skip で同じ目的が達成できる。

### 5.2 採用案の policy 要件（Phase 3 仕様化）

a. **環境検出ルール**:
   - WSL 判定: `/proc/version` に `Microsoft` / `WSL` を含む、または `/proc/sys/kernel/osrelease` に `microsoft-standard-WSL` を含む。
   - 個別 path 検出: deny entry ごとに `os.path.realpath()` 解決し、`/mnt/c/` / `/mnt/d/` 配下に解決される場合は「Windows 側ファイル」と判定。
   - 二段検出（WSL でなくても、対象 path が他の symlink で外部に出ている場合は同じ扱い）。

b. **profile-gen 動作**:
   - WSL かつ `~/.aws` / `~/.ssh` 等が dangling symlink（sandbox view 外解決）の場合、`sandbox.filesystem.denyRead` / `denyWrite` の該当 entry を **emit しない**。
   - 同時に `permissions.deny` 側に `Read(~/.aws/*)` / `Read(~/.ssh/*)` を **必ず emit**（既存設計と同じ）。
   - profile 出力上部に `$comment` で「platform=wsl, layer-3 entries suppressed: [list]」と機械可読 metadata を残す。

c. **bootstrap fallback（案 A）**:
   - profile に Layer 3 entry が残っている状態で bwrap 起動を試み、`Can't create file at` / `Can't mount tmpfs on` エラーを検知したら、当該 entry を skip して 1 回だけ retry。
   - skip 時は `.state/journal.jsonl` に `sandbox_deny_skipped` イベントを 1 行 append（`reason="bwrap_bootstrap_failure"`, `entry`, `bwrap_stderr_excerpt`）。
   - retry も失敗するなら `failIfUnavailable` 設定に従い fail-closed か fall-open。

d. **failIfUnavailable の意味再定義**（round 3 §8 残課題 3 の解消）:
   - `failIfUnavailable: true` = bwrap 不在 / bwrap 起動失敗 / mount 失敗のいずれでも sandbox 起動を fail-closed（プロセス起動失敗）。
   - `failIfUnavailable: false`（default） = bwrap 不在のみ fall-open（disabled で起動）、bwrap 起動失敗 / mount 失敗は **bootstrap fallback (c) の skip-and-retry を経由**してから判断。
   - profile-tightened は WSL では (c) 経由で Layer 3 が剥がれて起動成功。Linux native では Layer 3 が活きたまま起動成功。

e. **可観測性**:
   - `/sandbox` status 出力に「suppressed entries: [list]」セクションを追加。
   - `claude-org-runtime settings show` 等で profile-gen 後の最終 deny set を表示できるようにする。
   - journal イベントは curator skill / dispatcher monitoring で検知できる名前空間に揃える。

## 6. 採用案の trade-off と残るリスク

1. **WSL での Layer 3 不在 → defense-in-depth の薄さ** — round 2 / 3 の実機で Layer 1 + 2 で credential が漏れないことは確認済だが、将来 hook 追加・classifier 変更で Layer 2 が壊れた場合に WSL では検知が遅れる可能性がある。緩和策として: profile-tightened の `permissions.deny` の `Read(~/.aws/*)` を Phase 2 hook（[`block-secret-read.sh`](./../../) 等。新規）で **dual layer** 化する案を Issue #379 で別途検討する。
2. **環境検出の偽陽性 / 偽陰性** — `/mnt/d`, `/mnt/wsl`, devcontainer の `/workspaces` symlink 等で同様の症状が出うる。検出ロジックは「symlink で sandbox の read allowlist 外に解決される」を実体的判定基準とし、`/mnt/c` 直書きは避ける。
3. **profile diff の env-dependency** — 同じ profile-tightened.json から異なる sandbox cmd-line が生成される（WSL/Linux native で異なる）。CI / レビュー時の diff 解釈に注意が必要。`claude-org-runtime settings show --explain` で「emit suppression reason」を表示できるようにし、レビュー時の混乱を抑える。
4. **Phase 1 schema への影響** — Issue #378（`role_configs_schema.json` に `sandbox` field 追加）に環境別 emit 概念が入るため、schema は「profile-as-input」と「emit-as-output」の 2 段に分け、profile-as-input は環境非依存に保ちたい。emit ロジック側に platform branching を寄せる。
5. **journal イベントの量** — 起動ごとに skip イベントが出ると累積する。`sandbox_deny_skipped` は同一構成では起動 1 回に 1 イベント、構成変更時のみ重複検知でカウントするデバウンス規約を curator 側で持つ。

## 7. スコープ外（後続 worker への引き継ぎ）

実装は本 worker の責務外。以下は採用案を実装する後続 worker（Issue #392 / Issue #378 連動）が扱う:

- `claude-org-runtime settings generate` の platform 検出ロジック追加
- `role_configs_schema.json` の `sandbox` field 追加（Phase 1 / Issue #378）
- bwrap stderr パーサとリトライ実装（案 A 部分の bootstrap fallback）
- `.state/journal.jsonl` の `sandbox_deny_skipped` event スキーマ確定
- profile-tightened.json の `$comment` 更新（emit 後の suppression metadata 注釈）
- runbook §sandbox 実機検証 への WSL 注記追加（[`docs/verification.md`](../verification.md)）

## 8. 関連リソース

- [`docs/sandbox-probe/notes/iteration-b-round3-results.md`](./notes/iteration-b-round3-results.md) — 本 policy が解こうとしている実機症状の詳細（特に §4.3, §6, §7.2, §8 残課題）
- [`docs/sandbox-probe/profiles/profile-tightened.json`](./profiles/profile-tightened.json) — Layer 2 (`permissions.deny`) と Layer 3 (`sandbox.filesystem.denyRead/denyWrite`) の二重化が定義された profile
- [`docs/sandbox-probe/notes/iteration-b-round2-results.md`](./notes/iteration-b-round2-results.md) — Layer 2 perms.deny で `.env` / credential 系が deny に転じる実機 confirm
- [`docs/verification.md`](../verification.md) §sandbox 実機検証 — bubblewrap 前提と現行 verification 手順
- [`docs/worker-permissions-design.md`](../worker-permissions-design.md) — `additionalDirectories` の design 注釈
