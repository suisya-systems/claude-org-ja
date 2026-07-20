# プロジェクト台帳（project dossier）と実行プロファイル — 設計

> **ステータス**: 第 1 段（台帳 + 実行プロファイル）実装中
> **一次入力**: Issue #744（`.state/drafts/issue-project-dossier.md`）
> **設計レビュー**: Codex gpt-5.5（重大 1 + 要対応 4 + Minor 1 + Nit 1）。本書はその全指摘への回答を含む
> **実装**: [`tools/project_dossier.py`](../../tools/project_dossier.py) / [`tools/gen_delegate_payload.py`](../../tools/gen_delegate_payload.py) / [`tools/gen_worker_brief.py`](../../tools/gen_worker_brief.py)
> **テスト**: [`tests/test_project_dossier.py`](../../tests/test_project_dossier.py) / [`tests/test_gen_delegate_payload.py`](../../tests/test_gen_delegate_payload.py)
> **依存ドキュメント**: [`docs/contracts/role-contract.md`](../contracts/role-contract.md) / [`docs/contracts/knowledge-curation-contract.md`](../contracts/knowledge-curation-contract.md)
> **スコープ外**: 第 2 段（プロジェクト orchestrator）。本書は台帳の静的構造と配線のみを扱う

---

## 1 背景と確定制約（本設計が覆さない前提）

現行の「プロジェクト」は [`registry/projects.md`](../../registry/projects.md) の 1 行（通称・パス・説明）にすぎず、構造を持たない。その結果プロジェクト固有の産物に育つ場所がなく、2026-07-18/19 の EN ミラーベルト 1 セッションだけで行き場のない産物が 3 つ発生した（セッション寿命の場所に置かれたスコープ契約 / Lead の個人メモリへ退避した運用知見 / 全体キューで待機するプロジェクト専属スキル候補）。

同セッションの 5 派遣を分析すると、実行設定は毎回 Lead がその場で決めていたが、実際にはタスク類型ごとにほぼ同じ形に収束していた。設定は暗黙に実在しており、一級市民化できる。

本設計が**覆さない**確定制約:

- **INV-1: プロファイルが設定するのは実行であって承認ではない。** マージ事前承認に相当するフィールドを持たせない。常設スコープ契約は人間承認つきの独立文書とし、プロファイルからは参照のみ。
- **INV-2: 過剰パラメータ化禁止。** 実測で変動した軸のみを入れる。未使用プロファイルは腐る前提（実測 2 回目で書く / 形骸化したら削除可）。
- **INV-3: 憲法層の変更であり実行基盤（ペイン / デーモン）に依存しない。** 将来の基盤刷新を生き残る。
- **INV-4: プロファイルが黙って何もしない軸を作らない。** 受け口の無い軸は宣言時に警告を出すか、そもそも拒否する（§4.3）。
- **INV-5: スキル = 手順書 / プロファイル = 実行設定 / ノート = 知見**、の三分を維持する。

---

## 2 台帳の構造

```
registry/projects/<slug>/
  charter.md            # 憲章: 何をもって良しとするか・制約・リポジトリ・慣習
  notes/<topic>.md      # 運用知見（1 ファイル 1 現象）
  profiles/base.toml    # 実行プロファイル基底
  profiles/<class>.toml # タスク類型別（base を継承・上書き）
  contracts/README.md   # 常設スコープ契約への「参照のみ」（§3）
```

`<slug>` は [`registry/projects.md`](../../registry/projects.md) の通称と一致させるが、台帳ディレクトリの存在は表の行とは独立に解決される（表に行が無い slug でも台帳は引ける）。台帳は `registry/` 配下の**サブディレクトリ**であり、`registry/projects.md` を読む既存パーサ（[`tools/registry_parser.py`](../../tools/registry_parser.py)）は明示ファイルパス指定のため影響を受けない。

---

## 3 `contracts/` は参照のみ（設計レビュー Blocker への回答）

**`contracts/` には契約の実体を置かない。人間承認済みの独立した契約文書への参照（リンク）だけを置く。**

