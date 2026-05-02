# Phase 5 (Layer 3 = orchestration glue) 設計議論用 Q&A 草案

- 作成日: 2026-05-03
- 関連 Issue: ja#（Layer 3 抽出可否、未起票）
- 入力: Phase 4 の `phase4-questions-2026-05-02.md` / `phase4-decisions-2026-05-02.md`
- 性質: **質問のみ**。回答は窓口（Lead）判断。Phase 3 / Phase 4 と同じく measurement-first で進める。
- 数: 12 問（Phase 4 と粒度・章立てを揃える）。
- **重要**: 本 Q&A は **「抽出するための質問」ではなく**、「Layer 3 を抽出すべきか / すべきでないか / どう抽出するか」を決めるための measurement-first 質問。最初の 2 問は kill 判定（= 抽出しない）にも進行判定（= 抽出する）にも耐える設計の measurement Q。最後の 3 問は公開 OSS 化判断（抽出形態 / public vs private / namespace・license・governance）。
- Layer 3 候補の中身: `.claude/skills/` (org-curate / org-dashboard / org-delegate / org-resume / org-retro / org-setup / org-start / org-suspend / skill-audit / skill-eligibility-check)、`.dispatcher/CLAUDE.md`、`.curator/CLAUDE.md`、`.hooks/` の **org-shaped 残置分** (block-workers-delete / block-dispatcher-out-of-scope / block-org-structure / block-git-push 等。Phase 4 で core-harness に移管された generic 分は除外)、`dashboard/` (SPA + server.py)。Phase 4 で `claude-org-runtime` に bundle 済みの dispatcher / curator prompt template の英語版とは **重複領域** がある（→ Q5, Q8 で扱う）。

---

## Q1. 抽出根拠の測定 — そもそも consumer / 痛点はあるか（kill ゲート）

Layer 1 (core-harness) と Layer 2 (claude-org-runtime) は「framework primitives」「runtime SoT」という明確な抽出動機があった。Layer 3 = orchestration glue については、抽出する積極理由が現時点で曖昧で、まず measurement で kill 判定にかける必要がある。

- **この時点で測定すべき事実**:
  - claude-org-ja 以外で `.claude/skills/org-*` を **実際に読み込んでいる** Claude Code consumer の数（en port を含めて何件か。fork / star ではなく実体）
  - 過去 6 ヶ月で Lead / worker から「skill / dispatcher prompt の OSS 化要望」が出た回数（Issue / Slack / セッションログ）
  - 直近 3 ヶ月の `.claude/skills/` 内ファイルの churn（追加 / 改稿 / 削除回数）— 高 churn = まだ glue 層が安定していない sign
  - `.curator/CLAUDE.md` / `.dispatcher/CLAUDE.md` の Phase 4 v0.1 prompt template との **diff 量** — 大差なら Layer 2 で十分、小差なら Layer 3 必要性低下
- **選択肢**:
  - (a) **抽出しない (kill)**: consumer < 2 かつ要望 0 件なら、Layer 3 は claude-org-ja に永続的に残す reference 配布物として確定し、Phase 5 を close する
  - (b) **保留 (defer)**: measurement だけ取って 1 quarter 後に再評価。Layer 2 v0.1 release が glue 層需要に与える影響を見てから判断
  - (c) **抽出前提で設計に入る**: consumer 候補が見えなくても、claude-org-ja 自身の整理目的（reference vs runtime の境界明確化）で抽出する

## Q2. measurement-first の具体プラン — 何をいつ計るか

Phase 4 Q12 と同じく、コード extract 前に取るべき数値の合意を取る。Layer 3 は Layer 2 と違って「kill 判定」が現実的選択肢なので measurement の重みが Phase 4 より大きく、本 Q を Q1 と一体で先に決める。

