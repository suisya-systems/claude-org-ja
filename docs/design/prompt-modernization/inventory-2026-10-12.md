# ja 指示書・スキル棚卸し（2026-10-12、ja main 31a5c40）

この文書は、2026-10-12 時点の ja main（31a5c40）に対して読み取り専用で行った指示書・スキル棚卸しの結果をまとめた原本である。
対象は窓口・振り分け役・作業者が読む 61 ファイル（13,775 行）で、節ごとに keep / move / delete / outdated / transport に分類した。分類は作業者による反証検証を経ている（§1-5）。
**Codex のレビューは受けていない。** delete 判定は、実装する各ワークストリームで正本と突き合わせて確かめ直すこと。
計画は [`docs/design/prompt-modernization-and-repo-split.md`](../prompt-modernization-and-repo-split.md) にあり、本書はその根拠資料である。行番号はすべて 31a5c40 時点のもの。

## 1. 要約

### 1-1. 規模

- **総行数は 13,775 行（61 ファイル）。** 内訳は 992,223 字（ASCII 673,215 / 非 ASCII 319,008）、推定 約 455k〜519k トークン。
  - 計測: `find CLAUDE.md .dispatcher .claude/rules .claude/skills -name '*.md' | xargs wc -l -m`
  - 生成元の `SKILL.md.in` など `.in` の 19 ファイルは合計に含めていない。分類と行数は、実際に読まれる生成物の側で取った。
- **トークンの推定式**は「ASCII 文字数 / 4 ＋ 非 ASCII 文字数 × 0.9〜1.1」。tokenizer で実測した値ではない。パスやコード片は 1 トークンあたり約 3 文字で切れやすいため、ASCII 部分は少なめに出ている可能性がある。

### 1-2. 起動時に毎回読まれる量

自動で読み込まれるのは次の 4 つ。

- cwd とその祖先にある CLAUDE.md
- `.claude/rules/pr-ci-watch.md`（10 行。`paths:` が無いので常に読まれる。`.claude/rules/pr-ci-watch.md:1-3`）
- スキル 26 個の description（6,659 字、約 3.9k〜4.6k トークン）
  - `disable-model-invocation` は 0 件なので、全役割に 26 個すべてが載る。
  - `owner:` キーで絞り込まれることは無い。
- SessionStart による注入は無い（`.claude/settings.json` の hooks は PreToolUse だけ）。

| 役割 | 自動（毎回） | 起動または 1 サイクルで必読の分を含む合計 | 出典 |
|---|---|---|---|
| 窓口（secretary） | 150 行 / 23,974 字 / 約 12.3k〜14.3k | **1,292 行 / 106,517 字 / 約 50.8k〜58.2k**（`/org-start` 543 行 ＋ capability ゲート 599 行。後者は `org-start/SKILL.md:369` の Block D-2 が無条件で Read させる） | `renga-layouts/ops.toml:29-33`, `CLAUDE.md:17` |
| 振り分け役（dispatcher） | 438 行 / 53,914 字 / 約 25.6k〜29.4k（祖先の窓口用 CLAUDE.md 140 行を含む。祖先分は推測） | **ワーカー 1 人の派遣で 3,655 行 / 411,164 字 / 約 192k〜219k** | `.dispatcher/CLAUDE.md:25-31`（DELEGATE 時の必読 5 ファイル、1,259 行）、`:81`（spawn ごとにゲート 599 行）、`dispatcher-resume/SKILL.md:356`（`worker-monitoring.md` 1,359 行を 1 サイクルごとに読む） |
| 作業者（worker） | 通常のワーカーは 約 233〜251 行 / 約 22,000 字 / 約 11.4k〜13.2k（brief 198 行 ＋ 初回の指示文）。ja を自分で編集するワーカー（Pattern B）は 約 486 行 / 約 55,500 字 / 約 27.6k〜31.8k（推測を含む） | 同左。条件付きで `renga-error-codes.md` 396 行が加わる | `tools/gen_worker_brief.py:414-417` |

- **最も重いのは dispatcher。** ワーカー 1 人を派遣した時点で、sonnet の 200k context とほぼ同じか、それを超える（推定）。
  - 大部分は `worker-monitoring.md` 1,359 行と、DELEGATE 時の必読 5 ファイル 1,259 行。
  - Read を offset 指定で部分的に読んでいる可能性はある（推測）。
- **作業者に届く brief の生成元は `worker-claude-template.md` ではない。**
  - 実際に使われるのは `tools/templates/worker_brief_normal.md` と `worker_brief_self_edit.md`（`tools/gen_worker_brief.py:46,414-417`）。
  - `git grep worker-claude-template -- tools tests` は 0 件。
  - したがって、このテンプレートを削っても作業者が読む量は変わらない。

### 1-3. 分類ごとの行数と削減見込み

| 分類 | 行数 | 構成比 | 削減の扱い | 削減行数 |
|---|---|---|---|---|
| keep | 11,178 | 81.1% | 残す | 0 |
| move | 809 | 5.9% | 常時読込から外れる | 809 |
| delete | 930 | 6.8% | 全行削る | 930 |
| outdated | 370 | 2.7% | 修正するだけで削らない | 0 |
| transport | 488 | 3.5% | 案ごとに別記（下表） | – |
| **計** | **13,775** | 100% | | **1,739（12.6%）＋ transport 分** |

transport の 488 行は分類段で付いた行数。案ごとの削減見込みは輸送層の評価段が別の方法で数えた値で、この 488 行の部分集合ではない（§4-H の既知の制限を参照）。

| transport 案 | 削減見込み | 合計（delete＋move＋transport） |
|---|---|---|
| a: renga に一本化 | 約 360 行。org-attach と org-down を廃止すればさらに ＋476 行 | 約 2,099 行（15.2%）、廃止込みで約 2,575 行 |
| **b: 1 か所に集約（推奨）** | 約 205 行（約 100KB、推測で約 25k〜33k トークン） | 約 1,944 行（14.1%） |
| c: 現状維持 | 0 | 1,739 行（12.6%） |

### 1-4. 起動時常時読込への効果

ここの数字は、節 3 の表のうち行数上位 120 件のリストに現れた分だけを足した**下限値**。

- **窓口**（起動ごとの 1,292 行に対して）: 約 191 行減（約 15%）。
  - `CLAUDE.md` の delete 17 行（`:43-51`, `:73-80`。後者は誤 delete の疑い #8 に当たる）
  - `org-start` の delete と move 61 行（`:37-46`, `:54-71`, `:282-292`, `:405-417`, `:433-441`）
  - capability ゲートの move のうち、リストに出た 113 行。群の担当者は全体を約 160 行と見積もっており、それならゲートを Read 1 回あたり約 27% 減らせる。
  - transport を b 案にすると、窓口が読む注記は 12 行 / 4.1KB と 10 行 / 3.4KB から、約 11 行 / 4KB に縮む。
- **dispatcher**（自動で読む 438 行のうち）: 約 43 行減。
  - `.dispatcher/CLAUDE.md:223-248` の 26 行と、祖先 CLAUDE.md の 17 行。
  - 派遣 1 回あたりの 3,655 行では 約 422 行減（約 11.5%）。内訳は spawn-flow 39、pane-layout 31、renga-error-codes 136、ゲート 113、worker-monitoring 60。
  - `worker-monitoring.md:319-431`（113 行）を切り出すかどうかは判断事項。
- **作業者**: 対象範囲内での削減効果はほぼ 0。
  - brief の生成元 `tools/templates/` は今回の対象範囲の外。
  - `worker-claude-template.md` は作業者に届いていない。
  - 効果が出るのは、`instruction-template.md:197-231`（AUTO テンプレート）を短くする場合だけ（判断事項）。

### 1-5. 方法

1. 11 のファイル群を並列に分類した（見出し単位。40 行を超える節は段落単位に分けた）。
2. 群ごとに反証検証をした。対象 333 件のうち **32 件で分類が変わった**。
3. ファイルをまたいで重複を検出した（クラスタ C1〜C15）。
4. 別のエージェントが起動時読込量の測定と、輸送層の選択肢評価をした。

分類に失敗したグループは無い。行範囲の被覆ずれも無い。

## 2. 削減効果の大きい順の上位 15 件

この 15 件はクラスタ単位で数えており、分類別の合計（§1-3）とは一部の行が重なる。単純に足し合わせないこと。

| # | 対象 | 分類 | 削減行数 | 根拠の要点 |
|---|---|---|---|---|
| 1 | `.claude/skills/org-delegate/references/worker-claude-template.md:12-400`（C1） | delete（大半）、`:1-11` は outdated | 約 370 | 作業者に届いていない複製。<br>正本は `tools/templates/worker_brief_normal.md` と `worker_brief_self_edit.md`（`tools/gen_worker_brief.py:46,415`）。<br>直近の 96282ca / 176e70a / 7d5109c はこのテンプレートしか変えておらず、内容が届いていない。<br>**注意: テンプレートにしか無い条項がある**（rebase、uv/pip、Codex の allowed_domains、safety block ほか）。そのまま消すと、どこにも残らない（§4-B、誤 delete の疑い #1〜7） |
| 2 | 輸送層の二重記述。36/39 ファイル、Epic #6 Issue G の但し書き 25 ファイルを含む | transport | b: 約 205（a: 約 360） | 二重記述の語を含む行は 205 行 / 151,751B。<br>注記ブロックは合計 約 291 行（スキル側 233 ＋ transport-duality.md ×3 の 29 ＋ renga-error-codes の broker 節 29）。<br>共有ヘッダのフラグメント 4 本を直せば、生成物 12 本の注記がまとめて縮む（`git grep -l dual-system-header -- '*.in'`）。§4-A を参照 |
| 3 | `.claude/skills/org-delegate/references/capability-first-drive-operational-gate.md`（`:8-21`, `:46-57`, `:157-164`, `:259-274`, `:297-312`, `:514-525`, `:565-587`, `:588-599` ほか） | move | 約 160（リストに出た分は 113） | 契約条文の引用、書き方の規約、保守用の台帳、理由の説明。<br>窓口は起動ごとに、dispatcher は spawn ごとに全 599 行を Read する（`org-start/SKILL.md:369`, `.dispatcher/CLAUDE.md:81`）。<br>`:104-111` は outdated: renga の下限が 2.0.0 になった（`tools/check_renga_compat.py:113`） |
| 4 | `.claude/skills/org-delegate/references/renga-error-codes.md:186-313`（＋`:314-321` は move） | delete | 128（＋8） | 擬似的なシェルの case 文。呼んでいる関数 `mark_worker_pane_closed` / `reresolve_peer_id` は、git grep で本ファイル以外に 0 件。<br>分岐の内容は同じファイルの `:18-35`, `:74-97`, `:98-144` の再掲。<br>move にする選択肢は §4-F |
| 5 | Group B の「自タブ確立は 2 手段・same_tab は数えない」（C2）。13 か所（`org-suspend:98-167`, `pr-watch-pane:402-409,439-446,530-544`, `org-attention-stop:55-69`, `org-attention-start:178-186`, `.dispatcher/CLAUDE.md:223-230`, `spawn-flow.md:113-125` ほか） | delete（重複）と move（経緯 `org-suspend:154-163`） | 約 120 | 同じ規則を書き下した箇所が約 150 行ある。正本候補は `.dispatcher/references/pane-close.md:112-121`。<br>ただし `renga-error-codes.md` には定義が無い（`grep 確立手段\|単一タブモデル` で 0 件）。正本を 1 か所に新しく置く判断が要る |
| 6 | close 判定表の再掲: `pane-close.md:404-422`, `:496-513`, `:537-551`、`.dispatcher/CLAUDE.md:223-248` | delete | 78 | 正本は `worker-monitoring.md` Step 5.3 の close 判定表（`:1238-1279`。編集は `.in` 側）。<br>再掲している側が自分で「正本は上記 Step 5.3」と書いている（`pane-close.md:421`, `:484`, `:549`） |
| 7 | `.claude/skills/org-start/SKILL.md:37-46`, `:54-71`, `:282-292`, `:405-417`, `:433-441` | delete 10 ／ move 51 | 61 | キュレーターを起動しない旨が同じファイル内に 5 回出てくる（frontmatter:7, :207, :384, :386, :431, :448）。<br>14 種のタブ scope の分類、C2 の設計メモ、引数の補足、wall-clock の表は手順ではない。<br>窓口が起動ごとに読む |
| 8 | `.dispatcher/references/worker-monitoring.md:920-928`, `:829-839`, `:1022-1031`, `:1037-1045`, `:1339-1350`, `:1351-1359` | move | 60 | 動機、インシデントの経緯、設計メモ、cadence の根拠。<br>`:1355` の journal.jsonl は M4 で廃止済み。<br>このファイルは dispatcher が 1 サイクルごとに読む。<br>outdated として `:507-518`, `:530-539`, `:589-608` の `py -3`（この環境に py は無い）がある |
| 9 | `.claude/skills/org-conveyor/SKILL.md:56-68`, `:97-107`, `:218-226`, `:227-237`, `:238-246` | move 13 ／ delete 40 | 53 | 位置づけの比較表は move。<br>退出条件は `references/exit-conditions.md:8-16`、verify の要約は `:169-173`、Non-goals は INV（`:69-81`）の再掲。<br>`:97-107` は誤 delete の疑い #9 に当たる |
| 10 | `.claude/skills/pr-watch-pane/SKILL.md:46-54`, `:237-251`, `:367-381`, `:512-521` | move 24 ／ delete 25 | 49 | `[name_taken]` の 3 条件は `:557-578` の再掲で、本文も自分で SoT はそちらと書いている。<br>自分で close する場合の分類は `:96-123` の再掲。<br>nocorrect の理由は `rationale.md:31-35` に既にある |
| 11 | `.claude/skills/runtime-release-with-paired-ja-sync/SKILL.md:41-61`, `:90-99`, `:134-145` | move 33 ／ delete 10 | 43 | 「なぜ必要か」と、release.yml が検証しない 2 つの失敗は理由の説明。<br>pre-fetch は `org-delegate/SKILL.md:172-180` と `release-pre-fetch.md` の重複（誤 delete の疑い #10） |
| 12 | `.claude/skills/org-suspend/SKILL.md:294-311`, `:337-348`, `:747-756`（C2 以外の部分） | delete 18 ／ move 22 | 40 | `:294-295` は自分で「正本は reference 側、複製しない」と書きながら再掲している（正本はゲートの §3-A `:318-344`）。<br>他の 2 か所は理由の説明 |
| 13 | `.dispatcher/references/spawn-flow.md:288-295`, `:412-442` | move | 39 | Worker Directory Registry の付録は、本文自身が「手順の続きではない」と書いている（`:410`）。<br>`:335-352` と `:357-374` は outdated（ファイル内の矛盾と、root 相対の DB パス）。修正だけなので削減は 0 |
| 14 | `.claude/skills/org-delegate/references/pane-layout.md:46-56`, `:57-68`, `:88-95` | delete 19 ／ move 12 | 31 | アルゴリズムは `spawn-flow.md:36-59` が、CLOSE_PANE は `pane-close.md` Step 3 が SoT（本文が自分でそう書いている）。<br>`:22-29` は outdated（curator 行に `model="sonnet"` が無い。`pane-close.md:320` と比べて） |
| 15 | work-discovery の triage scan（C6）: `.dispatcher/CLAUDE.md:259-288` と、`$REPO_FLAGS` の念押し 3 か所 | delete（重複） | 約 31 | 正本は `pane-close.md` Step 6（`:575-710`）。<br>dispatcher が常時読む `.dispatcher/CLAUDE.md` 側を 4 行に縮める。<br>念押しは 7bda7af（2026-08-06 #832）より後に再発が無い（`git log -S'REPO_FLAGS'`） |

圏外だが削減のあるもの:

- C3: messaging 文脈の pane_not_found。約 30 行だが、矛盾を先に解消する必要がある。
- C4: Codex の手順。`instruction-template.md:83-93`、約 13 行。
- C7: awaiting_user。約 16 行。
- C5: state-db cutover。約 20 行。
- C9: 軽量レーン。約 10 行で、正本の修正が先。

## 3. ファイルごとの節単位の表

### CLAUDE.md（140 行: keep 88 / move 10 / delete 27 / outdated 3 / transport 12）

| 行範囲 | 節 | 分類 | 根拠 | 行数 |
|---|---|---|---|---|
| CLAUDE.md:1-4 | 冒頭（窓口の役割宣言） | keep | 役割定義。secretary の起動時に毎回読まれる root CLAUDE.md の前提。代替記述なし | 4 |
| CLAUDE.md:5-15 | 事実は出典付きで書く（citation-or-abstain） | keep | docs/contributing/citation-or-abstain.md 現存（ls OK）。導入 da5c2d1 2026-07-27 (#798)、157a73a/8ea28e6 で改訂継続。詳細は docs 側に既に分離済みで本文は要点のみ | 11 |
| CLAUDE.md:16-18 | 起動時 | keep | .claude/skills/org-start/SKILL.md 現存。curator オンデマンド化と整合（.dispatcher/references/pane-close.md Step 5、tools/check_curate_threshold.py 現存） | 3 |
| CLAUDE.md:19-27 | コミュニケーション（内部配管を実況しない） | keep | 09e9fda 2026-09-03 (#987) で追加された直近の規則。registry/projects.md は tools/ensure_projects_registry.py が生成（org-start:152-169） | 9 |
| CLAUDE.md:28-39 | 輸送層（transport）両系 — 既定 renga / opt-in broker ＋ 二フレーム注記 | transport | broker/renga 併記・MCP 名読み替え・Epic #6 Issue G 但し書き・二フレーム注記(Refs #604)。見出し「既定 renga」はコード既定と逆: tools/transport.py:19-20 は runtime 0.1.28 で broker へフリップ、scripts/install.sh:268,281,283 も broker 既定。38 行目の #941 経緯は履歴 | 12 |
| CLAUDE.md:40-42 | PR 後の CI 監視 | delete | 同じ規則が .claude/rules/pr-ci-watch.md:7-9（全ファイル適用）と .claude/skills/org-pull-request/SKILL.md:63,113 にある（起動契機・Bash 背景禁止・孤児化理由まで同文）。hook .hooks/block-adhoc-pr-watch.sh が settings.json:92-111 で強制<br>**補足**: 正本: 起動手順は .claude/skills/org-pull-request/SKILL.md:63、禁止規律は .claude/rules/pr-ci-watch.md:7-10<br>**検証**: 反証で維持: pr-ci-watch.md:7-9 が経路限定・Bash/直接起動禁止・hook 強制を持ち、起動契機は org-pull-request/SKILL.md:63（.in 側で編集）にある。CLAUDE.md:41 にしか無いのは『手動 ! 実行は人間が張り付く緊急経路のみ』という例外 1 句。削除すると規則が厳しくなる方向なので安全側。必要なら pr-ci-watch.md へ移す。`git grep` では tests/tools に本文を参照する箇所は 0 件。 | 3 |
| CLAUDE.md:43-51 | PR マージ後の次タスク提案（proactive next-dispatch） | delete | org-pull-request/SKILL.md:338-345 (2b-iii) が post_merge トリガ・propose-only・ゴール台帳順を同内容で持つ。ただし同 :340 が本節を「方針の一次参照」と呼んでいるので参照の付け替えが要る<br>**補足**: 正本: .claude/skills/org-pull-request/SKILL.md:338-345（手順を実行する場所）。削除時は同 :340 の CLAUDE.md 参照を外す<br>**検証**: 反証で維持: org-pull-request/SKILL.md:338-345（2b-iii）が post_merge トリガ・ゴール台帳順・§5.2 提示・propose-only・spawn 禁止を同じ強さで持つ。CLAUDE.md 側だけにある条件は見つからない。両者が互いを参照し合っている（:340 は CLAUDE.md を『一次参照』、CLAUDE.md:50 は 2b-ii を指す）ので、削除時は :340 の参照を外す。tests/tools からの参照は 0 件。 | 9 |
| CLAUDE.md:52-54 | ドキュメント表記 | keep | docs/contributing/markdown-conventions.md 現存、検証スクリプト tools/audit_link_paths.py 現存（同 md:38,54 が参照） | 3 |
| CLAUDE.md:55-60 | 役割の境界（窓口がやること・3 スキル分割） | keep | org-delegate / org-escalation / org-pull-request の SKILL.md 現存。スキル索引として機能。60 行目の「Issue #320 のキャリーアウト」は 1 句の経緯で影響小 | 6 |
| CLAUDE.md:61-63 | 窓口 handover / resume への導線 | keep | secretary-handover / secretary-resume SKILL.md 現存。ポインタのみ | 3 |
| CLAUDE.md:64-65 | ディスパッチャー handover の窓口側打鍵規律 | keep | 65 行の「skill 側に未記載」を確認: dispatcher-handover/SKILL.md・dispatcher-resume/SKILL.md を grep しても text/Enter 2 段・15 秒 poll の記述なし（:29,:202-209 は打鍵指示のみ）。9457860 2026-07-31 (#813)、6889d66 2026-09-04 | 2 |
| CLAUDE.md:66-68 | 実作業は全てワーカーに委譲する | keep | 窓口の基本制約。org-delegate/SKILL.md:59-61 がこの原則文を CLAUDE.md から引用（SoT はこちら） | 3 |
| CLAUDE.md:69-72 | タスクルーティング 2 レーン制 冒頭（パイロット実証） | move | 71 行は 2026-06-12 のパイロット 2 走 (#546/#545) の実証経緯で手順ではない。org-delegate/SKILL.md:61 も「背景・実証は CLAUDE.md」と経緯参照に使っている<br>**検証**: 反証で維持: 71 行目は 2026-06-12 のパイロット（#546/#545）で 18 分と実証した経緯だけで、手順は含まない。69 の見出しは節の骨格なので残す。移すなら org-delegate/SKILL.md:61 の『背景・実証は CLAUDE.md』を移動先に付け替える。 | 4 |
| CLAUDE.md:73-80 | 軽量レーンの発動条件 | delete | org-delegate/SKILL.md:59-74（レーン選択判定の表と『迷ったら重量』）と同一の 4 条件。判定は org-delegate 内で窓口が実行と明記（:59）<br>**補足**: 正本: .claude/skills/org-delegate/SKILL.md:59-74<br>**検証**: 反証で維持: org-delegate/SKILL.md:63-74 の表が同じ 4 条件・Agent isolation=worktree・『迷ったら重量』を持つ。CLAUDE.md 側だけの条件は無い。ただし CLAUDE.md:66-67 が『下記軽量レーン条件』と前方参照しているので、その文言は org-delegate への参照に直す必要がある。 | 8 |
| CLAUDE.md:81-85 | 軽量レーンの必須条件（背景起動・Codex・人間ゲート） | keep（当初 delete） | org-delegate/SKILL.md:75-78 に同じ 3 条件。ただし org-delegate:69,76 は『run_in_background=true 必須』のままで、本ファイル:82 と .hooks/block-foreground-subagent.sh:13-25（パラメータ廃止、40c33f1 2026-08-19 #944）が正しい<br>**補足**: 正本は org-delegate/SKILL.md:75-78 だが、先に同 :69,:76 を『パラメータ廃止・常時背景（hook はキーが存在し true でない時のみ deny）』へ直してから本節を削る。82 行の #942 経緯は rationale へ<br>**検証**: 反証で delete→keep: 正本とされた org-delegate/SKILL.md:69,76 は『run_in_background=true 必須』のままで古い。現行の事実（パラメータ廃止・キーがあり厳密 true でないときだけ deny）を書いているのは CLAUDE.md:82 だけで、これは .hooks/block-foreground-subagent.sh:11-27（40c33f1 #944）と一致する。正しい側を消すと誤記だけが残るので、org-delegate を直すまでは keep。82 行目の #942 経緯は後で move 候補。 | 5 |
| CLAUDE.md:86-87 | 重量レーンが必須のケース | delete | org-delegate/SKILL.md:66-71『重量レーンへ必ず倒すケース』と同内容（判断境界/escalation/日またぎ/常駐監視）<br>**補足**: 正本: .claude/skills/org-delegate/SKILL.md:66-71<br>**検証**: 反証で維持: org-delegate/SKILL.md:66-71 が同じ 4 ケース（判断境界/escalation・日またぎ・常駐監視）を『軽量条件を一部満たしても優先』という同じ強さで持つ。CLAUDE.md:86 自身も org-delegate を参照している。 | 2 |
| CLAUDE.md:88-89 | 重量レーンの ultracode 武装 | delete | org-delegate/SKILL.md:82-96 が 3 基準・kickoff 武装・前段位置づけを同内容で持ち、本節自身が『武装の実装詳細は org-delegate』と参照。fa5542e 2026-08-29 で両方改訂<br>**補足**: 正本: .claude/skills/org-delegate/SKILL.md:82-96（＋ .dispatcher/references/spawn-flow.md 3-5a）<br>**検証**: 反証で維持: org-delegate/SKILL.md:82-96 が 3 基準・機械的タスクは非武装・rate limit・--impl-guidance・『brief 許可は必要条件、send_keys kickoff が発動条件』・前段位置づけを同文で持つ。CLAUDE.md 側だけの条件は無い。tests/tools からの参照は 0 件。 | 2 |
| CLAUDE.md:90-97 | worker への追加依頼の境界（1 worker = 1 task = 1 scope） | keep | org-delegate/SKILL.md:288 が『canonical な 3 rule は CLAUDE.md を SoT 参照』と明記。knowledge/curated/delegation.md:133 に 1 worker 1 scope 内の運用例。org-delegate:290-292 側が重複 | 8 |
| CLAUDE.md:98-99 | 違反事例（2026-05-21 voice-v2-independent） | move | 事故の経緯と『guard は別 Issue』の注記で手順ではない<br>**検証**: 反証で維持: 98 行目は 2026-05-21 の違反事例と『guard は別 Issue』の注記だけ。手順の 3 rule は 92-96 で完結している。 | 2 |
| CLAUDE.md:100-103 | ワーカー peer message には必ず ack | keep | org-delegate/references/ack-template.md 現存。ack 欠落で worker が idle dead-lock する制約。全メッセージに効く規則なので常時ロード側に置く意味がある | 4 |
| CLAUDE.md:104-107 | retro gate ack の宛先 | keep | tools/dispatcher_retro_gate.py 現存（stdin の check_messages 結果で判定、:1-30）、consumer .dispatcher/references/pane-close.md:17,48。事故記録 knowledge/curated/dispatcher-monitoring.md:69-88（2026-05-09） | 4 |
| CLAUDE.md:108-111 | awaiting_user 通知の目的説明 | move | 110 行は通知が必要な理由の説明（画面前にユーザー不在で放置される）で手順ではない<br>**検証**: 反証で維持: 110 行目は理由の説明（不在時に放置される・classifier が urgent にマップ）が中心。手順は対象 gate（112-116）と emit 形（118-124）で完結している。ただし 108 の見出しと 110 冒頭の『停止 gate で 1 行 emit する』の 1 文は節の要旨なので残す。 | 4 |
| CLAUDE.md:112-125 | awaiting_user 対象 gate（4 箇所）と canonical emit 形 | keep | tools/journal_append.sh 現存。org-delegate:362-366 / org-escalation:59-76 / org-pull-request:71-75,204 が本節を参照先にしている（4 gate のハブ）。runtime classifier.py / tools/templates/attention.example.json:28,88 に secretary_awaiting_user | 14 |
| CLAUDE.md:126-128 | 通知側の挙動（並走の runtime PR が追加する） | outdated | 未来形で書かれているが実装済み: 導入済み runtime 0.1.49 の claude_org_runtime/attention/classifier.py・config.py に secretary_awaiting_user、tools/templates/attention.example.json:28 で urgent<br>**補足**: 『runtime の attention classifier が kind=awaiting_user を secretary_awaiting_user (default urgent) にマップする』と現在形に（出典: tools/templates/attention.example.json:28,88、runtime attention/classifier.py）。同句は org-delegate:366 / org-escalation:76 / org-pull-request:75 にもある<br>**検証**: 反証で維持: 導入済み runtime（site-packages/claude_org_runtime-0.1.49.dist-info）で実装済み。classifier.py:52 が "awaiting_user": "secretary_awaiting_user"、config.py:50 が urgent、tools/templates/attention.example.json:28 も urgent。未来形の『並走 PR が追加する』は事実と食い違う。同じ句が org-escalation/SKILL.md の 4.5 にもある。 | 3 |
| CLAUDE.md:129-137 | ワーカーからの判断仰ぎは人間にエスカレーション（一次承認禁止） | keep | org-escalation/SKILL.md:4-7,43-44 と内容が重なるが、受信時に毎回効くトリガ規則で常時ロードに置く意味がある（判断事項に挙げる） | 9 |
| CLAUDE.md:138-140 | 状態保存・pending-decisions register（必須） | delete | org-escalation/SKILL.md:46-62 が Progress Log 追記・journal_append.sh worker_escalation（:51）・pending_decisions.py append/resolve（:55,:62）を手順として持つ。本節自身が『org-escalation を一次参照（Issue #320 で SoT 化）』と書いている<br>**補足**: 正本: .claude/skills/org-escalation/SKILL.md:46-76。CLAUDE.md には『判断仰ぎ受信時は /org-escalation を回す』の 1 行だけ残す<br>**検証**: 反証で維持: org-escalation/SKILL.md:47-76（Step 2〜5）が Progress Log 追記・journal_append.sh worker_escalation・pending_decisions append/resolve/mark-user-replied を同じ強さ（『欠落禁止』）で持つ。CLAUDE.md:140 だけにあるのは『欠落すると誤発火/見逃し』という理由の 1 句。tests からの参照は無い（tests/test_pending_decisions.py は tool のテスト）。1 行のポインタを残す案は妥当。 | 3 |

### .claude/rules/pr-ci-watch.md（10 行: keep 10 / move 0 / delete 0 / outdated 0 / transport 0）

| 行範囲 | 節 | 分類 | 根拠 | 行数 |
|---|---|---|---|---|
| .claude/rules/pr-ci-watch.md:1-4 | frontmatter（description） | keep | 6889d66 2026-09-04 で frontmatter 補完 | 4 |
| .claude/rules/pr-ci-watch.md:5-10 | PR の CI 監視は /pr-watch-pane のみ | keep | hook .hooks/block-adhoc-pr-watch.sh 現存、.claude/settings.json:92,97,102,111 で配線、テスト tests/test-block-adhoc-pr-watch.sh。e17b5c4 2026-08-20、c7875c0 2026-10-05 で改訂。CLAUDE.md:40-42 の重複に対する正本 | 6 |

### .claude/skills/secretary-handover/SKILL.md（157 行: keep 146 / move 0 / delete 0 / outdated 11 / transport 0）

| 行範囲 | 節 | 分類 | 根拠 | 行数 |
|---|---|---|---|---|
| .claude/skills/secretary-handover/SKILL.md:1-17 | frontmatter | keep | allowed-tools の tools/journal_append.py / .sh は現存。ただし Step 2 の python3 -c は allowed-tools に無い（判断事項） | 17 |
| .claude/skills/secretary-handover/SKILL.md:18-32 | 冒頭・重要な前提 | keep | state.db が SoT（tools/state_db/ 現存、journal_append.py:10-16 が DB-only を明記）。26 行の『キュレーターのペインは生かしたまま』は curator 常駐廃止後は通常存在しない（軽微） | 15 |
| .claude/skills/secretary-handover/SKILL.md:33-47 | Step 1: handover 対象を整理 | keep | .state/pending_decisions.json は tools/pending_decisions.py が producer。会話の温度感は構造化できないという本スキルの存在理由 | 15 |
| .claude/skills/secretary-handover/SKILL.md:48-78 | Step 2: state.db から構造化情報を取得 | keep | tools/state_db/queries.py:331-355 の get_org_state_summary が session/active_runs/active_worker_dirs/recent_events を返す。curator null 正常は org-start:386 の CLEAR と整合 | 31 |
| .claude/skills/secretary-handover/SKILL.md:79-131 | Step 3: handover ファイルを書き出す（形式・注意） | keep | consumer は secretary-resume/SKILL.md:66-80（created_at 鮮度判定、各節）。86 行の素の cp は .prev.md が既存だと -i alias で無言停止しうる（判断事項） | 53 |
| .claude/skills/secretary-handover/SKILL.md:132-139 | Step 4: イベントを記録する | outdated | py -3 は Windows 専用ランチャー。この Linux ホストで `command -v py` は空、失敗は 2>/dev/null \|\| echo で握りつぶされ常に skip。event secretary_handover の consumer は `git grep secretary_handover -- tools dashboard` で 0 件<br>**補足**: Linux/WSL 運用では記録されない。CLAUDE.md:120 の canonical 形 `bash tools/journal_append.sh secretary_handover ...`（allowed-tools:16 に既にある）へ寄せるか、consumer 不在なので Step ごと削るかを決める<br>**検証**: 反証で維持: `command -v py` の出力は空（Linux/WSL）。py -3 が失敗しても 2>/dev/null \|\| echo で握りつぶされ、常に skip になる。tools/journal_append.py は現存し --json も受け付ける（:130）。`git grep secretary_handover` のヒットは skills 外で 0 件。ただし events は汎用表示の recent_events に出うる（推測）ので、『consumer 不在だから削除』は弱い。journal_append.sh 形へ寄せる修正が安全。 | 8 |
| .claude/skills/secretary-handover/SKILL.md:140-149 | Step 5: ユーザーへ案内（前半） | keep | /clear → /secretary-resume の案内。secretary-resume SKILL.md 現存 | 10 |
| .claude/skills/secretary-handover/SKILL.md:150-152 | Step 5 案内文『キュレーターペインは稼働を続ける』 | outdated | キュレーターは常駐しない: 同ファイル:73-74、org-start/SKILL.md:7,386、.dispatcher/references/pane-close.md Step 5（閾値超過時のみ一時起動）<br>**補足**: 『ディスパッチャー・ワーカーペインは稼働を続ける』に（キュレーターを外す）。出典 org-start/SKILL.md:7,37-42<br>**検証**: 反証で維持: キュレーターは常駐しない。根拠は org-start/SKILL.md:7,37-42（CLEAR で明示クリア）、同ファイル:73-74（curator null が正常系）、org-start:519（オンデマンド専用）。同ファイル:24-25 の前提文も同じ誤りを含む（本行の範囲外）。 | 3 |
| .claude/skills/secretary-handover/SKILL.md:153-157 | 窓口がやってはいけないこと | keep | /clear はユーザー入力・SHUTDOWN を送らない（/org-suspend との区別）。org-suspend SKILL.md 現存 | 5 |

### .claude/skills/secretary-resume/SKILL.md（173 行: keep 147 / move 0 / delete 0 / outdated 8 / transport 18）

| 行範囲 | 節 | 分類 | 根拠 | 行数 |
|---|---|---|---|---|
| .claude/skills/secretary-resume/SKILL.md:1-16 | frontmatter（transport 以外） | keep | tools/capability_gate.py・journal_append.* 現存。編集は SKILL.md.in 側（tools/gen_skill_prose.py が生成） | 16 |
| .claude/skills/secretary-resume/SKILL.md:17-21 | allowed-tools の mcp__org-broker__* のみ | transport | 生成物は コード既定 broker で render（tools/transport.py:19-20）されるため mcp__org-broker__* だけが並ぶ。運用 renga では mcp__renga-peers__* が許可リストに無い。編集は .in 側 | 5 |
| .claude/skills/secretary-resume/SKILL.md:22-38 | 冒頭・前提 | keep | curator null 正常（org-start:386 と整合）、/org-start・/org-resume は SKILL.md 現存 | 17 |
| .claude/skills/secretary-resume/SKILL.md:39-46 | 輸送層両系注記 — 既定 broker / opt-in renga ＋ 二フレーム | transport | SKILL.md.in:39 の `{{> dual-system-header-short }}` から tools/skill_src/fragments/dual-system-header-short.broker.md を注入。CLAUDE.md:28 と見出しの既定が逆（broker vs renga）。Epic #6 Issue G 但し書きを含む | 8 |
| .claude/skills/secretary-resume/SKILL.md:47-54 | Step 0: identity 確認（set_summary / caller pane id） | keep | 契約 docs/contracts/backend-interface-contract.md T-§4.2 現存。59cf3af/98735cf 2026-08-09 で導入した caller pane id 規則 | 8 |
| .claude/skills/secretary-resume/SKILL.md:55-59 | Step 0: broker では RENGA_PANE_ID を読まない分岐 | transport | broker 専用の分岐。運用 renga では不発 | 5 |
| .claude/skills/secretary-resume/SKILL.md:60-65 | Step 0: 期待値・修復・caller-id 未確定 | keep | set_pane_identity は数値 id 必須（59cf3af 2026-08-09 で MUST 化）。org-start:134-139 と同じ規則を別呼び出し点で適用 | 6 |
| .claude/skills/secretary-resume/SKILL.md:66-81 | Step 1: handover ファイル読み込み・鮮度判定 | keep | producer は secretary-handover/SKILL.md:81-124（created_at frontmatter） | 16 |
| .claude/skills/secretary-resume/SKILL.md:82-100 | Step 2: state.db 再取得 | keep | get_org_state_summary 現存（queries.py:349-355）。`python -c` の python はこのホストで `command -v python` 空（判断事項） | 19 |
| .claude/skills/secretary-resume/SKILL.md:101-126 | Step 3: ペイン生存確認（capability gate） | keep | org-delegate/references/capability-first-drive-operational-gate.md 現存、§6 表 #3 は同 reference:527 に列挙。98735cf 2026-08-09 / 87a39be で全 8 経路配線 | 26 |
| .claude/skills/secretary-resume/SKILL.md:127-154 | Step 4: ブリーフィング | keep | 出力テンプレート。curator 常駐なし表記は現行と一致 | 28 |
| .claude/skills/secretary-resume/SKILL.md:155-159 | Step 5: handover ファイルを保持 | keep | .prev.md は secretary-handover:86 が作る | 5 |
| .claude/skills/secretary-resume/SKILL.md:160-167 | イベント記録 | outdated | secretary-handover:132-139 と同じく py -3 は Linux で不在（`command -v py` 空）→ 常に skip。secretary_resumed の consumer は git grep で 0 件<br>**補足**: `bash tools/journal_append.sh secretary_resumed ...`（allowed-tools:14 にある）へ寄せるか、consumer 不在なので削除。編集は .in 側<br>**検証**: 反証で維持: secretary-handover と同じ構造で、py -3 は Linux に無いため常に skip（`command -v py` 空）。生成元 SKILL.md.in:157 にも同じ記述があるので、編集は .in 側。secretary_resumed も skills 外の参照は 0 件。 | 8 |
| .claude/skills/secretary-resume/SKILL.md:168-173 | やってはいけないこと | keep | 再 spawn しない・食い違いは人間判断。Step 3 の規則と整合 | 6 |

### .claude/skills/org-start/SKILL.md（543 行: keep 392 / move 70 / delete 13 / outdated 0 / transport 68）

| 行範囲 | 節 | 分類 | 根拠 | 行数 |
|---|---|---|---|---|
| .claude/skills/org-start/SKILL.md:1-28 | frontmatter（allowed-tools） | keep | 列挙したツール（check_runtime_version / check_herdr_compat / ensure_projects_registry / secretary_queue_watcher / check_role_configs / capability_gate / org_state_converter）はすべて ls で現存 | 28 |
| .claude/skills/org-start/SKILL.md:29-31 | allowed-tools の renga/broker 両系 | transport | mcp__renga-peers__* と mcp__org-broker__*（opt-in 注記付き）の併記。30 行のコメント『broker（opt-in）』はコード既定（tools/transport.py:19-20）と逆 | 3 |
| .claude/skills/org-start/SKILL.md:32-36 | タイトル・概要 | keep | スキルの目的 | 5 |
| .claude/skills/org-start/SKILL.md:37-46 | キュレーターは起動しない（注記） | delete | 同じ事実が同ファイルに 5 回: frontmatter:7、Block A:207、D-3:384、D-5:386,400、失敗モード:431、Step 4:448。Issue #502 バックログは .dispatcher/references/pane-close.md:237,265 にもある<br>**補足**: 正本: frontmatter:7（要旨）と Block D-5 :386（CLEAR の理由）。Issue #502 は pane-close.md:237 に既出<br>**検証**: 反証で維持: 同じファイル内に要旨が繰り返し出てくる: frontmatter:7、207、384、386（CLEAR の理由）、400、431（pane-close Step 5-3/5-4 へのリンク）、448。#502 のバックログ注記だけは pane-close.md 側にあるとされ、本ファイルでは失われる。tools の org-start 参照（check_curate_threshold.py:4 等）はコメントのみで、本文の文字列には依存していない。 | 10 |
| .claude/skills/org-start/SKILL.md:47-53 | 前提: renga --layout ops で起動・14 種ツール | transport | renga 起動を前提にした記述。コード既定 broker では `claude-org-runtime org up` 起動（scripts/install.sh:336-340）。renga 2.0.0 下限は tools/check_renga_compat.py:113 と一致 | 7 |
| .claude/skills/org-start/SKILL.md:54-71 | 14 種のタブ scope 分類（Group A/B/messaging/例外） | move | 契約 T-§cap / T-§4.2 の解説で、本スキルの手順はこの分類を参照しない（各手順は個別に caller pane id・capability gate の正本へリンク済み: :124,:369）。正本は docs/contracts/backend-interface-contract.md<br>**検証**: 反証で維持: 契約 T-§cap / T-§4.2 の要約で、正本は docs/contracts/backend-interface-contract.md。org-start の手順で実際に close_pane を撃つ箇所は、423-427 で 3 段の手順を自前で持っている。tools/check_group_b_selectors.py が検査するのは close_pane(target=…) の形で、この prose は対象外。移動先へのポインタは 1 行残す。 | 18 |
| .claude/skills/org-start/SKILL.md:72-78 | エラー分岐参照・check_renga_compat 15 種の補足 | move | org-start は check_renga_compat.py を呼ばない（allowed-tools:9-30 に無い）。15 種は check_renga_compat.py:122-143 と一致する豆知識で、手順に不要。server_info 未追加の理由は docs/design/renga-decoupling.md<br>**検証**: 反証で維持: allowed-tools（9-30）に check_renga_compat.py は無く、15 種の話は手順と関係ない。72-73 の renga-error-codes へのポインタは :93 と :415 にもあるので、失われても困らない。 | 7 |
| .claude/skills/org-start/SKILL.md:79-88 | state DB 前提（M4） | keep | tools/state_db/writer.py の StateWriter・post-commit 再生成（:158-196）、drift 検出 tools/state_db/drift_check.py 現存。importer コマンドは tools/state_db/importer.py | 10 |
| .claude/skills/org-start/SKILL.md:89-98 | 輸送層両系注記 — 既定 renga / opt-in broker ＋ 二フレーム | transport | 受信モデル・spawn 儀式・エラー分岐の broker 併記、ratified 日付、Epic #6 Issue G 但し書き、二フレーム注記（Refs #604）。CLAUDE.md:28-39 とほぼ同文 | 10 |
| .claude/skills/org-start/SKILL.md:99-100 | Step 0 見出し | keep | 見出し | 2 |
| .claude/skills/org-start/SKILL.md:101-106 | Step 0-0: transport 判定 | transport | 103 行 `echo "${ORG_TRANSPORT:-renga}"` は無設定を renga と読むが、コード解決は無設定→broker（tools/transport.py:19-20、tools/peer_notify.py:85-94 は resolve() 経由）。二フレームの食い違いが手順に出ている箇所 | 6 |
| .claude/skills/org-start/SKILL.md:107-113 | Step 0-1/0-2: set_summary・MCP 疎通確認 | keep | list_peers での窓口発見に必要。疎通確認は全 MCP 呼び出しの前提 | 7 |
| .claude/skills/org-start/SKILL.md:114-118 | Step 0-2: transport 別の復旧（renga mcp install / broker daemon） | transport | renga / broker の 2 系統の復旧手順を併記 | 5 |
| .claude/skills/org-start/SKILL.md:119-146 | Step 0-3: secretary identity 検証と自動リカバリ | keep | 契約 T-§4.2 caller pane id 規則、59cf3af 2026-08-09（Group B 数値 id MUST 化）・98735cf 2026-08-09。name_in_use は org-delegate/references/renga-error-codes.md に記載（grep 4 件） | 28 |
| .claude/skills/org-start/SKILL.md:147-151 | Step 0-3: broker 分岐・broker 論理エントリ注記 | transport | broker 専用の長文分岐（147 行は 1 行で約 1,800 字）。logical_pane エラーは renga-error-codes.md に記載。運用 renga では不発 | 5 |
| .claude/skills/org-start/SKILL.md:152-169 | Step 0-4: プロジェクトレジストリ配置 | keep | tools/ensure_projects_registry.py・registry/projects.example.md・docs/operations/registry-projects-migration.md 現存。61ef0b7 2026-07-31 (#814) | 18 |
| .claude/skills/org-start/SKILL.md:170-174 | Step 0-5: workers_dir 確認（削除禁止） | keep | registry/org-config.md 現存。過去成果を消さない制約 | 5 |
| .claude/skills/org-start/SKILL.md:175-192 | Step 0.5: herdr 互換性 preflight（手順・exit 分岐） | keep | tools/check_herdr_compat.py 現存（SUPPORTED_PROTOCOLS を runtime から import :136-148）。cc08568 2026-07-22 (#748/#750)。発動は broker+herdr のみで、herdr 見送り判断との関係は判断事項 | 18 |
| .claude/skills/org-start/SKILL.md:193-198 | Step 0.5 設計メモ（判定レイヤ・対応窓 SoT） | move | スクリプト内部の設計説明。版写像 0.7.0-0.7.1→14 等は check_herdr_compat.py:88-100 と一致（outdated ではない）が、手順には不要<br>**検証**: 反証で維持: 判定レイヤ・SoT・stdout/stderr の分け方の説明だけ。実行と exit 分岐は 182-191 で完結しており、手順は混ざっていない。 | 6 |
| .claude/skills/org-start/SKILL.md:199-204 | Step 1〜3: 並列起動フェーズ（実行モデル） | keep | Step 0.5 通過後に Block A を発火する順序の不変条件。201 行の wall-clock 短縮の数字だけは経緯 | 6 |
| .claude/skills/org-start/SKILL.md:205-223 | Block A: ディスパッチャー spawn | keep | spawn_claude_pane 引数。dispatcher は cwd=.dispatcher・bypassPermissions・sonnet（38d3a25 / Appendix:511-516 と一致） | 19 |
| .claude/skills/org-start/SKILL.md:224-244 | Block B: 前回の状態確認 | keep | get_org_state_summary（queries.py:349-355 に run_status_counts あり）、importer コマンド現存。SUSPENDED 時は org-resume SKILL.md の Phase を呼ぶ | 21 |
| .claude/skills/org-start/SKILL.md:245-262 | Block C: ダッシュボード起動・sandbox 注記 | keep | dashboard/server.py 現存（PID_FILE :54、PORTS 8099-8101 :50）。停止は org-suspend/SKILL.md:464 | 18 |
| .claude/skills/org-start/SKILL.md:263-266 | Block C2: runtime drift 検出（概要） | keep | tools/check_runtime_version.py 現存。2670ce3 2026-07-08 | 4 |
| .claude/skills/org-start/SKILL.md:267-268 | Block C2: sandbox 内では PyPI に届かない（#119 の経緯） | move | 2026-07-08 #119 phantom dispatch の事故経緯。実行指示（ホスト実行）は 269 行と 277 行に重ねて書かれている<br>**検証**: 反証で維持: #119（2026-07-08）の経緯が中心。ホスト実行の指示は 269 にあり、exit 2 の解釈も 277 にあるので、手順は 269-280 で保たれる。 | 2 |
| .claude/skills/org-start/SKILL.md:269-281 | Block C2: 実行と exit 分岐・interpreter 併記 | keep | exit 0-3 の契約はスクリプト側と対応。interpreter 併記は d5720a2 2026-08-08 (#865)、local install は 40dbf39 2026-07-22 (#747) | 13 |
| .claude/skills/org-start/SKILL.md:282-292 | Block C2: 設計メモ | move | PyPI JSON API の採用理由、pin window、yanked 除外などスクリプト内部の設計説明。手順は 269-280 で完結している<br>**検証**: 反証で維持: 283-291 は PyPI API の採用理由、pin window、#747、yanked の除外、stdout の分け方など、スクリプト内部の説明。窓口がとる行動（転記・interpreter 併記・local install の読み替え）は 274-280 に全部ある。 | 11 |
| .claude/skills/org-start/SKILL.md:293-310 | Block C3: secretary 滞留 watcher（broker のみ） | transport | broker 専用ブロック（295 行: renga なら skip）。tools/secretary_queue_watcher.py 現存、停止は org-suspend Phase 3.6（:471）・org-down。db2aae0 2026-07-05。運用 renga では毎回 skip される | 18 |
| .claude/skills/org-start/SKILL.md:311-322 | Sidebar: attention watcher 起動案内 | keep | org-attention-start / org-attention-stop SKILL.md・tools/templates/attention.example.json・docs/operations/attention-watch.md 現存 | 12 |
| .claude/skills/org-start/SKILL.md:323-326 | Block C4: role config drift 検出（概要） | keep | tools/check_role_configs.py 現存。e6e3de1 2026-08-03 (#819) | 4 |
| .claude/skills/org-start/SKILL.md:327-330 | Block C4: なぜ起動時に見るか・drift の種類 | move | 理由の説明。CI 側の事実（.github/workflows/tests.yml:49 は --include-local なし）とは一致しているが手順ではない<br>**検証**: 反証で維持: 327 は理由（CI の tests.yml が --include-local を付けない盲点）、329 は drift 種別の説明と getting-started へのリンク。実行とその分岐は 331-341 で完結している。 | 4 |
| .claude/skills/org-start/SKILL.md:331-342 | Block C4: 実行・exit 分岐・cwd 制約 | keep | --include-local / --role は check_role_configs.py の CLI。exit 1/2 契約は tests/test_check_role_configs.py の ExitCodeContractTests | 12 |
| .claude/skills/org-start/SKILL.md:343-348 | Block C4: 設計メモ | keep（当初 move） | stdout/stderr 分離・ASCII フォールバックの理由。スクリプト内部の説明<br>**検証**: 反証で move→keep: 346 行目に、sandbox の deny で exit 2 に落ちたら dangerouslyDisableSandbox でホスト再実行するという手順が入っている。exit 2 の分岐（339）は『報告する』としか書いておらず、この指示は他に無い。346 を手順側へ移してからなら、残りの 344,345,347 は move できる。 | 6 |
| .claude/skills/org-start/SKILL.md:349-366 | Block D-1: 起動プロンプトを画面判定で承認 | keep | runtime claude_org_runtime/dispatcher/spawn_prompt.py・runner.py に spawn-prompt-step 実装（0.1.49 導入済み）。事故 2026-09-25（claude-org-runtime#185）、c775107 2026-09-30 | 18 |
| .claude/skills/org-start/SKILL.md:367-367 | Block D-1: broker の 2 段承認 | transport | broker の folder-trust + dev-channel sidecar 併記（1 行で約 900 字） | 1 |
| .claude/skills/org-start/SKILL.md:368-385 | Block D-2〜D-4: list_peers poll・挨拶・Block B 合流 | keep | capability-first-drive-operational-gate.md §6 表 #2 / §1-2（:388-405）と対応。98735cf 2026-08-09、87a39be | 18 |
| .claude/skills/org-start/SKILL.md:386-401 | Block D-5: DB transaction で identity 記録 | keep | StateWriter.CLEAR は tools/state_db/writer.py:51、None は保持（:298-308）。縮退時の peer_id CLEAR は dispatcher-resume Step 0 と逆である旨も正しい | 16 |
| .claude/skills/org-start/SKILL.md:402-404 | Block D-6: JSON スナップショット再生成 | delete | D-5 の StateWriter(claude_org_root=Path('.')).transaction() の commit で writer.py:180-196 _regenerate_json_snapshot が dashboard.org_state_converter.convert を自動実行する。さらに dashboard/server.py は state.db を直接読む（:55）。producer が既に D-5 にある<br>**補足**: 不要（D-5 の post-commit hook が同じ .state/org-state.json を再生成、tools/state_db/writer.py:180-196）。org-resume / spawn-flow にも同じ手動呼び出しがあるので併せて確認<br>**検証**: 反証で維持: D-5 の transaction() → commit()（tools/state_db/writer.py:130-144）が _regenerate_json_snapshot（:179-196、convert で .state/org-state.json を書く）を自動で呼ぶ。.dispatcher/references/spawn-flow.md:381 も『手動呼び出し不要』と明記している。削除すると allowed-tools:15-16 が未使用になる。org-resume/SKILL.md:108-109 にも同じ手動呼び出しがある。 | 3 |
| .claude/skills/org-start/SKILL.md:405-417 | 補足: spawn_claude_pane 引数の詳細 | move | Block A の引数は固定値で、解説は毎回の起動に不要。cd 方式禁止は .dispatcher/references/spawn-flow.md:128 と Appendix:503 にも重複。参照元は同ファイル:221 のみ<br>**検証**: 反証で維持: Block A の引数は固定値で、各引数の意味の解説になっている。cd 方式の禁止は Appendix 502 と spawn-flow.md:128 にもあり、エラーコードは :93 と 失敗モード 421 がカバーしている。参照元が 221 だけなので、移動時はそこを付け替える。 | 13 |
| .claude/skills/org-start/SKILL.md:418-432 | 失敗モード | keep | Group B 3 段（自タブ確立→identity 照合→数値 close）は 59cf3af 2026-08-09 の MUST。縮退時の fatal 根拠差し替えは 87a39be/98735cf | 15 |
| .claude/skills/org-start/SKILL.md:433-441 | Stage A / Stage B の wall-clock 効果（表） | move | 改修前後の所要時間の経緯表。手順なし<br>**検証**: 反証で維持: 改修前後の wall-clock を比べた経緯表だけで、手順は含まない。 | 9 |
| .claude/skills/org-start/SKILL.md:442-445 | 起動後の裏取り: dispatcher の /loop 監視を打鍵で確認 | keep | 79c4adf 2026-07-03 (#663) で明文化。dispatcher-resume/SKILL.md Step 5（:343）と .dispatcher/references/spawn-flow.md 3-5a に同じ再点火手順 | 4 |
| .claude/skills/org-start/SKILL.md:446-449 | Step 4: 準備完了の報告（見出し） | keep | 見出しと対象（dispatcher のみ） | 4 |
| .claude/skills/org-start/SKILL.md:450-453 | Step 4: C2 / C4 出力の扱い（再掲） | keep（当初 delete） | 450 行は Block C2 の exit 分岐（:274-278、#119/#747 の注記まで）、452 行は Block C4 の exit 分岐（:336-339）とほぼ同文。新情報は『末尾に空行を 1 つ挟む』だけ<br>**補足**: 正本: Block C2 :274-278 と Block C4 :336-339。空行の指定はテンプレート例 :482-496 に含める<br>**検証**: 反証で delete→keep: 同じ強さの重複ではない。452 行目にしか無い条件が 2 つある: 『C2 と C4 の warning が両方出たら 1 行ずつ並べる』と『末尾に空行を 1 つ挟む』。Step 4 は報告を組み立てる実行地点でもある。この 2 点を C2 / C4 かテンプレートへ移してからなら削れるが、現状の delete は条件を失う。 | 4 |
| .claude/skills/org-start/SKILL.md:454-466 | Sidebar: dispatcher 自己修復ビュー（broker フレーム時のみ） | transport | broker(tmux) 専用の案内（456 行）。tools/org-dispatcher-view.sh・docs/operations/dispatcher-view.md は現存、7d56832 2026-08-09。運用 renga では案内しない | 13 |
| .claude/skills/org-start/SKILL.md:467-499 | Step 4: 報告テンプレートと warning 添付例 | keep | [runtime drift] / [role config drift] 行の形式はスクリプト出力と対応。498 行の『再実行コマンドを出さない』理由は短い | 33 |
| .claude/skills/org-start/SKILL.md:500-543 | Appendix: ClaudeCode 起動コマンド（役割別） | keep（当初 move） | curator 引数は .dispatcher/references/pane-close.md:314-326、worker のモデル選定は .dispatcher/references/spawn-flow.md:104-112 と org-delegate/SKILL.md:100 と重複。ただし .dispatcher/CLAUDE.md:30・spawn-flow.md:130・docs/verification.md:55 がこの節を参照先にしているので単純削除はできない<br>**検証**: 反証で move→keep: 中身は役割別 spawn パラメータの仕様で、dispatcher は bypassPermissions+sonnet、curator は cwd=../.curator・auto・sonnet。.dispatcher/CLAUDE.md:30 と spawn-flow.md:130,141 がこれを起動仕様の正本として参照しており、ディスパッチャーが手順として使っている。rationale ではない。『理由:』の段落（516,527,537）だけは move できる。 | 44 |

### .claude/skills/org-dashboard/SKILL.md（53 行: keep 52 / move 0 / delete 1 / outdated 0 / transport 0）

| 行範囲 | 節 | 分類 | 根拠 | 行数 |
|---|---|---|---|---|
| .claude/skills/org-dashboard/SKILL.md:1-16 | frontmatter | keep | dashboard/server.py 現存。allowed-tools の open / start は Linux に無い（判断事項） | 16 |
| .claude/skills/org-dashboard/SKILL.md:17-21 | 概要 | keep | server が /api/state を配信（dashboard/server.py:341） | 5 |
| .claude/skills/org-dashboard/SKILL.md:22-30 | Step 1: サーバー状態確認 | keep | PID_FILE .state/dashboard.pid（server.py:54）。org-start Block C :249-252 と同じコマンド（両方が実行地点） | 9 |
| .claude/skills/org-dashboard/SKILL.md:31-39 | Step 1.5: サーバー起動 | keep | server.py は 8099 が使用中なら 8100/8101 に逃げる（:50,:596）ので『8099 でアクセス可能』は常に正しいわけではない（軽微） | 9 |
| .claude/skills/org-dashboard/SKILL.md:40-48 | Step 2: ブラウザで開く | keep | 案内文は有効。この Linux/WSL ホストでは `command -v open xdg-open wslview` がすべて空で、Mac/Windows 前提のコマンドしか書かれていない（判断事項） | 9 |
| .claude/skills/org-dashboard/SKILL.md:49-51 | 補足: リアルタイム反映 | keep | server.py:436-470 の watcher が state.db / -wal / workers/*.md / knowledge/curated/*.md の mtime を監視 | 3 |
| .claude/skills/org-dashboard/SKILL.md:52-52 | 補足: data.json の手動生成は不要 | delete | data.json の producer も consumer も無い。`git grep data.json` のヒットは .gitignore:44・docker/Dockerfile:27・.dockerignore:29（除外指定）と無関係なテストだけ。もう存在しない成果物への言及<br>**補足**: 削除（server.py は state.db を直接読む、:55）<br>**検証**: 反証で維持: `git grep data.json` のヒットは .gitignore:44、docker/Dockerfile:27、Dockerfile.dockerignore:29（除外指定）と、無関係な tests/test-block-org-structure.sh:167 だけで、producer も consumer も無い。dashboard/server.py:55 は state.db を直接読む。 | 1 |
| .claude/skills/org-dashboard/SKILL.md:53-53 | 補足: org-start で起動・org-suspend で停止 | keep | org-start Block C :245-259、org-suspend/SKILL.md:464 `kill $(cat .state/dashboard.pid)` | 1 |

### .claude/skills/goal-setup/SKILL.md（105 行: keep 105 / move 0 / delete 0 / outdated 0 / transport 0）

| 行範囲 | 節 | 分類 | 根拠 | 行数 |
|---|---|---|---|---|
| .claude/skills/goal-setup/SKILL.md:1-21 | frontmatter | keep | tools/work_discovery_repos.py・tools/work_discovery_goals.py（validate サブコマンド :1056）現存。4a52d6f 2026-09-30 (#1028) で新設 | 21 |
| .claude/skills/goal-setup/SKILL.md:22-33 | 概要（書式の正本・起動主体） | keep | registry/goals/README.md・example.md 現存、docs/design/work-discovery-triage.md 現存。consumer: work-discovery/SKILL.md:30,170-171 が /goal-setup を案内 | 12 |
| .claude/skills/goal-setup/SKILL.md:34-48 | Step 1: 対象リポジトリを決める | keep | work_discovery_repos.py --format json（:446-450、既定 json）が included/opted_out/skipped を返す（:376-378） | 15 |
| .claude/skills/goal-setup/SKILL.md:49-60 | Step 2: 既存台帳を確認 | keep | validate --repo（:1059）、status ok/unset/error と case mismatch（work_discovery_goals.py:304-337） | 12 |
| .claude/skills/goal-setup/SKILL.md:61-73 | Step 3: ゴール候補を推定して提示 | keep | gh repo view / issue list は allowed-tools:18-19 と一致。読み取りのみ | 13 |
| .claude/skills/goal-setup/SKILL.md:74-83 | Step 4: 質問して条項を固める | keep | 条項順＝提案優先度は CLAUDE.md:47・org-pull-request:342（条項順が主キー）と整合 | 10 |
| .claude/skills/goal-setup/SKILL.md:84-99 | Step 5: 下書き → 書式検証 → 確認 → 保存 | keep | validate --file（:1058）。送信同意は registry/goals/README.md:41-43（claude -p で api.anthropic.com）と一致 | 16 |
| .claude/skills/goal-setup/SKILL.md:100-105 | やらないこと | keep | .gitignore:39-41 で registry/goals/* を除外（README / example のみ追跡）。triage 自体は work-discovery が担当 | 6 |

### .dispatcher/CLAUDE.md（288 行: keep 218 / move 11 / delete 36 / outdated 19 / transport 4）

| 行範囲 | 節 | 分類 | 根拠 | 行数 |
|---|---|---|---|---|
| .dispatcher/CLAUDE.md:1-12 | Dispatcher 冒頭・役割 | keep | DELEGATE/CLOSE_PANE 受信と .state 記録は現行運用。CLOSE_PANE 手順は .dispatcher/references/pane-close.md:7 に、spawn 手順は spawn-flow.md に現存する | 12 |
| .dispatcher/CLAUDE.md:13-15 | 役割境界: credential proxy 禁止（規則） | keep | knowledge/curated/delegation.md:71 に実例がある（B1-1 で dispatcher が 2 回拒否）。これを機械的に強制する hook は無いので、散文の規則として残す | 3 |
| .dispatcher/CLAUDE.md:16-16 | 役割境界: 背景 | move | 設計理由を説明する 1 行で、手順ではない。同じ経緯が knowledge/curated/delegation.md:71-75 に既にある<br>**検証**: 反証で維持: 16行は『背景:』の設計理由1行。同趣旨が knowledge/curated/delegation.md:70-74 にある。手順は15・17行が持つので移しても実行内容は失われない | 1 |
| .dispatcher/CLAUDE.md:17-18 | 役割境界: 適用 | keep | escalation 先と差し戻し方針という行動指示。同じ内容は curated にしか無い（git grep 'dispatcher proxy' → .dispatcher/CLAUDE.md:15 と knowledge/curated/delegation.md:71 の 2 件のみ） | 2 |
| .dispatcher/CLAUDE.md:19-22 | 輸送層両系（既定 renga / opt-in broker・二フレーム注記・Epic #6 Issue G） | transport | broker/renga の併記、mcp__renga-peers__→mcp__org-broker__ の読み替え、Refs #604 の二フレーム注記、Epic #6 Issue G の但し書き。root CLAUDE.md の輸送層節と handover/resume の dual-system-header-long とほぼ同文 | 4 |
| .dispatcher/CLAUDE.md:23-32 | スキル参照 | keep | 参照先 5 本（spawn-flow.md, pane-layout.md, instruction-template.md, org-start SKILL.md, renga-error-codes.md）は ls で全部 OK。27 行の『旧 org-delegate Step3/4 から移設 Issue #320』だけは経緯の記述 | 10 |
| .dispatcher/CLAUDE.md:33-36 | delegate-plan helper 導入経緯 | move | 35 行は Phase 1/Phase 4/closes #129 という経緯。ls tools/dispatcher_runner.py → MISSING（移行済みの旧実体）。helper の目的説明 1 文は 37 行以降に残せば足りる<br>**検証**: 反証で維持: 35行の Phase1/4・closes #129 は経緯の説明。ただし33行の見出しと35行後半の目的文（deterministic部分をPythonへ寄せる）は37行以降に無いので残すこと。移すのは括弧内の経緯だけ | 4 |
| .dispatcher/CLAUDE.md:37-57 | いつ使うか・CLI 例・--transport | keep | `claude-org-runtime dispatcher delegate-plan --help`（0.1.49 を導入済み）で --task-json/--panes-json/--transport {renga,broker}/--max-concurrent-workers/--locale-json/--template-repo/--state-dir が現存。registry/org-config.md:34 max_concurrent_workers: 8 | 21 |
| .dispatcher/CLAUDE.md:58-58 | --max-concurrent-workers の renga overflow 詳述 | delete | git grep -- '--overflow-to-new-tab' は tools/scripts/.hooks で 0 件、ヒットは prose のみ。本文自身も『現行 ja はこれらのフラグを渡していない…今日は到達しない』と書いている。broker gate の説明は spawn-flow.md:38 と重複<br>**補足**: broker gate の説明の正本は .dispatcher/references/spawn-flow.md:38。overflow の説明は配線するまで要らない<br>**検証**: 反証で維持: runtime 0.1.49 は --overflow-to-new-tab を受け付ける（runner.py:46）が、ja の tools/scripts/.hooks/tests では git grep 0件で配線されていない。broker gate の説明は spawn-flow.md:38 と同じ。tests 側の参照も無い | 1 |
| .dispatcher/CLAUDE.md:59-79 | task JSON・model・出力 ready_to_spawn・approve_spawn_prompts ループ | keep | runner.py:2305 PLAN_VERSION=2。approve_spawn_prompts と spawn-prompt-step が現存（spawn_prompt.py:75 DEFAULT_APPROVAL_DEADLINE_MS=120000）。2026-09-25 の盲目 Enter 事故は直近の記録 | 21 |
| .dispatcher/CLAUDE.md:80-80 | plan_version<2（runtime 0.1.42 以前）時の手当て | keep（当初 delete） | pyproject.toml:30 / requirements.txt:181 は claude-org-runtime>=0.1.49。導入済み 0.1.49 の runner.py:2305 は PLAN_VERSION=2。0.1.42 以前の helper は依存制約上入らないので、この分岐には到達しない<br>**検証**: 反証で delete→keep: pin は >=0.1.49（pyproject.toml:30）。ただし org-start SKILL.md:262-292 では pin窓外の installed が警告止まりの exit 2 で起動を止めず、runtime の複数同居も明記されている。盲目Enter事故（2026-09-25）を防ぐ安全柵なので削除は危険 | 1 |
| .dispatcher/CLAUDE.md:81-85 | list_peers 前の capability gate・控え id・ultracode kickoff | keep | capability-first-drive-operational-gate.md が現存し、commit 87a39be/98735cf（2026-08-08/09）で配線済み。ultracode の send_keys 武装は本タスク自身の kickoff で実走している（Issue #554） | 5 |
| .dispatcher/CLAUDE.md:86-91 | split_capacity_exceeded / input_invalid の扱い | keep | runner.py:2468 _renga_rect_escalation_message の docstring に『claude-org-ja forwards this text to the secretary verbatim』。spawn-flow.md 3-1c(55-60) と一部重複（judgments 参照） | 6 |
| .dispatcher/CLAUDE.md:92-96 | helper の実ファイル書き出し | keep | helper は seed（Status: planned）と outbox instruction を書く。spawn_gate.py:513 が seed の `Pane Name:` 行を前提にしている | 5 |
| .dispatcher/CLAUDE.md:97-97 | MCP 呼び出し後の Status active 遷移 | outdated | 『ディスパッチャーが Status を active に遷移』とあるが、spawn-flow.md:353 は spawn_gate.py verify が成功時の副作用として書くと定めている（spawn_gate.py:826-839 worker_record）<br>**補足**: Status active / Pane ID / Started / Progress Log は tools/spawn_gate.py verify が書く（spawn_gate.py:826-839）。手で補うのは worker_record.status が missing/error のときだけ（spawn-flow.md:353）<br>**検証**: 反証で維持: spawn_gate.py:590-605 の docstring どおり、verify が副作用で Status planned→active・Pane ID・Started・派遣完了を書く（:826-844）。食い違うのは Status 遷移の1節だけで、同じ行の verify ゲートと same_tab_peer_id merge の指示は keep | 1 |
| .dispatcher/CLAUDE.md:98-106 | journal_append と spawn gate のコマンド例（前半） | keep | tools/journal_append.sh と tools/spawn_gate.py が現存する。docstring は events テーブルを唯一の書き先としている（journal_append.py:10-13） | 9 |
| .dispatcher/CLAUDE.md:107-107 | --peer-cwd の照合先コメント | outdated | 『runs.worker_dir_id と照合』とあるが、spawn_gate.py:47 は delegate_sent イベントの dir と照合し、runs は fallback（:60）<br>**補足**: --peer-cwd の照合先は delegate_sent イベントの dir（tools/spawn_gate.py:47,144）。食い違うと worker_dir_divergence になる（:174）。spawn-flow.md:470 が正しい<br>**検証**: 反証で維持: spawn_gate.py:47-61 を確認: 照合先は delegate_sent の dir。runs.worker_dir_id は意図的に基準から外され fallback 扱い。spawn-flow.md:470 が正しい記述 | 1 |
| .dispatcher/CLAUDE.md:108-114 | spawn gate コマンド例（後半） | keep | spawn_gate.py verify の引数（--task/--pane-id/--peer-*/--approval/--instruction）は spawn-flow.md:453-460 と一致する | 7 |
| .dispatcher/CLAUDE.md:115-116 | helper の実装説明（core-harness 0.3.0 audit / flock） | outdated | grep core_harness tools/journal_append.py / journal_append.sh → 0 件、grep flock\|fcntl → 0 件。docstring（:10-13）は StateWriter で state.db に書くとしている。pyproject.toml:29 は core-harness>=0.4.0<br>**補足**: journal_append.py は tools.state_db.writer.StateWriter 経由で .state/state.db の events テーブルに書く（journal_append.py:10-21）。core_harness.audit は使っていない<br>**検証**: 反証で維持: tools/journal_append.py/.sh を grep すると core_harness・flock・fcntl は0件。docstring :10-13 は StateWriter 経由で state.db に書くと明記している。docs/journal-events.md への参照は残す | 2 |
| .dispatcher/CLAUDE.md:117-121 | 使わないケース | keep | helper 不使用のフォールバック経路は spawn-flow.md:337 の『helper 未経由』テンプレートが受けている | 5 |
| .dispatcher/CLAUDE.md:122-127 | ワーカーへの報告先ルール | outdated | 124 行に『ワーカーは list_peers で窓口を自動発見する』とあるが、worker-claude-template.md:317 は安定名 to_id="secretary" に固定送信する。ja_locale.json の report_target_default=secretary で helper が brief に埋める<br>**補足**: ワーカーは安定名 `secretary` 宛に送る（worker-claude-template.md:316-320）。背景タブでは数値 id（同:371）。報告先は brief と locale が担うので、126 行の『念押し』は helper 経路では dispatcher が本文を書かない（judgments 参照）<br>**検証**: 反証で維持: 124行『list_peers で自動発見』は誤り。worker-claude-template.md:316-317 は安定名 secretary 宛に送る。ただし125-126行の念押しは docs/contracts/role-contract.md:128 が dispatcher の義務として定めているので、誤りは124行だけで、125-126は keep | 6 |
| .dispatcher/CLAUDE.md:128-131 | spawn_claude_pane args[] の取り扱い | delete | 同じ規則と同じ事故例（2026-05-09 --skip-settings）が spawn-flow.md:129 にある（git grep skip-settings でヒット）。本文も『詳細は spawn-flow 3-2』と書いている<br>**補足**: 正本は .dispatcher/references/spawn-flow.md:129（3-2 の args[] ルール）<br>**検証**: 反証で維持: spawn-flow.md:129 に同じ規則・同じ事故例・knowledge/raw のリンクがあり、spawn-flow は DELEGATE のたびに必読（CLAUDE.md:25）。tests/tools で skip-settings/flag-like を git grep すると0件 | 4 |
| .dispatcher/CLAUDE.md:132-143 | 窓口への返信方法（安定名 secretary） | keep | org-start SKILL.md:47,140 の `renga --layout ops` と set_pane_identity 修復が現存。worker-claude-template.md:317 も同じ規約 | 12 |
| .dispatcher/CLAUDE.md:144-145 | [pane_not_found] 時の from_id 再送フォールバック | outdated | renga-error-codes.md:22/:98 の messaging 分岐は『list_peers 再取得→数値 id で 1 回再送』。worker brief はさらに同一 org の確認を必須にしている（別 org への誤送信防止）。本行は未確認の from_id へ無条件に再送させる<br>**補足**: 正本は .claude/skills/org-delegate/references/renga-error-codes.md の『pane_not_found の messaging 分岐』節（:98）。宛先が同一 org と確認できるまで再送しない<br>**検証**: 反証で維持: renga-error-codes.md:98-123 の messaging 分岐では、list_peers を引き直し、控えた id が残っているか same_tab:true を確認してから1回だけ再送する。from_id へ無確認で再送する144行はこれと食い違う | 2 |
| .dispatcher/CLAUDE.md:146-157 | ワーカーペイン監視: 起動条件と役割分担 | keep | /loop 3m は .hooks/check-loop-directive.sh が正文に固定している（commit b910d4e #992）。下限 2.0.0 は scripts/install.sh:268 の renga@2.0.0 と一致 | 12 |
| .dispatcher/CLAUDE.md:158-161 | 詳細ポインタ | keep | .dispatcher/references/worker-monitoring.md が現存（監視ループ 1 サイクルは :85） | 4 |
| .dispatcher/CLAUDE.md:162-163 | 要約: loop 開始停止・サイクル順 | delete | 162 は同ファイル 149 行の再掲。163 のサイクル順は worker-monitoring.md:102/204/227/432/573 の Step 1-5 と重複する<br>**補足**: 正本は .dispatcher/CLAUDE.md:149 と worker-monitoring.md『監視ループ 1 サイクル』(:85-)<br>**検証**: 反証で維持: 162行は149行の再掲。163行のサイクル順は /loop 正文（dispatcher-resume SKILL.md:356）と worker-monitoring『監視ループ1サイクル』にある。tests 側の参照は無い | 2 |
| .dispatcher/CLAUDE.md:164-164 | spawn_gate.py audit を各サイクル冒頭で叩く | keep | git grep 'spawn_gate.py audit' のヒットは .dispatcher/CLAUDE.md:164 と spawn-flow.md:477 だけで、worker-monitoring.md には無い。hook が固定する /loop 正文は worker-monitoring を指すので、この行が唯一の配線になっている。経緯（2026-08-18 の 2 件）は move 候補 | 1 |
| .dispatcher/CLAUDE.md:165-165 | relay_scan --cycle を各サイクル冒頭で叩く | delete | worker-monitoring.md:1188 に同じコマンド（python3 ../tools/relay_scan.py --recipient secretary --cycle）があり、:1201/:1230 に理由も書かれている。本行は 2026-07-30〜08-19 の経緯と Issue #955/#971 の再掲<br>**補足**: 正本は .dispatcher/references/worker-monitoring.md:1188（Step 5.25）<br>**検証**: 反証で維持: worker-monitoring.md:1185-1205/1229 に同じコマンド・exit 分岐・2026-07-30〜08-19 と 2026-09-03 の経緯がすべてある。/loop 正文の対象も同ファイル。tests/test-check-loop-directive.sh は CLAUDE.md を見ていない | 1 |
| .dispatcher/CLAUDE.md:166-170 | stall / spinner / PR-pending / decision register / pane_output 要約 | keep（当初 delete） | 定数と規則は worker-monitoring.md に全部ある: STALL_PR_MERGE_LOOKBACK_MIN :577/:668、SPINNER_ACTIVE_SUPPRESS_CAP_MIN :579/:648、latest-resolution :782、PANE_OUTPUT_* :1035/:1050。各行も『詳細は worker-monitoring』と書いている<br>**補足**: 正本は .dispatcher/references/worker-monitoring.md Step 5 (b)(b-2)(b-3)(c)(1-bis) と Step 5.2<br>**検証**: 反証で delete→keep: tests/test_completion_reported_flag.py:206-209 が .dispatcher/CLAUDE.md に 'completion_reported_at' があることを assert しており、この語は170行にしか無い（grep で確認）。消すとテストが落ちる。166-169は削れるが、170の要約は残すかテストを同時に直すこと | 5 |
| .dispatcher/CLAUDE.md:171-172 | 自動承認禁止・ループ停止前 --mark-halted | delete | 171 は worker-monitoring.md:1319『6. 重要: ディスパッチャーが自動で承認・拒否することはしない』と同文。172 は worker-monitoring.md:1323-1330（Step 7 の --mark-halted）と重複<br>**補足**: 正本は worker-monitoring.md:1319 と :1323-1330（Step 7）<br>**検証**: 反証で維持: 171行は worker-monitoring.md:1319 と同じ文。172行は :1321-1335（Step 7 の停止条件・--mark-halted・exit 分岐）と同じ強さで重複しており、片方にしか無い条件は見当たらない | 2 |
| .dispatcher/CLAUDE.md:173-175 | 監視対象ペイン名の取得元 | outdated | 『.state/workers/worker-{peer_id}.md』とあるが、seed は worker-{task_id}.md（.dispatcher/CLAUDE.md:94, spawn-flow.md:337）。worker-monitoring.md:1337 にも同じ誤記<br>**補足**: ファイル名は .state/workers/worker-{task_id}.md、Pane Name は worker-{task_id}（spawn-flow.md:126,337）<br>**検証**: 反証で維持: runtime 0.1.49 の write_worker_seed（runner.py:3165-3169）は worker-{task_id} を名乗り Pane Name: worker-{task_id} を書く。ファイル名は worker-{task_id}.md。同じ誤記が worker-monitoring.md:1337 にもある | 3 |
| .dispatcher/CLAUDE.md:176-191 | handover / resume と起動時分岐 | keep | dispatcher-handover / dispatcher-resume の SKILL.md が現存。resume Step 7（:409 mv → .consumed.md）と 7 日鮮度（resume :196-198）が本節と一致。窓口側の consumer は root CLAUDE.md:64-65 | 16 |
| .dispatcher/CLAUDE.md:192-201 | 監視 gap を埋める内部状態ファイル | keep | 各ファイルの意味（cursor の session 束縛は commit f401964 #919）を説明しているのは本節だけ。同じ一覧が handover:52-58 と resume:44-48 にもある（judgments 参照） | 10 |
| .dispatcher/CLAUDE.md:202-208 | ペインクローズ: ポインタと閉じない規則 | keep | pane-close.md:13-21（完了報告ゲート）と tools/dispatcher_retro_gate.py が現存 | 7 |
| .dispatcher/CLAUDE.md:209-209 | CLOSE_PANE 順序要約（py -3 表記） | outdated | `which py` → not found、/usr/bin/python3 のみ。pane-close.md:244 は py -3 を『# Windows』、python3 を Mac/Linux と両方書いているが、本行は py -3 だけ。(6) の exit 10 は pane-close.md 6-2 の dedup を反映していない<br>**補足**: python3 ../tools/check_curate_threshold.py / work_discovery_scan.py（Windows のみ py -3。pane-close.md:243-245, :581-583）。exit 10 でも dedup が unchanged なら転送しない（pane-close.md:620-640）<br>**検証**: 反証で維持: pane-close.md:244-245/581-584 は py -3 を Windows、python3 を Mac/Linux と併記している。:624-640 では exit10 のあと work_discovery_dedup.py（実在）で unchanged なら送らない。209行はこれを反映していない | 1 |
| .dispatcher/CLAUDE.md:210-222 | retro_deferred とオンデマンド curator 概要 | keep | tools/check_curate_threshold.py が現存し、consumer は pane-close.md:240-246。curate-inflight.json の SoT は worker-monitoring.md:1239 以降の Step 5.3 | 13 |
| .dispatcher/CLAUDE.md:223-248 | curator close の自タブ確立・判定表要約 | delete | 本文が『close 判定表 1 つに従う…結論だけ要約』と書いている。表の本体は worker-monitoring.md:1239（共通の close 判定表、行 1/行 8 保持、hard cap :1302）と pane-close.md:146-164<br>**補足**: 正本は .dispatcher/references/worker-monitoring.md Step 5.3 の close 判定表（:1239-）<br>**検証**: 反証で維持: worker-monitoring.md:1239-1282 に自タブ確立の2手段（:1243-1244）、same_tab を数えない規則（:1246）、判定表 行1-8、informational 報告、無条件削除禁止がすべて同じ強さで書かれている。check_group_b_selectors の allowlist に .dispatcher は無い | 26 |
| .dispatcher/CLAUDE.md:249-258 | curator coalesce・timeout・state.db に書かない | keep | single-flight（pane-close.md:255 5-2）と timeout/hard cap（worker-monitoring.md:1292-1302）が現存する。短いエントリ要約として残す | 10 |
| .dispatcher/CLAUDE.md:259-264 | work-discovery triage scan 概要 | keep | tools/work_discovery_scan.py と work_discovery_repos.py が現存。consumer は pane-close.md:552-582 | 6 |
| .dispatcher/CLAUDE.md:265-270 | $REPO_FLAGS 旧手順と設計 § 参照 | move | zsh の単語分割で失敗した経緯（Issue #829）と設計 §6.3/§8/§10 の由来説明。今の手順は 1 コマンド（pane-close.md:582）で足りる<br>**検証**: 反証で維持: $REPO_FLAGS の失敗経緯（#829）と設計§の由来の説明。いまの手順は pane-close.md:582-584 の1コマンドで完結する。261-265行の実行指示と271行以降の規則は範囲外なので残る | 6 |
| .dispatcher/CLAUDE.md:271-275 | INV-1/2/4/5 境界 | keep | propose-only と窓口経由は root CLAUDE.md『PR マージ後の次タスク提案』節と一致する現行制約 | 5 |
| .dispatcher/CLAUDE.md:276-278 | exit code 分岐（exit 10 → 窓口へ転送） | outdated | pane-close.md:620-640 では exit 10 のあと work_discovery_dedup.py を通し、unchanged（exit 3）なら送らない（2026-09-23 に同じ 3 件を 7 回再送した事故）。本行は exit 10 を常に転送としている<br>**補足**: exit 10 では tools/work_discovery_dedup.py で前回と比べ、changed のときだけ転送する（pane-close.md 6-2:620-640）<br>**検証**: 反証で維持: pane-close.md:624-640 を確認: exit 10 のあと work_discovery_dedup.py を通し、exit 3（unchanged）なら転送しない（2026-09-23 に7回再送した事故）。exit 2 の通知義務は277行と一致 | 3 |
| .dispatcher/CLAUDE.md:279-288 | journal 記帳・候補 JSON 素通し・詳細ポインタ | keep | work_discovery_scanned イベントの記帳は pane-close.md 6-3（:648-668）に現存 | 10 |

### .dispatcher/references/spawn-flow.md（479 行: keep 353 / move 59 / delete 6 / outdated 44 / transport 17）

| 行範囲 | 節 | 分類 | 根拠 | 行数 |
|---|---|---|---|---|
| .dispatcher/references/spawn-flow.md:1-4 | 冒頭 | keep | 参照元は .dispatcher/CLAUDE.md:27（DELEGATE 受信時に必読） | 4 |
| .dispatcher/references/spawn-flow.md:5-11 | 輸送層 両系注記（3 点・二フレーム・Issue G） | transport | broker/renga の 3 点併記、mcp__ 読み替え、Refs #604 の二フレーム注記、Epic #6 Issue G の但し書き。root CLAUDE.md の輸送層節とほぼ同文 | 7 |
| .dispatcher/references/spawn-flow.md:12-17 | state-db cutover 注記 | keep | tools/state_db/writer.py が現存（update_session :289, register_worker_dir :342, upsert_run :449） | 6 |
| .dispatcher/references/spawn-flow.md:18-37 | 3-1 / 3-1a / 3-1b 概要 | keep | helper の choose_split が runner に現存。22 行の『旧設計は序数 k』は経緯（小） | 20 |
| .dispatcher/references/spawn-flow.md:38-38 | broker capacity gate 注記 | transport | broker 面と renga 面で capacity 機構が分岐する説明。broker 前提の二系統記述 | 1 |
| .dispatcher/references/spawn-flow.md:39-44 | renga overflow / spawn_tab / ceiling_applies の説明 | delete | git grep -- '--overflow-to-new-tab' は tools/scripts/.hooks で 0 件。本文自身も『現行 ja はこれらのフラグを渡していない』（42 行）と書いている。配線されていない runtime 機能の解説<br>**補足**: 配線するまで要らない。必要になったら `delegate-plan --help` と runtime の docstring を見ればよい<br>**検証**: 反証で維持: overflow 系フラグは runtime 側にある（runner.py:46,1270）が、ja の tools/scripts/.hooks/tests では git grep 0件で配線されていない。未配線の事実は3-1d:86 にも残る。check_renga_compat の spawn_tab は capability 観測で、この記述とは関係しない | 6 |
| .dispatcher/references/spawn-flow.md:45-50 | runtime SoT ポインタ | keep | --help に --peers-json/--server-capability/--tab/--overflow-to-new-tab がある（0.1.49）。build_plan/choose_split は runner に現存 | 6 |
| .dispatcher/references/spawn-flow.md:51-55 | 3-1c 候補空: verbatim 転送規則 | keep | runner.py:2474 の docstring『claude-org-ja forwards this text to the secretary verbatim』 | 5 |
| .dispatcher/references/spawn-flow.md:56-57 | verbatim の根拠（runtime docstring / LocaleConfig 4 キー） | keep（当初 move） | runtime 内部と locale の対象外という設計理由の説明。ja_locale.json は 4 キー（constraints_default/report_target_default/claude_md_filename_default/instruction_template）で事実は合っているが、手順としては不要<br>**検証**: 反証で move→keep: 56行冒頭『言い換え・要約・翻訳・再構成をしない』と57行冒頭『英語のまま転送する』は verbatim 転送の手順そのもの。移せるのは runtime docstring や LocaleConfig 4キーの根拠節だけで、bullet 全体を move にすると規則が消える | 2 |
| .dispatcher/references/spawn-flow.md:58-62 | 日本語見出しの別送・全分岐適用・skip | keep | helper の exit 2 分岐と対応する（.dispatcher/CLAUDE.md:86） | 5 |
| .dispatcher/references/spawn-flow.md:63-74 | 3-1d 背景タブ: 条件 1〜3 | keep | registry/dogfood_pending.md と dogfood-protocol.md が現存。capability_gate.py:45-53 に placement_production_activation 等がある | 12 |
| .dispatcher/references/spawn-flow.md:75-75 | 3 capability を要求する理由 | move | 契約 T-§cap の independence rule の解説で、条件 3 の手順自体は 73 行で完結している<br>**検証**: 反証で維持: 3 capability を spawn 前に要求する理由（independence rule・放置ペイン）の解説。条件3の確認手順は73行で完結している | 1 |
| .dispatcher/references/spawn-flow.md:76-79 | 条件 4〜6 | keep | capability_gate.py:87 の --gate 照会が現存。worker-idle-state.json の placement 判定は 3-2b と対応 | 4 |
| .dispatcher/references/spawn-flow.md:80-85 | 条件 6 の現状（2026-08-10 時点）・背景配置の実績・記録規律 | move | 本文が『恒久の事実ではない』と明記している日付付きスナップショットと、2026-08-09 の人間批准の経緯。手順は『判定のたびに照会』の 1 文で足りる<br>**検証**: 反証で維持: 日付付きのスナップショットと 2026-08-09 の批准経緯。手順として残すのは『判定のたびに照会し直す』と、84行の『記録は人間が書き、本経路の副作用として書かない』の2点。表の条件6は fail closed なので発動許可の誤読は起きない | 6 |
| .dispatcher/references/spawn-flow.md:86-87 | 背景経路は helper plan を使わない | keep | helper に --tab を渡していない（git grep で tools 0 件）ので、3-2b/3-4b の手動経路が必要 | 2 |
| .dispatcher/references/spawn-flow.md:88-89 | 契約側の対応（R1-R4 解説） | move | 契約 T-§4.2-place と R 条件の充足状況の解説。SoT は docs/contracts/backend-interface-contract.md T-§4.2-place-rec<br>**検証**: 反証で維持: 契約 T-§4.2-place と R1-R4 の対応・充足状況の解説で、手順の指示は無い。SoT は契約 T-§4.2-place-rec。90行の『背景 worker は close_pane を撃たず escalate』は範囲外で残る | 2 |
| .dispatcher/references/spawn-flow.md:90-91 | R3 close 半分未配線 → 背景 worker の CLOSE_PANE は escalate | keep | 『背景 worker の CLOSE_PANE は close_pane を撃たず窓口へ escalate』という行動規則を含む。周辺の経緯文は move 候補（judgments 参照） | 2 |
| .dispatcher/references/spawn-flow.md:92-111 | 3-2 spawn 呼び出しとモデル選択 | keep | org-start SKILL.md:536 が『モデル: sonnet 行が無ければ opus』を参照しており consumer が現存 | 20 |
| .dispatcher/references/spawn-flow.md:112-113 | モデルと classifier の独立性・dispatcher は sonnet | move | 設計理由の説明。dispatcher が sonnet/bypassPermissions で起動する事実は org-start SKILL.md:209-218 が正本<br>**検証**: 反証で維持: classifier とモデルの独立性は理由の説明。dispatcher の sonnet/bypassPermissions は org-start SKILL.md:209-219 の spawn 例が正本。worker のモデル規則は108-111行で完結している | 2 |
| .dispatcher/references/spawn-flow.md:114-114 | pane-layout ポインタ | keep | pane-layout.md が現存 | 1 |
| .dispatcher/references/spawn-flow.md:115-124 | 同一タブ既定の理由（Group A/B・selector・cross_tab_peers 解説） | keep（当初 move） | 契約 T-§4.2 / T-§sup / T-§cap / renga ソース行の解説 10 行。行動規則は 125-126 の MUST（close_pane/set_pane_identity は数値 id）に集約されている<br>**検証**: 反証で move→keep: 範囲内に行動規則が混ざっている: 115の『届くことは別タブ配置の licence にならない（MUST NOT）』、122の『cross_tab_peers から pane 制御到達を導出しない』、124の『タブ切替を pane_not_found の復旧にしない・窓口へ報告』。move できるのは解説の116-121・123だけ | 10 |
| .dispatcher/references/spawn-flow.md:125-138 | Group B MUST・name/role/cwd・args[]・エラーハンドリング | keep | commit 59cf3af（2026-08-09 #896）で Group B を数値 pane_id に移行済み。args[] 規則の正本（129）。ただし 129 が参照する knowledge/raw/2026-05-09-… は .gitignore:16 knowledge/raw/* で非追跡なので、リンク先が repo に無い | 14 |
| .dispatcher/references/spawn-flow.md:139-188 | 3-2b 背景タブ spawn と id 束縛 | keep | bound_* の永続化は worker-monitoring.md の (3-a-3)/(3-a-5) が consumer。capability_gate と背景経路が現存 | 50 |
| .dispatcher/references/spawn-flow.md:189-216 | 3-3 pane_started 確認 | keep | poll_events の cursor 意味論。背景経路の分岐は 3-2b と対応 | 28 |
| .dispatcher/references/spawn-flow.md:217-238 | 3-3b 起動プロンプト画面判定ループ | keep | spawn-prompt-step は runtime に現存（spawn_prompt.py:75 締切 120000ms）。2026-09-25 の盲目 Enter 事故（runtime#185）は直近の記録。221 行の plan_version<2 言及だけは到達不能（judgments 参照） | 22 |
| .dispatcher/references/spawn-flow.md:239-241 | broker の場合の 2 段承認 | transport | broker の folder-trust と dev-channel sidecar 承認の併記段落 | 3 |
| .dispatcher/references/spawn-flow.md:242-275 | 3-4 list_peers 待機・縮退・3-4b | keep | capability-first-drive-operational-gate.md §6 表 #7 が現存。spawn_gate の --evidence send_delivery と対応。commit 98735cf/87a39be。248 行の『runs.worker_dir_id と照合』は spawn_gate.py:47（delegate_sent）と食い違う（rules/judgments 参照） | 34 |
| .dispatcher/references/spawn-flow.md:276-279 | 3-5 指示送信（使用中 transport の send_message） | transport | 『既定 renga なら mcp__renga-peers__… / broker なら mcp__org-broker__…』の読み替え併記 | 4 |
| .dispatcher/references/spawn-flow.md:280-283 | 3-5a 冒頭 | keep | Issue #554。本ワーカー自身が send_keys の ultracode kickoff で武装されている（実走） | 4 |
| .dispatcher/references/spawn-flow.md:284-285 | 3-5a transport 両系注記 | transport | send_keys を mcp__renga-peers__ と mcp__org-broker__ で読み替える注記。『renga は未実走』とあるが、本タスクは renga 経由で武装されている（推測: 当時は broker でしか確認していなかった） | 2 |
| .dispatcher/references/spawn-flow.md:286-287 | 許可の判定 | keep | gen_delegate_payload.py --impl-guidance の描画先を判定する手順。本 brief の実装ガイダンス節に許可文言がある | 2 |
| .dispatcher/references/spawn-flow.md:288-295 | なぜ send_keys か・武装 scope | move | 実走で確定した事実と理由の説明。手順は 296-312 にある<br>**検証**: 反証で維持: send_keys を使う理由と武装 scope の実走事実の説明。実行手順は296-312行（2段打鍵・単独トークン・背景タブでは bound_pane_id）にそろっており、294の『別 turn に武装は引き継がれない』で新たに必要になる操作は無い | 8 |
| .dispatcher/references/spawn-flow.md:296-322 | 3-5a 手順 1〜6 | keep | 手順 6 は 2026-09-04 continuo-110-lease-renewal で 1 タスク 6 回の手動承認が要った件が根拠。worker brief（worker-claude-template.md）にも同じ規約があり consumer が現存 | 27 |
| .dispatcher/references/spawn-flow.md:323-324 | 検証状況メモ | move | 『本タスクで broker 実走確認済』という検証経緯のメモ。結論は手順 1 の『最も単純な保険』と同じ（302 行）<br>**検証**: 反証で維持: 検証経緯のメモ。結論の『send_keys を唯一のトリガにする』は302行と同じ文（delete でもよい） | 2 |
| .dispatcher/references/spawn-flow.md:325-334 | 非 ultracode・3-6 順次起動・Step 4 分担 | keep | helper の seed 生成と post-spawn の分担は .dispatcher/CLAUDE.md:92-97 と対応 | 10 |
| .dispatcher/references/spawn-flow.md:335-352 | Step 4-1 Status active flip とフォールバックテンプレート | outdated | 337 行で dispatcher に Status 更新と Progress Log 追記をさせているが、直後の 353 行は spawn_gate.py verify が副作用として書き dispatcher は手で書かないとしており、ファイル内で矛盾している（spawn_gate.py:826-839）<br>**補足**: Status active / Pane ID / Started / Progress Log は spawn_gate.py verify が書く（spawn-flow.md:353）。手で書くのは worker_record.status が missing/error のときと helper 未経由のときだけ<br>**検証**: 反証で維持: 337行は dispatcher に Status flip を書かせ、353行は spawn_gate verify が書く（spawn_gate.py:590-605,826-844）としており矛盾している。ただしフォールバック用テンプレート（339-351）は tests/test_worker_seed_status_contract.py:316-322 が Status: active を assert しているので必ず残す | 18 |
| .dispatcher/references/spawn-flow.md:353-354 | spawn_gate verify が書く旨 | keep | tools/spawn_gate.py:826-839 で worker_record と missing/error を判定している | 2 |
| .dispatcher/references/spawn-flow.md:355-356 | Status: 行は省略不可（runner.py 行番号・45 秒） | outdated | 導入済み runtime 0.1.49: _seed_status は runner.py:1352（記載は 1265-1286）、WORKER_BIND_WINDOW_SECONDS は :390 で値は 120000//1000+30+15=165 秒（記載は 45、:305）。規則そのもの（Status: 行が必須）は tests/test_worker_seed_status_contract.py が現存<br>**補足**: runtime 0.1.49 では WORKER_BIND_WINDOW_SECONDS = DEFAULT_APPROVAL_DEADLINE_MS//1000+30+15 = 165 秒（runner.py:390, spawn_prompt.py:75）。行番号を書くより関数名だけで参照するほうがよい<br>**検証**: 反証で維持: installed 0.1.49 で確認: _seed_status は runner.py:1352、WORKER_BIND_WINDOW_SECONDS は runner.py:390 で 120000//1000+30+15=165 秒（spawn_prompt.py:75）。『Status: 行は必須』という規則自体は test_worker_seed_status_contract が固定しているので keep | 2 |
| .dispatcher/references/spawn-flow.md:357-374 | Step 4-2 DB 登録コード | outdated | connect('.state/state.db') と claude_org_root=Path('.') が root 相対で、sys.path の調整も無い。dispatcher の cwd は .dispatcher/（.dispatcher/CLAUDE.md:42）で、ls .dispatcher → CLAUDE.md と references だけ。dispatcher-resume:209-216 は ../ と sys.path.insert を使っている<br>**補足**: dispatcher-resume SKILL.md:208-221 と同じ形（sys.path.insert(0, os.path.abspath('..'))、connect('../.state/state.db')、claude_org_root=Path('..')）にする<br>**検証**: 反証で維持: dispatcher の cwd は .dispatcher/（CLAUDE.md:42）で、python -c は cwd を sys.path に入れるため tools が解決できず、connect('.state/state.db') も cwd 相対になる。dispatcher-resume SKILL.md:208-221 の ../ と sys.path.insert の形が正しい | 18 |
| .dispatcher/references/spawn-flow.md:375-380 | Step 4-3 journal 追記 | outdated（当初 delete） | .dispatcher/CLAUDE.md:104 に同じイベントの追記がある（こちらは ../ 付きで正しい）。本節は bash tools/journal_append.sh で cwd .dispatcher/ からは解決しない（.dispatcher/tools は存在しない）<br>**補足**: 正本は .dispatcher/CLAUDE.md:104（bash ../tools/journal_append.sh worker_spawned …）<br>**検証**: 反証で delete→outdated: 重複より誤記の問題。Step 4 は helper 未経由の経路の手順でもあり（333行）、CLAUDE.md:97-104 は helper 節の中にある。消すとフォールバック経路で worker_spawned が抜け、spawn_gate verify が exit10 になる（:60-61）。パスを ../tools/journal_append.sh に直すべき | 6 |
| .dispatcher/references/spawn-flow.md:381-385 | Step 4-4 JSON 自動再生成・4-5 監視登録 | keep | StateWriter の post-commit hook は Issue #284（spawn-flow.md:14 と一致） | 5 |
| .dispatcher/references/spawn-flow.md:386-407 | Step 4-6/4-7 same_tab_peer_id・bound_peer_id | keep | consumer は worker-monitoring.md の (3-a-3)/(3-a-5)/Step 5(b) 規則 (4)。commit b08c997/59cf3af（2026-08-09） | 22 |
| .dispatcher/references/spawn-flow.md:408-409 | Step 4-8 Step 5 へ | keep | 派遣完了ゲートへの導線 | 2 |
| .dispatcher/references/spawn-flow.md:410-411 | 項番重複の経緯注記 | move | 『かつて派遣完了報告が付録の後ろに…』という編集履歴の説明<br>**検証**: 反証で維持: 項番重複の編集履歴の説明で、手順は含まない。必要な指示（Step 5 へ進む）は408行にある | 2 |
| .dispatcher/references/spawn-flow.md:412-442 | Worker Directory Registry（DB 由来のセクション定義付録） | move | 本文自身が『手順の続きではない』と書いている（410）。運用ルール（439-441）の実行主体は窓口（gen_delegate_payload.py apply / org-pull-request 2b-ii）で、dispatcher の手順ではない。git grep で同じ表の重複は無い<br>**検証**: 反証で維持: DB 由来セクションの定義を置く付録（410行で手順ではないと明記）。運用ルール439-441の実行主体は窓口。dispatcher 向けの『markdown 直接編集禁止・transaction 経由』は357行と同じなので、移しても失われない | 31 |
| .dispatcher/references/spawn-flow.md:443-444 | Step 5 見出し | keep | 派遣完了ゲート節の見出し | 2 |
| .dispatcher/references/spawn-flow.md:445-448 | 由来（2026-08-18 の 2 件） | move | 事故の経緯と『壊れていたのは検証の側』という分析。ゲートの規則は 449 行以降にある<br>**検証**: 反証で維持: 2026-08-18 の2件の経緯と『壊れていたのは検証側』という分析。MUST NOT と verify の手順は449-477行にあり、範囲内に実行指示は無い | 4 |
| .dispatcher/references/spawn-flow.md:449-478 | spawn_gate verify 手順・exit 分岐・peer-cwd 照合・evidence send_delivery | keep | tools/spawn_gate.py が現存（delegate_sent :144, worker_dir_divergence :174, GATE_EPOCH :161）。worker_spawn_verified は docs/journal-events.md:93 | 30 |
| .dispatcher/references/spawn-flow.md:479-479 | v1 止血注記（Interlock v2・#740） | move | v2 方針（逆移植しない）という経緯の説明で、dispatcher の手順ではない<br>**検証**: 反証で維持: v1 止血と Interlock v2・#740 の方針の説明で、dispatcher の実行手順は含まない | 1 |

### .dispatcher/references/loop-directive-guard.md（109 行: keep 109 / move 0 / delete 0 / outdated 0 / transport 0）

| 行範囲 | 節 | 分類 | 根拠 | 行数 |
|---|---|---|---|---|
| .dispatcher/references/loop-directive-guard.md:1-15 | 冒頭・何を止めるか | keep | .hooks/check-loop-directive.sh が現存（CronCreate/ScheduleWakeup で 12 ヒット）。commit b910d4e #992。正文は dispatcher-resume SKILL.md:356 | 15 |
| .dispatcher/references/loop-directive-guard.md:16-30 | なぜ機構にしたか（2026-09-02 事故） | keep | このファイルは起動時に読まれない rationale 文書（参照元は permissions.md:338, hook, docs/getting-started.md, tests/test-check-loop-directive.sh だけ）。既に rationale の置き場なので移す必要は無い | 15 |
| .dispatcher/references/loop-directive-guard.md:31-46 | D-: 監視ループ以外も一律 deny・stop:true 例外 | keep | hook の :36 と :120-124 で stop:true を許可している | 16 |
| .dispatcher/references/loop-directive-guard.md:47-76 | D-: 閉じた 2 形との完全一致・空白除去 | keep | tests/test-check-loop-directive.sh と tests/test_dispatcher_resume_loop_invariant.py が現存 | 30 |
| .dispatcher/references/loop-directive-guard.md:77-91 | D-: 判定不能は fail-closed | keep | hook :64 に CLAUDE_ORG_PATH 未設定時の deny がある。tests/test-hooks-payload-fail-closed.sh が現存 | 15 |
| .dispatcher/references/loop-directive-guard.md:92-104 | 配布範囲: ディスパッチャーのみ | keep | tools/org_extension_schema.json:29,:261 と permissions.md:286 に登録済み。org-start SKILL.md:323-334 の Block C4 で check_role_configs.py --include-local を実行 | 13 |
| .dispatcher/references/loop-directive-guard.md:105-109 | 正文を変更したいとき | keep | tools/gen_skill_prose.py と SKILL.md.in が現存（編集は .in 側） | 5 |

### .claude/skills/dispatcher-handover/SKILL.md（218 行: keep 196 / move 0 / delete 14 / outdated 0 / transport 8）

| 行範囲 | 節 | 分類 | 根拠 | 行数 |
|---|---|---|---|---|
| .claude/skills/dispatcher-handover/SKILL.md:1-23 | frontmatter | keep | 編集は SKILL.md.in 側（gen_skill_prose.py の生成物）。allowed-tools の mcp__org-broker__send_message は broker フレームで描画されたもの（transport の judgments 参照） | 23 |
| .claude/skills/dispatcher-handover/SKILL.md:24-31 | 目的 | keep | 窓口側の打鍵経路 root CLAUDE.md:64-65 が consumer | 8 |
| .claude/skills/dispatcher-handover/SKILL.md:32-39 | 輸送層両系ヘッダ（既定 broker・二フレーム・Issue G） | transport | SKILL.md.in:32 では {{> dual-system-header-long }} の partial（tools/gen_skill_prose.py）が展開される。既に共通 partial 1 か所から生成されている | 8 |
| .claude/skills/dispatcher-handover/SKILL.md:40-51 | 重要な前提（実行主体・ペイン維持・SoT） | keep | resume:37-43 と対になる前提 | 12 |
| .claude/skills/dispatcher-handover/SKILL.md:52-61 | 触らない状態ファイル一覧 | keep | handover 内の正本。同じ一覧が同ファイル 216-217・.dispatcher/CLAUDE.md:192-200・resume:44-48,429-430 にもある | 10 |
| .claude/skills/dispatcher-handover/SKILL.md:62-83 | Step 1 / Step 2 冒頭 | keep | get_org_state_summary は tools/state_db/queries.py:326 に現存 | 22 |
| .claude/skills/dispatcher-handover/SKILL.md:84-95 | Step 2 root cwd 用コードブロック | delete | 40-41 行で本 skill は .dispatcher/ cwd で実行すると定めているのに、このブロックは connect('.state/state.db') を root 相対で呼ぶ（sys.path も無し）。同じ処理の .dispatcher/ 版が 106-120 にあり重複<br>**補足**: 正本は同ファイル 104-120（.dispatcher/ から実行する版）<br>**検証**: 反証で維持: 40-41行は .dispatcher/ cwd で実行すると定めているのに、84-95行は root 相対で sys.path の調整も無いので、この cwd では import に失敗する。同じ処理を正しく書いた版が104-120行にある。注: 編集は SKILL.md.in 側、81-83行の導入文も整理対象 | 12 |
| .claude/skills/dispatcher-handover/SKILL.md:96-121 | 取り出す項目と .dispatcher/ 版コード | keep | queries.py:326 get_org_state_summary。$TMPDIR に書くのはサンドボックスの書き込み境界と整合する | 26 |
| .claude/skills/dispatcher-handover/SKILL.md:122-185 | Step 3 handover ファイル書き出し | keep | resume Step 1（:182-198）が created_at と 7 日窓の consumer。.dispatcher/CLAUDE.md:187 の起動分岐も読む | 64 |
| .claude/skills/dispatcher-handover/SKILL.md:186-195 | Step 4 イベント記録 | keep | docs/journal-events.md:454 に dispatcher_handover（active_workers, pending_decisions, note）が登録済み | 10 |
| .claude/skills/dispatcher-handover/SKILL.md:196-215 | Step 5 secretary 通知・やってはいけないこと前半 | keep | 窓口側の consumer は root CLAUDE.md:64（DISPATCHER_HANDOVER_READY → /clear → /dispatcher-resume） | 20 |
| .claude/skills/dispatcher-handover/SKILL.md:216-217 | 状態ファイルを編集/削除しない（再掲） | delete | 同じファイル 52-58 の『絶対に削除・編集しない』一覧と同内容（対象 4 ファイルも同じ）<br>**補足**: 正本は同ファイル 52-58<br>**検証**: 反証で維持: 52-58行が同じ4ファイルに worker-*.md を加えて『絶対に削除・編集しない』と書いており、正本のほうが強い。216-217にしか無い条件は無い。注: 編集は .in 側 | 2 |
| .claude/skills/dispatcher-handover/SKILL.md:218-218 | /loop を自分で停止しない | keep | この行にしか無い。監視の連続性という制約と整合する | 1 |

### .claude/skills/dispatcher-resume/SKILL.md（436 行: keep 422 / move 0 / delete 0 / outdated 1 / transport 13）

| 行範囲 | 節 | 分類 | 根拠 | 行数 |
|---|---|---|---|---|
| .claude/skills/dispatcher-resume/SKILL.md:1-29 | frontmatter | keep | 編集は SKILL.md.in 側。allowed-tools の mcp__org-broker__* 6 件は broker フレームで描画されたもの（transport の judgments 参照） | 29 |
| .claude/skills/dispatcher-resume/SKILL.md:30-36 | 目的 | keep | /loop 再開の正文は 356 行で、hook がそこを読む | 7 |
| .claude/skills/dispatcher-resume/SKILL.md:37-50 | 前提（状態ファイル継続を含む） | keep | 状態ファイル一覧の resume 側の正本 | 14 |
| .claude/skills/dispatcher-resume/SKILL.md:51-58 | 輸送層両系ヘッダ（既定 broker・二フレーム・Issue G） | transport | broker/renga の併記と Refs #604 の二フレーム注記、Epic #6 Issue G の但し書き。gen_skill_prose の partial から描画されたもの | 8 |
| .claude/skills/dispatcher-resume/SKILL.md:59-67 | Step 0 の前: 冒頭（one-shot・経緯リンク） | keep | stale 再発火ガードの目的。リンク先の knowledge/raw/2026-06-19-… は .gitignore:16 で非追跡なので repo に無い（git log --all でも 0 件）。経緯の要旨は tests/test_dispatcher_resume_loop_invariant.py:3 の docstring にある | 9 |
| .claude/skills/dispatcher-resume/SKILL.md:68-130 | Step 0 の前: 存在判定と (i)/(ii)/unknown 分岐 | keep | commit 98735cf/b08c997（2026-08-09）で導入。capability gate 表 #5 が現存。journal の anomaly_observed は journal_append.sh 経由 | 63 |
| .claude/skills/dispatcher-resume/SKILL.md:131-177 | Step 0 identity 確認（RENGA_PANE_ID・broker 未確定・peer_id 名前引き不可） | keep | commit 59cf3af（2026-08-09 #896、Group B は数値 id）。update_session は writer.py:289 にあり、未指定を保持と解釈する。142-147 の broker 分岐は transport 寄りだが手順が異なるので残す | 47 |
| .claude/skills/dispatcher-resume/SKILL.md:178-201 | Step 1 handover 読み込み・鮮度 | keep | handover:177-182 の UTC 規約と対応する。7 日窓は .dispatcher/CLAUDE.md:187 と一致 | 24 |
| .claude/skills/dispatcher-resume/SKILL.md:202-256 | Step 2 state.db atomic 更新 | keep | tools/state_db/writer.py:289 update_session、StateWriter.transaction が現存。コードは ../ と sys.path を正しく使っている | 55 |
| .claude/skills/dispatcher-resume/SKILL.md:257-278 | Step 3 照合 | keep | queries.py:326 get_org_state_summary | 22 |
| .claude/skills/dispatcher-resume/SKILL.md:279-298 | Step 4 ペイン生存確認 | keep | capability gate 表 #6 と §1-2 が現存（commit b08c997 #892 で二段化） | 20 |
| .claude/skills/dispatcher-resume/SKILL.md:299-344 | Step 5-1 inflight 再生成・5-2 再開条件 | keep | pane-close.md 5-3（:311）の JSON 正本と worker-monitoring Step 5.3 が consumer。commit 79c4adf（2026-07-03 #663）で『宣言ではなく実 invoke』を明記 | 46 |
| .claude/skills/dispatcher-resume/SKILL.md:345-358 | INVARIANT(loop-prompt) と canonical /loop 正文 | keep | tests/test_dispatcher_resume_loop_invariant.py:30 の INVARIANT_MARKER が pin しており、.hooks/check-loop-directive.sh が実行時にこの fenced block を読む | 14 |
| .claude/skills/dispatcher-resume/SKILL.md:359-368 | cursor 束縛の扱い | keep | commit f401964（2026-08-10 #919）。判定の正本は worker-monitoring『監視ループ 1 サイクル』Step 1（:102） | 10 |
| .claude/skills/dispatcher-resume/SKILL.md:369-373 | check_messages drain（broker push 一次の補足） | transport | broker の channel sidecar、§9.6/§9.3 lease-reap など broker 前提の補足。drain の論理自体は transport に依存しない | 5 |
| .claude/skills/dispatcher-resume/SKILL.md:374-400 | idle state・監視対象 0 件・Step 6 ブリーフィング | keep | 窓口側の consumer は root CLAUDE.md:65（DISPATCHER_RESUMED / _IDLE を待つ） | 27 |
| .claude/skills/dispatcher-resume/SKILL.md:401-405 | Step 7 consumed rename の目的 | keep | .dispatcher/CLAUDE.md:190 の分岐と対応する | 5 |
| .claude/skills/dispatcher-resume/SKILL.md:406-406 | .consumed.md が .prev.md に置き換わる旨 | outdated | handover:127-132 がバックアップするのは live の .md → .prev.md だけで、.consumed.md には触らない。.consumed.md を置き換えるのは次回 resume の mv（本ファイル:409）<br>**補足**: .consumed.md は次の resume の mv（:409）で上書きされるまで残る。.prev.md は handover Step 3 が live の .md を退避したもの（dispatcher-handover SKILL.md:127-132）<br>**検証**: 反証で維持: handover SKILL.md:127-132 が退避するのは live の .md→.prev.md だけ。resume 後は .md が無いので次の handover で .consumed.md は置き換わらず、次の resume の mv（:409）まで残る。注: 編集は .in 側 | 1 |
| .claude/skills/dispatcher-resume/SKILL.md:407-428 | mv コマンド・イベント記録・やってはいけないこと前半 | keep | docs/journal-events.md:455 に dispatcher_resumed（pane_id, peer_id, active_workers, note）が登録済み | 22 |
| .claude/skills/dispatcher-resume/SKILL.md:429-430 | 状態ファイルを初期化/削除しない（再掲） | keep（当初 delete） | 同じファイル 44-48 の『新規作成・初期化しない』と同内容（対象 4 ファイルも同じ）<br>**補足**: 正本は同ファイル 44-48<br>**検証**: 反証で delete→keep: 同じ強さではない。44-48行は『新規作成・初期化しない』だけで、『削除しない』は429-430にしか無い。消すと削除禁止が抜けるので残すか、44-48側へ統合する（.in 側で編集） | 2 |
| .claude/skills/dispatcher-resume/SKILL.md:431-436 | 食い違い報告・atomic・/loop prompt 省略禁止 | keep | 434-436 は INVARIANT（:345-353）の要約で、テストが pin している。atomic は Step 2 と一致する | 6 |

### .dispatcher/references/worker-monitoring.md（1359 行: keep 1197 / move 106 / delete 6 / outdated 44 / transport 6）

| 行範囲 | 節 | 分類 | 根拠 | 行数 |
|---|---|---|---|---|
| .dispatcher/references/worker-monitoring.md:1-3 | タイトルと導入 | keep | 本ファイルは .dispatcher/CLAUDE.md「ワーカーペイン監視」節から参照される本体（.dispatcher/CLAUDE.md の『詳細手順…は worker-monitoring.md を参照』）。生成物: tools/skill_src/manifest.json:90-91 が worker-monitoring.md.in → .md。編集は .in 側 | 3 |
| .dispatcher/references/worker-monitoring.md:4-9 | 輸送層 両系注記（既定 broker / opt-in renga、Issue G） | transport | 二重記述: 本ファイルは『既定 broker / opt-in renga』（L5）、root CLAUDE.md と .dispatcher/CLAUDE.md は『既定 renga / opt-in broker』の運用フレーム。.in:5 が {{> worker-monitoring-transport }} fragment 展開。L9 に『broker 実走は Issue G スコープ』但し書き。実運用は renga（brief 参考事実） | 6 |
| .dispatcher/references/worker-monitoring.md:10-14 | 観測の原則 見出し・適用宣言 | keep | 全判定 (Step1/3/4/5/5.1/5.2/5.3) がこの節を参照（L193, L237, L447 等の #observation-principle リンク）。Issue #869（2026-08-08 誤検知 3 種）が根拠 | 5 |
| .dispatcher/references/worker-monitoring.md:15-16 | 原則導入の経緯（2026-08-08 誤検知 3 種） | move | 事故経緯の説明で手順ではない。同旨が設計メモ L1341 にも重複。rationale 側へ<br>**検証**: 反証で維持: L15 は 2026-08-08 誤検知 3 種の経緯と『原則をここに置く』理由だけ。参照指示の本体は L14（範囲外）に残る。tests の grep に固定文字列は無い（tests/test_completion_reported_flag.py だけが本文を pin しており、L15 の文言は対象外）。 | 2 |
| .dispatcher/references/worker-monitoring.md:17-20 | (P1) 観測できない≠起きていない | keep | (P4) の OBSERVATION_UNAVAILABLE 経路の前提。Issue #869 (2026-08-08) の誤検知対策として L82 に記録 | 4 |
| .dispatcher/references/worker-monitoring.md:21-22 | (P1) sibling 手順書の先例 | move | pane-close.md 1 の『理由』を一般化したという由来説明で、手順上の指示は無い<br>**検証**: 反証で維持: L21 は pane-close.md 1 の『理由』を一般化したという由来の説明で、手順の指示は無い。(P1) の規範は L19 に残る。tests/tools にこの文言の pin は無い。 | 2 |
| .dispatcher/references/worker-monitoring.md:23-32 | (P2) 複数観測面一致での申告 | keep | (3-a-2) 表・(b-4)・Step5.2(b) が本規則を適用（L237, L724, L1047）。Issue #869 | 10 |
| .dispatcher/references/worker-monitoring.md:33-49 | (P3) 観測面ごとの証拠能力表 | keep | docs/contracts/backend-interface-contract.md §1.5/§4.2/§3.1/T-§2.2 を出典に持つ判定表。tools/inspect_pane_state.py の _NEW_SPINNER_RE を参照（L46） | 17 |
| .dispatcher/references/worker-monitoring.md:50-75 | (P4) 観測不能の報告語彙と journal・de-dup | keep | journal_append.sh anomaly_observed/notify_sent kind=observation_unavailable の producer。(3-a-4)/(b-4)/5.3 が消費（L302, L740, L1275） | 26 |
| .dispatcher/references/worker-monitoring.md:76-81 | (P5) 精度向上≠行動増加・close_pane 3 経路限定 | keep | close_pane の許可経路を pane-close.md / 5.3(a)(b) に限定する制約。Issue #869 | 6 |
| .dispatcher/references/worker-monitoring.md:82-82 | (P5) 2026-08-08 実績の経緯 | move | 『人間が別タブで開いていたセッションだった』という事故経緯。設計メモ L1342 と同旨の重複<br>**検証**: 反証で維持: L82 は人間が別タブで開いていたセッションという実例の記述。禁止そのものは L81 と L83 にあり、L1342（設計メモ）にも同じ実例がある。test の pin は無い。 | 1 |
| .dispatcher/references/worker-monitoring.md:83-84 | (P5) 判定不能は行動を増やさない | keep | 規範 1 行。(3-a-4) L306 が参照 | 2 |
| .dispatcher/references/worker-monitoring.md:85-99 | 監視ループ見出し・時刻 UTC 規約 | keep | worker-idle-state.json / curate-inflight.json の ts 規約。consumer: Step5.2(c)(ii) の occurred_at 比較 (L1086)、5.3 の now-started_at (L1288)。pane-close.md 5-3 を一次参照 | 15 |
| .dispatcher/references/worker-monitoring.md:100-135 | Step1 poll_events と cursor の session 束縛 | keep | commit f401964『event cursor を backend session に束縛し…』。2026-08-09 実測 (cursor 64 vs next_since 7)。schema は docs/contracts/state-schema-contract.md §1.7 | 36 |
| .dispatcher/references/worker-monitoring.md:136-166 | Step1 session_key 導出と cursor 破棄の fail loud | keep | f401964 の手順本体。EVENT_CURSOR_RESET は .dispatcher/CLAUDE.md『監視 gap を埋める内部状態ファイル』節が参照。kind=stale_event_cursor は契約 T-§4.2-place-rec (R3) 記録と一致 | 31 |
| .dispatcher/references/worker-monitoring.md:167-198 | Step1 イベント処理と attribution | keep | 契約 T-§3.1 (name 単独照合禁止) 準拠。canonical→alias 順は renga-error-codes.md poll_events 節が一次参照。WORKER_PANE_EXITED は docs/contracts/role-contract.md:37,88 に記載 | 32 |
| .dispatcher/references/worker-monitoring.md:199-202 | pane_started 無視 / events_dropped 再掲 / heartbeat の jq / Phase 2.1 --timeout | delete | L200 は L170 の完全重複。L199/L201 は L167 の types フィルタで既に除外済み。『既存 jq フィルタ』『Phase 2.1 の --timeout』は CLI 時代の残骸: git grep -n 'Phase 2.1\\|jq フィルタ' のヒットは本ファイルと .in のみ（MCP 呼び出しに jq も --timeout も無い）<br>**補足**: 重複の正本: L167（types フィルタ）と L170（events_dropped の journal 記録）<br>**検証**: 反証で維持: L167 の types=[pane_exited,events_dropped] で pane_started と heartbeat は除外済み。L200 は L170 と同文。jq も --timeout も MCP 呼び出しには無い（L120 は timeout_ms=5000）。git grep 'Phase 2.1\|jq フィルタ' のヒットは本ファイルと .in だけ。tests に pin 無し。.in 側を編集する。 | 4 |
| .dispatcher/references/worker-monitoring.md:203-218 | Step2 check_messages 自己報告の転送 | keep | APPROVAL_BLOCKED/ERROR_DETECTED は role-contract.md:88 で窓口宛と規定。journal_append.sh source=self_report | 16 |
| .dispatcher/references/worker-monitoring.md:219-225 | Step2 WORKER_COMPLETION_NOTED / WORKER_REOPENED | keep | git grep -ln WORKER_COMPLETION_NOTED: producer は org-delegate/SKILL.md・org-pull-request/SKILL.md、consumer は本ファイル・pane-close.md・delegation-lifecycle-contract.md。Issue #658 | 7 |
| .dispatcher/references/worker-monitoring.md:226-236 | Step3 list_panes 突き合わせと背景 worker 振り分け | keep | tracked_pane_id producer。L235『pane 上限 16』は pr-watch-pane/SKILL.md:200 の RENGA_MAX_PANES=16 と一致 | 11 |
| .dispatcher/references/worker-monitoring.md:237-243 | (3-a-1) 自分の list_panes が org タブを解決しているか | keep | capability-first-drive-operational-gate.md §6 表 #9 の call site。契約 §1.5/§4.2/§4.3。Issue #869 | 7 |
| .dispatcher/references/worker-monitoring.md:244-270 | (3-a-2) 独立面突き合わせ表 | keep | 契約 T-§2.1 indeterminate 規律・T-§3.1。§1-2 が owner と明記し再掲しない構造。5.3 close 判定表 L1257/L1265 が本ゲートを参照 | 27 |
| .dispatcher/references/worker-monitoring.md:271-295 | (3-a-3) 陽性履歴の控え（worker-idle-state 追加フィールド） | keep | same_tab_peer_id の spawn producer は spawn-flow.md Step 4 / .dispatcher/CLAUDE.md delegate-plan 節が配線済み。inspect_pane_state.py compute_idle_transition が未知キー保全 | 25 |
| .dispatcher/references/worker-monitoring.md:296-318 | (3-a-4) unknown/indeterminate 時の継続動作 | keep | Step7 L1324 の停止条件と連動。契約 delegation-lifecycle-contract.md T7/E1 | 23 |
| .dispatcher/references/worker-monitoring.md:319-376 | (3-a-5) 背景タブ worker の生存判定（tracked-any-tab） | keep | spawn-flow.md:82『人間承認のもとで背景配置は既に複数回行われている』、契約 L460 で (R2) satisfied の根拠として本節を名指し。L321『実際には 1 件も発生しない』は spawn-flow.md:82 と食い違う（判断事項参照） | 58 |
| .dispatcher/references/worker-monitoring.md:377-409 | (3-a-5) 全ペイン index の fail-closed ゲートと使い方 | keep | fail-closed 規則（selector を送らない）は現時点でも効く。ただし発火しない: 契約 backend-interface-contract.md:880『Status: PROPOSED』、grep -n cross_tab_list tools/capability_gate.py は 0 件 | 33 |
| .dispatcher/references/worker-monitoring.md:410-422 | 全ペイン index の現況（2026-08-10）と 3 条件解除手順 | keep（当初 move） | L412 自身が『契約 L-§discharge を写したもの、食い違ったら契約側が正』と明記。毎サイクルの手順ではなく人間・実装者向けの解除手順。現況は日付付きスナップショット<br>**検証**: 反証で move→keep: rationale ではなく手順。L410 は『今日は枝が発火しない』という gate、L414 は MUST NOT の順序、L419(b)(i) は dispatcher が実行する『撃つ前に pending を書く (MUST)』。contract:976-1006 L-§discharge と重複するので pointer 化は可能だが、それは move ではなく正本への集約で、人の判断が要る。 | 13 |
| .dispatcher/references/worker-monitoring.md:423-431 | (3-a-5) 終了確定・画面監視・追加待ち時間 | keep | 背景 worker の inspect 宛先 bound_pane_id と終了確定規則。契約 T-§4.2 Group A / T-§3.1 | 9 |
| .dispatcher/references/worker-monitoring.md:432-452 | Step4 inspect_pane 実行・宛先・エラー分岐 | keep | lines=height は Issue #492 gap1。FOREMAN_STOPPING は docs/contracts/role-contract.md:113 が規定。error code は renga-error-codes.md:31 | 21 |
| .dispatcher/references/worker-monitoring.md:453-467 | (a)(b) target line と APPROVAL_BLOCKED regex | keep | tools/inspect_anomaly_scan.py が正準実装（L507）、tests/test_inspect_anomaly_scan.py | 15 |
| .dispatcher/references/worker-monitoring.md:468-488 | (b-fp) 数字選択メニューの取りこぼし（2026-07-27） | keep | 恒久対応未実装: grep -n 'Enter to select\\|❯' tools/inspect_anomaly_scan.py は 0 件。目視確認の当座運用がまだ必要 | 21 |
| .dispatcher/references/worker-monitoring.md:489-495 | (c) cursor 補強の confidence 分岐 | keep | (e) step2 の通知判定が consume | 7 |
| .dispatcher/references/worker-monitoring.md:496-506 | (d) ERROR 検出パターン | keep | inspect_anomaly_scan.py scan_lines() に codify。Issue #492/#671 | 11 |
| .dispatcher/references/worker-monitoring.md:507-518 | (d) 正準実装の呼び出し（py -3） | outdated | L513 が `py -3 ../tools/inspect_anomaly_scan.py`。`command -v py python3 python` の出力は /usr/bin/python3 のみ（py も python も無い）。同型の綴り誤りで relay が 20 日空振りした記録 L1203<br>**補足**: python3 ../tools/inspect_anomaly_scan.py。同ファイル L782/L858/L1188 は既に python3。L781 の『PowerShell 環境は py -3』注記と同形の注記に留める<br>**検証**: 反証で維持: L513 は py -3 だけ。command -v の出力は /usr/bin/python3 のみ。同じ型の綴り誤り（python）で relay が 20 日空振りし、58d8eda(#945) で python3 に直した（L1203）。L781 は python3 に PowerShell 注記を添える形、pane-close.md:244-245 は両方を併記。install.ps1 があるので Windows 注記は残す。 | 12 |
| .dispatcher/references/worker-monitoring.md:519-520 | 旧形式 spinner-age 誤検知抑止の理由（Issue #698） | move | --spinner-state-file を使う理由の説明。手順（フラグを付ける）は L511-515 に既にある。inspect_anomaly_scan.py:41 docstring が同じ説明を持つ<br>**検証**: 反証で維持: 凍結サマリ誤検知の原理と helper の内部挙動（state file が壊れたときのフォールバック）の説明。dispatcher の作業（--spinner-state-file を付ける）は L511-515 にある。tests に pin 無し。 | 2 |
| .dispatcher/references/worker-monitoring.md:521-529 | (d-fp) 文書中 status code の誤検知（2026-07-29） | keep | 恒久対応未実装: inspect_anomaly_scan.py にコメント行除外のヒューリスティックは無い（grep -n 'docstring\\|comment' は 0 件）。抑制判断の当座運用が必要 | 9 |
| .dispatcher/references/worker-monitoring.md:530-539 | (e) 観測記録（py -3 journal_append.py） | outdated | L535 `py -3 ../tools/journal_append.py`。本環境に py が無い（command -v は /usr/bin/python3 のみ）<br>**補足**: python3 ../tools/journal_append.py anomaly_observed ...（他の journal 記録は bash ../tools/journal_append.sh で統一済み）<br>**検証**: 反証で維持: L535 は py -3 ../tools/journal_append.py。この環境に py は無い（command -v は /usr/bin/python3 のみ）。correction は python3（Windows 注記付き）。L530 の見出しと step1 の手順は残し、綴りだけ直す。 | 10 |
| .dispatcher/references/worker-monitoring.md:540-558 | (e) 通知判定・de-dup・notify_sent | keep | 30 秒 de-dup は Step2/5/5.1/5.2 が共有（L207, L822, L1015, L1151） | 19 |
| .dispatcher/references/worker-monitoring.md:559-566 | (f) 通知フォーマット | keep | 窓口が受ける APPROVAL_BLOCKED/ERROR_DETECTED 形式（role-contract.md:88） | 8 |
| .dispatcher/references/worker-monitoring.md:567-569 | (g) 自己申告と inspect の併用設計 | move | 設計説明。de-dup の規則自体は (e) step2 にある。設計メモ L1347 と同旨<br>**検証**: 反証で維持: 『両方独立稼働で OK』という設計説明。de-dup 規則の本体は (e) step2（L540-）。手順の指示は無い。 | 3 |
| .dispatcher/references/worker-monitoring.md:570-572 | (h) secretary inspect cadence は別 Issue（#492 gap4） | move | 『本 PR スコープ外』という PR 時点の判断記録で、dispatcher の手順ではない<br>**検証**: 反証で維持: Issue #492 gap4 を PR スコープ外にした判断の記録。dispatcher の手順は含まない。 | 3 |
| .dispatcher/references/worker-monitoring.md:573-580 | Step5 定数（15/60/90 分） | keep | SPINNER_ACTIVE_SUPPRESS_CAP_MIN の正準値は tools/inspect_pane_state.py。.dispatcher/CLAUDE.md エントリ要約が同値を引用 | 8 |
| .dispatcher/references/worker-monitoring.md:581-583 | Step5 (a) 動機（Issue #287） | move | 事故経緯（session #12）の説明のみ<br>**検証**: 反証で維持: Issue #287 / session #12 の動機説明だけ。判定は (b) の L585 から始まる。 | 3 |
| .dispatcher/references/worker-monitoring.md:584-586 | Step5 (b) stall 候補条件 | keep | 3 サイクル hash 不変の規則。(b-4-i) が consume | 3 |
| .dispatcher/references/worker-monitoring.md:587-588 | content hash 化の理由（Issue #680） | move | 旧実装の誤発火経緯。knowledge/curated/dispatcher-monitoring.md:36-42 に同じ説明が既にある<br>**検証**: 反証で維持: 旧実装が誤発火した経緯（#680）。L587 末尾の『手製ハッシュ禁止』は L589 の『hash / streak を手計算しない』が同じ強さで書いているので、規範は失われない。 | 2 |
| .dispatcher/references/worker-monitoring.md:589-608 | inspect_pane_state.py record モード呼び出し（py -3） | outdated | L595 `py -3 ../tools/inspect_pane_state.py`。py は本環境に無い（command -v 出力は /usr/bin/python3 のみ）。--prev-record/--anomaly-fired は inspect_pane_state.py:439,459 に実在<br>**補足**: python3 ../tools/inspect_pane_state.py --prev-record ... --now-ts ...<br>**検証**: 反証で維持: L595 は py -3 ../tools/inspect_pane_state.py で、この環境に py は無い。--prev-record と --now-ts は helper に実在する。直すのは綴りだけで、記録をそのまま書く手順は keep。 | 20 |
| .dispatcher/references/worker-monitoring.md:609-610 | observation-only モード注記 | keep | --prev-spinner-signature 等は inspect_pane_state.py:417,426 に実在 | 2 |
| .dispatcher/references/worker-monitoring.md:611-644 | idle streak の保持・schema・更新規則 (1)〜(7) | keep | helper compute_idle_transition() が規則 (1)(2)(2-bis)(3)(5) を生成、(4)(6)(7) は caller-applied。tests/test_inspect_pane_state.py。L631 に Codex round 3 経緯が埋め込まれている（判断事項） | 34 |
| .dispatcher/references/worker-monitoring.md:645-657 | (b-3) active-spinner suppress | keep | Issue #671。helper の suppress_stall/cap_exceeded を consume。kind=spinner_active_suppress の soft-note | 13 |
| .dispatcher/references/worker-monitoring.md:658-692 | (b-2) PR-pending-merge sub-state 判定 | keep | pr_opened/pr_merged は docs/journal-events.md の secretary 記帳 event。Issue #304 | 35 |
| .dispatcher/references/worker-monitoring.md:693-699 | (b-2) 設計メモ | move | 60 分の根拠・option 1 採用理由などの設計理由で手順ではない<br>**検証**: 反証で維持: L694-698 は option1 を採った理由、60 分の根拠、merge 後に lookback が戻ることの説明。判定手順の本体は (b-2) 本文にある。 | 7 |
| .dispatcher/references/worker-monitoring.md:700-711 | (b-fp) 型1 長時間バッチ待ちの誤発火（2026-07-28） | keep | 当座運用（stall_suppressed_by_secretary 記録）が手順として生きている。恒久対応は未実装と本文が明記 | 12 |
| .dispatcher/references/worker-monitoring.md:712-723 | (b-fp) 型2 ultracode Workflow 待機の誤発火 | keep | 2026-07-28 方針変更の報告基準。grep -in 'Waiting for\\|dynamic workflow' tools/inspect_pane_state.py は 0 件で helper 未対応。本タスク自体も ultracode 武装中 | 12 |
| .dispatcher/references/worker-monitoring.md:724-743 | (b-4) 発火前の観測面チェック | keep | Issue #869（2026-08-08 STALL 誤検知 2 件） | 20 |
| .dispatcher/references/worker-monitoring.md:744-776 | (c)(1) events scan | keep | L754『worker_escalation は catalog 未掲載』は grep -n worker_escalation docs/journal-events.md 0 件で今も正しい | 33 |
| .dispatcher/references/worker-monitoring.md:777-793 | (c)(1-bis) decision-register gate | keep | pending_decisions.py:563 に latest-resolution サブコマンド実在。Issue #603 | 17 |
| .dispatcher/references/worker-monitoring.md:794-795 | (c)(2) poll_events forward-compat プレースホルダ | move | 『本 PR ではプレースホルダとして記述するに留める』将来案。現行手順への指示は無い。同趣旨が設計メモ L829 と 5.1(c)(2) L960 にもある<br>**検証**: 反証で維持: poll_events を将来使う場合のプレースホルダ（『本 PR ではプレースホルダ』）。『events scan を authoritative とする』は L829 と (c)(1) にもあるので、規範は失われない。 | 2 |
| .dispatcher/references/worker-monitoring.md:796-823 | (d) acked/timeout 分岐・(e) de-dup | keep | STALL_SUSPECTED 通知と kind=stall_acked/stall_suspected の producer | 28 |
| .dispatcher/references/worker-monitoring.md:824-827 | (f) 設計メモ 15/60 分の根拠・60 分超過 | move | 定数の根拠説明。Issue #304 仕様準拠の経緯<br>**検証**: 反証で維持: L825-827 は定数の根拠と long-tail の説明。実行手順の playbook は L828 で、範囲外なので残る。 | 4 |
| .dispatcher/references/worker-monitoring.md:828-828 | (f) ci_completed status=incomplete playbook | keep | dispatcher が gh pr checks を叩かない役割境界の手順。runtime PR #126/#127 で 2 連続観測。設計メモ内にあるので手順側へ移す余地あり | 1 |
| .dispatcher/references/worker-monitoring.md:829-839 | (f) 設計メモ残り・想定シナリオ | move | events scan primary 理由・register gate 理由・acceptance シナリオで、手順ではない<br>**検証**: 反証で維持: events scan を primary にした理由、soft-note、register gate と CLI に寄せた理由、acceptance シナリオ。どれも理由とテスト状況の説明。tests に pin 無し。 | 11 |
| .dispatcher/references/worker-monitoring.md:840-844 | 5.1 relay gap 見出し・定数 | keep | CLAUDE.md root『pending-decisions register』節が本 Step 5.1 を参照 | 5 |
| .dispatcher/references/worker-monitoring.md:845-878 | (a-0) pending-decisions register lookup | keep | pending_decisions.py:539 --older-than-min 実在。producer は org-escalation/SKILL.md の append/resolve。L847/L864/L875 に経緯文あり | 34 |
| .dispatcher/references/worker-monitoring.md:879-912 | (a-2) user_replied_at lookup | keep | pending_decisions.py:545 --user-replied-older-than-min 実在。Issue #301 | 34 |
| .dispatcher/references/worker-monitoring.md:913-919 | proxy 経路の (a-3) Fallback 格下げ経緯 | move | 経緯説明。『proxy 経路の最終削除は別 Issue』が L875・L918・L1023 の 3 か所で重複<br>**検証**: 反証で維持: proxy を fallback へ格下げした経緯と対象ケースの説明。fallback へ移る条件そのものは L911 にある。『最終削除は別 Issue』は L918 と L1023 で重複している。 | 7 |
| .dispatcher/references/worker-monitoring.md:920-928 | 5.1 (a) 動機・proxy 経路の歴史的スコープ | move | インシデントパターンと旧 PR #298 の歴史説明のみ<br>**検証**: 反証で維持: インシデントのパターンと PR #298 の歴史的スコープ。判定は (b) の L929 から始まる。 | 9 |
| .dispatcher/references/worker-monitoring.md:929-955 | 5.1 (b) relay gap 候補条件 | keep | proxy 経路の判定条件（a-3 fallback として現役）。sqlite3 クエリ | 27 |
| .dispatcher/references/worker-monitoring.md:956-963 | 5.1 (c) secretary→worker 観測不能と代替 | keep | 『常に true として扱う』という判定規則を含む。L960 はプレースホルダ | 8 |
| .dispatcher/references/worker-monitoring.md:964-989 | 5.1 (d) secretary pane 画面 diff | keep | secretary-pane-snapshot.txt / secretary-output-changes.jsonl の永続化契約。L988 は (P1) の適用 | 26 |
| .dispatcher/references/worker-monitoring.md:990-1003 | 5.1 (e) out-of-band 通知 (1)(2) | keep | ALERT 表示と secretary 宛 send_message の両系統 | 14 |
| .dispatcher/references/worker-monitoring.md:1004-1004 | 5.1 (e)(3) list_peers client_kind 直送の将来案 | move | 『Issue 化候補 (本 PR では実装しない)』の将来案で手順ではない<br>**検証**: 反証で維持: 『将来』『Issue 化候補』の案で、現行手順への指示は無い。 | 1 |
| .dispatcher/references/worker-monitoring.md:1005-1007 | 5.1 (e) 両方必ず発行 | keep | 規範 1 行 | 3 |
| .dispatcher/references/worker-monitoring.md:1008-1021 | 5.1 (f) de-dup と journal | keep | kind=relay_gap_suspected の producer | 14 |
| .dispatcher/references/worker-monitoring.md:1022-1031 | 5.1 (g)(h) 設計メモ | move | (g) は L875/L918 と同じ『register 化済み・proxy 削除は別 Issue』の 3 回目。(h) は Step5 との関係説明<br>**検証**: 反証で維持: (g) は (a-0) に着地したという経緯の 3 回目。(h) は Step5 との関係の説明で、L1030 の idle streak 条件は (b)(4) 本体にある。 | 10 |
| .dispatcher/references/worker-monitoring.md:1032-1036 | 5.2 見出し・定数 | keep | PANE_OUTPUT_SETTLE_SEC/IDLE_STREAK_MIN は .dispatcher/CLAUDE.md エントリ要約が引用 | 5 |
| .dispatcher/references/worker-monitoring.md:1037-1045 | 5.2 (a) 動機（2026-05-09 インシデント） | move | 事故経緯と既存検知器との比較説明。(g) L1171 にも同インシデントが重複<br>**検証**: 反証で維持: 2026-05-09 のインシデントと既存の検知器との比較だけ。判定は (b) の L1046 から始まる。 | 9 |
| .dispatcher/references/worker-monitoring.md:1046-1062 | 5.2 (b) fire 候補条件 (1)〜(6) | keep | completion_reported_at gate（Issue #658、実運用で 4 回再現: L1173）。runs.status は state.db runs テーブル | 17 |
| .dispatcher/references/worker-monitoring.md:1063-1096 | 5.2 (c) 2 段問い合わせ SQL | keep | events テーブルは M4 cutover (#267) で SoT（.dispatcher/CLAUDE.md の journal 注記） | 34 |
| .dispatcher/references/worker-monitoring.md:1097-1098 | task_id SQL 補間の安全性注記 | move | Codex round 1 Minor の設計判断記録。実行手順は変えない<br>**検証**: 反証で維持: Codex Minor で下した安全性判断の記録。『将来は helper 経由必須』も将来条件で、今のサイクルの作業は変わらない。 | 2 |
| .dispatcher/references/worker-monitoring.md:1099-1117 | 5.2 (d) 5 分岐 | keep | soft-note kind 群 (pane_output_*) の producer | 19 |
| .dispatcher/references/worker-monitoring.md:1118-1143 | 5.2 (e) worker nudge + secretary informational | keep | 両系統通知の文面と成功条件 | 26 |
| .dispatcher/references/worker-monitoring.md:1144-1157 | 5.2 (f) de-dup と journal | keep | kind=pane_output_without_peer_msg の producer | 14 |
| .dispatcher/references/worker-monitoring.md:1158-1180 | 5.2 (g) 設計メモ（定数根拠・Codex 対応・#658 gate 理由） | keep（当初 move） | 23 行の根拠説明。L1167 の『前サイクル last_check_ts』理由は knowledge/curated/dispatcher-monitoring.md:5-17 と同内容。規則本体は Step5 (b) 更新規則 L631-640 と 5.2(b) にある<br>**検証**: 反証で move→keep: L1174『完了報告後も有効なまま』の出現は本ファイルでこの 1 か所だけで、tests/test_completion_reported_flag.py:test_skip_scoped_to_pane_output_only が .md と .in の両方で assertIn している。移すとテストが落ちる。skip の範囲を限定する規範でもあるので L1174 は keep。L1176-1179 も規範を含むため、それ以外の根拠行だけを切り出すのが妥当。 | 23 |
| .dispatcher/references/worker-monitoring.md:1181-1196 | 5.25 relay scan --cycle と exit 分岐 | keep | tools/relay_scan.py:689 --cycle 実在。commit b49208e (#996)。2026-09-03 の 44.5 分停止 | 16 |
| .dispatcher/references/worker-monitoring.md:1197-1201 | --cycle に畳んだ理由・audit 順序・報告条件の理由 | move | 設計理由。relay_scan.py:51 docstring『Why --cycle exists』が同じ説明を持つ。不変条件 L1229-1230 とも重複<br>**検証**: 反証で維持: 『なぜ』の段落 3 つ（#955/#971、順序、報告条件）。手順の本体は L1188 の --cycle と exit の分岐。relay_scan.py docstring にも同じ説明がある。 | 5 |
| .dispatcher/references/worker-monitoring.md:1202-1206 | [] と出力無しを区別・heartbeat | keep | RELAY_SCAN_BROKEN 規則。2026-07-30〜08-19 の python 綴り事故（134 件滞留）、commit 58d8eda (#945) | 5 |
| .dispatcher/references/worker-monitoring.md:1207-1222 | 5.25 relay 送信と mark-delivered/mark-failed | keep | relay_scan.py:38,42,698 に --mark-delivered/--mark-failed 実在。org-pull-request の recv-model fragment が [relay] 受信を consume | 16 |
| .dispatcher/references/worker-monitoring.md:1223-1232 | 5.25 設計上の不変条件 | keep | L1229『2 本に割ってはならない』等 MUST NOT を含む。説明部分は move 余地あり（判断事項） | 10 |
| .dispatcher/references/worker-monitoring.md:1233-1277 | 5.3 curate-inflight 見出し・close 判定表 | keep | .dispatcher/CLAUDE.md『オンデマンド curator』節と pane-close.md:229,275 が #step-5-3-close を参照。契約 T-§4.2 / T-§cap | 45 |
| .dispatcher/references/worker-monitoring.md:1278-1318 | 5.3 (a) 完了受領 / (c) 消失 / (b) timeout・hard cap | keep | CURATE_TIMEOUT_MIN 20 / HARD_CAP 40 は .dispatcher/CLAUDE.md と同値。kind=curate_close_failed/curate_stall_truncated/curate_close_unresolved の producer | 41 |
| .dispatcher/references/worker-monitoring.md:1319-1320 | 6. 自動で承認・拒否しない | delete | 同一ファイル L81 (P5) が同じ禁止を .dispatcher/CLAUDE.md 引用付きで既に書いている。grep -n '自動で承認・拒否することはしない' のヒット: .dispatcher/CLAUDE.md:171、本ファイル L81/L1121/L1319<br>**補足**: 正本: .dispatcher/CLAUDE.md:171（常時読込）と本ファイル L81<br>**検証**: 反証で維持: .dispatcher/CLAUDE.md:171 が同じ強さの同文で、常時読み込まれる。tests に pin 無し。ただし L81 が『本ファイル 6』を参照しているので、削除と同時に L81 の参照先を CLAUDE.md へ直すこと。 | 2 |
| .dispatcher/references/worker-monitoring.md:1321-1336 | 7. ループ停止条件と --mark-halted | keep | relay_scan.py:49 --mark-halted 実在。Issue #955、b49208e | 16 |
| .dispatcher/references/worker-monitoring.md:1337-1338 | 監視対象ペイン名の取得元 | outdated | `.state/workers/worker-{peer_id}.md` と書くが、state ファイルは task_id 単位（tools/check_state_drift.py:30 `.state/workers/worker-{task_id}.md`、本ファイル L194/L301 も worker-{task_id}.md）。同文が .dispatcher/CLAUDE.md:173 にも重複<br>**補足**: .state/workers/worker-{task_id}.md の Pane Name (worker-{task_id})。.dispatcher/CLAUDE.md:173 の同文は削除して本ファイル 1 か所に寄せる<br>**検証**: 反証で維持: state ファイルは task_id 単位。根拠は tools/check_state_drift.py:30、org-delegate/SKILL.md:333 の worker-{task_id}.md。同じ誤りが .dispatcher/CLAUDE.md:173 と pane-close.md:95 にもある。 | 2 |
| .dispatcher/references/worker-monitoring.md:1339-1350 | 設計メモ | move | #869 の配置理由・cursor/inspect/regex の設計理由。L1341/L1342 は L15/L82 と、L1345 は L135 と重複<br>**検証**: 反証で維持: 設計の理由が並ぶ節。L1350 の『code で分岐』は renga-error-codes.md:3,16 が正本で、cursor のファイル位置は Step1 の L106-135 にある。手順は失われない。 | 12 |
| .dispatcher/references/worker-monitoring.md:1351-1359 | cadence 設定の根拠（2026-05-02 検証） | move | /loop 3m の根拠データ。L1355 の journal.jsonl は M4 (#267) で廃止（.dispatcher/CLAUDE.md の journal 注記）、『Foreman Claude』は旧名。手順ではない<br>**検証**: 反証で維持: /loop 3m の根拠データ。journal.jsonl は M4 で廃止済み（docs/journal-events.md:3-4）。『Foreman』のヒットは本ファイルと .in だけ。手順の指示は無い。 | 9 |

### .dispatcher/references/pane-close.md（714 行: keep 617 / move 37 / delete 52 / outdated 7 / transport 1）

| 行範囲 | 節 | 分類 | 根拠 | 行数 |
|---|---|---|---|---|
| .dispatcher/references/pane-close.md:1-4 | 冒頭（タイトル・本体仕様の位置づけ） | keep | .dispatcher/CLAUDE.md:202-210 のエントリポイントが本ファイルを参照（git grep pane-close.md で .dispatcher/CLAUDE.md・worker-monitoring.md から多数ヒット）。 | 4 |
| .dispatcher/references/pane-close.md:5-5 | 輸送層 両系注記（既定 renga / broker 置換 / 二フレーム注記 Refs #604 / Epic #6 Issue G） | transport | renga/broker の読み替え注記と二フレーム注記の全文。root CLAUDE.md・.dispatcher/CLAUDE.md:19-21 に同文がある。本ファイル内は mcp__renga-peers__* で書かれているが、生成物の worker-monitoring.md:1239 は mcp__org-broker__list_panes なのでフレームが食い違う。 | 1 |
| .dispatcher/references/pane-close.md:6-12 | ペインクローズ: 振り返り完了前に閉じない原則 | keep | docs/contracts/role-contract.md:132 の CLOSE_PANE discipline と一致。retro_deferred の consumer は tools/state_db/queries.py:458、docs/journal-events.md:100。 | 7 |
| .dispatcher/references/pane-close.md:13-20 | 1. 振り返り / 完了報告ゲート導入 | keep | tools/dispatcher_retro_gate.py が現存（--attempt/--max-attempts 10 = :23,:310）。consumer は本節と .dispatcher/CLAUDE.md:208。Issue #285 の 1 行は短い理由づけなので残す。 | 8 |
| .dispatcher/references/pane-close.md:21-40 | 0. 受領記録の確認（completion_reported_at / runs.status） | keep | e51fd79 2026-09-23『CLOSE_PANE の重複問い合わせ…を止める』(#1024)。producer は worker-monitoring.md:219-223（WORKER_COMPLETION_NOTED で set）。直近の再発記録あり（:35 に 2026-09-23 の 4 task、:36 に Issue #869）。 | 20 |
| .dispatcher/references/pane-close.md:41-55 | 1. 初回送信（--print-initial-prompt） | keep | dispatcher_retro_gate.py:314 に --print-initial-prompt あり。 | 15 |
| .dispatcher/references/pane-close.md:56-75 | 2. polling ループと exit code 分岐 | keep | exit code 0/1/2/3/4 が dispatcher_retro_gate.py:185,285,196,278,296 の return と一致（3=replied_no_ack :46）。 | 20 |
| .dispatcher/references/pane-close.md:76-76 | 理由（実インシデント） | move | 設計理由と事故の経緯。出典 knowledge/raw/2026-05-03-delegation-smoke-completion-report.md は不在（knowledge/raw/* は .gitignore:16 で除外、ja-main の raw は 0 件）。近い記録は knowledge/curated/dispatcher-monitoring.md:67-90。<br>**検証**: 反証で維持: 理由説明のみ。手順は直後の :78-85 fallback と gate 手順にある。参照先 knowledge/raw/2026-05-03-delegation-smoke-completion-report.md は git grep で本行以外ヒット 0。tests/tools からの参照も無い（git grep pane-close -- tests はヒット 0）。 | 1 |
| .dispatcher/references/pane-close.md:77-84 | secretary unreachable 時の fallback（retro_deferred・閉じない） | keep | tools/journal_append.sh が現存。retro_deferred は docs/journal-events.md:100 と queries.py:458 に定義あり。 | 8 |
| .dispatcher/references/pane-close.md:85-86 | fallback 3: /org-resume で再試行 | outdated | git grep -n retro_deferred の結果、skills 配下は 0 件。.claude/skills/org-resume/SKILL.md に retro / CLOSE_PANE を再試行する手順は無く、consumer が存在しない。<br>**補足**: 再試行の契機は現状どこにも実装されていない。.dispatcher/CLAUDE.md:210 も同じ主張。org-resume に手順を足すか、記述を『窓口が CLOSE_PANE を再送する』（本ファイル :179-181）へ寄せる。<br>**検証**: 反証で維持: org-resume/SKILL.md 全 118 行を読んだが、retro_deferred・CLOSE_PANE の再試行手順は無い（git grep retro_deferred の結果は .dispatcher/CLAUDE.md:210, pane-close.md:80-82, docs, queries.py:458, curated だけ）。『保留扱い』の部分は今も有効なので、修正は『再試行の契機』の文言に限る。 | 2 |
| .dispatcher/references/pane-close.md:87-93 | 観点 | keep | retro の観点リスト。org-retro 相当の手順本体。 | 7 |
| .dispatcher/references/pane-close.md:94-98 | 情報収集（worker-{peer_id}.md） | outdated | 状態ファイルの名前は worker-{task_id}.md で、runtime の dispatcher/runner.py:3162 が `workers / f"worker-{task_id}.md"` と書く。.dispatcher/CLAUDE.md の helper 節も worker-{task_id}.md。<br>**補足**: `.state/workers/worker-{task_id}.md` が正しい（claude_org_runtime/dispatcher/runner.py:3162）。.dispatcher/CLAUDE.md:173 と worker-monitoring.md:1337（.in:1333）も同じ誤り。<br>**検証**: 反証で維持: runtime 0.1.49 の dispatcher/runner.py:3162 は state_dir/'workers'/f"worker-{task_id}.md"、:3168 は Pane Name: worker-{task_id}。正しくは .state/workers/worker-{task_id}.md。worker-{peer_id} は .dispatcher/CLAUDE.md:173 と worker-monitoring.md:1337(.in:1333) にも残る。runtime の prompts/templates/dispatcher.md:612,660 にも同じ誤りがある。 | 5 |
| .dispatcher/references/pane-close.md:99-105 | 2. 知見の記録 | keep | .claude/skills/org-curate/references/knowledge-standards.md が現存。consumer は check_curate_threshold.py（raw の件数を数える）。 | 7 |
| .dispatcher/references/pane-close.md:106-111 | 3. ペインを閉じる: 数値 pane_id 原則・pane_id の出所 | keep | 59cf3af 2026-08-09 で Group B を数値 pane_id へ移行し MUST 化（#896）。正本は契約 backend-interface-contract.md の T-§4.2。 | 6 |
| .dispatcher/references/pane-close.md:112-116 | 自タブ確立の 2 手段 | keep | worker-monitoring.md:1240-1247 と .dispatcher/CLAUDE.md の『オンデマンド curator』節が『Step 3 冒頭の 2 手段』として本節を参照しているので、正本は本節。 | 5 |
| .dispatcher/references/pane-close.md:117-117 | same_tab を確立手段に数えない理由 | move | 契約の解釈を述べる設計理由。規則そのものは :112 の『確立手段は次の 2 つだけ』で足りる。同趣旨が worker-monitoring.md:1246 と .dispatcher/CLAUDE.md:224-229 にもある。<br>**検証**: 反証で維持: 規則本体は :112『確立手段は次の 2 つだけ』で決まり、:117 は契約解釈の説明。worker-monitoring.md:1246 にほぼ同文がある。否定方向の使い方（same_tab==False に pane_id を割り当てない）は org-suspend Phase 1 が正本。tests/tools の same_tab ヒットは brief の placement 用で、本文の照合ではない。 | 1 |
| .dispatcher/references/pane-close.md:118-120 | 2 手段とも不成立なら close しない | keep | 手順として効いている規則（契約 T-§4.2 の MUST、59cf3af）。 | 3 |
| .dispatcher/references/pane-close.md:121-121 | 2 つ目は確立できたときだけ数える（事実上 broker のみ） | move | T-§cap の fail-safe default から導いた帰結の説明。実運用は ORG_TRANSPORT=renga（printenv の出力）なので、運用上の影響は judgments に挙げる。<br>**検証**: 反証で維持: T-§cap の fail-safe から導いた帰結の説明。『撃たずに保留へ』という行動は :119 にすでに書かれている。worker-monitoring.md:1248 にも同じ帰結がある。消してもテストは壊れない（tests/tools に該当語なし）。 | 1 |
| .dispatcher/references/pane-close.md:122-140 | close 直前の identity 照合 1-4 と close 呼び出し | keep | fc9ae42 2026-08-08（観測不能と不在の区別、#870）、59cf3af 2026-08-09。worker-monitoring.md の 3-a / observation-principle（:11）が現存。 | 19 |
| .dispatcher/references/pane-close.md:141-141 | 成功時の renga 内部挙動（Event::PaneExited emit） | move | renga の実装の詳細（exit_event_emitted ガード）の説明で、手順としては使わない。exactly-once の正本は契約 §3.1（worker-monitoring.md:193 が引用）。<br>**検証**: 反証で維持: Event::PaneExited / exit_event_emitted は renga 内部挙動の説明で、dispatcher の操作ではない。git grep -e PaneExited -e exit_event_emitted -- tests tools .hooks scripts はヒット 0。 | 1 |
| .dispatcher/references/pane-close.md:142-145 | エラーコード分岐 pane_not_found / last_pane | keep | .claude/skills/org-delegate/references/renga-error-codes.md が現存。 | 4 |
| .dispatcher/references/pane-close.md:146-170 | 保留の扱い (a) 記帳 | keep | anomaly_observed は docs/journal-events.md:204 に定義済み。保留専用の state ファイルを作らない方針は .dispatcher/CLAUDE.md:192 の『監視 gap を埋める内部状態ファイル』と整合する。 | 25 |
| .dispatcher/references/pane-close.md:171-188 | 保留 (b) 再試行契機 | keep | worker-monitoring.md Step 3 (3-a) と停止条件 7 が現存。窓口が再送する契機は手順として効いている。:182-187 は理由説明が長いので縮約の余地がある（推測）。 | 18 |
| .dispatcher/references/pane-close.md:189-205 | 保留 (c) 後続 Step も実行する | keep | check_curate_threshold.py はファイル数だけを数える（counting rules）。--free-panes は work_discovery_scan.py:123 に現存。 | 17 |
| .dispatcher/references/pane-close.md:206-215 | 4. 窓口への報告（RETRO_RECORDED） | keep | .dispatcher/CLAUDE.md:208 の要約と対応する手順本体。 | 10 |
| .dispatcher/references/pane-close.md:216-233 | 5. curate 閾値チェック導入・実行コンテキスト | keep | org-start/SKILL.md:7,37 に『キュレーターは起動しない』、worker-monitoring.md:1233 に Step 5.3 が現存。 | 18 |
| .dispatcher/references/pane-close.md:234-239 | starvation の既知の限界（Issue #501/#502） | move | 既知の限界とバックログの説明で手順ではない。同じ記述が docs/contracts/role-contract.md:285 と org-start/SKILL.md:44 にもある。org-retro に check_curate_threshold を呼ぶ箇所は無い（git grep の結果）ので、未実装のまま。<br>**検証**: 反証で維持: 既知の限界とバックログ（#501/#502）の説明で、行動を求めていない。role-contract.md:285 と org-start/SKILL.md:44 にも言及がある。org-retro に check_curate_threshold は無い（git grep -- .claude/skills/org-retro がヒット 0）ので未実装のまま。 | 6 |
| .dispatcher/references/pane-close.md:240-254 | 5-1. 閾値チェックスクリプトの実行 | keep | check_curate_threshold.py:112-114 の EXIT 0/10/2 と一致。 | 15 |
| .dispatcher/references/pane-close.md:255-272 | 5-2. single-flight 確認（coalesce）分岐 | keep | curate-inflight.json の producer は本ファイル 5-3、consumer は worker-monitoring.md:1234。 | 18 |
| .dispatcher/references/pane-close.md:273-282 | live curator + inflight 無しが起きうる経路の説明 | move | 到達経路を説明する設計理由。手順は :283 以降の 1-3 にある。<br>**検証**: 反証で維持: 到達経路の説明。実行手順は :283-309 の 1-3（再取得・報告・監視ループへ委譲）にそろっている。:273 の太字見出し（無報告で通さない）だけは手順の導入として残すとよい。 | 10 |
| .dispatcher/references/pane-close.md:283-310 | inflight の再取得と窓口報告 | keep | dispatcher-resume Step 5 の再生成と揃える旨の記述あり（.claude/skills/dispatcher-resume/SKILL.md が現存）。 | 28 |
| .dispatcher/references/pane-close.md:311-325 | 5-3. curator spawn 呼び出し・cwd | keep | spawn_claude_pane は MCP ツール。consumer は org-curate/SKILL.md（.curator cwd）。 | 15 |
| .dispatcher/references/pane-close.md:326-326 | model=sonnet の理由 | move | 設計理由（38d3a25 2026-07-05、Closes #499）。手順は :321 の model="sonnet" だけで足りる。<br>**検証**: 反証で維持: model=sonnet の選定理由（公式記事の引用）。:321 の spawn 引数 model="sonnet" だけで手順は足りる。テストからの参照は無い。 | 1 |
| .dispatcher/references/pane-close.md:327-345 | [name_in_use] の切り分け 1-3 | keep | 59cf3af 2026-08-09 で stale-binding の分岐を導入。carve-out の 3 手順（pr-watch-pane / org-pull-request）は契約側に記録されている。 | 19 |
| .dispatcher/references/pane-close.md:346-350 | close 判定表に足す行ではない旨 | move | 判定表との関係を述べる設計メモで、手順としての作用は無い。<br>**検証**: 反証で維持: 判定表と競合しないことを述べる設計メモ。行動は :337-345（close を撃たず報告して Step 6 へ）で完結している。 | 5 |
| .dispatcher/references/pane-close.md:351-357 | その他エラー / spawn 直後の inflight 書き込み | keep | worker-monitoring.md:1234 が『spawn 直後に書いた』ことを前提にしている。 | 7 |
| .dispatcher/references/pane-close.md:358-375 | started_at は決定的 UTC コマンド | keep | 0cc0a48 2026-06-14『オンデマンド curator 孤立の2バグを決定的 UTC 化…』(Closes #568) の実事故がある。理由説明の :360-366 は rationale へ移せる。 | 18 |
| .dispatcher/references/pane-close.md:376-403 | inflight JSON 形式と pane_id の型注記 | keep | consumer は worker-monitoring.md:1239（inflight の pane_id で照合）。型の根拠は runtime の broker_queue_event.schema（org-attach:106-109 で引用）。 | 28 |
| .dispatcher/references/pane-close.md:404-422 | pane_id は非同期 close の起点（判定表の要約） | delete | 内容は判定表の行 1-8 の再掲で、:421 自身が『8 行の判定の正本は上記 Step 5.3 の close 判定表』と書いている。worker-monitoring.md:1238-1279 に同じ規則があり、.dispatcher/CLAUDE.md:218-240 にも 3 つ目の写しがある。<br>**補足**: 正本は .dispatcher/references/worker-monitoring.md:1238-1279（.in 側で編集）の共通 close 判定表。ここは 1 行の参照に置き換える。<br>**検証**: 反証で維持: worker-monitoring.md:1239-1282 と突き合わせた。行 1/8 の保持、行 3 の報告、行 5 の残存ファイル close、無条件削除の禁止、裏取りゲートの要件はすべて表の行と付帯条項（:1254-1261, :1265, :1282）にある。:421 自身も正本は表だと書いている。pane_id を控える理由は表の preamble :1239 にもある。 | 19 |
| .dispatcher/references/pane-close.md:423-439 | curate_result の意味 / 保持される状態ファイル / state.db に書かない | keep | worker-monitoring.md に curate_result が 6 か所（grep -c）。org-start/SKILL.md:41-42 に curator_pane_id を CLEAR、null が正常系とある。 | 17 |
| .dispatcher/references/pane-close.md:440-468 | 5-4. boot 確認 1-2（画面判定・list_peers・send-as-probe） | keep | org-start Block D（SKILL.md:349）と spawn-flow 3-3b が現存。盲目 Enter 事故は 2026-09-25（runtime 0.1.43、.dispatcher/CLAUDE.md の出力の扱い）。capability gate reference は §3-B（:346）にある。 | 29 |
| .dispatcher/references/pane-close.md:469-480 | capability 形かつ承認済みの枝 | keep（当初 move） | :474 自身が『旧版 fallback（現行配備の全 backend）…今日の挙動は変わらない』と書いており、今日は通らない枝。判定の正本は capability-first-drive-operational-gate.md §1-2（:80）。<br>**検証**: 反証で move→keep: 『今日は通らない枝』は未確認。renga の list_peers の実出力には『[your tab]』のタブ情報が付く。brief テンプレ tools/templates/worker_brief_normal.md:226 も same_tab: true 候補を前提にしている。renga が same_tab/tab を広告していればこの枝は現役。休眠中でも条件付きの手順なので rationale へは移さない。 | 12 |
| .dispatcher/references/pane-close.md:481-495 | 3. 登録されない場合の破棄（判定表に委ねる・数値 id・自タブ確立） | keep | 59cf3af 2026-08-09。手順の骨子は判定表への委譲と spawn 戻り値による照合。 | 15 |
| .dispatcher/references/pane-close.md:496-513 | give-up 分岐での判定表各行（行 3/4/7/8）の再掲 | delete | 行 3/4/7/8 の扱いは worker-monitoring.md:1255-1264 の表の該当行と同じ内容（sed で確認）。:484 自身が『この give-up 分岐だけの別ルールを足さない』と書いている。<br>**補足**: 正本は worker-monitoring.md:1238-1279 の close 判定表。行 2 が主経路になる旨の 1 文だけ残す。<br>**検証**: 反証で維持: 行 3/4/7/8 の扱いは worker-monitoring.md:1256-1261 と一致する。行 4 の『本ハンドラでは裏取りを完走できない』は表の行 1（裏取り未確定は保持）の帰結になる。:484 自身が別ルールを足さないと書いている。行 2 が主経路である旨の 1 文と、自タブ確立が前段に来る旨（:490-494）は残す。 | 18 |
| .dispatcher/references/pane-close.md:514-536 | 5-5 起動指示 / 5-6 監視ループへの即時復帰 | keep | CURATE_DONE などの受け口は worker-monitoring.md Step 5.3 (a)（:1280 付近）。 | 23 |
| .dispatcher/references/pane-close.md:537-551 | 5-7. 完了受領・timeout・クローズ（監視ループ側の責務） | delete | 監視ループ側の責務の再要約。worker-monitoring.md:1233-1290 と .dispatcher/CLAUDE.md:212-257（オンデマンド curator 節）に同じ内容があり、:549 自身が『本 CLOSE_PANE ハンドラ側に残る作業は無い』と書いている。<br>**補足**: 正本は worker-monitoring.md Step 5.3。ここは『以降は監視ループ Step 5.3 が担う』の 1 行にする。<br>**検証**: 反証で維持: :549 が『本ハンドラ側に残る作業は無い』と明言している。列挙した責務（受領・20 分 timeout・照合 close・保持・hard cap）はすべて worker-monitoring.md:1234-1292 に正本として書かれている。見出しと 1 行の参照だけ残せばよい。 | 15 |
| .dispatcher/references/pane-close.md:552-559 | 6. triage scan 導入 | keep | tools/work_discovery_scan.py が現存。docs/design/work-discovery-triage.md も現存。 | 8 |
| .dispatcher/references/pane-close.md:560-569 | 不変条件 INV-1/2/4/5 | keep | 実行する場所はこのスキル本体なので正本とする。.dispatcher/CLAUDE.md:272-276 が重複している（judgments に挙げる）。 | 10 |
| .dispatcher/references/pane-close.md:570-586 | 6-1. scan 実行コマンド | keep | work_discovery_scan.py:25 に --all-registry-repos あり。7bda7af 2026-08-06 (#832)。 | 17 |
| .dispatcher/references/pane-close.md:587-592 | 旧 REPO_FLAGS 手順を使わない理由（zsh 単語分割） | move | 事故の経緯（Issue #829、7bda7af 2026-08-06）。同じ説明が work_discovery_repos.py:34-45 の docstring にある。手順は 1 コマンド形だけで足りる。<br>**検証**: 反証で維持: Issue #829 の事故経緯。手順は :582-584 の 1 コマンド形で足りる。tests/test_work_discovery_scan.py:2291 の REPO_FLAGS は docstring で、この文書は読まない。旧手順を禁じる短い 1 文は残してもよい。 | 6 |
| .dispatcher/references/pane-close.md:593-619 | 解決失敗 / 監査情報 / timeout 300000 / api.anthropic.com / free-panes | keep | 4a52d6f 2026-09-30（goal-driven、#1028）で判定段 claude -p を導入。--free-panes は scan.py:123。 | 27 |
| .dispatcher/references/pane-close.md:620-647 | 6-2. exit code 分岐と dedup | keep | work_discovery_dedup.py:12（3=unchanged）、:75（return 2）。e51fd79 2026-09-23（同一 3 件を 7 回再送した事故）。 | 28 |
| .dispatcher/references/pane-close.md:648-683 | 6-3. 監査ログ記帳と recommendation_ref | keep | dedup.py:54 が events の candidate_refs を読むので consumer がある。 | 36 |
| .dispatcher/references/pane-close.md:684-688 | イベント名の台帳定義は別タスク | move | 作業当時のスコープ注記（『本タスク』）。git grep work_discovery_scanned docs/journal-events.md は 0 件で、今も未定義。<br>**検証**: 反証で維持: 作業当時のスコープ注記。git grep work_discovery_scanned -- docs の結果は docs/design/work-discovery-triage.md だけで、docs/journal-events.md には無い。journal_append が free-form で受けるので、手順への影響は無い。 | 5 |
| .dispatcher/references/pane-close.md:689-714 | 6-4. 窓口への転送 / エラー通知 | keep | Issue #829（7bda7af）。送信先 secretary の規約は .dispatcher/CLAUDE.md:132。 | 26 |

### .claude/skills/org-down/SKILL.md（196 行: keep 137 / move 12 / delete 0 / outdated 0 / transport 47）

| 行範囲 | 節 | 分類 | 根拠 | 行数 |
|---|---|---|---|---|
| .claude/skills/org-down/SKILL.md:1-9 | frontmatter（name/description/effort） | keep | e00ee96 2026-07-16 で新設（#711）。.in は無い（ls で確認）。 | 9 |
| .claude/skills/org-down/SKILL.md:10-33 | allowed-tools（mcp__org-broker__* のみ） | transport | MCP ツールが broker 名だけで、実運用の renga（printenv ORG_TRANSPORT=renga）の mcp__renga-peers__* が無い。あわせて Bash(python -m tools.state_db.importer) は、POSIX では python が存在しない（`which python` → not found）。 | 24 |
| .claude/skills/org-down/SKILL.md:34-46 | 概要・責務境界 | keep | `claude-org-runtime org down` は runtime cli.py:143（'down' stops the daemon）にある。org-suspend Phase 3.5 の注記（org-suspend:467-469）が本スキルを相互参照している。 | 13 |
| .claude/skills/org-down/SKILL.md:47-50 | runtime#142 pre-flight は担当外 | move | 担当境界の経緯説明で、手順としての作用は無い。<br>**検証**: 反証で維持: 担当境界の経緯説明。runtime の org down を呼ぶだけという点は Step 3 の手順で表されている。tests/tools に runtime#142 への参照は無い。 | 4 |
| .claude/skills/org-down/SKILL.md:51-56 | 輸送層の前提（broker のみ意味を持つ / renga では skip） | transport | broker と renga の二フレーム記述。:54 に誤植『jud断』がある。Step 3 の 1（:149-156）と内容が重なる。 | 6 |
| .claude/skills/org-down/SKILL.md:57-95 | Step 1: /org-suspend 完走と suspend gate | keep | b08c997 2026-08-09（org-down gate の二段化、#892）。参照先の org-suspend Phase 3.6/3.7（:471,:521）、Phase 4 の手順 7（:765）、F-1（:170）、最後のペイン（:745）がすべて現存。 | 39 |
| .claude/skills/org-down/SKILL.md:96-103 | 判定不能は STOP に倒す理由 | move | worker 監視との向きの違いを説明する設計理由。規則は :89-94 に書かれている。<br>**検証**: 反証で維持: STOP 規則そのものは :89-94 にある。:96-102 は worker 監視と向きが違う理由の説明で、新しい条件は足していない。tests の『判定不能』ヒットは hook のテストで、この文書とは無関係。 | 8 |
| .claude/skills/org-down/SKILL.md:104-144 | Step 2: dashboard の stale-pid-safe 停止 | keep | tools/stop_dashboard.py が現存。org-suspend:461-469 の blind kill を置き換える点も相互参照あり。 | 41 |
| .claude/skills/org-down/SKILL.md:145-148 | Step 3 見出し・gate 条件 | keep | gate は Step 1 で定義済み。 | 4 |
| .claude/skills/org-down/SKILL.md:149-156 | transport 確認（renga なら skip） | transport | `echo ${ORG_TRANSPORT:-broker}` による broker / renga の分岐。実運用は renga なので、運用上はこちらが主経路になる。 | 8 |
| .claude/skills/org-down/SKILL.md:157-184 | org down 実行・注・Step 4 broker 報告 | keep | runtime 0.1.49 の cli.py:143 に down があり、docs/getting-started.md:84,215 と一致。 | 28 |
| .claude/skills/org-down/SKILL.md:185-193 | renga フォールバック時の報告 | transport | renga 側の報告文。secretary_queue_watcher が broker 専用である点は org-suspend:471『broker のみ』と一致。 | 9 |
| .claude/skills/org-down/SKILL.md:194-196 | gate 不成立時の報告 | keep | Step 1 の STOP 分岐と対応。 | 3 |

### .claude/skills/org-resume/SKILL.md（118 行: keep 74 / move 0 / delete 0 / outdated 44 / transport 0）

| 行範囲 | 節 | 分類 | 根拠 | 行数 |
|---|---|---|---|---|
| .claude/skills/org-resume/SKILL.md:1-7 | frontmatter（name/description/effort） | keep | 最終更新は 96293f4 2026-05-14。.in は無い。 | 7 |
| .claude/skills/org-resume/SKILL.md:8-17 | allowed-tools | outdated | POSIX には python が無い（`which python` → python not found、/usr/bin/python3 のみ）。本文の python -c（:44, :94）と python3 dashboard/org_state_converter.py（:109）が許可リストに無い。<br>**補足**: POSIX は python3、Windows は py -3 にする。本文で使う Bash（python3 -c …、dashboard/org_state_converter.py）も許可に揃える。<br>**検証**: 反証で維持: which python → python not found、which python3 → /usr/bin/python3。:16 の python -m importer は POSIX で動かない。本文の python3/python -c（:44, :94）と dashboard/org_state_converter.py（:108-109）は許可リストに無い。 | 10 |
| .claude/skills/org-resume/SKILL.md:18-21 | タイトル・概要 | keep | スキル本体の導入。 | 4 |
| .claude/skills/org-resume/SKILL.md:22-30 | state DB 前提（Issue #267 / M4） | keep（当初 move） | M4 移行の経緯（43a281b 2026-05-05）。:29 の rebuild コマンドは python -m で、POSIX では python が無いため動かない（`which python` → not found）。importer の --rebuild/--no-strict は importer.py:692,697 に現存。<br>**検証**: 反証で move→keep: 経緯の注記に現役の制約が混ざっている。構造化セクションの書き込みは StateWriter.transaction 経由、markdown の直接編集は禁止（drift_check が検出）、という点。:29 の rebuild コマンドも手順（python 綴りは which python → not found なので python3 に直す）。丸ごと move すると制約が手順面から消える。 | 9 |
| .claude/skills/org-resume/SKILL.md:31-40 | Phase 1-0 state schema migration | keep | tools/state_migrate.py の return 0/2/1（:127,154,164）が exit 0/2/1 の分岐と一致。 | 10 |
| .claude/skills/org-resume/SKILL.md:41-53 | Phase 1-1 DB から briefing 取得（python -c） | outdated | get_resume_briefing_light は queries.py:718 に現存し、キーも一致（:35-43）。ただし起動コマンドが python -c で、POSIX では `which python` → not found。.dispatcher/CLAUDE.md:157 に python 綴りで relay が 20 日空振りした実事故の記録がある。<br>**補足**: POSIX は python3 -c …、Windows は py -3 -c …（.dispatcher/CLAUDE.md:157 の relay_scan 事故と同じ類型）。:52-53 の rebuild 案内も python3 -m tools.state_db.importer にする。<br>**検証**: 反証で維持: get_resume_briefing_light は現存する。:44 の python -c と :53 の python -m は POSIX で not found になる（which python）。ただし根拠に引かれた .dispatcher/CLAUDE.md:157 の relay 空振り事故は grep で確認できなかった（同ファイルの relay_scan は :165,172 で python3 綴り）。outdated 判定自体は which の結果だけで成り立つ。 | 13 |
| .claude/skills/org-resume/SKILL.md:54-59 | Phase 1-2/3 サマリー提示・notes/ | keep | notes/ は ja-main に実在（ls で確認）。 | 6 |
| .claude/skills/org-resume/SKILL.md:60-73 | Phase 2 現実との照合 | keep | Worker Directory Registry は snapshotter が今も生成している（test_snapshotter.py:85）。.state/workers/ は runtime runner.py:3162 が書く現行経路なので、:65 の『旧経路』表記だけ不正確。 | 14 |
| .claude/skills/org-resume/SKILL.md:74-84 | Phase 3 再開計画の提案・人間確認 | keep | 人間ゲートの規則。CLAUDE.md『役割の境界』と整合する。 | 11 |
| .claude/skills/org-resume/SKILL.md:85-90 | Phase 4-1/2 ワーカー派遣 | keep | /org-delegate が現存。 | 6 |
| .claude/skills/org-resume/SKILL.md:91-104 | Phase 4-3 DB に Status/Resumed を書く（python -c） | outdated | StateWriter.__init__ と update_session は writer.py:53,289 に現存。起動コマンドが python で、POSIX では not found。<br>**補足**: python3 -c（POSIX）/ py -3 -c（Windows）。<br>**検証**: 反証で維持: :94 の python -c は POSIX で not found（which python）。StateWriter / update_session は現存するので、直すのは起動コマンドだけ（python3 / py -3）。 | 14 |
| .claude/skills/org-resume/SKILL.md:105-111 | Phase 4-4 JSON スナップショット再生成 | keep | dashboard/org_state_converter.py が現存し、org-start と spawn-flow.md からも参照されている（git grep -l）。 | 7 |
| .claude/skills/org-resume/SKILL.md:112-112 | Phase 4-5 ディスパッチャー・キュレーター起動は /org-start | outdated | org-start/SKILL.md:7,37 は『キュレーターは起動しない（オンデマンド化）』と書いている。<br>**補足**: /org-start が起動するのはディスパッチャーだけ。キュレーターは CLOSE_PANE Step 5 でのオンデマンド起動（pane-close.md:216-）。<br>**検証**: 反証で維持: org-start/SKILL.md:7 と :37-40 は『キュレーターは起動しない（オンデマンド化）』と書き、pane-close.md Step 5 のオンデマンド起動を指している。/org-start が起動するのはディスパッチャーだけ。 | 1 |
| .claude/skills/org-resume/SKILL.md:113-118 | Phase 4-6 resume イベント記帳 | outdated | payload が resumed_items だが、docs/journal-events.md:451 では resume の payload は `restored_workers[]`, `note`。journal_append.py の --json は :130 に現存。<br>**補足**: payload キーを docs/journal-events.md:451 の restored_workers[] / note に揃える（または台帳側を改訂する）。<br>**検証**: 反証で維持: docs/journal-events.md:451 の resume payload は restored_workers[], note で、:116 の例 resumed_items と食い違う。tools/state_db/queries.py:497 の resume 必須キーは () なので、実行時エラーにはならず台帳とずれるだけ。 | 6 |

### .claude/skills/org-attach/SKILL.md（280 行: keep 245 / move 0 / delete 0 / outdated 0 / transport 35）

| 行範囲 | 節 | 分類 | 根拠 | 行数 |
|---|---|---|---|---|
| .claude/skills/org-attach/SKILL.md:1-23 | frontmatter | keep | 42e1b5a 2026-06-17 で新設。.in は無い。broker 専用スキルなので org-broker ツール名で正しい。 | 23 |
| .claude/skills/org-attach/SKILL.md:24-32 | タイトル・概要（read-only） | keep | スキル本体の導入。 | 9 |
| .claude/skills/org-attach/SKILL.md:33-54 | 輸送層 両系（frame C）— broker(tmux) 専用 / renga では適用外 | transport | broker と renga のフレーム説明。:44 の SINGLE-TAB MUST、:47 の renga src/app/org_sidebar.rs（外部リポジトリなので未確認）、:51 の dispatcher-view.md（:55 の §4 に現存）を参照している。 | 22 |
| .claude/skills/org-attach/SKILL.md:55-67 | 既定の二フレーム注記（Refs #604） | transport | 二フレーム注記。org-attention-stop:35（既定 broker）と org-attention-start:31（既定 renga）の書き方の違いを説明している。 | 13 |
| .claude/skills/org-attach/SKILL.md:68-71 | 権限フレーム（list_panes は ops-tier） | keep | runtime の broker/surface.py:415-429 に _OPS_TOOLS と _OPS_TIERS={dispatcher, secretary} がある。 | 4 |
| .claude/skills/org-attach/SKILL.md:72-78 | /usr/bin/tmux 絶対パス必須 | keep | 生成するコマンドすべてに効く制約。理由は 1 段落で短い。 | 7 |
| .claude/skills/org-attach/SKILL.md:79-96 | Step 0 輸送フレーム確認（renga なら停止） | keep | printenv ORG_TRANSPORT の出力は renga。今の運用ではこのスキルは常にここで停止する（judgments に挙げる）。 | 18 |
| .claude/skills/org-attach/SKILL.md:97-113 | Step 1 list_panes とフィールド | keep | runtime の terminal/tmux.py:49 が BROKER_SOCKET="claude-org-broker"。 | 17 |
| .claude/skills/org-attach/SKILL.md:114-121 | attach 可能なのは子ペインだけ（窓口は logical pane） | keep | broker/server.py:1312 に register_logical_pane がある。broker-dogfood-runbook.md:570 の『対象範囲（重要）』に現存。 | 8 |
| .claude/skills/org-attach/SKILL.md:122-128 | list_panes 空 / tool_not_authorized 時 | keep | surface.py:846 で ops-tier 以外を拒否している。 | 7 |
| .claude/skills/org-attach/SKILL.md:129-133 | list_peers 直前の capability gate 適用 | keep | 87a39be 2026-08-08 で list_peers 全 8 経路へ配線（#879）。gate の reference は §3-B（:346）に現存。 | 5 |
| .claude/skills/org-attach/SKILL.md:134-145 | 縮退時 / 承認済み時の帰結 | keep（当初 move） | :144 自身が『旧版 fallback（現行配備の全 backend）では従来どおり』と書いており、今日は通らない枝。正本は gate reference §1-2（:80）。list_peers は補足用で、join には必須ではない（:126-127）。<br>**検証**: 反証で move→keep: :134-137 の縮退時の帰結（破棄しても pane-id join は継続）は capability 形のときの現役規則。『現行配備は全て旧版 fallback』は gate reference :40 の自己申告にすぎない。renga の list_peers はタブ情報を出す（実出力に [your tab]）ので未確定。read-only スキル内の短い条件付き規則なので keep に寄せた。 | 12 |
| .claude/skills/org-attach/SKILL.md:146-169 | Step 2 tmux list-panes -a -F | keep | tmux.py:142 が session 名 f"claude-org-broker-{pid}-{seq}" を生成しており、出力例と一致。 | 24 |
| .claude/skills/org-attach/SKILL.md:170-190 | Step 3 pane_id join と unmatched の扱い | keep | 手順本体。 | 21 |
| .claude/skills/org-attach/SKILL.md:191-213 | Step 4 attach コマンド生成・Ctrl-b の読み替え | keep | 7d56832 2026-08-09（renga サイドバーとの衝突注記、#889）。dispatcher-view.md:55-67 に根拠がある。 | 23 |
| .claude/skills/org-attach/SKILL.md:214-271 | 出力フォーマット（worked example） | keep | 出力の雛形。:252-260 は Step 4 の注意（:195-212）を出力用に再掲したもの。58 行と長いので縮約できる余地がある（推測）。 | 58 |
| .claude/skills/org-attach/SKILL.md:272-280 | やらないこと（read-only 不変条件） | keep | frontmatter の allowed-tools（:16-22）と一致している。 | 9 |

### .claude/skills/org-delegate/SKILL.md（385 行: keep 351 / move 2 / delete 3 / outdated 20 / transport 9）

| 行範囲 | 節 | 分類 | 根拠 | 行数 |
|---|---|---|---|---|
| .claude/skills/org-delegate/SKILL.md:1-22 | frontmatter（name/description/allowed-tools の Bash 部） | keep | tools/gen_delegate_payload.py・tools/ensure_projects_registry.py・tools/journal_append.{sh,py}・tools/state_db/importer.py が ls で現存。生成物（tools/skill_src/manifest.json:84-85 で source=SKILL.md.in）なので編集は .in 側 | 22 |
| .claude/skills/org-delegate/SKILL.md:23-29 | allowed-tools の mcp__org-broker__* 6 件 | transport | allowed-tools が broker 名（mcp__org-broker__*）だけを列挙。運用中の renga（mcp__renga-peers__*）は列挙されていない。生成器がコード既定 broker で render した結果（tools/transport.py:19-24）。編集は .in 側 | 7 |
| .claude/skills/org-delegate/SKILL.md:30-40 | 冒頭説明・本 SKILL のスコープ | keep | リンク先の .dispatcher/references/spawn-flow.md、org-pull-request/org-escalation の SKILL.md、references/ack-template.md が ls で現存。窓口とディスパッチャーの分担を示す索引 | 11 |
| .claude/skills/org-delegate/SKILL.md:41-42 | state-db cutover 注記（StateWriter 経由必須） | keep | tools/state_db/writer.py:12（post_commit_regenerate）、:110-144（worker archive の自動 move）、tools/state_db/drift_check.py が現存。M4/#267 の由来説明は短いので本文に残してよい | 2 |
| .claude/skills/org-delegate/SKILL.md:43-44 | 輸送層（transport）両系の注記 | transport | broker/renga の読み替え（mcp__org-broker__* → mcp__renga-peers__*）と references/transport-duality.md へのポインタ | 2 |
| .claude/skills/org-delegate/SKILL.md:45-57 | 窓口とディスパッチャーの役割分担表 | keep | 表が参照する gen_delegate_payload.py と spawn-flow.md が現存。CLOSE_PANE は .dispatcher/references/pane-close.md が consumer | 13 |
| .claude/skills/org-delegate/SKILL.md:58-64 | レーン選択判定（導入と表ヘッダ） | keep | CLAUDE.md:69-87「タスクルーティング 2 レーン制」と内容が重複する。正本は手順を実行する本スキル側とし、CLAUDE.md 側を縮める方向（judgments に記載） | 7 |
| .claude/skills/org-delegate/SKILL.md:65-65 | 軽量レーン行の「run_in_background=true 必須」 | outdated | .hooks/block-foreground-subagent.sh:13-25,133-149 によると、現行ハーネスでは Agent 入力から run_in_background が除かれており、キーが無い呼び出しは許可される<br>**補足**: CLAUDE.md:82 と Issue #942 のとおり、subagent は常に背景で動き、呼び出し側が指定するパラメータは無い。「run_in_background=true 必須」を削り、「背景実行（同期実行禁止）」とだけ書く<br>**検証**: 反証で維持: .hooks/block-foreground-subagent.sh:13-25 は、Agent の入力から run_in_background が廃止されたと書く。:143-149 はキーが無い呼び出しを許可する。CLAUDE.md:82 には『呼び出し側で指定するパラメータは無い』とある。 | 1 |
| .claude/skills/org-delegate/SKILL.md:66-75 | 重量レーン行・重量へ倒すケース | keep | CLAUDE.md:86 と同じ趣旨。正本は手順の場である本スキル | 10 |
| .claude/skills/org-delegate/SKILL.md:76-76 | 軽量レーン必須条件: run_in_background=true で起動 | outdated | .hooks/block-foreground-subagent.sh:143-149 はキーが無い呼び出しを許可し、キーがあって true 以外のときだけ deny する<br>**補足**: CLAUDE.md:82 に合わせる。現行ハーネスは常に背景で動き、指定パラメータは無い<br>**検証**: 反証で維持: 65 行目と同じ。hook の BG_STATE 判定（absent のとき Agent を許可、:133-149）と CLAUDE.md:82 が現行の事実。 | 1 |
| .claude/skills/org-delegate/SKILL.md:77-80 | 軽量レーン必須条件（Codex in-loop・人間ゲート）と routing の締め | keep | CLAUDE.md:83-84 と重複する。正本は本スキル | 4 |
| .claude/skills/org-delegate/SKILL.md:81-90 | 重量レーンの brief 強化（ultracode 3 基準・--impl-guidance） | keep | gen_delegate_payload.py:2274 に --impl-guidance がある。dispatcher 側の consumer は spawn-flow.md:286。CLAUDE.md:88 は同じ 3 基準を全文で重複して持つので、正本は本スキル | 10 |
| .claude/skills/org-delegate/SKILL.md:91-92 | 強い権限の事前許可文言を agent プロンプトに書かせない | keep | spawn-flow.md:318 に同じ意味の制約がある。二重配置は意図的（本文で「片方だけだと再発」と明記） | 2 |
| .claude/skills/org-delegate/SKILL.md:93-94 | brief 文言は許可の宣言であって武装ではない（Issue #554 の機構説明） | delete | 同じ機構説明が spawn-flow.md:282-298 と CLAUDE.md:88 にもある。窓口の手順は 90 行目（--impl-guidance で許可を書く）で完結する<br>**補足**: 正本は .dispatcher/references/spawn-flow.md 3-5a（282-298 行）。本スキルには「発動は dispatcher の send_keys kickoff（spawn-flow 3-5a）」の 1 行ポインタだけ残す | 2 |
| .claude/skills/org-delegate/SKILL.md:95-96 | 専用フラグ化はスコープ外・ultracode の位置づけ | keep | gen_delegate_payload.py の add_argument（2237-2300）に ultracode 用フラグは無く、記述と一致する | 2 |
| .claude/skills/org-delegate/SKILL.md:97-98 | 段別モデル指定（推奨） | keep | spawn-flow.md:316 と対になる二重配置で、本文に「一致させる」と明記がある。consumer は Workflow tool の agent() opts.model | 2 |
| .claude/skills/org-delegate/SKILL.md:99-105 | 重量レーンのワーカーモデル選定（opus/sonnet・モデル: sonnet 行） | keep | 受け側は spawn-flow.md:104-111 で、「モデル: 行が無ければ opus」が一致する。gen_delegate_payload.py に model フラグが無いことも add_argument 一覧で確認 | 7 |
| .claude/skills/org-delegate/SKILL.md:106-117 | 委譲前チェックリスト（曖昧語・OS 前提） | keep | 窓口がユーザーに聞き返す手順。実装に依存しない運用規則 | 12 |
| .claude/skills/org-delegate/SKILL.md:118-118 | Windows worker + CLI ツールの cp932 注意 | delete | 本文自体が「rendered brief の Windows 注意事項にも常時記載済み」と書いている。tools/templates/worker_brief_normal.md:21 に cp932 の規則があり、全ワーカーに配られる<br>**補足**: 正本は tools/templates/worker_brief_normal.md:18-22（Windows 環境の注意事項）。由来は references/rationale.md §1 に既にある<br>**検証**: 反証で維持: cp932 の規則は worker_brief_normal.md:21（条件ブロックの外なので常に出力される）にも、worker_brief_self_edit.md:20 にもある。ワーカーには必ず届くので、窓口が --impl-guidance で重ねて書く必要は無い。由来は rationale.md §1 にある。 | 1 |
| .claude/skills/org-delegate/SKILL.md:119-120 | pin 管理パッケージのバグ疑いは drift check を確認してから | keep | .claude/skills/org-start/SKILL.md:177 で Block C2（runtime drift）が現存。由来は rationale.md §2 | 2 |
| .claude/skills/org-delegate/SKILL.md:121-130 | incorporation / sync 系タスクの初手チェックリスト | keep | 窓口が brief に「初手 cp 禁止」を書く手順。導入は f0efed1（2026-05-09 #394）。2026-07 以降の commit に selective/cherry-pick の再発記録は無いが、手順そのものなので keep | 10 |
| .claude/skills/org-delegate/SKILL.md:131-140 | 並列委譲で同一ファイルを編集するケースの brief 事前文言 | keep | --impl-guidance（gen_delegate_payload.py:2274）と --parallel-notes（:2276）が現存。実例は rationale.md §4（2026-07-22 ja#747/#748） | 10 |
| .claude/skills/org-delegate/SKILL.md:141-141 | rebase 規定は worker-claude-template.md に従う | outdated | 「完了報告前の rebase」節は worker-claude-template.md:175-185 にしか無い。実 brief の tools/templates/worker_brief_normal.md と worker_brief_self_edit.md には無く（grep -c '完了報告前の rebase' = 0）、ワーカーに届いていない<br>**補足**: brief は gen_worker_brief.py:414-417 が tools/templates/worker_brief_{normal,self_edit}.md から作る。rebase 規定を効かせるなら worker_brief_normal.md へ移植し、ポインタもそちらへ向ける<br>**検証**: 反証で維持: tools/templates/worker_brief_{normal,self_edit}.md を grep しても rebase は 0 件。gen_worker_brief.py:414-417 が読むのはこの 2 つだけなので、ワーカーには届いていない。編集は .in 側で行う。 | 1 |
| .claude/skills/org-delegate/SKILL.md:142-155 | 並列委譲の締め・Step 0 プロジェクト名前解決 | keep | tools/ensure_projects_registry.py と registry/projects.example.md が現存（ls registry）。Issue #811 の operator-local 生成と一致 | 14 |
| .claude/skills/org-delegate/SKILL.md:156-170 | Step 0.5 work-skill 検索 | keep | gen_delegate_payload.py:2275 に --knowledge がある。補足: frontmatter に type/triggers を持つ SKILL は grep で 1 件だけなので、照合は実質 description 頼み | 15 |
| .claude/skills/org-delegate/SKILL.md:171-173 | Step 0.6 見出し | keep | release-pre-fetch.md への入口 | 3 |
| .claude/skills/org-delegate/SKILL.md:174-174 | 「以下 4 条件のいずれか」（列挙は 3 条件） | outdated | 直後の 176-178 行は 3 項目しか無い。導入時の 1a3272c（2026-05-15 #465）から「4 条件」と書かれたまま。release-pre-fetch.md:22-24 も 3 項目<br>**補足**: 「以下 3 条件」に直す（180 行の「本体に残す 4 条件」も同じ）<br>**検証**: 反証で維持: 箇条書きは 176-178 の 3 項目だけ。release-pre-fetch.md:22-24 も 3 項目。正しくは『3 条件』。 | 1 |
| .claude/skills/org-delegate/SKILL.md:175-179 | Step 0.6 トリガー 3 条件 | keep | release タスクの命名（task_id に release を含む等）が判定材料。本文に「見逃しは BLOCKER 直結」とある。release-pre-fetch.md:18-24 は同内容の重複で、そちらを delete とした | 5 |
| .claude/skills/org-delegate/SKILL.md:180-180 | worker permissions deny の根拠・4 条件省略禁止 | outdated | tools/org_extension_schema.json:729 で worker_roles.default.permissions.allow に Bash(git fetch:*) が入っている。deny されているのは :746 の git pull だけ<br>**補足**: 根拠は「worker は git pull（local main の ff）ができない」と書く。fetch は許可済み。「4 条件」は「3 条件」に直す<br>**検証**: 反証で維持: schema の allow（:729）に Bash(git fetch:*) がある。ただし deny は git pull（:746-749）だけではなく、:744-745 の『git -C * fetch』も含む。正しくは『素の git fetch は可、git -C fetch と pull は不可』。あわせて『4 条件』を『3 条件』に直す。 | 1 |
| .claude/skills/org-delegate/SKILL.md:181-182 | Pattern B との切り分け（Issue #480） | move | 手順ではなく適用範囲の説明で、release-pre-fetch.md:28-45 に同じ説明の詳細版がある<br>**補足**: release-pre-fetch.md「Issue #480 との関係」節に一本化する | 2 |
| .claude/skills/org-delegate/SKILL.md:183-195 | Step 0.7〜2 一括生成・dispatch 前検証チェック | keep | gen_delegate_payload.py に preview/apply サブコマンドがある（2319-2335）。手動検証の詳細は delegate-flow-details.md §1.5 | 13 |
| .claude/skills/org-delegate/SKILL.md:196-229 | 標準フロー（preview/apply/send_plan）と T1 記録・再 apply 規則 | keep | gen_delegate_payload.py:2119（T1: queued と delegate_sent を同一トランザクションで記録）、:127（queued 以外の再 apply は失敗させる）、:2327（apply の help）。220 行の mcp__org-broker__send_message は輸送層の名前 | 34 |
| .claude/skills/org-delegate/SKILL.md:230-241 | よく使うフラグ | keep | preview --help で --placement/--report-target を確認（gen_worker_brief.py:685 _add_report_args を流用）。--base-ref・--mode・--skip-settings(:2331)・--from-toml・--closes-issue・--refs-issues・--impl-target・--commit-prefix・--branch も argparse に全てある | 12 |
| .claude/skills/org-delegate/SKILL.md:242-249 | Pattern/role/branch 判定詳細ポインタ・対象ファイル抽出 | keep | リンク先の delegate-flow-details.md と claude-org-self-edit.md が現存 | 8 |
| .claude/skills/org-delegate/SKILL.md:250-253 | 標準経路が想定外の出力を返した場合 | keep | docs/legacy/hand-typed-delegate-path.md が現存。delegate-flow-details.md:179-189 は同じ規則の重複で、正本はこちら | 4 |
| .claude/skills/org-delegate/SKILL.md:254-264 | Step 1.7 Codex design review trigger（4 条件のみ） | outdated | 本文は「以下のいずれかに該当する場合のみ」として 4 条件を挙げる。references/codex-design-review.md:15 には 5 つ目のトリガー（監視ロールへの blocking wait 追加 / lifecycle 変更。ファイル変更 1 件でも発動）がある<br>**補足**: codex-design-review.md:9-15 の 5 条件に合わせ、監視ロール条件を追記する<br>**検証**: 反証で維持: SKILL.md:257-260 は 4 条件だけ。codex-design-review.md:15 には 5 つ目の監視ロール条件（ファイル変更 1 件でも発動）がある。追加 3 問の手順（:32-40）もこの条件を前提にしている。 | 11 |
| .claude/skills/org-delegate/SKILL.md:265-278 | Step 1.8 dogfood・Step 3/4 ポインタ | keep | registry/dogfood_pending.md と references/dogfood-protocol.md が現存（ls）。DELEGATE_COMPLETE は spawn-flow.md が producer | 14 |
| .claude/skills/org-delegate/SKILL.md:279-281 | Step 5 見出し | keep | 節見出し | 3 |
| .claude/skills/org-delegate/SKILL.md:282-284 | cwd 注意: state.db touching tools（Issue #398 根本対応中） | outdated | 7c8d639「make state.db tools cwd-independent (Closes #398) (#400)」。journal_append.sh:30-31 と journal_append.py:48 は repo root を自己解決し、set_run_pr_open.py:64-66 は resolve_state_db_path を使う<br>**補足**: tools 群は cwd に依存しないので注意は不要。残る cwd 依存は 2a の python -c（connect('.state/state.db') を相対パスで開く、SKILL.md:349）だけなので、その直前に 1 行注記する<br>**検証**: 反証で維持: 7c8d639（2026-05-10 Closes #398）が入っている。journal_append.sh:30-31 は REPO_ROOT を、journal_append.py:48 は _REPO_ROOT を自分で求め、set_run_pr_open.py:64-66 は resolve_state_db_path を使う。cwd に依存するのは 2a の python -c（connect('.state/state.db') と tools の import）だけ。 | 3 |
| .claude/skills/org-delegate/SKILL.md:285-294 | 窓口→worker メッセージング規約（1 worker = 1 task = 1 scope） | keep | CLAUDE.md:90-98 と 3 規則が全文で重複する。本文は CLAUDE.md を SoT と呼ぶが、タスクの方針では正本は手順の場である本スキル（judgments に記載） | 10 |
| .claude/skills/org-delegate/SKILL.md:295-304 | DELEGATE_COMPLETE 受信時の挨拶 | keep | DELEGATE_COMPLETE の producer は spawn-flow.md。挨拶本文の「org-broker 送信」は輸送層の名前 | 10 |
| .claude/skills/org-delegate/SKILL.md:305-306 | .claude/ 編集タスクの send_keys 事前承認（self_edit_approval.py） | keep | tools/self_edit_approval.py:1045（send）、:1057（--target）、:1097（audit）が現存。Issue #956 の Enter 落ち事故は rationale.md §7 | 2 |
| .claude/skills/org-delegate/SKILL.md:307-322 | ワーカーからのメッセージ受信時: canonical event flow | keep | ack の正本は ack-template.md。CLAUDE.md:100-102 も ack 必須を書いているが、要約とポインタ | 16 |
| .claude/skills/org-delegate/SKILL.md:323-338 | 0. 判断仰ぎ / 1. 進捗報告（worker_reported 記帳） | keep | docs/journal-events.md:96 に worker_reported（worker, task, summary）。consumer は .dispatcher/CLAUDE.md:170（PANE_OUTPUT_WITHOUT_PEER_MSG）。実例は rationale.md §6（2026-07-08） | 16 |
| .claude/skills/org-delegate/SKILL.md:339-360 | 2a 完了報告: REVIEW 遷移・WORKER_COMPLETION_NOTED・dogfood 更新 | keep | WORKER_COMPLETION_NOTED の consumer は .dispatcher/references/worker-monitoring.md:219 と pane-close.md:23-38（2026-09-23 に 4 task の実例）。StateWriter.update_run_status は writer.py に現存 | 22 |
| .claude/skills/org-delegate/SKILL.md:361-361 | 人間向け理解サマリ（スキーマ SoT=worker-claude-template.md） | outdated | ワーカーに届くサマリ規定は tools/templates/worker_brief_normal.md:190。worker-claude-template.md は gen_worker_brief.py:414-417 が読まない文書<br>**補足**: スキーマ SoT のリンク先を tools/templates/worker_brief_normal.md:190（自己編集は worker_brief_self_edit.md）に変える。手順の中身は keep<br>**検証**: 反証で維持: ワーカーに届くサマリの規定は worker_brief_normal.md:190 と worker_brief_self_edit.md:121。worker-claude-template.md は gen_worker_brief.py から読まれない。同じリンクが docs/contracts/role-contract.md:258 と org-pull-request SKILL.md.in:70 にもあるので、まとめて直す必要がある。 | 1 |
| .claude/skills/org-delegate/SKILL.md:362-367 | awaiting_user 通知 emit・承認待ち停止 | keep | tools/templates/attention.example.json:28,88 に secretary_awaiting_user（urgent）がある。366 行の「並走 runtime PR が拾う」は導入時の言い回しで、実装済み（runtime 0.1.49） | 6 |
| .claude/skills/org-delegate/SKILL.md:368-373 | 2b/2c ポインタ・WORKER_REOPENED | keep | WORKER_REOPENED の consumer は worker-monitoring.md:219 と pane-close.md:25 | 6 |
| .claude/skills/org-delegate/SKILL.md:374-385 | ワーカー監視と介入判定 | keep | inspect_pane/send_keys は両輸送層に現存。「Codex 4 ラウンド目以降で介入」は brief の round 上限 3（worker_brief_normal.md:182）と整合する | 12 |

### .claude/skills/org-delegate/references/delegate-flow-details.md（198 行: keep 189 / move 9 / delete 0 / outdated 0 / transport 0）

| 行範囲 | 節 | 分類 | 根拠 | 行数 |
|---|---|---|---|---|
| .claude/skills/org-delegate/references/delegate-flow-details.md:1-17 | 導入・契約（T1 予約のみ） | keep | docs/contracts/delegation-lifecycle-contract.md が現存。11-14 行は delegate_sent イベント（SKILL.md:223、Issue #928）に触れていないが、矛盾ではなく省略 | 17 |
| .claude/skills/org-delegate/references/delegate-flow-details.md:18-40 | Step 0.7 gitignored target check（Pattern C gitignored sub-mode） | keep | gen_delegate_payload.py:1275-1279 に check-ignore の probe があり、:345-346 に CLAUDE.local.md の判定がある | 23 |
| .claude/skills/org-delegate/references/delegate-flow-details.md:41-71 | Step 1 Pattern A/B/C 判定・live_repo_worktree | keep | tools/resolve_worker_layout.py:92-96 で _ACTIVE_RUN_STATUSES=('queued','in_use','review') が記述と一致する | 31 |
| .claude/skills/org-delegate/references/delegate-flow-details.md:72-104 | §1.5 (a) コミット済みベース存在確認 | keep | SKILL.md:192 から一次参照されている。生成器が検証しないことは SKILL.md:190 に明記 | 33 |
| .claude/skills/org-delegate/references/delegate-flow-details.md:105-130 | §1.5 (b) 契約 grep | keep | docs/contracts/ と .dispatcher/references/worker-monitoring.md が現存。SKILL.md:193 の詳細版 | 26 |
| .claude/skills/org-delegate/references/delegate-flow-details.md:131-153 | §2 Role 判定表 | keep | tools/org_extension_schema.json の worker_roles に default / claude-org-self-edit / doc-audit がある。golden fixture tests/fixtures/delegate_payload/*doc_audit* が現存 | 23 |
| .claude/skills/org-delegate/references/delegate-flow-details.md:154-178 | §3 DELEGATE 本文テンプレの必須行 | keep | tests/fixtures/delegate_payload/delegate_payload_pattern_a_default_full.golden.md の行構成が一致する。base 設定時に足される「ベース:」行（gen_delegate_payload.py:598-625、#808）は記載が無い（軽微） | 25 |
| .claude/skills/org-delegate/references/delegate-flow-details.md:179-189 | §4 標準経路が想定外の出力を返した場合（規則部） | keep（当初 delete） | SKILL.md:251-253 に同じ規則（手で再現しない・Issue を切って pause・--skip-settings だけ可）がある<br>**補足**: 正本は SKILL.md:251-253<br>**検証**: 反証で delete→keep: 186-187 行の『手作業で迂回するかは user が判断する。Secretary が自分で例外を認めてはならない』は SKILL.md:253 に無い（SKILL 側は『手作業 fallback はスコープ外』とだけ書く）。片方にしか無い例外なので、純粋な重複ではない。消すなら、この 1 文を SKILL 側へ移してから。 | 11 |
| .claude/skills/org-delegate/references/delegate-flow-details.md:190-198 | §4 legacy 手書き経路の経緯 | move | docs/legacy/hand-typed-delegate-path.md の由来と、そこで起きた失敗モードの説明。手順としては不要<br>**補足**: references/rationale.md か docs/legacy/hand-typed-delegate-path.md の冒頭へ<br>**検証**: 反証で維持: 主に legacy 経路の由来と失敗モードの説明。194 行の『通常運用で参照するのはプロトコル違反』は SKILL.md:253（『標準オペレーションでは参照禁止』）に既にある。195-196 行の『runtime CLI が無い環境の fallback にもならない』は移す先の文書に残すこと。 | 9 |

### .claude/skills/org-delegate/references/worker-claude-template.md（400 行: keep 57 / move 7 / delete 317 / outdated 17 / transport 2）

| 行範囲 | 節 | 分類 | 根拠 | 行数 |
|---|---|---|---|---|
| .claude/skills/org-delegate/references/worker-claude-template.md:1-11 | 冒頭「Step 1.5 で配置する CLAUDE.md のテンプレート / そのまま書き出す」 | outdated | gen_worker_brief.py:414-417 は tools/templates/worker_brief_normal.md / worker_brief_self_edit.md を読む。本ファイルを読むコードは無い（git grep worker-claude-template を tests/tools/scripts に向けて 0 件）<br>**補足**: 実テンプレートは tools/templates/worker_brief_{normal,self_edit}.md。本ファイルは誰も消費しない複製で、96282ca（#1014）と 176e70a（#1019）は本ファイルにしか入っておらずワーカーに届いていない<br>**検証**: 反証で維持: gen_worker_brief.py:414-417 が読むのは tools/templates/worker_brief_{normal,self_edit}.md だけ。git grep の結果、tests/ tools/ scripts/ .hooks/ にこのファイルを読むコードは無い。インストール済み runtime 0.1.49 の中にも参照が無い。96282ca と 176e70a が変更したのは本ファイル 1 つだけ（git show --stat）。 | 11 |
| .claude/skills/org-delegate/references/worker-claude-template.md:12-29 | # Worker・作業ディレクトリ・禁止事項 | delete | tools/templates/worker_brief_normal.md:1-17 と同内容で、本ファイルは消費されない（gen_worker_brief.py:414-417）<br>**補足**: 正本は tools/templates/worker_brief_normal.md:1-17<br>**検証**: 反証で維持: worker_brief_normal.md:1-17 と同じ内容（禁止事項 4 は同文）。本ファイルを読むコードは無い。ただし docs/contracts/*（role-contract.md:13 など）が本ファイルを SoT として参照しているので、参照先の付け替えが必要。 | 18 |
| .claude/skills/org-delegate/references/worker-claude-template.md:30-34 | 正しい作業手順（git init / git clone を worker が実行） | outdated | instruction-template.md:37-39,51-53 は「ワーカー側で git clone / git init を実行しないこと（窓口が spawn 前に準備済み）」と書く。実 brief にこの節は無い（grep -c '正しい作業手順' worker_brief_normal.md = 0）<br>**補足**: 作業ディレクトリは gen_delegate_payload.py apply が clone / worktree を準備する。この節は削除<br>**検証**: 反証で維持: instruction-template.md:37-39,51-53 は『ワーカー側で git clone / git init しない（窓口が準備済み）』と書く。実 brief にこの節は無い。 | 5 |
| .claude/skills/org-delegate/references/worker-claude-template.md:35-52 | Windows 注意事項・Bash 絶対パス規約 | delete | 実 brief の worker_brief_normal.md:18-36 に同等の節がある（実 brief には cp932 の項もある）。本ファイルは消費されない<br>**補足**: 正本は tools/templates/worker_brief_normal.md:18-36<br>**検証**: 反証で維持: worker_brief_normal.md:18-36 が同じ内容を持ち、cp932 の項まで含む上位互換。 | 18 |
| .claude/skills/org-delegate/references/worker-claude-template.md:53-58 | 着手前 preflight（書き込み境界・/tmp 共有・npm cache・背景実行禁止） | delete | worker_brief_normal.md:37-42 と同文<br>**補足**: 正本は tools/templates/worker_brief_normal.md:37-42<br>**検証**: 反証で維持: worker_brief_normal.md:37-42 と同文。 | 6 |
| .claude/skills/org-delegate/references/worker-claude-template.md:59-59 | Codex の Bash 呼び出しに allowed_domains（chatgpt.com）を明示 | delete | 176e70a（2026-09-21 #1019）で本ファイルにだけ追加され、ワーカーには届いていない。c7875c0（2026-10-05 #1033）以降、tools/org_extension_schema.json:627 の sandbox network.allowedDomains に chatgpt.com 等が既定で入っている<br>**補足**: sandbox の allowlist（tools/org_extension_schema.json:627、runtime settings generate が配る）が代わりを務める<br>**検証**: 反証で維持: schema:627 の network.allowedDomains に chatgpt.com、*.chatgpt.com、api.openai.com などが入っている（c7875c0 #1033）。tests/test_worker_allowed_domains_sync.py が、この一覧と runtime の WORKER_SANDBOX_SETTINGS（strictAllowlist）が一致することを検査している。実 brief には 1 度も入っていない。 | 1 |
| .claude/skills/org-delegate/references/worker-claude-template.md:60-60 | linked worktree の node_modules 親解決 | delete | worker_brief_normal.md:43 と同文<br>**補足**: 正本は tools/templates/worker_brief_normal.md:43<br>**検証**: 反証で維持: worker_brief_normal.md:43 と同文。 | 1 |
| .claude/skills/org-delegate/references/worker-claude-template.md:61-61 | uv / pip キャッシュは $TMPDIR に置く | keep | 2026-09-14・09-15 の 2 件の実例が本文にあり、sandbox は **/credentials* を denyRead（本セッションの設定でも確認）。ただし 96282ca（#1014）は本ファイルにしか入っておらず（worker_brief_normal.md の UV_CACHE_DIR は 0 件）、ワーカーに届いていない<br>**補足**: 効かせるには tools/templates/worker_brief_normal.md の preflight へ移植する（judgments に記載） | 1 |
| .claude/skills/org-delegate/references/worker-claude-template.md:62-74 | cp/mv alias・git 退避禁止・dangerouslyDisableSandbox・スクラッチコマンド前半 | delete | worker_brief_normal.md:44-56 と同文<br>**補足**: 正本は tools/templates/worker_brief_normal.md:44-56<br>**検証**: 反証で維持: worker_brief_normal.md:44-56 と同文。uv/pip cache を扱う 61 行と 75-77 行はこの範囲の外で、本ファイルにしか無い（96282ca）。 | 13 |
| .claude/skills/org-delegate/references/worker-claude-template.md:75-77 | UV_CACHE_DIR / PIP_CACHE_DIR の export | keep | 61 行の uv 規則とセットのコマンド。実 brief には無い<br>**補足**: 61 行と一緒に worker_brief_normal.md:48-60 のコマンドブロックへ移植する | 3 |
| .claude/skills/org-delegate/references/worker-claude-template.md:78-107 | スクラッチコマンド後半・プロジェクト情報・現在のタスク・ナレッジ参照・権限 | keep（当初 delete） | worker_brief_normal.md:56-107（npm ci/pack、プロジェクト情報、タスク、ナレッジ参照、権限）と同等。本ファイルは消費されない<br>**補足**: 正本は tools/templates/worker_brief_normal.md:56-107<br>**検証**: 反証で delete→keep: 91-100 行（knowledge/curated と raw を読めること、いつ参照するか）は無条件の規定。brief:86-90 の『ナレッジ参照』は BEGIN:references の条件ブロックで、--knowledge を渡したときしか出力されない。同じ強さではないので、91-100 行は brief へ移植するか残すかを判断すること。78-90 行と 102-107 行は brief:56-106 と重複している。 | 30 |
| .claude/skills/org-delegate/references/worker-claude-template.md:108-133 | 書き込み可能範囲・監査ロール書き込み無し・拒否される場所 | delete | worker_brief_normal.md:39 が書ける場所・doc-audit は書き込み面が無いこと・scratchpad 禁止・hook の許可パス 3 つを持つ。本ファイルは消費されない<br>**補足**: 正本は tools/templates/worker_brief_normal.md:39。doc-audit の /tmp に穴がある件の出典は docs/contracts/role-pattern-sandbox-contract.md:1060,1148<br>**検証**: 反証で維持: 書ける場所、doc-audit は書き込み面が無いこと、scratchpad 禁止、hook の許可パス（knowledge/raw を含む）は brief:39 にある。/tmp 直下が書けないことは『置ける場所は worker_dir と $TMPDIR だけ』から読み取れる。 | 26 |
| .claude/skills/org-delegate/references/worker-claude-template.md:134-138 | 書き込みを拒否されたときの対処（2026-08-27 ロックアウト事故） | move | 135 行の手順は worker_brief_normal.md:46 と重複。136-137 行は事故の経緯。schema:601 の注記によると、現在は allowUnsandboxedCommands:false（strict mode）で dangerouslyDisableSandbox の再実行自体が無視される<br>**補足**: 事故の経緯は rationale 系の文書へ。手順は brief:46 が正本<br>**検証**: 反証で維持: 135 行の手順は brief:46 と重複。136-137 行は 2026-08-27 の事故の経緯。schema:601 の注記によると allowUnsandboxedCommands:false で、本セッションでも dangerouslyDisableSandbox は無効になっている。 | 5 |
| .claude/skills/org-delegate/references/worker-claude-template.md:139-151 | 監査・調査タスクの行動規範（別仮説を実機で反証） | delete | git log -S'実機反証' の該当は f0efed1（2026-05-09 #394）だけで、2026-07 以降の再発記録は無い（knowledge/raw は 0 件）。145 行の 2026-08-27 事例は strict sandbox で起きなくなった。実 brief に無いので、削除してもワーカーの挙動は変わらない<br>**補足**: 残すなら worker_brief_normal.md へ移植（judgments に記載）<br>**検証**: 反証で維持: 同じ規範が knowledge/curated/delegation.md:83-87 §3 にある。git log --since=2026-07-01 --grep=仮説 は 0 件。実 brief に無く、本ファイルは読まれないので、消してもワーカーの挙動は変わらない。正本は knowledge/curated/delegation.md §3。 | 13 |
| .claude/skills/org-delegate/references/worker-claude-template.md:152-162 | probe / fuzzing 系タスクの credential 取扱い | delete | schema:601 の注記によると、runtime>=0.1.46 の spawn 時 --settings overlay が ~/.config/gh/hosts.yml と gh token 環境変数を deny し、~/.ssh・~/.aws は denyRead。導入は f0efed1（2026-05-09）で、その後の再発記録は無い。実 brief に無い<br>**補足**: 技術的な deny が代わりを務める（tools/org_extension_schema.json:601 の $comment_sandbox_hardening）<br>**検証**: 反証で維持: delete は維持するが、根拠を直す。技術的な deny が覆うのは gh の hosts.yml・~/.aws・~/.ssh だけで、~/.netrc と ~/.npmrc は schema にも runtime にも出てこない（grep 0 件）。正本は knowledge/curated/delegation.md:90-95 §4（同じ文言）とする。 | 11 |
| .claude/skills/org-delegate/references/worker-claude-template.md:163-174 | 生成物 prose（生成元あり）の編集タスク | delete | CI の tests.yml:35 が tools/test_gen_skill_prose.py:614-626（production manifest に drift が無いことの検査）を回すので、drift は CI で止まる。本ファイルは消費されず、実 brief にも無い（grep -c gen_skill_prose = 0）<br>**補足**: CI ゲート（tools/test_gen_skill_prose.py:614）が代わりを務める。ワーカーに事前に知らせたいなら self-edit brief に 1 行移植する<br>**検証**: 反証で維持: tools/test_gen_skill_prose.py:614-626 が production manifest の --check を回し、tests.yml:35 が CI でこれを実行する。2026-07 以降の git log に生成物の直接編集による drift 事故は見当たらない。本ファイルは読まれない。 | 12 |
| .claude/skills/org-delegate/references/worker-claude-template.md:175-186 | 完了報告前の rebase（手順 1-5） | keep | SKILL.md:141 と rationale.md §4 がこの規定を前提にしている。ただし実 brief（worker_brief_normal / self_edit）に無く、ワーカーには届いていない<br>**補足**: 効かせるには worker_brief_normal.md へ移植する（judgments に記載） | 12 |
| .claude/skills/org-delegate/references/worker-claude-template.md:187-188 | rebase の背景（2026-07-08 kura conveyor） | move | 事故の経緯で、手順としては不要<br>**補足**: references/rationale.md へ<br>**検証**: 反証で維持: 2026-07-08 の kura PR #46/#47 の経緯説明で、手順は含まない。 | 2 |
| .claude/skills/org-delegate/references/worker-claude-template.md:189-280 | Codex セルフレビュー（full 前提・CODEX_HOME・空の合格） | delete | worker_brief_normal.md:108-179 に同等の手順がある。実 brief の方が新しい（ログ名に cksum、.codex-home を exclude 登録）。本ファイルは消費されない<br>**補足**: 正本は tools/templates/worker_brief_normal.md:108-179<br>**検証**: 反証で維持: worker_brief_normal.md:108-179 が同じ内容を持ち、そちらの方が新しい（cksum を使ったログ名、exclude 登録）。tests/fixtures の golden も brief 側。ただし delegation-lifecycle-contract.md:152,155 が本節を SoT として参照しているので、付け替えが必要。 | 92 |
| .claude/skills/org-delegate/references/worker-claude-template.md:281-284 | 安全機構に掛かった場合（safety block）のリカバリ | delete | 実 brief には無いが、ワーカーに届く指示文の instruction-template.md:82（手動）と :222（AUTO-EXPAND、runtime runner.py:2189 が展開）が同じ規則を持つ<br>**補足**: 正本は instruction-template.md:82 / :222<br>**検証**: 反証で維持: instruction-template.md:82（手動経路）と :222（AUTO-EXPAND。runtime の dispatcher/runner.py が読み込んで展開する）が、同じ safety block のリカバリ規則を持つ。 | 4 |
| .claude/skills/org-delegate/references/worker-claude-template.md:285-309 | round 規律・minimal・codex 禁止事項 | delete | worker_brief_normal.md:180-189（round 上限 3・同一指摘・綱引き・Minor/Nit・codex:rescue・gpt-6-astra）と :197-210（minimal）に同等の規定がある。293 行（別ワーカーにレビュー委譲しない）は a266d07 以降に再発記録が無い<br>**補足**: 正本は tools/templates/worker_brief_normal.md:180-210<br>**検証**: 反証で維持: round 規律は brief:180-188、minimal は brief:197-210 にある。ただし brief の minimal ブロックは depth=minimal のときしか出力されない（gen_worker_brief.py:330-331）。full ワーカーに minimal を追指示するときの書式は instruction-template.md:95-103 に残すこと。 | 25 |
| .claude/skills/org-delegate/references/worker-claude-template.md:310-317 | 作業完了時: 完了報告の送信方法 | delete | worker_brief_normal.md:223-225 と同じ役割（送信関数は ${transport_send_message} で render される）<br>**補足**: 正本は tools/templates/worker_brief_normal.md:223-225<br>**検証**: 反証で維持: worker_brief_normal.md:223-225 と同じ役割（送信関数は ${transport_send_message} で埋まる）。 | 8 |
| .claude/skills/org-delegate/references/worker-claude-template.md:318-318 | 完了報告の輸送層両系・二フレーム注記 | transport | renga/broker の読み替え、Refs #604 の二フレーム注記、Epic #6 Issue G の但し書き | 1 |
| .claude/skills/org-delegate/references/worker-claude-template.md:319-319 | ディスパッチャーではなく窓口へ | delete | worker_brief_normal.md:225 と同文<br>**補足**: 正本は tools/templates/worker_brief_normal.md:225<br>**検証**: 反証で維持: brief:225 に『ディスパッチャーではなく窓口に送ること』と同文がある。 | 1 |
| .claude/skills/org-delegate/references/worker-claude-template.md:320-320 | フォールバック: pane_not_found なら numeric pane id で送る | outdated | 実 brief の worker_brief_normal.md:225-227 は「同じ org だと確認できるまで一切再送しない / dispatcher へ 1 回だけ escalate / 確認できなければ停止」。ここは確認なしに numeric id へ再送させる逆向きの規定<br>**補足**: worker_brief_normal.md:225-227 と renga-error-codes.md の「pane_not_found の messaging 分岐」節が正しい<br>**検証**: 反証で維持: brief:225-227 は『同じ org だと確認できるまで再送しない。数値 id も含む』と書く。本行は確認なしに数値 id へ送らせるので、逆向きの規定になっている。 | 1 |
| .claude/skills/org-delegate/references/worker-claude-template.md:321-322 | 完了報告の内容（何を完了したか・成果物） | keep（当初 delete） | 実 brief では作業完了時の節（worker_brief_normal.md:223-229）が担う<br>**補足**: 正本は tools/templates/worker_brief_normal.md:223-229<br>**検証**: 反証で delete→keep: brief:223-229 には報告内容（何を完了したか・成果物）の列挙が無い。normal.md:207 は minimal で『適用されない』と書くだけで、full の書式はどこにも定義していない。full の報告内容を定めているのは本ファイルと instruction-template.md:72 だけ。brief へ移植するまでは消せない。 | 2 |
| .claude/skills/org-delegate/references/worker-claude-template.md:323-323 | rebase clean 確認行（必須） | keep | 175-186 の rebase 規定とセットの報告項目。実 brief に無い<br>**補足**: rebase 規定と一緒に worker_brief_normal.md へ移植する | 1 |
| .claude/skills/org-delegate/references/worker-claude-template.md:324-331 | 残作業・人間向け理解サマリ・PR 後のペイン保持 | keep（当初 delete） | worker_brief_normal.md:190-195（サマリ）と :228（ペイン保持）に同等の規定がある<br>**補足**: 正本は tools/templates/worker_brief_normal.md:190,228<br>**検証**: 反証で delete→keep: 325-331 行（サマリ、ペイン保持）は brief:190 と :228 と重複している。324 行の『残作業や注意点』は brief に無い（321 行と同じ事情）。範囲全体を delete にはできない。324 行を除けば delete でよい。 | 8 |
| .claude/skills/org-delegate/references/worker-claude-template.md:332-332 | レビュー待機中の受信（broker push / pull フォールバック・二フレーム） | transport | broker の channel sidecar / pull fallback と、renga の in-band push の併記。Refs #604 の注記と Epic #6 Issue G の但し書き | 1 |
| .claude/skills/org-delegate/references/worker-claude-template.md:333-362 | 振り返り記録フォーマット・SUSPEND 対応 | delete | worker_brief_normal.md:229（振り返り）と :231-232（SUSPEND）に同等の規定がある。事実/判断/根拠/適用場面の書式を強制する hook は無い（check-worker-boundary.sh が検査するのはファイル名だけ）<br>**補足**: 正本は tools/templates/worker_brief_normal.md:229-232<br>**検証**: 反証で維持: SUSPEND は brief:231-232 にある。raw の書式（事実/判断/根拠/適用場面）の正本は org-curate/references/knowledge-standards.md:10 にある（org-curate SKILL.md:169 が参照している）。delegation-lifecycle-contract.md:178 の参照は付け替えが必要。 | 30 |
| .claude/skills/org-delegate/references/worker-claude-template.md:363-375 | 条件付き追記: 背景タブ配置 | delete | worker_brief_normal.md:211-221 の <!--BEGIN:background_tab_report--> ブロックが同じ内容を持ち、--placement background_tab のとき出力される<br>**補足**: 正本は tools/templates/worker_brief_normal.md:211-221<br>**検証**: 反証で維持: brief:211-221 の BEGIN:background_tab_report ブロックが同じ内容を持ち、identity 照合まで含む。 | 13 |
| .claude/skills/org-delegate/references/worker-claude-template.md:376-388 | 条件付き追記: 監視ロール待ち合わせ設計の必須制約 | delete | instruction-template.md:157-164 と同じ 4 項目。ワーカーに届くのはそちら（dispatcher が送る指示文の制約節）<br>**補足**: 正本は instruction-template.md:157-164<br>**検証**: 反証で維持: instruction-template.md:157-164 が同じ 4 項目を持つ。codex-design-review.md:40 もそちらを参照している。brief にこの節を追記するコードは無い。 | 13 |
| .claude/skills/org-delegate/references/worker-claude-template.md:389-400 | 変数一覧 | delete | 本テンプレートは render されない。実際の置換変数は gen_worker_brief.py の _build_substitutions（render() 内、:420 付近）と ${worker_dir} 形式で、{var} 形式ではない<br>**補足**: 変数の正本は tools/gen_worker_brief.py と tools/templates/worker_brief.example.toml<br>**検証**: 反証で維持: 本ファイルは render されない。実際の置換は gen_worker_brief.py の _build_substitutions が ${var} 形式で行う。 | 12 |

### .claude/skills/org-delegate/references/instruction-template.md（233 行: keep 194 / move 9 / delete 19 / outdated 2 / transport 9）

| 行範囲 | 節 | 分類 | 根拠 | 行数 |
|---|---|---|---|---|
| .claude/skills/org-delegate/references/instruction-template.md:1-2 | タイトル | keep | 生成物（tools/skill_src/manifest.json:66-67）なので編集は .in 側 | 2 |
| .claude/skills/org-delegate/references/instruction-template.md:3-3 | 「org-broker の send_message で送信」 | transport | 輸送層の名前（renga 運用では mcp__renga-peers__send_message）。spawn-flow.md:278 は手保守で「既定 renga」と書いている | 1 |
| .claude/skills/org-delegate/references/instruction-template.md:4-4 | 権限・報告先等は worker-claude-template.md に一元化 | outdated | brief を作るのは gen_worker_brief.py:414-417 で、tools/templates/worker_brief_*.md を読む<br>**補足**: 一元化先は tools/templates/worker_brief_normal.md / worker_brief_self_edit.md。.in 側で直す<br>**検証**: 反証で維持: gen_worker_brief.py:414-417 が読むのは tools/templates/worker_brief_*.md。編集は .in の 4 行目で行う。 | 1 |
| .claude/skills/org-delegate/references/instruction-template.md:5-12 | 輸送層両系注記（既定 broker / opt-in renga・二フレーム・Epic #6 Issue G） | transport | 3 分岐（受信モデル / spawn 儀式 / エラー分岐）の併記と Refs #604 の二フレーム注記。ack-template.md:5-11、pr-watch-pane/transport-duality.md:5-11 とほぼ同文 | 8 |
| .claude/skills/org-delegate/references/instruction-template.md:13-21 | ultracode タスクでの worker 着手規約（テンプレ外の説明） | move | コードフェンスの外にあり、ワーカーには送られない保守者向けの説明。中身は 61 行と 216 行（ワーカーに届く待機文）、spawn-flow.md:282-300 と重複する<br>**補足**: 規則の正本は spawn-flow.md 3-5a。本節は理由説明として rationale へ移すか、1 行ポインタに縮める<br>**検証**: 反証で維持: コードフェンスの外にある保守者向けの説明。ワーカー向けの規則は 61 行と 216 行、spawn-flow 3-5a にある。ただし spawn-flow.md:300 がこの節を名指しで参照しているので、ポインタは残すこと。 | 9 |
| .claude/skills/org-delegate/references/instruction-template.md:22-58 | 手動テンプレート: タスク・プロジェクトの準備・パターン別・ブランチ戦略 | keep | .dispatcher/CLAUDE.md:29,67（instruction を直接指定する経路）と spawn-flow.md:278 が「このフォーマットに従う」と参照している | 37 |
| .claude/skills/org-delegate/references/instruction-template.md:59-70 | 作業の進め方（ultracode 待機文）・制約・検証深度行 | keep | 検証深度の行を必ず送る規則は delegate-flow-details.md:167-175 と golden fixture の「検証深度:」行が裏付ける。待機文の consumer は spawn-flow.md:299-300 | 12 |
| .claude/skills/org-delegate/references/instruction-template.md:71-71 | Knowledge 層プライバシー | keep | 実 brief に無い（worker_brief_normal.md で operator-private / プライバシー は 0 件）ため、ワーカーに届く唯一の経路。222 行（AUTO）にも同文がある | 1 |
| .claude/skills/org-delegate/references/instruction-template.md:72-77 | full の通常検証・人間向け理解サマリ | keep（当初 delete） | worker_brief_normal.md:110（full の前提）と :190-195（サマリ）に同等の規定がある。brief は全ワーカーが読み込む<br>**補足**: 正本は tools/templates/worker_brief_normal.md:110,190<br>**検証**: 反証で delete→keep: 72 行の『通常の完了報告フォーマット（成果物説明・残作業・PR 草案）』を、ワーカーに届く経路で定めているのはここだけ。brief には無い（:207 の minimal 注記だけ）。73-77 行のサマリは brief:190 と重複しているので、分割すれば delete できる。 | 6 |
| .claude/skills/org-delegate/references/instruction-template.md:78-78 | ultracode は Codex ゲートの前段 | keep | 実 brief の ultracode は Bash 絶対パスの文脈（worker_brief_normal.md:23-25）にしか出てこない。前段という位置づけは指示文でしか届かない | 1 |
| .claude/skills/org-delegate/references/instruction-template.md:79-81 | 追加ゲート（codex review surface）・確認コマンド・未導入 skip | delete | worker_brief_normal.md:108-116 に review surface の実行形と「available なら」の規定がある<br>**補足**: 正本は tools/templates/worker_brief_normal.md:108-116<br>**検証**: 反証で維持: brief:108-116 に review surface と『available なら／未導入なら skip』がある。command -v の確認例は brief に無いが、小さな差なので手順には影響しない。 | 3 |
| .claude/skills/org-delegate/references/instruction-template.md:82-82 | safety block は codex 未導入 skip と別扱い | keep | 実 brief に無い（grep -c '安全機構に掛かって' worker_brief_normal.md = 0）。ワーカーに届くのは指示文だけ | 1 |
| .claude/skills/org-delegate/references/instruction-template.md:83-93 | 空の合格・round 規律・codex:rescue 禁止・実行形・large diff | delete | worker_brief_normal.md:117-189 に同等の規定がある（空の合格 :158-179、round 3 :182、Minor/Nit :186、large diff :187、codex:rescue :188）。83 行は worker-claude-template.md を誤って正本と呼んでいる<br>**補足**: 正本は tools/templates/worker_brief_normal.md:117-189<br>**検証**: 反証で維持: 空の合格は brief:150-179、round 規律・Minor/Nit・large diff・codex:rescue は brief:180-188 にあり、tests/fixtures の golden とも一致する。83 行が worker-claude-template を SoT と呼んでいるのは誤り。 | 11 |
| .claude/skills/org-delegate/references/instruction-template.md:94-94 | 重い codex exec を背景実行（tasklist 完了検知） | outdated | worker_brief_normal.md:42 は「ネットワークに出るコマンドは背景実行で流さない（2026-09-20）」と規定し、codex exec はネットワークに出る。tasklist は Windows 専用で、Linux ワーカーでは動かない<br>**補足**: brief:42 に従い前景で実行する。重いデザインレビューは窓口側の手順（codex-design-review.md）で扱う<br>**検証**: 反証で維持: brief:42 は、ネットワークに出るコマンドを背景実行しないよう定めている。codex exec はネットワークに出る。tasklist は Windows 専用。 | 1 |
| .claude/skills/org-delegate/references/instruction-template.md:95-103 | minimal 手順 | keep（当初 delete） | worker_brief_normal.md:197-210 に同等の規定がある（BEGIN:codex_minimal ブロック）<br>**補足**: 正本は tools/templates/worker_brief_normal.md:197-210<br>**検証**: 反証で delete→keep: brief の minimal ブロックは depth=minimal のときしか出力されない（gen_worker_brief.py:330-331）。org-pull-request SKILL.md:242 は、full ワーカーへの minimal 追指示の書式を instruction-template に従わせている。その経路では brief に minimal の規定が無いので、ここが唯一の出典になる。 | 9 |
| .claude/skills/org-delegate/references/instruction-template.md:104-107 | 選択は窓口の責任（ワーカーは勝手に full にフォールバックしない） | keep | 検証深度の値の扱いを決める規則。delegate-flow-details.md:173-175 の snapshot 固定と対になる | 4 |
| .claude/skills/org-delegate/references/instruction-template.md:108-120 | cross-cutting 変更の consistency grep（判定例） | keep | 窓口が brief を組むときの手順。導入は a266d07（初回公開）で、再発記録は見つからないが念押しではなく手順なので keep | 13 |
| .claude/skills/org-delegate/references/instruction-template.md:121-155 | 推奨 grep target ディレクトリ・指示例 | keep | 列挙された .claude/ registry/ knowledge/curated/ dashboard/ .dispatcher/ .curator/ .hooks/ docs/ tools/ tests/ は ls で全て現存 | 35 |
| .claude/skills/org-delegate/references/instruction-template.md:156-165 | 監視ロール待ち合わせ設計の brief 必須文言 | keep | codex-design-review.md:15,40 から参照されている。worker-claude-template.md:376-388 の重複は delete とし、こちらを正本とする | 10 |
| .claude/skills/org-delegate/references/instruction-template.md:166-175 | doc-audit 専用: chunk 転送方式 | keep | 実例は 2cdebff（2026-05-03 readme-drift-audit）。doc-audit に Write/Edit の allow が無いことは org_extension_schema.json の worker_roles.doc-audit.description で確認。172 行の mcp__org-broker__send_message は輸送層の名前 | 10 |
| .claude/skills/org-delegate/references/instruction-template.md:176-180 | 使用時の注意（具体的に・制約明示） | delete | 一般的な念押しで、出典も再発記録も無い（a266d07 から変わっていない）。手順に影響しない<br>**補足**: なし（削除）<br>**検証**: 反証で維持: 一般的な念押しで、出典も再発記録も無い。tests/ と tools/ に参照は無い。 | 5 |
| .claude/skills/org-delegate/references/instruction-template.md:181-196 | 自動展開テンプレートの説明・変数一覧 | keep | site-packages/claude_org_runtime/dispatcher/runner.py:2189 に load_instruction_template がある。runtime 0.1.49 が導入済み（pip show） | 16 |
| .claude/skills/org-delegate/references/instruction-template.md:197-231 | AUTO-EXPAND-TEMPLATE 本体 | keep | runner.py:499-500 がこのマーカーで切り出す。ただし 222 行は 1 行に brief と重複する Codex 規則を詰め込み、worker-claude-template.md を指している（judgments に記載）。226 行の「org-broker で」は輸送層の名前 | 35 |
| .claude/skills/org-delegate/references/instruction-template.md:232-233 | マーカー位置を変更しない注記 | keep | runner.py:2205 はマーカーが無いと例外を投げる | 2 |

### .claude/skills/org-delegate/references/ack-template.md（78 行: keep 56 / move 5 / delete 7 / outdated 2 / transport 8）

| 行範囲 | 節 | 分類 | 根拠 | 行数 |
|---|---|---|---|---|
| .claude/skills/org-delegate/references/ack-template.md:1-3 | タイトル・用途 | keep | SKILL.md:40 が single SoT と明記。生成物（manifest.json:60-61）なので編集は .in 側 | 3 |
| .claude/skills/org-delegate/references/ack-template.md:4-11 | 輸送層両系注記 | transport | instruction-template.md:6-12 と同文の 3 分岐併記・二フレーム注記・Epic #6 Issue G の但し書き | 8 |
| .claude/skills/org-delegate/references/ack-template.md:12-14 | なぜ ack が必須か（見出し） | move | 理由説明の節 | 3 |
| .claude/skills/org-delegate/references/ack-template.md:15-15 | worker-claude-template が「ペイン保持。次の指示お待ちします」を書かせている | outdated | git grep '次の指示お待ちします' -- tools/templates と worker-claude-template.md の結果は 0 件。どのテンプレートもこの文言を書かせていない<br>**補足**: 実 brief（worker_brief_normal.md:228）は「PR 作成後はペインを保持し、明示のクローズ指示まで待機」とだけ書く。ack を必須とする根拠はそちらで言い直す<br>**検証**: 反証で維持: git grep '次の指示お待ちします' のヒットは ack-template(.in) と CLAUDE.md:102 だけで、どのテンプレートも書かせていない。CLAUDE.md:102 も同じ古い記述を持つ。編集は .in の 9 行目で行う。 | 1 |
| .claude/skills/org-delegate/references/ack-template.md:16-17 | dead-lock の理由・session #13 の観測 | move | 事故の経緯（598bea5、2026-05-06 #314）<br>**補足**: references/rationale.md へ<br>**検証**: 反証で維持: dead-lock の理由と session #13 の観測は経緯の説明。ack を必須とする手順は SKILL.md の canonical flow step 1（:313）にある。 | 2 |
| .claude/skills/org-delegate/references/ack-template.md:18-18 | ack ≠ user 承認 | delete | SKILL.md:321 と CLAUDE.md:102 に同じ規則がある<br>**補足**: 正本は SKILL.md:321<br>**検証**: 反証で維持: SKILL.md:321 と CLAUDE.md:102 に同文の『ack ≠ user 承認』がある。 | 1 |
| .claude/skills/org-delegate/references/ack-template.md:19-36 | ack の最低 3 要素・進捗報告 ack 例文 | keep | SKILL.md:40,321,332 が参照する single SoT | 18 |
| .claude/skills/org-delegate/references/ack-template.md:37-37 | 進捗 ack 後の worker_reported 記帳 | delete | SKILL.md:328-338 と同じ（適用範囲・journal_append コマンド・Issue #699）<br>**補足**: 正本は SKILL.md:328-338<br>**検証**: 反証で維持: SKILL.md:328-338 が、適用範囲（rebase 報告・確認依頼を含む）・journal_append コマンド・Issue #699 をすべて持つ。 | 1 |
| .claude/skills/org-delegate/references/ack-template.md:38-58 | 完了報告 ack・Codex round 完了 ack | keep | SKILL.md:342 が参照する例文。round 上限 3 は brief:182 と一致する | 21 |
| .claude/skills/org-delegate/references/ack-template.md:59-67 | 判断仰ぎ ack | keep | org-escalation の一次対応（「受領しました、人間に確認します」のみ、CLAUDE.md:135）と一致する | 9 |
| .claude/skills/org-delegate/references/ack-template.md:68-68 | 判断仰ぎは SKILL.md Step 5 subsection 0 に従い… | outdated | SKILL.md:324-326 の subsection 0 は org-escalation へのポインタだけになっている（Issue #320 で切り出し、CLAUDE.md:137）<br>**補足**: 手順の正本は .claude/skills/org-escalation/SKILL.md。.in 側で直す<br>**検証**: 反証で維持: SKILL.md:324-326 の subsection 0 は org-escalation へのポインタだけ。worker_escalation と pending_decisions の手順は org-escalation SKILL.md にある（grep で 9 件）。 | 1 |
| .claude/skills/org-delegate/references/ack-template.md:69-73 | ブロック報告 ack | keep | 判断仰ぎ ack の派生 | 5 |
| .claude/skills/org-delegate/references/ack-template.md:74-78 | アンチパターン 3 件 | delete | 18-25 行と SKILL.md:310-322 を裏返して言い直しただけ。git log --since=2026-07-01 -i --grep=ack に ack 漏れの再発記録は無い<br>**補足**: 正本は SKILL.md:310-322（canonical event flow）<br>**検証**: 反証で維持: 3 件とも SKILL.md の内容を言い直しただけ。:313 の flow step 1、:321 の ack≠承認、2a の『受信直後・他の状態更新より前』。 | 5 |

### .claude/skills/org-delegate/references/transport-duality.md（11 行: keep 0 / move 0 / delete 0 / outdated 0 / transport 11）

| 行範囲 | 節 | 分類 | 根拠 | 行数 |
|---|---|---|---|---|
| .claude/skills/org-delegate/references/transport-duality.md:1-11 | org-delegate 輸送層両系の読み替え（全文） | transport | ファイル全体が broker / renga の読み替え、push / pull、spawn 儀式、エラーコード、Epic #6 Issue G の但し書き。生成物（manifest.json:114-115） | 11 |

### .claude/skills/org-pull-request/references/transport-duality.md（7 行: keep 0 / move 0 / delete 0 / outdated 3 / transport 4）

| 行範囲 | 節 | 分類 | 根拠 | 行数 |
|---|---|---|---|---|
| .claude/skills/org-pull-request/references/transport-duality.md:1-4 | org-pull-request 輸送層両系（導入） | transport | 移設元の説明と読み替えの SoT 宣言。生成物（manifest.json:108-109） | 4 |
| .claude/skills/org-pull-request/references/transport-duality.md:5-7 | notify_peer は raw env 判定で無設定時は renga にフォールバック | outdated | tools/peer_notify.py:85-136 は _resolve_transport() で tools.transport.resolve を呼ぶ（58d8eda、#945）。CLAUDE.md:38 も Refs #941 で解消済みとしている<br>**補足**: notify_peer はコード既定フレーム（無設定なら broker）で動く。raw env 判定は runtime 未導入時の never-raise フォールバックとしてだけ残る（peer_notify.py:41-45）。.in 側で直す<br>**検証**: 反証で維持: peer_notify.py の _resolve_transport は tools.transport.resolve を呼ぶ（58d8eda #945）。raw env 判定は runtime 未導入時の fallback として残るだけ（:41-45）。CLAUDE.md:38 は『Refs #941 で解消済み』と書く。編集は .in 側で行う。 | 3 |

### .claude/skills/pr-watch-pane/references/transport-duality.md（11 行: keep 0 / move 0 / delete 0 / outdated 0 / transport 11）

| 行範囲 | 節 | 分類 | 根拠 | 行数 |
|---|---|---|---|---|
| .claude/skills/pr-watch-pane/references/transport-duality.md:1-11 | pr-watch-pane 輸送層両系の読み替え（全文） | transport | instruction-template.md:6-12 とほぼ同文の 3 分岐・二フレーム・Epic #6 Issue G。生成物（manifest.json:102-103） | 11 |

### .claude/skills/org-delegate/references/rationale.md（31 行: keep 31 / move 0 / delete 0 / outdated 0 / transport 0）

| 行範囲 | 節 | 分類 | 根拠 | 行数 |
|---|---|---|---|---|
| .claude/skills/org-delegate/references/rationale.md:1-31 | 規律の由来（§1-§7） | keep | move の受け皿として既にある（b9eca49、2026-09-12 #1010）。SKILL.md:118,119,130,143,294,338,306 から参照されている | 31 |

### .claude/skills/org-delegate/references/release-pre-fetch.md（49 行: keep 19 / move 19 / delete 9 / outdated 2 / transport 0）

| 行範囲 | 節 | 分類 | 根拠 | 行数 |
|---|---|---|---|---|
| .claude/skills/org-delegate/references/release-pre-fetch.md:1-4 | 見出し・一次参照元 | keep | SKILL.md:180 から参照されている | 4 |
| .claude/skills/org-delegate/references/release-pre-fetch.md:5-5 | worker の permissions.deny に git fetch / pull / remote update | outdated | tools/org_extension_schema.json:729 で worker_roles.default.permissions.allow に Bash(git fetch:*) がある。deny は :746 の git pull だけ<br>**補足**: worker は fetch できるが pull（local main の ff）はできない、と書く。根拠は :729 と :746<br>**検証**: 反証で維持: schema:729 で git fetch が allow に入っている。deny は :744-745 の『git -C * fetch』と :746-749 の pull で、remote update は deny に無い。正しくは『素の fetch は可、-C fetch と pull は不可』。 | 1 |
| .claude/skills/org-delegate/references/release-pre-fetch.md:6-16 | 窓口側 fetch + ff のコマンド | keep | 窓口が実行する手順。git fetch:* / git pull:* は roles.secretary.required_allow にある（schema の walk 結果） | 11 |
| .claude/skills/org-delegate/references/release-pre-fetch.md:17-25 | 適用条件 3 項目 | delete | SKILL.md:175-178 と同じ 3 条件。SKILL 側に「本体に残すのは省略禁止」とあるので、本体を正本とする<br>**補足**: 正本は SKILL.md:174-178<br>**検証**: 反証で維持: SKILL.md:176-178 と同じ 3 条件。SKILL 側が本体に残すことを明言している。 | 9 |
| .claude/skills/org-delegate/references/release-pre-fetch.md:26-26 | worker permissions deny は意図的設計 | outdated | git fetch は worker に許可されている（schema:729）<br>**補足**: 「worker は pull / ff できない（schema:746）」と言い直す<br>**検証**: 反証で維持: fetch は allow（schema:729）に入っている。『26 行の 1 文目（通常タスクでは実行しない）は適用条件の一部なので、SKILL 側へ寄せること。 | 1 |
| .claude/skills/org-delegate/references/release-pre-fetch.md:27-45 | Issue #480 との関係（Pattern B は apply が自動 fetch） | move | 適用範囲の説明で、手順は 6-16 行で完結する。fetch が許可された今は、Pattern A でも worker が fetch してから origin/main を起点に切れるため、Step 0.6 が必要かどうか自体が判断事項（judgments に記載）<br>**補足**: rationale へ移すか、Step 0.6 を見直すときに一緒に整理する<br>**検証**: 反証で維持: 適用範囲の説明。手順は 6-16 行で完結する。fetch が許可された今、Step 0.6 が必要かどうかは人間が判断する事項。 | 19 |
| .claude/skills/org-delegate/references/release-pre-fetch.md:46-49 | 背景・経緯ポインタ | keep | knowledge/curated/release-process.md へのポインタ（1 行） | 4 |

### .claude/skills/org-delegate/references/codex-design-review.md（59 行: keep 47 / move 2 / delete 7 / outdated 3 / transport 0）

| 行範囲 | 節 | 分類 | 根拠 | 行数 |
|---|---|---|---|---|
| .claude/skills/org-delegate/references/codex-design-review.md:1-16 | 見出し・トリガー条件表（5 条件） | keep | SKILL.md:264 から一次参照されている。監視ロール条件（15 行）は SKILL 側に抜けている（SKILL.md:254-264 を outdated とした） | 16 |
| .claude/skills/org-delegate/references/codex-design-review.md:17-20 | exec プロンプト形を維持する理由 | delete | knowledge/curated/codex.md:52 に同じ理由（diff が無いので review surface を使えず、breadth が要るので exec 形）がある<br>**補足**: 正本は knowledge/curated/codex.md:52。本文には「diff が無いので codex exec のプロンプト形を使う」の 1 行だけ残す<br>**検証**: 反証で維持: knowledge/curated/codex.md:50-52 が同じ理由（diff が無い → exec 形、breadth）を持つ。『exec プロンプト形を使う』の 1 行は残す。 | 4 |
| .claude/skills/org-delegate/references/codex-design-review.md:21-28 | codex exec コマンド | keep | 窓口が実行するコマンド。-m gpt-6-astra は brief:188 と一致する | 8 |
| .claude/skills/org-delegate/references/codex-design-review.md:29-31 | codex:rescue 禁止（CLAUDE.local.md の禁止事項）・モデル名・cli 版 | outdated | root の CLAUDE.local.md は追跡外（git ls-files CLAUDE.local.md の出力が空）。codex:rescue 禁止は tools/templates/worker_brief_normal.md:188 と knowledge/curated/codex.md:48 にある<br>**補足**: 出典を knowledge/curated/codex.md:48 に直す。モデル名と 0.153.4 以上の記述は窓口も使うので残す<br>**検証**: 反証で維持: git ls-files CLAUDE.local.md の出力は空（root では追跡外）。codex:rescue 禁止の正本は knowledge/curated/codex.md:48 と brief:188。モデル名と CLI の版は残す。 | 3 |
| .claude/skills/org-delegate/references/codex-design-review.md:32-47 | 監視ロールの追加 3 問・リリースライン判定観点 | keep | instruction-template.md:157 の必須文言と対になる。リリースライン観点の実例は renga PR #325 / Issue #306 | 16 |
| .claude/skills/org-delegate/references/codex-design-review.md:48-49 | リリースライン観点の由来（renga #325） | move | 事故の経緯<br>**補足**: references/rationale.md へ<br>**検証**: 反証で維持: 48 行は renga #325 の経緯。手順にあたる追記観点は 44-46 行にあり、そちらは残る。 | 2 |
| .claude/skills/org-delegate/references/codex-design-review.md:50-56 | review 要約の組み込み | keep | --impl-guidance と --knowledge は gen_delegate_payload.py:2274-2275。tmp/ は .gitignore:117 で ignore されている | 7 |
| .claude/skills/org-delegate/references/codex-design-review.md:57-59 | helper script（本 PR では実装しない） | delete | 導入時の PR 単位の注記。git grep -ln 'design.review' -- 'tools/*.py' は check_curate_threshold.py / pr_watch.py だけで、design review の helper は無い。手順には影響しない<br>**補足**: なし（削除）<br>**検証**: 反証で維持: 『本 PR では実装しない』は導入時の PR 単位の注記。design review の helper は tools/ に無い。 | 3 |

### .claude/skills/org-delegate/references/renga-error-codes.md（396 行: keep 228 / move 13 / delete 128 / outdated 2 / transport 25）

| 行範囲 | 節 | 分類 | 根拠 | 行数 |
|---|---|---|---|---|
| .claude/skills/org-delegate/references/renga-error-codes.md:1-4 | タイトル・code で分岐する推奨 | keep | consumer: git grep -l renga-error-codes で SKILL.md/.dispatcher/CLAUDE.md/pane-close.md/spawn-flow.md 等 40 件ヒット。code 分岐は下の各節が担う | 4 |
| .claude/skills/org-delegate/references/renga-error-codes.md:5-5 | 版数の読み分け（1.x 系 / 2.0 系）注記 | outdated | 本文は「既定の前提は renga 0.14.0+（1.x 系）」とし Known codes 表をこの世代基準で読ませるが、tools/check_renga_compat.py:113 MIN_REQUIRED_VERSION=(2,0,0)、pane-layout.md:30 / .dispatcher/CLAUDE.md:151「サポート下限は 2.0.0」(#847, 2026-08-07)<br>**補足**: 前提は renga 2.0.0+（tools/check_renga_compat.py:113）。capability を 4 トークン個別判定する規則と server_too_old を世代証拠にしない規則は残し、1.x 既定の枠組みを外す<br>**検証**: 反証で維持: tools/check_renga_compat.py:113 MIN_REQUIRED_VERSION=(2,0,0)（直前コメント「2.0.0 is a breaking operational floor」）。pane-layout.md:30 と .dispatcher/CLAUDE.md:151 にも「サポート下限は 2.0.0」とある。本文の「既定の前提は renga 0.14.0+（1.x 系）」はこれと食い違う。4 トークンを個別に判定する規則と、server_too_old を世代の証拠にしない規則は残す。 | 1 |
| .claude/skills/org-delegate/references/renga-error-codes.md:6-17 | Wire format | keep | [<code>] 形式の分岐は spawn-flow.md:8 / pane-close.md:78 等が [pane_not_found] 等で分岐（git grep -n '\[pane_not_found\]' .dispatcher で多数） | 12 |
| .claude/skills/org-delegate/references/renga-error-codes.md:18-21 | Known codes 表ヘッダ | keep | 表本体の見出し | 4 |
| .claude/skills/org-delegate/references/renga-error-codes.md:22-22 | pane_not_found 行 | keep | pane 制御/messaging 文脈分岐は pane-close.md:78,110 と worker brief テンプレ(tools/templates/worker_brief_normal.md)が参照。行内の「注意（1.x 系）」は下限 2.0.0 (check_renga_compat.py:113) で歴史記述化しており削れる | 1 |
| .claude/skills/org-delegate/references/renga-error-codes.md:23-24 | pane_vanished / last_pane 行 | keep | last_pane は org-suspend が参照(git grep last_pane .claude/skills/org-suspend)。2.0 実装確認は commit 8ad6263 (#885)。24 行の上流ソース行番号列挙は rationale へ寄せられる | 2 |
| .claude/skills/org-delegate/references/renga-error-codes.md:25-25 | split_refused 行 | outdated | 「キュレーター → 窓口に escalate」とあるが curator は常駐せずオンデマンド(CLAUDE.md 起動時節)、capacity 枯渇は helper の escalate.to_id を verbatim 送信(.dispatcher/CLAUDE.md:86, spawn-flow.md:59)<br>**補足**: ディスパッチャーが delegate-plan helper の escalate（to_id=secretary）をそのまま窓口へ送る（.dispatcher/CLAUDE.md:86）<br>**検証**: 反証で維持: 25 行に「キュレーター → 窓口に escalate」とあるが、CLAUDE.md:17 では curator は常駐しない。.dispatcher/CLAUDE.md:86 と spawn-flow.md:55 では、helper の escalate.to_id/message をディスパッチャーがそのまま窓口へ転送する。指摘どおり。 | 1 |
| .claude/skills/org-delegate/references/renga-error-codes.md:26-35 | cwd_invalid〜internal 行 | keep | name_in_use は org-start Step 0 が使用、shutting_down の FOREMAN_STOPPING は worker-monitoring.md が参照(git grep FOREMAN_STOPPING)。IO_ERROR_DETECTED / IPC_PROTOCOL_ERROR は他に consumer 0 件(git grep) | 10 |
| .claude/skills/org-delegate/references/renga-error-codes.md:36-41 | renga 2.0 系 適用範囲 | keep | server_too_old が 1.x 接続時に出る注意は check_renga_compat.py の floor 判定と整合。2.0.0 リリース日記述は事実(commit e16d28e 2026-08-07) | 6 |
| .claude/skills/org-delegate/references/renga-error-codes.md:42-52 | 2.0 系 capability 表 | keep | 4 トークンを check_renga_compat.py(3b server_info probe)が照会。セッション中の renga-peers MCP が list_peers 全タブ列挙を提供(MCP server instructions) | 11 |
| .claude/skills/org-delegate/references/renga-error-codes.md:53-61 | Group A / Group B 別 gate | keep | Group B 数値 pane_id 化は commit 59cf3af (#896, 2026-08-09)。pane-close.md:110 が同規則で close | 9 |
| .claude/skills/org-delegate/references/renga-error-codes.md:62-69 | Group B MUST / stale-binding carve-out / caller pane id 規則 | keep | carve-out の適用先 pr-watch-pane / org-pull-request が実在。RENGA_PANE_ID は org-start / dispatcher-resume / secretary-resume が使用(git grep RENGA_PANE_ID 6 ファイル) | 8 |
| .claude/skills/org-delegate/references/renga-error-codes.md:70-70 | なぜ MUST ではなく SHOULD だったか（2026-08-07 記録） | move | 段落自身が「2026-08-07 の決定の記録として残してあり」「いま効いている規範は上の MUST」と明言。手順として不要。正本は契約 T-§4.2 recorded decision<br>**検証**: 反証で維持: 70 行は「当時の記録」と自分で書いている格下げの経緯で、件数の正本は契約 T-§4.2 の ledger にあると明記している。現行規範は 62/68 行の MUST にある。tests/tools からこの段落を参照する箇所は無かった（git grep 'renga-error-codes' -- tests tools は messaging 分岐の見出しとポインタのみ）。 | 1 |
| .claude/skills/org-delegate/references/renga-error-codes.md:71-73 | #290 世代 server 注記 | keep | 「3 トークン揃っても安全でない」は Group B gate の判断材料。1 行 | 3 |
| .claude/skills/org-delegate/references/renga-error-codes.md:74-83 | 2.0 系で追加されたコード表 | keep | tab 系コードは delegate-plan helper の復旧文言が扱う(capability-first-drive...md:584-586)。server_too_old は check_renga_compat の floor と対 | 10 |
| .claude/skills/org-delegate/references/renga-error-codes.md:84-97 | server_too_old 復旧 clause (a)/(b) | keep | `claude mcp list` の絶対パス probe は check_renga_compat.py:25,408,457 が実装（PATH-first binary の取り違え実例 2026-08 記載） | 14 |
| .claude/skills/org-delegate/references/renga-error-codes.md:98-108 | pane_not_found messaging 分岐 冒頭とポインタ | keep | tests/test_transport_seam.py:176-195 が見出しと capability-first-drive へのポインタを pin。worker brief(CLAUDE.md 完了報告節)が本節を正本として参照 | 11 |
| .claude/skills/org-delegate/references/renga-error-codes.md:109-144 | messaging 復旧手順本体と注記 | keep | consumer: worker brief テンプレ tools/templates/worker_brief_normal.md、org-suspend Phase 4、pane-close.md:78。status 書き手規定は docs/contracts/state-semantics-contract.md 実在。142 の 1.x 注記は下限 2.0.0 で不要化 | 36 |
| .claude/skills/org-delegate/references/renga-error-codes.md:145-148 | broker（ORG_TRANSPORT=broker）との関係 | transport | broker/renga 二系の差分説明（broker は cross_tab_peers 非広告） | 4 |
| .claude/skills/org-delegate/references/renga-error-codes.md:149-152 | 契約側の正本 | move | 1 段落の大半が amendment 批准状況(2026-08-09 RATIFIED)と gate 未記録の経緯説明。運用規範は capability-first-drive...md と本ファイル上部に既出。正本ポインタ 1 行だけ残せば足りる<br>**検証**: 反証で維持: 151 行の中身は契約 amendment の批准状況と capability 条件の要約。同じ規則は 147 行の broker 節と 40-97 行の 2.0 節に既にある。正本へのリンク 1 行を残せば手順は失われない。 | 4 |
| .claude/skills/org-delegate/references/renga-error-codes.md:153-173 | Broker 追加コードとツール名射影 | transport | 155 が「既定 renga（ORG_TRANSPORT 無設定）」と書くが tools/transport.py:19-20 は DEFAULT_TRANSPORT=broker（runtime 0.1.28 でフリップ）。broker 固有コード表・push 一次注記(172)を含む二系記述 | 21 |
| .claude/skills/org-delegate/references/renga-error-codes.md:174-185 | MCP ツール特有の ok-return ルール | keep | (no peers / (message dropped 接頭辞の分岐。renga-peers MCP の実挙動の記述。2.0 fail-loud 化(#289)との切り分けも有効 | 12 |
| .claude/skills/org-delegate/references/renga-error-codes.md:186-313 | シェル側のハンドリング例（擬似 case 文） | delete | 呼ばれる関数 mark_worker_pane_closed / reresolve_peer_id 等は git grep で本ファイル以外 0 件=実体なし。分岐内容は Known codes 表(22-34)・2.0 コード表(76-82)・messaging 手順(109-143)の再掲。127 行<br>**補足**: 正本は同ファイルの Known codes 表 18-35 / 2.0 コード表 74-97 / messaging 分岐 98-144<br>**検証**: 反証で維持: mark_worker_pane_closed / reresolve_peer_id は git grep で本ファイル以外 0 件で、実行されない擬似コード。分岐の中身（pane_ctl の same_tab true/false/不明、server_too_old の clause a/b、tab 系 4 コード）は 22 行の例外 2 つ、78-96 行、109-143 行と一致する。tests の参照（test_transport_seam.py:185-192）は messaging 見出しとポインタだけで、本節は対象外。 | 128 |
| .claude/skills/org-delegate/references/renga-error-codes.md:314-321 | なぜ code か、substring ではなく | move | 設計理由と外部依存(renga ソース)の列挙。手順ではない<br>**検証**: 反証で維持: 316-321 行は設計理由と外部ソースの列挙。範囲外の 322 行（未知の code は非致命扱い・デフォルト分岐必須）は手順なので、移すときは 322 行を本体に残すこと。 | 8 |
| .claude/skills/org-delegate/references/renga-error-codes.md:322-323 | 未知 code は非致命・default branch 必須 | keep | broker 節 155 も「未知コードは default-branch」と依拠。規則 1 行 | 2 |
| .claude/skills/org-delegate/references/renga-error-codes.md:324-339 | Event stream poll_events と突合キー規則 | keep | poll_events は org-suspend(8)/dispatcher-resume(3)/org-start(2) 等が使用(git grep -c poll_events)。events_dropped は worker-monitoring.md が参照 | 16 |
| .claude/skills/org-delegate/references/renga-error-codes.md:340-356 | type 別扱い / types フィルタ / 初回呼び出し | keep | worker-monitoring.md の監視ループが前提。355 の「旧 renga events --timeout と同じ」と 351 の renga PR #120 参照は履歴（他 consumer 0 件）で削れる | 17 |
| .claude/skills/org-delegate/references/renga-error-codes.md:357-396 | Raw キー入力 send_keys | keep | send_keys は permissions.md:38,144 で許可、2 段打鍵は spawn-flow.md 3-5a と tools/self_edit_approval.py が実装。396 は SoT(spawn-flow 3-5a)へのポインタ付き要約 | 40 |

### .claude/skills/org-delegate/references/capability-first-drive-operational-gate.md（599 行: keep 458 / move 120 / delete 0 / outdated 18 / transport 3）

| 行範囲 | 節 | 分類 | 根拠 | 行数 |
|---|---|---|---|---|
| .claude/skills/org-delegate/references/capability-first-drive-operational-gate.md:1-7 | タイトル・Read 義務 | keep | call site: org-suspend / org-start / secretary-resume / dispatcher-resume / spawn-flow / pane-close / worker-monitoring / org-attach が本ファイル名を参照(git grep -l 19 件)。tools/capability_gate.py:7 も consumer として明記 | 7 |
| .claude/skills/org-delegate/references/capability-first-drive-operational-gate.md:8-21 | 3 ゲートの区別・ファイル名の理由 | move | 契約 T-§ratification の引用と命名理由の説明。手順は §1 以降<br>**検証**: 反証で維持: 8-21 行は契約 T-§ratification の 3 ゲートの引用と、ファイル名の理由。実行手順は §1 以降にある。tests/test_transport_seam.py:192 が参照するのはファイル名だけ。 | 14 |
| .claude/skills/org-delegate/references/capability-first-drive-operational-gate.md:22-24 | 輸送層両系の読み替え注記 | transport | mcp__renga-peers__* / mcp__org-broker__* 読み替え注記 | 3 |
| .claude/skills/org-delegate/references/capability-first-drive-operational-gate.md:25-30 | 「現在配備の backend で本ゲートが発火するものは無い」 | outdated | 「renga も 2.0.0 を実際に挿すまでは旧版形」とあるが下限は 2.0.0(check_renga_compat.py:113, #847 2026-08-07)、2.0 実機 dogfood 済み(commit 49876aa 本文 2026-08-09)。2.0 の list_peers は same_tab を載せる(renga-error-codes.md:48)<br>**補足**: 運用中の renga(>=2.0.0)では list_peers が capability 形になり本ゲートが発火しうる。非発火は org-broker のみ。推測: 実運用 renga の列挙は marker 付き<br>**検証**: 反証で維持: check_renga_compat.py:113 の下限は 2.0.0。commit 49876aa（2026-08-10, #918）の本文に 2026-08-09 の背景タブ placement 実走と批准が記録されている。renga-error-codes.md:48 では、cross_tab_peers がある場合の PeerInfo は same_tab を載せる。「renga も 2.0.0 を実際に挿すまでは旧版形」は事実と合わない。 | 6 |
| .claude/skills/org-delegate/references/capability-first-drive-operational-gate.md:31-39 | §1 版判定表 | keep | tests/test_transport_seam.py と各 call site が本判定を前提。same_tab/tab marker は renga 2.0 PeerInfo(renga-error-codes.md:48) | 9 |
| .claude/skills/org-delegate/references/capability-first-drive-operational-gate.md:40-43 | 旧版 fallback は全配備 backend の経路 | outdated | 25-27 と同じ前提。renga 下限 2.0.0(check_renga_compat.py:113)で renga は旧版 fallback に当たらない<br>**補足**: 旧版 fallback を通るのは org-broker（capability 非広告、契約 T-§cap）だけ<br>**検証**: 反証で維持: 25 行と同じ理由（renga の下限は 2.0.0, check_renga_compat.py:113）。旧版 fallback を通るのは、capability を広告しない org-broker だけ。 | 4 |
| .claude/skills/org-delegate/references/capability-first-drive-operational-gate.md:44-45 | §1-1 見出し・導入 | keep | 規則見出し | 2 |
| .claude/skills/org-delegate/references/capability-first-drive-operational-gate.md:46-57 | §1-1 契約条文の英文引用 | move | 契約 T-§cap / fail-safe precedence の逐語引用。規則自体は 58-78 に再述されている<br>**検証**: 反証で維持: 46-57 行は契約の英文の逐語引用。58-78 行が同じ規則を日本語で再述している（非依拠の 3 項目、旧版形への読み替え禁止）。手順は 58 行以降で足りる。 | 12 |
| .claude/skills/org-delegate/references/capability-first-drive-operational-gate.md:58-79 | §1-1 解釈と行動の分離・non-reliance 3 項 | keep | §3-B / §6 縮退先が依拠する定義。commit 87a39be (#879) で全 8 経路に配線 | 22 |
| .claude/skills/org-delegate/references/capability-first-drive-operational-gate.md:80-89 | §1-2 三値判定の正本宣言 | keep | commit 98735cf (#890) で org-start / dispatcher-resume / secretary-resume の same_tab ガードの正本として新設 | 10 |
| .claude/skills/org-delegate/references/capability-first-drive-operational-gate.md:90-103 | §1-2-a 評価順 段 1-4 | keep | capability_gate.py --gate first_drive の exit 0/1/2 で分岐（tools/capability_gate.py:25-36） | 14 |
| .claude/skills/org-delegate/references/capability-first-drive-operational-gate.md:104-111 | §1-2-a 段 5（現在配備の全 backend は旧版 fallback） | outdated | 段 5 の「現在配備の全 backend — org-broker を含む — はここ」は renga 下限 2.0.0(check_renga_compat.py:113)と食い違う。段の規則自体は有効<br>**補足**: 段 5 を通るのは非広告 backend(org-broker)のみ。renga 2.0+ は段 3/4<br>**検証**: 反証で維持: 104/107 行の「現在配備の全 backend」は renga の下限 2.0.0 と合わない。食い違うのは括弧内の記述だけ。110 行の MUST NOT（評価順の正本）と段 5 の規則は残す。 | 8 |
| .claude/skills/org-delegate/references/capability-first-drive-operational-gate.md:112-148 | §1-2-b 真理値表と行 5 の制約 | keep | org-suspend/SKILL.md の宛先選定真理値表と往復参照。背景タブ誤退役防止 commit ad33c84 (#906) | 37 |
| .claude/skills/org-delegate/references/capability-first-drive-operational-gate.md:149-156 | unknown で禁じられること | keep | 誤 WORKER_PANE_EXITED 防止。WORKER_PANE_EXITED は pane-close.md / worker-monitoring.md / dispatcher-resume が発行(git grep 6 ファイル) | 8 |
| .claude/skills/org-delegate/references/capability-first-drive-operational-gate.md:157-164 | org-suspend ‡ との対応注 | move | 2 文書間の字面差の説明（「矛盾しているのは字面だけ」）。読み手向け注で手順ではない<br>**検証**: 反証で維持: 157-164 行は org-suspend との字面の差を説明する注。新しい手順は含まない（161 行のコピー禁止は文書保守者向け）。 | 8 |
| .claude/skills/org-delegate/references/capability-first-drive-operational-gate.md:165-216 | §1-2-c 集約表・不在の licence・登録待ち・宛先数値 id | keep | spawn-flow.md 3-4 / pane-close.md 5-4 の per-site 適用行が本節を参照(本ファイル 576-582)。commit d83ab9d (#897) | 52 |
| .claude/skills/org-delegate/references/capability-first-drive-operational-gate.md:217-243 | §1-2-d 自 identity を名前で確定しない | keep | RENGA_PANE_ID 起点の照合は dispatcher-resume / secretary-resume / org-start が実施(git grep RENGA_PANE_ID) | 27 |
| .claude/skills/org-delegate/references/capability-first-drive-operational-gate.md:244-258 | §1-2-d 導出表（peer_id 未確定 4 分岐） | keep（当初 move） | 段落冒頭が「導出」と明示。上の規則から導かれる帰結の説明で、call site は §6 表で縮退先を読む<br>**検証**: 反証で move→keep: 253-257 行は手順を含む: DB 更新に渡すのは照合済みの caller pane id だけ、resume 系は保持、起動系は明示クリア、推測値は書かない。dispatcher-resume/SKILL.md:169,249 がこの §1-2-d を「詳細は」として参照しており、§6 の表 #2b/#4（553,555 行）も §1-2-d を参照する。移すと参照先が消える。 | 15 |
| .claude/skills/org-delegate/references/capability-first-drive-operational-gate.md:259-274 | §1-2-e call site 側に書く形 | move | 文書保守者向けの書き方規約（実行時の手順ではない）。docs/contributing/ 側が置き場候補<br>**検証**: 反証で維持: 261-271 行は call site 側に書く文面の規約で、文書保守者向け。git grep '1-2-e' で他のファイルからの参照は 0 件。581 行が言及しているのは同じファイル内。 | 16 |
| .claude/skills/org-delegate/references/capability-first-drive-operational-gate.md:275-296 | §2 記録の照会と exit 表 | keep | tools/capability_gate.py 実在、exit 0/1/2 = recorded/not_recorded/undetermined(capability_gate.py:73-76) | 22 |
| .claude/skills/org-delegate/references/capability-first-drive-operational-gate.md:297-312 | §2 インライン python を使わない理由 | move | cwd 依存・LIKE 部分一致の理由は tools/capability_gate.py:9-19 の docstring と同内容。規則は「capability_gate.py を使う」1 行で足りる<br>**検証**: 反証で維持: 298-304 行の理由（cwd 依存、LIKE の部分一致）は tools/capability_gate.py:9-19 の docstring と同じ内容。305-309 行はツール内部の挙動。297 行の「capability_gate.py を使う」1 行は本体に残す前提で妥当。 | 16 |
| .claude/skills/org-delegate/references/capability-first-drive-operational-gate.md:313-345 | §3 / §3-A interactive-action | keep | org-suspend Phase 1/4 が適用。記録は tools/journal_append.sh notify_sent kind=capability_first_drive（journal_append.sh 実在） | 33 |
| .claude/skills/org-delegate/references/capability-first-drive-operational-gate.md:346-385 | §3-B monitoring-read-only | keep | first_drive_pending gate は capability_gate.py:29-32。送達確認後のみ印を書く規則は無人経路の報告握り潰し防止 | 40 |
| .claude/skills/org-delegate/references/capability-first-drive-operational-gate.md:386-421 | §3-B-1 登録待ち型・send-as-probe | keep | spawn-flow.md 3-4/3-5、pane-close.md 5-4/5-5、org-start Block D-2 が適用(本ファイル §6 #2/#7/#8) | 36 |
| .claude/skills/org-delegate/references/capability-first-drive-operational-gate.md:422-447 | §3-B-2 送信失敗後の復旧も gate 対象 | keep | renga-error-codes.md:102-107 が本節を参照、tests/test_transport_seam.py:192 がポインタを pin | 26 |
| .claude/skills/org-delegate/references/capability-first-drive-operational-gate.md:448-454 | 参照の所在（双方向）注 | move | 文書間リンク構造の保守メモ。リンク実在は renga-error-codes.md:102-107 で確認済み<br>**検証**: 反証で維持: 448-453 行は文書間リンクの保守メモ。ポインタが実在することは tests/test_transport_seam.py:176-192 が検査しており、本注を消してもテストは壊れない。 | 7 |
| .claude/skills/org-delegate/references/capability-first-drive-operational-gate.md:455-460 | first_drive_pending は承認記録ではない | keep | capability_gate.py:29-32 の定義と一致する規範。3 行 | 6 |
| .claude/skills/org-delegate/references/capability-first-drive-operational-gate.md:461-493 | §4 production-activation gate | keep | capability_gate.py --gate production_activation、check_renga_compat.py --require-live 実在。dispatcher cwd のパス差注意は実害記述あり | 33 |
| .claude/skills/org-delegate/references/capability-first-drive-operational-gate.md:494-513 | §5 イベント種別 | keep | 3 kind は capability_gate.py:25-36 の GATES と一致。ladder 分離は capability_gate.py:38-60 | 20 |
| .claude/skills/org-delegate/references/capability-first-drive-operational-gate.md:514-525 | §6 call site 規範文テンプレート | move | 各 call site に貼る文面の書き方（保守者向け）。call site には既に配線済み(commit 87a39be/#879, d83ab9d/#897)<br>**検証**: 反証で維持: 516-524 行は call site に貼る規範文のテンプレートで、文書保守者向け。call site 側には配線済み（例: dispatcher-resume/SKILL.md:94,159,287 が §6 の表 #N を引用）。548-560 行の表は残る。 | 12 |
| .claude/skills/org-delegate/references/capability-first-drive-operational-gate.md:526-547 | §6 owner 規定・#9 責務分割 | keep（当初 move） | どの文書が何の owner かの説明。実行時に必要な縮退先は 548-560 の表にある<br>**検証**: 反証で move→keep: 526-546 行は、承認後に §1-2 が掛かる経路の範囲を定める規範を含む。#2b/#4 に §1-2-d、#9 にも §1-2 が掛かり、「#9 には掛からない」と読んではならない（MUST NOT）。説明文ではない。ただし 541 行の「現行配備の全 backend」は 104 行と同じく outdated なので直す。 | 22 |
| .claude/skills/org-delegate/references/capability-first-drive-operational-gate.md:548-564 | §6 適用先表 #1-#9 と org-down | keep | 表の各ファイル(org-suspend/org-start/secretary-resume/dispatcher-resume/spawn-flow/pane-close/worker-monitoring)が本ファイルを参照(git grep -l で確認) | 17 |
| .claude/skills/org-delegate/references/capability-first-drive-operational-gate.md:565-587 | 上表に行を持たない追加 call site | move | 棚卸し結果と配線先の保守台帳（冒頭「棚卸しで見つかった」）。実行手順は各配線先にある<br>**検証**: 反証で維持: 565-586 行は棚卸しで見つけた追加 call site と、その配線先の台帳。実行規範は配線先（spawn-flow 3-4、pane-close 5-4、renga-error-codes の messaging 手順）にある。 | 23 |
| .claude/skills/org-delegate/references/capability-first-drive-operational-gate.md:588-599 | 対象外にした list_peers の言及 | move | 配線しない箇所とその理由の監査記録。実行時に読む必要なし<br>**検証**: 反証で維持: 590-599 行は配線対象から外した箇所とその理由の監査記録。実行時の手順は含まない。 | 12 |

### .claude/skills/org-delegate/references/claude-org-self-edit.md（141 行: keep 85 / move 11 / delete 13 / outdated 30 / transport 2）

| 行範囲 | 節 | 分類 | 根拠 | 行数 |
|---|---|---|---|---|
| .claude/skills/org-delegate/references/claude-org-self-edit.md:1-11 | 冒頭 前提（Pattern 判定）と事故 2 件 | keep | .hooks/block-org-structure.sh が本ファイル名を参照(git grep)、Step 0.7 は org-delegate/SKILL.md:184-186 に実在。consumer: SKILL.md:245,306, delegate-flow-details.md:60 | 11 |
| .claude/skills/org-delegate/references/claude-org-self-edit.md:12-25 | §1 claude-org-self-edit ロールで settings 生成 | outdated | 手動 settings generate は tools/gen_delegate_payload.py:1991-2077 が実行(SKILL.md:186 で Step 1.5 settings 生成を一括化)。24 の「両者の同期未整備（phase4-completion:71-77）」は tools/check_runtime_schema_drift.py(Phase 5c, #130)が CI で diff 済み<br>**補足**: settings は gen_delegate_payload.py が --role 付きで生成(1991-2077)。schema 同期は tools/check_runtime_schema_drift.py が CI 検査。role の hook 除外説明は org_extension_schema.json:861 に一致し残してよい<br>**検証**: 反証で維持: org-delegate/SKILL.md:186 では settings の生成を tools/gen_delegate_payload.py がまとめて行う（:1986-2019 で --role claude-org-self-edit を含む argv を組む）。24 行の「両者の同期未整備」は、tools/check_runtime_schema_drift.py（Phase 5c #130）が .github/workflows/tests.yml:43 で CI 実行されていることと合わない。hook 除外の説明は残す。 | 14 |
| .claude/skills/org-delegate/references/claude-org-self-edit.md:26-30 | §2 CLAUDE.local.md に書く | keep | tools/gen_worker_brief.py:452-463 が self_edit 時 CLAUDE.local.md へ自動切替、gen_delegate_payload.py:363-377 | 5 |
| .claude/skills/org-delegate/references/claude-org-self-edit.md:31-32 | Pattern C 強制での同 repo 排他 | outdated | 「SKILL.md Step 0.7 同節『並行作業との競合』項に併記済み」とあるが git grep '並行作業との競合' は本行のみヒット＝参照先不在<br>**補足**: 排他規則自体は本行が唯一の記載。参照先の記述は存在しない（SKILL.md:184-190 に該当項なし）<br>**検証**: 反証で維持: git grep '並行作業' は org-delegate 配下で本行以外 0 件で、参照先の「並行作業との競合」項は存在しない。outdated なのは参照先の記述だけで、C/C を同時に起動しない排他規則はここにしか書かれていないので残す。 | 2 |
| .claude/skills/org-delegate/references/claude-org-self-edit.md:33-34 | CLAUDE.md と CLAUDE.local.md の両方が読まれる | keep | self-edit 時の前提事実。1 行 | 2 |
| .claude/skills/org-delegate/references/claude-org-self-edit.md:35-45 | 通常手順の読み替え（CLAUDE.md→CLAUDE.local.md） | outdated | 読み替え対象の手動生成手順は gen_delegate_payload.py が一括実行(SKILL.md:186)し、出力先切替は gen_worker_brief.py:452-463 が自動。instruction-template.md:193 に claude_md_filename 変数あり<br>**補足**: 窓口の手動読み替えは不要。tools/gen_delegate_payload.py:363-377 / gen_worker_brief.py:452-463 が自動で CLAUDE.local.md に出力<br>**検証**: 反証で維持: gen_worker_brief.py:451-463 が self_edit のとき出力先を CLAUDE.local.md に自動で切り替え、gen_delegate_payload.py:363-377 も同様。instruction-template.md:193,199-206 は {claude_md_filename} 変数で置き換える。窓口が手で読み替える手順は要らなくなった。44 行の「ルート CLAUDE.md を上書きしない」は制約なので残す。 | 11 |
| .claude/skills/org-delegate/references/claude-org-self-edit.md:46-53 | ワーカー完了時の Secretary cleanup 責務（#478） | keep | tools/run_complete_on_merge.py:101 cleanup_pattern_c_local_md 実在、org-pull-request/SKILL.md:302-330 が呼ぶ。発生事例 lt-lapras-392778-01 | 8 |
| .claude/skills/org-delegate/references/claude-org-self-edit.md:54-58 | cleanup 関数の判定・動作・順序非依存 | delete | 同内容が実行箇所 org-pull-request/SKILL.md:302-330（呼び出しコードと #478/#486 注記）と run_complete_on_merge.py:101-174 docstring にある<br>**補足**: 正本: .claude/skills/org-pull-request/SKILL.md:302-330（実装 tools/run_complete_on_merge.py:101）<br>**検証**: 反証で維持: 判定・動作・順序非依存（#486）は org-pull-request/SKILL.md:302 と :317-330 のコードコメント、run_complete_on_merge.py の cleanup docstring（join 優先・NULL fallback・missing_ok・pattern_c_cleanup）に同じ強さで書かれている。tests/tools からこの文面への参照は無い。 | 5 |
| .claude/skills/org-delegate/references/claude-org-self-edit.md:59-61 | scope 注（settings.local.json は別 Issue） | move | Issue スコープの経緯説明<br>**検証**: 反証で維持: 60 行は Issue スコープの注（settings.local.json の削除は別 Issue）で、手順は含まない。 | 3 |
| .claude/skills/org-delegate/references/claude-org-self-edit.md:62-69 | §3 live repo worktree base | keep | tools/resolve_worker_layout.py:42,78 が live_repo_worktree を自動選択。tests/test_resolve_worker_layout.py 実在 | 8 |
| .claude/skills/org-delegate/references/claude-org-self-edit.md:70-77 | de facto 慣行の経緯と理由 | move | session #11-#12 の PR 番号列挙と採用理由。手順ではない<br>**検証**: 反証で維持: 70-76 行は session #11-#12 の PR 番号の列挙と採用理由。手順は 64-68 行と 80-85 行の表、および tools/resolve_worker_layout.py の自動選択にある。 | 8 |
| .claude/skills/org-delegate/references/claude-org-self-edit.md:78-86 | 使い分け表と resolve_worker_layout 自動選択 | keep | resolve_worker_layout.py:26,42 の pattern_variant 定義と一致 | 9 |
| .claude/skills/org-delegate/references/claude-org-self-edit.md:87-94 | §4 冒頭で「ルート CLAUDE.md は無視」を明示 | delete | 同文面が tools/templates/worker_brief_self_edit.md:3 に固定で入り、生成器が自動出力。窓口が手で書く手順は残っていない(SKILL.md:186)<br>**補足**: 正本: tools/templates/worker_brief_self_edit.md:3<br>**検証**: 反証で維持: tools/templates/worker_brief_self_edit.md:3 に同じ文言が固定で入っている。gen_worker_brief.py:413-415 が self_edit のときこのテンプレートを使う。窓口が手で書く経路は無い（SKILL.md:186）。 | 8 |
| .claude/skills/org-delegate/references/claude-org-self-edit.md:95-105 | §5 スコープ境界と正準定義 | keep | 発生事例 #612（references/ 編集の無ゲート成功）。SKILL.md:306 が本節を一次参照。自己縮小禁止は anti-laundering | 11 |
| .claude/skills/org-delegate/references/claude-org-self-edit.md:106-109 | 背景（2 層ガード） | keep | org_extension_schema.json:861 self-edit role は block-org-structure.sh を drop。send_keys のみ承認として通る点はハンドシェイクの前提。前半の説明部は rationale へ寄せられる | 4 |
| .claude/skills/org-delegate/references/claude-org-self-edit.md:110-111 | 輸送層両系注記（既定の二フレーム / Epic #6 Issue G） | transport | 既定 renga/opt-in broker、broker folder-trust 2 段、二フレーム注記(Refs #604)、Epic #6 Issue G 待ちを含む | 2 |
| .claude/skills/org-delegate/references/claude-org-self-edit.md:112-129 | ハンドシェイク手順 1（self_edit_approval.py send） | keep | tools/self_edit_approval.py 実在（send/audit, exit 10, --target %N, self_edit_approval_sent: 同 :87,107,151） | 18 |
| .claude/skills/org-delegate/references/claude-org-self-edit.md:130-134 | なぜ 1 コマンドなのか | keep（当初 move） | 事故経緯(2026-07-31 ja-registry-template-001 / 2026-08-25 pr-merged-double-entry-fix, #956)と分割理由。分割はツール内部に閉じたので手順としては不要。self_edit_approval.py:31-51 docstring にも同旨<br>**検証**: 反証で move→keep: 134 行は今も効く復旧手順: draft が残ったときに入力欄を消す前に、inspect_pane で idle を確認する。busy のペインに Escape を送るとターンが中断される。tools/self_edit_approval.py:180-199 の _REMEDY（prior_draft/not_submitted）は「消してから再実行」とだけ書き、Escape と idle の注意を載せていない。132-133 行の経緯部分だけを移すよう分割すべき。 | 5 |
| .claude/skills/org-delegate/references/claude-org-self-edit.md:135-138 | ハンドシェイク手順 2-3（brief / worker） | keep | worker brief 側の確認義務。org-escalation/SKILL.md 実在 | 4 |
| .claude/skills/org-delegate/references/claude-org-self-edit.md:139-141 | 根拠（knowledge/curated/delegation.md の節） | outdated | grep -n '自身を編集\\|事前に調整\\|self-edit' knowledge/curated/delegation.md → 0 件。git log -S '事前に調整する' -- knowledge/ も 0 件＝参照先の節が存在しない<br>**補足**: 参照先なし。削除するか、実在する根拠(Issue #289 / #478 / #612 / #956)に差し替え<br>**検証**: 反証で維持: knowledge/curated/ には codex/delegation/dispatcher-monitoring/release-process/schema-validation/testing/wsl-tui しか無い。grep '自身を編集\|自己編集\|self-edit\|事前に調整' knowledge/curated/delegation.md は 0 件、knowledge/ 全体でも 0 件で、参照先の節は存在しない。 | 3 |

### .claude/skills/org-delegate/references/pane-layout.md（107 行: keep 24 / move 16 / delete 28 / outdated 15 / transport 24）

| 行範囲 | 節 | 分類 | 根拠 | 行数 |
|---|---|---|---|---|
| .claude/skills/org-delegate/references/pane-layout.md:1-13 | タイトル(org-broker MCP)・輸送層両系注記 | transport | 生成物（編集は pane-layout.md.in / tools/skill_src/fragments/pane-layout-transport.*.md）。既定 broker / opt-in renga・spawn 儀式・push 一次・Epic #6 Issue G 待ちの二系記述 | 13 |
| .claude/skills/org-delegate/references/pane-layout.md:14-17 | 初期レイアウト（broker の detached セッション） | transport | broker 面の tmux detached / WezTerm 別窓 / logical secretary 記述。renga 運用では該当しない | 4 |
| .claude/skills/org-delegate/references/pane-layout.md:18-19 | capacity 律速の backend 分岐 | delete | 同内容が spawn-flow.md:38-43 と .dispatcher/CLAUDE.md:56-58 にある（--transport / --max-concurrent-workers / choose_split バイパス）<br>**補足**: 正本: .dispatcher/references/spawn-flow.md:36-44（CLI 例は .dispatcher/CLAUDE.md:44-58）。編集は .in 側<br>**検証**: 反証で維持: spawn-flow.md:38-43 と .dispatcher/CLAUDE.md:44-58 に同じ内容がある（--transport の明示、--max-concurrent-workers 既定 8、choose_split のバイパス）。tests が pane-layout の本文を参照する箇所は無い（git grep pane-layout -- tests は 0 件）。編集は .in とフラグメント側。 | 2 |
| .claude/skills/org-delegate/references/pane-layout.md:20-21 | 設計 SoT 注記（transport-lab） | transport | broker 視覚タイリング不在の設計 SoT 注記。docs/operations/broker-dogfood-runbook.md 実在 | 2 |
| .claude/skills/org-delegate/references/pane-layout.md:22-29 | 配置ルール表 | outdated（当初 delete） | dispatcher 行は org-start/SKILL.md:209-220、curator 行は pane-close.md:313-322 と同じ引数。ただし curator 行は model="sonnet" が欠落（pane-close.md:320 は sonnet）<br>**補足**: 正本: org-start/SKILL.md:209-220（dispatcher）/ .dispatcher/references/pane-close.md:313-322（curator, model=sonnet）/ spawn-flow.md 3-2（worker）<br>**検証**: 反証で delete→outdated: 重複は事実だが、.dispatcher/CLAUDE.md:28、org-start/SKILL.md:207、spawn-flow.md:114 が pane-layout.md を「ペイン配置ルール」の参照先として指しているので、表だけ消すとポインタが空になる。27 行の curator 行は model="sonnet" が欠けていて、pane-close.md:320 と食い違う。これを直すのが先で、削除はポインタの張り替えとセットで人が判断すべき。 | 8 |
| .claude/skills/org-delegate/references/pane-layout.md:30-31 | spawn_claude_pane を使う理由 | transport | cd プレフィックス禁止(auto-upgrade 不発で channel push 不達)は keep 相当だが、段落は broker --mcp-config / renga 1 段の両系 flag 説明が主 | 2 |
| .claude/skills/org-delegate/references/pane-layout.md:32-37 | balanced split 戦略 冒頭の輸送層注記・overflow 例外 | delete | spawn-flow.md:38-43 と同内容（overflow-to-new-tab / server-capability spawn_tab / 現行 ja は未配線）。claude-org-runtime dispatcher delegate-plan --help でフラグ実在を確認<br>**補足**: 正本: .dispatcher/references/spawn-flow.md:38-43<br>**検証**: 反証で維持: 34-36 行は spawn-flow.md:38-42 とほぼ同じ文面（overflow-to-new-tab、server-capability spawn_tab、peers-json 必須、現行 ja は未配線）。spawn-flow 側の方が条件が細かく（41,43 行）、正本として十分。 | 6 |
| .claude/skills/org-delegate/references/pane-layout.md:38-45 | なぜ balanced split が必要か | keep | claude_org_runtime.dispatcher.runner.MIN_PANE_WIDTH=20 / MIN_PANE_HEIGHT=5（runtime 0.1.49 で import 確認）。42 の旧 k-table 経緯は rationale へ寄せられる | 8 |
| .claude/skills/org-delegate/references/pane-layout.md:46-56 | アルゴリズム（delegate-plan helper が SoT） | delete | spawn-flow.md:36 と .dispatcher/CLAUDE.md:44-88 が実行手順として同内容を持つ。55 の「SKILL.md Step 3-1c」は org-delegate/SKILL.md:276 で spawn-flow.md へ移設済み<br>**補足**: 正本: .dispatcher/references/spawn-flow.md:36-59（3-1 / 3-1c）、CLI は .dispatcher/CLAUDE.md:44-58<br>**検証**: 反証で維持: 48-53 行は spawn-flow.md:36,45-46 と同じ（helper が SoT、CLI の形、runtime を一次参照）。55 行の「SKILL.md Step 3-1c」は既に spawn-flow.md 3-1c へ移っている（.dispatcher/CLAUDE.md:86 が spawn-flow 3-1c を参照）。 | 11 |
| .claude/skills/org-delegate/references/pane-layout.md:57-68 | Verification trace（Issue #307 参考） | move | 本文自身が「参考」「正準値は runtime SoT」と明記。curator は常駐しない(CLAUDE.md)ので初期レイアウト前提も現行運用と異なる<br>**検証**: 反証で維持: 59 行に本文自身が「参考」「正準値は runtime SoT」と書いている。手動で辿った trace で、実行手順は含まない。 | 12 |
| .claude/skills/org-delegate/references/pane-layout.md:69-75 | Edge cases / 運用時の注意 | outdated | 72「キュレーター → 窓口にエスカレーション」は curator 非常駐(CLAUDE.md)と .dispatcher/CLAUDE.md:86（helper escalate を窓口へ verbatim）に反する。71 は旧 k-table 経緯<br>**補足**: split_refused/capacity は dispatcher が窓口へ直接 escalate（.dispatcher/CLAUDE.md:86）。レース・target 責務(73-74)は残せる<br>**検証**: 反証で維持: 72 行の「キュレーター → 窓口にエスカレーション」は CLAUDE.md:17（curator は常駐しない）と .dispatcher/CLAUDE.md:86（helper の escalate を窓口へそのまま転送）に合わない。71 行は旧 k-table の経緯。73-74 行は残せる。 | 7 |
| .claude/skills/org-delegate/references/pane-layout.md:76-78 | 運用メモ: 全ペイン同一タブ（broker 枠） | transport | 生成 fragment pane-layout-same-tab.broker.md 由来の broker 面根拠 | 3 |
| .claude/skills/org-delegate/references/pane-layout.md:79-79 | renga 面の Group A/B capability 根拠 | delete | Group A/B・server_too_old・legacy フォールスルーの説明が renga-error-codes.md:53-72 と同内容（本行自身が「文面の SoT は renga-error-codes.md」と明記）<br>**補足**: 正本: .claude/skills/org-delegate/references/renga-error-codes.md:53-72。編集は tools/skill_src/fragments/pane-layout-same-tab.renga.md<br>**検証**: 反証で維持: 79 行は自分で「文面の SoT は renga-error-codes.md」と書いており、Group A/B と server_too_old の扱いは renga-error-codes.md:5,40-72 にある。編集元は tools/skill_src/fragments/pane-layout-same-tab.renga.md（manifest.json:78-79）。消すなら broker 側のフラグメントと対で扱う。 | 1 |
| .claude/skills/org-delegate/references/pane-layout.md:80-87 | 命名規約・role ラベル | keep | name=worker-{task_id} は spawn-flow / pane-close / capability gate 表で使用。name_invalid（全桁数字拒否）は renga-error-codes.md:29 | 8 |
| .claude/skills/org-delegate/references/pane-layout.md:88-95 | ワーカー完了時 CLOSE_PANE 手順 | delete | 本文自身が「手順の SoT は pane-close.md Step 3」と明記。pane-close.md:110 以降に同じ照合・数値 id・自タブ確立規則<br>**補足**: 正本: .dispatcher/references/pane-close.md Step 3（:110 付近）<br>**検証**: 反証で維持: 95 行の末尾に「手順の SoT は pane-close.md Step 3」とある。pane-close.md:106 以降（### 3. ペインを閉じる、:146 の保留扱い、:160-164 の自タブ未確立と再割当ての分岐）に、照合・数値 id・自タブ確立・close 見送りがすべて書かれている。 | 8 |
| .claude/skills/org-delegate/references/pane-layout.md:96-97 | org-suspend 時の停止順 | keep | last_pane 扱いは renga-error-codes.md:24 と整合。1 行 | 2 |
| .claude/skills/org-delegate/references/pane-layout.md:98-103 | split direction 慣習 | keep | org-start/SKILL.md:211-219（horizontal）と pane-close.md:313-317（vertical）が使用 | 6 |
| .claude/skills/org-delegate/references/pane-layout.md:104-107 | 将来機能 / upstream 追跡 | move | 未実装の希望機能メモで手順ではない。対応 Issue 番号の記載なし<br>**検証**: 反証で維持: 106-107 行は Issue 番号の無い未実装機能の希望メモ。実行手順は含まない。編集は pane-layout-future-upstream.*.md のフラグメント側。 | 4 |

### .claude/skills/org-delegate/references/dogfood-protocol.md（86 行: keep 76 / move 0 / delete 9 / outdated 1 / transport 0）

| 行範囲 | 節 | 分類 | 根拠 | 行数 |
|---|---|---|---|---|
| .claude/skills/org-delegate/references/dogfood-protocol.md:1-4 | タイトル・一次参照元 | keep | org-delegate/SKILL.md:270 が本ファイルを一次参照 | 4 |
| .claude/skills/org-delegate/references/dogfood-protocol.md:5-6 | protocol の由来（Curator session #18） | keep（当初 move） | PR #288 の事象に基づく導入経緯の説明<br>**検証**: 反証で move→keep: 5 行の 1 文目は protocol そのものの定義（新規 tool/runtime/workflow の PR で paired dogfood issue を作り、次の実使用を dogfood pass として予約する）で、手順の要旨。経緯（Curator session #18 / PR #288）は後半の 1 文だけなので、移すのは後半だけにする分割が安全。 | 2 |
| .claude/skills/org-delegate/references/dogfood-protocol.md:7-14 | 適用条件 | keep | SKILL.md:268 が同条件を要約して参照。registry/dogfood_pending.md 実在。ただし台帳 entries は 0 行（導入 commit b78fda0 2026-05-08 以後の更新なし） | 8 |
| .claude/skills/org-delegate/references/dogfood-protocol.md:15-32 | 窓口の責務 (A) 実装起票 / (B) dogfood pass earmark | keep | --impl-guidance / --knowledge は tools/gen_delegate_payload.py の引数。dogfood_run_task_id は spawn-flow.md:71(3-1d 条件 1)が読む | 18 |
| .claude/skills/org-delegate/references/dogfood-protocol.md:33-41 | org-pull-request 側の責務（cross-ref） | delete | 実行箇所 org-pull-request/SKILL.md:81（impl_pr 記入・gh issue create・pending→open・PR 本文追記）と :303 に同内容。40 の「paired issue クローズ時に consumed→closed」は :303「本スキル側では保証しない」と食い違う<br>**補足**: 正本: .claude/skills/org-pull-request/SKILL.md:81, 303。consumed→closed は本ファイル 76-85 の窓口 hygiene<br>**検証**: 反証で維持: 37-39 行は org-pull-request/SKILL.md:81 の (a)-(d) と同じ強さで一致する。40 行の「paired issue クローズ時に consumed→closed」は :303「本スキル側では保証しない」と食い違い、窓口の hygiene（本ファイル 76-85 行）が正本。 | 9 |
| .claude/skills/org-delegate/references/dogfood-protocol.md:42-51 | register フォーマット | keep | registry/dogfood_pending.md の列定義と一致 | 10 |
| .claude/skills/org-delegate/references/dogfood-protocol.md:52-75 | register 状態遷移 | keep | SKILL.md:272,360 が状態遷移を参照。69 の遷移ラベル「org-pull-request §4」は org-pull-request:303 と食い違う（要修正） | 24 |
| .claude/skills/org-delegate/references/dogfood-protocol.md:76-85 | consumed → closed 観察タイミング | keep | org-pull-request/SKILL.md:303 がこの hygiene に委譲。gh issue view は窓口が実行 | 10 |
| .claude/skills/org-delegate/references/dogfood-protocol.md:86-86 | /org-resume 起動時の hygiene | outdated | grep -n dogfood .claude/skills/org-resume/SKILL.md → 0 件。org-resume に該当手順なし<br>**補足**: 現状 org-resume は dogfood 台帳を走査しない。手順を org-resume に足すか、本行を削除<br>**検証**: 反証で維持: grep -n dogfood .claude/skills/org-resume/SKILL.md は 0 件。dogfood_pending を扱うのは org-delegate と org-pull-request の SKILL だけで、tools にも無い。org-pull-request/SKILL.md:303 も同じ存在しない手順を前提にしている。 | 1 |

### .claude/skills/org-delegate/references/dogfood-issue-template.md（79 行: keep 77 / move 1 / delete 1 / outdated 0 / transport 0）

| 行範囲 | 節 | 分類 | 根拠 | 行数 |
|---|---|---|---|---|
| .claude/skills/org-delegate/references/dogfood-issue-template.md:1-8 | 冒頭・用途 | keep | org-pull-request/SKILL.md:81 が --body-file のテンプレとして参照 | 8 |
| .claude/skills/org-delegate/references/dogfood-issue-template.md:9-16 | Title | keep | org-pull-request:81 の --title "dogfood follow-up: <surface>" と一致 | 8 |
| .claude/skills/org-delegate/references/dogfood-issue-template.md:17-72 | Body template | keep | dogfood-protocol.md:30 で --knowledge として brief に同梱される defect 報告フォーマット | 56 |
| .claude/skills/org-delegate/references/dogfood-issue-template.md:73-76 | 区切り・Notes 見出し | keep | 見出し | 4 |
| .claude/skills/org-delegate/references/dogfood-issue-template.md:77-77 | gh issue create は org-pull-request 側 | delete | 責務分担は org-pull-request/SKILL.md:81 と dogfood-protocol.md:15-23 に既出<br>**補足**: 正本: .claude/skills/org-pull-request/SKILL.md:81<br>**検証**: 反証で維持: 責務の分担は org-pull-request/SKILL.md:81（gh issue create）と dogfood-protocol.md:17-23（org-delegate は append だけ）に同じ強さで書かれている。 | 1 |
| .claude/skills/org-delegate/references/dogfood-issue-template.md:78-78 | Closes を使わない | keep | テンプレ本文末 Refs #<source PR>(70) の運用規則。1 行 | 1 |
| .claude/skills/org-delegate/references/dogfood-issue-template.md:79-79 | 複数カテゴリ defect の集約（PR #288 原型） | move | PR #288 由来の経緯。規則部分は 5 行目「個別 fix issue へ split」と重複<br>**検証**: 反証で維持: 規則（集約したうえで個別 fix issue へ分ける）は 5 行と重複する。残りは PR #288 が原型だったという経緯だけ。 | 1 |

### .claude/skills/org-suspend/SKILL.md（791 行: keep 698 / move 43 / delete 32 / outdated 7 / transport 11）

| 行範囲 | 節 | 分類 | 根拠 | 行数 |
|---|---|---|---|---|
| .claude/skills/org-suspend/SKILL.md:1-32 | frontmatter (allowed-tools) | keep | 参照 tool は全て現存: ls tools/journal_append.sh\|.py, tools/capability_gate.py, tools/secretary_queue_watcher.py, tools/state_db/importer.py。mcp__ は org-broker 名のみ（生成時 transport で render、扱いは transport 判断に従属）。編集は SKILL.md.in 側 | 32 |
| .claude/skills/org-suspend/SKILL.md:33-37 | タイトル・導入 | keep | スキル本体の要約 1 文 | 5 |
| .claude/skills/org-suspend/SKILL.md:38-42 | curator 不在は正常系 | keep | org-start が curator_pane_id を CLEAR（本ファイル:357 が引用）、.dispatcher/CLAUDE.md の null 正常系規定が現存。同趣旨が :356-361 / :723-729 にもあり、ファイル内で 3 回言っている | 5 |
| .claude/skills/org-suspend/SKILL.md:43-51 | 責務境界（/org-suspend と /org-down） | keep | .claude/skills/org-down/SKILL.md:37,57 が『/org-suspend を完走してから org down』と消費しており、両側が現存 | 9 |
| .claude/skills/org-suspend/SKILL.md:52-62 | mcp__org-broker__ 経由の宣言 + 輸送層両系注記 + 二フレーム注記（Refs #604 / Epic #6 Issue G） | transport | 既定 broker / opt-in renga の両系注記と、コード既定 vs 運用既定の二フレーム注記・Epic #6 Issue G 待ちの但し書き。root CLAUDE.md の同節は逆向き（既定 renga / opt-in broker）で書かれている。.in 側の共有ヘッダ partial から生成 | 11 |
| .claude/skills/org-suspend/SKILL.md:63-97 | Phase 1 手順1: id 台帳（list_peers / list_panes を面ごとに取る） | keep | 契約 T-§4.2 / T-§cap は docs/contracts/backend-interface-contract.md に現存。live renga の server_info が cross_tab_peers / caller_scope を広告しているので、この分岐は実際に通る経路（2026-10-12 に実測） | 35 |
| .claude/skills/org-suspend/SKILL.md:98-108 | (G) 2 グループに分ける理由（renga src/ipc/mod.rs・#290 世代のスキュー） | keep（当初 move） | 上流ソースの行番号を引いて、なぜ token が別なのかを説明している設計理由の部分。手順として効くのは :109-129 の Group 定義と MUST だけ。移し先: references/rationale.md<br>**検証**: 反証で move→keep: 98-106 は理由説明で移せるが、:106-108『caller_scope があるから全 pane 制御がフォーカス非依存という推論をしてはならない』は契約 T-§cap Independence の MUST NOT で、今も効く規則。:114 / :141 にも近い文はあるが、この禁止を一般形で言っているのはここだけ。:106-108 を 1 行残し、:99-105（renga src/ipc/mod.rs と #290 世代の説明）だけを rationale へ移すのが安全 | 11 |
| .claude/skills/org-suspend/SKILL.md:109-119 | Group A / Group B の定義 | keep | live renga server_info の capabilities に caller_scope と caller_scope_close_identity が別トークンとして存在する（2026-10-12 実測）。renga-error-codes.md:53 に同じ分類がある | 11 |
| .claude/skills/org-suspend/SKILL.md:120-129 | (G-MUST) Group B は数値 pane_id のみ | keep | git 59cf3af (#896) で MUST に昇格。pane-close.md:108 も同じ規則で、close を実行するのはこのスキル本体なので keep | 10 |
| .claude/skills/org-suspend/SKILL.md:130-153 | (G-OWNTAB) 自タブ確立の 2 手段 / same_tab は数えない | keep | 本文 :134-137 自身が pane-close.md の同名節と同一規則だと書いている（.dispatcher/references/pane-close.md:108-117）。attention-stop:52-67 にも同じ規則がある。close を実行する場所はそれぞれなので delete にはしない（judgments 参照） | 24 |
| .claude/skills/org-suspend/SKILL.md:154-163 | かつて SHOULD だった経緯（ja#823） | move | 本文 :162-163 自身が『この段落は当時の状態を説明する記録である』と書いている。昇格は git 59cf3af (#896) で済んでいる<br>**検証**: 反証で維持: 本文 :162-163 自身が『この段落は当時の状態を説明する記録』と書いている。従うべき規則は :120-129 の (G-MUST) にあり、ここに残すべき手順は無い。tests/ と tools/ に 'ja#823' / 'SHOULD だった' のヒットは無い（git grep 確認） | 10 |
| .claude/skills/org-suspend/SKILL.md:164-167 | server_too_old の fail-closed | keep | renga-error-codes.md:84 に『server_too_old の復旧手順』節が現存し、契約 T-§6 clause (b) を参照している | 4 |
| .claude/skills/org-suspend/SKILL.md:168-181 | (F) フォーカス前提 | keep | caller_scope を広告しない backend 向けの安全規則。org-broker は単一タブなので今も意味がある | 14 |
| .claude/skills/org-suspend/SKILL.md:182-221 | 台帳の join 規則（pane_id は list_panes 由来・name join・same_tab の否定使用） | keep | pane-close.md:117 が『org-suspend Phase 1 の id 台帳』を名指しで参照している（consumer が現存） | 40 |
| .claude/skills/org-suspend/SKILL.md:222-237 | Phase 1 手順2-5（SUSPEND 送信・30 秒 poll） | keep | ワーカー brief の SUSPEND 対応節が consumer（worker CLAUDE.md『SUSPEND 対応』） | 16 |
| .claude/skills/org-suspend/SKILL.md:238-261 | 宛先選定の真理値表: R 集合・role gate・unresolved | keep | gate ref §6（capability-first-drive-operational-gate.md:514 以降）が『#1/#1b は org-suspend 自身の宛先真理値表が owner』と定めている | 24 |
| .claude/skills/org-suspend/SKILL.md:262-267 | 版判定は列挙 1 回につき 1 度 | delete | 重複。capability-first-drive-operational-gate.md:31-42 の §1 が同じ規則を表つきで持っており、本ファイル :294 自身も『正本は reference 側』と書いている<br>**補足**: 正本: .claude/skills/org-delegate/references/capability-first-drive-operational-gate.md:31-42（§1）<br>**検証**: 反証で維持: gate ref:31-38 の §1『列挙 1 回につき 1 度だけ、レコード単位で切り替えない』と同じ内容。gate ref:262 と :270-271 は呼び出し側に『版判定条件の複製』を MUST NOT としている。:290 で ref の Read が義務付けられている。tests/ と tools/ にこの文言のヒットは無く、ヒットは SKILL.md.in:245 だけ（編集は .in 側）。表の『版判定』列から ref §1 を指す 1 行だけは残すとよい | 6 |
| .claude/skills/org-suspend/SKILL.md:268-279 | 真理値表 行1-10 | keep | suspend 固有の宛先決定表（gate ref §6 が owner と認めている） | 12 |
| .claude/skills/org-suspend/SKILL.md:280-280 | 真理値表 行11 の『renga 1.4 系および現行 org-broker はここ』 | outdated | tools/check_renga_compat.py:113 が MIN_REQUIRED_VERSION=(2,0,0)、scripts/install.sh:268 が renga@2.0.0 を pin。live では renga 2.2.0、server_info が cross_tab_peers を広告（2026-10-12 実測）<br>**補足**: 旧版 fallback を通るのは org-broker だけ。renga は 2.0.0 以上が必須（tools/check_renga_compat.py:113）で cross_tab_peers を広告するので、2.0 系の行（1-10）で扱われる<br>**検証**: 反証で維持: tools/check_renga_compat.py:113 が MIN_REQUIRED_VERSION=(2,0,0)、scripts/install.sh:268 が renga@2.0.0 を pin している。live の mcp__renga-peers__server_info は version 2.2.0 で、capabilities に cross_tab_peers / caller_scope_close_identity を含む（本検証で実測）。この行に当たる現行 backend は org-broker だけ。編集は SKILL.md.in:263 側 | 1 |
| .claude/skills/org-suspend/SKILL.md:281-282 | 真理値表 行12 | keep | 未知 role を対象外にする安全規則 | 2 |
| .claude/skills/org-suspend/SKILL.md:283-287 | 旧版 fallback の発動条件は 1 つだけ | delete | 重複。gate ref §1（:35-38）と §1-1『marker を 1 件でも観測したら旧版形として読み替えてはならない(MUST)』（:44）が同じ内容<br>**補足**: 正本: capability-first-drive-operational-gate.md:31-44（§1 / §1-1）<br>**検証**: 反証で維持: gate ref:38 の §1 表（全レコード両方欠落のときだけ旧版）と ref:44-62 の §1-1（marker を 1 件でも見たら旧版として読まない MUST、fail-safe で上書きしない）に含まれる。:284-286 の same_tab=False の補足も §1-1 の一般則が包含する。ref:270 は版判定条件の複製を禁じている。テストの参照は無い（git grep で SKILL.md.in:266 のみ） | 5 |
| .claude/skills/org-suspend/SKILL.md:288-293 | operational gate へのポインタ（Read して interactive-action を適用） | keep | gate ref §6:518 の『各 call site に規範文を置く』規約に沿った call-site 文。tools/capability_gate.py が現存 | 6 |
| .claude/skills/org-suspend/SKILL.md:294-311 | gate の要点再掲（報告項目・確認前は送らない・1 度だけ・差分報告） | delete | 重複。本文 :294-295 自身が『正本は reference 側。ここに手順を複製しない』と言いつつ再掲している。gate ref §3-A（:318-344）に報告項目・停止・記録・差分報告がすべてある<br>**補足**: 正本: capability-first-drive-operational-gate.md:318-344（§3-A）と §2（:275）<br>**検証**: 反証で維持: 報告項目は gate ref §3-A の :328-335 と同じ。『確認前は 1 通も出さない』は :327、1 度だけ記録するのは :337-341 と §2（:275-）、差分報告は :343-344、Phase 4 の扱いは §6 の表 1b（:551）にある。org-suspend 固有の条件で ref に無いものは見当たらない。:296 は自分で『手順を複製しない』と書いている。tests/ と tools/ に参照は無い | 18 |
| .claude/skills/org-suspend/SKILL.md:312-314 | production-activation gate は suspend を止めない | delete | 重複。gate ref §4（:461-494）の『このゲートで止まる経路は無い』と同じ内容<br>**補足**: 正本: capability-first-drive-operational-gate.md:461-494（§4）<br>**検証**: 反証で維持: gate ref:479-482 の §4 が『このゲートで止まる経路は無い…org-suspend の宛先真理値表がまさにこの形…止まるのは first drive gate だけ』と、org-suspend を名指しして同じ内容を同じ強さで書いている。テストの参照は無い | 3 |
| .claude/skills/org-suspend/SKILL.md:315-317 | 旧版 fallback（現在配備の全 backend が通る）では gate を発動しない | outdated | live renga 2.2.0 の server_info が cross_tab_peers を広告している（2026-10-12 実測）。renga の下限は check_renga_compat.py:113 で 2.0.0。gate ref:40 にも同じ古い記述がある<br>**補足**: 現行の renga 運用では list_peers に marker が載るので capability 形になり、first drive gate が発動する。無停止で進むのは org-broker（単一タブ）経路だけ<br>**検証**: 反証で維持: live の server_info で renga 2.2.0 が cross_tab_peers を広告している（本検証で実測）。check_renga_compat.py:113 で下限は 2.0.0。このため renga 運用では list_peers に marker が載り、§1 の表（gate ref:37）により first drive gate が発動する。無停止になるのは org-broker 経路だけ。gate ref:40 にも同じ古い記述がある。編集は SKILL.md.in:298 側 | 3 |
| .claude/skills/org-suspend/SKILL.md:318-322 | 宛先は必ず peer_id | keep | renga-error-codes.md:98 の messaging 分岐（名前解決は送信者タブ内）と整合する | 5 |
| .claude/skills/org-suspend/SKILL.md:323-336 | † 同タブのみが自組織 | keep | 契約 T-§4.2 retained の同一タブ launch 規則（renga-error-codes.md:25 も同じ前提） | 14 |
| .claude/skills/org-suspend/SKILL.md:337-348 | なぜ identity 照合で他タブを自組織と認定しないか / 将来の再訪条件 | move | 設計理由と将来条件の説明だけで手順は無い（手順は :349-355）。schema.sql の worker_dir_id nullable 等への言及も理由の部分<br>**検証**: 反証で維持: 『なぜ』と『将来の再訪条件』だけを書いた引用ブロックで、命令に当たる内容は :349-355 の『他タブピアの扱い』（broadcast しない / close しない / 報告）と :334-335 にある。tools/state_db/schema.sql への言及も根拠説明にとどまる | 12 |
| .claude/skills/org-suspend/SKILL.md:349-362 | 他タブピアの扱い + curator 補足 | keep | 報告文面は :785 で使われる。curator 補足は :38-42 と重なる | 14 |
| .claude/skills/org-suspend/SKILL.md:363-367 | § 他タブ停止確認の注記 | keep | Phase 4 :618-621 の例外へのポインタ | 5 |
| .claude/skills/org-suspend/SKILL.md:368-380 | ‡ same_tab 欠落時（name 一致で同タブと結論しない） | keep | 契約 T-§2.2 の name 非一意性に基づく。gate ref §1-2 と近いが、suspend 用の表に対する規則 | 13 |
| .claude/skills/org-suspend/SKILL.md:381-415 | Phase 2: 未応答ワーカーのスクレイプ | keep | .state/workers/worker-{id}.md は org-resume/SKILL.md:90 が消費。inspect_pane は live の renga-peers に現存 | 35 |
| .claude/skills/org-suspend/SKILL.md:416-424 | Phase 3 冒頭: state.db が SoT・StateWriter 経由・notes/ | keep | tools/state_db/writer.py が現存、notes/README.md も現存 | 9 |
| .claude/skills/org-suspend/SKILL.md:425-425 | journal.jsonl は M4 で廃止 | move | 移行の経緯。tools/journal_append.py:12 の docstring も DB-only へのルーティングを説明している<br>**検証**: 反証で維持: 移行の経緯を書いた 1 行。実際の手順は :453-458（tools/journal_append.py を使う、DB-only）で足りる。journal.jsonl へ書けという指示ではないので、消しても手順は欠けない | 1 |
| .claude/skills/org-suspend/SKILL.md:426-429 | importer --rebuild --no-strict | keep | tools/state_db/importer.py:686-697 に --db / --rebuild / --no-strict が現存 | 4 |
| .claude/skills/org-suspend/SKILL.md:430-448 | 手順1-2: prev.md バックアップ・update_session(status=SUSPENDED) | keep | writer.py:282-283 の _SESSION_FIELDS に status / suspended_at / updated_at がある。begin/commit は writer.py:116,130 に現存（CMD 用 fallback が成立する）。prev.md は docs/overview-technical.md:43 / docs/verification.md:245 が参照 | 19 |
| .claude/skills/org-suspend/SKILL.md:449-449 | 手順3: upsert_run(task_id=..., status=...) | outdated | tools/state_db/writer.py:449-453 で upsert_run の project_slug が keyword-only の必須引数（既定値なし）。書かれたとおりに呼ぶと TypeError になる<br>**補足**: upsert_run(task_id=..., project_slug=..., status=...) と書く（writer.py:449-456）<br>**検証**: 反証で維持: tools/state_db/writer.py:449-456 で upsert_run(self, *, task_id: str, project_slug: str, ...) となっており、project_slug は既定値の無い keyword-only 引数。書かれたとおり task_id と status だけで呼ぶと TypeError になる。正しくは upsert_run(task_id=..., project_slug=..., status=...) | 1 |
| .claude/skills/org-suspend/SKILL.md:450-460 | 手順4-5: worker 状態ファイル更新・suspend イベント | keep | docs/journal-events.md:450 に suspend の行（reason / active_workers[] / pending_items[]）がある | 11 |
| .claude/skills/org-suspend/SKILL.md:461-466 | Phase 3.5 ダッシュボード停止（blind kill） | keep | .state/dashboard.pid の producer/consumer は org-dashboard/SKILL.md:25。org-down/SKILL.md:62 が差し替え先として参照している | 6 |
| .claude/skills/org-suspend/SKILL.md:467-470 | blind kill を残す理由の注記 | move | 設計理由。org-down 側（org-down/SKILL.md:62-65,106）にも同じ説明がある<br>**検証**: 反証で維持: 『簡素なまま残す』理由と、org-down 側の実装の違いを説明する注記。実行する手順は :463-465 の kill 1 行で完結している | 4 |
| .claude/skills/org-suspend/SKILL.md:471-520 | Phase 3.6 secretary_queue_watcher の停止（broker のみ） | keep | tools/secretary_queue_watcher.py:121 に --stop が現存。producer は org-start/SKILL.md:293 Block C3。renga 運用では丸ごと skip される経路（judgments 参照） | 50 |
| .claude/skills/org-suspend/SKILL.md:521-531 | Phase 3.7 冒頭（teardown 前に止める理由・identity 照合） | keep | Issue #468、git e00ee96 (#711) で導入 | 11 |
| .claude/skills/org-suspend/SKILL.md:532-562 | Phase 3.7 手順1-6 | keep | org-attention-stop Step 1-3 をほぼ写したもの（本文 :563-564 で明言）。ただし自タブ確立が不成立のときの sidecar の扱いが attention-stop:71 と食い違う（judgments 参照） | 31 |
| .claude/skills/org-suspend/SKILL.md:563-565 | attention-stop へのポインタ | keep | リンク先 .claude/skills/org-attention-stop/SKILL.md が現存 | 3 |
| .claude/skills/org-suspend/SKILL.md:566-574 | Phase 4 冒頭（停止順・台帳 id で回す） | keep | 停止順序と id 規律を定める手順 | 9 |
| .claude/skills/org-suspend/SKILL.md:575-580 | pane_not_found の分岐（正本ポインタ） | keep | renga-error-codes.md:98 に『pane_not_found の messaging 分岐』節が現存 | 6 |
| .claude/skills/org-suspend/SKILL.md:581-597 | messaging 文脈の再解決 1-2b | keep | renga-error-codes.md:98-143 とほぼ同じ内容で、suspend 固有の差分は台帳と 2-b の旧版枝だけ（judgments 参照） | 17 |
| .claude/skills/org-suspend/SKILL.md:598-599 | 『現在配備の backend（org-broker / renga 1.x）はここを通る』 | outdated | tools/check_renga_compat.py:113 で renga の下限は 2.0.0。live server_info が cross_tab_peers を広告している（2026-10-12）<br>**補足**: 旧版 fallback 枝を通るのは org-broker だけ。renga 2.0+ は marker を返すので same_tab: true 枝を通る<br>**検証**: 反証で維持: 『renga 1.x』は現行の事実と合わない。renga は 2.0.0 以上が必須（check_renga_compat.py:113）で、live 2.2.0 が cross_tab_peers を広告しているので、:594 の same_tab:true 枝を通る。この枝（2-b 第 2 項）を通るのは org-broker だけで、枝そのものは org-broker のために残す必要がある | 2 |
| .claude/skills/org-suspend/SKILL.md:600-614 | messaging 2c-4（再送 1 回・lifecycle 確認・pane_closed はディスパッチャーが書く） | keep | docs/contracts/state-semantics-contract.md が現存。項目番号『4.』が :604 と :611 で重複している（表記上の誤り） | 15 |
| .claude/skills/org-suspend/SKILL.md:615-628 | pane 制御文脈の分岐 | keep | G-MUST の適用箇所 | 14 |
| .claude/skills/org-suspend/SKILL.md:629-643 | 停止手順1（台帳最新化・G-OWNTAB 判定） | keep | close を撃つ前の確立段 | 15 |
| .claude/skills/org-suspend/SKILL.md:644-646 | 停止手順2（ワーカーへ SHUTDOWN） | keep | SHUTDOWN 文面 | 3 |
| .claude/skills/org-suspend/SKILL.md:647-693 | 停止手順3 Pass 1（poll_events で pane_exited） | keep | poll_events は renga-error-codes.md:324『Event stream』節にあり、types フィルタと初回 since の意味論もそこ（:349,353）と一致する | 47 |
| .claude/skills/org-suspend/SKILL.md:694-719 | 停止手順3 Pass 2（照合後に数値 close・残存報告） | keep | G-MUST / identity 照合の実行箇所 | 26 |
| .claude/skills/org-suspend/SKILL.md:720-744 | 停止手順4-6（dispatcher / curator） | keep | 同じ 2-pass を dispatcher / curator に適用する手順 | 25 |
| .claude/skills/org-suspend/SKILL.md:745-746 | 窓口は自分を close しない（規則） | keep | 手順上の禁止事項 | 2 |
| .claude/skills/org-suspend/SKILL.md:747-756 | 自己 close しない 2 つの根拠 | move | 理由の説明（人間との接点・責務境界）。規則は :746 の 1 文で足りる<br>**検証**: 反証で維持: 規則は :746『窓口は自分自身を close_pane しない』の 1 文。:747-756 は 2 つの根拠（人間との接点・責務境界）の説明だけで、手順は含まない | 10 |
| .claude/skills/org-suspend/SKILL.md:757-758 | [last_pane] は強制再試行しない | keep | renga-error-codes.md の Known codes に last_pane がある | 2 |
| .claude/skills/org-suspend/SKILL.md:759-764 | last_pane を安全網にしない理由 | move | 『2.0 系では未確認』という推測を含む理由説明で、手順は無い<br>**検証**: 反証で維持: 『2.0 系では未確認』という推測を含む理由説明。手順として効くのは :757-758（[last_pane] は強制再試行せず exit に委ねる）と、:758-759 の『安全網として当てにしない』の文頭部分で、後者は :746 の自己 close 禁止が包含する。:757-759 前半は残す | 6 |
| .claude/skills/org-suspend/SKILL.md:765-791 | 手順7: 報告（全停止 / 部分中断の文面） | keep | org-down/SKILL.md:66-70 が suspend 成功の gate として Phase 4 の全停止を参照している | 27 |

### .claude/skills/org-escalation/SKILL.md（94 行: keep 79 / move 0 / delete 5 / outdated 3 / transport 7）

| 行範囲 | 節 | 分類 | 根拠 | 行数 |
|---|---|---|---|---|
| .claude/skills/org-escalation/SKILL.md:1-18 | frontmatter | keep | tools/pending_decisions.py と journal_append.sh が現存。mcp__ は org-broker__send_message だけで、renga 運用では名前が合わない（transport 判断に従属）。編集は SKILL.md.in 側 | 18 |
| .claude/skills/org-escalation/SKILL.md:19-23 | タイトル・一次承認しない原則 | keep | root CLAUDE.md『ワーカーからの判断仰ぎは人間にエスカレーションする』節と同じ規則。実行場所はこのスキルなので正本はこちら（CLAUDE.md 側が重複） | 5 |
| .claude/skills/org-escalation/SKILL.md:24-30 | 輸送層両系注記 + 二フレーム注記（Epic #6 Issue G） | transport | SKILL.md.in:24 の {{> dual-system-header-long }} partial から生成される broker/renga の併記と二フレーム注記 | 7 |
| .claude/skills/org-escalation/SKILL.md:31-35 | 状態保存の必要性（3 層） | keep | consumer: .dispatcher/references/worker-monitoring.md:787 が register を、:748 が worker_escalation を参照 | 5 |
| .claude/skills/org-escalation/SKILL.md:36-38 | ack template SoT ポインタ | delete | ファイル内の重複。:42 が同じリンクと『重複コピーを置かない』を繰り返している<br>**補足**: 正本: 同ファイル :42（手順 1 内のリンク）<br>**検証**: 反証で維持: :42 が同じ ack-template.md へのリンクと『重複コピーを置かない』を手順 1 の中に持っている。:37 の『最低 3 要素・例文・アンチパターン』は ack-template.md の :20 / :26 / :74 の見出しそのもの。tests/ と tools/ にヒットは無い（'ack template' の git grep は SKILL.md.in:31 のみ） | 3 |
| .claude/skills/org-escalation/SKILL.md:39-43 | 正準フロー 1: 最初に ack・一次承認しない | keep | ack-template.md:59『判断仰ぎ ack（escalation）』節が現存 | 5 |
| .claude/skills/org-escalation/SKILL.md:44-44 | 自己解釈禁止（CLAUDE.md feedback_relay_user_decisions_to_workers 参照） | outdated | git grep 'feedback_relay_user_decisions_to_workers' のヒットは本ファイルと .in の 2 件だけ。root CLAUDE.md にこのキーは存在しない<br>**補足**: 参照先は root CLAUDE.md『ワーカーからの判断仰ぎは人間にエスカレーションする』節（自己解釈禁止の本文がある）。規則の文自体は keep<br>**検証**: 反証で維持: 'feedback_relay_user_decisions_to_workers' を git grep すると、ヒットは本 SKILL.md と .in:38 だけ。実際の正本は CLAUDE.md:129-136『ワーカーからの判断仰ぎは人間にエスカレーションする』節で、:136 に自己解釈禁止の本文がある。参照先をこの節に直す。規則の文は keep | 1 |
| .claude/skills/org-escalation/SKILL.md:45-58 | ack≠承認 / 手順 2: 3 層保存 | keep | pending_decisions.py:525-528 の append --task-id --message が現存。worker_escalation は worker-monitoring.md:748,769 が SQL で消費 | 14 |
| .claude/skills/org-escalation/SKILL.md:59-65 | 手順 3: 人間へ提示・ask-time emit・resolve to_user | keep | runtime 0.1.49 の classifier.py:52 が awaiting_user を secretary_awaiting_user に変換、config.py:50 で urgent。pending_decisions.py:530-534 に --kind to_user がある | 7 |
| .claude/skills/org-escalation/SKILL.md:66-75 | 手順 4 mark-user-replied / 4.5 emit コマンド | keep | pending_decisions.py:555-560 に mark-user-replied がある。consumer は worker-monitoring Step 5.1 (a-2) | 10 |
| .claude/skills/org-escalation/SKILL.md:76-76 | 『並走 runtime PR の classifier が拾う』 | outdated | その runtime PR は出荷済み。site-packages の claude_org_runtime-0.1.49 classifier.py:52 と config.py:50 に実装がある<br>**補足**: 『claude-org-runtime の attention classifier（0.1.49 時点で classifier.py:52）が secretary_awaiting_user / urgent として拾う』<br>**検証**: 反証で維持: runtime は出荷済み。~/.local/lib/python3.12/site-packages の claude_org_runtime-0.1.49 で、attention/classifier.py:52 が awaiting_user を secretary_awaiting_user に対応付け、config.py:50 が urgent を割り当てている。『並走 runtime PR』は過去の状態を指している | 1 |
| .claude/skills/org-escalation/SKILL.md:77-82 | 手順 5: ワーカーへ転送・resolve to_worker | keep | pending_decisions.py:530-534 に to_worker がある | 6 |
| .claude/skills/org-escalation/SKILL.md:83-84 | 重複保険ポリシー見出し・3 層独立維持 | keep | 方針の 1 行 | 2 |
| .claude/skills/org-escalation/SKILL.md:85-86 | append/resolve 欠落で誤発火/見逃し | delete | ファイル内の重複。:32-35 が同じ因果（欠落すると SECRETARY_RELAY_GAP_SUSPECTED が誤発火・見逃し）を述べている<br>**補足**: 正本: 同ファイル :32-35<br>**検証**: 反証で維持: :86 の『欠落すると SECRETARY_RELAY_GAP_SUSPECTED が誤発火 / 見逃し』は :32-35 と同じ因果。:85 の『3 層を独立に維持』は :33『3 層に同時記録』と :47『3 層同時、欠落禁止』が包含する。dispatcher 側は自前の文（.dispatcher/CLAUDE.md:164-168）を持っていて、この行には依存していない | 2 |
| .claude/skills/org-escalation/SKILL.md:87-87 | 『3. ブロック報告』と重複した場合は本フロー優先 | outdated | git grep '3. ブロック' のヒットは本ファイルと .in だけ。org-delegate/SKILL.md の Step 5 は #### 1.（:328）と #### 2a.（:340）の構成で、判断仰ぎ・ブロッカーは『(0)』と呼ばれている（:330）<br>**補足**: 『ブロッカー報告も本スキルで扱う（org-delegate Step 5 の判断仰ぎ・ブロッカー (0) の正本）』とする。該当する見出しはもう無い<br>**検証**: 反証で維持: '3. ブロック報告' を git grep するとヒットは本ファイルと .in:81 だけ。org-delegate/SKILL.md の Step 5 は :324『#### 0. 判断仰ぎ・スコープ拡張・ブロッカー』/ :328『#### 1.』/ :340『#### 2a.』/ :369『#### 2b / 2c』の構成。ack-template.md:70 の『### ブロック報告 ack』には番号が付いていない。主張どおり参照先は org-delegate Step 5 の (0)。ただし見出しは :330 ではなく :324 | 1 |
| .claude/skills/org-escalation/SKILL.md:88-94 | 発動条件外 | keep | リンク先の org-delegate/SKILL.md:328 #### 1.、:340 #### 2a.、:375『ワーカー監視と介入判定』がすべて現存 | 7 |

### .claude/skills/org-attention-start/SKILL.md（240 行: keep 231 / move 8 / delete 0 / outdated 0 / transport 1）

| 行範囲 | 節 | 分類 | 根拠 | 行数 |
|---|---|---|---|---|
| .claude/skills/org-attention-start/SKILL.md:1-23 | frontmatter | keep | .in が無い手保守のスキル。allowed-tools は mcp__renga-peers__* で、ほかの生成スキル（org-broker 名）と輸送層の表記が揃っていない（judgments 参照） | 23 |
| .claude/skills/org-attention-start/SKILL.md:24-30 | タイトル・導入 | keep | claude-org-runtime 0.1.49 の attention/cli.py が watch コマンドを提供 | 7 |
| .claude/skills/org-attention-start/SKILL.md:31-31 | 輸送層両系注記（既定 renga / opt-in broker・二フレーム・Epic #6 Issue G） | transport | 1 行に詰めた長文の両系注記。ほかの 3 ファイルとは既定の向きが逆（renga を既定と書いている） | 1 |
| .claude/skills/org-attention-start/SKILL.md:32-37 | 前提: cwd = repo root、spawn で cwd='.' | keep | Step 3 :128-130 の cwd 規則の前提 | 6 |
| .claude/skills/org-attention-start/SKILL.md:38-45 | 設計判断（sidecar 採用・人間判断 2026-05-17） | move | state.db を拡張しなかった理由の説明で手順は無い<br>**検証**: 反証で維持: :39-44 は sidecar を採用し state.db を拡張しなかった理由の説明。手順（sidecar に .state/attention_pane.json を使うこと）は Step 1 以降と skill の description が持っている。tests/ にこの節の文言のヒットは無い（sidecar のヒットは duplicate_sidecar のフィクスチャだけで無関係） | 8 |
| .claude/skills/org-attention-start/SKILL.md:46-71 | Step 1: 二重起動チェック | keep | renga-error-codes.md:28 に name_in_use がある（broker では name_taken、:163） | 26 |
| .claude/skills/org-attention-start/SKILL.md:72-90 | Step 2: 設定ファイル配置 | keep | tools/templates/attention.example.json が現存（HEAD 31a5c40 で更新） | 19 |
| .claude/skills/org-attention-start/SKILL.md:91-136 | Step 3: spawn_pane・--broker-state-dir をリテラルで足す・zsh の罠 | keep | runtime attention/cli.py:381,410 に --broker-state-dir がある。env を読まないことは cli.py:32-45 で確認（ORG_BROKER_STATE_DIR の参照が無い）。zsh の罠は #829 / git 3333b6d (#871) の記録あり。dispatcher の安定名は org-start/SKILL.md:215 | 46 |
| .claude/skills/org-attention-start/SKILL.md:137-148 | 失敗時の分岐（split_refused / pane_not_found / name_in_use） | keep | MIN_PANE_WIDTH=20 は pane-layout.md:40 と一致。broker では name_in_use ではなく name_taken が返る（renga-error-codes.md:163）が記載が無い（transport 判断に従属） | 12 |
| .claude/skills/org-attention-start/SKILL.md:149-206 | Step 4: 即時クラッシュ検出・起動失敗時の掃除（自タブ確立→identity→数値 close） | keep | runtime cli.py:385,392 に attention scan --dry-run --json がある。自タブ確立の記述（:179-187）は attention-stop:52-67 / pane-close.md:108-117 と同じ規則 | 58 |
| .claude/skills/org-attention-start/SKILL.md:207-229 | Step 5: sidecar 記録・journal | keep | sidecar は org-attention-stop と org-suspend Phase 3.7 が消費。attention_watch_started は runtime cli.py:247 が対になるイベントとして言及 | 23 |
| .claude/skills/org-attention-start/SKILL.md:230-240 | Step 6: 報告 | keep | docs/operations/attention-watch.md が現存 | 11 |

### .claude/skills/org-attention-stop/SKILL.md（175 行: keep 161 / move 7 / delete 0 / outdated 0 / transport 7）

| 行範囲 | 節 | 分類 | 根拠 | 行数 |
|---|---|---|---|---|
| .claude/skills/org-attention-stop/SKILL.md:1-28 | frontmatter | keep | allowed-tools は生成された org-broker 名の全セットで、spawn_claude_pane / send_message など本スキルが使わないものまで含む。編集は SKILL.md.in 側 | 28 |
| .claude/skills/org-attention-stop/SKILL.md:29-34 | タイトル・導入 | keep | リンク先の org-attention-start が現存 | 6 |
| .claude/skills/org-attention-stop/SKILL.md:35-41 | 輸送層両系注記 + 二フレーム注記 | transport | org-escalation:24-30 と同じ dual-system-header-long の生成物（broker 既定 / renga opt-in・Epic #6 Issue G） | 7 |
| .claude/skills/org-attention-stop/SKILL.md:42-44 | Step 1 見出し | keep | 見出し | 3 |
| .claude/skills/org-attention-stop/SKILL.md:45-51 | なぜ identity 確認が必要か（Issue #468） | move | 事故類型の説明。規則そのものは :78-79 と :94-96 で繰り返し述べられている<br>**検証**: 反証で維持: Issue #468 の事故類型（pane id の recycle）の説明。:49-50 の規則文（list_panes の name/role で確認したペインだけを close する）は、:78-79（sidecar の name は信用しない）、:80-86（verified / recycled / gone の分類）、:94-96（確認済み集合だけを close）で手順として同じ強さで書かれている | 7 |
| .claude/skills/org-attention-stop/SKILL.md:52-68 | 列挙を自タブと確立する（Group B MUST） | keep | git 59cf3af (#896)。close を実行するのはこのスキル。pane-close.md:108-117 と org-suspend:130-153 も同じ規則 | 17 |
| .claude/skills/org-attention-stop/SKILL.md:69-91 | Step 1 手順 1-4（確立・確認済み集合・分類） | keep | verified / recycled / gone の分類は org-suspend:540-544 に写されている（consumer） | 23 |
| .claude/skills/org-attention-stop/SKILL.md:92-123 | Step 2: close の実行・分類ごとの挙動 | keep | 数値 pane_id で close する規則（G-MUST） | 32 |
| .claude/skills/org-attention-stop/SKILL.md:124-148 | Step 3: sidecar 削除・journal | keep | git grep attention_watch_st のヒットはスキル 3 本だけで docs/journal-events.md には載っていない。runtime cli.py:245-271 も watcher 終了時に attention_watch_stopped を自分で書く（二重記録の可能性。judgments 参照） | 25 |
| .claude/skills/org-attention-stop/SKILL.md:149-175 | Step 4: 報告文面 | keep | ユーザー向け文面（verified / stale / 既停止） | 27 |

### .claude/skills/pr-watch-pane/SKILL.md（591 行: keep 452 / move 43 / delete 56 / outdated 0 / transport 40）

| 行範囲 | 節 | 分類 | 根拠 | 行数 |
|---|---|---|---|---|
| .claude/skills/pr-watch-pane/SKILL.md:1-22 | frontmatter (name/description/Bash allowed-tools) | keep | tools/journal_append.sh/.py, tools/pick_watcher_anchor.py 実在 (ls OK)。description は skill 一覧で常時読まれる。編集は SKILL.md.in 側 (diff で 23-27 以外同一) | 22 |
| .claude/skills/pr-watch-pane/SKILL.md:23-27 | allowed-tools mcp__org-broker__* | transport | 生成物は {{FQ}}→mcp__org-broker__ で render (diff SKILL.md.in:23-27 は mcp__renga-peers__)。実運用は renga (printenv ORG_TRANSPORT=renga)。生成フレームと運用フレームの二重。編集は .in 側 | 5 |
| .claude/skills/pr-watch-pane/SKILL.md:28-45 | 冒頭導入 + 監視結果の二経路 | keep | pr_watch.py:6 が events の ci_completed を書く、pr-watch.sh:87 が .state/pr-watch-<PR>.log。consumer は org-pull-request:83-105。.claude/rules/pr-ci-watch.md も同経路を正規とする | 18 |
| .claude/skills/pr-watch-pane/SKILL.md:46-54 | peer push は best-effort 注記 | move（当初 delete） | 同じ規則 (push は best-effort・events DB が正路・merge gate の前提にしない) が org-pull-request/SKILL.md:83-87 と本ファイル 39-45 にある。ORG_BROKER_STATE_DIR 欠落の話は本ファイル Step1 142-152 / Step3-1 256-268 が手順化済み<br>**補足**: 正本: .claude/skills/org-pull-request/SKILL.md:83-87（受信側の判定規則）。env の手順は本ファイル 142-152 / 256-268<br>**検証**: 反証で delete→move: 正本とされた org-pull-request:83-87 と本ファイル 39-45 は『push は best-effort・events DB が正路』しか書いていない。非既定 state dir で ORG_BROKER_STATE_DIR が無いと broker send が既定 .state/broker を掴み push が欠落する、という固有の理由は 142-152 / 263-268 にも pr-watch-pane rationale.md §3 にも無い（267 は空値 export の話で別件）。削除するとこの理由が失われるので、rationale へ move に寄せる。 | 9 |
| .claude/skills/pr-watch-pane/SKILL.md:55-61 | 輸送層両系注記 | transport | mcp__org-broker__ / renga の読み替え注記と references/transport-duality.md への誘導。broker/renga 併記 | 7 |
| .claude/skills/pr-watch-pane/SKILL.md:62-78 | spawn 対象は汎用 CLI ペイン（spawn 儀式なし・FQ 読み替え） | transport | broker の 2 段承認 / renga の 1 段承認の対比と、既定 broker / opt-in renga のツール名読み替え。二重記述そのもの | 17 |
| .claude/skills/pr-watch-pane/SKILL.md:79-88 | 前提: 窓口専用・cwd trap 吸収 | keep | contract docs/contracts/backend-interface-contract.md:315 で generic spawn_pane は secretary のみ。Step3 の command は tools/pr-watch.sh・.state/ を相対参照 (225 行) するので cwd 指定は今も必要 | 10 |
| .claude/skills/pr-watch-pane/SKILL.md:89-95 | 前提: 自己 close は tmux ペイン前提 | keep | command 末尾 tmux kill-pane -t "$TMUX_PANE" (225 行)。Issue #647 対応 5f75e3a (2026-07-03) | 7 |
| .claude/skills/pr-watch-pane/SKILL.md:96-118 | 前提: 自己 close の 2 分類 (tmux / herdr・wezterm・renga) | keep | 6cc24eb (2026-08-10, #916) で分類導入。renga 残留実測 2026-08-09 (rationale.md:7)。herdr/wezterm 枝の扱いは judgments 参照 | 23 |
| .claude/skills/pr-watch-pane/SKILL.md:119-126 | renga では窓口 close も条件付き / Windows 手動経路 | keep | 契約 T-§4.2 Group B fail-safe は MUST (59cf3af 2026-08-09, #896)。tools/pr-watch.ps1 実在 | 8 |
| .claude/skills/pr-watch-pane/SKILL.md:127-153 | Step 1: 引数解決と cwd / repo / transport env 捕捉 | keep | Step3 command の export 前置の値源。peer_notify.py:85,132-136 が resolve() で ORG_TRANSPORT を読む (58d8eda 2026-08-19 #945)。経緯は既に rationale.md §3 | 27 |
| .claude/skills/pr-watch-pane/SKILL.md:154-165 | Step 2: 冪等チェック | keep | pane name pr-watch-<PR> を冪等キーとして使う手順。watcher-cleanup.md・org-pull-request:146 も同 name で識別 | 12 |
| .claude/skills/pr-watch-pane/SKILL.md:166-198 | Step 3-0: 幾何でアンカー決定 | keep | tools/pick_watcher_anchor.py:97(--panes-json),554(capacity_exhausted),92(--transport) 実在。8e50401 (2026-08-29 #977)、2026-08-30 実害 (rationale.md:13) | 33 |
| .claude/skills/pr-watch-pane/SKILL.md:199-204 | pane_cap_reached は助言 | keep | pick_watcher_anchor.py:158 _FALLBACK_RENGA_MAX_PANES=16, :542-554 で renga のみ ceiling | 6 |
| .claude/skills/pr-watch-pane/SKILL.md:205-213 | 床値と候補優先度 | keep | pick_watcher_anchor.py:160-170 が claude_org_runtime.dispatcher.runner から MIN_PANE_* 等を import、docstring :40-52 が優先度を記述 | 9 |
| .claude/skills/pr-watch-pane/SKILL.md:214-228 | Step 3-1: spawn 呼び出し形 | keep | command 文字列が手順の実体。pr-watch.sh:6,26,43 に --merge-watch / --no-detach | 15 |
| .claude/skills/pr-watch-pane/SKILL.md:229-236 | nocorrect 前置 (必須・置換禁止) | keep | d12a1e3 (2026-08-06, Closes #825) で根治、2026-08-06 PR #824 で実発生 (rationale.md:29) | 8 |
| .claude/skills/pr-watch-pane/SKILL.md:237-251 | nocorrect 3 要素の理由 (非 zsh 互換 / 2>/dev/null / \|\| true) | move | 各要素は 225 行の command 文字列に既に固定。本段は『なぜ省略不可か』の説明で、実機確認部分は rationale.md:31-35 に既にある。省略禁止の 1 行だけ残せば手順は成立 (推測: 3 行程度に圧縮可)<br>**検証**: 反証で維持: 3 要素は 225 行の固定 command 文字列に入っており、229『必須・置換禁止』と 252-254『常に行頭に残す』が手順を担っている。237-251 は理由の説明で、rationale.md:31-35 §4 に実証の記録がある。2>/dev/null と \|\| true は省略禁止の MUST なので、各 1 行は本文に残す前提で move を支持する。 | 15 |
| .claude/skills/pr-watch-pane/SKILL.md:252-255 | 犠牲文は固定文字列 | keep | 窓口が placeholder と誤認して置換しないための手順制約。225 行と対 | 4 |
| .claude/skills/pr-watch-pane/SKILL.md:256-268 | env 前置注入と条件付き前置 | keep | peer_notify.py:132-136 resolve() 経由、空 export の誤動作回避。PR #73 障害・Refs #941 (58d8eda 2026-08-19) | 13 |
| .claude/skills/pr-watch-pane/SKILL.md:269-274 | 多層防御の位置づけ | move | 同内容が rationale.md:25 に既にある (path A 修理 / relay / notify_failed)。手順ではなく位置づけの説明。本文側は削るだけで足りる<br>**検証**: 反証で維持: rationale.md:25 §3 に path A 修理 / (B) relay / notify_failed がほぼ同じ文で書かれている。269-274 には実行すべき指示が無く、位置づけの説明だけ。move（実質は本文側の削除）で問題ない。 | 6 |
| .claude/skills/pr-watch-pane/SKILL.md:275-277 | target/direction は Step 3-0 の候補を使う | keep | pick_watcher_anchor.py 出力の consumer | 3 |
| .claude/skills/pr-watch-pane/SKILL.md:278-283 | なぜ固定アンカーをやめたか（Refs #335） | delete | grep '幅 24': SKILL.md:279 と references/rationale.md:13 にほぼ同文。172 行が既に rationale §2 を参照している<br>**補足**: 正本: .claude/skills/pr-watch-pane/references/rationale.md:13<br>**検証**: 反証で維持: rationale.md:13 §2 と同じ文（2026-08-30 / 幅 24 / 397x53 / renga#335）で、172 行は既に §2 を参照している。git grep '固定アンカー\|幅 24' -- tests tools .hooks はヒット 0 件なので、消してもテストは壊れない。 | 6 |
| .claude/skills/pr-watch-pane/SKILL.md:284-288 | role=watcher / cwd | keep | role=watcher は org-pull-request:147 の identity 照合、pick_watcher_anchor の候補選定で消費 | 5 |
| .claude/skills/pr-watch-pane/SKILL.md:289-293 | --no-detach 必須 (Issue #650) | keep | pr-watch.sh:12 既定 setsid+nohup 自己 detach、:60 で no_detach 分岐 | 5 |
| .claude/skills/pr-watch-pane/SKILL.md:294-300 | Ctrl-b s / renga サイドバー注記 | delete | 人間の覗き方の注記で窓口の手順ではない。同内容が docs/operations/dispatcher-view.md:55 §4『外側フレームが renga の場合』と :199 にあり、本文も参照している (3b1e396 2026-08-08)<br>**補足**: 正本: docs/operations/dispatcher-view.md:55- (§4)<br>**検証**: 反証で維持: docs/operations/dispatcher-view.md §4（55 行〜、renga のソース行まで出典付き）と §8.3（199 行）が、同じ回避策の `[ui] org_sidebar="off"` / prefix 変更を正本として書いている。本段はそこへ誘導しているだけで、窓口の手順ではない。tests / tools / .hooks に依存する箇所は無い（tools/org-dispatcher-view.sh:59,178 は自前の文言を持つ）。 | 7 |
| .claude/skills/pr-watch-pane/SKILL.md:301-306 | command: tee 二段 + 自己 close | keep | 225 行 command の説明。tmux backend のみ効く点は 96-118 と同旨だが短い | 6 |
| .claude/skills/pr-watch-pane/SKILL.md:307-315 | $TMUX_PANE は実行時 env / 非 tmux で無言失敗 | keep | 置換禁止の手順制約。Issue #911 renga 残留 2026-08-09 (rationale.md:7) | 9 |
| .claude/skills/pr-watch-pane/SKILL.md:316-321 | socket を -L で固定しない理由 | move | 制約自体は 225 行の command 文字列 (tmux kill-pane に -L 無し) に固定済み。本段は理由説明のみ<br>**検証**: 反証で維持: 制約は 225 行の固定 command（tmux kill-pane に -L 無し）に組み込まれていて、窓口はこの文字列を編集しない。316-321 は socket 解決の理由説明だけで、別に実行すべき指示は無い。 | 6 |
| .claude/skills/pr-watch-pane/SKILL.md:322-330 | self-close は tmux 層だけ掃除 (stale binding) | keep（当初 delete） | stale binding の症状と裸 name allowlist は Step3 360-366 と Step5(b) 549-553 で再掲され、本段自身が『3 条件と扱いは Step 5 の (b) が SoT』と書く<br>**補足**: 正本: 同ファイル 549-591 (Step 5 (b))<br>**検証**: 反証で delete→keep: 327 行の『そこで撃つ close_pane(target="pr-watch-<PR>")』は tools/check_group_b_selectors.py:167-175 の ALLOWLIST にある文脈文字列で、--self-close の説明として契約に記録された 3 件のうちの 1 件。消すと allowlist が stale になって exit 1 になり（同 :54,:77-78）、tools/test_check_group_b_selectors.py:603-618 RepositoryRegressionTest が落ちる。521 行もこの段を名指しで参照している。 | 9 |
| .claude/skills/pr-watch-pane/SKILL.md:331-338 | --merge-watch / 不変条件 | keep | pr-watch.sh:6 --merge-watch、pr_watch.py:122 PR_MERGE_WATCH_TIMEOUT | 8 |
| .claude/skills/pr-watch-pane/SKILL.md:339-343 | 戻り値控え / spawn 失敗分岐の導入 | keep | pane_id は watcher-cleanup.md:9 の束縛で消費。renga-error-codes.md 実在 | 5 |
| .claude/skills/pr-watch-pane/SKILL.md:344-355 | [split_refused] 候補を順に降りる | keep | 2026-08-30 の誤読実害 (rationale.md:13)、8e50401 #977。rules/pr-ci-watch.md:4 も立てられない時は人間報告 | 12 |
| .claude/skills/pr-watch-pane/SKILL.md:356-366 | [pane_not_found] / [name_taken] 切り分け 1-3 | keep | 5f75e3a (2026-07-03 #647) で stale 登録簿掃除経路を明記 | 11 |
| .claude/skills/pr-watch-pane/SKILL.md:367-381 | [name_taken] 分岐内の 3 条件再掲 | delete | 条件 (1)(2)(3) と DEFAULT_TRANSPORT 推定禁止が 557-578 とほぼ同文。本段自身が『条件の並びと根拠の SoT は下記 Step 5 の (b)』(372, 377 行)<br>**補足**: 正本: 同ファイル 557-578。本段は『Step 5 (b) の 3 条件が揃えば』の 1 行参照に置換<br>**検証**: 反証で維持: 条件 (1)(2)(3)・『DEFAULT_TRANSPORT から推定しない』・『確定できなければ不成立』は 557-578 に同じ強さで書かれ、本段自身も 372 / 377 行で Step 5 (b) を SoT と書いている。allowlist の文脈『で name 解決させて登録簿を』は 382 行で範囲外なので、check_group_b_selectors は壊れない。1 行の参照に置き換える前提で支持する。 | 15 |
| .claude/skills/pr-watch-pane/SKILL.md:382-385 | 3 条件成立時: close → spawn 1 回再試行 | keep | Step 3 固有の自己回復動作 (再試行 1 回・続けば中断)。5(b) には無い | 4 |
| .claude/skills/pr-watch-pane/SKILL.md:386-391 | broker 以外では close せず報告 | delete | 583-588 と同旨 (renga legacy 解決の hazard / rationale §7 参照)<br>**補足**: 正本: 同ファイル 583-588<br>**検証**: 反証で維持: 583-588 に同じ規則がある（broker 以外は裸 name に頼らず close しない・報告する、根拠は rationale §7）。本段に固有の『自動再試行もしない』は、close しないで報告して中断する以上、自明に従う。allowlist の文脈文字列は含まない。 | 6 |
| .claude/skills/pr-watch-pane/SKILL.md:392-395 | broker 固有エラーコード | transport | [no_backend]/[token_invalid] 等 broker 拡張コードの列挙。CLAUDE.md『輸送層両系』エラー分岐と同じ二重記述 | 4 |
| .claude/skills/pr-watch-pane/SKILL.md:396-401 | Step 4-1: identity 確定 | keep | name/role は Step2 冪等・Step5 cleanup で消費 | 6 |
| .claude/skills/pr-watch-pane/SKILL.md:402-409 | 補修前の自タブ確立 (定義文) | delete | grep '単一タブモデル内で解決\|caller_scope` を確立': 本ファイル 120/374/408/443/534/559、org-pull-request:141、org-attention-start:183、org-attention-stop:59、org-suspend:138 に同じ定義<br>**補足**: 正本候補: 同ファイル 530-537 (Step 5 (a)) または契約 T-§4.2。複数スキル横断は judgments 参照<br>**検証**: 反証で維持: (i) org-broker の単一タブ解決 / (ii) caller_scope の定義は 530-537 と同じ文。ただし 405-407 の『spawn の戻り値として id が手元にあっても免除にならない』は 530-537 に無く、446-448 にしか残らない。Step 4-1 にこの 1 文を残し、定義は 530-537 への参照にすること。テストに依存する箇所は無い（git grep で該当句は .hooks/block-relative-close-pane.sh:114 の契約名だけ）。 | 8 |
| .claude/skills/pr-watch-pane/SKILL.md:410-417 | 補修不成立時の扱い / focused 既定の罠 / peer 非保持 | keep | set_pane_identity の target 既定 focused で別ペイン改名する固有の hazard。契約 T-§4.2 MUST (#896) | 8 |
| .claude/skills/pr-watch-pane/SKILL.md:418-440 | Step 4-2: 全画面 inspect と起動失敗判定 | keep | Issue #825 (d12a1e3 2026-08-06)、末尾読みで見逃した実例 rationale.md:39 | 23 |
| .claude/skills/pr-watch-pane/SKILL.md:441-444 | 失敗時掃除: 自タブ確立 (定義文) | delete | 402-409 と同じ定義の 3 度目 (grep 443-444 行ヒット)<br>**補足**: 正本: 同ファイル 530-537<br>**検証**: 反証で維持: 441-444 は 530-537 の (i)/(ii) 定義と同じ文。固有の『どちらも成立しなければ close を撃たない・id 免除なし』は 445-448 にあって範囲外なので残る。テストに依存する箇所は無い。 | 4 |
| .claude/skills/pr-watch-pane/SKILL.md:445-456 | 失敗時掃除: 不成立時報告 / identity 再確認 / 数値 close | keep | pane_id recycle 対策 (#896 全数移行) | 12 |
| .claude/skills/pr-watch-pane/SKILL.md:457-471 | Step 4-3: 副作用ベース生存確認 | keep | pgrep 偽陽性 2026-08-06 (rationale.md:41)。tee がログ生成 (225 行) | 15 |
| .claude/skills/pr-watch-pane/SKILL.md:472-498 | Step 5-1: journal 記録 + watcher_restart_guard check | keep | watcher_restart_guard.py:25,74 が pr_watch_pane_started を読み欠落で missing、:268-288 exit 0/2/3。044a48f (2026-08-29 #979) | 27 |
| .claude/skills/pr-watch-pane/SKILL.md:499-511 | Step 5-2: ユーザー報告テンプレ | keep | 報告文。/org-attach は broker tmux 前提 (org-attach description) で renga 運用では不適用 → judgments | 13 |
| .claude/skills/pr-watch-pane/SKILL.md:512-521 | Step 5-3 冒頭: 自己 close 分類の再掲 | delete | tmux / herdr・wezterm・renga の 2 分類と renga caller_scope 依存は前提節 96-123 の再掲 (本段自身が『前掲「前提」節の 2 分類』と参照)<br>**補足**: 正本: 同ファイル 96-123<br>**検証**: 反証で維持: 2 分類と『renga は caller_scope に依存する』は 96-123（とくに 119-123）の再掲で、本文自身が『前掲「前提」節』と書いている。ただし 512 行の番号付き見出しと、522 行以降の freshness gate（watcher-cleanup.md が SoT）は手順なので残すこと。 | 10 |
| .claude/skills/pr-watch-pane/SKILL.md:522-529 | イベント駆動 close は freshness gate 併用 | keep | watcher-cleanup.md が SoT と明示 (watcher-cleanup.md:3)。f687e8c (2026-08-25 #957) | 8 |
| .claude/skills/pr-watch-pane/SKILL.md:530-541 | (a) live pane の close (自タブ確立→数値 id) | keep | 契約 T-§4.2 Group B fail-safe MUST (#896, 2026-08-09)。本スキル内の自タブ確立定義の正本候補 | 12 |
| .claude/skills/pr-watch-pane/SKILL.md:542-548 | (a) は列挙に出るから安全ではない | move | 理由説明。機序は rationale.md:55-57 §7 に既にある<br>**検証**: 反証で維持: 『(a) でも自タブ確立が要る』という MUST は 530-537 の手順に含まれている。542-548 は (a) と (b) の違いの理由説明で、仕組みは rationale.md:55-57 §7 にある。新しい実行指示は無い。 | 7 |
| .claude/skills/pr-watch-pane/SKILL.md:549-565 | (b) stale binding allowlist 本体 + 条件 (1) 積極証拠 | keep | 本節が 3 条件の SoT と自己宣言 (553 行)。契約 T-§4.2 stale-binding carve-out | 17 |
| .claude/skills/pr-watch-pane/SKILL.md:566-572 | DEFAULT_TRANSPORT から推定してはならない | transport | MUST NOT の根拠が『運用既定 renga / コード既定 broker』二フレーム (transport.py:19-20 DEFAULT_TRANSPORT=broker)。理由部は rationale.md:47-51 に重複 | 7 |
| .claude/skills/pr-watch-pane/SKILL.md:573-591 | (b) 確定不能時 / 条件 (2)(3) / 実行 / broker 以外 / retained id 不使用 | keep | 条件 (2)(3) と fail-safe 規律。契約 T-§4.2 'MUST gate it on all three conditions together' (org-pull-request rationale.md:43) | 19 |

### .claude/skills/pr-watch-pane/references/rationale.md（57 行: keep 57 / move 0 / delete 0 / outdated 0 / transport 0）

| 行範囲 | 節 | 分類 | 根拠 | 行数 |
|---|---|---|---|---|
| .claude/skills/pr-watch-pane/references/rationale.md:1-4 | 冒頭: 由来の記録 | keep | SKILL.md から 10 箇所参照 (grep 'references/rationale.md' SKILL.md)。b9eca49 (2026-09-12 #1010) で移設された move 先 | 4 |
| .claude/skills/pr-watch-pane/references/rationale.md:5-10 | §1 自己 close が効かない環境のゾンビ残留 | keep | SKILL.md:110 から参照。2026-07-22 herdr / 2026-08-09 renga 実測 | 6 |
| .claude/skills/pr-watch-pane/references/rationale.md:11-18 | §2 なぜ固定アンカーをやめたか | keep | SKILL.md:172,194 から参照。SKILL.md:278-283 の重複の正本 | 8 |
| .claude/skills/pr-watch-pane/references/rationale.md:19-26 | §3 env 前置注入の由来 | keep | SKILL.md:151,273 から参照。peer_notify.py:132-136 resolve() と整合 (未設定=broker) | 8 |
| .claude/skills/pr-watch-pane/references/rationale.md:27-36 | §4 nocorrect 前置の実証 | keep | SKILL.md:236 から参照。#825 2026-08-06 | 10 |
| .claude/skills/pr-watch-pane/references/rationale.md:37-42 | §5 全画面 inspect / pgrep 偽陽性の由来 | keep | SKILL.md:423,462 から参照 | 6 |
| .claude/skills/pr-watch-pane/references/rationale.md:43-52 | §6 裸 name carve-out の契約由来 / 推定禁止の理由 | keep | SKILL.md:556,572 から参照。49 行は運用既定 renga / コード既定 broker の二フレームに依拠 (transport 判断の影響を受ける) | 10 |
| .claude/skills/pr-watch-pane/references/rationale.md:53-57 | §7 pre-capability renga legacy 解決の hazard | keep | SKILL.md:388,547,586 から参照。契約 backend-interface-contract.md:209 の legacy fall-through 記述と整合 | 5 |

### .claude/skills/org-pull-request/SKILL.md（345 行: keep 322 / move 2 / delete 7 / outdated 5 / transport 9）

| 行範囲 | 節 | 分類 | 根拠 | 行数 |
|---|---|---|---|---|
| .claude/skills/org-pull-request/SKILL.md:1-26 | frontmatter (description / Bash allowed-tools) | keep | set_run_pr_open.py / run_complete_on_merge.py / journal_append.* 実在 (ls OK)。編集は SKILL.md.in 側 | 26 |
| .claude/skills/org-pull-request/SKILL.md:27-29 | allowed-tools: bash tools/pr-watch.sh / pwsh・powershell pr-watch.ps1 | delete | .hooks/block-adhoc-pr-watch.sh:47-49 が Bash の tools/(pr-watch.(sh\|ps1)\|pr_watch.py) を deny (settings.json:92-111 で配線)。.claude/rules/pr-ci-watch.md:5 も直接起動禁止。許可しても必ず hook で拒否される死んだ許可<br>**検証**: 反証で維持: .hooks/block-adhoc-pr-watch.sh:51-53 は Bash で tools/(pr-watch.(sh\|ps1)\|pr_watch.py) にマッチしたら必ず deny する（settings.json:92-111 で配線、tests/test-block-adhoc-pr-watch.sh:78-83 で固定）。rules/pr-ci-watch.md:9 も直接起動を禁じている。人間が ! で起動する経路は allowed-tools と関係ない。git grep で依存はヒット 0 件。編集は SKILL.md.in:27-29 側で行う。 | 3 |
| .claude/skills/org-pull-request/SKILL.md:30-35 | allowed-tools mcp__org-broker__* | transport | 生成物は mcp__org-broker__ で render、運用は renga (printenv ORG_TRANSPORT=renga) | 6 |
| .claude/skills/org-pull-request/SKILL.md:36-46 | 冒頭 / T5 contract / ack≠承認 | keep | docs/contracts/delegation-lifecycle-contract.md:43 §1.5, :89 T5 実在。ack≠承認 は CLAUDE.md『ワーカー peer message を受けたら』にも再掲 (CLAUDE.md 側が重複) | 11 |
| .claude/skills/org-pull-request/SKILL.md:47-48 | 輸送層両系注記 | transport | 受信モデル等 3 分岐と references/transport-duality.md への誘導 | 2 |
| .claude/skills/org-pull-request/SKILL.md:49-52 | 2b-i 見出し・明示承認 | keep | 人間ゲート。CLAUDE.md『ack ≠ user 承認』と対 | 4 |
| .claude/skills/org-pull-request/SKILL.md:53-53 | push / PR 作成・PR 本文は英語規約 | keep | git grep feedback_pr_issue_english: 本行と .in のみ、repo 内に定義無し (operator memory)。直近 31a5c40 等の squash 本文は日本語 (git log -1 --format=%B 31a5c40)。推測: 規約が現運用とずれている → judgments | 1 |
| .claude/skills/org-pull-request/SKILL.md:54-65 | --base / set_run_pr_open back-fill / 手打ちイベント / /pr-watch-pane 起動 / 多層受信 (B) | keep | gen_delegate_payload.py:233 base_branch (ff504aa #809)、set_run_pr_open.py:245 source=、writer.py:563 set_run_pr、worker-monitoring.md:1181 Step 5.25 relay、28aebea (#831) | 12 |
| .claude/skills/org-pull-request/SKILL.md:66-66 | (A) 直 push 補助: peer_notify は raw env 判定 | outdated | 本行は『tools/peer_notify.py は raw env 判定のため』と書くが、peer_notify.py:7-8,85,132-136 は tools.transport.resolve() で分岐し raw env (:147) は resolve 不能時のフォールバックのみ。58d8eda (2026-08-19 #945)<br>**補足**: peer_notify.py は resolve() 経由 (explicit > ORG_TRANSPORT > DEFAULT_TRANSPORT=broker)。raw env 分岐は runtime 不在等の never-raise フォールバックのみ (peer_notify.py:90-147、CLAUDE.md 二フレーム注記『Refs #941 で解消済み』)<br>**検証**: 反証で維持: tools/peer_notify.py:85-87 は _resolve_transport() で分岐し、:131-136 で tools.transport.resolve() を使う。raw env 判定（:139-147 _raw_env_transport）は、runtime を import できない / ValueError のときのフォールバックに限られる。rationale.md:15 §2 自身が『Refs #941 で raw env 判定をやめ』と書いている。 | 1 |
| .claude/skills/org-pull-request/SKILL.md:67-69 | (A)(B) 二重着信は既定 / フォールバック poll / 終端分岐 | keep | 2026-07-30〜08-19 の片系死の記録 (本文)、f687e8c (2026-08-25 #957)。pr_watch.py:148,1530 HEAD_UNCONFIRMED、relay_scan.py:206 | 3 |
| .claude/skills/org-pull-request/SKILL.md:70-70 | ORG_TRANSPORT=renga の場合 | transport | opt-in renga 時の読み替え (運用実態は renga) | 1 |
| .claude/skills/org-pull-request/SKILL.md:71-106 | awaiting_user emit / 理解サマリ再掲 / merge-watch / status 据え置き / dogfood / CI 完了検知の正路 (events poll) | keep | org-delegate/SKILL.md:361 が Human Understanding Summary を永続化、journal_append.py notify_sent、pr_watch.py:78 events 書込。dogfood は judgments (register 未使用) | 36 |
| .claude/skills/org-pull-request/SKILL.md:107-110 | ⚠️ cwd 注意: state.db 相対パスでクラッシュ / Issue #398 対応中 | outdated | 7c8d639『make state.db tools cwd-independent (Closes #398)』。pr_watch.py:187-198 は REPO_ROOT=Path(__file__).parent.parent と resolve_state_db_path()、pr-watch.sh:34-35,87 は REPO_ROOT 基点<br>**補足**: pr_watch.py / pr-watch.sh は cwd 非依存 (Closes #398, 7c8d639)。cwd が要るのは pr-watch-pane の command が相対パスで tee/起動する点のみ (pr-watch-pane SKILL.md:225)<br>**検証**: 反証で維持: 7c8d639『make state.db tools cwd-independent (Closes #398)』。tools/pr_watch.py:187-198 は REPO_ROOT と resolve_state_db_path() を使い、tools/state_db/discover.py:151-167 は env または discover_repo_root() で絶対パスを返す。pr-watch.sh:35,87 も REPO_ROOT が基点。『相対パスで開く』『#398 対応中』は事実と合わない。 | 4 |
| .claude/skills/org-pull-request/SKILL.md:111-114 | ⚠️ 監視は専用ペインで (Bash 背景起動禁止) | delete | 同規則が本ファイル 63 行・.claude/rules/pr-ci-watch.md:3-5・CLAUDE.md『PR 後の CI 監視』にあり、Bash からの tools/pr-watch.* 起動は .hooks/block-adhoc-pr-watch.sh:47-49 が機械的に deny<br>**補足**: 正本: .claude/rules/pr-ci-watch.md (+ hook)。手順上は本ファイル 63 行<br>**検証**: 反証で維持: rules/pr-ci-watch.md:7-9、CLAUDE.md:41、本ファイル 63 行に同じ規則があり、hook も機械的に deny する。固有の『Bash ラッパーの完了通知は監視本体の完了ではない』は、hook が起動自体を拒否するので前提が成立しない。確定を events 行で行う規則は 87 行にある。ここ以外の依存は無い（git grep で SKILL.md / .in のみヒット）。 | 4 |
| .claude/skills/org-pull-request/SKILL.md:115-149 | 監視終端イベント駆動 close: トリガ / freshness / 識別子束縛 close | keep | 57c9ac0 (2026-07-22 #751)、f687e8c (#957)、watcher-cleanup.md が SoT。自タブ確立定義 138-145 は pr-watch-pane と重複 (judgments) | 35 |
| .claude/skills/org-pull-request/SKILL.md:150-170 | stale binding 裸 name 3 条件 + harness 追加要件 | keep | 160-168 の harness 追加要件 (再 spawn しない cleanup では条件(2)不成立) は本スキル固有。条件 (1)(2)(3) 本文は pr-watch-pane:557-588 と重複 → judgments | 21 |
| .claude/skills/org-pull-request/SKILL.md:171-205 | worker CLOSE_PANE との区別 / MERGE_WATCH_TIMEOUT / HEAD_UNCONFIRMED gate | keep | pr_watch.py:122,148,1530-1537、契約 :315 secretary の spawn_pane ops tier | 35 |
| .claude/skills/org-pull-request/SKILL.md:206-207 | 既存 PR_MERGED / NO_RUN 経路は影響を受けない | move | 機能追加時 (Issue #639) の変更範囲注記で手順ではない。分岐自体は 69 行で手順化済み<br>**検証**: 反証で維持: Issue #639 で追加したときの変更範囲の注記。PR_MERGED / PR_MERGED_NO_RUN の分岐は 69 行で手順として書かれていて、ここに新しい実行指示は無い（delete でもよい程度）。 | 2 |
| .claude/skills/org-pull-request/SKILL.md:208-266 | 2c フィードバックループ (watcher 掃除 / restart guard / WORKER_REOPENED / in_use / イベント) | keep | watcher_restart_guard.py:268-288、journal_append.py:23-28,109 fix_pushed post-check (044a48f 2026-08-29 #979)、worker-monitoring.md:221 WORKER_REOPENED、writer.py update_run_status | 59 |
| .claude/skills/org-pull-request/SKILL.md:267-301 | 2b-ii クローズ条件 / pr_merged 手打ち禁止 / CLOSE_PANE / watcher 掃除 / worktree remove --force | keep | contract §1.5 (:43)、run_complete_on_merge.py が pr_merged を同一 tx で記録、f687e8c (#957)、rationale.md §6 (#491) | 35 |
| .claude/skills/org-pull-request/SKILL.md:302-303 | Pattern C CLAUDE.local.md cleanup / dogfood paired issue | keep | run_complete_on_merge.py:101-157 cleanup_pattern_c_local_md(worker_dir_abs=)。dogfood は registry/dogfood_pending.md に例示行のみ (最終更新 b78fda0 2026-05-08) → judgments | 2 |
| .claude/skills/org-pull-request/SKILL.md:304-332 | run_complete_on_merge 呼び出し / StateWriter 最終 close | keep | run_complete_on_merge.py:72 no_run、:206,214 exit 2、writer.py:54 claude_org_root kwarg | 29 |
| .claude/skills/org-pull-request/SKILL.md:333-333 | legacy ハンドロール完了スクリプト museum | keep（当初 move） | docs/legacy/pr-merge-completion-manual.md 実在 (ls OK) だが標準経路外の経緯説明。手順には不要<br>**検証**: 反証で move→keep: 経緯の説明だけではない。『museum copy を使うのは Issue を切ってユーザー判断を仰いだ後に限る』という、窓口の行動に効く gate が含まれている。ls で docs/legacy/pr-merge-completion-manual.md が現存するので、この制約は今も意味を持つ。1 行なので keep に寄せる。 | 1 |
| .claude/skills/org-pull-request/SKILL.md:334-337 | パターン別 lifecycle / snapshot 自動再生成 | keep | writer.py:166-170 post_commit_regenerate | 4 |
| .claude/skills/org-pull-request/SKILL.md:338-345 | 2b-iii proactive next-dispatch | keep | work_discovery_scan.py:442 trigger, :123 --free-panes, :1783 generated_for。e40b40c (2026-09-30 #1029)。CLAUDE.md『PR マージ後の次タスク提案』が同内容を重複保持 (CLAUDE.md 側が削減候補) | 8 |

### .claude/skills/org-pull-request/references/rationale.md（43 行: keep 39 / move 0 / delete 4 / outdated 0 / transport 0）

| 行範囲 | 節 | 分類 | 根拠 | 行数 |
|---|---|---|---|---|
| .claude/skills/org-pull-request/references/rationale.md:1-4 | 冒頭 | keep | SKILL.md から 8 箇所参照 (grep 'references/rationale.md')。b9eca49 (#1010) の move 先 | 4 |
| .claude/skills/org-pull-request/references/rationale.md:5-10 | §1 --repo 解決 | keep | SKILL.md:60,309 から参照。2026-08-06 renga PR #302 汚染 (28aebea #831) | 6 |
| .claude/skills/org-pull-request/references/rationale.md:11-16 | §2 peer_notify が resolve() 経由になった経緯 | keep | SKILL.md:85 から参照。peer_notify.py:132-136 と整合 | 6 |
| .claude/skills/org-pull-request/references/rationale.md:17-20 | §3 watcher イベント駆動 close | delete | 同じ 2026-07-22 PR #154/#749/#750 herdr 残留の記録が .claude/skills/pr-watch-pane/references/rationale.md:7 にあり (renga 残留も含むより完全な版)。SKILL.md:121 の参照先を差し替える必要あり<br>**補足**: 正本: .claude/skills/pr-watch-pane/references/rationale.md:5-9 (§1)<br>**検証**: 反証で維持: pr-watch-pane/references/rationale.md:5-9 §1 は、同じ 2026-07-22 PR #154/#749/#750 の herdr 残留に加えて renga 残留（PR #908）も含む上位の記録になっている。参照元は SKILL.md:121（.in:115）だけで、リンク先を差し替えれば足りる。tests / tools に依存は無い。 | 4 |
| .claude/skills/org-pull-request/references/rationale.md:21-26 | §4 pr_merged 手打ち禁止の理由 | keep | SKILL.md:287,308 から参照。f687e8c (#957)、relay_scan.py:206 | 6 |
| .claude/skills/org-pull-request/references/rationale.md:27-32 | §5 再 push 後 watcher 機械確認の理由 | keep | SKILL.md:224 から参照。2026-08-30 PR #73 インシデント | 6 |
| .claude/skills/org-pull-request/references/rationale.md:33-40 | §6 worktree remove --force / Pattern C 引数 | keep | SKILL.md:299,302 から参照。run_complete_on_merge.py:156-157 | 8 |
| .claude/skills/org-pull-request/references/rationale.md:41-43 | §7 裸 name 3 条件の SoT ポインタ | keep | pr-watch-pane rationale §6/§7 への 3 行ポインタ。ただし SKILL.md:150-170 は箇条書き以上を持っており本節の『二重に持たない』記述と食い違う → judgments | 3 |

### .claude/skills/org-pull-request/references/watcher-cleanup.md（36 行: keep 33 / move 3 / delete 0 / outdated 0 / transport 0）

| 行範囲 | 節 | 分類 | 根拠 | 行数 |
|---|---|---|---|---|
| .claude/skills/org-pull-request/references/watcher-cleanup.md:1-4 | 冒頭: 束縛規則の SoT 宣言 | keep | SKILL.md:130,136 と pr-watch-pane SKILL.md:527 から参照 | 4 |
| .claude/skills/org-pull-request/references/watcher-cleanup.md:5-10 | name で再導出してはならない理由と束縛 | keep | 再 push で新 pane_id の watcher が立つ運用 (SKILL.md:219) に対する誤 close 防止。f687e8c (#957) | 6 |
| .claude/skills/org-pull-request/references/watcher-cleanup.md:11-19 | freshness gate: イベント種別ごとの head フィールド | keep | pr_watch.py:1530-1531 HEAD_UNCONFIRMED は head と last CI-confirmed head を別出力 | 9 |
| .claude/skills/org-pull-request/references/watcher-cleanup.md:20-33 | 照合不能は superseded 扱いにしない | keep | relay_scan.py:206 UNVERIFIABLE_RELAY_TAIL、pr_watch.py:1498 head_tag='unknown'。Issue #954 (f687e8c 2026-08-25) | 14 |
| .claude/skills/org-pull-request/references/watcher-cleanup.md:34-36 | 掃除タイミングを遅らせない理由 | move | 即時掃除の規則は SKILL.md:128-130 にあり、本節は理由説明のみ。rationale.md へ統合可<br>**検証**: 反証で維持: 即時掃除の規則は SKILL.md:125-130 に手順としてある。34-36 は『watcher は既に exit 済み / 遅らせると herdr で残留する』という理由だけ。ただし SKILL.md:129-130 は『遅らせる影響』をこの節へリンクしているので、移すときにリンク先を更新すること。 | 3 |

### .claude/skills/org-setup/SKILL.md（215 行: keep 190 / move 4 / delete 0 / outdated 19 / transport 2）

| 行範囲 | 節 | 分類 | 根拠 | 行数 |
|---|---|---|---|---|
| .claude/skills/org-setup/SKILL.md:1-19 | frontmatter (allowed-tools) | keep | ls で tools/org_setup_prune.py / check_role_configs.py / ensure_projects_registry.py 全て現存。skill 本文 Step3.5/5/6 がこれらを呼ぶ（SKILL.md:77,113,200） | 19 |
| .claude/skills/org-setup/SKILL.md:20-25 | 冒頭導入 | keep | 2 行の目的記述。削る余地なし | 6 |
| .claude/skills/org-setup/SKILL.md:26-27 | 輸送層 両系注記（既定 renga / opt-in broker） | transport | renga/broker 二フレーム注記。加えて事実齟齬: 本行は『ORG_TRANSPORT 無設定=renga で恒等』と書くが tools/transport.py:19-20 は無設定=DEFAULT_TRANSPORT=broker、tools/org_setup_prune.py:754 も『ORG_TRANSPORT 未設定/broker で broker 射影』。no-op は TEMPLATE_TRANSPORT=renga 解決時のみ（transport.py:61, org_setup_prune.py:747-749） | 2 |
| .claude/skills/org-setup/SKILL.md:28-41 | 設定ファイルの配置先とスコープ | keep | prune の対象パス表と一致（org_setup_prune.py:15-16 secretary/dispatcher/curator、worker は org-delegate）。schema roles.*.description も同じパス（tools/org_extension_schema.json roles.secretary/dispatcher/curator） | 14 |
| .claude/skills/org-setup/SKILL.md:42-45 | 各ロールの必要設定（参照） | keep | references/permissions.md へのポインタ。permissions.md は org_setup_prune.py:1004 extract_role_blocks と check_role_configs.py:284 check_docs が機械的に読む | 4 |
| .claude/skills/org-setup/SKILL.md:46-60 | Step 1-2 現在設定の読取と差分 | keep | additive 手順本体。対象 4 ファイルは schema roles の settings パスと一致 | 15 |
| .claude/skills/org-setup/SKILL.md:61-67 | Step 3 マージして書き込む | keep | sandbox.network.allowedDomains は git ff6d785 (2026-09-30) で追加。check_role_configs.py:255-281 check_sandbox_network が欠落を ERROR にする（consumer 現存） | 7 |
| .claude/skills/org-setup/SKILL.md:68-87 | Step 3.5 registry/projects.md 生成 | keep | tools/ensure_projects_registry.py・registry/projects.example.md・docs/operations/registry-projects-migration.md 現存。.gitignore:20-27 が Issue #811 で projects.md を ignore。org-start/SKILL.md:21-22 も同コマンドを許可 | 20 |
| .claude/skills/org-setup/SKILL.md:88-102 | Step 4 結果報告テンプレート | keep | 出力書式の例。短い。tool 依存なし | 15 |
| .claude/skills/org-setup/SKILL.md:103-124 | Step 5 --prune モード | keep | org_setup_prune.py の --role/--all/--dry-run が argparse に現存（:1100-1126 付近）、Targets secretary/dispatcher/curator（:15）。permissions.md:246-252 に同内容の重複あり（そちらを delete） | 22 |
| .claude/skills/org-setup/SKILL.md:125-149 | user 拡張の保護 settings.local.override.json とマージ規則 | keep | org_setup_prune.py:170-171 override_path、:1019-1027 で読込、deep_merge/_merge_lists（:250-281）が記載どおり list 和集合・dict 重複排除・scalar overlay 勝ち | 25 |
| .claude/skills/org-setup/SKILL.md:150-154 | .gitignore 対象の注記 | outdated | 本文が『.gitignore:23-25 で override と .bak を ignore』と書くが .gitignore:23-25 は registry/projects.md のコメント。grep -n override .gitignore → 55,56<br>**補足**: .gitignore:55 `.claude/settings.local.override.json`、:56 `.claude/settings.local.json.bak.*`、:51 `.curator/.claude/`、:60 `.dispatcher/.claude/`。行番号付き引用をやめるか正しい行へ<br>**検証**: 反証で維持: grep -n override/bak .gitignore → 55 `.claude/settings.local.override.json`, 56 `.claude/settings.local.json.bak.*`, 51 `.curator/.claude/`, 60 `.dispatcher/.claude/`。.gitignore:21-30 は registry/projects.md のコメント。:23-25 という引用は誤り | 5 |
| .claude/skills/org-setup/SKILL.md:155-161 | check_role_configs の override 除外と安全契約 | outdated | 機構は現存（check_role_configs.py:235 _load_override_allow、org_setup_prune.py:966-990 _validate_target_safety）。だが例示が誤り: claude-peers 禁止は global.forbidden_allow_regex（schema global）で disallow_allow_regex ではなく、『現 renga-peers』は禁止対象でない（schema $comment_forbidden_allow_regex『renga-peers is the current allowed channel』）<br>**補足**: 『global.forbidden_allow_exact（Bash(git *) 等）/ global.forbidden_allow_regex（^mcp__claude-peers__）/ roles.<r>.disallow_allow_regex（^Bash(*)$ 等）は override でも ERROR』と書き直す。出典 tools/org_extension_schema.json global・roles.*.disallow_allow_regex<br>**検証**: 反証で維持: schema global.forbidden_allow_regex=['^mcp__claude-peers__']、global.forbidden_allow_exact=['Bash(git *)',…]。roles.*.disallow_allow_regex=['^Bash\(\*\)$','^Bash$'…] (schema:168,289)。$comment_forbidden_allow_regex(schema:15)が『renga-peers is the current allowed channel』と書く。『現 renga-peers 禁止』は誤り | 7 |
| .claude/skills/org-setup/SKILL.md:162-175 | {claude_org_path} の解決順 | keep | org_setup_prune.py detect_claude_org_path（env.CLAUDE_ORG_PATH → hook command の bash "<abs>/.hooks/" 正規表現）と _root_as_claude_org_path（required_hook_scripts 全実在時のみ）が記載順どおり。unresolved placeholders は :916 | 14 |
| .claude/skills/org-setup/SKILL.md:176-179 | root 採用条件の理由 | move | 『.hooks/ 実在だけだと無関係プロジェクトを受理し…』は設計理由。同旨が org_setup_prune.py の _root_as_claude_org_path docstring にある（手順には不要）<br>**検証**: 反証で維持: 176-179 は『.hooks/ 実在だけを条件にしない理由』の説明。手順(4 の条件・unresolved placeholders で中断)は :171-175 に残る。実装は tools/org_setup_prune.py:217 _root_as_claude_org_path | 4 |
| .claude/skills/org-setup/SKILL.md:180-191 | --claude-org-path 明示例 / バックアップ | keep | --claude-org-path・--no-backup が argparse に現存（org_setup_prune.py:1100,1105）。.bak 名は :334-335 strftime %Y%m%d-%H%M%S と一致 | 12 |
| .claude/skills/org-setup/SKILL.md:192-197 | Step 6 見出しと mode 説明（denyRead のみ） | outdated | 見出しと :196 が『denyRead に対してのみ』と書くが、同モードは denyWrite も処理する（org_setup_prune.py:23-27 docstring、:546-551 process_user_common_sandbox が denywrite_candidates を受ける、git 2751645 2026-05-11 #433）。:194 の『main pull 後 1 回必須』は 2026-05-11 の移行告知（git 66524db）<br>**補足**: 『denyRead / denyWrite 双方に idempotent union-merge（denyWrite は ~/.claude/settings.json）』。出典 org_setup_prune.py:127-129 USER_COMMON_SANDBOX_DENYWRITE_CANDIDATES<br>**検証**: 反証で維持: org_setup_prune.py:23-28 の docstring が denyRead と denyWrite(~/.claude/settings.json) の両方を書く。:546-551 process_user_common_sandbox が denywrite_candidates を受ける。:127-129 にその定数がある。git 2751645 (2026-05-11 #433)。:196『denyRead に対してのみ』は誤り | 6 |
| .claude/skills/org-setup/SKILL.md:198-205 | Step 6 実行コマンド | keep | --user-common-sandbox / --dry-run 現存（org_setup_prune.py:1110, :38-39 docstring）。permissions.md:105-116 の同一コマンド列はこちらを正本として delete | 8 |
| .claude/skills/org-setup/SKILL.md:206-206 | Step 6 対象ディレクトリ列挙 | outdated | 対象に `~/.config/gh` を含めているが、org_setup_prune.py:79-86 USER_COMMON_SANDBOX_DENYREAD_CANDIDATES に無く、:105-107 USER_COMMON_SANDBOX_DENYREAD_REMOVE（retire=自動除去）側。git 7798142 (2026-05-12, #439) で除外<br>**補足**: 対象は ~/.ssh ~/.aws ~/.kube ~/.gnupg ~/.docker ~/.config/aws-vault。~/.config/gh は retire リストで既存 entry を自動除去（permissions.md:66-82 と一致させる）<br>**検証**: 反証で維持: org_setup_prune.py:79-86 CANDIDATES は ~/.ssh ~/.aws ~/.kube ~/.gnupg ~/.docker ~/.config/aws-vault。~/.config/gh は :105-107 DENYREAD_REMOVE(retire)側。git 7798142 2026-05-12 #439。permissions.md:66-82 とも食い違う | 1 |
| .claude/skills/org-setup/SKILL.md:207-209 | Step 6 詳細ポインタ | keep | permissions.md:60 節への 1 行参照 | 3 |
| .claude/skills/org-setup/SKILL.md:210-215 | 注意事項 | keep | .gitignore:51,55,56,60 で local 設定 ignore 済み。:215 の正典指定先 tools/test_org_setup_prune.py 現存 | 6 |

### .claude/skills/org-setup/references/permissions.md（496 行: keep 456 / move 14 / delete 25 / outdated 1 / transport 0）

| 行範囲 | 節 | 分類 | 根拠 | 行数 |
|---|---|---|---|---|
| .claude/skills/org-setup/references/permissions.md:1-11 | Source of truth 注記 | keep | check_role_configs.py:284-318 check_docs が permissions.md の role block を schema に対して検証、tests/test_check_role_configs.py:1226『real schema + real permissions.md must pass』。org_setup_prune.py:1-8 も本ファイルを template 源とする | 11 |
| .claude/skills/org-setup/references/permissions.md:12-49 | ユーザー共通 JSON | keep | schema roles.user_common.required_allow と 22 entry 完全一致（python で突合）。docs_section『ユーザー共通』で check_docs が抽出 | 38 |
| .claude/skills/org-setup/references/permissions.md:50-57 | Bash permission 方針（renga 運用コマンド限定） | keep | renga-layouts/ops.toml 現存。renga 下限 2.0.0 は org-start/SKILL.md:53・org-delegate/references/pane-layout.md:30 と同表記。旧 glob 撤去の経緯 1 文は move 候補だが短いので keep | 8 |
| .claude/skills/org-setup/references/permissions.md:58-59 | renga-peers MCP ツール 15 種の注意 | keep | 上の allow 列挙は 15 件で一致。ただし本セッションの renga-peers MCP 指示は spawn_codex_pane を含む 16 ツールを列挙（観察。repo 内出典なし）→ judgments へ | 2 |
| .claude/skills/org-setup/references/permissions.md:60-61 | user-common-sandbox 節見出し | keep | SKILL.md:208 から参照される節 | 2 |
| .claude/skills/org-setup/references/permissions.md:62-63 | main pull 後の 1 回必須（警告） | keep（当初 delete） | SKILL.md:194 に同じ『main pull 後の 1 回必須 + python tools/org_setup_prune.py --user-common-sandbox』警告がある（両方 grep -n '1 回必須' でヒット）。手順の実行場所は SKILL Step 6<br>**補足**: 正本: .claude/skills/org-setup/SKILL.md:194<br>**検証**: 反証で delete→keep: 同じ強さではない。permissions.md:62 は denyRead と denyWrite の両方に触れる。正本とされた SKILL.md:194 は denyRead だけを書き、本棚卸しで outdated と判定済み(上記 192)。不正確な側を正本にして正確な側を消すことになる。SKILL 側を直すまでは keep(正本は permissions.md:62 にする) | 2 |
| .claude/skills/org-setup/references/permissions.md:64-65 | --user-common-sandbox の概要 | keep | org_setup_prune.py:18-31 docstring と一致（denyRead+denyWrite を単一フラグで処理）。Issue 経緯の括弧書きは move 可 | 2 |
| .claude/skills/org-setup/references/permissions.md:66-87 | denyRead 候補・フィルタ・retire リスト | keep | 候補 6 件は org_setup_prune.py:79-86 と一致、retire ~/.config/gh は :105-107、filter_existing_user_dirs :353。gh 除外の理由文（:82 前半）は move 可 | 22 |
| .claude/skills/org-setup/references/permissions.md:88-94 | denyWrite 候補と存在チェック無し | keep | org_setup_prune.py:127-129 USER_COMMON_SANDBOX_DENYWRITE_CANDIDATES=(~/.claude/settings.json,)、存在チェック無しは :113-118 コメントと一致 | 7 |
| .claude/skills/org-setup/references/permissions.md:95-96 | symlink skip 非適用の理由・directory denyWrite 見送り | move | Issue #433 スコープ外の設計判断の説明。同旨が org_setup_prune.py:120-124 コメントにある。手順には不要<br>**検証**: 反証で維持: directory denyWrite を見送った経緯(Issue #433)は org_setup_prune.py:122-126 のコメントと同じ趣旨。ただし :95 冒頭の『symlink-escape skip を適用しない』はフィルタ規則の仕様。移すなら 1 行の規則として :92 の一覧に残すこと | 2 |
| .claude/skills/org-setup/references/permissions.md:97-104 | 動作（idempotent / malformed abort） | keep | _merge_sandbox_deny（org_setup_prune.py:416）、shape 検査 :456-480 の ValueError、result['sandbox'] :507 | 8 |
| .claude/skills/org-setup/references/permissions.md:105-117 | 使用方法（コマンド例） | delete | SKILL.md:198-204 と同一コマンド（--dry-run / 実行）。no-op 行のみ追加情報だが SKILL:202『上書き候補がない場合は no-op』で既出<br>**補足**: 正本: .claude/skills/org-setup/SKILL.md:198-204<br>**検証**: 反証で維持: SKILL.md:198-204 と同じコマンド(--user-common-sandbox --dry-run / 実行)。no-op は SKILL:202 に書かれている。tests/test_check_role_configs.py は ```json ブロックだけを抽出し(check_role_configs.py:135)、bash ブロックは検査しないので、消してもテストは壊れない | 13 |
| .claude/skills/org-setup/references/permissions.md:118-121 | 前提 1 / 前提 2（Layer 2/3 merge セマンティクスの設計理由） | move | Issue #429 Task A / #433 の調査結論と設計理由の説明。手順・制約を追加しない（実装は org_setup_prune.py で完結）<br>**検証**: 反証で維持: Layer2/3 merge セマンティクスの調査結論と設計理由(#429 Task A / #433)。:118 が含意する『permissions.deny に Read(~/.aws/*) を書くな』という注意は移し先でも残すこと。実装は org_setup_prune.py で完結している | 4 |
| .claude/skills/org-setup/references/permissions.md:122-123 | 実装・テスト所在 | keep | 関数 merge_user_common_sandbox_denyread :511 / _denywrite :530 / filter_existing_user_dirs :353 / process_user_common_sandbox :546 / _merge_sandbox_deny :416、テストクラス tools/test_org_setup_prune.py:562,717,804 全て現存 | 2 |
| .claude/skills/org-setup/references/permissions.md:124-129 | 窓口節見出し・narrow 方針 | keep | schema global.forbidden_allow_exact に Bash(git *)/Bash(gh:*) 等があり check_role_configs が強制 | 6 |
| .claude/skills/org-setup/references/permissions.md:130-197 | 窓口 JSON（permissions allow/deny） | keep | check_docs が docs_section『窓口』で抽出し schema roles.secretary と照合、tests/test_transport_allowlist_consume.py:65 が allow を取り出して比較。prune の生成源（org_setup_prune.py:1004） | 68 |
| .claude/skills/org-setup/references/permissions.md:198-218 | 窓口 JSON（hooks / sandbox.network） | keep | .hooks/block-workers-delete.sh 現存、check_role_configs の hook path 検査（:570-610）対象。allowedDomains は ff6d785 | 21 |
| .claude/skills/org-setup/references/permissions.md:219-220 | sandbox.network.allowedDomains 注記 | keep | SKILL.md:66 が理由をこの注記へ委譲。check_role_configs.py:255-281 が検査 | 2 |
| .claude/skills/org-setup/references/permissions.md:221-222 | {claude_org_path} 置換の注意（窓口） | keep | hook の絶対パス必須理由。check_role_configs が root-anchored path 等値で検査（:570-582 docstring） | 2 |
| .claude/skills/org-setup/references/permissions.md:223-224 | mcp__renga-peers__* 重複の理由 | move | user_common と重複列挙する理由の説明のみ。手順・制約を加えない（重複自体は schema roles.secretary.required_allow が要求）<br>**検証**: 反証で維持: 重複列挙の理由の説明。列挙する義務自体は schema roles.secretary.required_allow が mcp__renga-peers__set_summary/list_peers/send_message/… を要求して機械的に強制している(python で確認) | 2 |
| .claude/skills/org-setup/references/permissions.md:225-226 | permissions.deny（ワーカー設定 Edit 禁止） | keep | JSON :192-196 の deny の説明。schema roles.secretary.required_deny と一致（check_docs 検証） | 2 |
| .claude/skills/org-setup/references/permissions.md:227-228 | Write(...) を併記しない理由と禁止 | keep | 『足し戻さないこと』は現行制約。runtime 0.1.42 同期（suisya-systems/claude-org-runtime#178）の経緯部分は move 可。Claude Code の警告文言が根拠 | 2 |
| .claude/skills/org-setup/references/permissions.md:229-230 | Edit deny の限界と Phase 3 課題 | move | 防御範囲の限界と将来課題（Phase 3）の説明。手順なし<br>**検証**: 反証で維持: Edit deny の防御範囲の限界と Phase 3 の将来課題の説明。新しい手順・制約は加えていない(制約は :225 と JSON にある) | 2 |
| .claude/skills/org-setup/references/permissions.md:231-232 | renga bootstrap 重複の理由 | move | 重複の理由説明のみ（:223 と同型）<br>**検証**: 反証で維持: renga bootstrap を重複列挙する理由だけを書いた 1 文で、:223 と同じ型。手順はない | 2 |
| .claude/skills/org-setup/references/permissions.md:233-236 | 並び順規約・hooks 説明 | keep | .hooks/block-workers-delete.sh:96-103 に renga 除外ロジック現存 | 4 |
| .claude/skills/org-setup/references/permissions.md:237-245 | 書いてはいけないもの | keep | schema global.forbidden_allow_exact（Bash(git *) 等 8 件）・forbidden_allow_regex ^mcp__claude-peers__ が CI で強制。一発コマンド・user path は closed_world で unknown allow | 9 |
| .claude/skills/org-setup/references/permissions.md:246-255 | 剪定は --prune で自動化済み / user 拡張の保護 | delete | SKILL.md:103-123（--prune コマンド）と :125-149（override）の重複。:254 自身が『詳細は SKILL.md Step 5 参照』と書く<br>**補足**: 正本: .claude/skills/org-setup/SKILL.md:103-149<br>**検証**: 反証で維持: SKILL.md:111-119 の --prune コマンドと :125-148 の override 説明の重複。:254 自身が『詳細は SKILL.md Step 5 参照』と書く。消しても check_role_configs の json 抽出(:135)には影響しない | 10 |
| .claude/skills/org-setup/references/permissions.md:256-261 | ディスパッチャー節・bypassPermissions 重要注記 | keep | schema roles.dispatcher.description『Runs in bypassPermissions mode』、org-start/SKILL.md:217 permission_mode="bypassPermissions" | 6 |
| .claude/skills/org-setup/references/permissions.md:262-300 | ディスパッチャー JSON（allow / Edit\|Write / Cron / Bash hooks） | keep | block-dispatcher-out-of-scope.sh / check-loop-directive.sh / block-git-push.sh / block-dangerous-git.sh / block-workers-delete.sh 全て .hooks/ に現存。check-loop-directive.sh:21 が本節を参照 | 39 |
| .claude/skills/org-setup/references/permissions.md:301-332 | ディスパッチャー JSON（no-verify / close_pane / env / sandbox） | keep | block-no-verify.sh・block-relative-close-pane.sh 現存（git 5585840 2026-09-21 #1020） | 32 |
| .claude/skills/org-setup/references/permissions.md:333-334 | {claude_org_path} 置換の注意（ディスパッチャー） | delete | :221 と同文（『{claude_org_path} は settings.local.json 生成時に解決済みの絶対パスに置換…クォートされている』）の再掲<br>**補足**: 正本: .claude/skills/org-setup/references/permissions.md:221（ファイル冒頭近くへ 1 回だけ）<br>**検証**: 反証で維持: :221 の前半と同じ文。:221 の方が相対パスで無効化する理由まで含み、強い。:492 にも同型の文がある({worker_dir} を追加)。冒頭の 1 か所へ集約するのが妥当 | 2 |
| .claude/skills/org-setup/references/permissions.md:335-336 | ディスパッチャー sandbox.network の理由 | keep（当初 move） | 『将来 sandbox を有効にしても止まらないよう先に配る』という設計理由。配布物は JSON 内で完結、check_role_configs が検査<br>**検証**: 反証で move→keep: :335 の前半には allowedDomains の用途と『窓口と同じく enabled は足さない』という指示(制約)が混ざっている。純粋な設計理由は後半の『将来 sandbox を有効にしても…先に配る』だけ。丸ごと move すると指示が消えるので keep(後半だけを move) | 2 |
| .claude/skills/org-setup/references/permissions.md:337-345 | hooks の役割分担 | keep | 7 hook 全て .hooks/ に現存。stash deny は git f27f533 (2026-10-09) と同期。:341 は Issue #470 等の理由が長く一部 move 可 | 9 |
| .claude/skills/org-setup/references/permissions.md:346-373 | キュレーター節と JSON | keep | schema roles.curator（docs_section キュレーター）と check_docs 照合、block-relative-close-pane.sh 現存 | 28 |
| .claude/skills/org-setup/references/permissions.md:374-379 | ワーカー節見出し・生成元注記 | keep | worker_roles に default / claude-org-self-edit / doc-audit の 3 role（schema 突合）。gen_delegate_payload.py:1991-2058 が claude-org-runtime settings generate を呼ぶ | 6 |
| .claude/skills/org-setup/references/permissions.md:380-435 | ワーカー JSON（permissions） | keep | check_docs が schema roles.worker（監査下限）と照合し CI pass。ただし実生成元 worker_roles.default とは不一致（deny に git -C * 系・remote・reflog が無く、allow に docker build 系が無い）→ judgments | 56 |
| .claude/skills/org-setup/references/permissions.md:436-489 | ワーカー JSON（hooks / env） | keep | check-worker-boundary.sh・block-org-structure.sh 等 .hooks/ に現存、roles.worker.required_hooks と一致 | 54 |
| .claude/skills/org-setup/references/permissions.md:490-491 | .env テンプレート除外の順序 | keep | schema worker_roles.default.permissions.deny で Read(.env)/Read(.env.*) 直後に ! 4 行（突合済み）。runtime 0.1.43+ は機能導入下限で現行 pin >=0.1.49 と矛盾しない。git c775107 2026-09-30 | 2 |
| .claude/skills/org-setup/references/permissions.md:492-493 | {claude_org_path} / {worker_dir} 置換注意 | keep | {worker_dir} はここだけの情報。{claude_org_path} 部分は :221 と重複 | 2 |
| .claude/skills/org-setup/references/permissions.md:494-495 | deny と hooks の役割分担（多層防御の説明） | move | 多層防御の設計理由。手順・制約は JSON とディスパッチャー節 :260 で既出<br>**検証**: 反証で維持: 多層防御の設計説明。bypass ロールでは hook だけが障壁になるという制約は :260 に既にある。『hook を副次防御として維持』という趣旨は移し先でも残すこと | 2 |
| .claude/skills/org-setup/references/permissions.md:496-496 | Phase 2 worker git guardrails | outdated | 『fetch/remote/reflog の deny family』を上の JSON の反映と書くが、JSON（:384-433）は git fetch:* を allow し remote/reflog/-C 系 deny を含まない。また worker-git-guardrails-design.md は docs/design/ に無く docs/contracts/ に在る（find で確認）<br>**補足**: 実 deny は schema worker_roles.default.permissions.deny（`git -C * fetch`・`git remote add/set-url/remove/rm`・`git reflog expire/delete` 等。素の `git fetch:*` は allow）。設計書は docs/contracts/worker-git-guardrails-design.md。経緯部は move<br>**検証**: 反証で維持: JSON :392 が Bash(git fetch:*) を allow し、:396-433 の deny には remote/reflog が無い(grep で確認)。schema worker_roles.default の deny は git -C * fetch / remote add\|set-url\|remove\|rm / reflog expire\|delete 等で、allow に git fetch:* がある。設計書の所在は docs/contracts/worker-git-guardrails-design.md(find で確認) | 1 |

### .claude/skills/org-conveyor/SKILL.md（253 行: keep 161 / move 13 / delete 40 / outdated 8 / transport 31）

| 行範囲 | 節 | 分類 | 根拠 | 行数 |
|---|---|---|---|---|
| .claude/skills/org-conveyor/SKILL.md:1-26 | frontmatter（description / allowed-tools 前半） | keep | tools/work_discovery_scan.py・journal_append.sh/.py 現存。編集は SKILL.md 直接（.in 無し: ls で確認） | 26 |
| .claude/skills/org-conveyor/SKILL.md:27-36 | allowed-tools の transport コメントと mcp__org-broker__* | transport | broker 面のみ列挙（per-transport 認可）。実運用 renga では list_panes/check_messages が未許可になる。knowledge/curated/delegation.md:155 は conveyor 長走（2026-09-08）が renga transport だったと記録 | 10 |
| .claude/skills/org-conveyor/SKILL.md:37-55 | 冒頭（目的・委譲先スキル一覧） | keep | 委譲先 work-discovery / org-delegate / org-pull-request / org-escalation 全て .claude/skills/ に現存。/verify の実在は repo 内で確認不能 → judgments | 19 |
| .claude/skills/org-conveyor/SKILL.md:56-68 | 位置づけ（他ループ系との差分） | move | /loop・/work-discovery・/org-delegate との比較表と設計説明。手順・制約を含まない<br>**検証**: 反証で維持: /loop・/work-discovery・/org-delegate との比較表と設計説明。:66-67 の『スコープ外は投入せず halt』は INV-2 (:74-75) が手順として持っているので、移しても規則は失われない | 13 |
| .claude/skills/org-conveyor/SKILL.md:69-82 | 不変条件 INV-1〜6 | keep | Non-goals・exit-conditions・scope-contract の参照元。ただし引用 memory slug（feedback-merge-approval 等）は secretary memory に無く、memory MEMORY.md:25『CI green = auto-merge（2026-06-13 恒久承認）』と INV-1 が食い違う → judgments | 14 |
| .claude/skills/org-conveyor/SKILL.md:83-96 | 輸送層 両系（per-transport 認可） | transport | 『本リポジトリの active transport は broker（コード既定）』と renga 再生成の注記。コード既定は tools/transport.py:19-20 で broker、運用は renga（knowledge/curated/delegation.md:155） | 14 |
| .claude/skills/org-conveyor/SKILL.md:97-107 | cross-tab の free-pane 会計 | delete | 本文自身が『同じ会計モデルの記述は work-discovery/SKILL.md の --free-panes 節にあり、本注記はそれに合わせてある』と明記。work-discovery/SKILL.md:120-135 に count_worker_population / free_worker_slots / 未 bind 予約の同記述（grep で確認）<br>**補足**: 正本: .claude/skills/work-discovery/SKILL.md:120-135。conveyor 側は『free-pane 数は work-discovery --free-panes 節の会計に従う』1 行参照へ<br>**検証**: 反証で維持: work-discovery/SKILL.md:119-136 に、caller タブへのスコープ・count_worker_population の name union・count_unbound_reservations・free_worker_slots を --free-panes に渡す、が同じ強さで書かれている。本文 :105-106 自身が合わせてあると明記。tests/tools に参照は無い。renga 限定の条件付き注記なので transport 集約の候補でもある | 11 |
| .claude/skills/org-conveyor/SKILL.md:108-114 | 実害の限定 / 本リポジトリでは非該当 | transport | 『active transport は broker でタブ概念を持たないため非該当』という transport フレーム依存の但し書き。運用 renga なら該当しうる | 7 |
| .claude/skills/org-conveyor/SKILL.md:115-122 | 受信は transport ツールに依存しない | outdated | 『pr-watch の tools/peer_notify.py: notify_peer は raw env 判定で active transport を選ぶ』と書くが、peer_notify.py:7-8,90-136 は tools.transport.resolve()（explicit > ORG_TRANSPORT > DEFAULT_TRANSPORT）経由。raw env は resolve 不能時のフォールバックのみ（CLAUDE.md 輸送層節 Refs #941 も同旨）<br>**補足**: 『notify_peer は transport.resolve() でコード既定フレームに解決する（raw env は runtime 未導入時の never-raise フォールバック）』。出典 tools/peer_notify.py:7-8,132-136<br>**検証**: 反証で維持: tools/peer_notify.py:7-9 と :90-138 は tools.transport.resolve()(explicit > ORG_TRANSPORT > DEFAULT_TRANSPORT)で経路を選ぶ。raw env は import 失敗か不明値のときの never-raise フォールバック(:131-138)だけ。Issue #941 で raw env 判定は廃止 | 8 |
| .claude/skills/org-conveyor/SKILL.md:123-143 | Step 1 承認スコープ契約 | keep | 書出し先 .state/conveyor/scope-contract.md と mkdir は allowed-tools（:26）と対。org-pull-request/SKILL.md:49 に 2b-i 節現存。事前承認範囲の列挙（:134-138）は scope-contract.md:19-29 表と一部重複 | 21 |
| .claude/skills/org-conveyor/SKILL.md:144-156 | Step 2-1〜2 triage（ゴール台帳・exit 2） | keep | work_discovery_scan.py に --trigger/--free-panes/--rank-mode legacy（:74-86,123）、goal_unset_repos/goal_errors（work_discovery_goals.py:830,857）、exit 2 fail-closed（scan.py:33,85）。git e40b40c 2026-09-30 で追随 | 13 |
| .claude/skills/org-conveyor/SKILL.md:157-173 | Step 2-3〜6 scope gate / 派遣 / iteration / verify | keep | org-delegate Step 0・Step 5、org-escalation 現存。verify は references/verify-evidence.md・dynamic-ports.md へ委譲（正本はこの Step 2-6） | 17 |
| .claude/skills/org-conveyor/SKILL.md:174-186 | Step 2-7〜11 PR / merge gate / バックプレッシャー / 退出 | keep | gate=ci_green_merge_gate の emit は org-pull-request/SKILL.md:73 に現存。2b-ii は同 :267。merge gate 停止は INV-1 依存 → judgments | 13 |
| .claude/skills/org-conveyor/SKILL.md:187-190 | 再入と引き継ぎ | keep | secretary-handover / secretary-resume スキル現存 | 4 |
| .claude/skills/org-conveyor/SKILL.md:191-217 | 観測可能性（TaskCreate/TaskUpdate/TaskList） | keep | allowed-tools :18-20 と対。skill 契約として出力書式とタイミングを規定。knowledge/curated/delegation.md:142-156 に実走記録（2026-09-08） | 27 |
| .claude/skills/org-conveyor/SKILL.md:218-226 | verify 統合（要約） | delete | Step 2-6（:169-173）と references/verify-evidence.md:13-40 / dynamic-ports.md の要約再掲。本文 :225 自身が詳細は references を一次参照と書く<br>**補足**: 正本: .claude/skills/org-conveyor/SKILL.md:169-173（手順）と references/verify-evidence.md<br>**検証**: 反証で維持: Step 2-6 (:169-173) に classifier・app code のときだけ /verify 必須・判定不能なら halt・Test plan への転記・動的ポートが揃っている。詳細は verify-evidence.md:13-40 と dynamic-ports.md。:225 自身が references を一次参照としている。tests/tools/.hooks からの参照は無い | 9 |
| .claude/skills/org-conveyor/SKILL.md:227-237 | 退出条件（機械的・要約） | delete | references/exit-conditions.md:10-16 の表と同じ 5 条件の再掲。Step 2-11（:185）が既に exit-conditions.md を参照<br>**補足**: 正本: .claude/skills/org-conveyor/references/exit-conditions.md:8-16<br>**検証**: 反証で維持: exit-conditions.md:10-16 の表に同じ 5 条件があり、閾値付きで詳しい。halt 時にサマリと理由を提示する手順は exit-conditions.md:62。Step 2-11 (:185) が参照している。テストからの参照は無い | 11 |
| .claude/skills/org-conveyor/SKILL.md:238-246 | やらないこと（Non-goals） | delete | 6 項目とも INV-1/2/3/4/6（:71-81）、Step 2-9『PR キュー上限なし』（:181-183）、CLAUDE.md『窓口は worker 作業を代行しない』の再掲<br>**補足**: 正本: .claude/skills/org-conveyor/SKILL.md:69-81（INV-6 に『/loop 置換でない・PR キュー上限なし』を吸収）<br>**検証**: 反証で維持: 6 項目は INV-1/2/3/4 (:71-79)、:181-183『PR キュー上限なし』、CLAUDE.md:96『窓口は worker 作業を代行しない』に既にある。注意: INV-6 (:81) が『（Non-goals 節）』を参照しているので、消すなら INV-6 に /loop 置換でないことと PR キュー上限なしを吸収し、参照も直すこと | 9 |
| .claude/skills/org-conveyor/SKILL.md:247-253 | 起動主体とパス解決 | keep | py -3 読み替えは allowed-tools :21-24 両形と対。:249 は INV-5 の再掲（1 行）。markdown-conventions.md 現存 | 7 |

### .claude/skills/org-conveyor/references/dynamic-ports.md（47 行: keep 32 / move 15 / delete 0 / outdated 0 / transport 0）

| 行範囲 | 節 | 分類 | 根拠 | 行数 |
|---|---|---|---|---|
| .claude/skills/org-conveyor/references/dynamic-ports.md:1-2 | 見出し | keep | SKILL.md:173,225 から参照 | 2 |
| .claude/skills/org-conveyor/references/dynamic-ports.md:3-8 | 問題説明と Issue #637 方針の経緯 | move | 衝突が起きる理由と Issue #637 で serial を採らなかった経緯。手順は :9 以降<br>**検証**: 反証で維持: 衝突が起きる理由と、Issue #637 で serial を採らなかった経緯の説明。規律『env で受け取る』は :9 と :11-19 に手順として残る | 6 |
| .claude/skills/org-conveyor/references/dynamic-ports.md:9-20 | 規律（env でポートを受け取る） | keep | verify-evidence.md:50,63 が本規律を参照。consumer は conveyor 経由の worker brief のみ（git grep dynamic-ports → conveyor 内 3 ファイルのみ） | 12 |
| .claude/skills/org-conveyor/references/dynamic-ports.md:21-32 | 空きポートの確保（portable） | keep | stdlib socket の手順。tools/run.sh は実在しない（ls で No such file）が汎用例の placeholder | 12 |
| .claude/skills/org-conveyor/references/dynamic-ports.md:33-36 | bind→close 競合の注記 | move | 理論上の競合と実害が出にくい理由の説明<br>**検証**: 反証で維持: 理論上の競合と、実害が出にくい理由の説明。:34-35『stdout 方式に切り替えてよい』は任意の選択肢で、移し先でも残すこと | 4 |
| .claude/skills/org-conveyor/references/dynamic-ports.md:37-42 | worker brief への載せ方 | keep | gen_delegate_payload.py:2274 --impl-guidance 現存 | 6 |
| .claude/skills/org-conveyor/references/dynamic-ports.md:43-47 | 不採用案（serial verify レーン化） | move | 却下した代替案の説明。末尾の『直列必須なら scope 縁』だけは手順なので移動先で残すか :9-20 へ 1 行吸収<br>**検証**: 反証で維持: 却下した代替案の説明。:46-47『直列必須なら scope 縁として契約に明記し人間判断』は手順なので、:11-19 の規律へ 1 行で吸収すること(分類者の補足どおり) | 5 |

### .claude/skills/org-conveyor/references/exit-conditions.md（66 行: keep 61 / move 0 / delete 5 / outdated 0 / transport 0）

| 行範囲 | 節 | 分類 | 根拠 | 行数 |
|---|---|---|---|---|
| .claude/skills/org-conveyor/references/exit-conditions.md:1-7 | 導入 | keep | SKILL.md:142,185,230 から参照される一次参照 | 7 |
| .claude/skills/org-conveyor/references/exit-conditions.md:8-17 | 退出条件一覧（表） | keep | 5 条件の正本。scope-contract.md:61-64 のキー名（codex_round_max 等）と対 | 10 |
| .claude/skills/org-conveyor/references/exit-conditions.md:18-26 | 1. Codex round 最大数到達 | keep | org-delegate/SKILL.md:375-381『Codex 4 ラウンド目以降は介入』と整合。knowledge/curated/delegation.md:147 に『上限到達時に差分限定 +1 round を毎回許可した』運用記録 → judgments | 9 |
| .claude/skills/org-conveyor/references/exit-conditions.md:27-34 | 2. 連続 false-positive | keep | conveyor 固有の退出条件。引用 memory slug feedback-no-stopgap は secretary memory 110 件に無し（ls \| grep stopgap 0 件） | 8 |
| .claude/skills/org-conveyor/references/exit-conditions.md:35-39 | 3. 時間予算 / 最大反復数 | keep | scope-contract.md:43,64 の approved_at / time_budget / max_iterations と対 | 5 |
| .claude/skills/org-conveyor/references/exit-conditions.md:40-45 | 4. worker escalation | keep | org-escalation スキル現存（INV-3） | 6 |
| .claude/skills/org-conveyor/references/exit-conditions.md:46-52 | 5. scope 縁（述語・委譲前チェック・verify 判定不能） | keep | SKILL.md INV-2、verify-evidence.md:33-34 と対 | 7 |
| .claude/skills/org-conveyor/references/exit-conditions.md:53-57 | 5. scope 縁（ゴール未設定・台帳エラー・exit 2） | delete | SKILL.md:155-156 に同条件（goal_unset_repos/goal_errors で halt・--rank-mode legacy を仰ぐ・exit 2 で halt）が手順として既出。表 :16 にも列挙<br>**補足**: 正本: .claude/skills/org-conveyor/SKILL.md:155-156<br>**検証**: 反証で維持: SKILL.md:153-156 に、プールがゴール条項に限られること・goal_unset_repos/goal_errors なら halt・legacy は人間の明示時だけ・exit 2 なら状態と理由を伝えて halt、が同じ強さである。表 :16 にも列挙。scope-contract.md:53 が参照するのは『exit-conditions.md 5』の項目番号で、表の行が残るので切れない | 5 |
| .claude/skills/org-conveyor/references/exit-conditions.md:58-59 | スコープ使い切りの正常退出 | keep | ここにしか無い規定 | 2 |
| .claude/skills/org-conveyor/references/exit-conditions.md:60-66 | halt 時の共通挙動 | keep | SKILL.md:216 と整合する halt 手順の正本 | 7 |

### .claude/skills/org-conveyor/references/verify-evidence.md（80 行: keep 74 / move 0 / delete 6 / outdated 0 / transport 0）

| 行範囲 | 節 | 分類 | 根拠 | 行数 |
|---|---|---|---|---|
| .claude/skills/org-conveyor/references/verify-evidence.md:1-6 | 導入 | keep | SKILL.md:169 から参照 | 6 |
| .claude/skills/org-conveyor/references/verify-evidence.md:7-12 | 実行主体（重要） | delete | SKILL.md:52（/verify は組込み・worker が worktree 内で実行）と :170-172（conveyor は app を起動しない・同等コマンド可・転記）で同内容が既出<br>**補足**: 正本: .claude/skills/org-conveyor/SKILL.md:170-172<br>**検証**: 反証で維持: SKILL.md:52 に組込みスキル・worker が worktree 内で実行、:170-172 に conveyor は app を起動しない・同等コマンド可・Test plan へ転記がある。責務 2 点も Step 2-6 にある。tests/tools からの参照は無い | 6 |
| .claude/skills/org-conveyor/references/verify-evidence.md:13-41 | applicability classifier | keep | touch domain 表と判定規律の正本。git diff:* が allowed-tools（SKILL.md:25）と対。gh pr view --json files は org-pull-request 経由 | 29 |
| .claude/skills/org-conveyor/references/verify-evidence.md:42-80 | エビデンス転記（Test plan テンプレート） | keep | org-pull-request/SKILL.md 2b-i（:49）/ 2c（:208）現存。Test plan 書式の正本 | 39 |

### .claude/skills/org-conveyor/references/scope-contract.md（86 行: keep 69 / move 17 / delete 0 / outdated 0 / transport 0）

| 行範囲 | 節 | 分類 | 根拠 | 行数 |
|---|---|---|---|---|
| .claude/skills/org-conveyor/references/scope-contract.md:1-6 | 導入 | keep | SKILL.md:129-130 が一次参照として指す | 6 |
| .claude/skills/org-conveyor/references/scope-contract.md:7-18 | なぜ機械契約として articulate するのか | move | 設計理由の説明（箇条の利点列挙）。手順なし<br>**検証**: 反証で維持: 機械契約にする利点を並べた設計理由。:12『人間に確認を取ってから回す』は SKILL.md Step 1 (:123-125) が手順として持っている | 12 |
| .claude/skills/org-conveyor/references/scope-contract.md:19-30 | 事前承認する / しない範囲（表） | keep | 境界の正本（SKILL.md:134-138 が要約）。merge 行は INV-1 と同じく memory『CI green = auto-merge』と食い違う → judgments | 12 |
| .claude/skills/org-conveyor/references/scope-contract.md:31-35 | push/PR を事前承認できる理由 | move | merge と区別する理由の説明。引用 memory slug feedback-no-overgate-after-decision は secretary memory に無し<br>**検証**: 反証で維持: push/PR と merge を区別する理由の説明。:34『bare な OK を merge 承認に流用しない』は SKILL.md:73 (INV-1) と表 :27 が持っているので、移しても規則は失われない | 5 |
| .claude/skills/org-conveyor/references/scope-contract.md:36-73 | テンプレート | keep | SKILL.md Step 1 が .state/conveyor/scope-contract.md へ書き出す雛形。キー名は exit-conditions.md の閾値名と対 | 38 |
| .claude/skills/org-conveyor/references/scope-contract.md:74-80 | 人間確認の手順 | keep | 唯一の事前人間ゲートの手順。SKILL.md:126-127 と整合 | 7 |
| .claude/skills/org-conveyor/references/scope-contract.md:81-86 | 契約の拡大・変更 | keep | 自己拡張禁止の規定。CLAUDE.md『worker への追加依頼の境界』節現存 | 6 |

### .claude/skills/org-curate/SKILL.md（301 行: keep 267 / move 17 / delete 7 / outdated 0 / transport 10）

| 行範囲 | 節 | 分類 | 根拠 | 行数 |
|---|---|---|---|---|
| .claude/skills/org-curate/SKILL.md:1-24 | frontmatter（name/description/allowed-tools、送信系以外） | keep | tools/check_curate_threshold.py 現存（ls）。consumer: .dispatcher/references/pane-close.md:244-245 が実行、docs/contracts/role-contract.md:158 が on-demand 1 回起動を規定。編集は SKILL.md.in 側（tools/gen_skill_prose.py 生成） | 24 |
| .claude/skills/org-curate/SKILL.md:25-25 | allowed-tools: mcp__org-broker__send_message | transport | 生成面で broker 名に render（.in:25 は mcp__renga-peers__send_message、diff で確認）。コード既定 broker / 運用 renga の二重記述の一部 | 1 |
| .claude/skills/org-curate/SKILL.md:26-31 | タイトル・概要 | keep | knowledge/raw → knowledge/curated の役割は docs/contracts/knowledge-curation-contract.md:36-58 と一致 | 6 |
| .claude/skills/org-curate/SKILL.md:32-38 | 輸送層両系ヘッダ（既定 broker / opt-in renga・二フレーム注記・Epic #6 Issue G） | transport | .in:32 の {{> dual-system-header-long }}（tools/skill_src/fragments/dual-system-header-long.broker.md 7 行）。同一注記は生成物 17 ファイルに展開（grep -rl '既定の二フレーム注記（Refs #604）' 非 .in = 17） | 7 |
| .claude/skills/org-curate/SKILL.md:39-43 | 起動モデル（オンデマンド化・/loop 禁止） | keep | role-contract.md:158（resident /loop 廃止 2026-06-07）、pane-close.md:244 が閾値スクリプトを実行 | 5 |
| .claude/skills/org-curate/SKILL.md:44-49 | パス解決（CWD=.curator/） | keep | .curator/CLAUDE.md:15「パス（重要）」節が同規約。allowed-tools 14-24 に両形あり | 6 |
| .claude/skills/org-curate/SKILL.md:50-60 | Step 0: reasons 4 種の表 | keep | tools/check_curate_threshold.py:116-119（閾値 5/5/20/1）、288-295（4 reason を append）と完全一致 | 11 |
| .claude/skills/org-curate/SKILL.md:61-99 | 0-A: 起動文脈判定（on-demand か手動か・inflight 補助シグナル） | keep | curator 孤立事故の修正 0cc0a48（2026-06-14, #568 CURATE_DONE 必須送信）。CURATE_* の consumer は .dispatcher/tools 等で 17 hit、curate-inflight.json は .dispatcher/CLAUDE.md:198 / worker-monitoring.md.in:1230。89-91 の『Codex Major 対応』は経緯注記 | 39 |
| .claude/skills/org-curate/SKILL.md:100-110 | reasons の出所（受領 or 自走・exit 0/10/2） | keep | check_curate_threshold.py:112-113（EXIT_BELOW_THRESHOLD=0 / EXIT_CURATE_NEEDED=10）、:318 で exit 2=error | 11 |
| .claude/skills/org-curate/SKILL.md:111-115 | 注意: reasons 有無と送信義務は独立 | keep | 0-A:96-97 と内容が一部重なるが『項 2 に落ちても on-demand』は固有。#568（0cc0a48）由来。統合は judgments へ | 5 |
| .claude/skills/org-curate/SKILL.md:116-127 | Step 1: 移行 sweep（常に実行） | keep | legacy_marker_sweep が check_curate_threshold.py:294-295 に現存 | 12 |
| .claude/skills/org-curate/SKILL.md:128-130 | Set A § Role: curator の書き込み権限注記 | move | 権限根拠の説明。正本は knowledge-curation-contract.md:37-38（active raw immutable / move-then-mark）。手順は 123-126 で完結<br>**検証**: 反証で維持: 128-130 は権限の根拠を説明する注記。同じ制約は手順側の 219 行（active な knowledge/raw/ は絶対に書き換えない）と docs/contracts/knowledge-curation-contract.md §1.1 Owner/move-then-mark に既にある。git grep で tests/tools からの参照は無い。編集は SKILL.md.in 側で行う | 3 |
| .claude/skills/org-curate/SKILL.md:131-133 | 以降の分岐 | keep | raw_threshold 有無の分岐。Step 0 表（57行）と整合 | 3 |
| .claude/skills/org-curate/SKILL.md:134-145 | Step 2: 読み込みと分類 | keep | テーマ粒度ガイダンスの所在として knowledge-curation-contract.md:53 が本 Step を参照 | 12 |
| .claude/skills/org-curate/SKILL.md:146-181 | Step 2.5: skill 化候補の抽出 | keep | .claude/skills/skill-eligibility-check/SKILL.md 現存、:129 が org-curate 呼び出し元責務を規定。contract.md:98 が Step 2.5 を trigger として参照。skill-candidates.local.md は Issue #755（101c6f6） | 36 |
| .claude/skills/org-curate/SKILL.md:182-201 | Step 3: 統合と書き出し | keep | knowledge/curated/*.md 7 ファイル現存（ls）。201 行の矛盾処理は knowledge-standards.md:34 と食い違い（judgments） | 20 |
| .claude/skills/org-curate/SKILL.md:202-219 | Step 4: archive への move とマーカー付与（手順） | keep | ceb27cd（2026-06-14, #517 Write ツール経路明文化）。contract.md:38,40 の move-then-mark と一致 | 18 |
| .claude/skills/org-curate/SKILL.md:220-231 | なぜシェルで付与しないか（17 byte 破損・.md.tmp 残骸の経緯） | move | 事故経緯と理由の説明。手順は 216-219 の『Write ツールで read→prepend→write』で完結。由来 ceb27cd #517（2026-06-14）、git log -S 'md.tmp' はこの 2 commit のみで以後の再発 commit なし<br>**検証**: 反証で維持: 大半は事故の経緯（#517, ceb27cd）。git log -S md.tmp のヒットは ceb27cd と d85d4ff の 2 件だけで、knowledge/ に md.tmp の記録は無い。ただし 228-231 の『シェルを使う場合は command mv -f と chr(33)』は条件付きの手順なので、移すときも 1 行は手順側に残すこと | 12 |
| .claude/skills/org-curate/SKILL.md:232-233 | archive 存在 = curated 済みシグナル | keep | check_curate_threshold.py の raw_active が archive/ を除外（:288 RAW_THRESHOLD 判定） | 2 |
| .claude/skills/org-curate/SKILL.md:234-235 | 根拠: Set E §1.1 / Set A | move | 契約根拠の引用のみ。正本 knowledge-curation-contract.md:37-40。128-130 とも重複<br>**検証**: 反証で維持: 235 は契約を引いているだけで、内容は knowledge-curation-contract.md:37-38（Owner / move-then-mark）と 128-130 に重なる。『archive 側ファイルの編集』の許可は 216-219 の手順が暗に前提にしており、手順側の記述は欠けない。tests からの参照も無い | 2 |
| .claude/skills/org-curate/SKILL.md:236-247 | Step 5: 改善提案の検討 | keep | references/knowledge-standards.md:36-46 を参照（現存）。role-contract.md:160 が secretary 宛提案を規定 | 12 |
| .claude/skills/org-curate/SKILL.md:248-248 | org-broker で窓口に提案送信 | transport | .in:242 は {{SERVER}}。生成面で org-broker と render | 1 |
| .claude/skills/org-curate/SKILL.md:249-251 | 提案フォーマット・人間承認まで変更しない | keep | role-contract.md:160 と整合 | 3 |
| .claude/skills/org-curate/SKILL.md:252-259 | Step 6: skill-audit 発火 | keep | skill-audit/SKILL.md:35-75（3 者同期を明記）と check_curate_threshold.py:117-118 の閾値一致 | 8 |
| .claude/skills/org-curate/SKILL.md:260-269 | Step 7: 送信義務 | keep | #568（0cc0a48, 2026-06-14）curator 孤立対策。dispatcher が CURATE_* を待つ: pane-close.md 5-3 / worker-monitoring.md step-5-3（:1233） | 10 |
| .claude/skills/org-curate/SKILL.md:270-276 | dispatcher 宛 direct send | keep | role-contract.md:160『direct send_message(to_id="dispatcher")』 | 7 |
| .claude/skills/org-curate/SKILL.md:277-277 | mcp__org-broker__send_message 例 | transport | .in:271 は {{FQ}}send_message | 1 |
| .claude/skills/org-curate/SKILL.md:278-291 | 順序規定・メッセージ 3 種 | keep | role-contract.md:160『after all secretary-bound improvement proposals are sent』。CURATE_* consumer 17 hit | 14 |
| .claude/skills/org-curate/SKILL.md:292-298 | 明示手動起動のみ省略可（再掲） | delete | 同ファイル 0-A:85-91（inflight 単独では on-demand にならない）と 93-99（手動の定義・省略可）をほぼ逐語で再掲。Read で照合<br>**補足**: 正本は同ファイル 0-A（61-99）。Step 7 側は 263-269 の送信義務と 299-300 の固有文（情報共有として送ってよい / [pane_not_found] 時のみ省略）だけ残す<br>**検証**: 反証で維持: 293-298 を Read で照合した。手動起動の定義は 93-96 と同じで、inflight だけでは送信トリガにしない規則は 87-91 とほぼ同じ文面。片方にしか無い条件は、299 の情報共有として送る件と 300 の [pane_not_found] 時は省略する件だけで、提案ではこの 2 つを残す。tests/tools から参照されていない | 7 |
| .claude/skills/org-curate/SKILL.md:299-301 | 情報共有送信・pane_not_found 時のみ省略 | keep | 0-A に無い固有規定。pane_not_found は backend-interface-contract.md:270 の shared code | 3 |

### .claude/skills/org-curate/references/knowledge-standards.md（59 行: keep 53 / move 6 / delete 0 / outdated 0 / transport 0）

| 行範囲 | 節 | 分類 | 根拠 | 行数 |
|---|---|---|---|---|
| .claude/skills/org-curate/references/knowledge-standards.md:1-4 | タイトル・共通参照の宣言 | keep | consumer: org-curate/SKILL.md:247、org-retro/SKILL.md:65、.dispatcher/references/pane-close.md:103、knowledge-curation-contract.md:13,35 | 4 |
| .claude/skills/org-curate/references/knowledge-standards.md:5-22 | 記録フォーマット（事実/判断/根拠/適用場面） | keep | knowledge-curation-contract.md:35 が本ファイルを raw の正本フォーマットとして参照。pane-close.md:103 も参照 | 18 |
| .claude/skills/org-curate/references/knowledge-standards.md:23-24 | 記録基準 | keep | 同文が org-delegate/references/worker-claude-template.md:353 と tools/templates/worker_brief_normal.md:229 にもあるが、contract.md:86 が本ファイルの『記録基準』を引用しているため正本はここ | 2 |
| .claude/skills/org-curate/references/knowledge-standards.md:25-35 | 統合の判断基準（マージする/しない） | keep | contract.md:58 が merge/conflict 規則の所在として参照。34 行『両方残し矛盾を明記』は org-curate/SKILL.md:201『新しい日付を優先し矛盾を明記』と食い違う（judgments） | 11 |
| .claude/skills/org-curate/references/knowledge-standards.md:36-47 | 改善提案の判断基準 | keep | org-curate/SKILL.md:247 が Step 5 で参照。signals.md:15 も『3 件』基準を本ファイルと同基準と明記 | 12 |
| .claude/skills/org-curate/references/knowledge-standards.md:48-53 | skill 化判定の委譲先 | move | 設計方針の説明（重複定義を置かない理由）。呼び出し手順は org-retro/SKILL.md:69-73 と org-curate/SKILL.md:149-150 に既にあり、skill-eligibility-check/SKILL.md:24 が同じ理由を記述<br>**検証**: 反証で維持: 節の中身はポインタと設計方針だけ。呼び出し手順は org-retro/SKILL.md:69-73 と org-curate/SKILL.md:147-150 にあり、理由は skill-eligibility-check/SKILL.md:22-25 に同じことが書かれている。git grep で tests/tools からの参照は無い。delete にしてもよい | 6 |
| .claude/skills/org-curate/references/knowledge-standards.md:54-59 | curated ファイルの品質基準 | keep | org-curate Step 3 の書き出し基準として参照（SKILL.md:247 経由）。他に同規定なし（git grep '独立して読めること' は本ファイルのみ） | 6 |

### .claude/skills/org-retro/SKILL.md（167 行: keep 156 / move 0 / delete 3 / outdated 0 / transport 8）

| 行範囲 | 節 | 分類 | 根拠 | 行数 |
|---|---|---|---|---|
| .claude/skills/org-retro/SKILL.md:1-12 | frontmatter（送信系以外） | keep | 起動元: CLAUDE.md:56（窓口が完了後の /org-retro）、role-contract.md:31。編集は SKILL.md.in 側 | 12 |
| .claude/skills/org-retro/SKILL.md:13-13 | allowed-tools: mcp__org-broker__send_message | transport | 生成面で broker 名に render（.in 側は transport-neutral） | 1 |
| .claude/skills/org-retro/SKILL.md:14-23 | タイトル・概要・技術知見はワーカーが記録 | keep | ワーカー側の記録指示が tools/templates/worker_brief_normal.md:229 に現存 | 10 |
| .claude/skills/org-retro/SKILL.md:24-30 | 輸送層両系ヘッダ | transport | .in:24 {{> dual-system-header-long }}。org-curate:32-38 と同一の 7 行（生成物 17 ファイルに展開） | 7 |
| .claude/skills/org-retro/SKILL.md:31-39 | Step 1: 委譲プロセスの振り返り観点 | keep | CLAUDE.md:56 が窓口の責務として /org-retro を規定。ただし実運用の振り返りは .dispatcher/references/pane-close.md:13『振り返り（org-retro 相当）』でも実施（実行主体は judgments） | 9 |
| .claude/skills/org-retro/SKILL.md:40-54 | Step 2: 記録すべきかの判断 | keep | pane-close.md:104『記録基準: 同じ種類の委譲で再び遭遇しそうなパターンのみ』が要約版を重複保持（実行主体が決まるまで正本未確定、judgments） | 15 |
| .claude/skills/org-retro/SKILL.md:55-66 | Step 3: 記録（delegation- プレフィックス） | keep | knowledge-curation-contract.md:34,37 が本 Step を delegation-{topic} の正本として参照。pane-close.md:100-103 に同じパス・フォーマット指示の重複 | 12 |
| .claude/skills/org-retro/SKILL.md:67-74 | Step 4: work-skill 化の判定（導入） | keep | skill-eligibility-check/references/signals.md 現存（ls）、skill-eligibility-check/SKILL.md:121 が org-retro を呼び出し元として規定 | 8 |
| .claude/skills/org-retro/SKILL.md:75-102 | Step 4.1: 入力 YAML と 3 値 decision | keep | skill-eligibility-check/SKILL.md 入力契約節・Step 2 と一致。skill-candidates.local.md は .gitignore 済み（Issue #755, 101c6f6） | 28 |
| .claude/skills/org-retro/SKILL.md:103-112 | Step 4.2: skill_recommend は黙ってキュー追記 | keep | knowledge/skill-candidates.md:4（N=5 バッチ問い合わせ）と skill-eligibility-check/SKILL.md:122-124 に同旨。呼び出し元である本スキルを正本とする | 10 |
| .claude/skills/org-retro/SKILL.md:113-148 | バッチ問い合わせ後の承認/却下/統合/保留の処理フロー（温存） | keep | claude-org-self-edit ロールは org-delegate/references/claude-org-self-edit.md:12-24 に現存、deferred は Issue #753（cff3d82）で skill-candidates.md:53 と一致。ただし実行時点は retro ではなくバッチ問い合わせ時（配置は judgments） | 36 |
| .claude/skills/org-retro/SKILL.md:149-158 | candidate_queue / curated_only の扱い | keep | skill-eligibility-check/SKILL.md:125-126 が同旨を呼び出し元責務として再掲（正本は呼び出し元の本スキル） | 10 |
| .claude/skills/org-retro/SKILL.md:159-164 | Step 5: 報告 | keep | 人間向け報告文例。他に同規定なし | 6 |
| .claude/skills/org-retro/SKILL.md:165-167 | skill_recommend はバッチ問い合わせで（再掲） | delete | 同ファイル 107-111（黙って次へ・pending≥5 のバッチ問い合わせまたは /skill-audit 発火時のみ）の逐語に近い再掲。Read で照合<br>**補足**: 正本は同ファイル Step 4.2 skill_recommend 節（103-112）。Step 5 は 164 行の『報告不要』で足りる<br>**検証**: 反証で維持: 165-167 は 107-111 の『pending>=5 のバッチ問い合わせか /skill-audit 発火時に限る』をそのまま言い直したもので、参照先も同じ knowledge/skill-candidates.md。片方にしか無い条件は無く、164 の『報告不要』で足りる。tests から参照されていない。編集は .in 側 | 3 |

### .claude/skills/org-retro/references/work-skill-template.md（77 行: keep 64 / move 0 / delete 0 / outdated 13 / transport 0）

| 行範囲 | 節 | 分類 | 根拠 | 行数 |
|---|---|---|---|---|
| .claude/skills/org-retro/references/work-skill-template.md:1-13 | テンプレ frontmatter（type / triggers / origin） | outdated | owner: 欄が無い。非 org-* skill 11 件すべて owner: あり（grep -c '^owner:' 各 =1）、type: / origin: は 11 件中 0 件。テンプレは a266d07（2026-04-30 初回公開）以来未更新<br>**補足**: skill-audit/SKILL.md:118-125 は owner: / maintainer: 欠落を監査対象にする。テンプレに owner: を追加し、type/origin は実在 skill で使われていない（0/11）ので要否を判断<br>**検証**: 反証で維持: テンプレの 1-13 行に owner: が無い。非 org-* skill 11 件はすべて owner:=1。type:/origin: は 0/11、triggers: は wsl だけが 1。owner は 3ec9ae6（#817, 2026-08-03）で skill-audit Step 4 に合わせて足された。git log でテンプレは a266d07 以降更新が無い | 13 |
| .claude/skills/org-retro/references/work-skill-template.md:14-48 | 本文テンプレ（概要/背景/前提条件/手順） | keep | consumer: org-retro/SKILL.md:122、knowledge-curation-contract.md:104,108 が skill 作成元テンプレとして参照 | 35 |
| .claude/skills/org-retro/references/work-skill-template.md:49-77 | 本文テンプレ（成果物/判断基準/応用/注意点） | keep | 同上。runtime-release-with-paired-ja-sync/SKILL.md:314-366 がこの見出し構成（成果物/判断基準・閾値/応用・バリエーション/注意点）を踏襲 | 29 |

### .claude/skills/runtime-release-with-paired-ja-sync/SKILL.md（372 行: keep 308 / move 46 / delete 12 / outdated 4 / transport 2）

| 行範囲 | 節 | 分類 | 根拠 | 行数 |
|---|---|---|---|---|
| .claude/skills/runtime-release-with-paired-ja-sync/SKILL.md:1-26 | frontmatter（owner/description/allowed-tools、送受信系以外） | keep | tools/gen_delegate_payload.py・tools/journal_append.sh/.py 現存（ls）。.in 無しの手保守 skill（ls で SKILL.md のみ）。直近更新 6889d66（2026-09-04）/ d221e6a（2026-08-09） | 26 |
| .claude/skills/runtime-release-with-paired-ja-sync/SKILL.md:27-28 | allowed-tools: mcp__org-broker__send_message / check_messages | transport | broker 名のみ列挙（renga-peers 名なし）。生成器を通らない手保守ファイルのため transport 方針を変えると手で直す必要あり | 2 |
| .claude/skills/runtime-release-with-paired-ja-sync/SKILL.md:29-40 | タイトル・概要・スコープ | keep | 窓口専属（owner: secretary, 3 行）。対象 test/fixture は ls で全て現存 | 12 |
| .claude/skills/runtime-release-with-paired-ja-sync/SKILL.md:41-61 | なぜこの skill が必要か | move | カスケードの経緯と 3 重コストの説明で手順を含まない。対象 4 ファイル（tests/test_attention_runtime_integration.py ほか）は Step 4 表 215-222 で再列挙される<br>**検証**: 反証で維持: 41-61 には手順の指示が無い。挙がっている 4 ファイルは、test_attention / org_extension_schema / attention.example.json が Step 4 の表（216-221）に、permissions.md が 233 行に再掲されている。tests からの参照も無い | 21 |
| .claude/skills/runtime-release-with-paired-ja-sync/SKILL.md:62-89 | 発動条件（required_* 追加・2 系統チェック） | keep | tests.yml:43 check_runtime_schema_drift.py / :49 check_role_configs.py --include-worker-settings。user_common settings_paths=[]（org_extension_schema.json）。実例 2026-08-08 runtime #161（50fc376） | 28 |
| .claude/skills/runtime-release-with-paired-ja-sync/SKILL.md:90-99 | Step 1: pre-fetch（git fetch + pull --ff-only） | delete | org-delegate/SKILL.md:172-180（Step 0.6、task_id に release を含む等の同条件）と org-delegate/references/release-pre-fetch.md:9-16 に同一コマンド・同一目的。派遣直前に実行する場所は org-delegate<br>**補足**: 正本は .claude/skills/org-delegate/SKILL.md Step 0.6（172-180）+ references/release-pre-fetch.md。本 skill からは参照 1 行に<br>**検証**: 反証で維持: コマンドと発動条件は org-delegate/SKILL.md:172-180 と references/release-pre-fetch.md:9-24 と一致する。ただし 101-102 の『non-ff なら派遣せず人間に報告』は正本 2 か所に無いので、範囲外の 101-102 は必ず残す。90-99 は参照 1 行に置き換える | 10 |
| .claude/skills/runtime-release-with-paired-ja-sync/SKILL.md:100-106 | non-ff なら派遣せず人間報告・cross-link | keep | non-fast-forward 時の停止は release-pre-fetch.md に無い固有規定（Read で確認） | 7 |
| .claude/skills/runtime-release-with-paired-ja-sync/SKILL.md:107-119 | Step 2: test_smoke.py の version リテラル co-update | keep | claude-org-runtime/tests/test_smoke.py:5 assert __version__ == "0.1.49"（リテラル現存）。__about__.py が SoT | 13 |
| .claude/skills/runtime-release-with-paired-ja-sync/SKILL.md:120-133 | Step 3: tag push は窓口手動・ユーザー承認 | keep | runtime .github/workflows/release.yml:3-5 on: push: tags ["v*"]。worker push 不可は worker brief 権限節。130 の [[chat_auth_is_enough]] は operator-local memory 参照（judgments） | 14 |
| .claude/skills/runtime-release-with-paired-ja-sync/SKILL.md:134-145 | release.yml が検証しない 2 つの失敗（版数不一致 / stale SHA） | move | 理由説明。事実は確認済み: release.yml は checkout→build→publish のみでタグ名・ancestry 検証なし、pyproject.toml:3,36 dynamic version = __about__.__version__。手順は 146-194 のブロックで完結<br>**検証**: 反証で維持: 134-145 は『なぜ検証が要るか』の説明。版数の SoT（__about__.py）は (d) の 177-180 行にコマンドとして入っており、手順側だけで実行できる。見出しの 132『省略不可』と 146 以降のブロックは残す。release.yml は runtime リポジトリ側にあるので ja-main では確認していない（move の判定には影響しない） | 12 |
| .claude/skills/runtime-release-with-paired-ja-sync/SKILL.md:146-194 | tag 前 fail-closed 検証ブロック（a〜f）と STOP 時の人間判断 | keep | src/claude_org_runtime/__about__.py 現存・__version__ = "0.1.49" 形式で sed 抽出と一致。d221e6a（2026-08-09, 現物突合修正）で整備 | 49 |
| .claude/skills/runtime-release-with-paired-ja-sync/SKILL.md:195-201 | 残余レースの扱い | move | 設計理由（CAS 不在でも不変 SHA に tag するので安全）の説明。手順上の要点『(e) を push 直前に置く』はブロック 182-185 に既にある<br>**検証**: 反証で維持: 196-201 は CAS が無いことと不変 SHA を使う理由の説明。手順の要点（(e) を push の直前に置き、外れたらやり直す）は 182-185 のコマンドと echo 文『最初からやり直す』に既にある | 7 |
| .claude/skills/runtime-release-with-paired-ja-sync/SKILL.md:202-208 | release.yml 監視は --repo 明示 | keep | allowed-tools 22-25 に gh run watch/view/list/rerun。窓口 cwd が ja root（CLAUDE.md の役割） | 7 |
| .claude/skills/runtime-release-with-paired-ja-sync/SKILL.md:209-220 | Step 4: paired ja-sync 対象表（前半） | keep | tests/fixtures/attention/expected_scan.json・tests/fixtures/runtime_schema_drift/sandbox_intent/・tests/test_runtime_schema_drift_semantic.py:25（hard failure）を ls/grep で確認。直近 31a5c40 が attention 系を同時更新 | 12 |
| .claude/skills/runtime-release-with-paired-ja-sync/SKILL.md:221-221 | attention 文言変更行（『15 kind ぶん』） | outdated | tools/templates/attention.example.json の templates は現在 16 キー（python で len 計数）。31a5c40^ では 15、31a5c40（2026-10-11）で ci_passed が追加された。_DISPATCH_ONLY_KEYS は test_attention_runtime_integration.py:61 に現存<br>**補足**: 『16 kind ぶん』（ci_passed 追加後、tools/templates/attention.example.json）。件数を書かず『kind ごとに』とするのが陳腐化しにくい<br>**検証**: 反証で維持: python で len(templates) を数えると現 HEAD は 16（ci_passed を含む）、31a5c40^ では 15。31a5c40（#1047）の stat に tools/templates/attention.example.json +5 がある。訂正は『16 kind』、または件数を書かない形にする | 1 |
| .claude/skills/runtime-release-with-paired-ja-sync/SKILL.md:222-223 | version floor 5 ファイル atomic 行 | keep | 5 ファイルは全て現存。ただし docker/Dockerfile:65・compose.yaml:17・README.md:83 は 0.1.46 のまま、pyproject.toml:30 は 0.1.49。c5987cc/f27f533/31a5c40 の 3 回の bump で docker を動かしていない（judgments） | 2 |
| .claude/skills/runtime-release-with-paired-ja-sync/SKILL.md:224-251 | 並列分割の可否・projection 変更は atomic PR | keep | check_role_configs.py が projection と tracked settings を検証（tests.yml:49）。:811,864 で settings_paths を解決。229 の [[parallelize_delegation]] は operator-local memory | 28 |
| .claude/skills/runtime-release-with-paired-ja-sync/SKILL.md:252-271 | pin window 定数 RUNTIME_PIN_LOWER_INCLUSIVE の bump 条件 | keep | check_runtime_schema_drift.py:65 = (0, 1, 48)、:97-98 _runtime_in_pin_window。31a5c40 のコミット本文が『$comment のみなので (0,1,48) 据え置き』と本規則どおり運用、requirements.txt のコメントにも記録 | 20 |
| .claude/skills/runtime-release-with-paired-ja-sync/SKILL.md:272-302 | Step 5: CI cascade の予測と委譲 | keep | tests.yml:3-7 on: push / pull_request のみ（PyPI publish で再実行されない事実を確認）。予測 red の 4 ツール/テストは全て現存 | 31 |
| .claude/skills/runtime-release-with-paired-ja-sync/SKILL.md:303-313 | Step 6: 同一セッションでの land | keep | .claude/skills/secretary-handover/SKILL.md 現存。handover へ残タスクを明記する手順 | 11 |
| .claude/skills/runtime-release-with-paired-ja-sync/SKILL.md:314-330 | 成果物・判断基準表 | keep | Step 4 の overlap ルールの要約。329 の [[secretary_does_not_investigate_ci]] は 284 と同参照 | 17 |
| .claude/skills/runtime-release-with-paired-ja-sync/SKILL.md:331-344 | 応用・バリエーション | keep | schema のみ / DEFAULT_NOTIFY のみ / classifier 追加の 3 パターン。Step 4 表と整合 | 14 |
| .claude/skills/runtime-release-with-paired-ja-sync/SKILL.md:345-347 | 『直近の実例は duplicate_sidecar』 | outdated | duplicate_sidecar は 3333b6d（#871, 0.1.40）。その後 31a5c40（2026-10-11, 0.1.49）で ci_passed kind が attention artifacts（expected_scan.json / test / template）と floor を同時に更新（git show --stat）<br>**補足**: 直近の実例は ci_passed（31a5c40, #1047）。ただし同 commit は docker 3 ファイルの floor を上げていないので、実例として挙げるなら floor 5 ファイル規則の扱いを先に決める<br>**検証**: 反証で維持: git log -- expected_scan.json で最新は 31a5c40（ci_passed、2026-10-11）、duplicate_sidecar の 3333b6d はその前。ただし docker 3 ファイルの floor まで含めて同じ commit で上げているのは 3333b6d だけで、31a5c40 は pyproject/requirements だけ（Dockerfile は 0.1.46 のまま）。誤りは『直近』の一語なので、例としては 3333b6d のままでよい | 3 |
| .claude/skills/runtime-release-with-paired-ja-sync/SKILL.md:348-349 | 注意点（見出し） | keep | 見出しのみ | 2 |
| .claude/skills/runtime-release-with-paired-ja-sync/SKILL.md:350-351 | v-タグ承認・ff-only 中断（再掲） | delete | 350 は同ファイル 129-130（tag push 前に明示承認）、351 は 101-102（non-ff で派遣せず人間報告）の再掲。Read で照合<br>**補足**: 正本は同ファイル Step 3（120-131）と Step 1（100-102）<br>**検証**: 反証で維持: 350 は 129-130（明示承認）と 125-126（版番号が焼かれる）の言い直し、351 は 101-102 の言い直しで、片方にしか無い条件は無い。前提として 101-102 は残すこと（Step 1 の delete 範囲 90-99 には入っていない）。tests からの参照は無い | 2 |
| .claude/skills/runtime-release-with-paired-ja-sync/SKILL.md:352-366 | 注意点（時間圧・schema 丸ごとコピー禁止・template は CI 非検出） | keep | tests 配下に attention.example.json を読むコードなし（git grep -n attention.example.json -- tests = 0 hit）。test は --state-dir のみ渡す（test_attention_runtime_integration.py:275） | 15 |
| .claude/skills/runtime-release-with-paired-ja-sync/SKILL.md:367-372 | 履歴的背景（v0.1.11・Issue #25/#26/#28） | move | 経緯の記述のみで手順なし。現行 runtime は 0.1.49（__about__.py）<br>**検証**: 反証で維持: 368-372 は v0.1.11 と Issue 番号の経緯だけで、手順は無い。tests/tools からの参照も無い | 6 |

### .claude/skills/work-discovery/SKILL.md（256 行: keep 203 / move 16 / delete 12 / outdated 11 / transport 14）

| 行範囲 | 節 | 分類 | 根拠 | 行数 |
|---|---|---|---|---|
| .claude/skills/work-discovery/SKILL.md:1-20 | frontmatter | keep | ls tools/work_discovery_{repos,scan,goals}.py 全て存在。put-aside サブコマンドは tools/work_discovery_goals.py:1051-1053（--ref/--note）。生成元 .in なし（ls .claude/skills/work-discovery → SKILL.md のみ） | 20 |
| .claude/skills/work-discovery/SKILL.md:21-30 | 導入・ゴール未設定の案内 | keep | registry/goals/README.md と .claude/skills/goal-setup/SKILL.md が存在（ls）。goal_unset_repos は scan/goals で 2 ヒット | 10 |
| .claude/skills/work-discovery/SKILL.md:31-35 | 設計参照・計算層ツール・repo 解決ツール | keep | docs/design/work-discovery-triage.md, tools/work_discovery_repos.py 存在（ls）。scan の副作用は .state/work_discovery/ と判定段（scan.py:2942 help, goals.py:58 DEFAULT_JUDGE_TIMEOUT=90） | 5 |
| .claude/skills/work-discovery/SKILL.md:36-36 | 案 B / 定常トリガ・post-merge は別 Phase | outdated | dispatcher の worker クローズ時 scan は .dispatcher/references/pane-close.md:600-620 で実装済み（--trigger worker_close）、post-merge は org-conveyor/SKILL.md:151 が --trigger post_merge を呼ぶ<br>**補足**: 定常トリガ（worker クローズ）は .dispatcher/references/pane-close.md Step 6（:600-620）、post-merge は .claude/skills/org-conveyor/SKILL.md:151 で既に配線済み。『別 Phase（別タスク）』ではなく『別スキル/別ロールが担う』と書くべき<br>**検証**: 反証で維持: pane-close.md:619 に `--trigger worker_close` の scan 手順が実装済み、org-conveyor/SKILL.md:151 は `--trigger post_merge` を呼ぶ。同じファイルの :108 も post_merge 起動を手順として書いている。「別 Phase」は事実と食い違う | 1 |
| .claude/skills/work-discovery/SKILL.md:37-45 | 起動主体とトリガ（窓口限定・常駐 /loop なし） | keep | propose-only 契約は scan の exit 0/10/2（scan.py:179-181）と対。ワーカー非起動は CLAUDE.md 役割境界と一致。現行の規則として有効 | 9 |
| .claude/skills/work-discovery/SKILL.md:46-47 | 定常トリガは別案（dispatcher 拡張・別 Phase） | outdated | 同上: pane-close.md:600-620 が worker_close scan を実行、org-setup/references/permissions.md:335 も『worker クローズ時の work-discovery scan』を前提に許可を配布<br>**補足**: 『worker クローズ時の scan は dispatcher（.dispatcher/references/pane-close.md:600-620）が実施済み。本スキルの責務ではない』に書き換え<br>**検証**: 反証で維持: 「本スキルの責務ではない」は正しいが、「別案・別 Phase」の括弧書きは古い。worker クローズ時の scan は pane-close.md:600-620 で dispatcher が実施済み。permissions.md:335 も worker クローズ時の scan を前提に許可を配っている | 2 |
| .claude/skills/work-discovery/SKILL.md:48-53 | INV-1 propose-only（本文） | keep | scan.py:179-181 exit code 契約、副作用は .state/work_discovery/ のみ（scan.py:2942）。現行の不変条件 | 6 |
| .claude/skills/work-discovery/SKILL.md:54-61 | INV-1 括弧書き（allowed-tools は宣言的意図・hook 例） | outdated | 本文 :54-55 は『allowed-tools が repo 解決と scan の read-only コマンドだけに絞られている』とするが、frontmatter :18-19 は書き込みを伴う work_discovery_goals.py put-aside も列挙。.hooks/block-foreground-subagent.sh は存在（ls）。残りは allowed-tools の意味論の説明（設計理由）<br>**補足**: allowed-tools には put-aside（.state/work_discovery/ への見送り台帳追記、goals.py:420）も含まれる。修正のうえ allowed-tools の意味論説明は references/rationale.md へ move 推奨<br>**検証**: 反証で維持: frontmatter の :18-19 には put-aside も入っている。put-aside は goals.py の append_put_aside で put_aside.jsonl に追記する（open 'a'）。「read-only コマンドだけ」は不正確。.hooks/block-foreground-subagent.sh は現存（ls で確認）。allowed-tools の意味論の説明は move でよい | 8 |
| .claude/skills/work-discovery/SKILL.md:62-69 | INV-2〜INV-5 | keep | /org-delegate Step 0 への接続（.claude/skills/org-delegate/SKILL.md 存在）。人間ゲート・窓口経由・秘書非調査は現行の役割規則 | 8 |
| .claude/skills/work-discovery/SKILL.md:70-80 | Step 1 見出し・実行コマンド | keep | --trigger（scan.py:2882）, --all-registry-repos（scan.py:2848）存在。consumer は窓口（本スキル） | 11 |
| .claude/skills/work-discovery/SKILL.md:81-82 | Bash timeout 300000 | keep | 判定段既定 90 秒（goals.py:58）。dispatcher 側にも同規則（pane-close.md:604）があるが役割別の実行箇所なので重複扱いしない | 2 |
| .claude/skills/work-discovery/SKILL.md:83-86 | 判定段は api.anthropic.com に出る | keep | org-setup/references/permissions.md:213,219 と tools/org_extension_schema.json:235 が allowedDomains を配布、tools/check_role_configs.py 存在。git ff6d785 2026-09-30 (#1031) | 4 |
| .claude/skills/work-discovery/SKILL.md:87-94 | --rank-mode 既定 goal / --all-registry-repos の解決 | keep | --rank-mode（scan.py:2925）、--all-registry-repos（scan.py:25,2848）。repos.py 存在 | 8 |
| .claude/skills/work-discovery/SKILL.md:95-98 | 旧手順禁止の理由（zsh SH_WORD_SPLIT, Issue #829） | move | 経緯説明。git 7bda7af 2026-08-06 (#832) で解消済み、scan.py:31,2569 にも同理由のコメント。手順としては『resolver 別実行の旧手順は使わない』1 行で足りる<br>**検証**: 反証で維持: :96-98 は zsh SH_WORD_SPLIT の経緯（7bda7af 2026-08-06 #832 で解消）で、scan.py:31,2570 と repos.py:40 にも同じ説明がある。:95 の「旧手順は使わない」1 行は手順として残す前提で move。tests/tools にこの文言を grep するものは無い | 4 |
| .claude/skills/work-discovery/SKILL.md:99-104 | repo_resolution 監査情報・解決失敗は exit 2 | keep | repo_resolution は scan/goals で 20 ヒット、EXIT_ERROR=2（scan.py:181,3209,3223） | 6 |
| .claude/skills/work-discovery/SKILL.md:105-105 | resolver は read-only（再掲） | delete | 同一ファイル内で 3 回目の記述（:35 と :60-61 に同文）。grep -n 'read-only' .claude/skills/work-discovery/SKILL.md で :34,:35,:55,:60,:105,:140 ヒット<br>**補足**: 正本 .claude/skills/work-discovery/SKILL.md:35<br>**検証**: 反証で維持: :35 と :60-61 に同じ内容（git remote get-url / gh repo view 読み取りのみ）がある。tests/tools からの参照も無い（git grep でヒットなし） | 1 |
| .claude/skills/work-discovery/SKILL.md:106-110 | --repo 排他・--trigger post_merge | keep | 排他チェック scan.py:3027-3034、post_merge は scan.py:2885 help と org-conveyor/SKILL.md:151 の consumer | 5 |
| .claude/skills/work-discovery/SKILL.md:111-118 | --free-panes の意味（broker 面 / renga 面） | transport | broker 面（ORG_TRANSPORT=broker/コード既定）と renga 面（opt-in）の二重記述。scan.py:125-129,2879 も同読み替え。runtime runner.py:2324 が free_worker_slots を ja --free-panes の consumer と名指し | 8 |
| .claude/skills/work-discovery/SKILL.md:119-124 | アクティブ worker 数は list_peers で数える（renga 2.0） | transport | renga 2.0 の caller タブスコープ前提の注記。runtime runner.py:488 count_worker_population 存在。renga 面専用の補足で broker 面とは別記述 | 6 |
| .claude/skills/work-discovery/SKILL.md:125-136 | renga overflow を配線したら plan.capacity を使う | move | 『配線したら』の未来条件。.dispatcher/CLAUDE.md:58 と spawn-flow.md:46 が『現行 ja は --overflow-to-new-tab 等を渡していない（今日は到達しない）』と明記。今日実行されない手順の設計理由<br>**検証**: 反証で維持: .dispatcher/CLAUDE.md:58 と spawn-flow.md:46 に「現行 ja は --overflow-to-new-tab 等を渡していない（実配線は未着手）」とあり、今日は実行されない条件付きの手順。`free_worker_slots` を grep する tests/tools は無い | 12 |
| .claude/skills/work-discovery/SKILL.md:137-139 | --top-n 既定 3・単一 JSON・exit code 分岐 | keep | --top-n（scan.py:122,2866）、EXIT_* 定数 scan.py:179-181 | 3 |
| .claude/skills/work-discovery/SKILL.md:140-141 | ツールは read-only（gh 読み取りのみ） | delete | 同ファイル :34 と :50-53 に副作用（.state/work_discovery/ 書き込み + claude -p）込みの正確な記述があり、本行は重複かつ『gh のみ』と不正確<br>**補足**: 正本 .claude/skills/work-discovery/SKILL.md:34 / :50-53<br>**検証**: 反証で維持: 「gh 読み取りのみ」は :34 / :51-52（.state/work_discovery/ への書き込みと claude -p）と食い違っており、正確な正本は :34/:50-53。後半の副作用の禁止も INV-1 の :53 と同じ。tests 側からの参照も無い | 2 |
| .claude/skills/work-discovery/SKILL.md:142-151 | Step 2 exit code 分岐表 | keep | exit 0/10/2（scan.py:179-181）、goal_rank.judge.status・goal_errors（8 ヒット）・excluded_merged（7）・input_truncated（15）が scan/goals に存在 | 10 |
| .claude/skills/work-discovery/SKILL.md:152-191 | Step 3 goal モード例とレンダリング規則 | keep | goal_request(2)/open_points(9)/excluded_goal(7)/goal_unset_repos(2)/cost_usd(7) が tools/work_discovery_{scan,goals}.py に存在（grep -c）。git 4a52d6f 2026-09-30 (#1028) で導入 | 40 |
| .claude/skills/work-discovery/SKILL.md:192-209 | legacy モード例と例の注記 | keep | --rank-mode legacy は scan.py:81,2552 に現存。人間指示時のみ使う経路。頻度が低いため references への退避は判断事項（judgments 参照） | 18 |
| .claude/skills/work-discovery/SKILL.md:210-226 | 共通レンダリング規則 | keep | effort_estimated(4)/unblocked_by_recent_merge(10)/excluded_blocked(8)/truncated_count(8)/base_branch_signals(8)/repo_resolution(20) が scan に存在（grep -c） | 17 |
| .claude/skills/work-discovery/SKILL.md:227-241 | Step 4 停止・見送り記録 | keep | put-aside --ref owner/repo#N（goals.py:1051-1053, :420）。--ref required=True | 15 |
| .claude/skills/work-discovery/SKILL.md:242-247 | パス解決 | keep | 窓口 CWD を前提にした相対パス規則。allowed-tools :14-19 に python3/py -3 両形を登録済み | 6 |
| .claude/skills/work-discovery/SKILL.md:248-256 | やらないこと（INV まとめ） | delete | 見出し自身が『INV まとめ』。各 bullet は :38-46（起動主体・/loop）と :48-69（INV-1〜5）の再掲で新情報なし<br>**補足**: 正本 .claude/skills/work-discovery/SKILL.md:38-46 / :48-69<br>**検証**: 反証で維持: 各 bullet を照合した: 自動着手=:64、spawn 等=:53/:65、GitHub 直接=:67、自前調査=:68-69、/loop=:43、ワーカー起動=:40。新しい条件は無い。「やらないこと」「INV まとめ」を grep する tests/tools も無い | 9 |

### .claude/skills/skill-audit/SKILL.md（167 行: keep 135 / move 12 / delete 8 / outdated 3 / transport 9）

| 行範囲 | 節 | 分類 | 根拠 | 行数 |
|---|---|---|---|---|
| .claude/skills/skill-audit/SKILL.md:1-14 | frontmatter（transport 行以外） | keep | 生成物。編集は .in 側（tools/skill_src/manifest.json:54-55 が SKILL.md.in→SKILL.md）。consumer: org-curate/SKILL.md:58-59,252（Step 6） | 14 |
| .claude/skills/skill-audit/SKILL.md:15-15 | allowed-tools: mcp__org-broker__send_message | transport | 生成元 SKILL.md.in:15 は mcp__renga-peers__send_message。生成器がコード既定 broker（tools/transport.py:19-24 DEFAULT_TRANSPORT）で render した結果。運用は renga | 1 |
| .claude/skills/skill-audit/SKILL.md:16-26 | タイトル・目的 | keep | 状態ベース発火・提案のみの方針は org-curate Step 6（org-curate/SKILL.md:252）と対 | 11 |
| .claude/skills/skill-audit/SKILL.md:27-33 | 輸送層（transport）両系注記 | transport | 既定 broker / opt-in renga、mcp__org-broker__*→mcp__renga-peers__* 読み替え、Epic #6 Issue G 待ち、二フレーム注記（Refs #604）。skill-audit 自身は send_message 1 回しか使わず spawn 儀式・エラー分岐の記述は不要 | 7 |
| .claude/skills/skill-audit/SKILL.md:34-57 | Step 1 発火条件チェック（awk / find） | keep | tools/test_check_curate_threshold.py:40-56 が本 Step 1 のコマンドを verbatim で parity 検査。check_curate_threshold.py:88-90,130 CANDIDATE_ENTRY_PATHS | 24 |
| .claude/skills/skill-audit/SKILL.md:58-61 | 数値の根拠・org-* 除外の理由 | move | N=5/M=20 と org-* 除外の設計理由。手順はコマンド :49-53 と :56-57 で完結<br>**検証**: 反証で維持: N=5/M=20 と org-* 除外の理由説明だけ。手順の判定条件は :56-57 とコマンド :49-53 で完結する。parity テスト（test_check_curate_threshold.py:333-）が見るのは :49 の awk で、この節は対象外。編集は .in 側（manifest.json:54） | 4 |
| .claude/skills/skill-audit/SKILL.md:62-78 | カウント定義の同期（3 者同期） | keep | test_check_curate_threshold.py:14-15,40-44 が parity テストを持ち、knowledge/skill-candidates.md:40-48 も同規則。本スキルが Step 1 の実行場所なのでこちらを残す | 17 |
| .claude/skills/skill-audit/SKILL.md:79-86 | deferred は数えない（Issue #753） | delete | deferred の遷移・再問い合わせ禁止・別日付エントリは knowledge/skill-candidates.md:52-58 と org-retro/SKILL.md:146 に同文。skill-audit 自身は status を更新しない（:84 自身が『ワーカー委譲経由』と記述）<br>**補足**: 正本 knowledge/skill-candidates.md:52-58（status 遷移定義）<br>**検証**: 反証で維持: deferred の遷移・再問い合わせ禁止・別日付エントリは skill-candidates.md:53,58,67 に、ワーカー委譲での更新は org-retro/SKILL.md:142-147 に同じ強さで書かれている。awk の完全一致による除外も skill-candidates.md:53 にある。tests の参照は無い | 8 |
| .claude/skills/skill-audit/SKILL.md:87-105 | Step 2 廃止候補 | keep | .state/workers/ は tools/state_db/queries.py, sweep_stale_workers.py 等が参照（grep -rln）。詳細は references/audit-checklist.md（存在） | 19 |
| .claude/skills/skill-audit/SKILL.md:106-116 | Step 3 重複統合候補 | keep | 手順本体。audit-checklist.md §2 と対 | 11 |
| .claude/skills/skill-audit/SKILL.md:117-125 | Step 4 owner 未明記 | keep | 手順本体。audit-checklist.md §3 と対 | 9 |
| .claude/skills/skill-audit/SKILL.md:126-127 | 既存 skill 全てが owner 未記載 | outdated | for f in .claude/skills/*/SKILL.md; grep -q '^owner:' → 26 中 11 が owner あり（非 org-* は全て）、欠落は org-* 15 本のみ。git 3ec9ae6 2026-08-03『work-skill 8 件の frontmatter に owner を追記 (#817)』<br>**補足**: 非 org-* skill は全て owner 記載済み（#817, 3ec9ae6）。未記載は org-* の 15 本（org-attach 〜 org-suspend）<br>**検証**: 反証で維持: owner 行の有無を数えると、26 skill のうち owner: が無いのは 15（いずれも org-*）。非 org-* skill は全部記載済み（3ec9ae6 #817）。「全て未記載」は事実と食い違う | 2 |
| .claude/skills/skill-audit/SKILL.md:128-130 | Step 5 見出し | keep | 見出しのみ | 3 |
| .claude/skills/skill-audit/SKILL.md:131-131 | org-broker の send_message で窓口へ | transport | org-broker 名指し。.in 側の同行を確認すると transport トークン展開（manifest.json:54-55 の生成対象） | 1 |
| .claude/skills/skill-audit/SKILL.md:132-146 | 報告テンプレ・0 件でも報告 | keep | 報告フォーマット本体。audit-checklist.md:84 が本テンプレを参照 | 15 |
| .claude/skills/skill-audit/SKILL.md:147-155 | トリガー経路 1（org-curate Step 6） | keep | org-curate/SKILL.md:58-59,252 Step 6 が reasons skill_candidates_pending/work_skill_count で発火。tools/check_curate_threshold.py 存在 | 9 |
| .claude/skills/skill-audit/SKILL.md:156-156 | 窓口が skill-candidates.md を見て手動起動 | outdated | knowledge/skill-candidates.md:8-12『エントリ常に空、実エントリは skill-candidates.local.md』（Issue #755, 101c6f6 2026-07-22）。公開ファイルを見ても pending は常に 0<br>**補足**: 見る対象は knowledge/skill-candidates.local.md（.gitignore:18）。公開ファイルは書式定義のみ<br>**検証**: 反証で維持: skill-candidates.md:8-12 によると公開ファイルにはエントリが常に無く、実エントリは .local.md（Issue #755）にある。見る対象は skill-candidates.local.md（または両方）に直すべき | 1 |
| .claude/skills/skill-audit/SKILL.md:157-159 | 人間依頼・/loop しない | keep | 現行規則（org-curate も常駐 /loop 廃止） | 3 |
| .claude/skills/skill-audit/SKILL.md:160-167 | 自動変更を避ける理由 | move | 設計理由の説明。手順上の規則（提案のみ）は :25 と :142 で既に述べている<br>**検証**: 反証で維持: 理由説明の節。提案だけで止めるという規則は :25 と :142 に既にある。この節にしか無い実行手順は無い | 8 |

### .claude/skills/skill-audit/references/audit-checklist.md（105 行: keep 92 / move 7 / delete 6 / outdated 0 / transport 0）

| 行範囲 | 節 | 分類 | 根拠 | 行数 |
|---|---|---|---|---|
| .claude/skills/skill-audit/references/audit-checklist.md:1-13 | 冒頭・1.1 言及検索 | keep | SKILL.md:91 から参照。knowledge/raw, knowledge/curated, .state/workers/ は現存の場所（.gitignore:16 raw は machine-local） | 13 |
| .claude/skills/skill-audit/references/audit-checklist.md:14-19 | 1.1 注意（採用有無は永続化されない） | delete | SKILL.md:98-100 に同趣旨（org-delegate は採用を永続化しない→言及検索は弱い）<br>**補足**: 正本 .claude/skills/skill-audit/SKILL.md:98-100<br>**検証**: 反証で維持: SKILL.md:98-100 と内容が同じ（採用の有無が永続化されず、言及検索は弱い）。checklist 側にしか無い条件は無く、tests の参照も無い | 6 |
| .claude/skills/skill-audit/references/audit-checklist.md:20-25 | 1.2 origin.task_id 再利用 | keep | grep -n '^origin' .claude/skills/*/SKILL.md → 0 件。現状は全 skill で skip される分岐（judgments 参照）。git: 2026-04-30 初版から未変更 | 6 |
| .claude/skills/skill-audit/references/audit-checklist.md:26-37 | 1.3 乖離・廃止候補の判定 | keep | 判定手順本体。SKILL.md:94 と対 | 12 |
| .claude/skills/skill-audit/references/audit-checklist.md:38-48 | 2.1 description 類似度 | keep | 判定手順本体 | 11 |
| .claude/skills/skill-audit/references/audit-checklist.md:49-57 | 2.2 triggers 重複 | keep | grep -ln '^triggers:' .claude/skills/*/SKILL.md → wsl-windows-chrome-screenshot 1 本のみ。ペア比較は現状ほぼ成立しない（judgments 参照） | 9 |
| .claude/skills/skill-audit/references/audit-checklist.md:58-67 | 2.3 特殊化・統合候補の判定 | keep | 判定手順本体 | 10 |
| .claude/skills/skill-audit/references/audit-checklist.md:68-81 | 3 owner 未明記チェック | keep | SKILL.md Step 4 に TBD プレースホルダ判定を加える詳細。26 中 15 本（org-*）が owner 欠落で検出対象が現存 | 14 |
| .claude/skills/skill-audit/references/audit-checklist.md:82-98 | 報告フォーマット | keep | SKILL.md:132-143 テンプレの判定根拠部分 | 17 |
| .claude/skills/skill-audit/references/audit-checklist.md:99-105 | 判定しきい値のメモ | move | 閾値の設計理由（偽陰性/偽陽性のトレードオフ）。数値自体は :12,:52,:65 に既出<br>**検証**: 反証で維持: 閾値は本文の :12（90 日）、:52（50%）、:65（2 つ以上）に既に書かれている。この節は設計理由。:105 の「調整したら raw に記録」は軽い運用メモなので、rationale へ移しても支障は無い | 7 |

### .claude/skills/skill-eligibility-check/SKILL.md（152 行: keep 144 / move 8 / delete 0 / outdated 0 / transport 0）

| 行範囲 | 節 | 分類 | 根拠 | 行数 |
|---|---|---|---|---|
| .claude/skills/skill-eligibility-check/SKILL.md:1-21 | frontmatter・導入 | keep | consumer: org-retro/SKILL.md:69-75（Step 4.1）、org-curate/SKILL.md:147-162（Step 2.5）。生成元 .in なし | 21 |
| .claude/skills/skill-eligibility-check/SKILL.md:22-26 | なぜ共通スキルか | move | 設計理由（SoT 化の動機）。手順には不要<br>**検証**: 反証で維持: SoT にした動機の説明だけで、実行手順は含まない | 5 |
| .claude/skills/skill-eligibility-check/SKILL.md:27-50 | 入力契約 | keep | org-curate/SKILL.md:150-162 がこの YAML 形で渡す（raw_files の扱いまで記述） | 24 |
| .claude/skills/skill-eligibility-check/SKILL.md:51-72 | Step 1 5 シグナル表・Step 2 3 値分岐 | keep | references/signals.md が詳細、org-retro/SKILL.md:72 が signals.md を判断基準の実体として参照 | 22 |
| .claude/skills/skill-eligibility-check/SKILL.md:73-75 | 閾値 3 点の根拠（org-retro 旧版比） | move | 『org-retro 旧版の 2 つ以上』という過去経緯に基づく設計理由<br>**検証**: 反証で維持: 閾値は :67-71 の表で完結している。:73-74 は org-retro 旧版と比べた理由説明 | 3 |
| .claude/skills/skill-eligibility-check/SKILL.md:76-92 | Step 3 出力・Step 4 追記先 | keep | 追記先 knowledge/skill-candidates.local.md（.gitignore:18、Issue #755 101c6f6 2026-07-22） | 17 |
| .claude/skills/skill-eligibility-check/SKILL.md:93-99 | 追記先は必ず .local.md 注記 | keep | knowledge/skill-candidates.md:8-14 と同趣旨だが、書き込みを実行する本スキル側の制約として必要。check_curate_threshold.py:130 CANDIDATE_ENTRY_PATHS | 7 |
| .claude/skills/skill-eligibility-check/SKILL.md:100-117 | エントリテンプレ・書き込み後の返却 | keep | knowledge/skill-candidates.md:20-33 とほぼ同一テンプレ。本スキル :97 は公開ファイルを一次参照と宣言しているが、実行場所は本スキル（どちらを正本にするかは judgments） | 18 |
| .claude/skills/skill-eligibility-check/SKILL.md:118-134 | 呼び出し元の責務 | keep | org-retro/SKILL.md:109-167 と org-curate の後続手順に同内容があり重複の疑い（judgments 参照）。確証が取れないので keep | 17 |
| .claude/skills/skill-eligibility-check/SKILL.md:135-147 | 重複呼び出しの扱い（pending マージ・deferred 非再追加） | keep | Step 4 実行時の分岐で本スキル固有。deferred 定義は knowledge/skill-candidates.md:53,58（cff3d82 2026-07-22, #753/#754） | 13 |
| .claude/skills/skill-eligibility-check/SKILL.md:148-152 | スキルを呼ばないケース | keep | 呼び出し判断の境界。短く、削っても効果小 | 5 |

### .claude/skills/skill-eligibility-check/references/signals.md（80 行: keep 74 / move 0 / delete 6 / outdated 0 / transport 0）

| 行範囲 | 節 | 分類 | 根拠 | 行数 |
|---|---|---|---|---|
| .claude/skills/skill-eligibility-check/references/signals.md:1-17 | 冒頭・1 raw_reappearance | keep | knowledge-standards.md は .claude/skills/org-curate/references/knowledge-standards.md:39『同じ種類の知見が3件以上』で基準一致（find） | 17 |
| .claude/skills/skill-eligibility-check/references/signals.md:18-31 | 2 steps_complexity | keep | 採点手順本体。SKILL.md:58 から参照 | 14 |
| .claude/skills/skill-eligibility-check/references/signals.md:32-43 | 3 trigger_articulable | keep | 採点手順本体 | 12 |
| .claude/skills/skill-eligibility-check/references/signals.md:44-57 | 4 criteria_articulable | keep | 採点手順本体 | 14 |
| .claude/skills/skill-eligibility-check/references/signals.md:58-71 | 5 reusable_output | keep | 採点手順本体 | 14 |
| .claude/skills/skill-eligibility-check/references/signals.md:72-77 | 採点結果の扱い（3 値分岐） | delete | SKILL.md:65-71 の Step 2 表と同一の閾値（3 点以上/2 点/1 点以下）<br>**補足**: 正本 .claude/skills/skill-eligibility-check/SKILL.md:65-71<br>**検証**: 反証で維持: :74-76 は SKILL.md:69-71 の表と閾値が完全に同じ。続く :78-80（matched_signals の記録）は範囲外なので残る | 6 |
| .claude/skills/skill-eligibility-check/references/signals.md:78-80 | matched_signals の追跡 | keep | SKILL.md:83,104 の該当シグナル欄と対 | 3 |

### .claude/skills/wsl-windows-chrome-screenshot/SKILL.md（176 行: keep 167 / move 9 / delete 0 / outdated 0 / transport 0）

| 行範囲 | 節 | 分類 | 根拠 | 行数 |
|---|---|---|---|---|
| .claude/skills/wsl-windows-chrome-screenshot/SKILL.md:1-18 | frontmatter | keep | git 90266e6 2026-09-21 (#1021) 追加、.in は作らない方針（同 commit message）。triggers: を持つ唯一の skill | 18 |
| .claude/skills/wsl-windows-chrome-screenshot/SKILL.md:19-26 | 導入（Linux Chrome 不可・繰り返し踏んだ手順） | keep | 前提条件の説明。rondo 側の経緯参照は短い | 8 |
| .claude/skills/wsl-windows-chrome-screenshot/SKILL.md:27-38 | §0 sandbox 外撮影は窓口が行う | keep | git dd2953f 2026-09-22 (#1023)。2026-09-22 auto mode 分類器拒否 2 回の記録に基づく直近規則。worker CLAUDE.md の dangerouslyDisableSandbox 反復禁止と整合 | 12 |
| .claude/skills/wsl-windows-chrome-screenshot/SKILL.md:39-73 | §1 1 呼び出し化と撮影スクリプト | keep | rondo#314/#350/#351/#352（2026-09-21）由来の手順本体。page-preview.mjs 等は rondo 側ファイルで ja-main では検証不能 | 35 |
| .claude/skills/wsl-windows-chrome-screenshot/SKILL.md:74-88 | pgrep 自殺・stdin・exec node | keep | Exit code 144 の症状と回避。手順上の制約 | 15 |
| .claude/skills/wsl-windows-chrome-screenshot/SKILL.md:89-114 | §2 捨て撮り・§3 UNC 出力・§4 color scheme | keep | rondo#318 由来（2026-09-21）。無言失敗の検出手順 | 26 |
| .claude/skills/wsl-windows-chrome-screenshot/SKILL.md:115-127 | §5 症状の弁別 | keep | 弁別表。手順本体 | 13 |
| .claude/skills/wsl-windows-chrome-screenshot/SKILL.md:128-156 | §6 before/after は git archive 別ツリー | keep | :146-151 の cp バックアップ / command cp -f / git restore deny は worker brief（CLAUDE.md preflight）と重複気味だが撮影文脈固有（judgments 参照） | 29 |
| .claude/skills/wsl-windows-chrome-screenshot/SKILL.md:157-167 | §7 撮った PNG は必ず 1 回見る | keep | rondo#319（2026-09-20）由来の高さ・SPA ハッシュの注意 | 11 |
| .claude/skills/wsl-windows-chrome-screenshot/SKILL.md:168-176 | 出典 | move | 経緯の出典一覧。参照先 knowledge/raw/archive/2026-09-2x-*.md 7 本は ja-main に存在しない（ls → 全 MISSING。.gitignore:16 knowledge/raw/* は machine-local）。手順には不要<br>**検証**: 反証で維持: 出典の一覧で手順は含まない。ja-main の knowledge/raw/ は .gitkeep だけ（.gitignore:16 で machine-local）なので参照先は追跡外。tests/tools の参照も無い | 9 |

## 4. 判断が要る点（人が決めるべきもの）

### 4-A. 輸送層の二重記述（a / b / c）

**推奨: b。さらに b+ の採否を判断する。**

| | a: renga に一本化 | **b: 1 か所に集約し、各所は参照だけにする（推奨）** | c: 現状維持 |
|---|---|---|---|
| 削減 | 約 360 行（注記 291 ＋ 散在する分岐 約 70。後者は推測を含む）。org-attach（280 行、broker/tmux 専用。`org-attach:33`）と org-down（196 行）を廃止すればさらに ＋476 | 約 205 行 / 約 100KB。注記 233 行を約 29 か所 × 1.5 行に縮め、transport-duality.md 3 本（29 行）を 1 本（約 11 行）にまとめる | 0 |
| コスト | drift ゲートの transport を切り替える、生成物 19 本を再 render、allowed-tools を書き直す、brief の golden を作り直す、broker 用フラグメント 13 本を削除する、手保守の約 15 ファイルを手で直す | ヘッダのフラグメント 4 本などを 1 行の参照に書き換えて再生成する。手保守の約 15 ファイルは手で置き換える。golden を作り直す | なし |
| リスク | コード上の既定が broker のまま（`tools/transport.py:19-20`, `scripts/install.sh:281,283`）なので、文書に無い経路で動く人が出る。**オーナーが 2026-10-12 に判断した「renga または tmux」のうち、tmux の経路を閉じてしまう** | 参照先を開かずに作業すると、broker 固有の分岐を見落としうる。分岐する点は 3 つ（受信モデル / spawn 儀式 / エラー分岐）に限られるので、それを参照行に明記して緩和する | 二重の枠による混乱と肥大が続く |

b を推す理由は 3 つ。

- 仕組みが既にある。前例は b9eca49（#1010）で、`org-delegate:44`、`org-pull-request:47`、`pr-watch-pane:55-60` が参照だけの形になっている。
- オーナーの判断（renga か tmux か）より前に入れられる。
- 後から a に進める。

人が決めること:

1. **renga と tmux（broker）のどちらに寄せるか。** b を先に入れておけば、a に進むかは後で決められる。
2. **b+ を採るか。** b+ は、生成物を運用と同じ renga の面で render し直す案。
   - drift ゲート（`tools/test_gen_skill_prose.py:625`）を ja 側の定数（例: `TEMPLATE_TRANSPORT="renga"`）に切り替える。
   - これは「生成物はコード既定の面で render する」という設計（notes/broker-skill-generator-design.md）の変更になる。
   - 現状では、生成されたスキルの frontmatter `allowed-tools` は `mcp__org-broker__*` しか持たない（`dispatcher-resume:23-28`, `org-down:24-31`, `org-conveyor:27-36`, `runtime-release:27-28` ほか）。renga 運用では事前許可が効いていない可能性がある（推測、未検証）。
3. **「既定」をどちらの表記に揃えるか。** 手保守のファイルは「既定 renga」、生成物は「既定 broker」と逆になっている。
   - 手順にまで食い違いが出ている: `.dispatcher/CLAUDE.md:44` は `${ORG_TRANSPORT:-broker}`、`org-start/SKILL.md:103` は `${ORG_TRANSPORT:-renga}`。
   - `org-setup/SKILL.md:26` も「既定 renga」と書くが、コード（`tools/org_setup_prune.py:747-754`）は broker に射影する。
4. **Epic #6 Issue G の但し書きを、25 ファイル一括で消すか、1 か所に「未着手」と注記するか。**
   - 件数は `git grep -l 'Issue G' -- CLAUDE.md .claude .dispatcher | grep -v '\.in$'` の結果で 25（ガイダンスの 24 と 1 件違う）。
   - 完了や中止の記録は無い。`git log --grep='Issue G'` で出るのは、但し書きを追加した c106c97 / 469e1f9 / 0975882（2026-06-17）だけ。
5. **「broker は未活性」という前提が正しいか確認する。** `tools/peer_notify.py:98-120` の #941 の記録には、別の org が broker で peer messaging していた事例がある（どの org かは推測）。
6. **a を採る場合に廃止するか**: org-attach、org-down、`org-suspend:471-520` Phase 3.6（50 行）、`org-start:293-310` の C3、`:454-466` のサイドバー。
7. **transport の判断を待たずに先に直すか**: 次の 2 つは事実の誤り。
   - `org-pull-request/references/transport-duality.md:7`: #945（58d8eda）より後は `resolve()` を使い、無設定なら broker になる（`tools/peer_notify.py:7-9,90-136`）。
   - `org-conveyor/SKILL.md:88-89`: 「active transport は broker」とあるが、運用は renga。
8. **`org-attention-start` だけ表記が逆**: 手保守で renga 名を使い、`org-attention-stop` と `org-suspend` は broker 名で生成されている。どちらに揃えるか。`:31` は見出しだけで本文が無い（delete）。

### 4-B. ワーカー brief の正本（C1）

1. `worker-claude-template.md` について、次のどちらにするか。
   - (a) 削除して、参照元のポインタを `tools/templates/worker_brief_normal.md` に向け直す（推奨。約 370 行減）。参照元は `org-delegate/SKILL.md:141,361`、`instruction-template.md:4,83,222`、`org-pull-request/SKILL.md:76`、`ack-template.md:15`、`docs/contracts/role-contract.md:258`、`delegation-lifecycle-contract.md:86,147,152,155,178`。
   - (b) 人間向けの解説として残し、実テンプレートと同期していることをテストで強制する。
   - 現状、同期させるテストは無い（`git grep worker-claude-template -- tests` で 0 件）。
2. テンプレートにしか無い条項を、実際の brief に移すか捨てるか。今の時点では、どれも作業者には効いていない。
   - 完了報告前の rebase と「rebase clean」行（`:175-187`, `:323`）。`org-delegate/SKILL.md:141` がこれを前提にしている。
   - uv/pip のキャッシュ（`:61`, `:75-77`、#1014。2026-09-14 と 09-15 に実例あり）。
   - Codex を走らせる Bash の allowed_domains（`:59`、#1019）。
   - safety block に掛かったときの continuation spawn（`:281-283`）。`instruction-template.md:82,222` は brief に描画されない。
   - 検証深度が不明なら窓口に確認する（`:191`）、codex の availability check（`:205-213`）、別ワーカーにレビューを委譲しない（`:293`）。
   - `/tmp` 直下は書けない（`:127`）、doc-audit の denyWrite の穴（`:123`）。
   - 振り返り記録のフォーマット（`:337-354`）。正本の `knowledge-standards.md:10-19` は作業者に届かない。
   - 監査タスクで別仮説を反証する手順（`:139-151`）、probe 系の credential（`:152-162`）、生成物 prose の編集（`:163-174`）。いずれも delete に分類したが、自分で編集する brief に 1 行だけ移す価値があるか。
3. normal と self_edit の共通部分を fragment にまとめるか（C4）。非空行のうち 55 行が同じ。生成器の変更が要る。
4. brief 側の npm `allowed_domains` 規則（`worker_brief_normal.md:42` 付近）。c7875c0（#1033）の `tools/org_extension_schema.json:627` が既に代わりを務めている可能性がある（範囲外のため推測）。
5. `instruction-template.md:22-105`（手動テンプレート）と `:197-231`（AUTO テンプレート）が二重になっている。手動の経路が今も使われているか確認できれば、約 80 行減らせる。
   - `:222` は 1 行で約 2,000 字ある。短くするなら、runtime の strict パース（runner.py:499-500）に影響しないことを確かめる必要がある。

### 4-C. 正本の向き（重複の、どちらを残すか）

- **CLAUDE.md と org-delegate**: 軽量レーン（`CLAUDE.md:73-89` と `org-delegate/SKILL.md:58-96`）、1 worker 1 task（`SKILL.md:285-294` が CLAUDE.md を SoT と呼んでいる）。
  - 「手順を実行する場所を正本にする」方針なら、CLAUDE.md 側を縮める。
  - ただし窓口は org-delegate を呼ばずに Agent を直接使うことがある。CLAUDE.md に 1 行の要約を残すか決める。
- **CLAUDE.md:43-51（次タスクの提案）**: 削るなら、`org-pull-request/SKILL.md:340` にある「一次参照」の記述も直す。
- **CLAUDE.md:108-127（awaiting_user）**: 参照のハブなので keep にした。gate の一覧を各スキルに分散させるかは別に判断する。
- **自タブ確立 2 手段（C2）の正本**: `pane-close.md` Step 3 に置くか、`renga-error-codes.md` に新しく節を作るか。
- **close 判定表**: `worker-monitoring.md:1238-1279` を正本にしてよいか。`.dispatcher/CLAUDE.md:212-257` の要約をどこまで削るか。
- **その他の正本の選択**:

| 規則 | 候補 |
|---|---|
| INV-1/2/4/5 | `pane-close.md:560-569` か `.dispatcher/CLAUDE.md:272-276` |
| 受領記録ゲート | `pane-close.md:21-40` か `worker-monitoring.md:223` |
| split_capacity_exceeded | `.dispatcher/CLAUDE.md:86` か `spawn-flow.md:55-59` |
| 内部状態ファイルを触らない一覧 | 5 か所 |

- **`org-start/SKILL.md:500-543`（Appendix）**: `spawn-flow.md:104-112`、`pane-close.md:314-326` と互いに参照し合って循環している。spawn-flow 3-2 にまとめるか。
- **`.dispatcher/CLAUDE.md:171`「自動承認しない」**: 安全上の中核の規則。常時読む CLAUDE.md と、`worker-monitoring.md:1319` のどちらに置くか。
- **`.dispatcher/CLAUDE.md:164` の `spawn_gate.py audit`**: worker-monitoring のサイクルには含まれていない。CLAUDE.md から削るなら、先にサイクル手順へ移す。
- **pr-watch-pane の裸 name close 3 条件**: `pr-watch-pane:557-588` を正本にして、`org-pull-request:150-170` を参照と harness 追加要件だけにするか。
- **skill-eligibility-check のエントリテンプレート**: `:100-115` と `knowledge/skill-candidates.md:20-33` のどちらを正本にするか。
- **skill-eligibility-check の呼び出し元の責務**: `:118-134` を、呼び出し元の `org-retro:109-167` 側に寄せるか。
- **org-retro の実行主体**: 窓口（`CLAUDE.md:56`）とディスパッチャー（`knowledge-curation-contract.md:34,37,86`）のどちらか。決まれば、`pane-close.md:13-104` との重複の片方が delete になる。

### 4-D. 重複を削る前に、矛盾を解消しておくもの

- **C3**: `org-suspend:595-599` は「旧版 fallback で同名が 1 件だけなら採用してよい」と書く。正本の `renga-error-codes.md:117-120,142` と矛盾する（誤 delete の疑い #13）。
- **C9**: `org-delegate/SKILL.md:65,76` の「`run_in_background=true` 必須」は古い。正しいのは `CLAUDE.md:82` と `.hooks/block-foreground-subagent.sh:11-23`（40c33f1、#944）。
  - 正本側を直さずに CLAUDE.md 側を消すと、古い記述だけが残る。
  - 同じ行の `isolation="worktree"` が今も有効かは確認していない。
- **conveyor と work-discovery の free-pane 会計**: 条件が違う（誤 delete の疑い #9）。
- **sidecar の扱い**: `org-suspend` Phase 3.7（`:534-537`）は削除し、`org-attention-stop:71` は削除しない。揃えれば約 30 行減る。
- **矛盾する知見の扱い**: `knowledge-standards.md:34` は「両方残す」、`org-curate/SKILL.md:201` は「新しい日付を優先」。
- **dogfood の SoT の指し先**: `dogfood-protocol.md:3`、`org-delegate/SKILL.md:360`、`registry/dogfood_pending.md` がそれぞれ別の場所を指す。
  - consumed→closed をどちらが担うかも食い違う（`dogfood-protocol.md:40,69` と `org-pull-request/SKILL.md:303`）。
- **`.dispatcher/CLAUDE.md:71` のモデル既定の理由**: 「auto classifier が不安定」と書くが、`spawn-flow.md:112`（専用モデルなので無関係）と矛盾する。outdated。
- **`pane-layout.md:22-29` の curator 行**: `model="sonnet"` が無い（`pane-close.md:320` と比べて）。どちらが正しいか。
- **再送回数**: `pane-close:36` は 4 回、`worker-monitoring:223` は 3 回。
- **背景配置**: `worker-monitoring.md:321`「背景配置は発生しない」と `spawn-flow.md:82`「既に複数回行われている」が食い違う。
- **`--peer-cwd` の照合先**: `spawn-flow.md:248` と `.dispatcher/CLAUDE.md:107` は runs.worker_dir_id、`spawn_gate.py:47` と `spawn-flow.md:470` は delegate_sent の dir。
- **INV-1「merge は事前承認しない」**: `org-conveyor:71-73`、`scope-contract.md:27`。
  - これに対し、2026-06-13 にユーザーが「CI green なら merge まで自走」を恒久承認した記録がある（窓口の memory。`knowledge/curated/delegation.md:144-146`）。
  - 引用されている memory slug は 3 つとも見つからない。
- **docker の floor 5 ファイル atomic 規則**: `runtime-release:222,241-246` が実態と合わない。docker は 0.1.46 のままで、c5987cc / f27f533 / 31a5c40 の 3 回とも上げていない。規則を直すか、追随漏れとして docker を直すか。
- **CLAUDE.md:112「gate 4 箇所」**: 5 つ目の `ci_unconfirmed_head_gate`（`org-pull-request/SKILL.md:202`）が抜けている。未来形の句（`CLAUDE.md:126-127`、`org-delegate:366`、`org-escalation:64,76`、`org-pull-request:75`）もまとめて直すか。
- **`org-delegate/SKILL.md:174,180`「4 条件」**: 列挙は 3 つしかない。
- **`org-delegate/SKILL.md:254-264`**: 5 つ目のトリガー（`codex-design-review.md:15`）が抜けている。

### 4-E. 環境の確認が要るもの（読み取り専用の制約のため未確認）

- **`py -3` と `python` の綴り**: この Linux/WSL ホストで `command -v` を打つと、`/usr/bin/python3` しか無い。
  - 該当箇所: `worker-monitoring.md:513,535,595`、`.dispatcher/CLAUDE.md:209`、`org-resume:44,94`、`secretary-resume:85,163`、`secretary-handover:135`、`org-start:231,237,388`。
  - Windows ホストで動かす運用が残っているか。python3 に揃えるか、`worker-monitoring.md:781` のように注記で両方書くか。
  - `secretary_handover` と `secretary_resumed` のイベントは常に skip されていて、consumer も 0 件。直すか、Step ごと消すか。
- **`secretary-handover/SKILL.md:86` の素の `cp`**: `-i` alias の環境では、確認プロンプトで止まりうる。
- **`org-dashboard:13-14,42-45`**: open / start は WSL に無い。ポートが 8100/8101 に逃げる場合もある（`server.py:50,596`）。
- **`org-start` Block C / C2 / Step 0.5 の `dangerouslyDisableSandbox: true`**: 窓口ロールで今も使えるか。
- **renga 運用で実際に close できているか**: 自タブ確立の手段 (ii) caller_scope の確立手順が `pane-close` に無いので、ワーカーの CLOSE_PANE が常に保留に倒れている可能性がある（推測）。
- **live の renga 2.2.0 は capability を広告している**: 2026-10-12 に実測した。
  - したがって「現在配備の全 backend は旧版 fallback」という記述（`org-suspend:280,315-316,598`、ゲート `:40`、`renga-error-codes.md:5,22,142`）を直す必要がある。
  - first_drive gate の記録が実際に取れているかは、state.db を見ないと確認できない（ja-main に .state が無い）。
- **`/verify` と TaskCreate / TaskUpdate / TaskList が実在するか**: `org-conveyor:18-20,52,170,196`。
- **`permissions.md:58`「renga-peers 15 種」**: このセッションでは spawn_codex_pane を含めて 16 ツールが見えている。
- **spawn-flow の DB パス**: `:357-374` と `:375-380` は root 相対で書かれているが、dispatcher の実際の cwd は確認していない。
- **`spawn-flow.md:284`「renga での武装は未実走」**: このタスク自体が renga の下で send_keys による武装を受けている。検証済みに更新するか。

### 4-F. 残すか廃止するか

- **dogfood protocol 全体**: `dogfood-protocol.md` 86 行 ＋ テンプレート 79 行。`registry/dogfood_pending.md` は b78fda0（2026-05-08）以降更新が無く、エントリも 0 件。ただし `spawn-flow.md:71` が条件として読んでいる。
- **herdr preflight**: `org-start/SKILL.md:175-198`（24 行）。`tools/check_herdr_compat.py` は現存する。
  - herdr / wezterm の枝（`pr-watch-pane:96-118,504-506`、`org-pull-request:63,115-130` ほか）を、「tmux ペインかどうか」の 2 分類に縮めるか。
- **Step 0.6 の pre-fetch**: 前提の「worker は git fetch を deny されている」が誤り。fetch は許可されている（`tools/org_extension_schema.json:729`）。deny されているのは pull だけ（`:746`）。
- **`plan_version<2` の分岐**: `.dispatcher/CLAUDE.md:80`、`spawn-flow.md:221`。pin が `>=0.1.49` なので、この経路は通らない。
- **未配線の overflow の解説**: `spawn-flow.md:39-44`、`.dispatcher/CLAUDE.md:58`、`work-discovery:125-136`。delete にするか、rationale へ移すか。
- **proxy 経路**: `worker-monitoring.md:929-1021`（約 93 行）。register が欠損した記録があるかで決める。
- **`worker-monitoring.md:319-431`**: 背景タブ用の 113 行。別の reference に切り出すか。
- **当座運用の段落**: `worker-monitoring.md:468-488,521-529,700-723`。helper を直して消すか。
- **`renga-error-codes.md:186-313`**: 擬似シェル。delete か、例示として move か。
- **`IO_ERROR_DETECTED` / `IPC_PROTOCOL_ERROR`**: consumer が 0 件。
- **repo の外を指す参照**: knowledge/raw へのリンク（`.gitignore:16` で非追跡）、memory slug、`[[...]]` 参照（`runtime-release:130,229,284,329`、`org-escalation:44`、`org-pull-request:53`）。本文に展開するか、削るか。
- **イベントの扱い**:
  - `attention_watch_*` は catalog に無く、runtime 側でも二重に記録されている可能性がある。
  - `work_discovery_scanned` は台帳に定義が無い。
  - `retro_deferred` を再試行する手順が org-resume に無い。
- **skill-audit**: `audit-checklist.md:20-25`（origin。常に skip される）、`:49-57`（triggers を持つスキルが 1 本しかない）。
- **`work-skill-template.md:1-13`**: owner が無く、type / origin はどのスキルも使っていない。
- **org-resume**: 2026-05-14（96293f4）以降、更新されていない。4 点の食い違いを別タスクで直すか。
- **移す場合に本文へ 1 行残すか**: 理由の節を move した後、制約を 1 行だけ本文に残すかどうか。対象は `pr-watch-pane:237-251,316-321`、`runtime-release:134-145`、`claude-org-self-edit.md:106-109`、`org-curate:220-231`。
- **`org-curate` の送信義務**: 4〜5 回書き直されている。0-A を正本にして、Step 7 を参照だけにするか。

### 4-G. 誤 delete の疑い（重複検出で見つかったもの）

次の delete（重複）判定は、正本の側に条件が足りないか、正本の方が古い。**削る前に、正本へ条件を移すか、正本を直す必要がある。**

| # | delete 判定された行 | 問題 |
|---|---|---|
| 1 | `worker-claude-template.md:53-58,60,62-74` | 隣の `:59`（#1019）と `:61,:75-77`（#1014）が normal に無い |
| 2 | 同 `:108-133` | `/tmp` 直下は書けないこと、ダウンロードは `$TMPDIR` へ、doc-audit の穴が normal に無い |
| 3 | 同 `:189-280` | 検証深度が不明なときの確認と、availability check が normal に無い |
| 4 | 同 `:281-284` | safety block からの回復手順。`instruction-template` は brief に描画されないので、作業者に届く経路が無い（推測） |
| 5 | 同 `:285-309` | 「別ワーカーにレビューを委譲しない」が normal に無い |
| 6 | 同 `:333-362` | 記録フォーマットと「一般知識は記録不要」が normal に無い |
| 7 | 同 `:175-187`（delete 対象ではないが関連） | rebase の節が brief に無く、`SKILL.md:141` の前提が崩れている |
| 8 | `CLAUDE.md:73-80`（＋`:81-84`） | 正本の `org-delegate:65,76` の方が古い。`org-delegate:61` は CLAUDE.md を参照している |
| 9 | `org-conveyor/SKILL.md:97-107` | work-discovery と会計の条件が違う |
| 10 | `runtime-release/SKILL.md:90-99` | 「non-ff なら報告」（`:101-102`）は正本に無い。正本の「4 条件」は 3 つしか無い |
| 11 | `skill-audit/SKILL.md:79-86` | pending → deferred に遷移させる手順（`:83-84`）が正本に無い |
| 12 | `.dispatcher/CLAUDE.md:162-163` | 範囲は正しい。ただし直後の `:164` の `spawn_gate.py audit` は消してはいけない |
| 13 | `org-suspend/SKILL.md:575-614`（参考） | 正本と矛盾する（C3） |

確認した範囲では、この 13 件以外の delete（重複）判定は正本と条件が一致していた。

### 4-H. 既知の制限

- **行範囲の被覆ずれは無い。** 分類に失敗したグループも無い。
- **トークン数はすべて推定**（式は §1-1）。
- **祖先の CLAUDE.md が読まれるかは推測。** dispatcher、curator、ja を自分で編集するワーカーで、窓口用の CLAUDE.md が読まれるかを実測していない。dispatcher で `/memory` を実行すれば確認できる。
- **transport の行数は 4 通りの数え方で違う。** 分類段は 488 行、評価段の注記ブロックは約 291 行、二重記述の語を含む行は 205 行、重複検出で `>` の引用ブロックを機械的に数えると 213 行。どの方法で数えたかによる差で、どれかが誤りというわけではない。1 行が 1 段落分の長さなので、規模はバイト数（151,751B）で見る方が実態に近い。
- **§1-4 と §2 の数字は、上位 120 件のリストとクラスタを足した下限または概算。** クラスタ同士で行が重なるので、分類別の合計（§1-3）とは足し合わせられない。
- **作業者の brief の生成元 `tools/templates/` と `.curator/CLAUDE.md` は対象範囲の外。** 参考として行数を測っただけで、分類はしていない。
- **`.state`、窓口の作業コピー、memory は読んでいない。** これらに依存する判断はすべて「要確認」とした（§4-E）。

## 5. 公式資料の判定基準との差

基準の本文と出典 URL は依頼文の判定基準 1-16 に従う。数値はすべて ja-main 31a5c40 で測った（`wc -l` / `wc -m` / `grep -c` / python）。

### 5-1. 数値判定

**基準 1: SKILL.md は 500 行以内**（https://code.claude.com/docs/en/skills.md）

| SKILL.md | 行数 | 超過 | 生成元 .in |
|---|---|---|---|
| org-suspend | 791 | +291 | あり（tools/skill_src/manifest.json） |
| pr-watch-pane | 591 | +91 | あり |
| org-start | 543 | +43 | なし（手で保守） |
| 残り 23 個 | 53-436 | - | 12 個が生成物 |
| 合計 26 個 | 6959 | 3 個が超過 | - |

全 SKILL.md の文字数は 401,592 字（`wc -m`）。そのうち輸送層の両系ブロック 17 か所が 38,919 字で、約 10% を占める。

参考として、SKILL.md 以外にも同じ上限を当てはめた（基準 1 の直接の対象ではない）。

| ファイル | 行数 | 判定 |
|---|---|---|
| .dispatcher/references/worker-monitoring.md | 1359 | 大幅超過。「監視ループ 1 サイクル」節（:85-1338）だけで約 1,250 行ある |
| .dispatcher/references/pane-close.md | 714 | 超過 |
| .claude/skills/org-delegate/references/capability-first-drive-operational-gate.md | 599 | reference で 500 行を超えるのはこの 1 本だけ |
| .dispatcher/references/spawn-flow.md | 479 | 上限内 |

**基準 4: CLAUDE.md は 200 行未満が目安**（https://code.claude.com/docs/en/memory.md）

| ファイル | 行数 | 判定 |
|---|---|---|
| CLAUDE.md | 140（28,728 byte） | 上限内。ただし 1 行が非常に長く、:38 は 1 段落で約 1,500 字ある |
| .dispatcher/CLAUDE.md | 288（47,828 byte） | 超過（+88） |
| dispatcher の起動時合計（root 140 + .dispatcher 288 + pr-ci-watch rule 10） | 438 | 超過 |
| .curator/CLAUDE.md | 58 | 上限内 |
| tools/templates/worker_brief_normal.md | 232（テンプレート） / full 深度で描画した実物は 198 | テンプレートは超過。描画後は上限ぎりぎり |
| tools/templates/worker_brief_self_edit.md | 143。祖先の root CLAUDE.md を足すと約 280 | 単体は上限内。合計では実質超過 |
| org-delegate/references/worker-claude-template.md（brief の並行コピー） | 400。本体は 12-362 行で約 350 行 | 超過 |
| .claude/rules/pr-ci-watch.md | 10。paths 無し（基準 7） | 行数は問題なし。ただし全ロールが無条件に読み込む |

**基準 3: 100 行を超える参照ファイルに冒頭の目次があるか**（https://platform.claude.com/docs/en/agents-and-tools/agent-skills/best-practices.md）

`grep -lE '目次|Contents|^- \[.*\]\(#'` で調べ、下表のどれも 0 件だった。

| ファイル | 行数 | 目次 |
|---|---|---|
| .dispatcher/references/worker-monitoring.md | 1359 | なし。:3 に内容を列挙した 1 文があるだけで、アンカーは 4 個 |
| .dispatcher/references/pane-close.md | 714 | なし |
| org-delegate/references/capability-first-drive-operational-gate.md | 599 | なし。:3-5 に §1→§3 と §6 への言及があるだけ |
| org-setup/references/permissions.md | 496 | なし |
| .dispatcher/references/spawn-flow.md | 479 | なし |
| org-delegate/references/worker-claude-template.md | 400 | なし。brief のテンプレートなので、目次が要るかは判断が要る |
| org-delegate/references/renga-error-codes.md | 396 | なし |
| org-delegate/references/instruction-template.md | 233 | なし |
| org-delegate/references/delegate-flow-details.md | 198 | なし |
| org-delegate/references/claude-org-self-edit.md | 141 | なし |
| .dispatcher/references/loop-directive-guard.md | 109 | なし |
| org-delegate/references/pane-layout.md | 107 | なし |
| skill-audit/references/audit-checklist.md | 105 | なし |

**基準 12: description の長さと人称**（https://code.claude.com/docs/en/skills.md）

| 項目 | 実測 |
|---|---|
| 1,536 字を超えるもの | 0 個。when_to_use はどのスキルも使っていない |
| 長い順 | org-attach 582 / pr-watch-pane 511 / org-conveyor 396 / org-attention-start 360 / goal-setup 343 |
| 最短 | org-suspend 65 |
| 26 個の合計（起動時に毎回読まれる） | 約 6,518 字 |
| 内容の逸脱 | 実装詳細を書いているもの: pr-watch-pane, org-attach。方針・経緯・理由を書いているもの: org-delegate, org-curate, org-start, skill-audit。非公式キー `triggers:` があるもの: wsl-windows-chrome-screenshot。詳細は 5-2 |

**基準 13: disable-model-invocation**（https://code.claude.com/docs/en/skills.md / https://platform.claude.com/docs/en/agents-and-tools/agent-skills/best-practices.md）

| 項目 | 実測 |
|---|---|
| disable-model-invocation を持つスキル | 0 個（frontmatter の全キーを列挙して確認） |
| 扱い | 一律に付けるという提案は反証で除外した（末尾参照）。どのスキルに付けるかは 5-3 で判断事項として挙げる |

参考として、強調語の密度を `grep -o` で数えた（基準 9 の材料）。

| ファイル | 太字 `**…**` | 備考 |
|---|---|---|
| worker-monitoring.md | 1019 | :419 は 1 行に 20 個 |
| spawn-flow.md | 395 | :78 は 1 行に 19 個 |
| org-suspend SKILL.md | 274 | Phase 1 だけで 149 個 |
| capability-first-drive-operational-gate.md | 265 組 | MUST / NEVER / CRITICAL が 40 回 |
| renga-error-codes.md | 216 組 | 同 23 回 |
| pane-close.md | 191 | - |
| .dispatcher/CLAUDE.md | 115 | :21 は 1 行に 10 個 |
| worker_brief_normal.md | 83 | 「必ず」が 10 回 |

### 5-2. 基準ごとの逸脱一覧

| 基準 | 箇所 | 基準との差 | 直し方 | 出典 URL |
|---|---|---|---|---|
| 1 | .claude/skills/org-suspend/SKILL.md:1-791 | 791 行。Phase 1（64-380、317 行）の大半が契約 T-§4.2/T-§cap の解説と真理値表 | Group A/B・(F)・(G-OWNTAB)・真理値表の解説（98-180, 238-379）を references/peer-ledger.md へ移す。本文は「台帳を作る → 表で宛先を決める → peer_id/pane_id で撃つ」だけにする。.in 側で直す | https://code.claude.com/docs/en/skills.md |
| 1 | .claude/skills/pr-watch-pane/SKILL.md:1-591 | 591 行。Step 3-1（214-395）と前提（79-125）で backend 別の自己 close の分類を長く説明している | 分類を references/rationale.md / transport-duality.md へ寄せる。.in 側で直す | 同上 |
| 1 | .claude/skills/org-start/SKILL.md:1-543 | 543 行。設計メモ（263-348）、wall-clock 表（433-441）、herdr preflight（175-198）が本文にある | references/rationale.md を新設して移す。herdr preflight は 5-3 の判断次第で消す | 同上 |
| 2 | .dispatcher/CLAUDE.md:164-165 | 2026-08-18 の事故 2 件、「134 件滞留」「44.5 分停止（#955/#971）」の経緯が手順の本文にある | 「唯一の自動検出経路なので毎サイクル必須」の 1 句にし、経緯は外へ出す | 同上 |
| 2 | CLAUDE.md:38 | 約 1,500 字の段落。#941 で解消済みの第三フレームの経緯まで書いている | 削るか `<!-- -->` に入れる | 同上 |
| 2 | worker-monitoring.md:1339-1359 | 「設計メモ」と「cadence の根拠」。根拠に廃止済みの journal.jsonl の実測値（:1355）を使っている。廃止は .dispatcher/CLAUDE.md:102 に書かれている | docs/design へ移すか HTML コメントに入れる | 同上 |
| 2 | tools/templates/worker_brief_normal.md:149-179（self_edit.md:105-114 も同様） | Codex 節が約 30 行あり、理由の説明が描画後 brief の約 15% を占める | 判定式と、未成立時の報告文だけを残す。理由は knowledge/curated/codex.md へ移す | 同上 |
| 2 | worker_brief_normal.md:38-46 | preflight の各 bullet に事故の日付が付いている。:41 は約 700 字ある | 「症状 → 対処」の 1-2 文にする | 同上 |
| 2 | org-suspend/SKILL.md:98-167, 337-347, 745-763 | 約 110 行が経緯と理由。154-163 は本文自身が「当時の状態を説明する記録」と書いている | 規則を 1 行ずつ残す。154-163 は削除する | 同上 |
| 2 | org-start/SKILL.md:263-348 | 86 行。Block C2-C4 の設計メモと、スクリプトの内部仕様（PyPI JSON API、timeout 8s、PEP 610） | 「コマンド 1 行 + exit code 別の対応表」にし、メモは docstring へ移す | 同上 |
| 2 | pr-watch-pane/SKILL.md:79-125; org-pull-request/SKILL.md:115-174 | 107 行。backend 別の自己 close の分類と #751/#911 の経緯を 2 つのスキルに重ねて書いている | 本文は「list_panes で確認した数値 pane_id で close。[pane_not_found] は正常」だけにする | 同上 |
| 2 | renga-error-codes.md:5,22-24,55-60,70 | 120 行。版の経緯（1 行で約 1.3KB）、上流ソースの行番号、2026-08-07 の判断記録。ファイル全体は 65,039 byte | 表は「code / 扱い / 再試行の可否」にする。:70 は丸ごと移す | 同上 |
| 2 | pane-layout.md:38-45,57-68,104-107 | 30 行。旧 k-table の経緯、手動トレース表、将来機能。判定の SoT は runtime（:48） | 3 節を移すか削る | 同上 |
| 2 | instruction-template.md:166-172 | 事故の経緯（2026-05-03）が入っている。送信先も mcp__org-broker__ に固定されている（基準 14 にも当たる） | 経緯を外へ出し、ツール名は輸送層に依存しない表記にする | 同上 |
| 3 | worker-monitoring.md:1-11 / pane-close.md:1-6 / spawn-flow.md:1-17 | 目次が無い。worker-monitoring は本体が番号付きの太字段落（:102, :204, :227, :432, :573）で、見出しが無いので飛べない | Step 単位の目次を置き、Step 1-6 と 5.1-5.3 を `###` 見出しにする | https://platform.claude.com/docs/en/agents-and-tools/agent-skills/best-practices.md |
| 3 | skills の 100 行超 reference 9 本（2,675 行） | 目次が 0 件 | 冒頭に 5-15 行の目次を置く | 同上 |
| 3 | capability-first-drive-operational-gate.md | 5 スキルから読まれ、そこからさらに spawn-flow.md など 4 種の reference を参照している（計 20 か所）。1 階層を超える | §1/§2/§6 を自己完結にし、そこから先の参照を切る | 同上 |
| 3 | renga-error-codes.md:102-107 | 「先に Read して適用せよ（MUST）」で、SKILL → renga-error-codes → gate と 2 階層になる。ワーカー brief もこの経路をたどらせる | gate のツール呼び出し 1 行に置き換える | 同上 |
| 3 | instruction-template.md / pane-layout.md / dogfood-issue-template.md（419 行） | org-delegate/SKILL.md から直接リンクされていない。届くのは reference 経由だけ | SKILL.md に 1 行のリンクを足すか、使っていない経路なら削る | 同上 |
| 4 | .dispatcher/CLAUDE.md:1-288 | 288 行。起動時の合計は 438 行。:33-121（delegate-plan）、:212-257（curator）、:259-288（triage）は特定のメッセージを受けたときしか使わない | 3 つの節を spawn-flow.md / pane-close.md へ移す。目標は 120 行前後 | https://code.claude.com/docs/en/memory.md |
| 5 | .dispatcher/CLAUDE.md:58 / registry/org-config.md:40 | 本文自身が「今日は到達しない」と認めるフラグ経路を説明している | 削る。配線されたときに spawn-flow 3-1b に書く | https://code.claude.com/docs/en/best-practices.md |
| 5 | .dispatcher/CLAUDE.md:97 / :115 | jsonl 時代の念押しが残っている（jsonl の廃止は :102-103 に書かれている）。helper の内部実装の説明もある | 「journal は `bash ../tools/journal_append.sh` で書く」の 1 行にする | 同上 |
| 5 | org-delegate/SKILL.md:59-106 と CLAUDE.md:70-96 | 48 行。レーン選択と ultracode の条件がほぼ同文で二重にある | 正本を org-delegate に置き、CLAUDE.md はポインタ 1 行にする | 同上 |
| 5 | org-start/SKILL.md:35-43; org-suspend/SKILL.md:38-42 | 14 行。curator がオンデマンドになった旨の注記が重複し、#502 の状況まで書いている | 「curator_pane_id が null でも正常」の 1 行にする | 同上 |
| 5 | work-discovery/SKILL.md:48-70 と 249-256 | 31 行。INV-1〜5 と「やらないこと」が同じ内容 | 「やらないこと」を削り、allowed-tools の解説を 1 句にする | 同上 |
| 5 | instruction-template.md:4 と 72-94, 222 | :4 で「繰り返さない」と宣言しながら、Codex の規律を再掲している。生成元の brief を含めると 3 重 | 検証深度の節を数行に縮める | 同上 |
| 5 | claude-org-self-edit.md:46-60 | 15 行。run_complete_on_merge.py の判定式を実装レベルで説明している | 「2b-ii で自動回収される」の 1 行にする | 同上 |
| 6 | .dispatcher/CLAUDE.md:160-172 | 「要約」と称しているが、各 bullet が 1 段落規模の仕様になっている | 各 bullet を「コマンド 1 行 + 参照先」にする | https://code.claude.com/docs/en/memory.md |
| 6 | spawn-flow.md:63-91 / worker-monitoring.md:415-419 | 35 行。dogfood でしか効かない経路と、実装者や人間がやる作業が常用手順書に混ざっている | 別ファイルに出し、dogfood が指定されたときだけ読ませる | 同上 |
| 6 | CLAUDE.md:108-127 | 20 行。4 つの gate でしか使わない emit 手順。:127 は runtime 0.1.49 に追随した現状と合わない | 各スキルの該当 Step へ移す。:127 は削る | 同上 |
| 6 | CLAUDE.md:64-65 | handover のときにしか効かない打鍵規律 | dispatcher-handover スキルの窓口側の節へ移す | 同上 |
| 7 | .claude/rules/pr-ci-watch.md:1-3 | paths が無いので全ロールが無条件に読み込む。効くのは窓口だけで、ワーカーは PR を作れない（worker_brief_normal.md:103-104） | 窓口のスキルへ移すか 1-2 行に縮める | https://code.claude.com/docs/en/memory.md |
| 8 | CLAUDE.md:82 | block-foreground-subagent.sh で強制済み（.claude/settings.json:82）なのに長文で再説明している | 「Agent は常に背景で動く（hook が同期起動を deny する）」の 1 行にする | https://code.claude.com/docs/en/memory.md / https://code.claude.com/docs/en/best-practices.md |
| 8 | CLAUDE.md:41 / pr-ci-watch.md:5-10 / pr-watch-pane/SKILL.md:4-13, 31-35 | block-adhoc-pr-watch.sh（settings.json:90-111）で強制済みなのに、3 か所で重複している | 正本を rule 1 か所にし、ほかは 1 行にする | 同上 |
| 8 | CLAUDE.md:33 / .dispatcher/CLAUDE.md:79-80 / spawn-flow.md:7,131,219 / pane-close.md:446-447 | 「盲目 Enter 禁止」が 10 か所以上にある。send_keys を検査する hook は無い（`grep -rn send_keys .hooks/*.sh` は 0 件） | hook 化の候補にする（5-3）。文章は 1 か所に残す | 同上 |
| 8 | pane-close.md:9 / .dispatcher/CLAUDE.md:208 | 「振り返りが終わるまで閉じない」を強調しているが、hook は相対セレクタしか見ない。dispatcher は bypassPermissions で動く（block-dispatcher-out-of-scope.sh:5） | hook 化の候補にする。文章は 1 行にする | 同上 |
| 8 | worker_brief_normal.md:16, :45 / self_edit.md:14, :30 / worker-claude-template.md:24-29, 63, 106 | stash / checkout -- / restore --source は block-dangerous-git.sh で強制済みなのに、約 600 字（template:28 は 1,329 byte）で再説明している。hook の行番号まで載せている | 「hook が deny する。退避は一時 commit」の 2 行にし、行番号は消す | 同上 |
| 8 | org-suspend/SKILL.md:98-153, 615-627; org-start/SKILL.md:424-428; renga-error-codes.md:62-68; pane-layout.md:93-94 | close_pane の相対セレクタ禁止は block-relative-close-pane.sh で強制済み（同 hook :12-15） | 「hook が拒否する」の 1 行にする | 同上 |
| 8 | org-suspend/SKILL.md:120-123; org-start/SKILL.md:128-131 | set_pane_identity は同じ Group B なのに、hook の matcher が close_pane しか見ていない | matcher を `mcp__.*__(close_pane\|set_pane_identity)` に広げる候補にする | 同上 |
| 8 | work-discovery/SKILL.md:51-63; org-conveyor/SKILL.md:3-11 | 「merge 自動化はしない」などを強い言葉で書いているが hook が無い。work-discovery:55-61 自身が「強制したいなら hook」と書いている | hook 化の候補にする。しないなら平文に戻す | 同上 |
| 8 | worker-claude-template.md:64, 135-137, 145 | sandbox 外での再試行を 3 か所で念押ししているが、tools/org_extension_schema.json:601, 625 の allowUnsandboxedCommands:false で既に無効 | 1 行にまとめる | 同上 |
| 8 | worker-claude-template.md:253-279; instruction-template.md:83, 222 | 「空の合格」の判定が文章だけで、3 か所に重複している。tools/ に該当スクリプトは無い | tools/codex_review_gate.sh を作る候補にする | 同上 |
| 8 | worker-claude-template.md:308; instruction-template.md:90 | 「codex:rescue を使わない」が文章だけ（hook と schema を grep して 0 件） | Skill matcher の hook で止める候補にする | 同上 |
| 8 | brief preflight（worker-claude-template.md:55） | 「git add -A / . を使わない」が文章だけ（block-dangerous-git.sh を grep して 0 件） | hook に deny を足す候補にする | 同上 |
| 9 | CLAUDE.md:73-86 | 「迷わず従来のワーカーレーンに倒す」（:79）など強調が散らばっている | 条件を表にし、「1 つでも満たさなければワーカーレーン」を 1 回だけ書く | https://platform.claude.com/docs/en/build-with-claude/prompt-engineering/claude-prompting-best-practices.md / https://code.claude.com/docs/en/best-practices.md |
| 9 | CLAUDE.md:129-140 | 全段落に強調がある。中身は org-escalation と重複している | 強調を 1 行に絞り、手順はスキル側だけに置く | 同上 |
| 9 | worker-monitoring.md:85-1338 | 太字 1,019 個 | 定数名、終了コード、:1319 のような要所にだけ残す | 同上 |
| 9 | spawn-flow.md:63-90 / :78 | 1 行に太字 19 個 | 表にし、強調は「1 つでも欠けたら同一タブ経路」の 1 句だけにする | 同上 |
| 9 | worker_brief_normal.md:149-188; worker-claude-template.md:240-283 | Codex 節のほぼ全文が強調されている | 「成功数 1 以上・失敗数 0・exit 0 以外は clean と書かない」の 1 行に絞る | 同上 |
| 9 | org-suspend/SKILL.md:64-380 | 太字 149 個、強い語 31 個 | 1 行（pane_id は list_panes 由来の数値だけ）に絞る | 同上 |
| 9 | org-start/SKILL.md:33-198 | 太字 99 個。175-198 は 1 行あたり 1.08 個 | Step ごとに 1 行以内にする | 同上 |
| 9 | 生成物の輸送層ブロック（org-escalation:20-39, org-retro:16-31, skill-audit:18-34, org-attention-stop:30-42, org-curate:28-50） | 1 行あたり太字 0.96-1.46 個 | ブロックをポインタ 1 行に置き換える（選択肢 b）。生成器側で直す | 同上 |
| 9 | org-delegate/SKILL.md:73; work-discovery/SKILL.md:38, 48 | 「迷ったら重量レーン」、見出しに「（厳守）」「（破ってはならない）」 | 条件として書き、見出しのラベルを外す | 同上 |
| 9 | capability-first-drive-operational-gate.md:44-460 | 太字 265 組、MUST/NEVER 40 回。見出しにまでラベルが付いている | MUST は停止条件の 1 行に限る | 同上 |
| 9 | renga-error-codes.md:22-25, 62-68, 78-96, 141 | 太字 216 組、MUST 23 回 | 各 code の「再試行 / escalate」の 1 語だけ強調する | 同上 |
| 9 | claude-org-self-edit.md:1-141; instruction-template.md:222 | 太字 72 組。:222 は 1 行に 8 組以上 | 節ごとに 1 行に絞る | 同上 |
| 10 | CLAUDE.md:69-88 | 実証・実測の記録（:71, :82）と、約 1,000 字の理論（:88） | :88 は (1)-(3) の条件と kickoff 武装必須だけにする | https://platform.claude.com/docs/en/build-with-claude/prompt-engineering/claude-prompting-best-practices.md |
| 10 | worker-monitoring.md:1163-1166 ほか | Issue / Refs 番号 109 件、日付 37 件。定数ごとに「根拠」の段落がある | 根拠は 1 句にし、経緯は設計メモへまとめる | 同上 |
| 10 | dispatcher-resume/SKILL.md:59-130; wsl-windows-chrome-screenshot/SKILL.md:20-38, 168-176 | 経緯（:62-66）、日付付きの事故（:30）、出典 9 件の列挙 | rationale.md か HTML コメントへ移す | 同上 |
| 10 | worker-claude-template.md:137, 143, 145, 161, 173, 187 | 節末尾に日付付きの経緯が続く | 理由は 1 文にする | 同上 |
| 10 | claude-org-self-edit.md:120-125 | 「なぜ 1 コマンドか」の理由説明。分割はツールが処理している | 「手で分割して打たない」の 1 文にする | 同上 |
| 11 | CLAUDE.md:28-38 / .dispatcher/CLAUDE.md:21 / spawn-flow.md:5-10 / pane-close.md:5 / worker-monitoring.md:5-9 | 「Epic #6 Issue G まで未活性」と runtime の版番号が 5 ファイルにある（`grep -n 'Issue G'` で 6 件） | 1 ファイルに集約して版と経緯を外す。各手順書には 1 行だけ置く | https://platform.claude.com/docs/en/agents-and-tools/agent-skills/best-practices.md |
| 11 | .dispatcher/CLAUDE.md:164, :166-170 | GATE_EPOCH などの helper 定数を入口で再掲している | 「判定は helper の出力に従う」とだけ書く | 同上 |
| 11 | org-start/SKILL.md:175-198 | herdr 0.7 系の protocol 写像（:194）。backend==herdr のときだけ発動する（:179） | 5-3 の判断で削除するかを決める | 同上 |
| 11 | org-start/SKILL.md:33-98; org-suspend/SKILL.md:56-62 ほか | 版番号らしき文字列が約 24 か所。「Epic #6 Issue G まで未活性」が 13 スキル・計 14 か所 | 版の下限は tools/check_*_compat.py に任せ、但し書きは一括で削る | 同上 |
| 11 | org-start/SKILL.md:433-441 | wall-clock の変化表（経緯） | 削るか rationale へ移す | 同上 |
| 11 | work-discovery/SKILL.md:246 | オペレーター固有の絶対パスが書かれている | `git rev-parse --show-toplevel` に書き換える | 同上 |
| 11 | renga-error-codes.md:3, 5; capability-first-drive-operational-gate.md:25-27 | 「renga 0.14.0+（1.x）」を前提にしている。tools/check_renga_compat.py:113 の下限は (2,0,0) | 前提を 2.0 系に書き直し、1.x の注記を消す | 同上 |
| 11 | instruction-template.md:222 と worker-claude-template.md:203 | 「約 2 倍速」を、前者は gpt-5.5 世代の実測という但し書き付き、後者は断定で書いている | 数値は knowledge/curated/codex.md に置く | 同上 |
| 11 | pane-layout.md:18, 20; worker-claude-template.md:318, 332; renga-error-codes.md:172 | 0.1.24+ が 8 か所、0.1.28 が 7 か所など。requirements.txt:181 は >=0.1.49 なので、どの分岐も常に満たされる | 版の条件を消す | 同上 |
| 12 | pr-watch-pane/SKILL.md:4-13; org-attach/SKILL.md:3-14 | 511 字と 582 字。backend 別の挙動や突き合わせ方式を description に書いている | 「何をするか 1 文 + トリガ語」にする（目安 200 字以内） | https://code.claude.com/docs/en/skills.md |
| 12 | org-delegate/SKILL.md:3-7; org-curate:7; org-start:7; skill-audit:8 | 方針、経緯（時間依存）、理由が description に入っている | 本文へ移す。.in 側で直す | 同上 |
| 12 | wsl-windows-chrome-screenshot/SKILL.md:12 | 非公式キー `triggers:`。推測: Claude Code はこのキーを読まないので、トリガ語は一覧に出ない | トリガ語を description に統合し、キーを削る | 同上 |
| 14 | .dispatcher/CLAUDE.md:19-21 と worker-monitoring.md:5-9 | 入口は「既定 renga」、監視手順は「既定 broker」と書く。org-broker の出現数は worker-monitoring で 39 件、spawn-flow で 4 件 | renga 基準に揃え、注記を 1 か所に集約する | https://code.claude.com/docs/en/memory.md |
| 14 | CLAUDE.md:32 と pane-close.md:5 | push 一次（ratified 済み 2026-06-15）と、古い pull モデルの記述が並ぶ | pane-close.md:5 を参照 1 行にする | 同上 |
| 14 | .dispatcher/CLAUDE.md:71 と org-start/SKILL.md:531-538 | 「sonnet だと分類器が不安定」と「分類器は専用モデルで動く」が食い違う | 理由句を削り、「既定 opus。指定があれば従う」にする | 同上 |
| 14 | CLAUDE.md:1-3 と .dispatcher/CLAUDE.md:1-3（self_edit.md:3 が傍証） | dispatcher が窓口の役割宣言とディスパッチャーの役割宣言を同時に読む。打ち消し文も無い | root を役割共通の数行にする。少なくとも .dispatcher 側に打ち消し文を入れる | 同上 |
| 14 | 生成物 9 個と org-attach:61 が「既定 broker」、CLAUDE.md:28-30, org-start:89, 102 ほかが「既定 renga」 | 「ORG_TRANSPORT 無設定」の解釈が逆になっている | 「既定」の語を使わず 1 か所で決める（選択肢 b）。生成器（tools/gen_skill_prose.py）で直す | 同上 |
| 14 | 生成物の allowed-tools（org-suspend:20-31, org-delegate:23-29, org-conveyor:27-29）と CLAUDE.md:28-30 | broker 系だけを事前承認していて、運用の renga 系は未承認。手で保守する org-start:30 は両系を列挙している | 運用の面に合わせて列挙対象を決め直す | 同上 |
| 14 | org-delegate/SKILL.md:76 と CLAUDE.md:82 | `run_in_background=true` 必須と、そのパラメータは廃止済み、が食い違う | :76 を「常時背景。追加指定は不要」にする | 同上 |
| 14 | skill-audit/SKILL.md:6 と :41-49, org-retro:99-100 | description が数える先の公開ファイルは常に空なので、条件が成立しない | 「.md / .local.md の合計」に直す | 同上 |
| 14 | 手で保守する 4 か所の「既定 renga」と、生成された 6 本の「既定 broker」（transport-duality.md 3 本ほか）。コードは tools/transport.py:19-20 で DEFAULT_TRANSPORT=broker | 同じ reference 群の中で既定が逆になっている | 1 か所に集約する。表記はオーナーが決める | 同上 |
| 14 | worker-claude-template.md:221, 229 と worker_brief_normal.md:124-125, 139 | 生成に使われていない並行コピー（生成器は tools/gen_worker_brief.py:415）で、CODEX_SRC とログ名が既にずれている | 本体 12-362 行を削り、生成元へのポインタにする | 同上 |
| 14 | claude-org-self-edit.md:37-39 | template の流用を指示しているが、生成器が既に self_edit.md から生成している | 「gen_delegate_payload.py --role claude-org-self-edit が生成する」の 1 行にする | 同上 |
| 14 | org-setup/references/permissions.md:3-8 と org-setup/SKILL.md:107, 127 | 正典を、前者は schema、後者は permissions.md としている | 正典を schema に一本化する | 同上 |
| 15 | .dispatcher/CLAUDE.md:15-17, :130 / CLAUDE.md:98 | 「背景:」「違反事例」「事故事例参照」など、保守者向けのメモが本文に混ざっている | `<!-- -->` に入れるか削る | https://code.claude.com/docs/en/memory.md |

### 5-3. 既存の分類（keep/move/delete/outdated）との関係

- **基準 2・10 は move の根拠を補強する。** 経緯・理由・日付つきの事故記録を手順の本文から外すべきだという論拠に、公式の出典（skills.md「State what to do rather than narrating how or why」、prompting best practices「Add context」）が加わる。5-2 の基準 2・10 の行はどれも「rationale / docs/design / knowledge/curated へ移す」で終わり、move の定義と一致する。
- **基準 5 は delete の根拠を補強する。** 削除テストで No になるもの（到達しない経路 .dispatcher/CLAUDE.md:58、廃止済み jsonl の念押し :97、コードを読めば分かる helper の内部 :115、claude-org-self-edit.md:46-60）と、同文の重複（CLAUDE.md:70-96 と org-delegate:59-106、work-discovery:48-70 と 249-256 など）は、delete の「重複」「存在しない運用への言及」と一致する。
- **基準 15 は move の受け皿を 1 つ増やす。** CLAUDE.md の中にある人間向けメモは、削らずに `<!-- -->` に入れれば文脈から外せる。ただし SKILL.md と reference には効かない（除外理由を参照）。
- **基準 8 による hook 化の候補**（文章を削る前に、強制手段があるかを決める）:
  1. spawn 直後の send_keys(enter) を、spawn-prompt-step の判定記録が無ければ deny する
  2. retro gate の ack 記録が無い worker への close_pane を deny する
  3. block-relative-close-pane.sh の matcher に set_pane_identity を足す
  4. 窓口ロールの `gh pr merge` を deny する（org-conveyor / work-discovery の「merge 自動化しない」）
  5. tools/codex_review_gate.sh を作り、空の合格の判定を機械化する
  6. Skill matcher で codex:rescue を deny する
  7. block-dangerous-git.sh に `git add -A / . / --all` の deny を足す

  これと別に、既に hook や設定で強制済みのもの（block-foreground-subagent / block-adhoc-pr-watch / block-dangerous-git / block-relative-close-pane / allowUnsandboxedCommands:false）は、文章側を 1 行に縮めてよい。これらは keep から delete（説明部分）への格下げ候補になる。
- **基準 14 と §4-D の重なり**:
  - 重複しているのは 2 件。.dispatcher/CLAUDE.md:71 のモデル既定の理由は、§4-D では spawn-flow.md:112 と、本節では org-start:531-538 と矛盾する。根拠が 2 系統あるので outdated が確定的になる。org-delegate:76 の run_in_background は §4-D の C9 と同じ件で、§4-D の「正本を直さずに CLAUDE.md 側を消すと古い記述だけが残る」という注意がそのまま当てはまる。
  - 本節で新しく出た矛盾は次のとおりで、§4-D に足すべきもの。
    - dispatcher の入口と worker-monitoring の既定が逆
    - CLAUDE.md:32 と pane-close.md:5 の受信モデル
    - root と .dispatcher の役割宣言の二重化
    - 「既定 broker」と「既定 renga」の生成物・手保守間の対立、および allowed-tools の列挙対象
    - skill-audit の description
    - worker-claude-template.md と生成元 brief のずれ
    - claude-org-self-edit.md:37-39
    - permissions.md と org-setup SKILL.md の正典
    - instruction-template.md:166-172 のツール名
  - このうち輸送層の 4 件（入口と監視手順、受信モデル、生成物と手保守、reference 群）は、選択肢 a/b/c の判断が出れば一括で解ける。それ以外は個別に直す必要がある。
- **新たに判断が要る点**:
  - 「既定」の表記をどちらに揃えるか。コードは tools/transport.py:19-20 の DEFAULT_TRANSPORT=broker、運用はオーナーの 2026-10-12 判断で renga。あわせて、生成物の allowed-tools をどちらの面で列挙するか。
  - disable-model-invocation をどのスキルに付けるか。一律に付けると、自然言語で発動する設計（「起動して」「今日は終わり」など）が壊れる。org-down / org-setup / org-conveyor のように個別に選ぶか、付けないままにするか。
  - root CLAUDE.md をどう扱うか。窓口専用の内容をスキルや rule へ移して root を共通の数行にするか、.dispatcher/CLAUDE.md に打ち消し文を入れるだけにするか。
  - worker-claude-template.md の本体（12-362 行）を削除し、生成元の tools/templates/worker_brief_*.md を唯一の正本とするか。
  - 基準 4 の測り方。描画後の brief（198 行）で測るか、テンプレート（232 行）で測るか。self-edit ワーカーの合計約 280 行を超過として扱うか。
  - 5-3 の hook 化候補 7 件のうち、どれを実装するか。実装しないものは、強調を外して平文にする。
  - org-start Step 0.5（herdr preflight、24 行）と tools/check_herdr_compat.py を削除するか。herdr 移行見送りの repo 内の出典は確認していない。
  - pr-ci-watch rule の行き先。窓口のスキルへ移すか、hook が強制しているので 1-2 行に縮めるか。
  - .dispatcher/references の 500 行超 2 本（worker-monitoring 1359 行、pane-close 714 行）を分割するか、目次と見出しの追加で済ませるか。
  - wsl-windows-chrome-screenshot の `triggers:` キーを削除するか。
- **基準 16**: `/doctor prompt-audit [path]`（v2.1.283 以降、https://code.claude.com/docs/en/memory.md 「Audit your instruction files」）は、窓口が後で回す。本節では実行していない。

### 反証で除外した指摘 9 件

1. [基準 8] CLAUDE.md:84, :96 の push / PR / merge の規律を hook 化する案。窓口は push を正規に担うロールで（org-pull-request SKILL.md:13, :53, :91）、subagent は窓口と同じ settings を使う。配線すると正規の push まで止まる。
2. [基準 8・14] .dispatcher/CLAUDE.md:132-144 の from_id 再送と、worker brief との矛盾。再送先は直近に受信したメッセージの from_id で、送り主として身元が確立している。brief:226 とは条件が違い、両立する。
3. [基準 9] worker_brief_normal.md:199 / self_edit.md:129 の「一切禁止」。minimal 節の中の 1 か所だけの強調で、基準 9 が許す「効かせたい 1 行」に当たりうる。過剰反応は推測にとどまる。
4. [基準 14] normal と self_edit で exclude 登録の扱いが違う件。self_edit は ja の worktree root にキャラクタデバイスがあるという条件の違いによるもので（self_edit.md:14-15、block-dangerous-git.sh:41-44）、結論は両方とも `git add -u` と明示 add で一致している。
5. [基準 13] 7 スキルに disable-model-invocation を一律に付ける案。自然言語で発動する設計が壊れるので誤り。5-3 で判断事項として扱う。
6. [基準 15] org-suspend:154-163、org-start:41-43, 433-441 の HTML コメント化。HTML コメントの除去は CLAUDE.md を読み込むときの仕様で、SKILL.md でも除去されるかは同じページからは言えない。論旨自体は基準 2・11 の指摘と重複する。
7. [基準 2] capability-first gate を「発火しないゲートのために 56KB 読ませている」とする指摘。前提が古い。下限 renga 2.0（tools/check_renga_compat.py:113）の list_peers は tab 情報を返すので、ゲートは発火しうる。規模の問題は基準 1・3 の指摘で拾っている。
8. [基準 11] Codex ラウンド判定の 3 分類を「選択肢の出しすぎ」とする指摘。3 分類は判定規則で、既定を示さずに手段を並べるアンチパターンには当たらない。重複は基準 5 の instruction-template の指摘で拾っている。
9. [基準 15] instruction-template:233 / renga-error-codes:70 / permissions.md:3-8 / pane-layout:20 の HTML コメント化。これらは Read で読まれる reference なので、`<!-- -->` にしても文脈から外れない。:233 は編集するときに必要な指示でもある。
## 付録 A. 起動時読込の測定（原文）

### 起動時に毎回読み込まれる量の測定（ja-main 31a5c40）

対象は `ja-main`（31a5c40、`git -C … log -1` で確認）。読み取りのみで、ファイルは作成も編集もしていない。

#### 推定方法
- **行数・文字数:** `wc -l -m` で実測した。ASCII と非 ASCII の内訳は python3 で文字コード `<0x80` かどうかを数えて分けた。
- **トークン:** tokenizer が無いので、**ASCII 文字数 / 4 ＋ 非 ASCII 文字数 × 0.9〜1.1** で推定した。値はすべて推定である。
  - パスやコード片は 1 トークンあたり約 3 文字で切れやすいので、ASCII 部分は下振れしている可能性がある。

#### 0. 対象全体（61 ファイル、`.in` は除く）
`find CLAUDE.md .dispatcher .claude/rules .claude/skills -name '*.md' | xargs wc -l -m` で数えた結果、61 ファイルあった。

| 行数 | 文字数 | ASCII | 非 ASCII | 推定トークン |
|---|---|---|---|---|
| 13,775 | 992,223 | 673,215 | 319,008 | 約 455k〜519k |

- 生成元の `.in` は 19 ファイルあり、上の合計には含めていない。
- 範囲外だが、参考として 3 ファイルも測った。
  - `.curator/CLAUDE.md`: 58 行 / 1,990 字
  - `tools/templates/worker_brief_normal.md`: 232 行 / 17,622 字
  - `tools/templates/worker_brief_self_edit.md`: 143 行 / 10,227 字

#### 1. 自動で読み込まれる仕組み（根拠）

| 仕組み | 事実と出典 |
|---|---|
| CLAUDE.md の祖先探索 | Claude Code は cwd から上の階層へ CLAUDE.md を読む（公式仕様）。repo 内にこの挙動を書いた記述は無い（`git grep '祖先\|ancestor CLAUDE'` は 0 件）。<br>確認した範囲: `/home/happy_ryo/{,work/,work/org/}CLAUDE.md` はどれも存在しない（ls の結果）。`~/.claude/CLAUDE.md` は全役割に載る（5 行 / 182 字）。 |
| `.claude/rules/*.md` | ファイルは `pr-ci-watch.md` の 1 つだけ（10 行 / 628 字）。frontmatter は `description` だけで `paths:` が無い（`.claude/rules/pr-ci-watch.md:1-3`）ので、常に読み込まれる。<br>Producer は `.hooks/block-adhoc-pr-watch.sh:8,43` と `tools/watcher_restart_guard.py:295`。 |
| スキル一覧（description） | `.claude/skills/*/SKILL.md` が 26 個ある。description の合計は 6,659 字（ASCII 3,149 / 非 ASCII 3,510）で、**約 3.9k〜4.6k トークン**。<br>`disable-model-invocation` を付けたスキルは 0 件なので、26 個すべてが一覧に載る。`owner:` キー（10 スキル）は Claude Code の絞り込みに使われないので、owner が誰でも全役割に見える。<br>dispatcher の cwd `.dispatcher/` から上位の `.claude/skills` が見えている根拠: 窓口が dispatcher ペインに `/dispatcher-resume` を打鍵する運用がある（`CLAUDE.md:65`）。 |
| SessionStart 等による context 注入 | 無い。`.claude/settings.json` の hooks は PreToolUse だけで、`tools/org_extension_schema.json` にも SessionStart / UserPromptSubmit の定義は無い。 |

#### 2. 役割ごとの読み込み量

##### 窓口（secretary、cwd = ja root）
起動経路: `renga-layouts/ops.toml:29-33` が素の `claude` を起動する。続いて `CLAUDE.md:17` がユーザーに `/org-start` を促す。

| 区分 | ファイル | 行 | 文字 | 推定トークン |
|---|---|---|---|---|
| 自動（毎回） | `CLAUDE.md` | 140 | 16,687 | 8.1k〜9.3k |
| 自動（毎回） | `.claude/rules/pr-ci-watch.md` | 10 | 628 | 0.3k |
| 自動（毎回） | スキル description 26 個 | – | 6,659 | 3.9k〜4.6k |
| **自動 小計** | | **150** | **23,974** | **12.3k〜14.3k** |
| 起動手順で必須（`/org-start`） | `.claude/skills/org-start/SKILL.md` | 543 | 49,640 | 22.6k〜25.8k |
| 起動手順で必須 | `capability-first-drive-operational-gate.md`（`org-start/SKILL.md:369` の Block D-2 が無条件で Read を指示） | 599 | 32,903 | 15.8k〜18.2k |
| **起動ごとの合計** | | **1,292** | **106,517** | **50.8k〜58.2k** |
| 条件付き | `pane-layout.md`（`org-start:207` の「従う」） | 107 | 16,868 | 7.3k〜8.2k |
| 条件付き | `org-resume/SKILL.md`（session が SUSPENDED のとき。`org-start:239-240`） | 118 | 4,623 | 1.8k〜2.0k |
| 条件付き（1 タスク 1 周） | `org-delegate` 385 ＋ `ack-template` 78（`CLAUDE.md:102`）＋ `org-pull-request` 345 ＋ `pr-watch-pane` 591（`CLAUDE.md:41`） | 1,399 | 104,518 | 47.1k〜53.6k |
| 条件付き | `org-escalation` 94 / `renga-error-codes` 396 / `secretary-resume` 173 | 663 | 54,638 | 約 25k〜28k |

##### 振り分け役（dispatcher、cwd = `.dispatcher/`）
起動経路: `org-start/SKILL.md:209-219` が `spawn_claude_pane(cwd=".dispatcher", model="sonnet")` で起動する。

| 区分 | ファイル | 行 | 文字 | 推定トークン |
|---|---|---|---|---|
| 自動（毎回） | `.dispatcher/CLAUDE.md` | 288 | 29,940 | 13.3k〜15.1k |
| 自動（推測） | 親ディレクトリの `CLAUDE.md`（窓口用。祖先探索による） | 140 | 16,687 | 8.1k〜9.3k |
| 自動（推測） | rules（10 行）＋ スキル description 26 個 | 10 | 7,287 | 4.2k〜5.0k |
| **自動 小計** | | **438** | **53,914** | **25.6k〜29.4k** |
| DELEGATE 受信時に必読（`.dispatcher/CLAUDE.md:25-31`「必ず読むこと」） | `spawn-flow.md` 479 / `pane-layout.md` 107 / `instruction-template.md` 233 / `org-start` Appendix 500-543（44 行）/ `renga-error-codes.md` 396 | 1,259 | 144,808 | 67.5k〜77.2k |
| spawn ごとに必読（`.dispatcher/CLAUDE.md:81`、`list_peers` の直前） | `capability-first-drive-operational-gate.md` | 599 | 32,903 | 15.8k〜18.2k |
| 監視 `/loop 3m` の各サイクル（`dispatcher-resume/SKILL.md:356` の指示文が `worker-monitoring.md` の「監視ループ 1 サイクル」を実行させる） | `worker-monitoring.md`（当該節は 85-1338 行。1,254 行 / 167,770 字 / 76.7k〜87.3k） | 1,359 | 179,539 | 82.7k〜94.3k |
| **ワーカー 1 人派遣時の合計** | | **3,655** | **411,164** | **約 192k〜219k** |
| 条件付き | `dispatcher-resume` 436 / `dispatcher-handover` 218 / `pane-close.md` 714（CLOSE_PANE 受信時）/ `loop-directive-guard.md` 109 | 1,477 | 89,713 | 約 41k〜47k |

- **重要（推定）:** ワーカーを 1 人派遣した時点で、dispatcher の読み込み量は約 19〜22 万トークンになる。sonnet の 200k context window とほぼ同じか、超える。
- Read ツールが offset 指定で部分的に読んでいる可能性はある（推測）。それでも `worker-monitoring.md` 単体で全体の約 4 割を占める。

##### 作業者（worker、cwd = `{workers_dir}/<project>`）

**事実訂正:** brief の生成元は `worker-claude-template.md` ではない。
- `tools/gen_worker_brief.py:414-417` が読むのは `tools/templates/worker_brief_normal.md`（自己編集タスクでは `worker_brief_self_edit.md`）である。
- `worker-claude-template.md` は契約文書から参照されている（`docs/contracts/role-contract.md:258` ほか）が、ワーカーが実際に読むことは無い。
- 両者はすでに食い違っている。
  - #1019（176e70a、2026-09-21）は `worker-claude-template.md:59` に「Codex レビューを走らせる Bash 呼び出しは `allowed_domains`…」を追加した。この文言は `tools/templates/worker_brief_*.md` にも現ワーカーの CLAUDE.md にも無い（`grep -F` で 0 件）。
  - 逆に afcf6f0（2026-10-08）は `tools/templates` 側だけを変更している。
  - 2 つを同期させるテストは無い（`git grep worker-claude-template -- tests` は 0 件）。

| 区分 | 内容 | 行 | 文字 | 推定トークン |
|---|---|---|---|---|
| 自動（毎回） | brief `CLAUDE.md`（実例: `/home/happy_ryo/work/org/workers/claude-org-ja/CLAUDE.md`。ASCII 10,229 / 非 ASCII 7,173） | 198 | 17,402 | 9.0k〜10.4k |
| 毎回（初回メッセージ） | dispatcher が送る指示（`instruction-template.md:181-233` の AUTO テンプレートを上限とみなす） | 約 35〜53 | 約 4,600 以下 | 約 2.4k〜2.8k |
| 自動 | 対象プロジェクト自身の CLAUDE.md / rules / skills | – | 未測定（プロジェクトによる） | – |
| **通常ワーカーの小計** | | **約 233〜251** | **約 22,000** | **約 11.4k〜13.2k** |
| ja 自己編集ワーカー（Pattern B）の場合 | `CLAUDE.local.md`（テンプレート 143 行 / 10,227 字、変数展開前）＋ worktree の `CLAUDE.md` 140 行（窓口用、`claude-org-self-edit.md:8,33`）＋ live repo の親 `CLAUDE.md` 140 行（推測。worktree は `{claude_org_path}/.worktrees/` の下にある、同 `:50`）＋ rules 10 行 ＋ description 26 個 ＋ 指示メッセージ | 約 486 | 約 55,500 | 約 27.6k〜31.8k |
| 条件付き | `renga-error-codes.md` の `pane_not_found` 節（brief の完了報告手順から参照） | 396（全体） | 39,282 | 18.2k〜20.8k |

参考までに curator（`.curator/CLAUDE.md` 58 行 / 1,990 字）の自動読み込みは、祖先の `CLAUDE.md` 140 行と description 26 個を足して約 0.9k＋8.1k＋3.9k トークンになる（推測）。

#### 3. 統合段で使ってほしい所見
1. **dispatcher が最も重い。** 約 19〜22 万トークンで、内訳は `worker-monitoring.md` 1,359 行と、DELEGATE 時の必読 5 ファイル 1,259 行が大半を占める。削減効果は全役割の中でここが最も大きい。
2. **窓口の起動時、capability ゲート（599 行）が無条件で読まれている。** `org-start:369` が Read を指示している。同じゲート文書の中で「現行 `org-broker` は capability を 1 つも広告しない」と書かれており（`org-start:147`）、この分岐が実際に効く場面は限られる。delete / move を判断する材料になる。
3. **`worker-claude-template.md`（400 行）はワーカーに届いていない。** 実際に読まれるのは `tools/templates/worker_brief_*.md` で、内容もすでに食い違っている。正本をどちらにするかは人が決める必要がある。
4. **窓口用 `CLAUDE.md`（140 行）が他の役割にも読まれている可能性がある**（祖先探索、推測）。dispatcher、curator、ja 自己編集ワーカーが対象で、自己編集ワーカーでは 2 回読まれる見込み。役割の取り違えの原因にもなる（`claude-org-self-edit.md:8`）。実測で確かめたい場合は、dispatcher で `/memory` を実行すると読み込まれたファイルを確認できる。
5. **スキル description 26 個（約 4k トークン）は全役割に載る。** `owner:` で役割を分けたつもりでも、context は減っていない。

## 付録 B. 輸送層二重記述の選択肢評価（原文。前段の a/b/c 3 案版。計画書の §輸送層 で置き換え済み、参考として残す）

### 輸送層の二重記述: 選択肢評価（ja-main 31a5c40、読み取りのみ）

**結論: b 案を推奨します。** 理由は 2 つあります。共通の注記ファイルへ寄せる仕組みは、生成器のフラグメントと、3 スキルで既に入っている移設パターンとして揃っています。また、a 案はオーナーの 2026-10-12 判断（renga **または tmux**）の片方、tmux の経路を文書から閉じてしまいます。追加で b+ として、生成物を renga 面で render し直すことを勧めます。

#### 1. 洗い出し（コマンドと結果）

**集計コマンド:**
```
git -C $R ls-files CLAUDE.md '.claude/rules/*.md' '.claude/skills/**/*.md' .dispatcher/CLAUDE.md '.dispatcher/references/*.md'
を 1 ファイルずつ回して
  grep -cE 'ORG_TRANSPORT|二フレーム|Issue G|transport-duality|既定 `broker`|opt-in `renga`|org-broker-channel|broker 固有|broker 節|broker 拡張|両系'   (= dual)
  grep -c 'mcp__org-broker__'   (= brokerFQ。本文・frontmatter のツール名)
注記ブロックの範囲は「輸送層…両系」で始まる引用ブロックを awk で切り出し、目視で補正した
```

- 合計: 二重記述の語を含む行は **205 行 / 151,751 バイト**（対象 39 ファイル中 36 ファイル）。
- `.claude/rules/pr-ci-watch.md` は 0 件。
- 1 行が段落 1 つ分と長いので、行数より**バイト数のほうが実際の量に近い**。

| ファイル（gen = `.md.in` から生成。編集は .in かフラグメント側） | 注記ブロック 行範囲 | ブロック行数 | dual 行 | brokerFQ |
|---|---|---|---|---|
| CLAUDE.md（窓口が起動時に読む） | 28-39 | 12 | 6 | 2 |
| .dispatcher/CLAUDE.md（振り分け役が起動時に読む） | 19-22（＋44, 50, 57, 170 の本文） | 4 | 6 | 1 |
| .dispatcher/references/spawn-flow.md | 5-17（＋38, 46, 72, 240, 278, 284） | 13 | 10 | 4 |
| .dispatcher/references/worker-monitoring.md (gen) | 5-10（＋140, 323, 807, 1119, 1142, 1156） | 6 | 10 | 39 |
| .dispatcher/references/pane-close.md | 5-6 | 2 | 1 | 1 |
| org-start/SKILL.md（窓口が起動時に実行） | 89-98（＋30, 101-103, 147, 179, 295, 367） | 10 | 13 | 10 |
| org-attach/SKILL.md | 33-66 | 34 | 15 | 5 |
| pr-watch-pane/SKILL.md (gen) | 55-77（＋本文 20 箇所） | 23 | 25 | 32 |
| org-conveyor/SKILL.md | 83-95 | 13 | 5 | 3 |
| dispatcher-handover / org-curate / org-retro / org-escalation / skill-audit / org-attention-stop / ack-template / instruction-template (いずれも gen、長いヘッダ) | 各 7-8 行（例: dispatcher-handover 32-38） | 計 61 | 各 5 | 3〜17 |
| dispatcher-resume / secretary-resume / org-suspend (gen、短いヘッダ) | 51-58 / 39-46 / 56-63 | 各 8 | 6 | 21 / 11 / 50 |
| org-delegate/references/pane-layout.md (gen) | 6-13（＋18, 30, 34, 50, 79） | 8 | 10 | 12 |
| org-delegate/references/capability-first-drive-operational-gate.md | 22-28 | 7 | 3 | 1 |
| org-delegate/references/worker-claude-template.md（作業者 brief に展開される） | 318-320, 332 | 4 | 2 | 2 |
| org-delegate/references/renga-error-codes.md | 145-152（broker との関係）, 153-173（broker 追加コード節） | 29 | 6 | 2 |
| transport-duality.md ×3（org-delegate / org-pull-request / pr-watch-pane、gen） | 全文 | 11 / 7 / 11 | 7 / 4 / 7 | 3 / 2 / 2 |
| org-delegate/SKILL.md, org-pull-request/SKILL.md (gen) | 44-45 / 47-48（参照だけの 2 行。b 案の形が既にある） | 2 / 2 | 1 / 4 | 12 / 15 |
| org-setup, claude-org-self-edit.md, pr-watch-pane/references/rationale.md | 26-27 / 110-111 / 49-50 | 各 2 | 1 / 1 / 4 | 1 / 1 / 0 |
| org-attention-start/SKILL.md | 31（**見出しだけで中身が無い**） | 1 | 1 | 1 |
| org-down, work-discovery, org-pull-request/references/rationale.md, runtime-release-with-paired-ja-sync, dynamic-ports.md | 本文中に散在 | – | 1〜3 | 0〜9 |

**注記ブロックの合計:**
- スキル本体などに置かれた注記: 233 行
- transport-duality.md ×3: 29 行
- renga-error-codes.md の broker 節: 29 行
- 合計 **約 291 行**

ブロックの外で本文に散らばる「broker（`ORG_TRANSPORT=broker`）の場合…」の分岐は、概算で 70〜80 行あります（205 行のうちブロック外の分。推測を含む）。

**起動時に毎回読まれる分:**

| 役割 | 読むもの | 量 |
|---|---|---|
| 窓口 | CLAUDE.md | 12 行 / 4.1KB |
| 窓口 | org-start | 10 行 / 3.4KB ＋ 散在 4 行 |
| 振り分け役 | .dispatcher/CLAUDE.md | 4 行 ＋ 散在 4 行（関係する行の合計 4.7KB） |
| 作業者 | brief | 4 行 / 約 2.4KB |

トークン換算は 1 トークン ≈ 3〜4.5 バイトとした**推測**です。全ファイル合計の 151,751B は約 34k〜50k トークン、窓口が起動時に読む分は約 2k〜2.5k トークンになります。

#### 2. 判断材料として確認した事実

**表記が 2 つの枠で食い違っている:**
- 生成スキル（manifest.json の 19 出力）は**コード既定（broker）の面**で render されています。本文は `mcp__org-broker__*`、ヘッダは「既定 `broker` / opt-in `renga`」です（`tools/gen_skill_prose.py:12-13`。drift ゲートは `tools/test_gen_skill_prose.py:625` で `--transport DEFAULT_TRANSPORT` を使う）。
- 手で保守しているファイルは**運用既定（renga）の枠**です。CLAUDE.md:28、.dispatcher/CLAUDE.md:19、org-start:89、worker-claude-template.md:318、renga-error-codes.md:155 が「既定 `renga` / opt-in `broker`」と書いています。
- 窓口は同じ起動の流れの中で両方を読みます。この食い違いを説明するためだけに「二フレーム注記（Refs #604）」が全体に入っています。
- 生成スキルの frontmatter `allowed-tools` は broker のツールだけです（dispatcher-resume:23-28、org-down:24-31 など）。renga のツールは `.claude/settings.json:19-28` が許可しています。

**実際の運用は renga:**
- この作業者の環境は `ORG_TRANSPORT=renga`、`RENGA_SOCKET` 設定済み、`RENGA_PANE_ID=232` です（`echo` で確認）。
- コード上の既定は broker です（`tools/transport.py`。runtime の `DEFAULT_TRANSPORT` を再公開しているだけで、ja 側では変えられない。`scripts/install.sh:281,283`）。

**outdated（現在の事実と食い違う記述）:**
- `org-pull-request/references/transport-duality.md:7`: 「`notify_peer` は `DEFAULT_TRANSPORT` を見ない raw env 判定で、無設定なら renga」とある。
  - 正しくは、#945（58d8eda、2026-08-19）以降は `tools.transport.resolve()` を使い、無設定なら broker になる（`tools/peer_notify.py:7-9, 90-136`）。raw env 判定が残るのは resolver が使えないときの退避経路だけ。
- `org-conveyor/SKILL.md:88-89`: 「本リポジトリの active transport は broker（`org-broker-channel` 稼働中）」とある。
  - 運用（`ORG_TRANSPORT=renga`）と食い違う。
- 未設定時の既定値が場所によって違う。
  - `.dispatcher/CLAUDE.md:44` は `${ORG_TRANSPORT:-broker}`、`org-start/SKILL.md:103` は `${ORG_TRANSPORT:-renga}`。
  - 運用では env を明示しているので、今のところ実害は無い。

**delete:**
- `org-attention-start/SKILL.md:31`: 見出しだけで本文の無い注記。

**消すとテストが壊れる文字列:**
- 文書の文字列を直接 assert しているのは `tests/test_transport_seam.py:185-205` だけです。対象は renga-error-codes.md の「`pane_not_found` の messaging 分岐」節で、broker の語には依存していません。
- 文書の書き換えで壊れるのは次の 2 つです。
  - `tools/test_gen_skill_prose.py:614-626`: 本番 manifest の drift ゲート。生成物と .in／フラグメントを同時に変えれば緑のまま。
  - `tests/fixtures/transport_seam/worker_brief_*.golden.md`: 作業者 brief の golden（broker が 1 件ずつ）。テンプレートの 318/332 行を変えたら golden の作り直しが要る。
- broker の名前に依存しているコードは、文書を消しても動き続けます（`git -C $R grep -l mcp__org-broker__ -- tools tests .hooks scripts`、ヒット 109 件）。
  - `.hooks/block-relative-close-pane.sh:38`（DD-2 の broker 例外）
  - `tools/check_group_b_selectors.py`、`tools/self_edit_approval.py`、`tools/gen_worker_brief.py`、`tools/org_extension_schema.json` など

**生成器の仕組み（b 案の実装コストに効く）:**
- `tools/skill_src/fragments/` に、輸送層ごとのフラグメント 27 ファイル（128 行）があります（`dual-system-header-{long,short}.{broker,renga}.md` など）。.in 側は `{{> name }}` と `{{FQ}}` で参照します（`gen_skill_prose.py:38-70`）。
- 長い／短いヘッダは 12 個の .in が共有しています（`git grep -l dual-system-header -- '*.in'`）。**フラグメント 4 ファイルを書き換えるだけで、生成物 12 ファイルの注記がまとめて縮みます。**
- 生成器は `--transport renga` での render に対応しています（`gen_skill_prose.py:90-99`。「rollback byte 安定」とある）。
- 参照だけ残す形は既に org-delegate:44、org-pull-request:47、pr-watch-pane:55-60 で実施済みです（b9eca49、#1010）。

#### 3. 案ごとの比較

| | a: renga 一本に寄せる | **b: 1 か所に集約し各所は参照だけ（推奨）** | c: 現状維持 |
|---|---|---|---|
| 削減行数（算出方法） | 注記ブロック 291 行 ＋ 散在分岐 約 70 行 ＝ **約 360 行**。別判断として org-attach（280 行、broker/tmux 専用。org-attach:33）と org-down（196 行、broker daemon の停止。org-down:4）を廃止すれば ＋476 行 | 注記ブロック 233 行を約 29 か所 × 1.5 行 ＝ 約 45 行に圧縮し、transport-duality.md 3 本（29 行）を 1 本（約 11 行）に統合。**約 205 行、バイトでは約 100KB（推測で約 25k〜33k トークン）**。起動時に読む分は窓口で約 11 行 / 4KB、振り分け役と作業者で各 3 行 | 0 |
| 実装コスト | drift ゲートの transport 変更、生成物 19 本を renga 面で再 render、allowed-tools の書き直し、brief golden の作り直し、broker 用フラグメント 13 本の削除、手保守 15 ファイルほどの手作業 | フラグメント 4 本（ヘッダ）と transport 系フラグメントを 1 行の参照に書き換えて再生成。手保守の約 15 ファイル（CLAUDE.md、.dispatcher/CLAUDE.md、org-start、spawn-flow、pane-close、worker template、org-attach、conveyor など）は手で置き換え。golden の作り直し | 無し |
| リスク | コード既定が broker のままなので、`ORG_TRANSPORT` 未設定の利用者は文書に書かれていない経路で動く（install.sh:281,283 と矛盾する）。**tmux（broker の backend）の経路が文書から消え、オーナー判断の「または tmux」を先回りして閉じる**。broker 用の hook とツールの説明が消える | 参照先を開かずに動くと、broker 固有の分岐（spawn 2 段承認、`[peer_not_found]` など）を見落としうる。分岐する点が受信モデル／spawn 儀式／エラー分岐の 3 つに限られていることを参照行に明記して緩和する | 二重の枠による混乱と肥大が続く。上の outdated（peer_notify、conveyor）も残る |
| 前提条件 | オーナーが tmux ではなく renga を選ぶこと。理想的には runtime 側で `DEFAULT_TRANSPORT` を renga に戻すか、install.sh を直すこと | 無し（オーナー判断の前に実施できる。どちらに転んでも後から a へ進める） | – |

**推奨 b の補強（b+）:**
集約と同時に、生成物を運用と同じ renga 面で render し直します。そうすれば本文のツール名・allowed-tools・ヘッダがすべて `mcp__renga-peers__*` で揃い、「二フレーム注記」そのものが要らなくなります。

- 変更点は drift ゲート（`test_gen_skill_prose.py:625`）を、ja 側の render 用定数（例: 既存の `TEMPLATE_TRANSPORT="renga"`、`tools/transport.py`）へ切り替えることです。
- ただしこれは「生成物はコード既定の面で render する」という設計（notes/broker-skill-generator-design.md）を変えることになるので、人の判断が要ります。

#### 4. Epic #6 Issue G の但し書き

**件数:**
- 対象の指示書群で **25 ファイル**です（`git -C $R grep -l 'Issue G' -- CLAUDE.md .claude .dispatcher | grep -v '\.in$'`）。
  - 内訳は、上の表の生成物 14 本、手保守の 11 本（CLAUDE.md、.dispatcher/CLAUDE.md、pane-close、spawn-flow、org-start、org-attach、org-attention-start、worker-claude-template、claude-org-self-edit など）。
- ガイダンスの「24」とは 1 件違います。上のコマンドの結果は 25 です。
- このほか、フラグメント 12 本と、リポジトリ全体で 45 ファイルにあります。

**Issue G の状態:**
- 完了・中止の記録は見つかりませんでした。
  - `git grep 'Issue G' -- knowledge` は 0 件。
  - `git log -i --grep='Issue G'` で出るのは、但し書きを**追加した** c106c97 / 469e1f9 / 0975882（2026-06-17）だけ。
- docs 側は今も「後日のトラック 3（ユーザー hands-on）」と書いています（`docs/operations/broker-dogfood-runbook.md:7`、`docs/design/transport-switch-ux.md:228-230`）。
- `notes/broker-promotion-plan-586.md:62` は、この一文を反転させる計画のまま止まっています。
- 約 4 か月更新が無く、オーナーは 2026-10-12 に herdr 移行を見送る判断をしています。但し書きは「Issue G 待ち」ではなく、実質的には未着手のまま放置されている状態に見えます（推測）。

#### 5. 人が決めること

1. renga と tmux（broker）のどちらに寄せるか。決まるまでは b を先に入れられ、a に進むかはその後に決められます。
2. b+ を採るか（生成物を renga 面で render する。設計 #9「コード既定の面で render する」の変更になる）。
3. Issue G の但し書き 25 ファイルを一括で削除するか、「未着手」と 1 か所に注記するか。
4. 判断材料の食い違い: 契約書は「Epic #6 broker dogfood #515 passed（2026-06-14）」としています（`docs/contracts/backend-interface-contract.md:5`）。一方 #941 の記録（`tools/peer_notify.py:98-120`）には、ある org（suisya-systems/interlock 系。推測）が broker で peer messaging していた事例があります。「broker は未活性」という前提が本当に正しいかを確認してください。
5. a を採る場合に org-attach（tmux 専用）と org-down（broker daemon の停止）を廃止するか。

ファイルは作成・編集していません。読んだのは `/home/happy_ryo/work/org/workers/claude-org-ja/ja-main` だけです。

## 付録 B2. 輸送層 4 案版

### 4-A. 輸送層の二重記述（a / b / c / d）

**推奨: (a) renga 一本。** 条件が 2 つある。1 つはオーナーが renga を選ぶこと。もう 1 つは、無設定のペインがコード既定の broker へ流れる問題（#941 と同じ型）を先に塞ぐこと。オーナーが tmux を選ぶなら (d) になる。ただし (d) は、下の D-1〜D-9 が塞がるまで適用できない。

**前提（オーナー確認済み）:**
- tmux は broker の backend としてしか使えない。tmux を残す限り broker も残る。
- 運用既定は renga で、broker の実運用は未活性。
- コード上の既定は broker（`tools/transport.py:19-20`、`scripts/install.sh:281,283`）。`tools/transport.py:36-43` は runtime の `DEFAULT_TRANSPORT` をそのまま再公開しているだけなので、ja 側だけでは既定を変えられない。
- この作業者の環境は `ORG_TRANSPORT=renga`、`RENGA_PANE_ID=232`、`ORG_BROKER_STATE_DIR` は未設定（`echo` で確認）。

#### 4 案の比較

| 案 | transport の削減 | 前提 | 主なリスク |
|---|---|---|---|
| **(a) renga 一本（推奨）** | 約 920 行（org-down Step 2 を移さず消すなら約 960） | オーナーが renga を選ぶこと。runtime の既定を renga に戻すか、ja 側に guard を足すこと | 無設定のペインが broker へ流れる（#941 型）。tmux の経路を文書から閉じる。ほかの org が broker で動いている可能性 |
| (b) 1 か所に集約 | 約 205 行（約 100KB） | なし | 参照先を開かず broker 固有の分岐を見落とす。「既定」の逆転と allowed-tools の偏りが残る。(a) か (d) へ進むと作業の大半が無駄になる |
| (c) 現状維持 | 0 | なし | 肥大が続く。事実誤り 2 件も残る |
| (d) broker＋tmux 一本 | 約 1,230 行（幅 1,000〜1,300、推測を含む） | Issue G トラック 3 の実走。D-1〜D-9 を塞ぐこと。許可設定の著述面を移すこと。Windows（WezTerm）の扱いを決めること | 未検証の経路へ本番を切り替える。renga 2.0 対応の文書を捨てる。broker が将来 capability を広告すれば、ゲートを書き直すことになる |

#### 算出方法（4 案共通）

- **母集団:** `git -C $R ls-files CLAUDE.md '.claude/rules/*.md' '.claude/skills/**/*.md' .dispatcher/CLAUDE.md '.dispatcher/references/*.md' | grep -v '\.in$'` で 61 ファイル、計 13,775 行（`cat | wc -l`）。
- **語の分布（`grep -c`）:**
  - `renga` を含む行: 540
  - `broker|tmux` を含む行: 709
  - 両方を含む行: 194
  - ツール名の行: `mcp__renga-peers__` 165、`mcp__org-broker__` 290
- **片方の系に固有の意味を持つ行（python で集計）:** ツール名を取り除いてから数えた。
  - broker 側（`broker|tmux|herdr|wezterm|daemon|folder-trust|queue_watcher|org-broker-channel`）: 609 行
  - renga 側（`renga` とマルチタブ capability の語）: 851 行
  - これは影響を受ける行の目安で、削減量そのものではない。
- **削減見込みの数え方:**
  - 節単位で数え、§1-3 の delete / move にすでに入っている行は差し引いた。差し引いたのは次の分。
    - capability ゲートの move 120
    - org-attach の注記 34
    - org-down の move 12
    - C2 の delete 約 120
    - renga-error-codes の delete 128
    - pr-watch-pane の delete のうち `:367-381`
  - これ以外の重なりは確かめていない（推測）。
  - ツール名の付け替え（`mcp__renga-peers__*` ⇔ `mcp__org-broker__*`）は書き換えで削減ではないので、削減は 0 とし、コストの側に入れた。

#### (a) renga 一本（broker と tmux の両方を外す）

| 消えるもの | 出典 | 行数 |
|---|---|---|
| 注記ブロック（スキル側 233、transport-duality.md 3 本の 29、renga-error-codes の broker 節 `:145-148`・`:153-173` の 29） | 付録 B §1 | 291 |
| 本文に散らばる「broker では…」の分岐 | 散在 | 約 70（付録 B の概算。推測） |
| org-attach の廃止（注記 `:33-66` の 34 行は上で数えたので除く） | `org-attach/SKILL.md:1-280` | 246 |
| org-down の廃止。Step 2 の stale-pid-safe なダッシュボード停止（`:104-144`、41 行）は org-suspend へ移す。move 12 を除く | `org-down/SKILL.md:1-196` | 143（移さず消すなら 184） |
| org-suspend Phase 3.6 | `org-suspend/SKILL.md:471-520` | 50 |
| org-start Block C3 ／ Step 0.5 の herdr preflight ／ dispatcher ビューのサイドバー | `org-start/SKILL.md:293-322`, `:175-198`, `:454-466` | 67 |
| pr-watch-pane の tmux 自動 close と、broker 登録簿に残る stale binding の分岐 | `pr-watch-pane/SKILL.md:89-123`, `:300-334`, `:360-381`, Step 5 (b) | 約 70（推測） |
| renga 面で render すると増える分。`pane-layout-initial` は renga 18 行／broker 7 行、`pane-layout-same-tab` は 6 行／2 行 | `wc -l tools/skill_src/fragments/*` | −15 |
| **計** | | **約 920** |

**tmux / broker に依存する手順の扱い:**

- **org-attach:** 廃止する。この手順は renga だと Step 0 で止まる作りで（`org-attach/SKILL.md:79-96`）、renga 運用では使われない。
- **org-down:** 廃止して /org-suspend に一本化する。
  - renga では Step 3 を飛ばし、「daemon の停止は不要」と案内するだけになっている（`:53-54`, `:153`, `:185-191`）。
  - Step 2 の stale-pid-safe な停止だけは、org-suspend の blind kill（`org-suspend/SKILL.md:465-468` の注）と置き換える形で移す。
- **pr-watch-pane:**
  - tmux による自動 close（`:89-123`、`:225` の末尾の `tmux kill-pane`、`:300-334`）を消す。
  - renga のペインは tmux ペインではないので、自分では閉じられない（`:107-112`、`references/rationale.md` §1、Issue #911）。閉じる経路は、窓口が監視の終わりに出すイベント駆動の close（org-pull-request の終端）だけになる。
  - `[name_taken]` の stale binding 分岐と、DD-2 の裸 name 例外は broker 専用なので要らなくなる。例外の実装は `.hooks/block-relative-close-pane.sh:38-44` と `tools/check_group_b_selectors.py` の ALLOWLIST。
- **org-suspend Phase 3.6 と org-start C3:** watcher は broker 専用（`org-start/SKILL.md:293-322`。`ORG_BROKER_STATE_DIR` が無いと exit 1 する）なので、両方とも消す。
- **org-start Step 0.5:** broker と herdr の組み合わせ専用（`:175`）。herdr はオーナーが見送り済みなので消す。
- **org-start のサイドバー:** broker（tmux）専用（`:454-466`）なので消す。

**残るもの:**
- renga-error-codes の renga 節
- Group B の自タブ確立
- capability ゲート（599 行）。renga 2.0 が必須の下限（`tools/check_renga_compat.py:113`）なので生きている。ゲートの `:26-28` にある「renga も 2.0.0 を挿すまでは旧版形」は outdated。

**コスト:**
- runtime の既定を renga に戻すペアリリースを出すか、`ORG_TRANSPORT≠renga` のときに起動を止める ja 側の guard を足す。
- drift ゲート（`tools/test_gen_skill_prose.py:625`）を renga 面に切り替え、生成物 19 本（`tools/skill_src/manifest.json`）を再 render する。
- 次のものを整理する。
  - broker 用フラグメント 13 本（56 行）
  - `tools/testdata/gen_skill_prose/*.broker.golden.md`
  - `tests/fixtures/transport_seam/worker_brief_*.golden.md`
- 生成スキルの `allowed-tools` は renga 名になり、`.claude/settings.json:19-28` と揃う。いまは broker 名だけ（例: `org-down/SKILL.md:24-31`）。
- 対象範囲外の文書:
  - `README.md:25,44` と `docs/getting-started.md:28,42` は `claude-org-runtime org up` を主経路として書いているので、`renga --layout ops` へ書き換える。
  - `docs/operations/broker-dogfood-runbook.md` は退役扱いにする。
- broker 用のコード（`tools/secretary_queue_watcher.py`、`tools/org-dispatcher-view.sh`、`tools/check_herdr_compat.py`、peer_notify の broker 経路）は、残しても害は無い。消すかは別に判断する。

**リスク:**
- 無設定のペインは broker へ流れる（`tools/peer_notify.py:98-120`）。文書が renga しか書いていないと、流れたことに気づけない。
- tmux の経路を文書から閉じてしまう。git で戻せるが、戻すときは注記を書き直すことになる。
- #941 の事例は suisya-systems/interlock#36 の push だった（`tools/peer_notify.py:107-110`）。その org が broker で動いていたかどうかは推測。

#### (b) 1 か所に集約

- **削減:** 約 205 行（前段の評価のまま）。注記 233 行を約 29 か所 × 1.5 行に縮め、transport-duality.md 3 本（29 行）を 1 本（約 11 行）にまとめる。
- **残るもの:** 両方の系の本文と、org-attach、org-down、C3、Phase 3.6、herdr preflight、capability ゲート。
- **コスト:**
  - フラグメント 4 本（`dual-system-header-{long,short}.{broker,renga}.md`）を書き換えて再生成する。
  - 手保守の約 15 ファイルを手で直す。
  - golden を作り直す。
- **リスク:**
  - 「既定」の逆転と、allowed-tools の偏りが残る。
  - (a) か (d) へ進むと、この作業の大半は捨てることになる。

#### (c) 現状維持

- 削減は 0、コストも 0。
- 二重の枠が残り、事実誤り 2 件（後述）も残る。
- 窓口が起動するたびに読む注記（CLAUDE.md 12 行、org-start 10 行）も減らない。

#### (d) broker＋tmux 一本（renga を外し、運用既定を broker に切り替える）

| 消えるもの | 出典 | 行数 |
|---|---|---|
| 注記ブロック（スキル側 233、transport-duality.md 3 本の 29。renga-error-codes の broker 節は本体として残す） | 付録 B §1 | 262 |
| 本文に散らばる renga の分岐 | 散在 | 約 70（(a) と対称とみた推測） |
| capability ゲートの全体（move 120 を除く） | `capability-first-drive-operational-gate.md:1-599` | 479 |
| renga-error-codes の renga 2.0 節と、renga 固有の ok-return 例外 | `renga-error-codes.md:36-144`, `:174-185` | 121 |
| ゲートと error-codes 以外にある、マルチタブ／Group B の自タブ確立の適用行（下記） | grep 一致の 365 行から C2 delete の約 120 を引いた | 約 245（推測） |
| renga 固有の運用注記（サイドバーの Ctrl+B、`renga --layout ops`、1.x のフォーカス依存など） | 散在 | 約 30（推測） |
| org-start Step 0.5 の herdr preflight（tmux だけに絞る場合） | `org-start/SKILL.md:175-198` | 24 |
| **計** | | **約 1,230** |

**capability ゲートを丸ごと消せる理由:** broker は capability を 1 つも広告しない（ゲートの `:26-27`、`renga-error-codes.md:145-147`）。したがって renga が無ければ、このゲートが発火する backend が無い。

**マルチタブ適用行の内訳:**
- grep 一致は 476 行で、そこからゲートの 56 行と error-codes の 55 行を引くと 365 行になる。
  - 正規表現: `caller_scope|cross_tab_peers|same_tab|capability_gate|capability-first|Group B|server_too_old|spawn_tab|tab_*|自タブ|単一タブ|T-§cap|T-§4.2|operational gate|marker`
- ファイル別の多いところは org-suspend 115、worker-monitoring 68、spawn-flow 41、pane-close 31。
- broker は単一タブモデルなので、確立手段の 1 つ目が常に成り立つ（`.dispatcher/references/pane-close.md:113`）。

**起動時に読む量への効果:**
- capability ゲートは、窓口が起動するたびに Read している（`org-start/SKILL.md:369`）。dispatcher も `list_peers` 待ちの前に Read している（`.dispatcher/CLAUDE.md:81`）。
- (d) ではこの 599 行がどちらの読み込みからも消える。(a) では消えない。

**残るもの:** broker 節、spawn の 2 段承認、pane-layout の broker 面、org-attach、org-down、C3、Phase 3.6、pr-watch-pane の自動 close。

**コスト:**
- 手保守のファイルを broker 面へ書き換える（`mcp__renga-peers__` を含む行が 165 行）。renga-error-codes は broker を正典にした構成へ組み替える。
- `.claude/settings.json` の許可は renga が 15 件、broker が 0 件（`grep -c`）。
- 許可設定のテンプレートの著述面は renga に固定されている（`tools/transport.py:47-60` の `TEMPLATE_TRANSPORT`、`tools/org_setup_prune.py:747-756`）。broker へ移すかは設計判断になる。
- Issue G トラック 3 を実走する。
- tmux だけに絞ると WezTerm が外れ、Windows native が使えなくなる（`scripts/install.sh:280-281`、`README.md:25`）。

**broker の実運用でまだ塞がっていない課題:**

- **D-1. Issue G トラック 3（本番 ja での broker 実走）が未実施。**
  - 文書は「後日のトラック 3（ユーザー hands-on）」のまま（`docs/operations/broker-dogfood-runbook.md:7`、`docs/design/transport-switch-ux.md:228-230`）。
  - `git log -i --grep='Issue G'` に出るのは、但し書きを追加した c106c97 / 469e1f9 / 0975882（2026-06-17）だけ。
  - 「既定経路ではない」を「既定運用経路である」へ反転させる計画は、計画のまま（`notes/broker-promotion-plan-586.md:62`）。
- **D-2. 実機での検証が古い。**
  - runbook の実機検証は runtime 0.1.22（2026-06-13）まで（`broker-dogfood-runbook.md:9`）。いまの pin は `>=0.1.49,<0.2`（`pyproject.toml:30`）。
  - socket 名は 0.1.29 で破壊的に変わっている（`broker-dogfood-runbook.md:597`）。
- **D-3. spawn のたびに 2 段の承認が要る。**
  - 承認の 1 段目は folder-trust、2 段目は dev-channel（`server:org-broker-channel`）（`tools/skill_src/fragments/spawn-ritual.broker.md:1`）。renga は 1 段で済む。
  - push 一次は channel sidecar に頼っている。sidecar が無い・不調・claude.ai ログインが無いときは pull に落ちる（`dual-system-header-long.broker.md:3`）。
- **D-4. secretary 宛メッセージが無言で消える障害があった。**
  - 原因は sidecar の二重走行で、runtime 側の observer lease で修正済み（`org-start/SKILL.md:297`）。
  - それでも ja は常駐の watcher（C3）を置き続けている。
  - Windows native では止めるのに PowerShell の手順が要る（`org-suspend/SKILL.md:497-520`）。
- **D-5. 許可設定が broker を持っていない。**
  - `.claude/settings.json` の broker 許可は 0 件。
  - 生成スキルの frontmatter は broker 名、手保守スキルは renga 名。どちらの面でも、片方の事前許可は効いていない可能性がある（推測、未検証）。
- **D-6. #941 の対応に残りがある。**
  - `resolve()` を使う修正は入った（58d8eda #945、`tools/peer_notify.py:90-136`）。
  - ただし runtime を import できないときの退避経路は、無設定を renga へ送る（`tools/peer_notify.py:141-147`）。renga を外すなら、ここも直す必要がある。
- **D-7. ペインの close が不完全。**
  - 自分で閉じる経路が効くのは tmux backend だけ。herdr / wezterm では閉じずに残る（`pr-watch-pane/SKILL.md:96-112`、`references/rationale.md` §1、Issue #751）。
  - tmux でも broker の登録簿に name binding が残り、同じ名前で再 spawn すると `[name_taken]` になる（`pr-watch-pane/SKILL.md:322-331`）。これを掃除するために hook に例外を置いている（`.hooks/block-relative-close-pane.sh:38-44`）。
- **D-8. 走行中のペインが画面に出ない。**
  - 子ペインは detached な tmux セッションで、窓口は logical pane（`tools/skill_src/fragments/pane-layout-initial.broker.md:3`）。
  - 単一セッション化はまだ入っていない（`broker-dogfood-runbook.md:600-608`）。
- **D-9. 背景で動く daemon の止め方に穴がある。**
  - SIGINT では止まらない。SIGTERM で止めると sidecar が残る（`broker-dogfood-runbook.md:111-117`）。
  - runtime 側で直した記録は見つからなかった。`git log -i --grep=SIGINT` に出るのは runbook の修正 cd80405 / 7ea7ceb だけ。
- **D-10. 運用の知見が溜まっていない。** `git grep -c -i -E 'broker|tmux|herdr' -- knowledge` の結果は 0 件。renga は curated の 5 ファイルに 7 か所ある。

**見つからなかったもの:**
- broker 関連の test skip / xfail は無かった（`git grep -n -E 'skipif|skipIf|pytest.mark.skip|xfail|pytest.skip\(' -- tests tools | grep -i broker` で 0 件。herdr の skip は正常系、`tools/test_check_herdr_compat.py:220-244`）。
- tools / scripts / .hooks に broker 関連の TODO / FIXME は無かった（0 件）。

**記述の食い違い:**
- `CHANGELOG.md:281,336-337` は「broker へ移行して既定化」「renga-free 完全移行を完了」と書いている。
- 一方、2026-08 の変更は renga 2.0 のマルチタブ追随が中心（036a852, 87a39be, 98735cf, 59cf3af, ad33c84）。
- 推測: 1.0.0 のあと、運用は renga に戻った。

#### 推奨: (a)

1. **運用の事実と一致する。** 運用既定は renga で、broker は未活性、knowledge には broker の知見が 0 件、Issue G は約 4 か月動いていない。使われていない経路の説明が毎回の読み込みに乗っていることが、肥大の本体になっている。
2. **(d) は削減量が最大だが、今は適用できない。** D-1〜D-9 を塞がない限り適用できず、本番の切り替えという文書以外の作業が先に要る。
3. **(b) では根本の問題が解けない。** 2 つの枠を残すので、「既定」の逆転も allowed-tools の偏りも残る。

オーナーが tmux を選ぶなら (d) にし、D-1〜D-9 を先に塞ぐ。決まるまでは (c) のままにして、事実誤り 2 件だけ先に直す。

#### 人が決めること（4 案に合わせて整理し直した）

1. **renga か tmux か。** renga なら (a)、tmux なら (d)、まだ決めないなら (c)、両方を長く併存させるなら (b)。
2. **「既定」表記の逆転。**
   - 食い違っている箇所:
     - `.dispatcher/CLAUDE.md:50` は `${ORG_TRANSPORT:-broker}`、`org-start/SKILL.md:103` は `${ORG_TRANSPORT:-renga}`。前段では `:44` としていたが、実際は `:50`。
     - `org-setup/SKILL.md:26` は「既定 renga」と書くが、`tools/org_setup_prune.py:747-756` は無設定を broker 側へ射影する。
   - 案ごとの扱い:
     - (a) なら renga に揃う。前段の b+ の判断もここに吸収される。
     - (d) なら broker に揃う。
     - (b) なら集約先に 1 回だけ書く。
3. **allowed-tools。**
   - (a) なら、生成スキルの frontmatter も renga 名になって揃う。
   - (d) なら、手保守のスキルと `.claude/settings.json` を broker にする。
   - (b) / (c) なら、生成スキルの事前許可が効いているかを実機で確かめる。
4. **Issue G の但し書き（25 ファイル）。**
   - (a) なら broker ごと消える。
   - (d) ならトラック 3 の後に「既定運用経路」へ反転させる。
   - (b) なら集約先 1 か所に「未着手」と書く。
5. **案によらず先に直す事実誤り。**
   - `org-pull-request/references/transport-duality.md:7`: #945 以降は `resolve()` を使い、無設定なら broker になる（`tools/peer_notify.py:7-9,90-136`）。(a) をすぐ入れるならファイルごと消えるので、直さなくてよい。
   - `org-conveyor/SKILL.md:88-89`: 「active transport は broker」とあるが、運用は renga。
6. **broker が未活性という前提の確認。** #941 の interlock の事例（`tools/peer_notify.py:107-110`）がある。(a) でも (d) でも効いてくる。
7. **(a) を採る場合に決めること。**
   - org-attach と org-down を廃止するか。
   - org-down の Step 2 を org-suspend へ移すか（削減が 143 行か 184 行かが変わる）。
   - DD-2 の hook 例外と、`secretary_queue_watcher.py` などのコードを消すか。
8. **(d) を採る場合に決めること。**
   - WezTerm と herdr を残すか。残せば D-7 も残る。
   - 許可設定の著述面を broker へ移すか。
9. **`org-attention-start` の表記。** これだけが手保守で renga 名になっている（stop と suspend は生成物で broker 名）。(a) / (d) なら自然に解消し、(b) / (c) なら揃える必要がある。`:31` の見出しだけの注記は、どの案でも delete。

#### 4 案版で差し替える §1-3 の transport 案の表

| 案 | 削減見込み | 合計（delete 930＋move 809＝1,739 に transport 削減を足した値 / 総行数 13,775 比） |
|---|---|---|
| **(a) renga 一本（推奨。オーナーが renga を選ぶ場合）** | 約 920 行（org-down Step 2 を移さず消すなら約 960） | 約 2,659 行（19.3%）。移さない場合は約 2,699 行（19.6%） |
| (b) 1 か所に集約 | 約 205 行（約 100KB） | 約 1,944 行（14.1%） |
| (c) 現状維持 | 0 | 1,739 行（12.6%） |
| (d) broker＋tmux 一本 | 約 1,230 行（幅 1,000〜1,300、推測を含む）。D-1〜D-9 が塞がるまで適用できない | 約 2,969 行（21.6%） |

#### 4 案版で差し替える §2 の #2 の行

| # | 対象 | 分類 | 削減行数 | 根拠の要点 |
|---|---|---|---|---|
| 2 | 輸送層の二重記述と、片方の系にしか無い手順・参照（39 ファイル中 36 ファイル、Issue G の但し書き 25 ファイルを含む） | transport | a: 約 920 / b: 約 205 / d: 約 1,230（c: 0） | 共通の注記ブロックが約 291 行。(a) ではこれに org-attach 246、org-down 143、org-start の C3・herdr・サイドバー 67、Phase 3.6 50、pr-watch-pane の tmux close 約 70 が加わる。(d) ではゲート 479（`org-start/SKILL.md:369` で毎回 Read される）、renga 2.0 節 121、マルチタブの適用行 約 245 が加わる。推奨は (a)（条件付き）。§4-A を参照 |
## 付録 C. 横断重複検出（原文）

### ファイル横断の重複検出（ja-main 31a5c40）

先に結論を 3 点書きます。

1. **`worker-claude-template.md` はワーカーに届いていない。** brief を生成しているのは `tools/gen_worker_brief.py:415` で、読み込むのは `tools/templates/worker_brief_normal.md` / `worker_brief_self_edit.md` だけです。テンプレート冒頭（`worker-claude-template.md:3-10`）の「Step 1.5 でこれを CLAUDE.md として書き出す」は古い記述です。Step 1.5 は `gen_delegate_payload.py` が一括で行います（`org-delegate/SKILL.md:186`）。直近の追記 3 件はテンプレートにしか入らず、ワーカーには一度も届いていません。
   - 96282ca（2026-09-16、uv/pip キャッシュ）
   - 176e70a（2026-09-21、Codex の allowed_domains）
   - 7d5109c（2026-07-08、完了報告前の rebase）
   - 確認: `git show --name-only` で 3 件とも変更ファイルは worker-claude-template.md の 1 つだけ。
   - 一方で、このテンプレートは今も「SoT」として参照されています: `org-delegate/SKILL.md:141,361`、`org-pull-request/SKILL.md:76`、`docs/contracts/role-contract.md:258`、`delegation-lifecycle-contract.md:86,147,152`。
2. **「自タブ確立は 2 手段だけ・same_tab は数えない」という規則が 10 か所に書き下されており、正本が無い。** `renga-error-codes.md` にも該当する文はありません（`grep 確立手段|単一タブモデル` で 0 件）。
3. 分類側の delete(重複) には、**正本側が古い、または正本側に条件が足りないもの**が混じっています（末尾の「誤 delete の疑い」）。

grep はすべて `git -C …/ja-main grep -n` で行い、生成物の `.md` を対象にしました（`.in` は除外）。

---

#### クラスタ一覧（削減効果の大きい順）

##### C1. ワーカー brief の二重系統（worker-claude-template.md と tools/templates/worker_brief_*.md）
- **規則**: ワーカーの行動規範全般（禁止事項・preflight・Codex 手順・完了報告・SUSPEND）
- **正本**: `tools/templates/worker_brief_normal.md`（232 行）と `worker_brief_self_edit.md`（143 行）。生成器が実際に読むのはこの 2 つ（`tools/gen_worker_brief.py:46,415`）。golden test も `tests/fixtures/transport_seam/worker_brief_normal_*.golden.md` を固定している。
- **重複**: `.claude/skills/org-delegate/references/worker-claude-template.md:13-388`（約 376 行。ファイル全体は 400 行）
- **確認**: `git -C … grep -ln worker-claude-template -- tools tests` で 0 件。テンプレートを読むツールもテストも無い。
- **テンプレートにだけある条項**: 行単位の差分（template 側の行が normal/self_edit に無いもの）から拾った。brief には入っておらず、ワーカーは読んでいない。
  - :30-33 正しい作業手順（git init / clone、絶対パスの確認）
  - :59 Codex を走らせる Bash では allowed_domains に `chatgpt.com` 等を入れる（#1019）
  - :61, :75-77 uv/pip キャッシュは `$TMPDIR` に置く（#1014）
  - :116, :127 一時ファイルは `$TMPDIR` へ。`/tmp` 直下は書けない（`$TMPDIR` とは別物）
  - :123 doc-audit の denyWrite に穴がある件
  - :139-150 監査タスクでは反証実験を必須にする
  - :152-161 probe 系タスクの credential 退避
  - :163-173 生成物 prose を編集するときは manifest を確認し、`.in` を編集して再生成し `--check` で照合する
  - :175-187, :323 完了報告前の rebase と「rebase clean」行
  - :191 検証深度が不明なら窓口に確認する
  - :205-213 codex の availability check コマンド
  - :281-283 safety block 時の continuation spawn
  - :293 別ワーカーにレビューを委譲しない
  - :337-354 raw の記録フォーマット
- **テンプレートが古くなっている点**（normal と食い違う）:
  - :229 ログ名に cksum が無い（normal:136-139 は付く）
  - :233 `--base origin/main` 固定（normal:143 は `${task_base_ref}`）
  - :316-320 「既定 renga」と numeric fallback（normal:225-227 は「同じ org と確認できるまで再送しない」）
  - :318 「既定 renga / opt-in broker」は、instruction-template.md:6 の「既定 broker / opt-in renga」と逆向き（既出の指摘: `notes/2026-08-05-renga2-org-audit.md:670`）
- **削減**: 上の固有条項を normal / self_edit に移植するか、捨てるかを人が決めた後なら、テンプレートを 5 行程度のポインタにして約 370 行減らせる。移植せずに消すと、上の条項はどこにも残らない。

##### C2. Group B（close_pane / set_pane_identity）の自タブ確立 2 手段・same_tab を数えない・identity 照合
- **規則**: Group B は、自タブのものと確立できた列挙から採った数値 pane_id でしか撃たない。確立手段は (i) 単一タブ backend（org-broker）と (ii) caller_scope の確立、の 2 つだけ。list_peers の same_tab は否定方向の絞り込みにしか使わない。close の直前に name / role を照合する。
- **正本候補**: `.dispatcher/references/pane-close.md:112-121`。`org-suspend/SKILL.md:134-137` が「harness 側で書き下したもの…両者は同一規則」と名指ししている。ただし pane 制御の総論である `renga-error-codes.md:53-72` には 2 手段の定義が無い（`grep 確立手段|単一タブモデル` で 0 件）。正本を新しく 1 か所に置く判断が要る。強制側の hook は `.hooks/block-relative-close-pane.sh`（相対セレクタの deny。2 手段の判定はしない）。
- **重複**:

| 位置 | 行数 |
|---|---|
| `org-suspend/SKILL.md:98-167`（Group A/B の説明。SHOULD 時代の経緯 154-163 を含む） | 約 70 |
| `org-suspend/SKILL.md:192, :641` | 各 1-3 |
| `worker-monitoring.md:1241-1248` | 8 |
| `pr-watch-pane/SKILL.md:402-409` | 8 |
| `pr-watch-pane/SKILL.md:439-446` | 8 |
| `pr-watch-pane/SKILL.md:530-544` | 15 |
| `org-attention-start/SKILL.md:178-186` | 9 |
| `org-attention-stop/SKILL.md:55-69` | 15 |
| `org-pull-request/SKILL.md:141-144` | 4 |
| `org-start/SKILL.md:424-427` | 4 |
| `.dispatcher/CLAUDE.md:223-230` | 8 |
| `spawn-flow.md:113-125` | 13（「同一タブ配置の理由」と混在） |
| `pane-layout.md:95` | 1 |

- **確認**: 上の grep に加え、org-suspend:130-153 と pane-close:112-121 を読み比べた。条件は同じ。
- **残すべき固有部分**:
  - org-suspend:125-129「本スキルは stale-binding carve-out に当たらない」
  - org-suspend:164-167 `server_too_old` は pending に残す
  - pr-watch-pane の stale-binding 3 条件（557-578）
  - 経緯段落（org-suspend:154-163、renga-error-codes:70）は move の対象
- **削減**: 約 150 行 → 各所 1-2 行のポインタにすると約 120 行減。

##### C3. messaging 文脈の pane_not_found 復旧（list_peers を取り直し、数値 id で 1 回だけ再送し、lifecycle を確認）
- **正本**: `org-delegate/references/renga-error-codes.md:98-143`
- **重複**: `org-suspend/SKILL.md:575-614`（40 行。589 に「同一規則」と自記）、`worker_brief_normal.md:225-227`（brief はワーカー唯一の文脈なので残す）
- **差異（正本に無い条件）**: org-suspend:595-599 は「旧版 fallback の列挙で同名が 1 件だけなら採用してよい。現配備の org-broker / renga 1.x はここを通り、この枝が無いと生存ワーカーを取り逃がす」と書く。正本 :117-120 は same_tab:true しか採らず、:142 は 1.x について「救済が効かないだけ」と書いている。**両者は矛盾する。** 重複を消す前に、どちらを正とするかを決めて正本へ寄せる必要がある。
- **削減**: 約 30 行（org-suspend 側を 5 行程度に縮める）

##### C4. Codex セルフレビュー手順（CODEX_HOME・空の合格・round 規律）
- **正本**: ワーカー向けは `worker_brief_normal.md:108-210`、根拠は `knowledge/curated/codex.md`
- **重複**:
  - `worker_brief_self_edit.md:71-132`（別ロール用の brief。self_edit の非空行 117 行のうち 55 行が normal と同一。削るには fragment 化が必要）
  - `worker-claude-template.md:189-309`（C1 の死蔵コピー）
  - `instruction-template.md:79-93`（窓口向けの再掲。15 行）
  - `instruction-template.md:222`（1 行だが非常に長い）
- **確認**: `grep -c "codex exec review"` は instruction-template 6 / template 5 / normal 3 / self_edit 3 件。
- **注意**: instruction-template:82 と :222 の safety block 回復は、brief（normal / self_edit）に**無い**（`grep "safety\|安全機構に掛か" worker_brief_normal.md` で 0 件）。下の誤 delete 疑い 4 を参照。
- **削減**: instruction-template:79-93 で約 13 行（:82 は残す）。template 側は C1 に含めて数える。

##### C5. state-db cutover の注記（StateWriter.transaction() 経由で書き、org-state.md を直接編集しない）
- **正本**: 散文の正本は無い。強制しているのは `tools/state_db/writer.py` と `drift_check.py`。
- **重複**:

| 位置 | 行数 |
|---|---|
| `org-start/SKILL.md:78-87` | 10 |
| `org-resume/SKILL.md:23-29` | 7 |
| `org-suspend/SKILL.md:418-424` | 7 |
| `spawn-flow.md:12-16` | 5 |
| `org-delegate/SKILL.md:42` | 1（長い行） |

- **差異**: 「M4 で撤去 / 廃止」「journal.jsonl は M4 で廃止」は過去の経緯で、move の対象。
- **削減**: 約 20 行（各所 1 行に縮める）

##### C6. work-discovery の triage scan（worker クローズ時）
- **正本**: 実行手順は `.dispatcher/references/pane-close.md` Step 6（575-710）。窓口側は `work-discovery/SKILL.md:78-148`。
- **重複**: `.dispatcher/CLAUDE.md:259-288`（30 行。常時読み込み）。上記の規則一覧にある `.dispatcher/CLAUDE.md:277` と pane-close:644 の exit 2、:284 と pane-close:604 / work-discovery:81 の 300000ms も同じクラスタ。
- **旧手順への念押しが 3 か所**:「`$REPO_FLAGS` は使わない」が `.dispatcher/CLAUDE.md:266-267`、`pane-close.md:587-591`、`work-discovery/SKILL.md:95` にある。直近の関連記録は 7bda7af（2026-08-06 #832）で、以後の再発は `git log -S'REPO_FLAGS'` で 0 件。
- **削減**: `.dispatcher/CLAUDE.md` 側を 4 行程度に縮めて約 25 行減。念押しは実行場所の 1 か所に残せばさらに約 6 行減。

##### C7. secretary の awaiting_user 通知
- **正本**: 各 gate の emit 箇所: `org-delegate/SKILL.md:362-366`、`org-pull-request/SKILL.md:71-73`、`org-escalation/SKILL.md:59-64,72-81`
- **重複**: `CLAUDE.md:108-127`（20 行）
- **outdated**: `CLAUDE.md:112`「対象 gate（4 箇所）」と :123 の gate 列挙は古い。`org-pull-request/SKILL.md:202` が 5 つ目の `gate=ci_unconfirmed_head_gate` を emit している。また :126-127「並走の runtime PR が…追加する」は未来形のままで、各スキル（org-delegate:366、org-escalation:64,76）にも同じ未来形が残る。
- **削減**: 約 16 行（CLAUDE.md には「user 待ちで止まる直前に各スキルの emit 行を実行」の 2 行だけ残す）

##### C8. ディスパッチャーの stall 検出の詳細
- **正本**: `worker-monitoring.md` Step 5 / 5.2（定数は 577, 579, 648, 668, 782, 1035-1164 に登場）
- **重複**: `.dispatcher/CLAUDE.md:166-170`（5 行。1 行ずつがほぼ段落の長さ）
- **確認**: `grep -n SPINNER_ACTIVE_SUPPRESS_CAP_MIN|STALL_PR_MERGE_LOOKBACK_MIN|latest-resolution|PANE_OUTPUT_IDLE_STREAK_MIN worker-monitoring.md` で全部ヒットした。
- **注意**: 同じ箇所の :164 `spawn_gate.py audit` は worker-monitoring に**無い**（`grep "spawn_gate.py audit" .dispatcher` で .dispatcher/CLAUDE.md:164 の 1 件だけ）。消してはいけない。
- **削減**: 5 行（文字量は大きい）

##### C9. 軽量レーン（subagent 直処理）
- **正本**: `org-delegate/SKILL.md:59-78`
- **重複**: `CLAUDE.md:73-84`
- **差異（正本が古い）**: org-delegate:65,76 は「`run_in_background=true` 必須」と書く。CLAUDE.md:82 と hook `.hooks/block-foreground-subagent.sh:11-23`（40c33f1、2026-08-19 #944）によれば、このパラメータはスキーマから廃止され、subagent は常に背景で動く。また org-delegate:61 は「背景・実証は CLAUDE.md を SoT 参照」としており、CLAUDE.md 側を消すと参照先が無くなる。
- **削減**: 約 10 行（org-delegate:65,76 を直してから）

##### C10. ワーカーのモデル既定（opus）
- **位置**: `.dispatcher/CLAUDE.md:71`、`spawn-flow.md:106-113`、`org-delegate/SKILL.md:105`
- **差異**: `.dispatcher/CLAUDE.md:71` は「auto classifier は sonnet だと不安定なため Opus 固定」と書くが、`spawn-flow.md:112` は「safety classifier はワーカーのセッションモデルとは別の専用モデル。モデル選択は承認フローの安全性とは独立」と書く。**理由づけが矛盾**しており、.dispatcher/CLAUDE.md:71 が outdated。指定の受け方も、:71 は「特殊ケースのみ明示」、spawn-flow:111 は「DELEGATE に `モデル: sonnet` 行があるときに限り」で違う。
- **正本**: spawn-flow:111（ディスパッチャー側）、org-delegate:105（窓口側）
- **削減**: 1 行

##### C11. PR の CI 監視は /pr-watch-pane だけ
- **正本**: `.claude/rules/pr-ci-watch.md:7-10`（`paths:` が無いので全ファイルに適用）。hook は `.hooks/block-adhoc-pr-watch.sh`。
- **重複**: `CLAUDE.md:41`、`org-pull-request/SKILL.md:63, 111-114`
- 分類側の delete と一致する。差異なし。削減は約 6 行。

##### C12. skill 候補の deferred（pending に戻さない・再問い合わせしない）と pending≥5 のバッチ問い合わせ
- **正本**: `knowledge/skill-candidates.md:50-61`
- **重複**:
  - `org-retro/SKILL.md:145-148`（4 行）、`:107-111`（5 行）
  - `skill-audit/SKILL.md:80-86`（7 行）
  - `skill-eligibility-check/SKILL.md:123-125`（3 行）、`:143-146`（Step 4 の実行場所なので残す）
- **差異**: skill-audit:83-84 の「バッチ問い合わせで人間が見送った候補は pending → deferred に更新する（ワーカー委譲経由）」は、**いつ・誰が遷移させるか**という手順で、正本 :53 は状態の定義しか書いていない。
- **削減**: 約 12 行

##### C13. ディスパッチャーの内部状態ファイルを handover / resume で触らない
- **正本**: `.dispatcher/CLAUDE.md:192-200`（ディスパッチャーは常時読む）
- **重複**: `dispatcher-handover/SKILL.md:52-58, 216-217`、`dispatcher-resume/SKILL.md:44-48, 429-430`
- **削減**: 約 6 行（スキル内の再掲だけ）

##### C14. release 系タスクの pre-fetch
- **正本**: `org-delegate/SKILL.md:172-180`、`references/release-pre-fetch.md`
- **重複**: `runtime-release-with-paired-ja-sync/SKILL.md:90-102`、`release-pre-fetch.md:17-25`
- **差異**:
  - 「non-ff なら派遣せず人間に報告する」は runtime-release:101-102 にしか無い（`grep "non-f\|報告" release-pre-fetch.md` で 0 件）。
  - org-delegate:174,180 は「4 条件」と書くが、列挙は 3 つ（:176-178）。release-pre-fetch.md:17-25 も 3 つ。数が outdated（第 4 条件が消えた経緯は未確認で、推測）。
  - runtime-release:96 は `cd <runtime workers_dir>` の形で書かれている。
- **削減**: 約 10 行

##### C15. 小さいクラスタ（実行箇所ごとの 1 行リマインダで、削減はわずか）
- **send_keys は text と Enter を別に送る**: 正本 `renga-error-codes.md:396`。`spawn-flow.md:304-310` と `CLAUDE.md:65`（窓口固有の用途として自分で根拠を書いている）は残してよい。`claude-org-self-edit.md:128-133` は tool が内部で処理するので説明は move の対象。
- **時刻は date -u の UTC で、ローカル時刻に Z を付けない**: 正本 `worker-monitoring.md:87-97`。`dispatcher-handover:138,177`、`dispatcher-resume:191-195,317`、`pane-close.md:358-378`、`spawn-flow.md:390` は書き込み箇所ごとの 1 行なので残す。
- **journal に生 JSON を `>>` で書かない**: 正本 `.dispatcher/CLAUDE.md:97`。`spawn-flow:375`、`pane-close:80,153,650` は括弧書き。削減ほぼゼロ。
- **起動プロンプトに盲目で Enter しない**: spawn 儀式の注記は transport の枠に入る。実行手順は `org-start:353-366`、`spawn-flow:219-232`、`pane-close:446-450`、`.dispatcher/CLAUDE.md:79` にあり、それぞれ別の経路。
- **絶対パス規約・cp932・git stash・cp -i・npm cache・dangerouslyDisableSandbox**: brief（normal / self_edit）とテンプレートの間だけの重複（C1）。`spawn-flow.md:321` は kickoff で「絶対パスで」と念押しするための 1 行で残す（事故経緯の文は move）。`org-delegate/SKILL.md:118`（cp932）は brief が常に含むので delete で妥当。`wsl-windows-chrome-screenshot:29-35,130,146` はレシピ固有の文脈なので重複ではない。
- **知見の記録基準**: `knowledge-standards.md:23`、`worker_brief_normal.md:229`、`template:353`。brief は残す。記録フォーマット（`knowledge-standards.md:10-19`）は curator 向けの文書なので、ワーカーには届いていない。
- **矛盾する知見の扱いが食い違う**: `knowledge-standards.md:34` は「マージせず両方残し、矛盾を明記」、`org-curate/SKILL.md:201` は「新しい日付を優先し、矛盾を明記」。重複ではなく**不一致**なので、人の判断が要る。

##### 参考: 輸送層の注記ブロック（分類対象外。集計にだけ使う）
冒頭の `>` 引用ブロックで「輸送層」を含むものを機械的に数えた。29 ファイル、計 213 行。大きいのは次の 4 つ:

| 位置 | 行数 |
|---|---|
| org-attach:33-66 | 34 |
| pr-watch-pane:55-77 | 22 |
| spawn-flow:5-10, 38-43 | 12 |
| renga-error-codes:62-72 | 11 |

これ以外の多くは 7 行。1 行が 1 段落相当なので、文字量は行数よりずっと大きい。

##### 副次的に見つけた outdated（分類側へ回す）
- `.dispatcher/CLAUDE.md:173`、`pane-close.md:95`、`worker-monitoring.md:1337` の `.state/workers/worker-{peer_id}.md`。helper が書くのは `worker-{task_id}.md`（`.dispatcher/CLAUDE.md:94`、`CLAUDE.md:138`）。
- `.dispatcher/CLAUDE.md:80` の「`plan_version` 2 未満（runtime 0.1.42 以前）」の分岐と、`spawn-flow.md:221` の同じ趣旨の箇所。pin は `>=0.1.49`（pyproject.toml:30）なので、通らない経路。delete 候補。

---

#### 誤 delete の疑い（正本側に無い条件を含む delete(重複) 判定）

| # | delete 判定された行 | 正本とされた場所 | 正本に無い条件・問題 |
|---|---|---|---|
| 1 | worker-claude-template.md:53-58, 60, 62-74 | normal:37-56 | この範囲は一致する。ただ隣の :59（Codex allowed_domains、#1019）と :61, :75-77（uv/pip、#1014）は normal に無く、テンプレートを丸ごと消すと失われる。2 件ともテンプレートだけを変えたコミット（176e70a / 96282ca） |
| 2 | worker-claude-template.md:108-133 | normal:39 | normal には「`/tmp` 直下は書けない（`$TMPDIR` とは別物）」（:127）、ダウンロード物は `$TMPDIR` へ（:116）、doc-audit の denyWrite の穴（:123）が無い |
| 3 | worker-claude-template.md:189-280 | normal:108-179 | normal には「検証深度が不明なら窓口に確認」（:191）と availability check のコマンド（:205-213）が無い |
| 4 | worker-claude-template.md:281-284（safety block） | instruction-template.md:82 / :222 | instruction-template は brief に描画されない。runtime が使う `tools/ja_locale.json` の instruction_template は `{instruction}` を差し込むだけの 7 行。生成される brief に safety block の回復手順は 0 件。窓口が手で貼らない限りワーカーには届かない（届く経路は推測） |
| 5 | worker-claude-template.md:285-309 | normal:180-210 | normal に「別ワーカーにレビューを委譲しない」（:293）が無い |
| 6 | worker-claude-template.md:333-362 | normal:229-232 | normal に記録フォーマット（事実 / 判断 / 根拠 / 適用場面）と「一般知識は記録不要」（:354）が無い。フォーマットの正本 knowledge-standards.md:10-19 はワーカーに届かない |
| 7 | （delete 対象外だが関連）worker-claude-template.md:175-187 の rebase | org-delegate/SKILL.md:141 がこれを SoT として参照 | brief に rebase 節が無い（`grep rebase worker_brief_normal.md` で 0 件）。SKILL.md:141 は「brief に従う」前提で書かれているが、規定はワーカーに届いていない |
| 8 | CLAUDE.md:73-80（と 81-84 を一緒に消す場合） | org-delegate/SKILL.md:59-78 | 正本 :65,:76 の「`run_in_background=true` 必須」は古い（hook block-foreground-subagent.sh:11-23、#944）。正しいのは CLAUDE.md:82。さらに org-delegate:61 が CLAUDE.md を SoT として参照しているので、消すと参照先が無くなる |
| 9 | org-conveyor/SKILL.md:97-107 | work-discovery/SKILL.md:120-135 | 条件が違う。conveyor は背景タブ配置を配線したら `free_worker_slots` を使い、「list_peers で数え直すだけでは bind 待ちを数え落とす」と書く。work-discovery:119-124 は別タブ配置なら list_peers で数えるとし、`free_worker_slots` は overflow を配線したときだけ。消す前に両者を揃える必要がある |
| 10 | runtime-release-with-paired-ja-sync/SKILL.md:90-99 | org-delegate Step 0.6 + release-pre-fetch.md | 「non-ff なら派遣せず人間に報告」（:101-102）は正本に無い。90-99 を 1 行の参照にするなら 100-102 は残す。正本側の「4 条件」は 3 つしか無い |
| 11 | skill-audit/SKILL.md:79-86 | knowledge/skill-candidates.md:52-58 | 「バッチ問い合わせで見送られた候補を pending → deferred に更新する（ワーカー委譲経由）」という遷移の手順（:83-84）は正本に無い。awk が完全一致で数えるので deferred が除外される、という実装の説明（:80-82）も Step 1 の awk に結びついている |
| 12 | .dispatcher/CLAUDE.md:162-163（要約） | .dispatcher/CLAUDE.md:149 と worker-monitoring | 範囲としては正しい。ただ、直後の :164 `spawn_gate.py audit` は worker-monitoring に無いので、要約と一緒に消してはいけない |
| 13 | （規則一覧からの追加。delete 判定は無い）org-suspend/SKILL.md:575-614 | renga-error-codes.md:98-143 | org-suspend:595-599 の「旧版 fallback の列挙で同名が 1 件だけなら採用してよい」は正本に無く、正本 :142 と矛盾する（C3） |

その他の delete(重複) 判定は、確認した範囲では正本と条件が一致していました。確認したもの: CLAUDE.md:40-51、CLAUDE.md:138-140、org-delegate:93-94 と spawn-flow 3-5a、.dispatcher/CLAUDE.md:223-248 と worker-monitoring Step 5.3、.dispatcher/CLAUDE.md:165 と worker-monitoring:1185-1188、claude-org-self-edit.md:87-94 と worker_brief_self_edit.md:3、org-pull-request:111-114 と rules/pr-ci-watch.md。

#### 人が決めること
- **C1**: テンプレートにしかない固有条項（rebase・監査の反証・credential・生成物 prose・uv/pip・Codex の allowed_domains・safety block）を normal / self_edit に移植するか、捨てるか。その後テンプレートをポインタ化してよいか。org-delegate:141,361、org-pull-request:76、docs/contracts（role-contract:258、delegation-lifecycle-contract:86,147,152,155,178）の SoT 参照先も合わせて付け替える。
- **C2**: 「自タブ確立 2 手段」の正本を pane-close.md に置くか、renga-error-codes.md に新しい節を作るか。
- **C3**: 旧版 fallback の同名 1 件を採用してよいか（org-suspend と renga-error-codes:142 の矛盾）。
- **C4**: normal と self_edit の共通部分（非空行で 55 行が同一）を fragment 化するか。生成器の変更が要る。
- **C15**: 矛盾する知見の扱いを「両方残す」にするか「新しい日付を優先」にするか。
