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
