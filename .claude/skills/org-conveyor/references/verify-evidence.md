# verify 統合: applicability classifier + エビデンス転記

[`/org-conveyor`](../SKILL.md) Step 2-6 の verify 統合の詳細。verify を **条件付き必須** にする判定（applicability
classifier）と、その結果を PR 本文 `## Test plan` へ機械転記する規律を定める。狙いは「app code を触ったのに
動作未確認のまま PR を流す」ことを防ぎつつ、docs / config だけの差分に無意味な verify を強制しないこと。

> **実行主体（重要）**: `/verify` は Claude Code の **組込みスキル**（app を起動して実行ビヘイビアを観測する）。
> verify を **実行するのは worker** であり、**app + 当該変更が居る worker の worktree 内**で走らせる。conveyor（窓口
> セッション・リポジトリルートで worker 変更を持たない）は **app を起動しない** — conveyor の責務は (1) applicability
> classifier で verify 要否を gate、(2) worker が返したエビデンスを PR `## Test plan` へ転記、の 2 つだけ。組込み `/verify` が
> app 形態に合わない場合は、worker が同等の app 起動コマンドを直接叩いて証跡を残す（[`.claude/skills/org-conveyor/SKILL.md`](../SKILL.md) Step 2-6）。

## applicability classifier（diff touch domain で判定）

worker は完了報告に **diff の touch domain** を、自身の worktree で得た `git diff --name-only <base>..HEAD` の出力ごと
申告する（worker 側が一次ソース。HEAD が worker のブランチに在るのは worker のみ）。conveyor はその申告ファイル一覧を
パス分類して `/verify` 必須かを決定的に判定する。worker のブランチが窓口セッションから local に見える場合
（object store 共有の Pattern B worktree 等）は conveyor 自身も `git diff --name-only <base>..<branch>` を再実行して
申告と突き合わせる（read-only の裏取り）。local に見えない場合（別 clone・push 前等）は PR 作成後の変更ファイル一覧
（`gh pr view --json files`、[`/org-pull-request`](../../org-pull-request/SKILL.md) 経由）で突き合わせる。

| touch domain | 例 | `/verify` |
|---|---|---|
| **app code** | アプリ実装・ランタイム挙動を変える source（`*.py` / `*.ts` の実装・CLI・サーバー・ツール本体・hook 等） | **必須** |
| **docs** | `*.md` / `docs/` / コメントのみ | 不要 |
| **config** | 設定値・lint / CI 設定・依存ピン（挙動を変えないもの） | 不要（挙動を変える config は app code 扱い） |
| **fixture / test data** | golden / fixture / サンプルデータ更新のみ | 不要（テスト本体のロジック変更は app code 扱い） |

判定規律:

- **混在（app code + docs/config/fixture）** → app code の存在が支配的。**`/verify` 必須**。
- **app code が無く docs / config / fixture のみ** → `/verify` 不要（理由を Test plan に 1 行記す: 例「docs-only、ビヘイビア変更なし」）。
- **判定不能**（touch domain がパス分類と食い違う / どちらに倒すべきか機械的に決まらない / app code か config か曖昧）
  → **scope 縁として halt**（[`.claude/skills/org-conveyor/SKILL.md`](../SKILL.md) INV-2）。誤って verify を skip して挙動退行を見逃すより、人間に上げる。
- 本スキル（claude-org 自身の `.claude/` 編集）のような **実行可能アプリを持たない skill/docs タスク**は docs 扱いになりうるが、
  その場合 verify policy（[`.claude/skills/org-conveyor/references/scope-contract.md`](scope-contract.md) の `verify policy`）に明記し、Test plan には
  「skill prose のみ・実行ビヘイビアなし」等の不要理由を残す。

> classifier はパスベースの決定的判定を一次に置き、worker の申告と食い違ったら **食い違い自体を halt 契機**にする
> （申告を鵜呑みにしない / パス分類を鵜呑みにしない、の二重チェック）。

## エビデンス転記（PR `## Test plan` へ自動転記）

`/verify` を実行した（または不要と判定した）ら、再現可能な証跡を完了報告に含め、[`/org-pull-request`](../../org-pull-request/SKILL.md)
2b-i の PR 作成時に PR 本文 `## Test plan` セクションへ **そのまま転記** する。窓口がコードを精読せずに、また将来の
レビュアーが追試できるように、**再現コマンドと観測結果** を残すのが要件。

完了報告に含めるエビデンス（worker が用意し、conveyor が転記）:

- **再現コマンド**: verify で実際に叩いたコマンド（動的ポートは env 名で示す。[`.claude/skills/org-conveyor/references/dynamic-ports.md`](dynamic-ports.md)）。
- **観測結果**: コマンド出力の要点 / 終了コード / スクリーンショットの保存パス（UI verify の場合）。
- **applicability 判定**: app code 触れ有無と classifier の結論（必須実行 or 不要理由）。

PR 本文へ転記する形（`## Test plan`）:

````markdown
## Test plan

- applicability: app code を変更（`tools/foo.py`）→ /verify 必須
- repro:
  ```
  PORT=$(python3 -c 'import socket;s=socket.socket();s.bind(("",0));print(s.getsockname()[1]);s.close()')
  PORT="$PORT" tools/run.sh &       # 動的ポート（references/dynamic-ports.md）
  curl -s "localhost:$PORT/health"  # → {"status":"ok"}
  ```
- result: health 200 / 終了コード 0 / 退行なし
- screenshot: .state/conveyor/evidence/<task_id>/health.png   # UI の場合
````

docs / config / fixture のみで verify 不要だった場合も、Test plan に **不要理由を 1 行** 残す（空欄にしない）:

```markdown
## Test plan

- applicability: docs-only（`.claude/skills/**/*.md` のみ）→ 実行ビヘイビア変更なし、/verify 不要
```

> 転記は **machine transcription**（worker が出した証跡を窓口が整形・貼付する）であって、窓口がエビデンスを
> 自作・補完することではない。エビデンスが欠落していたら通常の review-feedback として worker に補完を依頼する
> （[`/org-pull-request`](../../org-pull-request/SKILL.md) 2c）。