- **この時点で測定すべき事実 (= measurement の候補一覧)**:
  - (i) **consumer 候補 inventory**: claude-org-ja 以外で `.claude/skills/` を採用しうる org / repo の数（Q1 の中核）
  - (ii) **skill churn**: 過去 6 ヶ月の `.claude/skills/` 内ファイル commit 数、変更行数、stable / unstable 分類
  - (iii) **skill 間 dependency graph**: Q3 の境界判断材料
  - (iv) **prompt diff (Phase 4 v0.1 vs claude-org-ja current)**: Q5 の重複判定材料
  - (v) **org-shaped hooks 改訂頻度**: Q6 の判断材料
  - (vi) **dashboard 利用度 (ja / en port)**: Q7 の判断材料
  - (vii) **ja↔en drift in skill / prompt**: Q9 の SoT 判断材料
  - (viii) **claude-org-ja 残余 LOC 予測**: Q10 の reference 配布物 positioning 材料
- **選択肢**:
  - (a) **(i) + (ii) を最優先で取り、kill 判定 (Q1) を先に下す**。kill されたらこの doc が結論として残る。kill されなかったら (iii)〜(viii) は extract 着手後に追加で取る（Phase 4 Q12=b 流派）
  - (b) **(i)〜(viii) すべて取ってから設計に入る**。measurement worker の負荷は大きいが、Layer 3 は Phase 4 より不確実性が高いので妥当（Phase 4 Q12=a 流派）
  - (c) **measurement 最低限 ((i) のみ)** で Q1 だけ判定し、proceed なら即 Step B 相当の skill schema 抽出 PR を試作（Phase 4 Q12=c 流派、measurement-first を緩める）

実施主体（worker 派遣 / Lead 手作業 / 自動 routine）も Phase 4 と同様に併せて決める。

## Q3. 抽出する場合のスコープ境界 — Option α / β / γ のどれを起点にするか

Q1 で (b)/(c) を取った場合、Layer 3 の MVP 境界をどこに置くか。

- **この時点で測定すべき事実**:
  - 各 skill の inter-dependency（`org-delegate` が `org-suspend` を呼ぶ等）。independent / coupled の比率（Q2-(iii)）
  - skill ごとの「core-harness primitives + claude-org-runtime API のみで動くか / claude-org-ja 固有の path に依存するか」の依存度ヒートマップ
  - dashboard SPA の `org-state.json` schema 依存度（Phase 4 Q9=c で schema は Layer 2 に置く決定との整合）
- **選択肢**:
  - (a) **narrow**: `org-delegate` と `org-start` の 2 skill だけ extract（最小可動部 = "ワーカー派遣の reference"）
  - (b) **wide**: 10 skill 全部 + dispatcher / curator prompts（Phase 4 v0.1 から重複分は移管）+ dashboard SPA + org-shaped hooks をまとめて 1 リポにする
  - (c) **split**: skill-only / dashboard-only / prompt-template-only に **3 分割** して Layer 3a/3b/3c とする（個別 release cycle）

## Q4. API 安定性ゲート — Layer 2 と同じ流派を取るか

Phase 4 Q2=b で Layer 2 は「0.x 期間 breaking 許容」が決定済。Layer 3 は **Layer 2 の上に立つ** 構造であり、Layer 2 の breaking がそのまま Layer 3 に波及する。

- **この時点で測定すべき事実**:
  - skill が Layer 2 API（journal events / org-state schema / dispatcher_runner CLI）を **どの粒度で叩くか** の inventory
  - Layer 2 v0.1 → v0.2 の breaking change 想定頻度（Phase 4 measurement worker 結果待ち）
  - skill の semver を独立に管理する妥当性（skill 単位で stable / unstable が分かれる sign があるか）
- **選択肢**:
  - (a) **Layer 2 と同じ「0.x breaking 許容」**（単純で運用負荷低い）
  - (b) **Layer 2 を exact pin、Layer 3 自体は安定気味に運用**（Phase 3 Step E と同じ流派）。Layer 2 を bump するたび Layer 3 を手で追従
  - (c) **stable skill / unstable skill を skill 単位で旗振り**（`org-delegate` は stable、`skill-audit` は experimental 等）。skill ごとに semver を分ける over-engineering risk あり

## Q5. Phase 4 で bundle 済みの prompt template との関係整理

Phase 4 Q5=b で「Python runner + 英語版 prompt template をセットで Layer 2 に extract」が決定済。Phase 5 で `.dispatcher/CLAUDE.md` / `.curator/CLAUDE.md` を Layer 3 に置こうとすると **二重所属** になる。

