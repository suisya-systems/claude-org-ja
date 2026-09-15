# テスト記述の運用知見

クロスプラットフォーム / CI で踏みやすいテスト失敗の回避策。

## Python テストでパス完全一致のアサーションは `os.path.join` を使う

POSIX ハードコード（`f"{base}/.git/config"` のような文字列結合）は Windows CI で失敗する。`os.path.join` はプラットフォームに応じて区切り文字（POSIX なら `/`、Windows なら `\`）を返すので、テスト側もそれに合わせる必要がある。

```python
# NG: POSIX ハードコード（Windows CI で fail）
assert any(p == f"{base_clone}/.git/config" for p in captured)

# OK: プラットフォーム非依存
expected_joined = os.path.join(base_clone, ".git/config")
assert any(p == expected_joined for p in captured)
```

`claude-org-runtime` の `test_settings_generator.py` で `base_clone` anchor のテストアサーション追加時に、Windows CI で実際に発火した。同テストファイル内の `home_anchor` 系テストは既にこのパターンで書かれていたため、コード内慣例としても確立済み。新規アサーション追加時は周辺コードのスタイルを踏襲する。

適用範囲: Python テストでパスの完全一致アサーションを書く全ケース（OS 依存の git/HOME パス、`additionalDirectories` 検証、bwrap arg 検証など）。

出典: `2026-05-10-delegation-windows-path-separator-in-tests.md`

## node:assert の大きな Buffer deepEqual 失敗が CPU 100% → OOM でホストごと落とす

cc-usage-insights の worker が `node --test` を実行 → exit 137。同日 3 回再派遣、3 回とも同じ死に方（kernel log: `Out of memory: Killed process (node) anon-rss ~24 GB` を計 3 回観測）。

根因: `test/packaging.test.mjs` の「2 つのディレクトリはバイト一致」assertion が `assert.deepEqual(readFileSync(a), readFileSync(b))` で ~95 KB vs ~82 KB の Buffer を比較していた。worker がソース側ファイルを編集し同期コマンド未実行 → 不一致 → Node の `assert` が失敗メッセージ用の差分計算に入り、数分 CPU 張り付き後にメモリが際限なく増える。

**切り分け手順**: worktree のコピーを一時ディレクトリに作り `ulimit -v <上限>; timeout 60 node --test <file>` で file ごとに実行する。HEAD 版ファイルを `git show HEAD:path >` で差し戻して二分探索する（`git checkout` はコピーでも元 worktree の index を触るので使わない）。

**対処**: 大きな Buffer の一致比較は `Buffer.equals()` を使う（`assert.deepEqual` / `assert.deepStrictEqual` は失敗時に差分表示のためのディープ比較コストを払う設計で、Buffer サイズに対して安全ではない）。

**運用上の教訓**: worker のテスト暴走はホスト全体を落とす。WSL の `.wslconfig` にメモリ上限が無いと巻き添えで全ペインが死ぬ。

**誤診の罠**: ユーザー体感は「ワーカー派遣すると CPU MAX」だったが、派遣自体でなく worker が走らせたテストが原因だった。`ps` の ELAPSED が壊れて（SEGV）いたので pid 順序と dmesg のタスク表で追った。

出典: `2026-09-08-node-assert-buffer-diff-oom.md`（cc-usage-insights Issue #43）