理由は表面的な整理ではない。種データ元の `.state/conveyor/scope-contract.md` には**人間によるマージ事前承認の override** が含まれ、`approved_at` は「2026-07-18（JST セッション）」、`max_iterations` / `time_budget` は「本セッション」、include 述語は既にクローズ済みの特定 PR / Issue 番号の列挙である。これは**期限切れのセッション成果物**であって、プロジェクトの恒久方針ではない。実体を台帳へコピーすると「セッション限りの特例」が「恒久的プロジェクト方針」へ化ける（INV-1 の直接的な破り方）。

規約:

- `contracts/` に置いてよいのは `README.md` 1 枚のみ。内容は**リンクと 1 行説明の索引**に限る。
- 旧スコープ契約は「履歴資料」と明示ラベルしたリンク参照のみ。「このプロジェクトの契約」として提示しない。
- 新しい常設スコープ契約が要るなら、**独立文書として人間承認を取り直す**。台帳へのコピーはその代替にならない。
- 実行系は `contracts/` を一切読まない。[`tools/project_dossier.py`](../../tools/project_dossier.py) はプロファイル解決でも brief 埋め込みでも `contracts/` を参照しない。

**機械的な歯止め**: プロファイル解決時に `contracts/` 配下へ `README.md` 以外のファイルが在れば警告 `dossier: contracts/ must hold references only` を出す（`plan.warnings` に載り preview に表示される）。散文だけの規約にせず、実体配置が観測されたら見えるようにする。

---

## 4 実行プロファイル

### 4.1 解決順序

```
profiles/base.toml  <  profiles/<class>.toml  <  --from-toml  <  CLI フラグ
（弱）                                                              （強）
```

- **プロファイル**はプロジェクト水準の既定値。
- **`--from-toml`** はタスク個別の明示設定なのでプロファイルより強い。
- **CLI フラグ**が最強。既存の「CLI 値が `None` でないときだけ上書き」というセンチネル方式（[`tools/gen_delegate_payload.py`](../../tools/gen_delegate_payload.py) `_gather_plan_kwargs`）をそのまま拡張する。

CLI は `--profile <slug>/<class>` または `--profile <slug>`（base のみ、明示的に許可）。

### 4.2 未定義クラスのフォールバック（設計レビュー Minor への回答）

**`--profile <slug>/<未定義クラス>` は警告つきフォールバックではなく エラー（`SystemExit`）とする。** エラーメッセージには当該台帳で利用可能なクラス一覧を列挙する。

理由: 静かに `base.toml` へ落ちると「プロファイル済みに見えるが実際は素の brief」という成果物が出る。これは INV-4 が禁じる「黙って何もしない」と同じ失敗様式であり、`ci-fx` のような打ち間違いを検出できない。EN ミラー運用でも解決規則の無い未知クラスの実運用解は「黙った既定」ではなく**即エスカレーション**だった。base のみを使いたい場合は `--profile <slug>`（クラス無し）で**明示的に**要求する。

台帳ディレクトリ自体が存在しない場合も同様にエラー。

### 4.3 軸 → 配管の対応表（設計レビュー Major への回答）

第 1 段では**受け口が既に在る軸だけを配線**する。受け口の無い軸は下表で「Stage 1 未配線」と明示し、プロファイルで宣言された場合は**警告**を出す（黙って何もしない軸を作らない = INV-4）。

