# 指示書・スキルの近代化とリポジトリ分割 計画書

- 作成: 2026-10-12。対象: ja main 31a5c40
- 根拠資料: [`docs/design/prompt-modernization/inventory-2026-10-12.md`](prompt-modernization/inventory-2026-10-12.md)（以下「棚卸し」。§番号は棚卸しの節）
- 読み手: この計画から Issue を起票する窓口、各 Issue を実装する作業者、判断するオーナー
- 行番号はすべて 31a5c40 時点。着手時にずれていたら、内容で探し直す

## 0. 目的と結論

窓口・振り分け役・作業者が読む指示書とスキルは 61 ファイル・13,775 行ある（棚卸し §1-1。`git ls-files CLAUDE.md '.claude/rules/*.md' '.claude/skills/**/*.md' .dispatcher/CLAUDE.md '.dispatcher/references/*.md' | grep -v '\.in$'` の合計）。公式推奨から次の点で外れている（棚卸し §5-1、§5-2）。

| 公式推奨 | 現状 | 出典 |
|---|---|---|
| SKILL.md は 500 行以内 | org-suspend 791 行、pr-watch-pane 591 行、org-start 543 行 | https://code.claude.com/docs/en/skills.md |
| CLAUDE.md は 200 行未満 | `.dispatcher/CLAUDE.md` 288 行 | https://code.claude.com/docs/en/memory.md |
| 100 行超の参照ファイルに目次 | 該当する参照ファイルのどれにも目次が無い | https://platform.claude.com/docs/en/agents-and-tools/agent-skills/best-practices.md |
| 強調を多用しない | `worker-monitoring.md` の太字 1,019 個 | https://platform.claude.com/docs/en/build-with-claude/prompt-engineering/claude-prompting-best-practices.md |
| 経緯を本文に書かない | 事故の日付・Issue 番号・設計メモが手順に混在 | https://code.claude.com/docs/en/skills.md |
| 例外なく守らせる決まりは hook | 文章だけの禁止事項が残る | https://code.claude.com/docs/en/memory.md |

振り分け役はワーカー 1 人の派遣で約 3,655 行、推定 19〜22 万トークンを読む（棚卸し §1-2。トークン数は推定式による）。

方針（オーナー決定 2026-10-12）:

1. **第 1 段階**: 今の両系（renga / broker）併記のまま、公式推奨に合わせて整える。記述のずれ（古い記述・矛盾）の修正も含める。
2. **第 2 段階**: 輸送層の分岐ごとにリポジトリを分割し、新しいリポジトリにする。共通部分は共通リポジトリにする。
3. 安易に main に入れない。統合用ブランチで作業し、試験用の組織を別の場所で実運用してから、main に入れるか第 2 段階へ直行するかをオーナーが決める。

## 1. 決定済みの事項（オーナー承認 2026-10-12）

### 1-1. 輸送層の記述は第 1 段階では触らない

第 2 段階の分割で消えるため、磨いても捨てることになる。第 1 段階で**文言を変えない**ものを次のとおり定める（棚卸し §4-A、付録 B、付録 B2 の対象と同じ）。

- 輸送層の注記ブロックと、その生成元 `tools/skill_src/fragments/*.md`（27 本。どのワークストリームも編集しない）
- `transport-duality.md` 3 本（org-delegate / org-pull-request / pr-watch-pane の references）
- 「Epic #6 Issue G まで未活性」の但し書き
- 「既定 renga」「既定 broker」の表記。生成物の frontmatter `allowed-tools` の列挙面
- 自タブ確立の 2 手段（broker の単一タブモデルと renga の `caller_scope`。棚卸し 付録 C の C2、[`.dispatcher/references/pane-close.md`](../../.dispatcher/references/pane-close.md) Step 3 `:112-121` ほか）
- 本文中の「broker では…」「renga では…」の分岐

例外は 1 つだけ: 行数基準を満たすために、上記を**文言を変えずに** reference ファイルへ移すのはよい。棚卸しが事実誤りと判定した 2 件（`org-pull-request/references/transport-duality.md:7`、`org-conveyor/SKILL.md:88-89`。棚卸し §4-A 7）も第 1 段階では直さず、§5 の第 2 段階の入力に回す。

### 1-2. `worker-claude-template.md` を削除し、条項を生成元へ移す

[`.claude/skills/org-delegate/references/worker-claude-template.md`](../../.claude/skills/org-delegate/references/worker-claude-template.md)（400 行）は作業者に届いていない。作業者の brief は `tools/templates/worker_brief_normal.md` / `worker_brief_self_edit.md` から生成される（`tools/gen_worker_brief.py:46,414-417`）。

削除の前に、写しにしか無い条項を条項ごとに処理する（棚卸し §4-B 2、§4-G #1〜7）。下表の「既定」で進め、W2 が正本と突き合わせて確定する。

| 条項（template の行） | 既定 | 移し先 |
|---|---|---|
| 完了報告前の rebase と「rebase clean」行（`:175-187`, `:323`） | 移す | normal / self_edit の full 節。`org-delegate/SKILL.md:141` が前提にしている |
| uv / pip のキャッシュを `$TMPDIR` へ（`:61`, `:75-77`） | 移す | normal / self_edit の preflight |
| Codex を走らせる Bash の `allowed_domains`（`:59`） | 移す | Codex 節 |
| safety block に掛かったときの回復（`:281-283`） | 移す（1〜2 行） | Codex 節 |
| 検証深度が不明なら窓口に確認（`:191`） | 移す（1 行） | 検証の節 |
| codex の availability check（`:205-213`） | 移す。W5 の `tools/codex_review_gate.sh` に入れられるならそちら | Codex 節 |
| 別ワーカーにレビューを委譲しない（`:293`） | 移す（1 行） | Codex 節 |
| `/tmp` 直下は書けない（`:127`） | 移す（1 行） | preflight |
| doc-audit の denyWrite の穴（`:123`） | W2 が doc-audit ロールの現状を確かめて決める | — |
| 振り返り記録のフォーマット（`:337-354`） | 移す（3 行程度に縮める） | 完了時の節 |
| 生成物 prose の編集（`:163-174`） | 移す（self_edit に 1 行。「`.in` を直して再生成」） | self_edit |
| 監査タスクの反証手順（`:139-151`）、probe 系の credential（`:152-162`） | 捨てる（棚卸しの分類どおり delete） | — |

