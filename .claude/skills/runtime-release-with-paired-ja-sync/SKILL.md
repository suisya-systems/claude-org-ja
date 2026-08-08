---
name: runtime-release-with-paired-ja-sync
owner: secretary
description: >
  claude-org-runtime のリリース (release-* タスク / vX.Y.Z タグ発行) を、
  同 PyPI 発行を受ける claude-org-ja 側の expectation 同期と
  ペアで設計・委譲・完了させるためのワークフロー。
  DEFAULT_NOTIFY 値・classifier vocabulary・role_configs_schema
  (ja 側ミラー: org_extension_schema)・attention.example.json テンプレ等が
  変わるリリースで発動する。
  CI cascade を予測・予防し、ja-side の red を回避することを目的とする。
---

# runtime-release-with-paired-ja-sync: runtime リリース＋ja-side 同期ワークフロー

claude-org-runtime のリリースは PyPI 発行を経由して claude-org-ja の
attention watcher integration test に必ず波及する。
本 skill はそのカスケードを「リリース起票時に予測し、paired ja-sync を同一 Secretary
セッション内で land しきる」ためのチェックリスト。

> **本 SKILL のスコープ**: 窓口が runtime release 系タスクを受領した際の
> 委譲設計と paired ja-sync 計画。実 commit / テスト修正はワーカー側であり本 skill ではない。

## なぜこの skill が必要か

`claude-org-runtime` 側で `DEFAULT_NOTIFY` の値や classifier 語彙・`role_configs_schema.json`
（runtime 同梱 schema。ja 側ミラーが `tools/org_extension_schema.json`）の項目を
更新してリリースすると、PyPI publish 後に `claude-org-ja` 側の以下が同時に古くなる:

- `tests/test_attention_runtime_integration.py` の expectation
- `tools/org_extension_schema.json` のバイト一致コピー
- `.claude/skills/org-setup/references/permissions.md` の projection
- `tools/templates/attention.example.json` の severity / TTL

過去のリリースサイクルでは「runtime release は無事だったが ja-side CI が翌日に red」になる
カスケードが観測されており、これを毎回観測してから後追いで直すと、

- Secretary セッションが切れて context を失う
- ja-side worker に「なぜこの変更が必要か」の経路を再構築させる
- 修正の paired-ness が暗黙的になり次回も同じカスケードを踏む

の 3 重コストが発生する。本 skill は paired-ness を**リリース起票時に明示化**し、
同一 Secretary セッション内で land しきる規律を提供する。

## 発動条件

以下のいずれかに該当する場合、Secretary は org-delegate より前にこの skill を確認する:

- task_id に `release` を含む、または `vX.Y.Z` 形式のタグを発行するリリース系タスク
- chore release 系のユーザー指示プロンプト（「runtime のリリース」「PyPI 上げ」等）
- runtime 側の `DEFAULT_NOTIFY` / classifier mapping / `role_configs_schema.json` /
  attention テンプレのいずれかを触る変更が land 寸前
- runtime 側の `role_configs_schema.json` にある `required_allow` / `required_deny` /
  `required_hook_scripts` / `required_hooks` への追加・変更を含むリリース。
  これらは「settings ファイルに必ず存在すべき項目」を増やす変更であり、runtime 側 CI が
  full green・Codex 指摘ゼロでも ja 側の安全性は証明されない。ja は floating pin
  （`>=X,<0.2`）のため PyPI 公開の瞬間から ja 側の 2 系統のチェックが赤くなる:
  (a) `tools/check_role_configs.py` — permissions projection と tracked settings を schema
  との整合で検証する。CI 配線は `--include-worker-settings`
  （[`.github/workflows/tests.yml`](../../../.github/workflows/tests.yml)）で、machine-local
  settings（`~/.claude/settings.json` 等）は CI 対象外。CI green でも local は stale に
  なり得るため、merge 後に `/org-setup` で反映する。なお `--include-local` は schema に
  `settings_paths` が宣言された role の settings.local.json しか見ず（`user_common` は
  `settings_paths: []`）、参照 schema も checked-in の ja ミラーなので、home レベル
  （`~/.claude/settings.json`）の allowlist 検証ゲートにはならない — user_common の
  現物反映は `/org-setup` 適用後に settings 現物を直接確認する。
  (b) `tools/check_runtime_schema_drift.py` — ja の `tools/org_extension_schema.json` と
  runtime 同梱 `role_configs_schema.json` の、ja 固有節を strip した上でのバイト比較
  （CI で実行。installed runtime が pin window 内なら skip されない）。
  実例: renga capability probe `server_info` の `required_allow` 追加
  （2026-08-08, runtime Issue #161）で露見