| 軸 | プロファイルキー | 配線先（Stage 1） | 状態 |
|---|---|---|---|
| 検証深度 | `[task].verification_depth` | `build_delegate_plan(verification_depth=)` → brief の Codex ゲート散文 | **配線済** |
| commit prefix | `[task].commit_prefix` | `build_delegate_plan(commit_prefix=)` → brief | **配線済** |
| ブランチ様式 | `[task].branch_style` | `{task_id}` / `{project_slug}` 展開 → `branch_override` → `layout.planned_branch` | **配線済** |
| ナレッジ参照 | `[references].knowledge` | `references_knowledge` → brief「ナレッジ参照」 | **配線済** |
| 実装ガイダンス | `[implementation].guidance` | `implementation_guidance` → brief「実装ガイダンス」 | **配線済** |
| 並列注記 | `[parallel].notes` | `parallel_notes` → brief「並列タスクとの干渉」 | **配線済** |
| プロジェクト説明 | `[project].description` | `project_description_override` | **配線済** |
| 台帳埋め込み | `[dossier].embed_charter` / `[dossier].embed_notes` | 本 Stage で新設する brief ブロック（§5） | **配線済（新設）** |
| モデル | `model` | 受け口なし。`DelegatePlan` にもフラグにも無く、EN の実測 7 件いずれにも記録が無い | **Stage 1 未配線・将来対応** |
| Codex ラウンド上限 | `codex_round_max` | 受け口なし。brief テンプレートに散文で「既定上限 3」が固定 | **Stage 1 未配線・将来対応** |
| PR 形状 | `pr_shape` | 受け口なし | **Stage 1 未配線・将来対応** |
| Codex レビューゲート | `codex_review` | 受け口なし。現状は `verification_depth` が実質同義を担う | **Stage 1 未配線・将来対応** |
| 権限モード | `permission_mode` | org 全体設定（[`registry/org-config.md`](../../registry/org-config.md)）。同ファイルの警告どおり値は各スキル / 契約に `auto` リテラルで焼き込まれており、プロジェクト単位の上書きは黙って desync する。EN 実測 7/7 が `auto` で変動ゼロ | **禁止（エラー）** |
| マージ事前承認相当 | `merge_preapproved` 等 | — | **禁止（エラー・INV-1）** |

キーの扱いは 4 分類:

1. **配線済** — 解決して適用する。
2. **未配線（既知）** — 受理するが `dossier: axis '<key>' is not wired in Stage 1 (no downstream surface)` を警告。
3. **禁止** — `SystemExit` で拒否。
4. **未知キー** — `SystemExit` で拒否（打ち間違い検出）。

Nit への回答: `verify_skill` は機構名であって挙動名でないため、将来配線する際の名称を **`codex_review`（bool）** に改める（「生成 brief に Codex レビューゲートを含めるか」の意）。`verification_depth` と意味が重なる点も含め、配線は第 2 段以降で実測が出てから判断する。

`verification_depth` と「`/verify` 実施要否」は別軸である点に注意（EN の実測: スコープ契約は翻訳作業に `/verify` 不要と書いたが、実際の翻訳バッチワーカーは `verification_depth = full` で走った）。片方から他方を導出しない。

### 4.4 プロファイルに入れてはいけないもの（種データ仕分けの原則）

**Lead / PR ゲートの計画コンテキストはワーカー実行プロファイルの knob ではない。** 特に EN の「マージ直列制約」（branch up-to-date 必須 + auto-merge 無効のため N 件マージは直列になる）は、出典自身が「Lead 側の処理時間として見込む」と書いているとおり**マージゲートの計画事実**であり、1 タスクを実行するワーカーはそれを観測も行動もできない。同じ理由で以下もプロファイル外（charter 散文か、第 2 段の orchestrator の領分）:

- 並列度 / 失敗ジョブ種別によるワーカー分割の判断
- 委譲先決定前のパイプライン所在判定（`sync_classifier`）
- `CI_COMPLETED indeterminate` の扱い（Lead / pr-watch の関心事）

ワーカーから見える半分だけは実装ガイダンスとして入れてよい（例「重なるドキュメントは 1 ブランチ 1 PR に束ねる」）。

---

## 5 charter / notes の brief 埋め込み仕様（設計レビュー Major への回答）

現行 brief はナレッジを**パス列としてのみ**埋め込み、内容は埋めない。台帳の狙いは「同一プロジェクトの 2 回目以降のワーカーが温かい状態で始まる」ことなので内容埋め込みが要るが、毎回のタスクに古い / 無関係なプロジェクト記憶を丸ごと流し込むと context 汚染になる。トレードオフに対する第 1 段の解:

**選択基準**

- `charter.md` は**常に全文**埋め込む（`[dossier].embed_charter = false` で明示的に切れる）。憲章は意図的に短く・安定であり、これが warm start の本体だから。
- `notes/` は**グロブしない**。プロファイルの `[dossier].embed_notes = ["...md", ...]` で**明示列挙されたものだけ**を埋め込む。列挙されない notes は従来どおりパス参照（`[references].knowledge`）に留める。
  - グロブにすると notes が増えるたび全 brief が単調に膨らむ。類型ごとに要る知見だけを選ぶのがプロファイルの仕事。
- 存在しない notes を列挙した場合は警告（黙って落とさない）。パス traversal（`..` / 絶対パス）は拒否。

**順序**

charter → `embed_notes` の宣言順。宣言順そのものが「読ませたい順」を表す。

**サイズ上限**

- 1 ファイルあたり **4,000 文字**
- 台帳ブロック合計 **12,000 文字**

上限超過は**行境界で切り詰め**、`（以下省略 — 全文は <repo-root パス> を参照）` のマーカーを必ず残す。黙って切らない。切り詰めが起きたら警告も出す。上限に当たり続けるなら charter が長すぎるか notes の選び方が粗いというシグナルであり、値を上げる前に台帳側を直す。

**描画**

新設の `<!--BEGIN:project_dossier-->` ブロックを両テンプレート（`worker_brief_normal.md` / `worker_brief_self_edit.md`）の「ナレッジ参照」直前に置く。台帳が解決されなかった場合はブロックごと消えるため、既存 brief の出力は**バイト等価**で不変。

---

## 6 org-curate / org-retro との関係

### 6.1 org-retro: プロファイル成長則

**同一類型の 2 回目の実測時にプロファイル化する。** 先回りで書かない（skill-eligibility の `raw_reappearance` と同じ規律）。1 回しか観測されていない類型は charter に「観測済み・未プロファイル」として名前だけ残す。

EN の実測でいえば、`ci-fix`（3 回）と `translation-pass`（2 回）はプロファイル化の閾値を満たし、`release`（1 回）と `canonical-name-sweep`（1 回）は満たさない。

### 6.2 org-curate: notes/ への振り分け

**この配線は本設計の時点で未確定であり、人間判断待ちである。**

[`docs/contracts/role-contract.md`](../contracts/role-contract.md) § Role: curator の Hard prohibitions は「curator は `.state/` / `registry/` / worker ディレクトリに書いてはならない — 書き込み面は `knowledge/curated/` とスキル候補キューのみ」と定めており、[`docs/contracts/knowledge-curation-contract.md`](../contracts/knowledge-curation-contract.md) にも同一の禁止が再掲されている。台帳の notes/ は `registry/projects/<slug>/notes/` に在るため、**キュレーターに notes/ への書き込みを許すと批准済み契約 2 本に正面から違反する**。

選択肢（人間判断待ち）:

- **A** 契約 2 本に日付つきの狭域 carve-out を追記する（既存のワーカー向け carve-out と同じ体裁の前例あり）。
- **B** 台帳の notes/ を `registry/` の外へ移す（Issue #744 の定めた構造を崩す）。
- **C** 書き込み主体を分離する。org-curate は**振り分け判定と note 草稿の提案**までを行い、`registry/projects/<slug>/notes/` への実書き込みは `registry/` を所有する**窓口**が行う。契約文面の改訂ゼロで矛盾も残らない。

判断が下りるまで、スキル散文への振り分け分岐追加は保留する。**振り分け基準自体**（何がプロジェクト固有ノートで、何がグローバル curated 知識か）は書き込み主体と独立に定義できるため先に確定させる:

- **プロジェクト固有ノート（notes/）**: 特定リポジトリの資産・慣習・罠に依存し、他プロジェクトへ持ち出すと誤りになる知見。判定の目安は「このプロジェクト名を伏せたら意味が通らないか」。
- **グローバル curated 知識（`knowledge/curated/`）**: 複数プロジェクトで再現する組織横断の知見（ツールの挙動、レビュー運用、フレームワークの罠）。
- 両方に跨る場合はグローバル側を正本とし、notes/ からは**参照**する（内容を二重化しない）。

