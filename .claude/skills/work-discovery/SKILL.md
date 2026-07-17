---
name: work-discovery
description: >
  open Issue を triage して「次の仕事候補（N 件 + 推奨 1 件）」を窓口が人間へ提示する。
  決定的ツール tools/work_discovery_scan.py を 1 回実行し、その候補 JSON を
  設計書 §5.2 の人間可読フォーマットでレンダリングするところで停止する（propose-only）。
  起動主体は窓口に限定。手動 / イベント起動のみ（常駐 /loop なし）。
  「次の仕事候補出して」「triage して」「次なにやる？」や PR マージ後の
  proactive next-dispatch で窓口が手動起動する。
effort: low
allowed-tools:
  - Bash(python3 tools/work_discovery_repos.py:*)
  - Bash(py -3 tools/work_discovery_repos.py:*)
  - Bash(python3 tools/work_discovery_scan.py:*)
  - Bash(py -3 tools/work_discovery_scan.py:*)
---

# work-discovery: 次の仕事候補の triage 提示（提案のみ）

open Issue を triage し、依存解決済みの候補を「N 件 + 推奨 1 件」の形で**窓口が人間へ提示する**。
判定（scan・ランク付け）は決定的ツールが担い、本スキルはその出力を人間可読に整形して見せるだけ。
**候補を出したら停止する。着手判断は人間が行う。**

- 設計一次参照: [`docs/design/work-discovery-triage.md`](../../../docs/design/work-discovery-triage.md)
  （§5.2 人間可読レンダリング / §6.2 案 B ローカル skill / §7 不変条件 INV-1〜5）。
- 計算層ツール: [`tools/work_discovery_scan.py`](../../../tools/work_discovery_scan.py)（read-only・副作用ゼロ。本スキルが消費する計算層）。
- repo セット解決ツール: [`tools/work_discovery_repos.py`](../../../tools/work_discovery_repos.py)（read-only。`registry/projects.md` の triage opt-in 列 + home repo 常時包含から scan に渡す `--repo owner/repo` セットを決定的に導出。設計 §10.4）。
- 本スキルは案 B（手動エントリ）。定常トリガ（dispatcher 拡張）と post-merge 統合は別 Phase（別タスク）。

## 起動主体とトリガ（厳守）

- **起動できるのは窓口だけ**（設計 §6.2）。委譲済みワーカーは本スキルを起動しない。
  ワーカーが自タスク外の「次の仕事」探索を起動すると「1 worker = 1 task = 1 scope」を崩すため
  （[`CLAUDE.md`](../../../CLAUDE.md) の役割境界）。
- **常駐 `/loop` を付けない**（設計 §6.2 留意 1）。手動起動、または PR マージ後の
  proactive next-dispatch のようなイベント起点でのみ走らせる。時間ベースの定常起動は
  変化の無い日に提示を汚すため避ける。
- 定常トリガ（worker クローズ）は別案（dispatcher 拡張・別 Phase）の責務であって本スキルではない。

## 不変条件（破ってはならない / 設計 §7）

- **INV-1 propose-only**: 本スキルの出力は「候補リスト + 提示」のみ。生成後に**停止する**。
  spawn / delegate / ブランチ作成 / commit / PR / Issue・PR への書き込みを**一切しない**。
  （allowed-tools が repo 解決（`tools/work_discovery_repos.py`）と scan（`tools/work_discovery_scan.py`）の
  read-only コマンドだけに絞られているのは、この不変条件の機械的担保でもある。resolver も scan と同じく
  read-only で、`git remote get-url` と任意の `gh repo view` 読み取りのみを行い、書き込み・spawn・git 変更をしない。）
- **INV-2 着手判断は人間ゲート必須**: 候補の選択は人間のみ。選ばれた候補は
  **既存の [`/org-delegate`](../org-delegate/SKILL.md) の Step 0 から**通常委譲フローに入る。
  本スキルが org-delegate を自分で呼ぶことは禁止。推奨（rank 1）の自動着手も禁止。
