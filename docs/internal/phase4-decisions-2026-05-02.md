# Phase 4 (Layer 2 = `claude-org-runtime`) — Lead 回答記録

- 日付: 2026-05-02
- 関連 Issue: ja#129 (Layer 2 = org-runtime 抽出)
- 入力 doc:
  - `docs/internal/phase4-inventory-2026-05-02.md`（コード棚卸し）
  - `docs/internal/phase4-questions-2026-05-02.md`（12 問の Q&A 草案）
- セッション: #5 ライブ Q&A（2026-05-02）にて Lead 回答確定
- 性質: Lead 判断の **永続化レコード**。後続 worker / Lead がこの doc 1 枚を参照して Phase 4 設計に入れることを目的とする。

---

## English summary (one paragraph)

Phase 4 extracts Layer 2 (`claude-org-runtime`) from the `claude-org-ja` monorepo as a Python-only PyPI-published runtime, in line with the measurement-first stance set in ja#129. The Lead chose a **wide MVP scope** that bundles role prompts (English template), state schema, and the dispatcher runner; allows **0.x breaking changes** without the core-harness "consumer ≥ 2" gate; treats the new `claude-org-runtime` repo as the **new source of truth**, with `claude-org-ja` / `-en` becoming consumers; freezes journal events / worker status / anomaly kinds via **Python `Enum` + JSON Schema**; keeps the dashboard SPA in `claude-org-ja` and exposes only schema from Layer 2; mandates **renga** (1.0 frozen) as a hard dependency; and treats Phase 4 as **Done only when in-tree `tools/dispatcher_runner.py` etc. are actually replaced** by the published runtime. As measurement, only **journal event distribution + dispatcher_runner.py churn** are taken first via a dedicated worker before any extraction PR.

---

## 各 Q への回答

### Q1. スコープ境界 — wide スコープで起点

- **回答**: **(c)** ロール prompt まで含めて extract し、外部 consumer が secretary / dispatcher / curator を立てられる reference 構成にする。
- **理由**:
  - Q5=b（Python + 英語版 prompt template バンドル）と整合させるためには、抽出単位に prompt を含めざるを得ない。
  - Q8=a（`claude-org-runtime` を新 SoT）にした以上、prompt も SoT 側に置かないと「runtime は新リポ、prompt は ja リポ」という分裂が起きる。
  - core-harness の「最小可動部」とは思想が違う: core-harness は **harness の最小単位 = フック契約** だったが、org-runtime の最小可動部は **役割が立ち上がる reference 一式**。
- **トレードオフ**: 初期 scope が膨らみ v0.1 release が遠くなる。一方で後から prompt を追加する破壊的変更を避けられる。

### Q2. API 安定性ゲート — 0.x breaking 許容

- **回答**: **(b)** 0.x の間は breaking change を許容（claude-org-ja / -en のみが consumer の前提）。
- **理由**:
  - Q1=c で wide scope を取ると、初期 schema が measurement なしに「正しい」とは判断できない。breaking ゲートを早期に課すと event schema を凍結したまま後悔することになる。
  - core-harness の "consumer ≥ 2 + 6 ヶ月" gate は consumer 候補が広い前提で意味があるが、org-runtime の現実 consumer は ja + en + 内部試験のみで自前管理可能。
  - Q4=c（`.state/` schema 一発 migrate）と整合: breaking 許容しないと migrate script 案が成立しない。
- **トレードオフ**: 外部利用者が現れた瞬間に gate を課す判断（→ 1.0 graduation 設計）を別途必要とする。

### Q3. 実装言語 — Python のみ

- **回答**: **(a)** Python のみ。
- **理由**:
  - `dispatcher_runner.py` は単発 CLI で hot path が無い。Rust 化の正味メリットが薄い。
  - Q10=a（PyPI publish）と素直に揃う。Rust を入れると wheel ビルド / cross-compile / abi3 等の運用負荷が一気に増える。
  - Claude Code consumer は既に Python を入れている前提が現実的（core-harness と同じ）。renga を Rust に揃えたいモチベーションは Layer 2 では発生しない（renga 依存は MCP 越しで済む）。
- **トレードオフ**: 将来 Rust core が必要になったら言語境界の作り直しコスト発生。ただし Python core を持っていることで段階導入は可能。

### Q4. config schema の互換性 — breaking + 一発 migrate script

- **回答**: **(c)** breaking change 許容。`.state/` 既存形式は新 schema に一括移行する migrate script を 1 本提供する。
- **理由**:
  - Q2=b（0.x breaking 許容）と整合。schema_version 並走 (b) を採ると、Q7=a（Enum + JSON schema 固定）の strict 化と矛盾する（並走中は両方の表現を受け入れる必要が出る）。
  - claude-org-ja の `.state/` は実質単一 consumer（自分自身）なので、並走の必要性が薄い。
  - 一発 migrate script は再実行可能 / dry-run 可能であれば十分にリスク制御可能。
