# Organization Config

> **同期注意**: CLAUDE.md には変数展開機構がないため、`.claude/skills/**` および `docs/contracts/role-contract.md` 内の `permission_mode` は `auto` リテラル直書きでハードコードされている（session #15 で Secretary が `acceptEdits` を誤代入した回帰を機に固定化）。このファイルの値を変更しただけでは skill / docs 側に反映されない。`default_permission_mode` を変更する場合は、以下も併せて手で書き換えること（`tools/gen_delegate_payload.py` だけはこのファイルを実行時に読むので追従する。`grep -rn '"auto"' .claude/skills docs/contracts` で取りこぼし確認）:
>
> - `.claude/skills/org-start/SKILL.md`
> - `.claude/skills/org-delegate/SKILL.md`
> - `.claude/skills/org-delegate/references/pane-layout.md`
> - `docs/contracts/role-contract.md`

## Permission Mode
default_permission_mode: auto

選択肢:
- bypassPermissions: 全許可、確認なし（デフォルト）
- auto: 分類器による安全チェック付き（Team/Enterprise/API プランのみ）
- default: 都度確認
- acceptEdits: ファイル編集のみ自動許可
- dontAsk: 明示許可のみ

### Role別の適用範囲

`default_permission_mode` は Curator / Worker に適用される。他のロールは以下のように扱う:

- **Secretary**: 対象外。`--permission-mode` 未指定の Claude Code デフォルト挙動（ツール実行前に確認プロンプトを表示）を維持する。Secretary は人間との接点であり、人間判断を要する操作の自動承認を避けるため。詳細は Issue #10 を参照。
- **Dispatcher**: `default_permission_mode` の値にかかわらず、固定で `bypassPermissions` を使用する。理由は `.claude/skills/org-start/SKILL.md` の「ディスパッチャー」節を参照。

## Workers Directory
workers_dir: ../workers

ワーカー専用ディレクトリの配置先。claude-org リポジトリからの相対パス。
リポジトリ外に配置することで、ワーカーの新規プロジェクト作成時に親リポジトリの git コンテキストが干渉しない。

## Max Concurrent Workers
max_concurrent_workers: 8

broker 面（`ORG_TRANSPORT=broker` / コード既定）でのワーカー同時並列上限。runtime 0.1.31 / #104（backend-aware worker capacity）で導入。dispatcher が `claude-org-runtime dispatcher delegate-plan` helper に `--transport broker --max-concurrent-workers <値>` として明示で渡し、helper は rect geometry の balanced split（`choose_split`）を**バイパス**して「アクティブ worker 数 < 上限なら固定 spawn target で spawn / 到達で `split_capacity_exceeded`」を返す（runtime は panes snapshot から transport を推定しないため dispatcher が `ORG_TRANSPORT` を解決して明示で渡す契約）。

- **既定 8**。`unlimited`（上限なし）は opt-in。
- **renga 面（`ORG_TRANSPORT=renga`, opt-in）で参照されないのは、caller タブ内に配置する既定経路のときだけ**: renga はターミナルサイズと MIN_PANE 制約が許す限り分割し続ける rect ベース balanced split が律速するため、この経路では `max_concurrent_workers` は参照されない。
- **例外 — overflow 時は renga 面でもこの値が live になる**（runtime 0.1.39 以降）: dispatcher が `--overflow-to-new-tab` を armed にし、**かつ `--server-capability spawn_tab` を宣言した**場合に限り、rect 上限が消える代わりに本値が **fleet ceiling** として効き、`--peers-json`（全タブを跨ぐ worker census）が**必須**になる（欠落は `input_invalid` / exit 1）。`spawn_tab` 未宣言なら overflow は発火せず caller タブ配置に降格する。このとき本値は「同時ワーカー数の上限」であると同時に「**overflow が発行してよい新規タブ数の上限**」でもある。**現行 ja はこのフラグを渡していない**（dispatcher の実呼び出し経路に配線されていない。説明文中の言及を除き、実行される呼び出しは存在しない）ため、今日の運用値としては broker 面のみ load-bearing。詳細は [`.claude/skills/org-delegate/references/pane-layout.md`](../.claude/skills/org-delegate/references/pane-layout.md)「ワーカーの balanced split 戦略」と [`.dispatcher/CLAUDE.md`](../.dispatcher/CLAUDE.md) の delegate-plan helper 節を参照。
- 判定ロジック・定数の正準 SoT は `claude_org_runtime.dispatcher.runner`（runtime 側）。本ファイルの値は dispatcher が helper へ渡す運用値の導線。