- **INV-3 自動 commit / 自動 PR をしない**: ソースツリー・Issue・PR・git（commit / branch / push）を
  一切変更しない。triage 結果をソースに残す運用にする場合も、それは別途人間判断による別タスクであり、本スキルが自動で行わない。
- **INV-4 窓口経由**: 提示は窓口セッションの会話内で人間へ行う。GitHub 等の人間可視面へ直接書かない。
- **INV-5 秘書は調査しない**: scan は決定的ツール実行であって「調査」ではない。候補の実現性深掘り・
  設計が要るなら、それは人間ゲートを通った後の委譲ワーカータスク。本スキル内で候補の中身を自前調査・実装しない。

## 手順

### Step 1 — repo セットを解決してから scan を 1 回実行する

本スキルは Claude が 2 つのコマンドを**順に**実行する（シェルスクリプトではないので `$(...)` 代入形は
使わない — allowed-tools は各コマンドの先頭 prefix で許可判定するため、`REPO_FLAGS=$(python3 …)` の
代入ラップは `python3 tools/work_discovery_repos.py:*` / `py -3 …` の許可 prefix にマッチしない）。POSIX は
`python3`、Windows は `py -3`。

**1a. resolver で `--repo` flags を得る**（`registry/projects.md` の triage opt-in 列 + home repo 常時包含から
`--repo owner/repo` の並びを決定的に導出。設計 §10.4）:

```bash
python3 tools/work_discovery_repos.py --format flags
```

- stdout は `--repo a/b --repo c/d` の 1 行。triage opt-in 行が 1 つも無い既定状態では home repo 1 つだけ
  （`--repo <home>` 1 回 = 従来の単一 repo scan と同一挙動）。**skip 情報・signal は stderr** に出る（stdout は
  flags 純粋）。`--format json` でもう一度走らせて `skipped` / `signals` を控えておくと Step 3 の監査提示に使える。
- **resolver が exit 2 を返したら 1b の scan を実行しない**。exit 2 は「home repo が git origin・`gh repo view` の
  両方で解決できず、かつ有効な triage opt-in 行も無く repos が空」の異常系。この場合は stderr の `error:` 行を
  そのまま人間へ伝えて停止する（**空の flags で scan を走らせると `--repo` 無し = gh カレントリポジトリの暗黙
  scan に無言フォールバックし解決失敗が隠れる**ため）。exit 0 のとき**だけ** 1b へ進む。

**1b. 1a の stdout（`--repo …`）を貼って scan を実行する**（exit 0 のときのみ）:

```bash
python3 tools/work_discovery_scan.py --trigger manual --repo suisya-systems/claude-org-ja
```

（`--repo …` の部分は 1a の stdout をそのまま貼る。opt-in が複数 repo にわたるときは `--repo` が複数並ぶ。）
- resolver は read-only（`git remote get-url` と任意の `gh repo view` 読み取りのみ。書き込み・spawn・git 変更なし）。
- `--trigger` は文脈ラベル。手動起動は `manual`、PR マージ後の proactive next-dispatch から呼ぶ場合は
  `--trigger post_merge` を付け、可能なら `--free-panes <空き worker slot 数>` も渡す（空き枠があると
  `parallelizable` 候補のランクが上がる）。
- `--free-panes` の意味は **空き worker slot 数**（＝ dispatch 可能な空き capacity の単位）であり、物理的な空き
  ターミナルペイン数ではない（runtime 0.1.31 / #104、backend-aware worker capacity 以降の読み替え）。broker 面
  （`ORG_TRANSPORT=broker` / コード既定）では `max_concurrent_workers`（既定 8, `registry/org-config.md`）から
  アクティブ worker 数を引いた残り、renga 面（opt-in）では rect ベース balanced split が受け入れ可能な空き split 枠。
  scan の計算ロジックはこの読み替えで変わらない（数を受け取るだけ）ので、窓口 / dispatcher が現行の輸送層に応じて
  空き slot 数を算出して渡す。
