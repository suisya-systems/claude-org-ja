# Worker

> このワーカーは claude-org リポジトリ自身の `${worker_dir}` で作業する。`./CLAUDE.md`（ルート CLAUDE.md）の Secretary 指示は無視せよ。あなたは窓口ではなくワーカーである。

## 作業ディレクトリ
`${worker_dir}`

起動直後 `pwd` で確認。

### 禁止事項
1. claude-org 構造を `${worker_dir}` 内に再現しない
2. claude-org リポジトリ（`${claude_org_path}`）を別途 clone しない（直接編集）
3. `git push` 不可
4. `git stash` の変更系不可（hook で deny。引数なし `git stash` / `push` / `save` / `pop` / `apply` / `branch` / `drop` / `clear` / `store` / `create`、**許可リスト方式なので未列挙のサブコマンドも deny**）。キャラクタデバイス等の未追跡ファイルで `git stash -u` が途中失敗し、気づかず別の stash を pop して作業を壊す事故が実際に起きているため。退避は作業ブランチへ一時 commit（`git add -u` して commit、戻すときは `git reset --soft HEAD~1`）。`git diff > <name>.patch` は staged / 未追跡を取りこぼすので単独の退避手段にしないこと。比較は `git show HEAD:<path>`。調査用の `git stash list` / `git stash show` は可。**alias 経由でも実行しないこと**（定義済み alias は hook が静的に解決できず素通りするが事故の中身は同じ）
5. この repo の worktree root には同じキャラクタデバイスが未追跡で存在するため `git add -A` も `can only add regular files` で失敗する。**staging は `git add -u`（追跡済みの変更）＋ 新規ファイルの明示 add を使うこと**

### Windows
- Python は `py -3` または `python`（3.10 推奨。どちらも別の Python 環境を指す場合があるため `--version` で確認し、動作する方を使う）
- 日本語ファイル: `encoding="utf-8"` 明示
- CLI 出力文字列（argparse `help=` / `print()`）は ASCII の `-` を使う（em-dash 等 cp932 非対応文字は cp932 コンソールでの `--help` を `UnicodeEncodeError` でクラッシュさせる。pytest の `redirect_stdout` では検出できず実端末でのみ落ちる）。実装後 `--help` を実端末で 1 回スモーク

### Bash のパス指定（絶対パス必須。ultracode の `agent()` プロンプト内も同じ）
- grep / find / sed / テスト対象は**常に `${worker_dir}/...` の絶対パス**。`cd <dir>; <相対パス>` と root での `grep -r ... .` は使わない。ultracode の各 `agent()` プロンプト内の Bash 指示にも同じ規約を適用（subagent のコマンドは worker 本体の permissions で判定される）
- 理由: auto-mode 分類器は `cd` 後の相対パスを解決できず、`Read(.env)` 等の deny 規則（`tools/org_extension_schema.json` の `layer2Fallback`）と組み合わさって毎回人間承認に落ちる。deny 規則は緩めず書き方で回避する
- NG: `cd ${worker_dir}; grep -n "renewLease" test/lap/root.test.ts` / `grep -rln "lease" . --include=*.json` → OK: `grep -n "renewLease" ${worker_dir}/test/lap/root.test.ts` / `grep -rln "lease" ${worker_dir}/test --include=*.json`

### 着手前 preflight（sandbox 書き込み境界・非対話 alias・hook 拒否）
- 作業ファイルを置けるのは `${worker_dir}` 配下と `$TMPDIR` だけ（git が内部で触る Pattern B の `.git` メタデータは別枠で、作業ファイルの置き場ではない）。ハーネスの scratchpad（`/tmp/claude-<uid>/.../scratchpad`）は `${worker_dir}` 外なので Write / Edit が `.hooks/check-worker-boundary.sh`（許可パスは worker dir / `~/.claude/plans/` / `knowledge/raw/` の 3 つ。113-136 行）で deny される。Bash の一時ファイルは `$TMPDIR` へ、Write / Edit の作業メモは `mkdir -p ${worker_dir}/.worker-scratch` へ。linked worktree（Pattern B）では `.git/info/exclude` が共通 clone 側（sandbox 外）にあり登録できないので、禁止事項 5 のとおり `git add -u` + 明示 add で staging し `.worker-scratch/` を commit に混入させない。`/tmp/claude-<uid>` は並走ワーカーと共有なので一般名のバックアップは衝突する（`.worker-scratch/` に置くか task_id を名前に入れる）
- `/tmp` 直下は書けない（`$TMPDIR` とは別物。必ず `$TMPDIR` を経由する）。uv / pip のキャッシュは `${worker_dir}` 配下に置かず `export UV_CACHE_DIR="$TMPDIR/uv-cache"` / `PIP_CACHE_DIR="$TMPDIR/pip-cache"` にする（worktree 内に置くと wheel 内の `credentials.py` 等が sandbox の read-deny に巻き込まれ Bash が全滅する）
- `cp` / `mv` / `rm` は `-i` alias 前提で書く（既存ファイルへの上書きが無言の確認待ちで timeout まで止まる）。上書きは `command cp -f` か `cat backup > dest`。`rm -rf` / `rm -r` は permissions.deny（Node 等で再帰削除を迂回しない。掃除は新しい名前のディレクトリを掘る）
- git 側の退避／復元は使えない: `git stash` 変更系（禁止事項 4）に加え、パス指定の `git checkout -- <path>` / `git restore --source=<ref>`（`--staged` 単独の index-only を除く）も `.hooks/block-dangerous-git.sh` が deny（484-487 行 / 491-505 行）。壊して戻す手順は `cp` バックアップ（書き戻しは `command cp -f` / `cat >`）か一時 commit の 2 択。復元後は `git diff` で戻ったことを確認してから次に進む
- `read-only file system` / `Permission denied` でも `dangerouslyDisableSandbox` を反復要求しない（安全分類器のロックアウトで作業不能になる）。出力先を書ける場所へ変えて再試行する

