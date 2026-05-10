# スキーマ検証の運用知見

YAML / JSON authoring と byte-level schema 比較で踏みがちな silent failure と、その防御パターン。

## 「キーが存在するか」と「値が truthy か」は別物として扱う

スキーマ validation で「絶対に書いてはならないキー」を reject する際、`raw_role.get(key) is None` ではなく `key in raw_role`（キー存在ベース）で判定する。`get()` は YAML/JSON で値を `null` にした authoring エラーを silent に許してしまう。

実例（claude-org-runtime#13、Codex review round 1/2）: `worker_roles` に `sandbox_by_pattern: {A?, B?, C?}` を追加し、worker role では `sandbox` / `sandbox_by_pattern` を mutual exclusive、org role では `sandbox_by_pattern` を reject する仕様を入れた。最初の実装は `raw_role.get("sandbox_by_pattern") is None` で「未宣言」を判定していたが、これは `sandbox_by_pattern: null`（キーは存在するが値が None）のとき、

- worker role: `sandbox` と `sandbox_by_pattern: null` が併存しても通る → silent fallthrough
- org role: `sandbox_by_pattern: null` が通る → reject されない

という silent misconfig を許してしまう。Codex review で指摘されて `"sandbox_by_pattern" in raw_role` に切り替えた。

教訓: `_VALID_ANCHORS` 系の閉じた集合 validation でも同じく「null 値 = 未指定ではない」を意識する。role × pattern × sandbox の cross-product schema や Phase 1 sandbox / Phase 2 hooks のような将来追加でも同じ罠が起きやすい。

出典: `2026-05-10-key-presence-vs-value-truthiness-in-schema-routing.md`

## best-effort 文字列置換には「未展開 placeholder 残存ガード」を必ず併設する

`_substitute()` が「mapping にあるキーだけ置換し、無いキーは untouched で残す」挙動を取るなら、render の最終段で「downstream consumer が literal として扱う部分に未展開 placeholder が残っていないか」を walk して reject する guard を必ず併設する。特に security boundary を表現する struct（sandbox / permissions）では必須。

実例（claude-org-runtime#13）: Pattern B sandbox は `{base_clone}/.git/worktrees/{task_id}` を含むが、CLI 呼び出しで `--base-clone` が渡されないと、rendered `settings.local.json` に literal `"{base_clone}/..."` が残る。bwrap launcher は `additionalDirectories` を concrete path として消費するので、未展開 placeholder がそのまま流れると sandbox の境界が静かに壊れる（Layer 3 が無効化されるか、起動時に意味不明なエラー）。

実装パターン: `_reject_unresolved_pattern_b_placeholders()` のように対象 dict を visit し、`{base_clone}` / `{task_id}` / `{branch_ref}` を含む string が残っていれば、不足している flag 名（`--base-clone` 等）を含む usable error を投げる。「足りない flag が何か」を error message に書くと operator がすぐ直せる。

出典: `2026-05-10-key-presence-vs-value-truthiness-in-schema-routing.md`

## ja-only Layer 2 credential mirror は drift checker で strip してから byte 比較する

`tools/check_runtime_schema_drift.py` の byte 比較は、ja 側 `worker_roles` の `permissions.deny` に含まれる ja 固有 Layer 2 home credential mirror（`~/.netrc`, `~/.npmrc`, `~/.config/gh/` 等）が runtime bundled schema には存在せず、byte drift として fail する問題がある。

理由: runtime bundled schema は汎用ベースラインで ja 固有 credential deny を含まない。ja 側 PR3（secretary/curator）は `required_deny` / `required_allow` 経由で対称に管理しているが、`worker_roles` の `permissions.deny` には ja-only の Layer 2 エントリが直接入る → byte 比較が非対称を検出して fail。

解決パターン: `_strip_ja_only_sandbox_bodies` 関数の `worker_roles` 処理パス内で、`_JA_ONLY_LAYER2_CREDENTIAL_DENIES`（Layer 2 credential mirror の既知エントリセット）に含まれるエントリを `permissions.deny` から除去してから byte 比較に渡す。

PR #416（commit 15c235, `ja-phase1-pr4-worker-roles-bodies`）で実装。

付記: 大型 1-PR では「事前 Codex design review + post-impl Codex self-review の二段構え」が有効。PR #416 では Codex round 1–3 で以下が検出・修正された:

- ja-only Layer 2 credential mirror の strip（このパターン）
- packed-refs B2 surface の追加
- doc-audit Pattern B read surface mirror の調整

出典: `2026-05-10-ja-only-layer2-credential-mirror-drift-strip.md`