- **この時点で測定すべき事実**:
  - Phase 4 v0.1 にバンドル予定の prompt template が「reference 用 minimal version」か「現行 ja の full 版」か（Phase 4 decisions §Q5 のスコープ確認）
  - claude-org-ja 内 `.dispatcher/CLAUDE.md` / `.curator/CLAUDE.md` の最新 LOC と、それを読む secretary / worker の頻度
  - Phase 4 v0.1 prompt と claude-org-ja current prompt の diff（Q2-(iv)）
- **選択肢**:
  - (a) **prompt は Layer 2 にすべて寄せる**: Layer 3 は skill + hooks + dashboard のみ。dispatcher / curator prompt は Layer 2 SoT に一本化し、claude-org-ja は consumer
  - (b) **Layer 2 は minimal reference prompt、Layer 3 が rich prompt**: Layer 2 は最小限の "起動可能な" template、Layer 3 が claude-org doctrine を反映した full prompt を持つ
  - (c) **prompt は Layer 2 / Layer 3 両方に置かず、claude-org-ja に残す**: Phase 4 Q5 決定を見直し、prompt は reference 配布物 (claude-org-ja) の専管に戻す

## Q6. org-shaped hooks（`.hooks/` の Phase 4 残置分）の去就

`.hooks/` のうち generic な分 (`block-no-verify.sh`, `block-dangerous-git.sh`, `check-worker-boundary.sh` 等) は Phase 3/4 で core-harness に移管済。残った org-shaped hooks (`block-workers-delete.sh`, `block-dispatcher-out-of-scope.sh`, `block-org-structure.sh`, `block-git-push.sh`) は **claude-org doctrine の実装** であり、Layer 3 = orchestration glue の構成要素として extract 候補となる。

- **この時点で測定すべき事実**:
  - org-shaped hooks の改訂頻度（過去 6 ヶ月の commit 数、Q2-(v)）
  - en port が同じ org-shaped hooks を独自移植しているか / どれだけ drift しているか
  - これらの hooks が読む org 名 (`registry/org-config.md`, `.dispatcher/`, `.state/` 等) の **抽象化可能性**
- **選択肢**:
  - (a) **Layer 3 に含める**: skill と一緒に `.hooks/` の org-shaped 分も extract。en port もここから取り込む
  - (b) **claude-org-ja 残置**: hooks は `registry/` / `.dispatcher/` 等の **物理 path** に強く結び付くため、reference 配布物 (claude-org-ja) の中でしか動かない。Layer 3 では持たない
  - (c) **Layer 2 (claude-org-runtime) に hooks 抽象を入れる**: Layer 2 が runtime SoT の立場で org-shaped hooks の生成 / 検証 API を提供し、Layer 3 は purely declarative（hook 設定 YAML 程度）にとどめる

## Q7. dashboard の去就 — Phase 4 Q9=c との接続

Phase 4 Q9=c で「dashboard SPA は claude-org-ja に残し、Layer 2 は schema (`org-state.json`) のみ提供」が決定済。Phase 5 で dashboard を Layer 3 に持っていくと Phase 4 決定を **書き換える** ことになる。

- **この時点で測定すべき事実**:
  - en port が独自に port した dashboard と claude-org-ja の dashboard の drift（Q2-(vi)）
  - dashboard SPA の release cycle が skill / prompt と揃うか（同じ頻度で改訂されるか）
  - 第三者 consumer が dashboard だけを使いたい / skill だけを使いたい のどちらの需要が強いか
- **選択肢**:
  - (a) **Phase 4 Q9=c を維持**: dashboard は claude-org-ja に残し、Layer 3 は dashboard を持たない（skill + prompt + hooks のみ）
  - (b) **dashboard を Layer 3 に統合**: Phase 4 Q9=c を書き換え、Layer 3 が dashboard SPA も持つ（release cycle が一致するなら筋）
  - (c) **dashboard を Layer 3 から独立分離**: Layer 3 = skill のみ、dashboard は別の Layer 3.5 = `claude-org-dashboard` リポ（Q3=c の split と整合）

## Q8. State / event catalog の正規化 — どこを SoT にするか

