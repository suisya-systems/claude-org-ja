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
codex exec review --base origin/main -m gpt-5.6-sol -c model_reasoning_effort=medium < /dev/null
```

- **前景実行を既定にする**: 背景化（`&`）+ ログ redirect は、worker が完了を待たず・指摘を読まずに完了報告してゲートを素通りする事故を招く。fast な review surface は前景で待てば自然にゲートが効く。コピペするコマンドにシェルのリダイレクト記号を含む `<main>` / `<N>` 等のプレースホルダを残さない（`< main` 等と誤解釈され落ちる）。背景化+ログ監視が要るのは下記の重い `codex exec` プロンプト（デザインレビュー等、長時間ハングしうる）に限る。

- `--base <branch>` で「ベースからの全差分」をレビュー（worker の `main からの差分` セマンティクスと一致）。`--commit <sha>` で単一コミットも可だが、**range は full（`--base`）を既定**にする（理由は下記「range」）。`mkdir -p tmp` で出力先を保証してから redirect する（`tmp/` 不在の repo で redirect が先に落ちるのを防ぐ）。
- `codex review` CLI が diff を直接供給するため、model が git を叩く agentic オーバーヘッドが無く速い。出力は codex 内蔵のレビュープロンプトで Blocker/Major 相当（P1/P2 等）を返す。

### 採用時に必ず保持する注記（ベンチマーク実測, 出典末尾参照）

下記注記 1 / 2 の速度・カバレッジの数値は **gpt-5.5 世代で取得した実測値**（出典の方式ベンチマーク時点の計測条件）。方式間の相対比較として引き続き有効だが、現行の指定モデル（注記 3）で取り直した値ではない。

1. **約2倍速は「中小 diff × low/medium effort」限定**。**high-effort review は大 diff（例 100 行超）でスケールせず**、`codex exec` 直打ちより遅くなる（実測: 127 行 diff で high≈138s vs exec-heavy≈87s）。large diff では effort を上げない。
2. **review surface は危険側 Major（false positive で gate 誤通過する系）は守れるが、benign な safe-side Major（過剰 polling 方向の false negative）や ReDoS 級の付加バグを取りこぼしうる**。実測では、ある guard の `か` clause 全域拒否による false-negative と可変長 lookahead の二乗時間 ReDoS を 3/3 で拾えたのは**重い多観点 `codex exec` プロンプトのみ**で、review surface は low/high とも取りこぼした。深掘りが要る局面（後述のデザインレビュー、設計に近い変更）では重い多観点 exec を併用する。
3. **model は `-m` で明示する**（ChatGPT アカウントで通るモデル名が限られるため）。現行世代の実行可能名は **`gpt-5.6-sol`**（`~/.codex/config.toml` の既定値でもある）。**通る名前は「世代番号を上げれば通る」ものではなく、サフィックス込みで個別に決まる** — 素の `gpt-5.6` も `gpt-5.6-codex` も 400 で弾かれる。したがって世代交代時は、置換前に実際に 1 回叩いて実行可能な名前を確認すること（下表の取り直し）。API キー surface は `OPENAI_API_KEY` 不在（`codex login status` は ChatGPT ログイン）で実行不能。reasoning effort は `minimal` が 400（`unsupported_value`。許容値は `none` / `low` / `medium` / `high` / `xhigh`）。以上より **`-m gpt-5.6-sol -c model_reasoning_effort=medium`** を明示する。

   実測（2026-07-27 / codex-cli 0.144.4 / ChatGPT アカウント。`codex exec --skip-git-repo-check -s read-only -m <model> -c model_reasoning_effort=medium` で 1 回ずつ確認）:

   | モデル名 | 結果 |
   |---|---|
   | `gpt-5.6-sol` | 実行可（現行世代の指定先） |
   | `gpt-5.6` | 400 `The 'gpt-5.6' model is not supported when using Codex with a ChatGPT account.` |
   | `gpt-5.6-codex` | 400（同上メッセージの `gpt-5.6-codex` 版） |
   | `gpt-5.5` | 実行可（一世代前。まだ通るが指定先ではない） |
   | `gpt-5.5-codex` | 400（同上メッセージの `gpt-5.5-codex` 版） |
4. **`codex:rescue` skill は引き続き禁止**（過去に 18 分超ハングの実害。`codex exec` 系直打ちに切り替えると正常動作）。

### デザインレビュー（実装前）は review surface ではなく exec プロンプト形を維持

デザインレビュー（`apply` 前の事前設計レビュー）は **diff が存在しない**ため `codex exec review --base` は使えない。設計内容 + 対象ファイル + 契約参照を渡す **`codex exec` のプロンプト形を維持**する。上記注記 2 のとおり、重い多観点プロンプトは subtle / 設計レベルの Blocker を拾う breadth に優れ、デザインレビューはまさにその breadth が要る用途であるため、ここでは exec プロンプト形が適切。model/effort（`-m gpt-5.6-sol -c model_reasoning_effort=medium`）と下記ハングガード・`codex:rescue` 禁止は同様に適用する。詳細トリガーと手順は [`.claude/skills/org-delegate/references/codex-design-review.md`](../../.claude/skills/org-delegate/references/codex-design-review.md) を参照。

## ゲートの「空の合格」— サンドボックス下で codex が diff を一度も読まない失敗モード

ワーカーのサンドボックス下では、`codex exec review` が **`git diff` を一度も実行しないまま「指摘なし」と読める文面を返して exit 0 で正常終了する**ことがある。検証深度 `full` が実質無検査になるうえ、**出力の文面を読むだけでは正常な合格と区別できない**ため、ハングよりも危険な失敗モードである（ハングは少なくとも異常だと分かる）。

### 真因は `CODEX_HOME` の配置

codex はコマンド実行のたびに実行ヘルパー（`codex-linux-sandbox`）を `CODEX_HOME` 配下へ配置する。既定の `~/.codex` はワーカーのサンドボックスで**書き込み不可**なので、ヘルパーを配置できず、結果として codex は**コマンドを 1 つも実行できない**。レビュー対象の diff を取得する手段が無いまま、モデルは「見えた範囲では問題なし」に相当する応答を返して終了する。

したがって対処は `CODEX_HOME` を書き込み可能な場所へ退避することだが、退避先には条件が 2 つある:

- **書き込み可能**であること
- **一時ディレクトリ配下でない**こと — codex は `codex_home` が temp dir 配下だとヘルパー配置を**明示的に拒否**する（`Refusing to create helper binaries under temporary dir "..."`）

### `$TMPDIR` は逆効果（一般則に対する明示的な例外）

「一時ファイルは `$TMPDIR` 配下へ」という一般則をそのまま適用して `CODEX_HOME=$TMPDIR/...` にすると、上記 2 条件目に抵触して**症状が悪化する**。素直に「書けない」で失敗するのではなく、codex 側の拒否が**空の合格に化ける**ためで、退避しない場合より発見が難しくなる。ワーカー作業ディレクトリ配下（`$PWD/.codex-home`）が両条件を満たす標準の退避先である（成果物ではないので commit しない）。

なお `-c sandbox_mode='"read-only"'` の明示は **codex 自身の内側サンドボックスを締める**設定であり、外側のサンドボックスには触れない。真因は「サンドボックスが厳しすぎること」ではなく `CODEX_HOME` の配置なので、**`danger-full-access` 等の緩める方向の変更は対処にならない**（安全機構にも掛かる）。

### 判定は「肯定的証拠」でしかできない

この失敗モードは、**終了コードでも出力の文面でも判定できない**:

- **終了コードは成立判定に使えない** — 空の合格でも **exit 0** を返す
- **但し書きも判定に使えない** — 「sandbox が起動できなかったので確度が低い」旨の但し書きが付くことはあるが、**環境によっては但し書きが消え、`No actionable findings were identified` という素の合格文だけになる**。文面の有無に依存した判定は環境依存で破綻する

よって「エラーが出ていないこと」（否定的証拠）ではなく、**「コマンドが実際に実行されたこと」（肯定的証拠）**でのみ判定できる。実行ログを保存し、実行記録の行数を数える:

```bash
grep -cE '^ *succeeded in [0-9]+(ms|s|m)' "$CODEX_REVIEW_LOG"   # 成功実行数: 1 以上が必要
grep -cE '^ *failed in [0-9]+(ms|s|m)' "$CODEX_REVIEW_LOG"      # 失敗実行数: 0 が必要
```

**行頭アンカー（`^ *`）と実行時間（`[0-9]+(ms|s|m)`）を両方要求する理由**: ログには**レビュー対象の diff 本文**も**codex が実行したコマンドの出力**もそのまま載るため、素の文字列 grep は自分自身にマッチする。実測で 2 種類の自己マッチを確認している:

1. `exec_command failed` / `Refusing to create helper` のような素の文字列 grep は、**本節のような記述を含む差分をレビューすると diff 側の記述にマッチする**（行頭アンカーで回避できる）
2. 行頭アンカーだけでは足りない — codex がバイナリを読んだ出力に ` failed in TUI` のような**文字列断片**が現れ、`^ *failed in ` にマッチする（実測で偽陽性 2 件）

codex の本物の実行記録は必ず `succeeded in 0ms:` のように**実行時間を伴う**ため、時間まで含めてアンカーすれば両方の自己マッチを踏まない。

**終了コードの併用**: exit 0 は成立の**十分条件ではない**が、**非 0 は失格条件として使える**。codex が 1 コマンド実行後に API / 認証 / 安全機構で異常終了すると、マーカー数だけでは成立条件を満たしてしまうため、終了コードを追加の必要条件として併用する（`tee` に通す場合は `set -o pipefail` が無いとパイプの終了コードが `tee` のものになり codex 側の失敗が隠れる）。

**並走ワーカーでのログ名**: `$TMPDIR` は並走ワーカー間で共有されるため、ログ名を固定にすると**別ワーカーの `succeeded in` を自分の成立根拠に取り違える**。ワークツリー名等でワーカーごとに分けること。

### ゲート未成立を「codex clean」と報告しない

成立条件（成功数 1 以上 / 失敗数 0 / 終了コード 0）を満たさない場合、出力がどれだけ合格に読めても**ゲートは回っていない**。「Codex ゲート未成立（diff 未読の空の合格、HEAD=`<sha>`）」と明示して判定数値を添えて報告する。これは safety block 時の扱い（ゲート未成立であって clean ではない）と同じ原則で、**「codex 未導入 skip」とは意味が異なる**。ゲートが回らないこと自体は報告すれば済むが、回っていないゲートを回ったことにすると検証深度 `full` が名目だけになる。

実行形（`CODEX_HOME` 退避を含む正規のコマンド列）の SoT は [`.claude/skills/org-delegate/references/worker-claude-template.md`](../../.claude/skills/org-delegate/references/worker-claude-template.md) と `tools/templates/worker_brief_*.md`。本節はその**根拠**（なぜ退避が要るか / なぜ `$TMPDIR` が逆効果か / なぜ終了コードで判定できないか）を残す。

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