- 既定の候補上限は `--top-n 3`。`--repo` は resolver の flags で明示的に渡す（home repo を常に含む）。
  triage opt-in が複数 repo にわたる場合は `--repo` が複数並び、cross-repo triage になる（設計 §10 / §10.4）。
- ツールは stdout に**単一 JSON オブジェクト**を出し、**exit code で分岐**する（JSON パース成否ではなく exit code を見る）。
- ツールは read-only（`gh` の読み取りサブコマンドのみ）。本スキルがツール以外の副作用を出してはならない。

### Step 2 — exit code で分岐する

| exit | status | 窓口の対応 |
|---|---|---|
| `0` | `no_candidates` | 候補ゼロ。「いま着手可能な（依存解決済みの）候補はありません」と人間に伝える。`excluded_blocked[]` が非空なら Step 3 と同じ「除外（依存未解決）: #<issue>（<note>）」の形で**必ず列挙する**（「何を見た結果ゼロなのか」を人間が監査できるように。設計 §5.2「除外枠を必ず見せる」/ §5.1）。さらに `input_truncated.open_issues` / `open_prs` が `true`（取得上限到達）なら Step 3 と同じ「Issue/PR の取得が上限到達のため候補が網羅的でない可能性があります」を**必ず添える**（非網羅な scan を「網羅した結果ゼロ」と誤読させないため）。**ここで停止**。 |
| `10` | `candidates_found` | Step 3 で §5.2 形式にレンダリングして提示。 |
| `2` | `error` | JSON の `error` フィールドの内容をそのまま人間へ伝え、「triage を実行できませんでした」と報告。候補を捏造しない。**ここで停止**。 |

> exit `1` には意味を割り当てない（Python 未捕捉例外の既定 exit と衝突し、クラッシュが「候補なし」に誤読されるのを防ぐため）。`0/10/2` 以外が返ったら error 扱いで人間に上げる。

### Step 3 — §5.2 形式で人間へ提示する（exit 10 のとき）

JSON を SoT として、設計 §5.2 の人間可読フォーマットへ整形する。proactive next-dispatch の現行慣行
（候補 2〜4 件 + 推奨 1、番号で即決）と互換に保ち、人間の操作を変えない。

```text
次の仕事候補（triage 結果・提案のみ / 着手はあなたの判断です）:

1. [推奨] #531 Add retry to uploader（優先度 high / 工数 S(推定) / 依存解決済み / 並列可(推定) / 直近マージ起点(推定)）
   └ 直近マージ #528 の follow-up・並列可（空き pane を埋められる）・工数 S(推定)・依存解決済み
2. #533 Refactor config loader（優先度 medium / 工数 M / 依存解決済み / 並列可(推定)）

除外（依存未解決）: #540（#537 が open のため）

着手するものを番号で指定してください。着手判断後に /org-delegate を回します。
```

（上例の #531 は `effort_estimated: true` なので `工数 S(推定)`、#533 は `size:M` ラベル由来で `effort_estimated: false` のため `工数 M`（`(推定)` なし）。`直近マージ起点(推定)` は `unblocked_by_recent_merge == true` の #531 にだけ出ている。）

レンダリング規則（JSON フィールド → 表示）:

- `candidates[]` を `rank` 昇順に番号付きで並べる。各行の骨格: `<issue-ref> <title>（優先度 <priority> / 工数 <effort>[(推定)] / 依存解決済み[ / 並列可(推定)][ / 直近マージ起点(推定)]）`。`[...]` で囲んだトークンは条件付き（下記）。
- **`<issue-ref>` の repo 修飾（cross-repo scan、設計 §5.2 / §10.4）**: 候補の `repo` が `null` でない（複数 repo を横断した cross-repo scan = resolver が triage opt-in で複数 repo を返した場合）なら、`#<issue>` の代わりに `<repo>#<issue>`（例 `aainc/token-tracking#42`）で表示し、出自 repo の曖昧さを無くす。単一 repo scan（`repo: null`）では従来どおり `#<issue>`。除外枠（下記）・推奨も同じ規則で修飾する。同じ規則は `blocking_refs` の表示にも適用される（home 参照は素の `#N`、cross-repo 参照は `owner/repo#N`。設計 §5.1）。
- **推奨は 1 件だけ**。`recommendation` の `(repo, issue)` の組に一致する候補の先頭に `[推奨]` を付け、直下に `└ <recommendation.reason>` を添える（cross-repo では `issue` 番号だけでは `ja#60` と `runtime#60` が衝突しうるので、`repo` も併せて突き合わせる）。
- **推定軸には `(推定)` を付す**（「機械が断定した」と人間が誤読して着手判断を機構へ明け渡すこと（設計 §4.4）を防ぐため）。軸ごとにフラグを見て条件付きで付ける:
  - **工数**: `effort_estimated == true`（ヒューリスティック推定）なら `工数 <effort>(推定)`。`false`（`size:S/M/L` 等のラベル由来）なら `(推定)` を付けず `工数 <effort>`。
  - **並列可 / 直近マージ起点**: フラグ `parallelizable` / `unblocked_by_recent_merge` が `true` のときだけ該当トークン（`並列可` / `直近マージ起点`）を出す（`false` なら表記自体を出さない）。これらは対応する `*_estimated` が常に推定（`true`）なので、出すときは常に `(推定)` 付き。直近マージ起点は `recommendation.reason` 内（例:「直近マージ #N の follow-up」）にも自然に現れる。
- **除外枠を必ず見せる**: `excluded_blocked[]` を「除外（依存未解決）: #<issue>（<note>）」の形で列挙する（監査性 + 全部見たうえで N 件、の安心）。空なら除外行を省く。
- **サイレント truncation をしない**: `truncated_count` が 1 以上なら
  「（他に依存解決済みだが順位外の候補が <truncated_count> 件あります）」の 1 行を添える。
  `input_truncated` の `open_issues` / `open_prs` が `true`（取得上限到達）なら「Issue/PR の取得が上限到達のため候補が網羅的でない可能性があります」も添える。
- **resolver の skip / signal を監査のため添える（cross-repo）**: Step 1 で控えた resolver JSON の `skipped[]`（triage opt-in なのに `パス` が GitHub URL でなく owner/repo を導けず scan 対象から外れた行）や `signals[]`（home repo 解決が fallback / 失敗した等）が非空なら、候補提示の末尾に「scan 対象の解決メモ:」として 1〜数行で添える（例「triage opt-in 行『<通称>』はパスが GitHub URL でないため scan 対象外（skip）」）。どの repo を見た結果の候補なのかを人間が監査できるようにするため。空なら省略。
- **毎回必ず**「提案のみ / 着手はあなたの判断です」を出す（INV-1 の運用上の現れ）と、末尾に「番号で指定 → 着手判断後に /org-delegate」を出す。

### Step 4 — 停止する

候補を提示したら**そこで終わる**。番号選択は人間が行う。人間が番号を選んだら、その着手は本スキルの外で
**[`/org-delegate`](../org-delegate/SKILL.md) の Step 0 から**始まる（INV-2）。本スキルが org-delegate を呼ばない・spawn しない・commit / PR しない。

## パス解決

- 本スキル中の `tools/...` / `docs/...` 表記は**リポジトリルート相対**。窓口セッションの CWD は
  リポジトリルート（`/home/happy_ryo/work/org/claude-org-ja`）なのでそのまま実行できる。別 CWD から呼ぶ場合はルート相対に読み替える。
- Windows では `python3` を `py -3` に読み替える（allowed-tools に両形を登録済み）。

## やらないこと（INV まとめ）

- 候補の自動着手・rank 1 の自動委譲（INV-1 / INV-2）。
- spawn / delegate / ブランチ / commit / PR / Issue・PR 書き込み（INV-1 / INV-3）。
- GitHub 等への直接提示（窓口の会話を経由する。INV-4）。
- 候補の中身の自前調査・実装（人間ゲート後の委譲タスク。INV-5）。
- 常駐 `/loop` での時間起動（設計 §6.2 留意 1）。
- ワーカーからの起動（起動主体は窓口に限定。設計 §6.2）。