Phase 4 Q7=a で「workflow_status / journal event / anomaly kind を Python `Enum` + JSON schema として Layer 2 で固定」が決定済。Layer 3 の skill は **これらの enum を多用** する（journal append / 状態遷移判定）。Layer 3 で **追加の event / state** を導入したいケースの扱い。

- **この時点で測定すべき事実**:
  - skill が呼び出している journal event 種別の inventory（Layer 2 の 35 種カタログとの差分。Phase 4 Step D の follow-up と整合）
  - skill 固有の中間状態（`worker_dispatched_pending_pr` 等）が Layer 2 enum に含まれるか / Layer 3 拡張が必要か
- **選択肢**:
  - (a) **Layer 2 enum がすべての SoT**: Layer 3 は新規 event を導入できない。新 event 追加要求は Layer 2 PR で吸収
  - (b) **Layer 3 で plugin event 機構を持つ**: Layer 2 が `register_event(name, schema)` API を提供し、Layer 3 がそれを使って独自 event を足す
  - (c) **Layer 3 内では string で扱う緩い運用**: enum 化は Layer 2 のみ、Layer 3 では Phase 3 以前の "string convention" に戻す（型安全性を犠牲にして開発速度を取る）

## Q9. ja↔en 同期戦略との接続 — Layer 3 の SoT をどこに置くか

Phase 4 Q8=a で「`claude-org-runtime` を新 SoT、ja / en は consumer に降格」が決定済。Layer 3 についても同じ路線を採るかが論点。

- **この時点で測定すべき事実**:
  - skill / prompt / hooks / dashboard の **言語混在度**（日本語コメント / 英訳済み箇所の比率、Q2-(vii)）
  - 第三者 consumer が日本語 skill のまま使うか英訳版を要求するかの想定
  - #171 auto-mirror runtime が Layer 3 までカバーする / カバーしないの再定義
- **選択肢**:
  - (a) **Layer 3 リポを新 SoT** にし、ja / en の Layer 4 は consumer。skill / prompt は英語 SoT、ja application 層が日本語訳 consumer
  - (b) **claude-org-ja を引き続き SoT**、Layer 3 は ja からの自動抽出 downstream（Phase 4 Q8 で却下した case を Layer 3 で復活）
  - (c) **bilingual SoT**: skill / prompt は **ja と en を同等の SoT** として Layer 3 リポ内に併存。translate は両方向（mirror ではなく fork-and-sync）

## Q10. Phase 5 抽出後に claude-org-ja に残るもの (DoD 観点も含む)

Layer 3 を抽出した後、claude-org-ja は何を持つ reference 配布物として残るか、その positioning と Phase 5 DoD を確定する。Phase 4 までで `dispatcher_runner.py` 等の runtime コードは消える前提。

- **この時点で測定すべき事実**:
  - 抽出後 claude-org-ja の LOC 内訳予測（残るもの: `registry/org-config.md`, `.state/` snapshot, knowledge/, docs/, sessions/ 等。Q2-(viii)）
  - 「reference 配布物として人が clone する」典型ユースケース（教育目的 / 内部試験 / 第三者 org 立ち上げ手本）
- **選択肢**:
  - (a) **claude-org-ja = "live demo + 知識ベース"**: runtime も skill も持たない。`registry/`, `knowledge/`, `sessions/`, `docs/` のみが本体。setup 手順は「Layer 1+2+3 を install して registry を書く」。Phase 5 DoD = ja から `.claude/skills/` 等が消えるまで（Phase 4 Q11=b 流派）
  - (b) **claude-org-ja = "thin shim + 内部試験場"**: skill / prompt の override / 拡張だけが残る。Layer 3 をそのまま使うのではなく ja 流派の改造 layer を持つ。Phase 5 DoD = Layer 3 v0.1 release + ja shim 1 件 merge（Phase 4 Q11=a 流派）
  - (c) **claude-org-ja を archive 化**: 抽出後は claude-org-ja を deprecated にし、後継 reference として `claude-org-reference`（仮）を Layer 4 として再起動する。Phase 5 DoD = archive 宣言 + 後継リポの README 完成

---

## 公開 OSS 化判断（最後の 2 問）

