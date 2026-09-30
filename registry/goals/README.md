# ゴール台帳（registry/goals/）

次タスク提案（[`/work-discovery`](../../.claude/skills/work-discovery/SKILL.md)）は、ここに書かれたゴール条項を基準に候補を並べる。設計は [`docs/design/work-discovery-triage.md`](../../docs/design/work-discovery-triage.md) §12。

- **ゴールは各オペレーターが書く**。台帳ファイルは git 管理外（`.gitignore` 済み、`registry/projects.md` と同じ扱い）。git 管理されるのはこの README と [`registry/goals/example.md`](example.md) だけ。
- **ゴール台帳の無いプロジェクトからは候補が出ない**。提案時に「ゴール未設定」と 1 行出るので、そのプロジェクトの台帳を書く。

## 置き場

`registry/goals/<owner>/<repo>.md`（GitHub の `owner/repo` を小文字にしたもの）。例: `https://github.com/Example-Org/Example-Repo` なら `registry/goals/example-org/example-repo.md`。
`registry/goals/` 直下のファイル（この README と example）は台帳として読まれない。

## 書き方

```markdown
# <自由なタイトル>

## G1 <条項の見出し>
<条項の本文（任意、複数行可）>
- unmet if: <この条項が未達とみなされる条件>
- unmet if: <条件は 1 行以上>

## G2 <次の条項>
- unmet if: ...
```

- 条項見出しは `## G<番号> <見出し>`。番号は 1 から欠番・重複なしの連番。**上にある条項ほど優先**される。
- 各条項に `- unmet if:` の行が 1 行以上必要。
- `## G…` 以外の見出しや前置きは自由に書いてよい（読まれない）。
- 形式が崩れているとそのプロジェクトは「ゴール台帳エラー」として候補を出さず、理由が提案に表示される。

## 候補がどう並ぶか

1. 各候補（open Issue）がどの条項の「未達」を解消する仕事かを、判定段（`claude -p`）が当てはめる。
2. 当たった条項の番号順に並べる（G1 に当たる候補が先）。同じ条項の中は従来の優先度ラベル等で並べる。
3. どの条項にも当たらない候補は出さない（除外枠に件数と理由が出る）。

候補を「今はやらない」と答えると記録され、その Issue に動きがあるまで再提示されない。

## 運用前提と送られるデータ

- 判定段は `claude -p` で `api.anthropic.com` に出る。窓口・dispatcher のサンドボックス設定（`sandbox.network.allowedDomains`）に `api.anthropic.com` を入れておくこと。入っていないと判定段がタイムアウトし、提案は失敗として表示される（1 時間は再試行しない）。
- 台帳を置いた repo についてだけ、候補の Issue のタイトル・要約・本文冒頭 600 文字・ラベルとゴール条項が判定段へ送られる。**台帳を置くことが、その repo の材料を送ることへの同意**になる。台帳の無い repo からは何も送られない。
- 費用は 1 回 0.50 USD、1 日 2.00 USD が既定の上限。判定結果は候補ごとに `.state/work_discovery/judgements/` に保存され、Issue の中身と条項が変わらない限り再判定しない。
