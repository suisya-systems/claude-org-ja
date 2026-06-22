# 承認スコープ契約（scope contract）テンプレートと規律

[`/org-conveyor`](../SKILL.md) の **唯一の事前人間ゲート**。`/work-discovery` が候補ごとに取る人間選択を、
**起動時に 1 回だけ取る機械契約** に畳み込んだもの。この契約の内側でだけ conveyor は再質問なしに自走し、
契約の外側に触れたら必ず halt する（[`.claude/skills/org-conveyor/SKILL.md`](../SKILL.md) INV-1〜INV-4）。

## なぜ機械契約として articulate するのか

「PR #635 round 6 まで自走可」「triage 上位 S 級を空き pane 分だけ自走」のような承認は、口頭では
輪郭が曖昧で、conveyor が「どこまでが承認内か」を機械的に判定できない。曖昧なまま回すと、
スコープ外への踏み込み（自動 merge / 別件混入 / 想定外作業の自己承認）が起きうる。そこで承認の輪郭を
**機械的に判定できる述語 + 予算** として書き下し、人間に確認を取ってから回す。これにより:

- conveyor は各候補・各 state transition を契約に照合して **投入可否を決定的に判定** できる。
- スコープの縁（contract の述語に合致しない / 判定不能）が **検出可能** になり、halt 契機が機械化される。
- 引き継ぎ（[`/secretary-resume`](../../secretary-resume/SKILL.md)）後も契約を読み戻せば同じ判定を再現でき、
  ループ状態をメモリに依存させない。

## 何を事前承認し / 何を絶対に承認しないか（境界・非交渉）

| 範囲 | 事前承認 | 根拠 |
|---|---|---|
| triage 投入（スコープ述語に合致する候補のみ） | ✅ する | 完了駆動ループの入口 |
| `/org-delegate` 派遣（合致候補・空き pane 内） | ✅ する | per-candidate 人間選択を契約へ畳み込み |
| worker iteration / verify | ✅ する | スコープ内作業 |
| **push / `gh pr create` / `pr-watch` CI 監視** | ✅ する | 起動時スコープ承認が [`/org-pull-request`](../../org-pull-request/SKILL.md) 2b-i の「ユーザー明示承認」前提を満たす |
| **merge** | ❌ **決して承認しない** | 不可逆点。常に PR ごとの独立人間ゲート（`feedback-merge-approval` / `feedback-no-overgate-after-decision`） |
| スコープ外候補の投入 | ❌ しない | scope 縁 → halt（INV-2 / INV-4） |
| worker escalation の一次承認 | ❌ しない | [`/org-escalation`](../../org-escalation/SKILL.md) 経由で人間へ（INV-3） |

> **push/PR を事前承認できる理由（merge と区別する）**: 人間が「この範囲は自走してよい」と明示した
> **持続的スコープ承認** が、in-scope な PR 作成までの mechanical pipeline を覆う（`feedback-no-overgate-after-decision`
> 「ユーザー判断確定後は不可逆点でのみ再承認」）。merge は不可逆点なので **この承認の射程外**に明示的に置き、
> 都度ゲートする。conveyor は bare な per-PR「OK」を merge 承認に流用しない。

## テンプレート

確定した契約を `.state/conveyor/scope-contract.md` に書き出す（ループ中の gate 判定で読み戻す SoT）。

```markdown
# Conveyor scope contract

- contract_id: <YYYY-MM-DD>-<topic>            # 例: 2026-06-23-bugfix-belt
- approved_by: human (窓口経由)                 # 承認した人間・経路
- approved_at: <YYYY-MM-DD HH:MM>              # スコープ承認を受けた時刻
- repo: <OWNER/REPO>                           # 対象リポジトリ（gh カレント解決でも可）

## scope predicate（機械的に判定できる述語）
- include: <述語>        # 例: label:bug AND size:S / #637 の follow-up に限る / PR #635 の review round
- exclude: <述語>        # 例: label:needs-design / 多ファイル設計判断を含むもの
- 判定不能候補の扱い: scope 縁として投入しない（halt）

## project context（pre-resolve 済み・org-delegate の Step 0 人間質問を回避するため）
- project: <registry/projects.md の通称>
- branch 規約: <feat/... 等>
- verify policy: <app code 変更時 /verify 必須 / docs-only は skip 等。references/verify-evidence.md 準拠>

## 並列・予算（退出条件 / バックプレッシャー）
- max_parallel: <起動時 free pane 数>          # references/exit-conditions.md
- codex_round_max: <既定 3>
- false_positive_streak_max: <既定 2>
- time_budget: <例: 2h>  /  max_iterations: <例: 10>
- PR キュー上限: なし（人間 merge が natural gate）

## merge gate（非交渉）
- merge は事前承認しない。CI green で halt し PR ごとに人間へ提示する。
```

> フィールドは固定キー（`label:` 等の述語はそのまま grep / gh フィルタに渡せる形）で書く。markdown だが
> conveyor が読み戻す **構造化契約** なのでキー名を勝手に変えない。

## 人間確認の手順（起動時 1 回）

1. 人間から受けたスコープ承認を上記テンプレートに articulate する。
2. **人間に読み返して確認を取る**（「この輪郭で自走します。merge は都度あなたが判断します」を含める）。
   確認が取れるまでループを開始しない。
3. 確認後 `.state/conveyor/scope-contract.md` に書き出し、[`.claude/skills/org-conveyor/SKILL.md`](../SKILL.md) Step 2 のループへ入る。

## 契約の拡大・変更（再確認が必要）

- スコープを **広げる**（include 述語の拡張 / 別 label の追加 / 予算の増額）のは **必ず人間の再承認** を経る。
  conveyor が自走中に「ついでにこれも」と契約を自己拡張してはならない（worker のスコープ拡張提案を一次承認しないのと同じ規律。
  [`CLAUDE.md`](../../../../CLAUDE.md)「worker への追加依頼の境界」と整合）。
- スコープを **狭める / 早期停止** は人間がいつでも指示できる（merge を止めれば pane が解放されずベルトが自然に詰まる）。