## Step 1: pre-fetch（リリース worker 派遣前）

リリース系タスクは workers_dir 側が古い main から派生すると tag 衝突や stale base
を引き起こすため、ワーカー派遣の直前に必ず以下を窓口が実行する:

```bash
cd <runtime workers_dir>
git fetch origin
git pull --ff-only origin main
```

`--ff-only` を必ず付ける。non-fast-forward になる場合は誰か別経路で main を動かしている
合図なので、worker を派遣せず人間に報告する。

> **cross-link**: workers_dir 側の pre-dispatch 整合性は [`.claude/skills/org-delegate/SKILL.md`](../org-delegate/SKILL.md)
> の派遣前チェックリストと同質の責務であり、本 skill は release-class 限定の上乗せ。

## Step 2: リリース worker への impl-guidance

リリース worker への brief 組み立て時、以下を **必ず** impl-guidance に明示する:

- `test_smoke.py` 内の version リテラル（あるいは同等の version 文字列を見るテスト）
  の更新を「known-required な co-update」として列挙する
- リリース本体の bump 操作と version リテラルが両方 commit に含まれることを完了条件にする

過去のリリースサイクルでは `test_smoke.py` の version リテラル更新漏れが
リリース直後の CI red 原因として観測されている（cascade の入口）。
brief で明示しない場合 worker は bump 対象を pyproject 系のみと解釈し、
テスト側のリテラルを取りこぼす。

## Step 3: リリース land + tag push

リリース PR がマージされた後、release.yml を発火させるための v-タグ push は
**Secretary が手動で実行**する。理由:

- tag push は GitHub Actions の release publishing pipeline を起動する破壊的操作で、
  撤回が事実上不可能（PyPI yank はあるが版番号は焼かれる）
- worker の自動 push 不可制約と整合させる必要がある

実施前に必ずユーザーに「v-タグを push して release.yml を起動します」という
明示的 OS 承認を取る（チャットの「OK」「進めて」で十分。 [[chat_auth_is_enough]]）。

### tag 前の fail-closed 検証（省略不可）

runtime の release workflow（`.github/workflows/release.yml`）は `on: push: tags: ["v*"]`
でタグ付き commit を checkout してビルドするだけで、**タグ名と版数の一致も、その commit が
main の先端かどうかも検証しない**。版数の SoT は `src/claude_org_runtime/__about__.py` の
`__version__`（`pyproject.toml` が `dynamic = ["version"]` で
`claude_org_runtime.__about__.__version__` を読む）。fetch だけでは ancestry も内容も
確かめられないため、検証を挟まずに tag すると次の 2 つが起きうる:

- **版数不一致**（タグ `vX.Y.Z` / `__about__.py` は旧版）: publish が既発行版の再アップロードとして
  PyPI に弾かれ job は red になるが、**タグ番号は焼かれる**
- **stale SHA**（版数は合っているが main の先端ではない）: 古い内容のまま publish が
  **成功**する。PyPI yank はできても版番号は再利用できない＝実質撤回不可

以下は **1 ブロックまとめて実行する**。各 gate は目視確認ではなく非ゼロ終了で表現してあり、
`set -euo pipefail` と合わせて 1 つでも外れれば `git tag` に到達せず止まる（「出力を見て判断する」
形にすると、貼り付け実行でそのまま tag まで走り抜けてしまう）:

```bash
# Secretary 側で実行（user の明示承認後）
set -euo pipefail
WD=<runtime workers_dir>
VER=X.Y.Z                    # 発行する版数（タグ名から先頭の "v" を除いたもの）
MERGE_SHA=<リリース PR の merge commit sha>

# (a) 最新化。squash / rebase merge の merge commit はローカル未取得の新規オブジェクトなので
#     tag の前に必ず fetch する（Step 1 の fetch は PR 前なので古い）。
#     --tags で remote 側の既存タグも取り込む
git -C "$WD" fetch origin --tags

# (b) 同名タグが未使用であること。remote が正（ローカルに無くても remote にあれば push は reject）
[ -z "$(git -C "$WD" ls-remote --tags origin "refs/tags/v$VER")" ] \
  || { echo "FAIL: tag v$VER is already on origin"; exit 1; }

# (c) tag 対象は origin/main の先端。リリース内容がそこに含まれることを ancestry で確認する
SHA="$(git -C "$WD" rev-parse origin/main)"
git -C "$WD" merge-base --is-ancestor "$MERGE_SHA" "$SHA" \
  || { echo "FAIL: $MERGE_SHA is not contained in origin/main"; exit 1; }
[ "$SHA" = "$MERGE_SHA" ] \
  || { echo "STOP: origin/main advanced past the release merge ($SHA)"; exit 1; }

# (d) その commit の版数がタグと一致すること。作業ツリーではなく commit から直接読み、
#     目視ではなく文字列比較する（worktree 側が未 commit / 別ブランチでも欺かれない）。
#     revspec の変数は必ず ${SHA} と波括弧で書く: zsh では $SHA:src/... の ":s/.../.../" が
#     パラメータ修飾子として食われ、別 revspec に化けたまま静かに落ちる
ACTUAL="$(git -C "$WD" show "${SHA}:src/claude_org_runtime/__about__.py" \
          | sed -n 's/^__version__ = "\(.*\)"$/\1/p')"
[ "$ACTUAL" = "$VER" ] \
  || { echo "FAIL: __about__.py at $SHA says '$ACTUAL', expected '$VER'"; exit 1; }

# (e) push 直前に origin/main を取り直し、検証中に動いていないかを再確認する
git -C "$WD" fetch origin main
[ "$SHA" = "$(git -C "$WD" rev-parse origin/main)" ] \
  || { echo "STOP: origin/main moved during verification; 最初からやり直す"; exit 1; }

# (f) 全 gate 通過。tag は不変の SHA に対して打つ
git -C "$WD" tag "v$VER" "$SHA"
git -C "$WD" push origin "v$VER"
```

(c) / (e) で `STOP` になった場合は、その場で tag を打ち直さず人間に報告する。「先端に乗り換えて
発行する」のか「相乗りする別 PR を含めずに切る」のかは人間の判断であり、Secretary が一次判断で
選んでよい種類の選択ではない。

> **残余レースの扱い**: (e) と `push` の間には原理的に窓が残る（GitHub の tag push には
> 「main が `$SHA` のままなら通す」という compare-and-swap が無いため、これは手順で閉じられない）。
> ただし **tag は不変の SHA に対して打つ**ので、この窓で main が進んでも発行される artifact は
> (c)(d) で検証した内容そのままであり、壊れた版が出ることはない。窓で起こりうるのは
> 「直後にマージされた別 PR がこの版に入らない」だけで、これは release の通常挙動である。
> したがって対策は「main を止める」ではなく、**(e) を push の直前に置き、外したらやり直す**で足りる。

push 後は GitHub Actions の release.yml ジョブを `gh run watch` 等で監視する。
このとき **`--repo` で runtime リポジトリを必ず明示**する
（Secretary の cwd は ja root のため、無指定の `gh run watch` は ja 側リポジトリの
run に解決される）。
PyPI publish 完了までは Step 4 の ja-side 派遣を待機しない（並走可。むしろ並走推奨）。

## Step 4: paired ja-sync の計画（並走で起票）

runtime release が以下のいずれかの変更を含むなら、**同一 Secretary セッションで**
ja-side 同期 PR を起票する。後回しにしない:

| runtime 側の変更 | ja-side 同期対象 |
|---|---|
| `DEFAULT_NOTIFY` の値変更 / 追加 / 削除 | `tests/test_attention_runtime_integration.py` の expectation と golden fixture `tests/fixtures/attention/expected_scan.json` の更新（新ポリシーを ja が採用する場合は `tools/templates/attention.example.json` も） |
| runtime `role_configs_schema.json` のフィールド改廃 | ja 側ミラー `tools/org_extension_schema.json` の共有面を runtime 同梱 schema に同期（ja 固有節は保持。下記注意点参照） |
| classifier vocabulary の追加 / 改名 | attention 系 artifacts の更新 — `tests/fixtures/attention/` の fixture / golden・`tests/test_attention_runtime_integration.py` の expectation・`tools/templates/attention.example.json`（permissions projection は classifier 語彙を持たないため対象外） |
| attention payload の severity / TTL ladder 変更 | `tools/templates/attention.example.json` の severity / TTL 同期＋`tests/test_attention_runtime_integration.py` の TTL 前提（fixture の経過時間セットアップ・severity 期待）と golden の整合 |
| runtime の sandbox 評価器（`render_role_with_metadata()` / `SandboxMetadata.to_jsonable()`）の出力形が変わる（schema JSON は不変） | `tests/fixtures/runtime_schema_drift/sandbox_intent/` の `expected_explain` / `expected_rendered_sandbox` golden を新 runtime で再生成＋floor 引き上げ。schema バイトが動かないので byte check は素通りするが、`tests/test_runtime_schema_drift_semantic.py` は pin window を意図的にバイパスして installed runtime に**無条件で hard fail** する（skip-with-warning の逃げ道が無い唯一の drift 次元） |
| attention event の `title` / `body` 文言・フィールドの追加 / 改名（severity / TTL は不変） | golden `tests/fixtures/attention/expected_scan.json` を再生成。`test_scan_output_matches_golden` は `desktop_dispatched` / `bell_dispatched` / `delivered`（`_DISPATCH_ONLY_KEYS`）を除く**全キーを比較**するため、severity 系のどの行にも当たらない文言変更だけでも fail する |
| ja が新 runtime 版の挙動・新 required 項目に依存する場合 | runtime version floor を **5 ファイル atomic** に引き上げ: `pyproject.toml` / `requirements.txt` / `docker/Dockerfile`（`ARG RUNTIME_VERSION`）/ `docker/compose.yaml`（`RUNTIME_VERSION` 既定値。Dockerfile の ARG を上書きするためここが古いと旧版が焼かれる）/ `docker/README.md`（マルチアーキビルド例の image tag。tag 規約 `<repo-ref>-r<runtime-version>` の `-r` suffix が唯一ここに書かれている）。共有 schema 面が動く場合は `RUNTIME_PIN_LOWER_INCLUSIVE` も同時に（下記「pin window 定数」） |

paired ja-sync は **複数 worker 並列**で派遣して構わない（むしろ推奨）。
ただし並列分割してよいのは**行間で対象ファイルが重ならない場合に限る**。
classifier kind と `DEFAULT_NOTIFY` が同時に動くリリースでは attention 系 artifacts
（integration test / golden / template）が行をまたいで重なるため、attention 系は
まとめて 1 本の PR にする。重ならない対象は 1 worker 1 PR で並走してよく、
窓口は org-delegate の並列委譲ガイダンスに従って分割する（[[parallelize_delegation]]）。

**例外 — permissions projection が変わる schema 変更は並列分割しない**:
`tools/check_role_configs.py` は permissions projection
（`.claude/skills/org-setup/references/permissions.md`）を schema との整合で常時検証し
CI でも走るため、projection に影響する schema 変更（`required_allow` / `required_deny` /
`required_hook_scripts` / `required_hooks` の追加・変更を含む）では、schema ミラー
（`tools/org_extension_schema.json`）・permissions projection・tracked settings は
独立には land できない（片方だけの中間 PR は CI red になる）。この一式は
**1 本の atomic PR** にまとめ、machine-local settings（`~/.claude/settings.json` 等）は
merge 後に `/org-setup` で反映する。さらに:

- 共有 schema 面が動く変更はそれ自体が新 runtime 版への依存なので、**version floor 5 ファイル
  （上表）も同じ atomic PR に含める**。floor だけ先行させると CI が新 runtime を解決して
  旧ミラーとの drift red になり、schema 側だけ先行させると旧 floor が旧 runtime を
  許容したまま残る — どちらの順でも中間状態が壊れる。
  **`required_*` の追加はこの条件の一例であって限定ではない**: projection に出ない
  フィールドの改廃でも共有面のバイトが動く以上、floor は同じ PR で動かす