参照元はすべて生成元へ向け直す。参照元の一覧（`git grep -n worker-claude-template` の結果から、履歴資料の `notes/`・`docs/internal/`・`docs/design/` を除いたもの）:

- `.claude/skills/org-delegate/SKILL.md:141,361`（`.in` 側）、`references/ack-template.md:15`（`.in` 側）、`references/claude-org-self-edit.md:37,39`、`references/instruction-template.md:4,83,222`（`.in` 側）
- `.claude/skills/org-pull-request/SKILL.md:76`（`.in` 側）
- `.dispatcher/references/spawn-flow.md:321`
- `docs/contracts/role-contract.md:13,258`、`delegation-lifecycle-contract.md:12,86,147,152,155,178`、`role-pattern-sandbox-contract.md:549,586,680,1175,1185,1313,1346`、`state-schema-contract.md:18`
- `docs/overview-technical.md:75`、`knowledge/curated/codex.md:102`

### 1-3. hook 化は 6 件を実装する

棚卸し §5-3 の候補 7 件のうち 6 件を実装する。

| # | 内容 | 主な対象ロール |
|---|---|---|
| H1 | spawn 直後の `send_keys`（Enter）を、spawn-prompt-step の判定記録が無ければ deny | 振り分け役 |
| H2 | retro gate の ack 記録が無い worker への `close_pane` を deny | 振り分け役 |
| H3 | `block-relative-close-pane.sh` の matcher に `set_pane_identity` を足す | 窓口・振り分け役 |
| H5 | `tools/codex_review_gate.sh` を新設し、「空の合格」の判定を機械化 | 作業者 |
| H6 | Skill matcher で `codex:rescue` を deny | 作業者 |
| H7 | `block-dangerous-git.sh` に `git add -A` / `git add .` / `git add --all` の deny を足す | 全ロール |

候補 4「窓口の `gh pr merge` を deny」は**実装しない**。CI 通過で取り込みまで自走する恒久承認（2026-06-13）と衝突するため。org-conveyor / work-discovery の「merge 自動化しない」の強調は平文に戻す（棚卸し §5-2 基準 8 の該当行）。

hook で強制済みの決まりは、文章を 1 行（「hook が deny する。代わりに〜」）に縮める。既存の hook（`block-foreground-subagent` / `block-adhoc-pr-watch` / `block-dangerous-git` / `block-relative-close-pane`、`allowUnsandboxedCommands:false`）の分はいつ縮めてもよい。H1〜H7 の分は、該当 hook が統合用ブランチに入ってから縮める。

### 1-4. 振り分け役の指示書を 120 行前後に縮める

[`.dispatcher/CLAUDE.md`](../../.dispatcher/CLAUDE.md)（288 行）から次の 3 節を手順書へ移す（棚卸し §5-2 基準 4）。

| 節 | 行 | 移し先の候補 |
|---|---|---|
| delegate-plan helper | `:33-121`（89 行） | `.dispatcher/references/spawn-flow.md` |
| オンデマンド curator | `:212-258`（47 行） | `.dispatcher/references/pane-close.md` Step 5 |
| work-discovery triage scan | `:259-288`（30 行） | `.dispatcher/references/pane-close.md` Step 6 |

3 節で 166 行あり、移せば残りは 122 行になる。移した先では、入口の CLAUDE.md に「DELEGATE を受けたら spawn-flow を読む」の 1 行を残す。

100 行を超える参照ファイルには冒頭に目次を置き、長い手順書は見出し（`###`）で飛べるようにする。`worker-monitoring.md` は本体が番号付きの太字段落で見出しが無い（棚卸し §5-2 基準 3）ので、Step 1〜6 と 5.1〜5.3 を見出しにする。

ファイルを分割するかは各ワークストリームが決めてよい。基準:

- 分割する: 1 ファイルの中に「毎回読む部分」と「特定の条件でしか読まない部分」があり、後者が 100 行を超える（例: `worker-monitoring.md:319-431` の背景タブ用 113 行、棚卸し §4-F）
- 分割しない: 目次と見出しで部分 Read できれば足りる。分割で参照の階層が 2 段を超える（公式推奨は SKILL.md から 1 段。棚卸し §5-2 基準 3）

### 1-5. 廃止するもの

- dogfood 手順: `org-delegate/references/dogfood-protocol.md`（86 行）、`dogfood-issue-template.md`（79 行）、`registry/dogfood_pending.md`。台帳は 2026-05-08 以降更新が無くエントリも 0 件（棚卸し §4-F）
- herdr の起動前検査: [`.claude/skills/org-start/SKILL.md`](../../.claude/skills/org-start/SKILL.md) Step 0.5（`:175-198`）と frontmatter `:19-20`、`tools/check_herdr_compat.py`、`tools/test_check_herdr_compat.py`。herdr 移行は 2026-10-12 にオーナーが見送った
- 残すもの: broker 専用のスキル（org-down / org-attach）は第 2 段階で broker 版へ移すまで残す。herdr / wezterm の分岐（pr-watch-pane ほか）は輸送層の記述なので 1-1 により触らない

### 1-6. 重複を消す前に正本を確かめる

重複を消すときは、残す側（正本）が正しいことを先に確かめる。次の 2 群は、削る前に正本側を直す。

