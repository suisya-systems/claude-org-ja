# Phase 4 (Layer 2 = org-runtime) 設計議論用 Q&A 草案

- 作成日: 2026-05-02
- 関連 Issue: ja#129
- 入力: `phase4-inventory-2026-05-02.md`
- 性質: **質問のみ**。回答は窓口（Lead）判断。Phase 3 (core-harness) と同じく measurement-first で進めるための論点整理。
- 数: 12 問（Phase 3 design memo の Q&A 構成に倣う）。

---

## Q1. スコープ境界 — Option α / β / γ のどれを起点にするか

inventory §3 で示した narrow / wide / renga 同等の 3 案のうち、measurement を取りに行く **MVP の境界** をどこに置くか。具体的には:

- (a) `dispatcher_runner.py` の `delegate-plan` 一個だけを最初に extract（Layer 1 = core-harness と同じ「最小可動部」戦略）
- (b) `delegate-plan` + `org_state_converter.py` + journal schema をセットで extract（state contract 中心）
- (c) ロール prompt まで含めて extract し、外部の Claude Code consumer が secretary / dispatcher / curator を立てられる reference 構成にする

(a)→(b)→(c) は段階追加可能なので「初期 release のスコープ」を聞いている。

## Q2. API 安定性ゲート — consumer ≥ 2 を最初から課すか

core-harness は "consumer ≥ 2 + 6 ヶ月 no-break" を 1.0 graduation gate にした（README 参照）。org-runtime は consumer 候補がより限定的（claude-org-ja 自身 + 翻訳版 en + 内部試験用 worker のみ）なので、

- (a) core-harness 同等のゲートを課す
- (b) 0.x の間は "API は壊しうる" として breaking change を許容（claude-org-ja のみが consumer の前提）
- (c) ゲートを「consumer ≥ 2」ではなく「`journal.jsonl` の event 種別が 30 日以上不変」のような **measurement-driven gate** に置き換える

どれを採るか。

## Q3. 実装言語 — Python だけか、Rust 補助か

`dispatcher_runner.py` は現状 Python (stdlib only)。renga は Rust。Layer 2 を:

- (a) **Python のみ**（Claude Code consumer は既に Python を入れている前提でよい）
- (b) **Python core + Rust optional**（hot path だけ Rust、ただし `dispatcher_runner.py` は単発 CLI なので速度要件は薄い）
- (c) **Rust 中心 + Python thin wrapper**（renga と同じ runtime 言語に揃え、長期メンテを Rust に集約）

の方針を 1 つ決めたい。

## Q4. config schema の互換性 — `.state/` 形式を変えてよいか

抽出に伴い `journal.jsonl` event スキーマや `org-state.md` セクション規約を整えるなら、claude-org-ja の既存ファイルとの互換が問題になる:

- (a) **既存形式を完全に維持**（Layer 2 は現状 schema を凍結して publish）
- (b) **schema_version を導入**して旧/新並走、`org_state_converter.py` に migration 関数を追加
- (c) **breaking change 許容**（claude-org-ja を fork 直前 snapshot に戻して新 schema へ一括移行）

`docs/org-state-schema.md` / `docs/journal-events.md` の既存記述をどこまで動かしてよいか。

## Q5. Dispatcher 単独抽出 vs バンドル — runtime と prompt は分離すべきか

inventory §2.6 にあるとおり、dispatcher は Python helper (`dispatcher_runner.py`) と Markdown prompt (`.dispatcher/CLAUDE.md`) の **2 層**で動いている。Layer 2 の抽出単位は:

- (a) **deterministic Python だけ extract**（prompt は claude-org-ja に残し、コミュニティが各自書く）
- (b) **Python + 英語版 prompt template** をセットで extract（"reference dispatcher prompt" として公開）
- (c) **prompt を schema 化**（Pydantic / JSON schema に近い形）して prompt rendering library として publish

prompt の OSS 公開は Phase 3 で `core-harness` が「AI agent framework ではない」と positioning を明示した経緯があるので、ここを跨ぐかどうかは戦略判断。

## Q6. Renga 依存の扱い — renga MCP プロトコルを spec として再宣言するか

`.dispatcher/CLAUDE.md` は renga-peers MCP の `poll_events` / `inspect_pane` / `check_messages` 等の使い方に強く依存する。Layer 2 を extract した consumer から見たとき:

- (a) **renga 必須**として publish（renga は 1.0 で API freeze 済なので問題ない）
- (b) **renga ≒ tmux/wezterm/etc も使える抽象化層**を Layer 2 内に追加（pane lifecycle interface）。実装は renga のみ
- (c) **renga 必須だが MCP tool name を直接 import せず adapter 経由**にして、将来差し替え余地を残す

