# runtime-release-with-paired-ja-sync の設計理由と経緯

[`.claude/skills/runtime-release-with-paired-ja-sync/SKILL.md`](../SKILL.md) の背景説明。手順と制約は SKILL.md 側にある。

## 1. なぜこの skill が必要か

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

## 2. tag 前検証が省略できない理由

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

## 3. 残余レースの扱い

(e) と `push` の間には原理的に窓が残る（GitHub の tag push には
「main が `$SHA` のままなら通す」という compare-and-swap が無いため、これは手順で閉じられない）。
ただし **tag は不変の SHA に対して打つ**ので、この窓で main が進んでも発行される artifact は
(c)(d) で検証した内容そのままであり、壊れた版が出ることはない。窓で起こりうるのは
「直後にマージされた別 PR がこの版に入らない」だけで、これは release の通常挙動である。
したがって対策は「main を止める」ではなく、**(e) を push の直前に置き、外したらやり直す**で足りる。

## 4. 経緯

本 skill は claude-org-runtime v0.1.11 リリースサイクルで観測されたカスケードを
ベースに、Issue #25 / #26 / #28 / Backlog #32 / #33 の議論を通じて結晶化した
ワークフロー。