- `required_hook_scripts` / `required_hooks` が新規 guard を導入する場合は、
  `.hooks/` の実体 script（とその test）も同 PR スコープに含める
  （`tools/check_role_configs.py` は hook command を `.hooks/<script>` の実ファイルに
  解決し、欠落・相対パス形を報告する）

### pin window 定数 `RUNTIME_PIN_LOWER_INCLUSIVE` の bump 条件

`tools/check_runtime_schema_drift.py` の `RUNTIME_PIN_LOWER_INCLUSIVE` は floor 5 ファイルとは
別物で、**byte check を走らせる window の下限**を決める（`_runtime_in_pin_window()`。window の
外では skip-with-warning になる）。floor bump に機械的に連動させると両方向に間違えるので、
判定は floor の有無ではなく **共有 schema 面のバイト内容が動くか**で行う:

- **動く**（`required_*` 追加を含む共有面の改廃全般）→ **同じ atomic PR で floor と同時に bump**。
  上げないと、旧 runtime が入った環境で byte check が skip ではなく **hard fail** になる
  （新ミラー vs 旧同梱 schema）。window を上げることで意図どおりの skip-with-warning に戻る
- **動かない**（pin-only の floor bump / evaluator drift / attention golden 再生成）→ **据え置く**。
  上げても byte check を通っていた旧 install を skip-with-warning に落とすだけで益が無い

どちらに倒したかと理由は `requirements.txt` の floor コメントに 1 行残す（過去の bump / 据え置きの
判断がそこに蓄積されており、次回の判断材料になる）。

floor bump の波及はこの 5 ファイル + 1 定数で閉じない点にも注意する。現行 floor の**値そのものを
本文に埋めている prose doc** が複数あるため、bump 時は `grep -rn "<旧 floor 版数>"` で洗い出して
同じ PR で追随させる（floor を宣言するファイルと、floor を説明するファイルは別物）。

## Step 5: CI cascade の予測と委譲

ja の CI（`.github/workflows/tests.yml`）は push / pull_request でのみ走り、
**PyPI publish では再実行されない**。このため 2 つの帰結がある:

- Step 4 の paired PR を publish 前に開いた場合、初回 CI run は旧 runtime を解決した
  ままで、放置しても新版で再検証されない。publish 確認後に明示的な rerun
  （`gh run rerun` / head 更新）で新版を解決させてから merge する
- publish 後に走る他の ja PR / main push の CI run は新版を解決するため、
  paired PR が未 land のままだとそれらが red になる

このとき **Secretary は CI failure を自分で調査しない**。
[[secretary_does_not_investigate_ci]] に従い、paired ja-sync worker に
「PR #N の CI 失敗を調査して直して」とだけ渡し、詳細 brief は書かない。
gh api / log / diff / source 読解は worker の責務。

予測される red の代表例:

- `tests/test_attention_runtime_integration.py` が golden `tests/fixtures/attention/expected_scan.json`
  との比較で fail（旧 `DEFAULT_NOTIFY` の severity だけでなく、title / body 文言や
  フィールド形状の変化でも落ちる）
- `tools/check_runtime_schema_drift.py` の byte check が `tools/org_extension_schema.json` と
  runtime 同梱 schema の差分で fail（installed runtime が pin window 内のとき）
- `tests/test_runtime_schema_drift_semantic.py` が evaluator 出力形の変化で fail
  （pin window に関係なく無条件に走る）
- `tools/check_role_configs.py` が schema ミラーと permissions projection / tracked settings の
  不整合で fail

これらは Step 4 で同期 PR が先行 land していれば回避可能。先行できないリリーススケジュール
の場合は、Step 5 で worker に投げる前提で release.yml 完了直後にスタンバイ。

## Step 6: 同一セッションでの land

Secretary 自身のセッションが context 上限に達すると、paired ja-sync の意図が
[`.claude/skills/secretary-handover/SKILL.md`](../secretary-handover/SKILL.md) を経ても暗黙化しやすい。
本 skill の全 Step は**できる限り同一 Secretary セッション内で完了**させる:

- リリース worker 派遣と並走で ja-sync worker を起票（Step 4）→ リリース PR merge →
  tag push → ja-sync PR を release.yml 完了前後で land・merge
- 1 セッションで land しきれない場合は handover に「runtime vX.Y.Z リリース後の paired ja-sync が
  残タスク」と明示し、resume 後の最初のターンで本 skill を再読する