## Q7. State machine の正規化 — enum を contract として固めるか

inventory §2.7 で示した worker status / journal event / anomaly kind の値はすべて **string convention** で、コードレベルで強制されていない。Layer 2 で:

- (a) **Python `Enum` + JSON schema** として固定（`workflow_status.py` 等を新設）
- (b) **JSON schema のみ**（言語非依存にする）で、実装側は string のまま
- (c) **現状維持**（命名は `docs/` の散文だけで規定し、コードでは strict 検証しない）

Phase 3 の Step B (schema/validator/generator) と同じノリで (a) を採るのが筋に見えるが、claude-org-ja 側の既存コードに型を入れる工数が読めない。

## Q8. ja↔en 同期戦略との接続 — ソースオブトゥルースをどこに置くか

claude-org-ja → en の sync は #171 の Option A（ja=SoT、auto-mirror runtime）で運用中。Layer 2 を **第三の repo (`org-runtime`)** に切り出した場合:

- (a) `org-runtime` を新たな SoT にし、claude-org-ja / en は consumer に降格
- (b) claude-org-ja を引き続き SoT、`org-runtime` は claude-org-ja から自動抽出される downstream
- (c) `org-runtime` は最初から **英語のみ**で書かれ、claude-org-ja / en は日本語 / 英語の prompt thin layer だけ持つ

current "ja=SoT" 体制と矛盾しないかが論点。

## Q9. dashboard / observability の去就 — Layer 2 か別 layer か

`dashboard/server.py` + `org_state_converter.py` は **runtime の観測層**。Layer 2 のスコープに含めるか:

- (a) **含める**（runtime と観測は不可分）
- (b) **別 layer (Layer 2.5 = org-observability) に分離**（dashboard SPA は別 release cycle）
- (c) **dashboard は claude-org-ja に残し、Layer 2 は schema (`org-state.json`) だけ提供**

en repo (`suisya-systems/claude-org`) には既に dashboard が port されているので、抽出時の重複も論点。

## Q10. release / packaging — PyPI に出すか

core-harness は PyPI Trusted Publisher 経由で公開予定（v0.3.2 で発火）。Layer 2 = org-runtime も:

- (a) **PyPI publish**（`pip install org-runtime` で外部利用可能）
- (b) **GitHub release のみ**（OSS としては公開、配布は git clone 想定）
- (c) **当面 private**（claude-org-ja repo 内 monorepo 化、別リポ抽出はしない）

PyPI に出すなら namespace（`suisya-org-runtime` / `claude-org-runtime` / `renga-orchestration`）の議論も必要。

## Q11. Phase 4 の DoD（Definition of Done）— "完了" の定義

Phase 3 は「core-harness 1.0 → claude-org-ja shim adoption → ja#128 close」で完了とした。Phase 4 は:

- (a) **Layer 2 リポ作成 + v0.1.0 release + ja shim 1 件 merge** までが MVP
- (b) **Layer 2 が claude-org-ja 内の `tools/dispatcher_runner.py` 等を実質置換**するまで（in-tree から消える）
- (c) **Layer 2 を使った第二の consumer（en port 以外）が誕生**するまで

Phase 3 と同等の "shim adoption" まで踏むのか、それとも単なる "extract & publish" で止めるのか。

## Q12. measurement-first の具体プラン — 何を計ってから設計に入るか

ja#129 で "measurement-first" を選んだので、コード extract 前に取るべき数値の合意が要る:

- (a) inventory §4 の 5 項目（`dispatcher_runner.py` churn、journal event 分布、worker file field 出現率、anomaly regex hit 率、ja↔en drift）すべて取る
- (b) **(a) のうち「event 分布」と「churn」だけ**取れば設計に入れる（残りは extract 後に取る）
- (c) measurement は最低限にして、まずは Step B 相当の **schema 抽出だけ先行 PR** を作る（Phase 3 と同じ進め方）

どこまで先に測るか、また measurement を **誰が**（worker 派遣 / Lead 手作業 / 自動 routine）取るか、両方を決めたい。

---

## 窓口判断のハイライト（特に重要そうな質問）

12 問の中で、**他の質問の答えを縛る**順に重要だと思われるのは以下:

1. **Q1（スコープ境界）** — α/β/γ の選択で他の質問の前提が大きく変わる。これだけは最初に決めたい。
2. **Q3（実装言語）** — Python のみか Rust 補助かで、PyPI release / consumer 想定 / renga との関係 (Q6, Q10) が連鎖して変わる。
