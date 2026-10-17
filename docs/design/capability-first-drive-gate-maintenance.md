# capability 初回駆動ゲートの保守メモ

[`.claude/skills/org-delegate/references/capability-first-drive-operational-gate.md`](../../.claude/skills/org-delegate/references/capability-first-drive-operational-gate.md)（以下「ゲート reference」）から移した、文書を保守する人向けの記述。役割が実行時に読む必要は無い。ゲートの手順はゲート reference だけで完結する。

## 目次

- [1. 3 ゲートの区別とファイル名の理由](#1-3-ゲートの区別とファイル名の理由)
- [2. marker 検出規則の契約条文](#2-marker-検出規則の契約条文)
- [3. org-suspend の ‡ との対応](#3-org-suspend-の--との対応)
- [4. call site 側に書く形](#4-call-site-側に書く形)
- [5. 照会にインライン python を使わない理由](#5-照会にインライン-python-を使わない理由)
- [6. renga-error-codes.md との参照の所在](#6-renga-error-codesmd-との参照の所在)
- [7. 適用先の表に行を持たない追加 call site](#7-適用先の表に行を持たない追加-call-site)
- [8. 対象外にした list_peers の言及](#8-対象外にした-list_peers-の言及)

## 1. 3 ゲートの区別とファイル名の理由

[`docs/contracts/backend-interface-contract.md`](../contracts/backend-interface-contract.md) の T-§ratification は「**Three gates apply, with different subjects, and they MUST NOT be conflated**」と書き、**ratification gate**（主語＝backend contract。契約改定を批准するかの人間判断）/ **operational gate**（主語＝harness。capability 広告列挙を初めて観測した時点の停止と人間報告）/ **production-activation gate**（主語＝deployment。server × mcp-peer 双方 2.0 系での実機 dogfood と人間確認）を分けている。同節は「Ratifying the amendment does not discharge this gate, and satisfying it does not ratify the amendment」とも書く。

ゲート reference が担うのは operational gate だけで、何も批准せず、何も有効化しない。批准可否の判断と実機 dogfood は人間の作業で、どのスキルもゲート reference の手順で代行できない。ファイル名を `capability-first-drive-…` に限定してあるのはそのため（`ratification` / `production-activation` と読み違えられないように）。production-activation gate との関係だけはゲート reference §4 で扱う（未記録時の縮退規則がそこに要るため）。

## 2. marker 検出規則の契約条文

ゲート reference §1-1 の規則の根拠。契約 T-§cap は marker の検出規則を harness 側の義務として次のように書く。

- capability 形の解釈を強制する: marker は「the presence of **either** field on **any** record of an enumeration is sufficient to detect the capability」であり、「a record carrying `same_tab` or `tab` **proves that enumeration is cross-tab-shaped whatever the query reported**, and reading it as legacy is the exact failure this amendment exists to prevent」。
- fail-safe で上書きすることを禁じる: fail-safe default 節の *Precedence over the marker definition* は「this fail-safe applies only where **no** marker is observed at all. Where at least one of `same_tab` / `tab` is present on at least one record of the enumeration, T-§2.2-fields governs, the capability-advertising path applies, and **the fail-safe MUST NOT be invoked to override it**」と書く。

ゲート reference §1-2-a 段 5 の根拠として、契約 T-§ratification は「The operational gate is **not** triggered on the non-advertising path」と書き、続けて「A harness whose enumerations are all legacy-shaped — which is every currently deployed backend, `org-broker` included — proceeds exactly as it does today; nothing about existing operation changes」と書く。後半の「every currently deployed backend」は契約の執筆時点の記述で、renga の下限が 2.0.0 になった現在（`tools/check_renga_compat.py:113`）は非広告 backend（`org-broker`）だけが当たる。

## 3. org-suspend の ‡ との対応

ゲート reference §1-2-b と [`.claude/skills/org-suspend/SKILL.md`](../../.claude/skills/org-suspend/SKILL.md) を往復して読むための注。

org-suspend は unknown を「他タブ扱いに倒す」と書く。あちらは broadcast の宛先選定なので、他タブ扱い＝送らない＝不作為が安全側になる。ゲート reference §1-2 は生存判定なので、unknown を「他タブ扱い」に潰すと不在に化け、安全側が反転する。両者は上位形「unknown では、その unknown を根拠にした作用を起こさない」で一致しており、帰結だけが経路ごとに違う。経路ごとの帰結を、もう一方の文面からコピーしないこと。org-suspend 自身も生存判定の面ではゲート reference と同じ向きに倒しており（Phase 2 / Phase 4 の「消滅済みとして記録せず判定不能のまま残す」`indeterminate`）、矛盾しているのは字面だけである。

## 4. call site 側に書く形

ゲート reference §6 の表の規範文と既存の未承認縮退段落はそのまま残し、その直後に適用結果だけを 2〜5 行で書く。判定条件・評価順・三値の定義を再掲しない:

```text
**capability 形かつ承認済み（§2 の `first_drive` が `recorded`）のときは、共有 reference §1-2 の
三値判定を <対象名> に適用する。「在」のときだけ <この経路の後段>。「不在」は <経路の既定処理>、
「unknown」は <作用を起こさずに報告>。**（判定手順と評価順の正本は §1-2。ここに重ねて書かない）
```

書いてはならないもの: 独自の same-tab poll 規範 / 独自の再送予算 / 三値表・版判定条件・承認条件の複製 / 未承認のまま §1-2-b へ抜ける記述。

各 call site には、`list_peers` 呼び出しの直前に次の規範文を置く。リンクの表示はリポジトリ root 形で固定し、target だけを各ファイルからの相対パスにする（[`docs/contributing/markdown-conventions.md`](../contributing/markdown-conventions.md)）:

```text
**list_peers の直前に
[`.claude/skills/org-delegate/references/capability-first-drive-operational-gate.md`](RELATIVE_PATH)
を Read し、<分岐名> の分岐を適用する。**
```

#7 / #8 の per-site 適用行: ゲート reference §6 の owner 規定により §1-2 は #7 / #8 にも規範として掛かっており（両経路とも呼び出し直前にゲート reference を Read する義務があるので §1-2-c の「登録待ち型は 在 のときだけゲートを開ける」に到達する）、加えて両ファイルの本文にも承認済み路の適用結果行を置いてある（[`.dispatcher/references/spawn-flow.md`](../../.dispatcher/references/spawn-flow.md) 3-4 / [`.dispatcher/references/pane-close.md`](../../.dispatcher/references/pane-close.md) 5-4）。そちらだけを読んだ役割も適用結果に到達する。各 per-site 行が書くのは上の形のとおり適用結果だけで、在の件数・評価順・真理値表・再送予算はいずれもゲート reference 側にしか無い。

## 5. 照会にインライン python を使わない理由

ゲート reference §2 は照会を [`tools/capability_gate.py`](../../tools/capability_gate.py) に限る。インラインの `python3 -c "from tools.state_db import ..."` を使わない理由は 2 つあり、どちらも黙って壊れる（同じ内容が `tools/capability_gate.py` の docstring にもある）:

- cwd 依存: ディスパッチャーの cwd は `.dispatcher/` なので、リポジトリ root が `sys.path` に無く `from tools.state_db import …` が `ModuleNotFoundError` になる。ツール側は自身の `__file__` から root を解決するので、どの cwd からでも同じ答えを返す。
- 部分一致: `payload_json LIKE '%capability_first_drive%'` は、別のイベントの自由記述 `note` がゲート名に言及しているだけでも一致し、ゲートを恒久的に「通過済み」にしてしまう。ツールは `json_extract(payload_json, '$.kind')` の完全一致で引く。

DB のパスはツール側が `resolve_state_db_path()` で解決する（`.state/state.db` 直書きにしない）。記録側の `tools/journal_append.py` と同じ解決なので、`STATE_DB_PATH` で別 DB に向いている環境でも記録した DB と照会する DB がずれない。

## 6. renga-error-codes.md との参照の所在

規範はゲート reference §3-B-2 が持ち、[`.claude/skills/org-delegate/references/renga-error-codes.md`](../../.claude/skills/org-delegate/references/renga-error-codes.md) の messaging 復旧手順は、その `list_peers` を引き直す step の前に §3-B-2 へのポインタを置いている。したがって `[pane_not_found]` を受けてそちらだけを読んだ役割も §3-B-2 に到達する。復旧手順側が持つのは「先に §3-B-2 を適用し、その結果を本手順へ渡す」というポインタだけで、判定条件と縮退規則は §3-B-2 に一元化してある。ポインタの実在は `tests/test_transport_seam.py` の `test_pointed_at_recovery_procedure_carries_the_gate_pointer` が検査する。

## 7. 適用先の表に行を持たない追加 call site

棚卸しで見つかった追加の実行 call site 3 つは、ゲート reference §6 の表の行ではなく次の場所に配線してある。いずれも実在する経路なので、探すときはここを見る。

| 経路 | 規範の所在 / 配線先 |
|---|---|
| `.dispatcher/CLAUDE.md` の delegate-plan `after_spawn[]` 要約 | 表 #7 と同じ call site の別入口。`claude-org-runtime` の delegate-plan helper が `list_peers` 待ちを機械生成した plan 要素として emit するため、helper 経路に乗ったディスパッチャーは [`.dispatcher/references/spawn-flow.md`](../../.dispatcher/references/spawn-flow.md) 3-4 を読まずに列挙する。emitter は ja の外にあるので ja 内の介入点はこの要約で、同ファイルの `after_spawn[]` 節に §3-B の適用と §1-2 の適用結果（「在」でのみゲートを開け、数値 `id` を控える）を置いてある |
| [`.claude/skills/org-delegate/references/renga-error-codes.md`](../../.claude/skills/org-delegate/references/renga-error-codes.md) の messaging 復旧手順 | 規範の owner は §3-B-2（縮退は復旧手順に伝播する = 引き直した列挙の数値 id で他タブへ再送しない / 消失を死亡確定の根拠にしない）。復旧手順側は `list_peers` を引き直す step の前に §3-B-2 へのポインタだけを持ち、規則を複製しない |
| [`.claude/skills/org-attach/SKILL.md`](../../.claude/skills/org-attach/SKILL.md) の表示ラベル突き合わせ | 分岐は `monitoring-read-only`。結果は attach コマンドのラベル生成にしか使わない（join key は `list_panes` の pane_id）ので危険度は最も低いが、全タブ列挙では他 org のペインに自 org のラベルを付けて人間に提示しうる。縮退先: 列挙を破棄し、pane-id join とラベル（`list_panes` 由来）だけで継続する — 落ちるのは peer 名 / cwd の補足だけで、attach 可否・join・lifecycle 判定には波及させない |

runtime 側（`claude_org_runtime` の delegate-plan helper が `list_peers` 再実行を指示する `target_tab_mismatch` / `tab_ambiguous` / `pane_not_found` の復旧文言）は ja リポジトリの外なので、ゲート reference では配線できない。

## 8. 対象外にした list_peers の言及

以下は「役割が手順として `list_peers` を実行する箇所」ではないので、ゲートを配線しない。

| 箇所 | 対象外の理由 |
|---|---|
| `.claude/skills/org-setup/references/permissions.md` の `mcp__renga-peers__list_peers` 行、および各スキル frontmatter の `allowed-tools` | 権限宣言であって呼び出しではない。実行時に列挙結果を受け取らないので、捨てるべき結果が存在しない |
| [`docs/contracts/backend-interface-contract.md`](../contracts/backend-interface-contract.md) / `docs/verification.md` / `docs/overview-technical.md` の言及 | ツールの意味論・適合条件を記述する契約文および説明文。手順ではない |
| [`.claude/skills/org-delegate/references/renga-error-codes.md`](../../.claude/skills/org-delegate/references/renga-error-codes.md) の Known codes 表・capability 説明・シェル例のコメント | エラーコードの意味の記述。実行手順は同ファイルの messaging 復旧 step で、そちらは対象外ではなく §3-B-2 が owner（上の §7 の表を見る） |
| `.dispatcher/CLAUDE.md` の「ワーカーは `list_peers` で窓口を自動発見する」 | ワーカー側の事実の記述。ディスパッチャーの手順ではない（ワーカーに `list_peers` を指示する文書はリポジトリ内に存在しない） |
| `notes/**` / `docs/design/**` の設計メモ・監査メモ、`docs/operations/**` / `docs/sandbox-probe/**` の runbook | 運用手順ではない / 人間オペレーター向けの一回性 runbook であって役割の常設手順ではない（[`docs/contributing/markdown-conventions.md`](../contributing/markdown-conventions.md) の in-scope からも外れる） |
| `tools/**` の定数・テスト fixture・generator golden | コード上の tool 名定数とテストデータ。実行時に列挙結果を受け取らない |