- 棚卸し §4-G の「誤 delete の疑い」13 件
- 棚卸し §4-D の矛盾（C3 の同名 1 件採用、C9 の `run_in_background`、free-pane 会計、再送回数ほか）と、§5-3 で §4-D に足すべきとされた矛盾

正本の向きは棚卸し §4-C の候補から各ワークストリームが選び、PR 本文に「正本: どのファイルの何行」を書く。

### 1-7. 数値の基準（各ワークストリームの完了条件で使う）

| 対象 | 基準 |
|---|---|
| SKILL.md | 500 行以内 |
| CLAUDE.md（root / `.dispatcher` / `.curator`） | 200 行未満。`.dispatcher/CLAUDE.md` は 120 行前後 |
| 作業者の brief | full 深度で描画した結果が 200 行未満（テンプレートの行数ではなく描画後で測る。棚卸し §5-3） |
| 100 行超の参照ファイル | 冒頭に目次 |
| description | 1 文の「何をするか」とトリガ語。目安 200 字以内。非公式キー `triggers:` は使わない |

## 2. 統合用ブランチと取り込みの規則

- 統合用ブランチ: `modernize/prompts-2026-10`。main の 31a5c40 以降の先端から切る。
- 第 1 段階の PR はすべてこのブランチ宛て（`gh pr create --base modernize/prompts-2026-10`）。main 宛てにしない。
- CI は PR に対して走る（`.github/workflows/tests.yml:3-7` の `pull_request` にブランチの絞り込みが無い）。全項目が通れば、窓口は統合用ブランチへ自動で取り込んでよい。取り込む前に `gh pr checks <PR>` で失敗 0 を確かめる。CI 監視は通常どおり `/pr-watch-pane <PR>`。
- main の変更を統合用ブランチへ取り込む:
  - 方法は **merge**（`git merge origin/main`）。rebase はしない。統合用ブランチには複数の PR が載っており、書き換えると開いている PR が壊れるため。
  - 頻度は毎日 1 回。加えて、main の変更が統合用ブランチ側のワークストリームが持つファイルに触れたときは、その日のうちに取り込む。
  - 作業は窓口がワーカーに委譲し、PR（main → 統合用ブランチ）で取り込む。衝突が生成物（SKILL.md）に出たら、`.in` 側で解決して再生成する。
- 生成物は `.in` を直して再生成する。生成物を手で直さない。
  - 生成物の一覧は `tools/skill_src/manifest.json` の 19 エントリ（SKILL.md 12 本と references 6 本と `.dispatcher/references/worker-monitoring.md`）。
  - 再生成: `python3 tools/gen_skill_prose.py --manifest tools/skill_src/manifest.json`。drift の確認は同じコマンドに `--check`（`tools/gen_skill_prose.py:904`）。CI では `tools/test_gen_skill_prose.py:625` の drift ゲートが同じ確認をする。
  - brief テンプレートを変えたら `tests/fixtures/transport_seam/worker_brief_*.golden.md`（`tests/test_transport_seam.py`）を作り直す。
- `.claude/**` を編集するワークストリームは claude-org-self-edit ロールで派遣し、編集前の承認ハンドシェイク（[`.claude/skills/org-delegate/references/claude-org-self-edit.md`](../../.claude/skills/org-delegate/references/claude-org-self-edit.md) §5、`:95-138`）を通す。承認の `--file` には Issue に書いた持ち主ファイルを列挙する。
- 再生成は `.claude/skills/` へ書き込む。本計画を書いた作業者の sandbox では `.claude/skills` が書き込み拒否に入っていた。最初のワークストリームで、self-edit ワーカーから再生成できるかを着手前に確かめる。できなければ窓口に上げる。

## 3. ワークストリーム

### 3-0. 全ワークストリーム共通

ファイルの持ち主は 1 ワークストリーム（W）だけにする。同じ W の中の Issue も、持ち主ファイルが重ならなければ並列に依頼してよい。

共通の完了条件:

1. 持ち主ファイルが 1-7 の数値基準を満たす
2. 1-1 の輸送層の記述の文言を変えていない（移動だけは可）。PR 本文に「輸送層の記述: 移動のみ / 変更なし」と書く
3. 重複を消したところは、正本が正しいことを確かめてから消した。PR 本文に正本の file:line を書く（1-6）
4. 生成物の drift ゲートと、CI の全項目が通る
5. delete 判定は棚卸しを鵜呑みにせず、実装前に正本と突き合わせて確かめ直した（棚卸しは Codex 未レビュー）
6. Codex レビュー: 上限 3 ラウンド、退出基準は P1 ゼロ
7. 移した理由・経緯の受け皿は `references/rationale.md`、`docs/design/`、`knowledge/curated/` のいずれか。Issue / PR 番号を本文の拠り所にしない

凍結（持ち主なし）: `tools/skill_src/fragments/*.md`、`transport-duality.md` 3 本（`.in` を含む）。

### W1 振り分け役