## Triage Home
triage_home: off

work-discovery の cross-repo triage で home repo（claude-org-ja 自身）を scan 対象に含めるかどうかの設定。

- **既定 off**。キー欠落も off、不正値も off として扱う（`signals[]` に記録して fatal にはしない）。
- `on` / `yes` / `true`（case-insensitive）で opt-in、`off` / `no` / `false` で明示 off。
- **off のとき resolver は home repo の解決自体を行わない**（`git remote get-url origin` も `gh repo view` も呼ばない）。
- 設定行は**行頭（列 0）から `triage_home:` で始める**こと。resolver は列 0 固定で読むため、引用や箇条書きの中に書いた `triage_home: ...` は設定として拾われない。

**注意**: 散文中に `triage_home: on` のような文字列を**行頭から**書かないこと（パーサが拾う）。箇条書き（`- ...`）やバッククォート内は列 0 でないので安全。

解決の実装は [`tools/work_discovery_repos.py`](../tools/work_discovery_repos.py)、設計は [`docs/design/work-discovery-triage.md`](../docs/design/work-discovery-triage.md) §10.4 を参照。

## Integration Branch
integration_branch: -

作業中の統合用ブランチ（複数タスクの PR を `main` ではなく一時的に集約するブランチ）を書いておく欄。`-` / 空は未設定。

- 設定中に `--base-ref` も projects.md の `base_branch` 列も無いまま派遣しようとすると、`tools/gen_delegate_payload.py preview` が「統合用ブランチ宛てなら `--base-ref <branch>` を付けて再生成」という warning を出す（Issue #1152）。
- **警告を出すだけで、起点ブランチは変えない**。優先順位 `--base-ref` > projects.md `base_branch` 列 > `origin/HEAD` はそのまま。`main` 宛てで正しいタスクは `--base-ref main` を明示すれば警告は出ない。
- 統合作業が終わったら `-` に戻す。
- 設定行は**行頭（列 0）から `integration_branch:` で始める**こと（`triage_home` と同じく列 0 固定で読む）。

## Merge Approval
merge_approval_mode: acceptance

CI が通った PR を窓口がマージする前に、人に仰いで止まるかどうかの設定（Issue #1167）。値は `tools/merge_approval_mode.py` が読み、`/org-pull-request` と `/org-conveyor` はその出力で分岐する。この組織の設定値は `acceptance`。

- `human`: CI が通ったら窓口は人にマージの判断を仰いで止まる。キーが無い・値が不正なときも `human` として扱う（不正値は stderr に警告）。読めない設定で人の確認を飛ばさないための安全側の扱い。
- `acceptance`: 承認済みのスコープの内側では、受け入れテストのゲートと CI が通れば人に仰がずにマージし、結果を事後に報告する。人に上げるのは止める線（受け入れテストの宣言なし・判断境界・元に戻せない変更・予算超過・テストを書き換えて合格させた疑い・ゲートの拒否）に当たったときだけ。止める線の一覧は [`/org-pull-request`](../.claude/skills/org-pull-request/SKILL.md) 2b-i が正本。ただし、変更したファイルが全部 Markdown（`*.md`）か `docs/` 配下の PR（docs-only）は、受け入れテストの宣言が無くても止める線に当たらない（オーナーが事前に承認済み。判定は [`tools/acceptance_merge_hook.py`](../tools/acceptance_merge_hook.py) の `docs_only`）。変更が空の PR と、他のファイルを 1 つでも含む PR は従来どおり止まる。
- どちらの方式でも、窓口のマージの手前では受け入れテストのゲート（[`.hooks/gate-acceptance-before-merge.sh`](../.hooks/gate-acceptance-before-merge.sh)）が動く。この設定が変えるのは止まるかどうかだけ。
- 上書き手段（環境変数・スコープ単位）は無い。この 1 行だけが設定。`/org-conveyor` はスコープ承認時の値を契約に記録し、稼働中に `acceptance` へ広げた変更は再承認まで `human` として扱う。
- パイロット中の既知の限界: attention watcher の `ci_passed` 通知は方式に関わらず鳴る。窓口がマージ前に head を確かめてから、フックが head を fetch するまでの間の push は検出できない。
- 設定行は**行頭（列 0）から `merge_approval_mode:` で始める**こと（`triage_home` と同じく列 0 固定で読む）。
