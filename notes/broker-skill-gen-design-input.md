# 案 b（transport-neutral source + generator）設計の入力メモ

> Epic #586 の prose sweep を「手動の両系反転（案 a）」から「transport-neutral source +
> generator でスキルを機械生成する（案 b）」へ pivot した際の設計入力。
> 2026-06-16、手動反転ワーカー（broker-prose-sweep-t1、承認ゲートで stand down・作業未着手）の
> discovery 分析を保全したもの。canonical な反転テンプレ／ファイル別難度表の SoT は
> [`notes/broker-promotion-plan-586.md`](./broker-promotion-plan-586.md)（§1 テンプレ / §1.3 論理反転チェックリスト / §2 inventory）。
> 本メモを入力とした確定設計は [`notes/broker-skill-generator-design.md`](./broker-skill-generator-design.md)
> （2026-06-16 人間 ratified）。**本メモは pivot 時点の discovery を保全した provenance** であり、確定値は設計
> doc 側が正本（特に frontmatter の扱いは下記 §保持すべき不変条件の訂正注記を参照）。

## なぜ generator 方式が手動反転より筋が良いか（自己矛盾の解消）

- Tier1 の dual-system ブロックは「定型ブロックの既定宣言」と「本文の操作系ツール参照
  （`mcp__renga-peers__*`）」が現状一致している。片方だけ反転すると **ファイル内で自己矛盾**
  （ブロックは「broker で書いてある」と宣言するのに本文は renga のまま）。手動反転の最大の難所が
  まさにこの「本文ツール名の既定が transport に連動する」点。
- generator 方式なら source を **transport-neutral プレースホルダ**化して両系（broker 既定 /
  renga fallback）を機械生成でき、**この自己矛盾リスク自体が消える**。

## generator でも保持すべき不変条件

- frontmatter `allowed-tools` = permissions アンカーで **renga byte 不変**
  （[`notes/broker-promotion-plan-586.md`](./broker-promotion-plan-586.md) §3.2）。
- renga-fallback 固有参照（`server:renga-peers` / `<channel source="renga-peers">` / 切戻し手順）は温存。
- `new_tab` / `focus_pane` の **broker surface 不在**（意図的除外）注記は保持。
- attention watcher の **transport 非依存**（2 段承認の対象外）注記は保持。

> **訂正注記（設計 doc §0.4 / §9.2 #9、2 段の訂正を経た最新値）**: 上記 1 点目「frontmatter `allowed-tools` =
> renga byte 不変」を**「renga 恒等のまま broker 面に残す」と読むのは誤り**である。実機確認で SKILL.md の
> `allowed-tools` は実際に `mcp__renga-peers__*` を列挙しており（現状 14 スキル）、Claude Code がディスクからこれを
> 読んで**ツール認可をゲート**する。broker 本文 × renga 恒等 frontmatter は **broker 既定実行でツール未認可**を招く
> （R2 で検出）。その後 union 案も auth レビューで不適と判明（broker 既定で renga 面を認可したままにし
> `focus_pane`/`new_tab` 等の broker 省略ツールで auth 迂回が起きる）。**最新の訂正案は per-transport render**:
> frontmatter は本文と同じ broker 単一面で render し（renga は broker 面に出さない）。**実装は skill 固有サブセットを
> 保存する per-entry 接頭辞リネーム**（`mcp__renga-peers__<tool>`→`mcp__org-broker__<tool>`、ワイルドカードは renga
> source surface で明示展開してから per-tool リネーム）であり、**`rewrite_allow_entries`（role-tier 置換）は skill
> frontmatter には使わない**（tier 拡大で過剰認可になるため。`rewrite_allow_entries` は permissions.md 素材テンプレ
> 専用）。「renga byte 不変」は **「renga が恒等基底で、`ORG_TRANSPORT=renga` 再生成すると byte 等価な renga
> frontmatter が得られる」= rollback byte 安定**の意味で honor する。**この per-transport 訂正は ratified 値（union）
> からの変更だったが、**2026-06-16 に人間が per-transport を再 ratify**（§9.2 #9。union は却下）。詳細は設計 doc
> §0.4 / §2.2 ※3-※5 / §4.2(2) / §5。

## surgical 最難ファイル（generator 化で個別ハンドリングが要る）

- `references/renga-error-codes.md`: renga = 正典 / broker = 加算 の **非対称構造**。単純な
  プレースホルダ展開では表現できない。
- `org-setup/SKILL.md`: permissions の **byte 等価アンカーの向き**（renga 側固定）。
- `org-start/SKILL.md` L66 付近: `echo "${ORG_TRANSPORT:-renga}"` は **コード相当**（既定値そのもの）。
- `.dispatcher/references/spawn-flow.md`: **2 段承認フロー**（folder-trust + dev-channel sidecar）。

## 差異の局所性（generator の条件分岐が小さく済む根拠）

2 transport の差は実質 4 点に局所化される: (1) ツール名接頭辞（`mcp__renga-peers__*` ↔
`mcp__org-broker__*`）、(2) 受信モデル（push 一次 vs in-band）、(3) spawn 儀式（1 段 vs 2 段承認）、
(4) エラーコード（broker 拡張）+ `new_tab`/`focus_pane` 不在。本文の大半は transport 非依存。