- 目的: 振り分け役が派遣 1 回で読む量を減らす。
- 持ち主ファイル: `.dispatcher/CLAUDE.md`、`.dispatcher/references/spawn-flow.md`、`pane-close.md`、`loop-directive-guard.md`、`worker-monitoring.md.in`（と生成物 `worker-monitoring.md`）、`.claude/skills/dispatcher-handover/SKILL.md.in`、`dispatcher-resume/SKILL.md.in`（と各生成物）
- やること:
  - 1-4 の 3 節移動と目次・見出し。`worker-monitoring.md:319-431` の切り出しは 1-4 の基準で決める（棚卸し §4-F）
  - 棚卸し §2 の #6（close 判定表の再掲 78 行。正本は `worker-monitoring.md` Step 5.3）、#8（`worker-monitoring.md` の move 60 行）、#13（`spawn-flow.md` の付録 39 行）、#15（triage scan の重複）
  - 棚卸し §3 の各ファイルの表の delete / move / outdated。`worker-monitoring.md:507-518,530-539,589-608` の `py -3` の扱い（棚卸し §4-E）
  - §4-D の矛盾のうち W1 のファイル内のもの: 再送回数（`pane-close:36` と `worker-monitoring:223`）、背景配置（`worker-monitoring.md:321` と `spawn-flow.md:82`）、`--peer-cwd` の照合先、`.dispatcher/CLAUDE.md:71` のモデル既定の理由
  - §4-C のうち W1 のもの: `.dispatcher/CLAUDE.md:171`「自動承認しない」の置き場所、`:164` の `spawn_gate.py audit` をサイクル手順へ移してから削る（§4-G #12）
  - dogfood の参照を外す: `spawn-flow.md:71` の条件行、`worker-monitoring.md:415-419`
  - `spawn-flow.md:321` の `worker-claude-template.md` への参照を `tools/templates/worker_brief_normal.md` に向け直す
  - 振り分け役が root `CLAUDE.md` を読むことの扱い: 祖先の CLAUDE.md は起動時に読み込まれる（https://code.claude.com/docs/en/memory.md 「CLAUDE.md and CLAUDE.local.md files in the directory hierarchy above the working directory are loaded at launch」）。`claudeMdExcludes` 設定（同ページ「Exclude specific CLAUDE.md files」）で外すか、`.dispatcher/CLAUDE.md` に打ち消し文を入れるかを決める（棚卸し §5-2 基準 14、§5-3）。設定で外す場合は振り分け役の設定の生成元を持つ W5 に依頼する
- やらないこと: 輸送層の記述（1-1）。H1・H2 の hook 実装（W5）
- 完了条件: 共通の完了条件。`.dispatcher/CLAUDE.md` が 120 行前後
- 依存: なし（最初に着手できる）。H1・H2 に当たる文章の 1 行化だけは W5 の後

### W2 窓口と委譲

- 目的: 窓口の常時読込を減らし、作業者の brief の正本を 1 本にする。
- 持ち主ファイル:
  - `CLAUDE.md`、`.claude/rules/pr-ci-watch.md`
  - `.claude/skills/org-delegate/SKILL.md.in`、`references/` のうち `ack-template.md.in`、`delegate-flow-details.md`、`rationale.md`、`release-pre-fetch.md`、`codex-design-review.md`、`claude-org-self-edit.md`、`worker-claude-template.md`（削除）、`dogfood-protocol.md`（削除）、`dogfood-issue-template.md`（削除）
  - `.claude/skills/org-pull-request/SKILL.md.in`、`references/rationale.md`、`references/watcher-cleanup.md`
  - `.claude/skills/org-escalation/SKILL.md.in`
  - `tools/templates/worker_brief_normal.md`、`worker_brief_self_edit.md`、`tests/fixtures/transport_seam/worker_brief_*.golden.md`
  - `registry/dogfood_pending.md`（削除）
  - 1-2 の参照元のうち `docs/contracts/*.md`、`docs/overview-technical.md`、`knowledge/curated/codex.md`
  - （上記 `.in` の生成物を含む）
- やること:
  - 1-2 の条項移管と `worker-claude-template.md` の削除
  - brief の Codex 節を `tools/codex_review_gate.sh` の呼び出しと判定 1 行に縮める（棚卸し §5-2 基準 2・9。`worker_brief_normal.md:149-179` ほか）。preflight の事故の日付を外す（`:38-46`）
  - 軽量レーンの正本を org-delegate に置き、`CLAUDE.md` は 1〜2 行の要約にする。**先に** `org-delegate/SKILL.md:65,76` の古い `run_in_background=true` 必須を直す（§4-D C9、§4-G #8）
  - `CLAUDE.md:108-127` の awaiting_user: gate 一覧に `ci_unconfirmed_head_gate`（`org-pull-request/SKILL.md:202`）を足し、未来形の句を直す（§4-D）。各スキルへ分散させるかは §4-C に従って決める
  - `CLAUDE.md:64-65` の打鍵規律を dispatcher-handover 側へ移すか決める（棚卸し §5-2 基準 6）
  - `.claude/rules/pr-ci-watch.md` は hook が強制済みなので 1〜2 行に縮めるか、窓口のスキルへ移す（棚卸し §5-2 基準 7・8）
  - `org-delegate/SKILL.md:174,180` の「4 条件」の数、`:254-264` の 5 つ目のトリガー（§4-D）
  - dogfood の参照を外して 3 ファイルを削除（`org-delegate/SKILL.md:270,360`、`org-pull-request/SKILL.md:81`）
  - `org-pull-request` の裸 name close 3 条件は、W3 が `pr-watch-pane:557-588` を正本に確定した後で参照と追加要件だけにする（§4-C）
- やらないこと: 輸送層の記述。W4 の共有 reference（instruction-template / pane-layout ほか）の中身
- 完了条件: 共通の完了条件。描画後の brief が 200 行未満。`git grep worker-claude-template` が履歴資料（`notes/`、`docs/internal/`、`docs/design/`）以外で 0 件
- 依存:
  - `worker-claude-template.md` の削除は、W1（`spawn-flow.md:321`）と W4（`instruction-template.md.in` の 3 か所）の参照の向け直しが入った後
  - dogfood 3 ファイルの削除は、W1 の dogfood 参照外しが入った後
  - Codex 節の縮小は W5 の H5 の後
- Issue の切り方: W2-1 brief と template（条項移管・削除・golden）、W2-2 org-delegate と org-escalation、W2-3 root CLAUDE.md・rules・org-pull-request、W2-4 dogfood 廃止

### W3 大きなスキル