---

## 7 安全レール（不変条件）

- **INV-1**（承認を設定しない）: 禁止キー分類（§4.3）で機械的に拒否する。
- **INV-4**（黙って何もしない軸を作らない）: 未配線軸は警告、未知キーはエラー。
- **contracts/ 参照のみ**: 実体配置は警告として観測可能（§3）。
- **既存出力の不変性**: `--profile` 未指定時、生成される brief / DELEGATE body は変更前とバイト等価。既存ゴールデンが回帰検出を担う。
- **純粋性**: 台帳の解決と読み取りは読み取り専用。`build_delegate_plan` の「純関数」性質を壊さない。

### 7.1 検証可能性

- 解決順序（base → class → `--from-toml` → CLI）の各段が勝つことを個別に assert する。
- 未定義クラス / 未知キー / 禁止キー / 未配線軸のそれぞれで、意図した挙動（エラー or 警告）を assert する。
- 埋め込みのサイズ上限・切り詰めマーカー・順序を assert する。
- `--profile` 無指定時に既存ゴールデンが不変であることを assert する。

---

## 8 種データ（初号台帳 `claude-org-en`）の仕分け

出典は `knowledge/curated/en-mirror-maintenance.md`（混成ノート）と `.state/conveyor/scope-contract.md`（**履歴参照のみ**、§3）。混成ノートは「ワーカー実行ルール / Lead 側マージタイミング / CI パターン / 翻訳ルール / フックの罠」が同居しているため、以下へ分割移送する。

> **配置は現在ブロック中**。`.hooks/block-org-structure.sh` は out-of-org ワーカーによる `registry/` 配下の**新規作成**を拒否する（Issue #736 の緩和は既 tracked ファイルの編集のみ）。本節は配置許可が下りた時点でそのまま流し込めるよう、仕分け結果を確定させたものである。

### 8.1 `charter.md`（安定・変化が稀なもの）

- `claude-org-en` とは何か: ja の英語 auto-mirror。runtime 系ファイルは機械ミラー、docs / skills は翻訳パスで追随する。
- **repo 名の罠**: 作業ツリー名は `claude-org-ja` だが実 origin は `https://github.com/suisya-systems/claude-org`（`-ja` なし）。`-ja` 付きの GitHub URL は 404 する（v1.0.0 CHANGELOG で実害）。URL を書く前に必ず `git remote get-url origin` で確認する。Codex レビューはこの種の誤りを検出しない（コード正当性の欠陥ではないため）。
- **EN 適応は意図的に最小限**: URL のみ EN 化し、banner / コメント / `TARGET_DIR` は据え置く。修理ついでに他の文字列を直さない（将来のミラー diff を小さく保つ）。
- **reverse-drift 則**: 機械 import された ja-canonical ファイル内の Codex 指摘は EN 側で直さない。blob SHA 比較で ja main に同欠陥があることを確認 → Lead 報告 → PR の Known limitations に記録 → ja 側 Issue を提案（auto-mirror で修正が還流する）。
- **観測済み・未プロファイルの類型**: `release` / `canonical-name-sweep`（各 1 回のみ）。成長則（§6.1）により 2 回目までプロファイル化しない。

### 8.2 `notes/`（1 ファイル 1 現象）