## 成果物

- runtime 側: リリース PR + v-タグ + PyPI 発行 + release.yml ジョブ green
- ja-side: paired sync PR（DEFAULT_NOTIFY expectation と attention golden / schema 共有面同期 /
  permissions projection と tracked settings / attention template / version floor）。
  **本数は固定ではなく Step 4 の overlap ルールで決まる** — attention 系は 1 本、
  schema ミラー × projection × settings × floor は 1 本の atomic PR にまとまる
- CI: ja-side main の CI が new runtime 版で green

## 判断基準・閾値

| 基準 | 値 | 根拠 |
|---|---|---|
| paired ja-sync の起票タイミング | release worker 派遣と並走（同セッション） | カスケード遅延を最小化 |
| `git pull --ff-only` の挙動異常 | 即 user 報告 | 別経路で main が動いた可能性 |
| Secretary CI 調査 | しない | worker への委譲が標準（[[secretary_does_not_investigate_ci]]） |

## 応用・バリエーション

- **schema 変更のみ・DEFAULT_NOTIFY 不変**: attention 系の test expectation は影響しないことが
  多いが、**schema / permissions 同期の 2 系統では閉じない** — 共有面のバイトが動く以上、
  floor 5 ファイルと `RUNTIME_PIN_LOWER_INCLUSIVE` まで含めた 1 本の atomic PR になる
- **DEFAULT_NOTIFY だけ動く・schema 不変**: attention golden と test expectation の更新で
  完結することが多い（schema 面が動かないので `RUNTIME_PIN_LOWER_INCLUSIVE` は据え置き。
  新 kind が新 runtime 経路に依存するなら floor 5 ファイルは要る）
- **classifier vocabulary を新規追加**: attention 系 artifacts（`tests/fixtures/attention/` の
  fixture / golden・`tests/test_attention_runtime_integration.py`・
  `tools/templates/attention.example.json`）が丸ごと動くケース。ただしこれらは Step 4 の
  overlap ルールにそのまま当たるので **並列分割せず 1 本の PR にまとめる**
  （permissions projection は classifier 語彙を持たないため対象外で、そもそも系統が割れない）。
  新 kind が新しい runtime 経路に依存するなら floor 5 ファイルも同じ PR に入れる。
  直近の実例は `duplicate_sidecar` の追随で、attention artifacts と floor が 1 commit に
  収まっている（`git log --oneline -- tests/fixtures/attention/expected_scan.json` で辿れる）

## 注意点

- v-タグ push は user 明示承認なしで実行しない。release.yml は一度焼くと PyPI 版番号が消費される
- workers_dir の `git pull --ff-only` で non-fast-forward を踏んだら worker 派遣を中断
- `tests/test_attention_runtime_integration.py` は paired PR が main に入る前に
  runtime 新版を解決しに行く可能性があるため、ja-side の同期 PR は release.yml 完了直前
  〜直後の数時間でランドさせる時間圧がある
- `tools/org_extension_schema.json` は runtime 同梱 schema の**丸ごと byte コピーで
  差し替えてはならない**。ja 固有の `sandbox` / `sandbox_by_pattern` ボディと Layer-2
  credential mirror の deny は ja 側 org policy であり、`tools/check_runtime_schema_drift.py`
  はこれら（と `$comment` キー）を strip した上で残りの共有面をバイト比較する。
  共有面のみを追加位置・順序まで runtime に揃え、ja 固有節は保持する
- `tools/templates/attention.example.json` は **どのテストからも読まれない**。
  `tests/test_attention_runtime_integration.py` は `--state-dir <tmpdir>` だけを渡して
  config 無しの runtime 既定文言を golden と突き合わせるため、テンプレの severity / 文面の
  更新漏れは **CI では一切検出されない**。CI green のまま `/org-attention-start` が配る
  運用既定だけが古くなる silent gap になるので、Step 4 でテンプレ行に当たったら CI ではなく
  人間のチェックリストで担保する
- 本 skill は窓口専属。worker 側 SKILL ではない

## 履歴的背景

本 skill は claude-org-runtime v0.1.11 リリースサイクルで観測されたカスケードを
ベースに、Issue #25 / #26 / #28 / Backlog #32 / #33 の議論を通じて結晶化した
ワークフロー。