- 目的: 500 行を超える SKILL.md 3 本を 500 行以内にする。
- 持ち主ファイル: `.claude/skills/org-suspend/SKILL.md.in`、`.claude/skills/pr-watch-pane/SKILL.md.in`、`pr-watch-pane/references/rationale.md`、`.claude/skills/org-start/SKILL.md`（手で保守。`.in` は無い）、`tools/check_herdr_compat.py`（削除）、`tools/test_check_herdr_compat.py`（削除）、新設する各スキルの `references/*.md`（と生成物）
- やること:
  - org-suspend（791 行）: Phase 1 の解説と真理値表を新しい reference へ移す（棚卸し §5-2 基準 1、`:98-180`, `:238-379`）。Group B の自タブ確立は輸送層の記述なので文言を変えずに移す（1-1）。`:294-311` の再掲、`:154-163` の当時の記録（棚卸し §2 #12）。`:595-599` の C3 の矛盾は正本 `renga-error-codes.md:117-120,142`（W4）に合わせて直す（§4-D、§4-G #13）
  - pr-watch-pane（591 行）: 棚卸し §2 #10。`[name_taken]` の 3 条件は `:557-578` を正本にする
  - org-start（543 行）: 棚卸し §2 #7、§5-2 基準 1・2（設計メモ `:263-348`、wall-clock の表 `:433-441` を `references/rationale.md` へ）。Step 0.5 と frontmatter `:19-20` を削除し、`tools/check_herdr_compat.py` とテストを消す（1-5）
  - 3 本の description を 1-7 の基準に合わせる（pr-watch-pane 511 字、org-start の経緯。棚卸し §5-1 基準 12）
- やらないこと: 輸送層の記述の文言（移動のみ可）。capability ゲートの中身（W4）
- 完了条件: 共通の完了条件。3 本とも 500 行以内
- 依存: org-suspend の C3 の修正は W4 の `renga-error-codes.md` の確定後。H3 に当たる文章の 1 行化は W5 の後
- Issue の切り方: W3-1 org-suspend、W3-2 pr-watch-pane、W3-3 org-start と herdr 検査の廃止

### W4 共有 reference

- 目的: 窓口と振り分け役が毎回読む共有 reference を縮める。
- 持ち主ファイル: `.claude/skills/org-delegate/references/capability-first-drive-operational-gate.md`、`renga-error-codes.md`、`pane-layout.md.in`、`instruction-template.md.in`（と生成物）
- やること:
  - capability ゲート（599 行）: 契約条文の引用・書き方の規約・台帳・理由を移す（棚卸し §2 #3、約 160 行）。窓口は起動ごとに（`org-start/SKILL.md:369`）、振り分け役は spawn ごとに（`.dispatcher/CLAUDE.md:81`）全文を Read している。`:104-111` の古い版下限（`tools/check_renga_compat.py:113` の下限は 2.0.0）
  - `renga-error-codes.md`: `:186-313` の擬似シェル（棚卸し §2 #4、§4-F）。版の経緯（`:5,22-24,55-60,70`）
  - `pane-layout.md`: 棚卸し §2 #14。`:22-29` の curator 行（§4-D）
  - `instruction-template.md`: 手動テンプレート `:22-105` と AUTO テンプレート `:197-231` の二重（§4-B 5）。`:222` を短くするなら runtime の strict パースに影響しないことを確かめる。`:4,83,222` の `worker-claude-template.md` への参照を生成元へ向け直す
  - 「現在配備の全 backend は旧版 fallback」の記述は live の renga 2.2.0 と合わない（棚卸し §4-E）。輸送層の記述に当たるかを先に判定し、当たれば触らない
- やらないこと: 輸送層の記述。Group B の自タブ確立の記述
- 完了条件: 共通の完了条件
- 依存: なし。W2 の template 削除と W3 の C3 修正がこの W を待つ

### W5 hook 6 件と設定

- 目的: 1-3 の 6 件を hook と道具にする。
- 持ち主ファイル: `.hooks/*.sh`、`tests/test-*.sh` のうち hook のテスト、`.claude/settings.json`（root）、`tools/org_extension_schema.json`、`tools/check_runtime_schema_drift.py`、`tools/codex_review_gate.sh`（新設）、`.claude/skills/org-setup/SKILL.md`、`org-setup/references/permissions.md`
- やること:
  - H1〜H7（H4 を除く）を実装し、`tests/run-all.sh` にテストを足す
  - 設定の置き場所を先に確かめる。
    - 窓口の hook は root `.claude/settings.json:65-116`。
    - 作業者の設定は ja の `tools/org_extension_schema.json` の `worker_roles` から生成される（`tools/gen_delegate_payload.py:2008-2011`）。
    - 振り分け役の設定は org-setup が `permissions.md` から配る。
  - `tools/org_extension_schema.json` を変える項目（H3 の matcher、H6 の作業者 hook など）は、runtime 同梱スキーマとの照合（`tools/check_runtime_schema_drift.py`、CI は `tests.yml:43`、pin の窓は `:65-66`）に掛かる。ja 専用 hook として照合から外す仕組み（`_JA_ONLY_HOOK_SCRIPTS`、`:185`）で足りるか、runtime とのペアリリースが要るかを最初に確かめる。ペアリリースが要る項目は、runtime を先に出すと main の CI が落ちる。その項目は main 取り込みと同時に出す（runtime-release-with-paired-ja-sync スキル）か、窓口に上げて扱いを決める
  - H1・H2 は、hook が読める判定記録（spawn-prompt-step の結果、`tools/dispatcher_retro_gate.py` の ack）が残っているかを先に確かめる。無ければ記録を残す変更が要る（runtime 側の可能性あり）。その場合は設計を窓口に上げてから実装する
  - org-setup の 2 ファイルは棚卸し §3 の表と §5-2 基準 14（`permissions.md:3-8` と `SKILL.md:107,127` の正典の食い違い）