- **トレードオフ**: 旧 `.state/` snapshot を持つフィールドケースがあると割を食う。fixture を `tests/migration/` に必ず置く運用ルールが要る（→ 未決事項参照）。

### Q5. Dispatcher 抽出単位 — Python + 英語版 prompt template

- **回答**: **(b)** Python ランナー + 英語版 prompt template をセットで extract（reference dispatcher prompt として公開）。
- **理由**:
  - Q1=c（wide scope）と直接整合。prompt を OSS 同梱しないと「reference 構成」を名乗れない。
  - prompt schema 化（c）は野心的すぎ、measurement なしに schema を切ると後悔する確率が高い。まず英訳テンプレートで実態を観察し、必要なら後から schema 化する余地を残す。
  - core-harness の "AI agent framework ではない" positioning は core-harness のもの。Layer 2 = org-runtime は明示的に「Claude Code エージェント協調 runtime」なので、prompt 同梱は positioning 矛盾にならない。
- **トレードオフ**: 英訳テンプレート保守の負荷が新たに発生する（→ #171 auto-mirror の射程に含めるかは未決）。

### Q6. Renga 依存 — 必須として publish

- **回答**: **(a)** renga 必須として publish。
- **理由**:
  - renga は 1.0 で API freeze 済みなので、必須依存にしても下流の壊れ方は予測可能。
  - 抽象化層 (b) を入れると、現状 consumer が renga しか居ないのに interface 設計だけ膨らむ典型的 over-engineering になる。
  - adapter 層 (c) は中間案として魅力的だが、Q12=b で先に measurement を取る方針なので、adapter は実需が見えてから足す。
- **トレードオフ**: tmux / wezterm 派が来たら API surface 設計し直しになる。

### Q7. State machine の正規化 — Enum + JSON schema 固定

- **回答**: **(a)** Python `Enum` + JSON schema として固定。`workflow_status.py` 等の専用モジュールを設ける。
- **理由**:
  - Q4=c（schema 一発 migrate）+ Q2=b（0.x breaking 許容）と整合。breaking 許容の世界では「文字列規約」は脆く、enum で固める方が migrate script の正確性も担保される。
  - Phase 3 の Step B（schema/validator/generator）と同じ流儀。Lead としては Step B の良いところを踏襲する判断。
  - JSON schema 単独 (b) は言語非依存だが、claude-org-ja 内の Python コードに型を入れる工数を結局払うので、最初から Python Enum を SoT にした方が二度手間が無い。
- **トレードオフ**: 既存 `dispatcher_runner.py` の string 散文に Enum を被せる既存コード改修が発生（Q11=b の DoD 上はどのみち必要なので相殺される）。

### Q8. ja↔en 同期との接続 — `claude-org-runtime` を新 SoT

- **回答**: **(a)** `claude-org-runtime` を新 SoT、claude-org-ja / -en は consumer に降格。
- **理由**:
  - Q1=c（wide scope）+ Q5=b（prompt template 同梱）を採った時点で、prompt を含む全体 SoT が ja に残る案は二重管理になる。
  - #171 の "ja=SoT, auto-mirror to en" は **runtime 抽出前** の前提。Layer 2 抽出後は SoT 階層が「runtime 層 = `claude-org-runtime`、application 層 = `claude-org-ja` / `-en`」に再編される。
  - Layer 2 が SoT になった後も、アプリ層の翻訳 (ja↔en) は #171 の枠組みで継続できる（mirror 対象が縮むだけ）。
- **トレードオフ**: SoT が動く影響で #171 の auto-mirror runtime の射程を再定義する必要がある（→ 未決事項参照）。

### Q9. dashboard / observability — Layer 2 は schema のみ

- **回答**: **(c)** dashboard SPA は claude-org-ja に残し、Layer 2 は `org-state.json` schema だけ提供。
- **理由**:
  - Q1=c で wide scope を採ったが、dashboard まで含めると release cycle が完全に律速される（SPA build / FE 依存）。
  - en port には既に dashboard が独自移植されている（inventory §5）。Layer 2 が dashboard 実装を抱え込むと、en 側との重複を Layer 2 が背負うことになり整合しない。
  - schema (`org-state.json` + journal events) を Layer 2 で公開すれば、ja / en どちらの dashboard も同じ contract に対して書ける。
- **トレードオフ**: dashboard 改修時に runtime 側 schema 変更が必要になるケースで PR が 2 リポにまたがる。

### Q10. release / packaging — PyPI publish, namespace `claude-org-runtime`（仮）

- **回答**: **(a)** PyPI publish。namespace = `claude-org-runtime`（仮、v0.1 release 直前に再確定）。
- **理由**:
  - Q3=a（Python のみ）+ Q1=c（reference 構成）の組合せでは、`pip install` で reference を立ち上げられることが採用障壁を下げる最大要因になる。
  - core-harness が Trusted Publisher 経路を整備済みなので、運用テンプレートを再利用できる。
  - namespace 案: `suisya-org-runtime` は組織名の表面化を避けたい。`renga-orchestration` は renga 本体と混乱する。`claude-org-runtime` が落としどころ。ただし PyPI 名衝突 / 商標確認は release 直前に再点検する。