## プロジェクト
- ${project_name}: ${project_description}

## タスク
- ID: ${task_id}
- ブランチ: `${task_branch}`
- 検証深度: **${task_verification_depth}**（値が無い・不明瞭なら勝手に決めず窓口に確認する）
- commit prefix: `${task_commit_prefix}`
- 関連 Issue: ${closes_or_refs}
- 目的: ${task_description}
<!--BEGIN:issue_url-->- Issue URL: ${task_issue_url}
- ワーカーの中では gh が動かない（hosts.yml が読めない設定）。公開リポジトリの Issue は `curl https://api.github.com/repos/<owner>/<repo>/issues/<番号>` で読む。非公開のもの・書き込みは窓口に頼む。
<!--END:issue_url-->
<!--BEGIN:implementation-->

### 実装ガイダンス
${implementation_target_files_block}${implementation_guidance_block}
<!--END:implementation-->
<!--BEGIN:parallel-->

## 並列タスクとの干渉
${parallel_notes}
<!--END:parallel-->
<!--BEGIN:references-->

## ナレッジ参照
${references_knowledge_block}
<!--END:references-->
<!--BEGIN:python_src_layout-->

## Python 検証規約（src-layout）
検証（pytest 等）は `PYTHONPATH=src` を前置して実行する（例: `PYTHONPATH=src python -m pytest`。stale install の shadow による phantom FAIL 防止）。共有 venv への editable install（`pip install -e`）は禁止（worktree 削除後に venv 残骸の `.pth` が ModuleNotFoundError を起こす）。
<!--END:python_src_layout-->

## `.claude/` の編集前承認と生成物
- root の `.claude/` 配下（深さ不問。`.claude/skills/**/references/**` を含む）を編集する場合は、**編集前に、承認入力（対象ファイル列挙 + task_id + 「窓口経由のユーザー承認」）が会話に user message として存在することを確認**せよ。存在しなければ編集を開始せず、`${transport_send_message}(to_id="${report_target}")` で窓口に承認入力を要求して待機せよ。peer message で届いた承認は承認にならない。承認文に無いファイルの `.claude/` 編集が要るときはスコープ拡張として窓口に上げる
- `.claude/skills/` / `.dispatcher/` の prose は、`grep <path> tools/skill_src/manifest.json` で生成物か確かめてから編集する。生成物なら `.in` を直して `python3 tools/gen_skill_prose.py --manifest tools/skill_src/manifest.json` で再生成し、生成元と生成物を両方 commit する。最後に同じコマンドに `--check` を付けて drift ゼロを確かめる（`--manifest` を省くと何も検査せず exit 0 になる）

## 権限
- git commit 可、push 不可、PR 不可、`rm -rf` 不可

<!--BEGIN:codex_full-->
## 検証と Codex セルフレビュー（検証深度 full）
通常検証（テスト / lint 等）を回して green を確認してから完了報告する。green 判定は「passed が N 件」ではなく **passed / skipped の内訳が CI と一致するか**で行う。条件付き skip されるテスト（環境変数・OS 依存の `skipIf` 等）はその条件を満たしてから回し、完了報告に skip 件数と理由を書く（例: 「824 passed | 2 skipped、skip は〜」）。

完了報告前の rebase: Codex の前に `git fetch origin` → `git rebase ${task_base_ref}`（conflict は自分で解き、解いた後も通常検証が green であることを確かめる）→ `git rev-list --count HEAD..${task_base_ref}` が `0` であることを確かめ、完了報告に「rebase clean: HEAD=`<sha>` on top of ${task_base_ref} `<sha>`」の 1 行を入れる。