- やらないこと: 他の W のファイルにある文章の 1 行化（持ち主の W が行う）。H4
- 完了条件: 共通の完了条件。hook ごとに deny されるケースと通るケースのテストがある。`tools/check_role_configs.py` と schema drift の確認が通る
- 依存: なし。W1・W2・W3・W6 の「hook 強制済みの文章の 1 行化」がこの W を待つ
- Issue の切り方: W5-1 H5（codex_review_gate.sh）と H7、W5-2 H3 と H6（スキーマ照合の確認を含む）、W5-3 H1 と H2（判定記録の確認を含む）、W5-4 org-setup

### W6 残りのスキル

- 目的: 残りのスキルを公式推奨に合わせる。
- 持ち主ファイル: 次のスキルの `SKILL.md`（生成物は `.in`）と `references/*.md`: org-conveyor、org-curate、work-discovery、runtime-release-with-paired-ja-sync、skill-audit、skill-eligibility-check、wsl-windows-chrome-screenshot、org-retro、org-resume、secretary-handover、secretary-resume、org-dashboard、goal-setup、org-attention-start、org-attention-stop、org-attach、org-down
- やること:
  - 棚卸し §3 の各ファイルの表。§2 の #9（org-conveyor。§4-G #9 の会計条件を先に揃える）、#11（runtime-release。§4-G #10）
  - `wsl-windows-chrome-screenshot/SKILL.md:12` の非公式キー `triggers:` を消し、トリガ語を description に入れる（棚卸し §5-2 基準 12）
  - org-conveyor / work-discovery の「merge 自動化しない」の強調を平文に戻す（1-3）
  - skill-audit の description の条件（棚卸し §5-2 基準 14）、§4-G #11
  - `org-attention-start:31` の見出しだけの節を消す（本文が無い。輸送層の表記そのものは触らない）
  - org-attach / org-down は輸送層以外の部分だけを整える（1-5）
- やらないこと: 輸送層の記述
- 完了条件: 共通の完了条件
- 依存: runtime-release の pre-fetch 重複は W2 の `release-pre-fetch.md` の確定後。H 系の文章の 1 行化は W5 の後
- Issue の切り方: W6-1 conveyor と work-discovery、W6-2 curate・retro・skill-audit・skill-eligibility-check、W6-3 secretary-handover / resume・org-resume・dashboard・goal-setup、W6-4 attention 2 本・attach・down・wsl-screenshot・runtime-release

### 3-7. 依存の順

```
W1 (dogfood 参照外し / spawn-flow の参照) ─┐
W4 (instruction-template の参照)          ─┼─> W2-1 template 削除、W2-4 dogfood 廃止
W5-1 (H5)                                  ─┘    W2-1 Codex 節の縮小
W4 (renga-error-codes 確定)  ───────────────> W3-1 org-suspend の C3
W3-2 (pr-watch-pane の正本)  ───────────────> W2-3 org-pull-request の close 3 条件
W2 (release-pre-fetch 確定)  ───────────────> W6-4 runtime-release
W5 の各 hook                 ───────────────> 各 W の「hook 強制済みの文章の 1 行化」
```

着手順の目安: W1・W4・W5-1 を最初に並列で出し、W3・W6 を続けて出す。W2 は依存が解けたものから出す。

### 3-8. `/doctor prompt-audit` による前後比較

`/doctor prompt-audit` は指示書の古い記述・存在しない参照・矛盾を報告する（https://code.claude.com/docs/en/memory.md 「Audit your instruction files」。Claude Code v2.1.283 以降）。

1. 第 1 段階の着手前に、窓口が統合用ブランチの初期状態（main と同じ）の作業コピーで `/doctor prompt-audit` を実行する。対象は既定（CLAUDE.md・rules・skills ほか）と `/doctor prompt-audit .dispatcher`
2. 出力を作業者に渡し、`docs/design/prompt-modernization/prompt-audit-before.md` として統合用ブランチに入れる
3. 全ワークストリームの取り込み後、同じ手順で `prompt-audit-after.md` を作る
4. 両者を突き合わせ、残った指摘を「輸送層の記述（第 2 段階へ）」「直す」「直さない（理由付き）」に分けて記録する。「直す」は追加の Issue にする

## 4. 試運転（main に入れる前の実運用確認）

### 4-1. 準備

- 統合用ブランチを本番とは別の親ディレクトリに clone する（例 `~/work/org-trial/claude-org-ja`）。`registry/org-config.md:28` の `workers_dir: ../workers` は repo からの相対パスなので、作業者の作業場所も `~/work/org-trial/workers` に分かれる。
- `.state/`（状態の記録）は clone ごとに別になる。`registry/projects.md` とゴール台帳（`registry/goals/`）は追跡外なので、試験に使うプロジェクトの分だけ手で用意する。
- clone で `/org-setup` を実行して設定を配る。`ORG_TRANSPORT=renga` を明示する。
- 画面: 本番とは別の端末ウィンドウで、clone のディレクトリから `renga --layout ops`（`README.md:63`）を起動する。
- 起動後、試験側の窓口で `list_peers` を実行し、本番の窓口・振り分け役が見えないことを確かめる。見えたら止める。同時に 2 つの組織が動いて記録が混ざった事故が過去にあるため。

### 4-2. 実際の仕事を通す

本番で受けるのと同じ種類の小さい仕事を 2〜3 件、次の順で最後まで通す: 依頼 → 作業者の派遣 → 判断の確認（escalation）→ 完了報告 → PR → CI 監視 → 取り込み → 片付け。1 件は `.claude/**` を編集する self-edit の仕事にする（承認ハンドシェイクを通すため）。

### 4-3. 確認項目

