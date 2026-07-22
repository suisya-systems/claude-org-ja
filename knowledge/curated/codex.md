# codex CLI 利用の運用知見

codex CLI（セルフレビュー / デザインレビュー用）の標準方式と、ハング挙動への対処。

## 標準方式: 差分セルフレビューは `codex exec review`（review surface）

検証深度 `full` の差分セルフレビュー（commit 後・完了報告前のゲート）は、**`codex exec` 直打ちの長文プロンプトではなく `codex exec review`（review surface）を既定とする**。固定 diff（既知 Blocker/Major を持つ実 PR を再構成）に対する方式間ベンチマークで、review surface が `codex exec` 直打ち（重い多観点プロンプト）の **約 2 倍速**（中小 diff）で、文書化済み Blocker のパリティは同一だったことに基づく。

正規実行形（diff セルフレビュー）:

```bash
# --base にはブランチのベース（通常 origin/main）を渡す。ローカル main は共有 clone で古いと
# 別タスク差分を巻き込む誤レビューになるため remote-tracking の origin/main を使い、参照前に
# git fetch origin を 1 回（fetch 不能でも review は継続）。review surface は高速なので前景実行し、
# 出力（Blocker/Major 相当）を見てから次に進む。stdin は < /dev/null で閉じる（背景化時の stdin 待ちハング回避）。
codex exec review --base origin/main -m gpt-5.5 -c model_reasoning_effort=medium < /dev/null
```

- **前景実行を既定にする**: 背景化（`&`）+ ログ redirect は、worker が完了を待たず・指摘を読まずに完了報告してゲートを素通りする事故を招く。fast な review surface は前景で待てば自然にゲートが効く。コピペするコマンドにシェルのリダイレクト記号を含む `<main>` / `<N>` 等のプレースホルダを残さない（`< main` 等と誤解釈され落ちる）。背景化+ログ監視が要るのは下記の重い `codex exec` プロンプト（デザインレビュー等、長時間ハングしうる）に限る。

- `--base <branch>` で「ベースからの全差分」をレビュー（worker の `main からの差分` セマンティクスと一致）。`--commit <sha>` で単一コミットも可だが、**range は full（`--base`）を既定**にする（理由は下記「range」）。`mkdir -p tmp` で出力先を保証してから redirect する（`tmp/` 不在の repo で redirect が先に落ちるのを防ぐ）。
- `codex review` CLI が diff を直接供給するため、model が git を叩く agentic オーバーヘッドが無く速い。出力は codex 内蔵のレビュープロンプトで Blocker/Major 相当（P1/P2 等）を返す。

### 採用時に必ず保持する注記（ベンチマーク実測, 出典末尾参照）

1. **約2倍速は「中小 diff × low/medium effort」限定**。**high-effort review は大 diff（例 100 行超）でスケールせず**、`codex exec` 直打ちより遅くなる（実測: 127 行 diff で high≈138s vs exec-heavy≈87s）。large diff では effort を上げない。
2. **review surface は危険側 Major（false positive で gate 誤通過する系）は守れるが、benign な safe-side Major（過剰 polling 方向の false negative）や ReDoS 級の付加バグを取りこぼしうる**。実測では、ある guard の `か` clause 全域拒否による false-negative と可変長 lookahead の二乗時間 ReDoS を 3/3 で拾えたのは**重い多観点 `codex exec` プロンプトのみ**で、review surface は low/high とも取りこぼした。深掘りが要る局面（後述のデザインレビュー、設計に近い変更）では重い多観点 exec を併用する。
3. **model は実質 gpt-5.5 固定**。ChatGPT アカウントでは `gpt-5.5` のみ実行可で、**`gpt-5.5-codex` は 400（not supported with ChatGPT account）/ API キー surface は OPENAI_API_KEY 不在で実行不能 / reasoning effort `minimal` は 400（image_gen/web_search ツールと併用不可）**。`-m gpt-5.5 -c model_reasoning_effort=medium` を明示する。
4. **`codex:rescue` skill は引き続き禁止**（過去に 18 分超ハングの実害。`codex exec` 系直打ちに切り替えると正常動作）。

### デザインレビュー（実装前）は review surface ではなく exec プロンプト形を維持

デザインレビュー（`apply` 前の事前設計レビュー）は **diff が存在しない**ため `codex exec review --base` は使えない。設計内容 + 対象ファイル + 契約参照を渡す **`codex exec` のプロンプト形を維持**する。上記注記 2 のとおり、重い多観点プロンプトは subtle / 設計レベルの Blocker を拾う breadth に優れ、デザインレビューはまさにその breadth が要る用途であるため、ここでは exec プロンプト形が適切。model/effort（`-m gpt-5.5 -c model_reasoning_effort=medium`）と下記ハングガード・`codex:rescue` 禁止は同様に適用する。詳細トリガーと手順は [`.claude/skills/org-delegate/references/codex-design-review.md`](../../.claude/skills/org-delegate/references/codex-design-review.md) を参照。

## ハングガード（review / exec 両形に共通）

`codex exec`（特に直打ちの長文日本語プロンプト）は、4000 文字超 / 多数の観点 / 階層的分類指示などプロンプトが大きいと、応答開始までに数十分かかるか永遠に応答しないことがある（codex-cli 0.129.0 で観測。thinking phase が stdout 沈黙する設計に起因）。review surface は内蔵プロンプトで軽いため発生しにくいが、運用ガードは両形に適用する:

1. stdin は `< /dev/null` で明示クローズ（background 実行時の stdin 待ちハングを防ぐ）。実行中の codex を `| tail` でパイプしない（バッファリングで出力が空に見える）。
2. ラウンドごとにログファイル名を変える（同一ファイルへ 2 プロセスが `>` で書くと混線する）。完了検知はマーカーではなく**プロセス終了**で待つ。
3. 出力 0 bytes が **5–10 分継続したら kill**（`wc -l <log>` が 0 のまま = 何も来ていない）。kill は `kill -9` で codex プロセスツリー全体（`zsh -c …` / `node …` / `codex` 本体）を落とす。
4. codex 未導入環境および kill 後は **skip 扱いとして完了報告に進んでよい**。
5. 観点を絞る（重い 4 段階分類 × 5 観点は exec 直打ちでハング要因）。差分セルフレビューでは review surface の内蔵プロンプトで足りる。

観測例（renga `feat/spawn-claude-pane-soft-validation` ワーカー, 2026-05-10）: プロセスは alive（`STAT=Sl`）だが stdout 0 bytes のまま 165 分（`etimes=9935s`）。`codex:rescue` skill ハング（18 分超）と異なり `codex exec` 直打ちでも長時間ハング。

出典:
- 方式ベンチマーク（review surface 採用根拠・上記注記の実測値）: `2026-06-16-codex-review-method-benchmark.md`
- exec 長文プロンプトハング: `2026-05-10-codex-exec-hang-on-long-japanese-prompt.md`
- stdin 待ちハング根因: `2026-06-16-codex-exec-stdin-hang.md`