- **トレードオフ**: Trusted Publisher 設定 / OIDC / version policy など publish 周りの初期投資が発生する（core-harness ですでに型はある）。

### Q11. Phase 4 DoD — claude-org-ja 内 in-tree 置換まで

- **回答**: **(b)** Layer 2 が claude-org-ja 内の `tools/dispatcher_runner.py` 等を実質置換するまで（in-tree から消える）。
- **理由**:
  - Phase 3 が "core-harness 1.0 → ja shim adoption → ja#128 close" まで踏んだのと対称。同じ強度の DoD を取らないと、Layer 2 が「extract したけど誰も使っていない」状態で停滞するリスクが高い。
  - Q8=a（`claude-org-runtime` を新 SoT）と整合: SoT を移したのに in-tree コードが残ると SoT 矛盾が常態化する。
  - 第二 consumer 誕生まで待つ案 (c) は外部要因に DoD を縛るので Lead としては取らない。
- **トレードオフ**: ja 内の置換工数が DoD に組み込まれる分、Phase 4 期間が長くなる。段階置換 vs 一発置換は未決（→ 未決事項参照）。

### Q12. measurement-first の具体プラン — event 分布 + churn を先に worker 派遣

- **回答**: **(b)** inventory §4 のうち「journal event 分布」と「`dispatcher_runner.py` churn」だけを先に取る。残りは extract 後に計測。実施は **worker 派遣**。
- **理由**:
  - schema 設計 (Q4=c, Q7=a) で固める対象が **event 種別** なので、event 分布は schema 設計前に必要。
  - churn は API 安定性ゲート判断 (Q2=b) と「どこを最初に extract するか」のヒントになる。
  - worker file field 出現率 / anomaly regex hit 率 / ja↔en drift は extract した後でも遅くない（schema が先に固まらないとそもそも測れない指標もある）。
  - measurement を Lead 手作業にすると Lead がボトルネックになり measurement-first の趣旨に反する。worker 派遣が筋。
- **トレードオフ**: 残り 3 指標の測定が後回しになるので、extract 後に「やっぱり最初に測っておくべきだった」になる小さなリスク。

---

## 後続アクション

1. **measurement worker (Q12) を別途派遣** — `journal.jsonl` event 分布 + `dispatcher_runner.py` の git churn（直近 6 ヶ月程度）を計測し、結果 doc を `docs/internal/phase4-measurement-2026-05-XX.md` 等で永続化。
2. **計測結果を踏まえ Step B 相当の schema 設計 PR を起票** — Q7=a の `Enum` + JSON schema を含む `claude-org-runtime` の v0.1 schema 案。ja#129 上に紐付け。
3. **Phase 4 DoD は in-tree 置換** — Q11=b に従い、`tools/dispatcher_runner.py` 等が claude-org-ja 内から消えるところまでをマイルストーンに含める。
4. **PyPI namespace は v0.1 release 直前に最終確定** — 仮 `claude-org-runtime`、PyPI 衝突 / 商標 / `pip install` UX を release window 内で点検。
5. **#171 auto-mirror runtime の再定義** — Q8=a で SoT が動くため、auto-mirror の射程（runtime 層は対象外、アプリ層のみ mirror など）を別 issue で議論。

---

## 未決事項

1. **PyPI namespace 最終決定タイミング** — `claude-org-runtime` は仮称。v0.1 release 直前（Phase 4 後半）に PyPI 名衝突 / 商標 / `renga` 系との混乱可能性を再点検する。
2. **migration script (Q4=c) のテスト方針** — 旧 schema fixture をどこから持ってくるか未定。候補: (i) `claude-org-ja` 各セッションの `.state/` snapshot を gitignored fixture として `claude-org-runtime/tests/migration/` に取り込む、(ii) inventory §2.2 の現スナップショットを 1 件だけ baseline 化、(iii) fixture を Lead 手で書き起こす。i が現実的だが PII / session narrative scrub 工程が要る。
3. **prompt template の英訳保守 (Q5=b)** — auto-mirror runtime (#171) の射程に含めるか、`claude-org-runtime` 内で独自に保守するかが未決。Q8=a で SoT が `claude-org-runtime` に動くので、`claude-org-runtime` 内に英語 SoT、ja application 層が翻訳 consumer になる構図が自然だが、#171 既存設計との接続を別 issue で議論する必要あり。
4. **in-tree 置換 (Q11=b) の進め方** — deprecation 期間を設けて段階的に shim 化していくか、Layer 2 v0.1 を切ったタイミングで一発 swap するか未決。core-harness Phase 3 では shim adoption を経たので段階案が有力だが、Q2=b（0.x breaking 許容）と Q4=c（一発 migrate）と整合させると一発 swap も筋。measurement 結果（Q12）次第で再判断する。
