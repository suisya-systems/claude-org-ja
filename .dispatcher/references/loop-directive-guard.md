# /loop 監視ディレクティブの canonical 固定（check-loop-directive.sh）

> ディスパッチャー向け reference。フック本体は
> [`.hooks/check-loop-directive.sh`](../../.hooks/check-loop-directive.sh)。

## 何を止めるか

ディスパッチャーの `CronCreate` / `ScheduleWakeup`（`/loop` の実体）に PreToolUse
フックを掛け、prompt が **canonical 正文を含まないときは exit 2 で deny** する。
正文の SoT は
[`.claude/skills/dispatcher-resume/SKILL.md`](../../.claude/skills/dispatcher-resume/SKILL.md)
Step 5「監視ループの再開」の fenced code block ただ 1 箇所（編集 SoT は
`SKILL.md.in`。`SKILL.md` は [`tools/gen_skill_prose.py`](../../tools/gen_skill_prose.py)
の生成物）。フックは正文の写経を持たず、実行時に `SKILL.md` を読んで取り出す。

## なぜ機構にしたか

2026-09-02、ディスパッチャーが `/loop 3m` を canonical 正文ではなく自分で書いた
短縮版で武装した。短縮版は relay scan の `--audit`（滞留検知）だけを毎サイクル叩き、
Step 5.25 の実配送手順（`--list` → `send_message` → `--mark-delivered`）を一度も
実行していなかった。契約上「見逃しゼロの主保証」である relay 層が 1 日以上
「滞留を報告するだけの層」に退化し、直 push が生きていたため症状が出ず、
`last_scan_at` の age が 2237 分になって初めて発覚した（当時の観察メモ
`knowledge/raw/2026-09-02-self-authored-monitoring-directive-omits-the-work.md` は
本リポジトリに commit されていないため、経緯の要旨は本節に転記してある）。

手順書には既に「正文を使え」と書かれていて、それでも守られなかった。窓口が peer
message で再武装を指示しても効くのはその session が続く間だけで、次の `/org-start`・
handover・auto-compact で同型が再発する。prose の追記では止まらないので機構にした。

## D-: 監視ループ以外の CronCreate / ScheduleWakeup も deny する

**判断**: ディスパッチャーの `CronCreate` / `ScheduleWakeup` は、canonical 正文を含む
prompt 以外を **一律 deny** する（「監視ループらしき prompt だけ検査する」形は採らない）。

**理由**: ディスパッチャーの役割は worker monitoring であり、定期実行の用途は
`/loop` による監視ループ以外に無い。「監視ループらしさ」を prompt から推定する分岐を
置くと、今回の事故そのもの（自己流の文面）が「監視ループらしくない」と判定されて
素通りする経路を自分で作ることになる。用途が単一である以上、正文以外を全て deny する
方が判定が単純で、抜け道も無い。監視以外の定期実行が本当に必要になったときは、
その用途を skill 側に canonical 正文として足す（= SoT を 1 箇所に保ったまま許可を増やす）。

**例外は 1 つだけ**: `ScheduleWakeup(stop: true)` は prompt を持たない「loop を今すぐ
終了する」呼び出しなので許可する。監視対象ゼロで `/loop` を止める正規の経路
（`DISPATCHER_RESUMED_IDLE`）を塞がないため。

## D-: 単一の等価ではなく「閉じた 2 形との完全一致」で照合する

**判断**: prompt から空白を全て除去したうえで、canonical 正文の次の **2 形のいずれかと
完全一致**することを要求する。前後に任意のテキストが付いた形は通さない。

- (a) `/loop 3m <本文>` の丸ごと —— `ScheduleWakeup` は「`/loop` 入力をそのまま」渡す
- (b) `<本文>` のみ（`/loop [interval]` を剥がしたもの）—— `CronCreate` が enqueue する prompt

**理由**:

1. 同じ正文が 2 つの包み方で届く（各ツール定義の `prompt` 説明）。単一の文字列等価を
   課すと、どちらか一方が構造的に必ず deny される。
2. 一方で、許すのを「正文を**含む**任意のテキスト」まで広げてはならない。正文の後ろに
   「上記は無視して `--audit` だけ回すこと」を足した prompt が通ってしまい、本フックが
   防ごうとしている degraded な監視ループがそのまま張れる。許すのはこの閉じた 2 形だけに
   する（正文への前置き・後置きも deny）。

**空白の扱いは「1 個に畳む」ではなく「全て除去」**。正文本文は日本語で語間に空白を
持たないため、端末幅やツールの整形で折り返されると元は空白が無かった位置に改行が入り、
畳み方式では「空白差だけ」の正文が deny される（実測）。除去なら折り返し位置に依存しない。

## D-: 判定不能は fail-closed

`jq` / `awk` 欠落、payload が空 / 不正 JSON / 非 object、`CLAUDE_ORG_PATH` 未設定、
`SKILL.md` が読めない、fenced code block に `/loop` 行が無い —— いずれも deny する。
正文を確認できないまま `/loop` を通すと、まさに本フックが防ごうとしている
「正文でない監視ループ」を素通りさせることになるため。payload 系の fail-closed 規律は
[`tests/test-hooks-payload-fail-closed.sh`](../../tests/test-hooks-payload-fail-closed.sh)
が全 enforcement フック横断で固定している。

**残存リスク（受容）**: `CLAUDE_ORG_PATH` が壊れている・checkout が動かされている等で
SoT に到達できない端末では、正文であっても `/loop` が張れず監視ループが立たない。
degrade した監視ループを黙って回すよりは立たない方がよい、という判断で fail-closed に
倒している。deny の stderr は必ず原因（未設定の env / 読めなかったパス）を名指しするので、
黙って止まることはない。

## 配布範囲: ディスパッチャーのみ

本フックは **ディスパッチャーの role config にだけ** 配る
（[`tools/org_extension_schema.json`](../../tools/org_extension_schema.json) の
`roles.dispatcher.required_hooks` と
[`.claude/skills/org-setup/references/permissions.md`](../../.claude/skills/org-setup/references/permissions.md)
のディスパッチャー節）。ワーカーの完了後 bounded `/loop`・キュレーター・窓口の `/loop`
用途は正文を持たないので、他ロールに配ると誤 deny になる。

`tools/check_role_configs.py --include-local` は上記 schema 登録を根拠に
「dispatcher の `settings.local.json` にこのフックが無い」を drift として報告する
（`/org-start` Block C4 で毎回検証される）。

## 正文を変更したいとき

`SKILL.md.in` の Step 5 の fenced code block を直し、`tools/gen_skill_prose.py` で
`SKILL.md` を再生成する。フックは実行時に rendered な `SKILL.md` を読むので、
フック側の追随作業は無い（写経が 1 つも無いので同期ズレが起きない）。