| # | 確認すること | 見る場所 |
|---|---|---|
| 1 | 作業者への指示（brief）が届き、移した条項（rebase、uv/pip ほか）が入っている | 作業者の `CLAUDE.local.md` |
| 2 | 作業者の報告に窓口の ack が返り、作業者が止まらない | 作業者ペイン |
| 3 | 判断の確認が人に上がり、`.state/pending_decisions.json` に記録される | 窓口の画面、`.state/` |
| 4 | `/pr-watch-pane` の監視ペインが立ち、終端通知が届く | 監視ペイン、events テーブルの `ci_completed` |
| 5 | 片付けで作業者ペインと監視ペインが閉じる | 画面 |
| 6 | 振り分け役の監視ループが回り続ける | 振り分け役の画面、events テーブル |
| 7 | 新しい hook が誤って正規の操作を止めない（H1 は承認手順、H2 は振り返り後の close、H7 は `git add -u`） | 各ペイン |
| 8 | 振り分け役のコンテキスト消費が減っている | 下記 4-4 |

### 4-4. 振り分け役のコンテキスト消費の測り方

- 測る時点: 振り分け役の新しいセッションで、最初のワーカーの `DELEGATE_COMPLETE` を送った直後。
- 測り方: 振り分け役のセッション記録 `~/.claude/projects/<cwd を - でつないだ名前>/<session>.jsonl` の最後の assistant メッセージの `usage` から、`input_tokens + cache_creation_input_tokens + cache_read_input_tokens` を取る（Claude Code のセッション記録はメッセージごとに `usage` を持つ）。cwd が clone の `.dispatcher` なので、本番の記録と取り違えない。補助として振り分け役のペインで `/context` を見る。
- 比べる相手: 本番（main）の振り分け役で、同じ時点の値。棚卸しの推定は派遣 1 回で約 19〜22 万トークン（§1-2）。

### 4-5. 問題が出たとき

- 統合用ブランチ上で直す（通常の Issue → PR）。本番は main のまま動いているので影響しない。
- 試験側を止めるときは `/org-suspend` を使い、clone ごと置いておく。

### 4-6. 合格の基準と行き先

合格の基準:

- 4-3 の 1〜7 がすべて満たされる
- 4-3 の 8 で、振り分け役の消費が本番より減っている。目安は棚卸しの見込み（派遣 1 回で約 11.5% 減、§1-4）以上。数値で判定するのはオーナー
- 試運転中に出た問題がすべて直っているか、Issue になっている

合格後の行き先（決めるのはオーナー）:

- (A) main に取り込む: 統合用ブランチ → main の PR を 1 本出す。main への取り込みで英語版リポジトリ `suisya-systems/claude-org` へ通知が飛ぶ（`.github/workflows/notify-en-changes.yml:9-12,23`）ので、1 回にまとまる。W5 でペアリリースが要った項目は、このとき runtime と同時に出す
- (B) 第 2 段階の分割に直行する: 統合用ブランチを分割の起点にする。ja の main は戻り先として残す

## 5. 第 2 段階（リポジトリ分割）の論点

実装計画ではなく、決めるべき論点と現時点の材料を並べる。

| 論点 | 現時点の材料 |
|---|---|
| 共通リポジトリの配り方 | Claude Code のプラグインはスキル・サブエージェント・hook・MCP サーバーを 1 単位で入れられる（https://code.claude.com/docs/en/plugins.md 「A Claude Code plugin is a directory of skills, agents, hooks, MCP servers, or other components」）。役割の指示書（CLAUDE.md）は同ページの部品の一覧に無い。窓口・振り分け役の CLAUDE.md は各版のリポジトリに置くことになる |
| 共通と分岐の境目 | 既存の生成の仕組みが境目にほぼ一致している。共通本文は `.in`（19 本、`tools/skill_src/manifest.json`）、輸送層ごとの差分は `tools/skill_src/fragments/` の 27 本（`*.broker.md` / `*.renga.md` 13 組と中立の 1 本） |
| Python の道具の置き場所 | `tools/`（state_db など）。runtime（claude-org-runtime）へ寄せるものがあるか。作業者の設定は ja の `tools/org_extension_schema.json` から生成していて（`tools/gen_delegate_payload.py:2008-2011`）、runtime 同梱スキーマと照合している（`tools/check_runtime_schema_drift.py`）。分割後にどちらが正本になるか |
| 派生リポジトリ | 英語版 `suisya-systems/claude-org` がある（`.github/workflows/notify-en-changes.yml:3-6,23`。main への取り込みごとに翻訳待ちの通知を送る）。通知の秘密情報 `NOTIFY_EN_PAT` が設定済みかと、英語版をどう分割に追随させるかは未確認。ほかの派生があるかも確かめる |
| どの版を作るか | 棚卸し 付録 B2（輸送層 4 案）。broker＋tmux 版を本番で使うには先に D-1〜D-10 を解く必要がある（付録 B2「broker の実運用でまだ塞がっていない課題」） |
| 第 1 段階で直さなかった輸送層の誤り | `org-pull-request/references/transport-duality.md:7`、`org-conveyor/SKILL.md:88-89`（1-1）。「既定」表記の逆転と生成物の `allowed-tools` の偏り（棚卸し §4-A 2・3） |

## 6. リスクと戻し方、未確認事項