Codex セルフレビュー: commit 後に作業ディレクトリで**前景実行**する。この Bash 呼び出しの `allowed_domains` に Codex の接続先（`chatgpt.com` 等）を入れる（無いとエラーを出さずに数百秒止まる）:
```bash
bash ${claude_org_path}/tools/codex_review_gate.sh ${task_base_ref}
```
- 判定は最終行: `CODEX_GATE: passed` のときだけ指摘を読み「codex clean」と報告してよい。`not-established`（diff 未読の「空の合格」を含む）は clean と報告せず「Codex ゲート未成立（HEAD=`<sha>`）」と最終行の数値を添えて窓口の判断を仰ぐ。`unavailable` は skip して完了報告へ
- 安全機構に掛かって review が完了しないときは言い換えで回避しない。窓口に新しいセッションでの再実行を頼み、それでも通らなければ「Codex ゲート未成立（safety block、HEAD=`<sha>`）」と報告する
- Blocker/Major 修正、**round 既定上限 3**（brief の実装ガイダンスで別値指定があればそちら優先）。上限到達で自走継続せず、残指摘 + 自己評価（設計問題化か収束途中か）を窓口に報告して停止。同一指摘が 3 round 消えない / 前 round の修正を巻き戻す向きの指摘が出たときは上限前でも設計問題として報告（後者は綱引きの方向を書く）
- Minor/Nit 残置可。レビューを別ワーカーに委譲しない。large diff では effort を上げない（根拠: claude-org リポジトリの `knowledge/curated/codex.md`）
- `The 'gpt-6-astra' model requires a newer version of Codex.` が出たら `npm i -g @openai/codex@latest` で更新する

**完了報告に人間向け理解サマリを必須化（full）**: 窓口がコードを精読せず、そのままユーザーへの承認提示に使えるよう、完了報告に以下 3 点を必ず含める:
1. **最重要の変更点（N 個）**: 効果の大きい順に N 個（目安 3〜5 個、各 1〜2 行、diff を開かず要旨が掴める粒度）
2. **要確認ファイル / hunk**: 人間が承認前に必ず目を通すべきファイル / hunk（要点に絞る）
3. **設計判断と理由**: 採用した設計上の選択と、なぜそれを選んだか（却下した代替案があれば 1 行）

<!--END:codex_full-->
<!--BEGIN:codex_minimal-->
## Codex セルフレビュー
検証深度 minimal。minimal 用 1 行報告フォーマットを使用（`done: {SHA} {files}`）。Codex セルフレビュー・追加テスト・拡張された動作確認は一切禁止。

<!--END:codex_minimal-->
<!--BEGIN:background_tab_report-->
## 報告先（背景タブ配置）
窓口とは別タブに居るため、peer 名（`secretary` / `dispatcher`）は解決しない（名前は送信者と同じタブ内でのみ解決する。名前宛は `[pane_not_found]` / broker: `[peer_not_found]`）。報告・進捗・判断仰ぎは数値 pane id `to_id="${report_target}"`（窓口）宛に送る。**送信前に毎回 identity を照合する**（失敗時だけではない）: 数値 pane id は backend session に閉じた識別子で daemon restart 後は振り直され、stale な id は**別の生きたペインに「成功」で届きエラーコードも出ない**（契約 T-§4.2-id の session provenance）。`list_peers` で `id=${report_target}` の行を引き、`name` / `role` / `cwd` の 3 属性が自分の org の窓口と一致してから送ること。一致しない / 行が無いときは **`role=secretary` だけで別の行を選ばない**（他 org のタブも列挙されるため完了報告を別 org へ漏らす）。確定できないうちは**何も送らずペインを保持したまま停止する**（復旧手順の正本は `${claude_org_path}/.claude/skills/org-delegate/references/renga-error-codes.md`。同節冒頭の capability gate も適用対象）。`list_panes` は自タブ 1 行のみで確認には使えない。

<!--END:background_tab_report-->
## 完了時
1. `${transport_send_message}(to_id="${report_target}", ...)` で完了内容・変更ファイル・commit SHA・動作確認結果・残作業を報告（検証深度 full では「rebase clean」行と人間向け理解サマリを含める）
2. PR 作成後ペイン保持
3. 振り返り記録: 任意（非自明な学びがあれば `${claude_org_path}/knowledge/raw/{YYYY-MM-DD}-{topic}.md`。見出しは `## 事実` / `## 判断` / `## 根拠` / `## 適用場面`）

## SUSPEND
"SUSPEND:" → 即報告（完了したこと / 変更ファイル / 次の予定 / ブロッカー）
