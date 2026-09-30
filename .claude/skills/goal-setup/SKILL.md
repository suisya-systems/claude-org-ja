---
name: goal-setup
owner: secretary
description: >
  プロジェクトのゴール台帳（registry/goals/<owner>/<repo>.md）を人間と対話して新規作成・改訂する。
  README と open Issue からゴール候補を推定して提示し、「何を目指すか」「何が起きていたら未達か」を
  短く質問して条項を固め、書式検証（tools/work_discovery_goals.py validate）を通してから保存する。
  「ゴール台帳を書きたい」「ゴールを設定して」「ゴール未設定と出た」「台帳エラーを直して」
  「このプロジェクトの目標を決めたい」等や、/work-discovery の「ゴール未設定」「ゴール台帳エラー」案内を
  受けて窓口が起動する。候補の提示そのものは /work-discovery の役目で本スキルではない。
argument-hint: "[プロジェクト通称 または owner/repo]"
effort: medium
allowed-tools:
  - Bash(python3 tools/work_discovery_repos.py:*)
  - Bash(py -3 tools/work_discovery_repos.py:*)
  - Bash(python3 tools/work_discovery_goals.py validate:*)
  - Bash(py -3 tools/work_discovery_goals.py validate:*)
  - Bash(gh repo view:*)
  - Bash(gh issue list:*)
---

# goal-setup: ゴール台帳を対話で書く

[`/work-discovery`](../work-discovery/SKILL.md) は、オペレーターが書いたゴール台帳の条項に当たる Issue だけを条項順に提案する。
**台帳の無いプロジェクトからは候補が出ず、書式が崩れた台帳も候補ゼロになる**。本スキルは、その台帳を人間との対話で作る・直す。

- 書式の正本: [`registry/goals/README.md`](../../../registry/goals/README.md)（例: [`registry/goals/example.md`](../../../registry/goals/example.md)）
- 設計: [`docs/design/work-discovery-triage.md`](../../../docs/design/work-discovery-triage.md) §12.3
- 書式検証: `tools/work_discovery_goals.py validate`（scan と同じパーサで読む。ここを通らない台帳は保存しない）

起動主体は窓口。台帳はオペレーターのローカルファイル（git 管理外）なので、commit・PR はしない。
ゴールの中身は人間が決める。窓口は候補を推定して示すが、条項の採否・文言・順番はすべて人間の答えで確定させる。

## Step 1 — 対象リポジトリを決める

引数（`$ARGUMENTS`）の通称を GitHub の `owner/repo` に解決する。`owner/repo` 形式ならそのまま使う。

```bash
python3 tools/work_discovery_repos.py --format json
```

`included[]` の `nickname` と引数を照らして `repo` を取る（Windows は `py -3`）。

- 引数が無い、または一致が無い・複数あるときは、`included[]` の通称一覧を示して聞き返す。
- `opted_out[]`（triage 列で除外）にある通称なら、台帳を書いても候補は出ないことを伝え、先に `registry/projects.md` の triage 列を戻すかを聞く。
- `skipped[]`（パスが GitHub URL でない等。`reason` 付き）にある通称なら理由を伝え、`owner/repo` を直接教えてもらう。
- 台帳のパスは `registry/goals/<owner>/<repo>.md`（小文字）。

## Step 2 — 既存台帳を確認する

```bash
python3 tools/work_discovery_goals.py validate --repo <owner>/<repo>
```

出力は 1 行の JSON（`status` が `ok` なら exit 0、それ以外は exit 1）。

- `unset` → **新規作成モード**。
- `ok` → **改訂モード**。台帳ファイルを Read し、今の条項（G1 から順に見出しと unmet if）を人間に見せて、何を変えたいか（追加・削除・順番・文言）を聞く。
- `error` → **修復モード**。`error` の内容（例: `G2: no 'unmet if:' line`）をそのまま伝え、台帳を Read して直し方を提案する。`case mismatch` はファイル名を小文字に直す話なので、その旨を伝える。

## Step 3 — ゴール候補を推定して提示する

新規作成モードと、改訂で条項を足すときに行う。材料は 2 つだけ（読み取りのみ）:

```bash
gh repo view <owner>/<repo>
gh issue list --repo <owner>/<repo> --state open --limit 100 --json number,title,labels
```

README の目的と、open Issue のまとまり（同じ困りごとを指す Issue 群）から、ゴール候補を 3〜5 個に絞って示す。
各候補は「目指す状態 1 行 + 根拠になった Issue 番号 2〜3 個」。
Issue をそのまま条項にしない。条項は「状態」であり、Issue はその未達を解消する仕事である。

## Step 4 — 質問して条項を固める

候補を叩き台に、1 条項ずつ短く聞く（まとめて 5 問を並べない）:

1. **何を目指すか**: 見出しになる 1 行（例「利用者がセットアップで詰まらない」）。
2. **何が起きていたら未達か**: `unmet if` を 1 行以上。Issue が 1 件出れば当たるくらい具体的に（例「README の手順どおりに進めて失敗する報告が open のまま残っている」）。「品質が低い」のような判定できない文は、どんな報告・状態を指すかを聞き返して具体化する。
3. **順番**: 全条項が出そろったら並びを見せ、「**上にある条項ほど優先して提案されます**。この順で合っていますか」と確認する。

条項の本文（見出しと unmet if の間の説明）は任意。人間が話した補足があれば 1〜2 行で残す。

## Step 5 — 下書き → 書式検証 → 人間の確認 → 保存

1. README の書式どおりに下書きを作る: `## G<番号> <見出し>` を 1 から欠番なしの連番で並べ、各条項に `- unmet if: …` を 1 行以上。下書きは `$TMPDIR/goal-setup-<owner>-<repo>.md` に書く。
2. 書式検証を必ず通す:
   ```bash
   python3 tools/work_discovery_goals.py validate --file "$TMPDIR/goal-setup-<owner>-<repo>.md"
   ```
   exit 1 なら `error` を読んで下書きを直し、通るまで繰り返す。通らない台帳は保存しない。
3. 下書き全文（改訂モードなら変更前との差分も）を人間に見せ、次の点を**明示して**保存してよいか聞く:
   - この台帳を置くと、その repo の候補 Issue のタイトル・要約・本文冒頭・ラベルとゴール条項が、提案の判定のため `claude -p` 経由で `api.anthropic.com` に送られる。**台帳を置くことがその送信への同意になる**（[`registry/goals/README.md`](../../../registry/goals/README.md)「運用前提と送られるデータ」）。
4. 明示の OK が出たら `registry/goals/<owner>/<repo>.md` に保存し（ディレクトリが無ければ作る）、保存先で再検証する:
   ```bash
   python3 tools/work_discovery_goals.py validate --repo <owner>/<repo>
   ```
   `status` が `ok` であることを確認して終える。「次の仕事候補を出すなら `/work-discovery`」と 1 行添える（自分では起動しない）。

## やらないこと

- 人間の答えを待たずに条項を確定・保存しない。推定した候補はあくまで叩き台。
- 台帳を commit しない・公開リポジトリに入れない（`registry/goals/` の台帳は `.gitignore` 済み）。
- Issue や repo に書き込まない（`gh` は `repo view` / `issue list` の読み取りだけ）。
- 候補の提案（triage）や委譲を始めない。それは `/work-discovery` → `/org-delegate` の役目。