ここから先は、Q1〜Q10 の答えがすべて Layer 3 抽出方向に揃った場合に固める **公開 OSS 化の具体実装**。Q1 で kill / defer が出た場合は Q11/Q12 はスキップ可能。

## Q11. 公開 OSS 化判断 (1/2) — そもそも公開するか / private に留めるか / 配布形態

Layer 1 / Layer 2 は OSS 公開前提だった (PyPI publish)。Layer 3 = skill + prompt + hooks は claude-org doctrine の **実装そのもの** に近く、公開すると組織方針を外部に晒す。さらに「skill / prompt は Markdown 主体」という性質が PyPI 配布と相性が悪い可能性もある。

- **この時点で測定すべき事実**:
  - skill / prompt 中に含まれる組織内 fixture（人名 / 内部 URL / 過去セッションの narrative）の量
  - en port が既に public OSS なので、Layer 3 を private にしても en port から実装は推測可能 → private にする実効性
  - Anthropic 公式 / コミュニティが「claude-org doctrine の実装」を OSS reference として参照したいニーズ
  - Claude Code の plugin / skill 機構が「外部リポからの読み込み」をどこまで標準サポートしているか（symlink / submodule / marketplace 等）
- **選択肢**:
  - (a) **public OSS、PyPI publish**（Layer 1/2 と完全に揃える。Markdown は `importlib.resources` 経由で取り出す）
  - (b) **public OSS、GitHub Release のみ**（Phase 3 Q10=A の流派）。consumer は git submodule / git subtree / `pip install git+...` で取り込む。Markdown 主体に整合
  - (c) **private GitHub repo**（suisya-systems org 内 private、招待制配布）。en port の public 状態とは矛盾を許容、または en port の Layer 3 相当も private に揃える

## Q12. 公開 OSS 化判断 (2/2) — namespace / license / governance

Q11 で public を取った場合の具体実装。Phase 4 Q10 で `claude-org-runtime` を仮 namespace としたのと対称。本 Q が **本 doc の最終問** であり、ここの答え次第で Phase 5 の release 計画が組める。

- **この時点で測定すべき事実**:
  - PyPI / GitHub repo 名衝突確認: `claude-org-skills`, `claude-org-glue`, `claude-org-orchestration`, `claude-org-doctrine` 等の候補名
  - GitHub org `suisya-systems` の repo 命名規約との整合
  - License: MIT 統一（claude-org-ja / core-harness と同じ）か Apache-2.0（特許条項あり）か。skill / prompt が「Anthropic Claude Code を前提とする」性質との相性
  - Governance: maintainer list / CODEOWNERS / RFC プロセス。Layer 1/2 と同じ governance を流用するか、Layer 3 だけ別運用にするか
- **選択肢**:
  - (a) **`claude-org-glue` (or `-skills`) を MIT で publish、governance は claude-org-ja / Layer 1 / Layer 2 と同じ maintainer 体制**（最小投資。Q3=a or Q3=b と整合）
  - (b) **複数リポに分割** (Q3=c の split に対応): `claude-org-skills` + `claude-org-prompts` + `claude-org-dashboard` を別 release cycle で publish。各リポ MIT、governance は共有
  - (c) **license / governance を Layer 1/2 と分離**: Layer 3 のみ Apache-2.0、別 maintainer 体制（claude-org doctrine の community 化を狙う）。over-engineering risk あり

---

## 窓口判断のハイライト（特に重要そうな質問）

12 問の中で、**他の質問の答えを縛る** / **kill 判定に直結する** 順に重要だと思われるのは以下:

1. **Q1（抽出根拠の測定 / kill ゲート）+ Q2（measurement プラン）** — 一体で先に答える。kill が出たら Q3〜Q12 は worker 投入不要、本 doc がそのまま結論として残る。
2. **Q3（スコープ境界 narrow/wide/split）** — Q4〜Q10 の前提を縛る。kill されなかった場合の **二番目の分岐点**。
3. **Q5（Phase 4 prompt template との関係整理）+ Q7（dashboard 去就）** — どちらも Phase 4 決定の書き換え可能性を含むため、Phase 4 Lead 判断との整合確認が必要。
4. **Q11（public / private / 配布形態）** — Q12 の namespace / license / governance 実装は Q11 の答えに完全従属。
