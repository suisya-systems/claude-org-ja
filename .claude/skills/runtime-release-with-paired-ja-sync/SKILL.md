---
name: runtime-release-with-paired-ja-sync
description: >
  claude-org-runtime のリリース (release-* タスク / vX.Y.Z タグ発行) を、
  同 PyPI 発行を受ける claude-org-ja 側の expectation 同期と
  ペアで設計・委譲・完了させるためのワークフロー。
  DEFAULT_NOTIFY 値・classifier vocabulary・org_extension_schema・
  attention.example.json テンプレ等が変わるリリースで発動する。
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

`claude-org-runtime` 側で `DEFAULT_NOTIFY` の値や classifier 語彙・`org_extension_schema.json`
の項目を更新してリリースすると、PyPI publish 後に `claude-org-ja` 側の以下が同時に古くなる:

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
- runtime 側の `DEFAULT_NOTIFY` / classifier mapping / `org_extension_schema` / attention テンプレ
  のいずれかを触る変更が land 寸前
- runtime 側の `role_configs_schema.json` にある `required_allow` / `required_deny` /
  `required_hook_scripts` / `required_hooks` への追加・変更を含むリリース。
  これらは「settings ファイルに必ず存在すべき項目」を増やす変更であり、runtime 側 CI が
  full green・Codex 指摘ゼロでも ja 側の安全性は証明されない。ja は floating pin
  （`>=X,<0.2`）のため PyPI 公開の瞬間から ja 側の 2 チェックが赤くなる:
  (a) `tools/check_role_configs.py --include-local`（settings 現物に新しい必須項目が無い）、
  (b) `tools/check_runtime_schema_drift.py`（ja の `tools/org_extension_schema.json` と
  runtime 同梱 `role_configs_schema.json` のバイト比較。installed runtime が pin window 内
  なら skip されない。CI でも実行される）。
  実例: renga capability probe `server_info` の `required_allow` 追加（2026-08-08,
  runtime Issue #161）で露見。詳細は
  [`knowledge/curated/release-process.md`](../../../knowledge/curated/release-process.md)
  の「runtime の `required_allow` 等 schema 追加は、runtime 側 green だけでは ja 側の
  安全性を証明しない」節を参照

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

```bash
# Secretary 側で実行（user の明示承認後）
git -C <runtime workers_dir> tag vX.Y.Z <merge commit sha>
git -C <runtime workers_dir> push origin vX.Y.Z
```

push 後は GitHub Actions の release.yml ジョブを `gh run watch` 等で監視。
PyPI publish 完了までは Step 4 の ja-side 派遣を待機しない（並走可。むしろ並走推奨）。

## Step 4: paired ja-sync の計画（並走で起票）

runtime release が以下のいずれかの変更を含むなら、**同一 Secretary セッションで**
ja-side 同期 PR を起票する。後回しにしない:

| runtime 側の変更 | ja-side 同期対象 |
|---|---|
| `DEFAULT_NOTIFY` の値変更 / 追加 / 削除 | `tests/test_attention_runtime_integration.py` の expectation 更新 |
| `org_extension_schema` のフィールド改廃 | `tools/org_extension_schema.json` のバイト一致コピー差し替え |
| classifier vocabulary の追加 / 改名 | `.claude/skills/org-setup/references/permissions.md` の projection 更新 |
| attention payload の severity / TTL ladder 変更 | `tools/templates/attention.example.json` の severity / TTL 同期 |

paired ja-sync は **複数 worker 並列**で派遣して構わない（むしろ推奨）。
4 つの同期対象は互いに独立しているため、1 worker 1 PR で並走できる。
窓口は org-delegate の並列委譲ガイダンスに従って分割する（[[parallelize_delegation]]）。

## Step 5: CI cascade の予測と委譲

PyPI publish が完了してから 1〜数時間以内に、ja-side リポジトリの CI が新 runtime 版を
解決しに行く。Step 4 の paired PR が間に合わずに main にマージされた古い ja-side が
CI red になることが多い。

このとき **Secretary は CI failure を自分で調査しない**。
[[secretary_does_not_investigate_ci]] に従い、paired ja-sync worker に
「PR #N の CI 失敗を調査して直して」とだけ渡し、詳細 brief は書かない。
gh api / log / diff / source 読解は worker の責務。

予測される red の代表例:

- `tests/test_attention_runtime_integration.py` が `DEFAULT_NOTIFY` の旧値を期待して fail
- `tools/org_extension_schema.json` の hash mismatch
- attention template の severity 不一致による smoke test fail

これらは Step 4 で同期 PR が先行 land していれば回避可能。先行できないリリーススケジュール
の場合は、Step 5 で worker に投げる前提で release.yml 完了直後にスタンバイ。

## Step 6: 同一セッションでの land

Secretary 自身のセッションが context 上限に達すると、paired ja-sync の意図が
[`/secretary-handover`](../secretary-handover/SKILL.md) を経ても暗黙化しやすい。
本 skill の全 Step は**できる限り同一 Secretary セッション内で完了**させる:

- リリース worker 完了 → tag push → ja-sync worker 4 並列派遣 → 各 PR レビュー & merge
- 1 セッションで land しきれない場合は handover に「runtime vX.Y.Z リリース後の paired ja-sync が
  残タスク」と明示し、resume 後の最初のターンで本 skill を再読する

## 成果物

- runtime 側: リリース PR + v-タグ + PyPI 発行 + release.yml ジョブ green
- ja-side: 4 種の paired sync PR（DEFAULT_NOTIFY expectation / schema バイト同期 /
  permissions projection / attention template）
- CI: ja-side main の CI が new runtime 版で green

## 判断基準・閾値

| 基準 | 値 | 根拠 |
|---|---|---|
| paired ja-sync の起票タイミング | release worker 派遣と並走（同セッション） | カスケード遅延を最小化 |
| `git pull --ff-only` の挙動異常 | 即 user 報告 | 別経路で main が動いた可能性 |
| Secretary CI 調査 | しない | worker への委譲が標準（[[secretary_does_not_investigate_ci]]） |

## 応用・バリエーション

- **schema 変更のみ・DEFAULT_NOTIFY 不変**: Step 4 の対象は schema / permissions 同期の
  2 系統のみで足りる（test expectation は影響しない場合がある）
- **DEFAULT_NOTIFY だけ動く・schema 不変**: test expectation 更新のみで完結することが多い
- **classifier vocabulary を新規追加**: 4 系統全てに波及する典型ケース。Step 4 のうち
  4 worker 並列を強く推奨

## 注意点

- v-タグ push は user 明示承認なしで実行しない。release.yml は一度焼くと PyPI 版番号が消費される
- workers_dir の `git pull --ff-only` で non-fast-forward を踏んだら worker 派遣を中断
- `tests/test_attention_runtime_integration.py` は paired PR が main に入る前に
  runtime 新版を解決しに行く可能性があるため、ja-side の同期 PR は release.yml 完了直前
  〜直後の数時間でランドさせる時間圧がある
- `tools/org_extension_schema.json` はバイト一致が前提。手書きの format 差で diff が出ると
  worker レビューで弾かれる
- 本 skill は窓口専属。worker 側 SKILL ではない

## 履歴的背景

本 skill は claude-org-runtime v0.1.11 リリースサイクルで観測されたカスケードを
ベースに、Issue #25 / #26 / #28 / Backlog #32 / #33 の議論を通じて結晶化した
ワークフロー。