| ファイル | 内容（出典行） |
|---|---|
| `mirror-ci-failure-triage.md` | まず切り分ける（複数系統ある）+ 系統 1 stale-base × 範囲 pin runtime → schema drift（修正は `origin/main` マージのみ、コード変更不要）+ 系統 2 ja-only リテラル × EN ローカル資産 / alias skew + 系統 3 installer clone-URL の機械ミラーによる smoke red（定型修正 2 行 + ローカル dry-run 再現手順） |
| `translation-pass-repo-quirks.md` | skill drift ゲートは EN では非活性だが `.in` 持ちスキルは `.md` と同時ステージが要る / `audit_link_paths.py` の EN ベースラインは約 110 違反なので green ではなく前後差分で見る / 同一ドキュメントに触る複数 Issue は後のマージ SHA の最終状態を 1 回訳す / Codex はロケール混入を検出する（修正は実行不能プレースホルダ化 + EN mirror note）/ unknown-class ファイルは解決規則が無いので即エスカレーションし待ち時間は翻訳を並行継続 |
| `belt-operations-retro.md` | ベルト運用の実績知見。**Lead 段取りの記録**であってワーカー実行設定ではない旨を冒頭に明記する |
| `hook-false-fire-on-registry-edit.md` | `block-org-structure.sh` が EN ミラー repo 自身の `registry/` 編集に誤発火する件（本タスクで新規作成側も未解消と判明。§9 参照） |

### 8.3 `profiles/`（実用 2 本のみ）

実測値のみを書く。EN の archived worker 7 件から読み取れた値が根拠であり、記録の無い軸は**書かない**。

- **`base.toml`**: `[project].description` のみ。`permission_mode` は書かない（実測 7/7 が `auto` で変動ゼロ = 軸ではない。かつ §4.3 で禁止）。`pattern` も書かない（同一類型が A と B に割れており類型安定でない — リゾルバに任せる）。
- **`ci-fix.toml`**: `verification_depth = "full"`（実測 3 件一致）、`commit_prefix = "fix(mirror):"`（`fix(installer):` から収束）、`[dossier].embed_notes = ["mirror-ci-failure-triage.md"]`、`[implementation].guidance` に「まず 3 系統のどれかを切り分ける。系統 1 は `origin/main` マージのみで直る」。
  **`branch_style` は付けない** — この類型は既存の `auto-mirror/ja-pr-<N>` ブランチ上で作業するため、ブランチはタスク側が渡す。架空のリテラルを置くと能動的に誤りになる。
- **`translation-pass.toml`**: `verification_depth = "full"`（後発の収束値。2026-07-16 の初期実行は `minimal` だった旨をノートに残す）、`commit_prefix = "docs:"`、`branch_style = "docs/{task_id}"`、`[dossier].embed_notes = ["translation-pass-repo-quirks.md"]`、`[parallel].notes` に「重なるドキュメントは 1 ワーカー 1 ブランチ 1 PR に束ねる」。

### 8.4 プロファイルに入れないもの（明示的な除外）

- **マージ直列制約** — 出典自身が「Lead 側の処理時間として見込む」と書く PR ゲートの計画事実。§4.4 のとおり charter 散文か窓口の段取りへ。
- 並列度 / 失敗ジョブ種別によるワーカー分割の判断、`sync_classifier` によるパイプライン所在判定、`CI_COMPLETED indeterminate` の扱い。いずれも Lead / pr-watch の関心事。
- `permission_mode`（§4.3 で禁止）。
- `CI_COMPLETED indeterminate` の回避策は**コピーせず** `knowledge/curated/pr-ci-monitoring.md` を参照する。並行タスクがこの挙動自体を修正中であり、台帳に焼き込むと陳腐化するため。

### 8.5 `contracts/README.md`

`.state/conveyor/scope-contract.md` への**履歴資料としてのリンク 1 本のみ**。実体はコピーしない（人間のマージ事前承認 override を含むため — §3）。

---

## 9 スコープ外 / 将来課題

- 第 2 段（プロジェクト orchestrator）。台帳が orchestrator の直列化された状態、orchestrator は台帳が目覚めた姿、という対で設計する。発動条件つき backlog。
- 未配線軸（`model` / `codex_round_max` / `pr_shape` / `codex_review`）の配線。実測で変動が観測されてから。
- `.hooks/block-org-structure.sh` は out-of-org ワーカーの `registry/` 配下**新規作成**を拒否するため、ワーカーが台帳を新規に作れない（Issue #736 の緩和は既 tracked ファイルの編集のみ）。台帳運用を回すには別途この誤発火の解消が要る。
