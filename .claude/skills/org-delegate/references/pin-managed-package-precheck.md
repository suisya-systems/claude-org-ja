# pin 管理された内部パッケージのバグらしき挙動 — 委譲前の既修正チェック（窓口が実行）

> **一次参照元**: [`.claude/skills/org-delegate/SKILL.md`](../SKILL.md) 委譲前チェックリスト（トリガーと 2 行の決定則要約のみ）。本ドキュメントは決定則・確認手順 (a)-(e)・落とし穴・episode 背景の詳細 SoT。

pin 管理された内部パッケージ（`claude-org-runtime` 等、`pyproject.toml` の依存に version 窓で pin される内部パッケージ）で「バグらしき挙動」を観測し、それを worker への委譲・Issue 化で潰そうとする場合、**着手前に installed pin と upstream の最新版・CHANGELOG を突き合わせ、既に upstream で修正済みでないかを確認する**。ja の venv が古い pin のまま upstream 既修正のバグを踏むと、存在しないバグの調査・修正に 1 ワーカー分を丸ごと溶かす（phantom dispatch）。

## 発動条件

以下を **すべて** 満たすとき発動する:

- 観測した「バグらしき挙動」の疑い元が pin 管理された内部パッケージ（`claude-org-runtime` 等）である
- その挙動を worker への委譲・Issue 化で潰そうとしている（＝これから作業を発生させる）

単なる ja 側コードのバグ・外部 SaaS 起因・pin されていない依存では発動しない（通常の委譲フローに進む）。

## 決定則（結論を先に）

このチェックの成果物は「既修正バグを踏んで無駄委譲する事故を止める STOP gate」である。下記 (a)-(c) はこの判定を下すための材料集めにすぎない — 先に分岐の結論を押さえる。**この判定表が本チェックの決定則の SoT であり、SKILL.md 本体の 2 行はこの要約である（決定則を変えるときはまず本表を直す）。**

| 判定 | 条件 | アクション |
|---|---|---|
| **既修正 → 委譲を止める** | installed < latest かつ CHANGELOG / closed issue に該当バグ修正がある | worker を派遣しないだけでなく **バグ Issue も立てない**。「venv upgrade + pin 窓 bump」の in-place 修正（軽量レーン相当）で片付ける（下記 (d)） |
| **未修正 → 委譲に進む** | installed == latest、または latest でも該当挙動が CHANGELOG に無い | 通常の委譲フローに進む。ただし「既修正でない」エビデンスを worker brief に明記する（下記 (e)） |

## 材料集めの手順 (a)-(c)

`claude-org-runtime` を例に示す（dist 名 `claude-org-runtime`、upstream repo `suisya-systems/claude-org-runtime`）。

### (a) ja 側 installed 版を確認（importlib.metadata）

runtime は venv にインストールされているため、**runtime が入った venv の python** で確認する:

```bash
python3 -c "from importlib.metadata import version; print(version('claude-org-runtime'))"
```

> **偽陰性に注意**: runtime が入っていない python（system python や別の venv）で実行すると `PackageNotFoundError` になる。これは「未リリース／未修正版」ではなく「その python からは見えていないだけ」なので、「バグ確定・委譲」と早合点しない。必ず runtime が入った venv の python で実行する。

### (b) PyPI 最新版を確認（urllib）

既存ツール [`tools/check_runtime_version.py`](../../../../tools/check_runtime_version.py) が installed（importlib.metadata）・latest（PyPI JSON API）・pin 窓（`pyproject.toml` から regex 抽出）を突き合わせて drift を検出する雛形として使える。installed を importlib.metadata で読むため、これも **runtime が入った venv の python** で実行する:

```bash
# drift 行が出れば installed < latest（pin 窓内の最新に満たない）
python3 tools/check_runtime_version.py
```

drift 行の形式は次のとおり（`...` にはパッケージ名 + pin 窓が焼き込まれる）:

```
[runtime drift] claude-org-runtime: installed=X latest=Y -- `python -m pip install --upgrade '...'` で更新できます
```

**出力が無い（silent）場合を「drift 無し ＝ 既修正でない」と即断してはならない。** `tools/check_runtime_version.py` は複数の状況で一律 silent skip する設計（docstring 参照）で、silent は主に次を兼ねる:

- (1) installed == latest（本当に pin 窓内で最新）
- (2) PyPI 不達（offline・sandbox 内）
- (3) runtime 未インストール
- (4) **installed が pin 窓内で最新でも、upstream の修正版が pin 窓の外にある**（`_latest_version` の候補が空になり None を返す）
- (5) pin spec / packaging のパース失敗

特に (4) は「既に修正版が出ているのに現行 pin 窓が古くて掴めない」ケースで、この gate が防ぎたい誤読の本丸である。(4) が疑わしいときは silent 出力に頼らず、(c) の CHANGELOG / closed-issue 確認と (d) の pin 窓 bump に進む。(2)/(3) の切り分けは下記「落とし穴」を参照。

### (c) CHANGELOG.md と直近 close された関連 Issue/PR を確認

installed..latest の範囲に該当バグ修正エントリがあるかを upstream repo（`--repo suisya-systems/claude-org-runtime` を明示）で確認する:

```bash
# CHANGELOG（installed..latest の範囲で該当バグ修正エントリを探す）
gh api repos/suisya-systems/claude-org-runtime/contents/CHANGELOG.md --jq '.content' | base64 -d

# 直近 close された関連 Issue / merge 済み PR（症状キーワードで絞る）
gh issue list --repo suisya-systems/claude-org-runtime --state closed --search "<症状キーワード>"
gh pr list --repo suisya-systems/claude-org-runtime --state merged --search "<症状キーワード>"
```

## 決定の適用 (d)/(e)

### (d) 既修正なら委譲を止める（Issue も立てない）

**installed < latest** かつ **CHANGELOG / closed issue に該当バグ修正がある**（または (b) の (4) で pin 窓外に修正版がある）→ 「既修正」と判定。**worker を派遣しないだけでなく、バグ Issue も起票しない**（存在しないバグを追う作業をそもそも発生させない）。代わりに窓口が「venv upgrade + pin 窓 bump」を in-place で片付ける（軽量レーン相当の小タスクで、重量レーンの worker 派遣は不要）:

- **upgrade コマンドは手打ちより (b) の drift 出力をコピーする**。`tools/check_runtime_version.py` は pin 窓を焼き込んだ `python -m pip install --upgrade '<PACKAGE><pin窓>'` をそのまま出力するため、pin 窓の陳腐化や out-of-window 版への誤 upgrade を避けられる（bare な `pip install --upgrade claude-org-runtime` は pin 窓外の PyPI 最大版を掴みうる）。
- latest が現行 pin 窓に収まらない場合（(b) の (4)）は、`pyproject.toml` の pin 窓（現状 `claude-org-runtime>=0.1.36,<0.2`）を bump してから upgrade する。

### (e) 委譲に進むならエビデンスを brief に載せる

既修正でない（**installed == latest**、または **latest でも該当挙動が CHANGELOG に無い**）と確認できた場合のみ委譲に進む。その際、worker brief に「既修正でない」エビデンス（確認した installed / latest の版・CHANGELOG に該当修正が無いこと）を明記し、worker が同じ確認を重複しないようにする。

## 落とし穴: sandbox 内は PyPI 不達で silent

`tools/check_runtime_version.py`（および /org-start の drift check）は **offline / sandbox 内では PyPI に到達できず silent skip する設計**（drift があっても何も出力しない）。上記 (b) のとおり「出力無し」は複数状況を兼ねるため、sandbox 内で空振りしたのを「drift 無し ＝ 既修正でない」と誤読すると、既修正バグをそのまま委譲してしまう。**確実を期すなら (b)/(c) はネットワーク到達可能な端末（sandbox 外）で実行する。**

## episode 背景: 2026-07-08 #119 phantom dispatch

2026-07-08、`claude-org-runtime` の挙動をバグとみなして worker に委譲したが、実際は upstream の #133 / #134 で **0.1.36 で既修正済み**だった。ja の venv が **0.1.34** のままで、修正済みのバグを踏んでいた。存在しないバグの調査に 1 ワーカー分を無駄にした。この根治として本チェックを委譲前チェックリストに追加した（ja pin は #119 後に `claude-org-runtime>=0.1.36,<0.2` へ bump 済み）。