| リスク・未確認 | 対処 |
|---|---|
| 棚卸しは Codex 未レビュー。delete 判定に誤りがありうる（§4-G で 13 件見つかっている） | 各 W が実装前に正本と突き合わせる（3-0 の 5） |
| トークン数は推定（棚卸し §1-1 の式） | 試運転で実測する（4-4） |
| 振り分け役が root `CLAUDE.md` を読んでいるか | 公式資料上は祖先の CLAUDE.md を読む（https://code.claude.com/docs/en/memory.md）。実機は振り分け役のペインで `/memory` を実行して確かめる。W1 の着手時に窓口が行う |
| 再生成が sandbox で書けない（2 の最後） | 最初の W で確かめ、できなければ窓口に上げる |
| W5 のスキーマ変更が runtime とのペアリリースを要する | W5-2 の最初に確かめる。要るなら main 取り込み時に出す |
| H1・H2 の判定記録が無い | W5-3 の最初に確かめる。runtime の変更が要るなら設計を窓口に上げる |
| 統合用ブランチが main から離れすぎる | 毎日 merge する（2） |
| 試験側と本番の組織が混ざる | 別ディレクトリ・別ウィンドウ・`list_peers` で確かめる（4-1） |
| 戻し方 | 第 1 段階は統合用ブランチの中で完結し、main は変わらない。統合用ブランチを捨てれば元に戻る。main 取り込み後に戻すときは、取り込みの merge commit を revert する |

## 7. Issue に切る単位の一覧

窓口はこの一覧から Issue を起票する。PR の宛先はすべて `modernize/prompts-2026-10`。

| # | タイトル案 | W | 依存 | 完了条件の要約 |
|---|---|---|---|---|
| 0 | 統合用ブランチ `modernize/prompts-2026-10` を作り、着手前の `/doctor prompt-audit` を記録する | 共通 | なし | ブランチがある。`prompt-audit-before.md` が入っている |
| 1 | 振り分け役の指示書を 120 行前後に縮め、3 節を手順書へ移す | W1 | 0 | `.dispatcher/CLAUDE.md` が 120 行前後。移した先に目次 |
| 2 | 振り分け役の手順書（worker-monitoring / pane-close / spawn-flow）に目次と見出しを付け、重複・経緯を外す | W1 | 1 | 100 行超に目次。棚卸し §2 #6・#8・#13・#15 を処理 |
| 3 | spawn-flow と worker-monitoring から dogfood と worker-claude-template への参照を外す | W1 | 0 | `spawn-flow.md:71,321`、`worker-monitoring.md:415-419` を処理 |
| 4 | dispatcher-handover / dispatcher-resume を整える | W1 | 0 | 棚卸し §3 の表を処理。drift ゲートが通る |
| 5 | capability ゲートを縮める | W4 | 0 | 約 160 行の move を処理。目次 |
| 6 | renga-error-codes と pane-layout を縮める | W4 | 0 | 擬似シェルと版の経緯を処理。C3 の正本を確定 |
| 7 | instruction-template の二重テンプレートを整理し、worker-claude-template への参照を外す | W4 | 0 | 手動と AUTO の二重を解消。参照 3 か所を生成元へ |
| 8 | Codex の「空の合格」判定を `tools/codex_review_gate.sh` にし、`git add -A` を hook で止める（H5・H7） | W5 | 0 | 道具とテスト。hook のテスト |
| 9 | close_pane hook を set_pane_identity に広げ、codex:rescue を hook で止める（H3・H6） | W5 | 0 | スキーマ照合の扱いを確定。hook のテスト |
| 10 | spawn 直後の Enter と、振り返り前の close を hook で止める（H1・H2） | W5 | 0 | 判定記録の有無を確定。hook のテスト |
| 11 | org-setup と permissions.md の正典を揃える | W5 | 0 | 正典が schema 1 本。目次 |
| 12 | 作業者 brief へ条項を移し、worker-claude-template.md を削除する | W2 | 3, 7, 8 | 1-2 の表を処理。描画後 200 行未満。golden 更新。参照 0 件 |
| 13 | org-delegate と org-escalation を整え、軽量レーンの正本を org-delegate にする | W2 | 0 | §4-D C9 を先に修正。棚卸し §3 の表を処理 |
| 14 | root CLAUDE.md・pr-ci-watch rule・org-pull-request を整える | W2 | 13, 17 | gate 一覧の修正。hook 強制済みの文章を 1 行に |
| 15 | dogfood 手順を廃止する | W2 | 3 | 3 ファイル削除。参照 0 件 |
| 16 | org-suspend を 500 行以内にする | W3 | 6 | 500 行以内。輸送層は移動のみ |
| 17 | pr-watch-pane を 500 行以内にする | W3 | 0 | 500 行以内。name close 3 条件の正本を確定 |
| 18 | org-start を 500 行以内にし、herdr の起動前検査を廃止する | W3 | 0 | 500 行以内。`tools/check_herdr_compat.py` とテストを削除 |
| 19 | org-conveyor と work-discovery を整える | W6 | 0 | 会計条件を揃える。merge 自動化の強調を平文に |
| 20 | org-curate・org-retro・skill-audit・skill-eligibility-check を整える | W6 | 0 | 棚卸し §3 の表を処理 |
| 21 | 窓口の引き継ぎ系・org-resume・org-dashboard・goal-setup を整える | W6 | 0 | 棚卸し §3 の表を処理 |
| 22 | attention 2 本・org-attach・org-down・wsl-screenshot・runtime-release を整える | W6 | 0（runtime-release の pre-fetch は 13） | `triggers:` 削除。棚卸し §3 の表を処理 |
| 23 | hook 強制済みの文章を 1 行に縮める（H1〜H7 の分の残り） | 各 W | 8, 9, 10 | 該当ファイルの持ち主が処理。持ち主ごとに分けて起票 |
| 24 | main の変更を統合用ブランチへ取り込む（定常） | 共通 | 0 | 毎日 1 回の merge PR |
| 25 | 第 1 段階後の `/doctor prompt-audit` と突き合わせ | 共通 | 1〜23 | `prompt-audit-after.md` と分類 |
| 26 | 試運転（試験用組織での 2〜3 件の通し） | 共通 | 25 | 4-3 の表を埋めた記録。合格判定はオーナー |
