# Worker（aainc-ops self-edit）

このワーカーは aainc-ops リポジトリ自身の worktree で作業する。`./CLAUDE.md`（ルート CLAUDE.md）の Secretary 指示は無視せよ。**あなたは窓口ではなくワーカーである。**

## 作業ディレクトリ（最重要制約）

あなたの作業ディレクトリ: `C:/Users/iwama/Documents/work/workers/aainc-ops/.worktrees/hook-phase2a`

起動直後に `pwd` を実行し、上記パスと一致することを確認せよ。

### 禁止事項
1. ルート `CLAUDE.md` を編集しない
2. `git push` 不可（完了報告で窓口依頼）

## 現在のタスク
- タスクID: hook-phase2a-sandbox-eval
- Issue: #79
- 目的: Phase 2a — sandbox.denyRead/denyWrite 導入 + eval/bash -c 明示パース化
- 検証深度: **full**
- ブランチ: feat/hook-phase2a-sandbox-eval

## 背景（必読）

- 調査レポート: `C:/Users/iwama/Documents/work/workers/hook-phase2-feasibility/report.md` を **必ず Read** すること
- Issue: `gh issue view 79` で本文確認
- Phase 1 の既存資産: `.claude/settings.json`, `.hooks/lib/segment-split.sh`, `.hooks/block-no-verify.sh`, `.hooks/block-dangerous-git.sh`
- 実装方針は report.md §3-(2) と §3-(5) を第一情報源とせよ

## やること（3 パート）

### Part 1: sandbox 設定追加

`.claude/settings.json` に以下を追加:

```json
"sandbox": {
  "filesystem": {
    "denyRead": [
      ".env",
      ".env.*",
      "**/credentials*",
      "**/*.pem",
      "~/.ssh/**",
      "~/.aws/**",
      "~/.config/gh/hosts.yml"
    ],
    "denyWrite": [
      "~/.claude/settings.json",
      "~/.ssh/**"
    ]
  }
}
```

- 既存の `permissions` / `hooks` を壊さないこと
- JSON 構文エラーを起こさないこと（`py -3 -m json.tool < .claude/settings.json` で検証）
- sandbox.enabled は Claude Code 側のデフォルトに任せる（明示 true にしない。既知バグ #32226 の影響範囲がまだ不明なため段階導入）

### Part 2: `.hooks/lib/segment-split.sh` に `unwrap_eval_and_bashc()` 追加

目的: 現状 `flatten_substitutions` の gsub 副作用で偶発的に検出できている `eval "..."` / `bash -c "..."` / `sh -c "..."` を**明示的な関数で独立化**する。Phase 2b で gsub 位置を修正しても検出が壊れないようにする。

仕様:
- 入力: セグメント 1 本
- 出力: セグメントに含まれる eval/bash -c/sh -c の引数文字列を**追加の検査対象セグメントとして並列に出力**
- マッチパターン:
  - `eval "X"` / `eval 'X'` / `eval X`（quote 有無）
  - `bash -c "X"` / `sh -c "X"` / `bash -c 'X'` / `sh -c 'X'`
  - ネスト（`bash -c "eval 'git commit --no-verify'"`）は最低 1 段は取り出す
- 既存 `flatten_substitutions` とは独立に動く（副作用に頼らない）
- `block-no-verify.sh` / `block-dangerous-git.sh` / `block-git-push.sh` から呼ばれるよう統合する
  - 既存の loop（セグメント走査）の手前で `unwrap_eval_and_bashc` の結果を SEGMENTS 配列に追加する形

### Part 3: README 更新

既に #81 で Phase 2 分割は反映済み。今回は以下を追加:

- 「三層防御の責任境界」表 (L123-131) に **sandbox 層** を追加し四層にする:
  - 起動タイミング: Bash サブプロセスの OS syscall
  - 守備範囲: `.env` / 認証情報系の読み書きを OS レベルで遮断
  - 設定場所: `.claude/settings.json` の `sandbox.filesystem`
- 「攻撃ベクトル × カバー層」マトリクス (新規表) を追加
  - 行: verify-bypass / force push / secret 読取 / 構造破壊 / 関数経由 bypass
  - 列: deny / PreToolUse hook / sandbox / pre-commit
  - 各セルに `✓` / `部分` / `-`
- PreToolUse hook の検知範囲セクションの eval/bash -c 記述から「副作用により動作」の断り書きを削除（明示パース化されたため）

## 受け入れ条件（Issue #79 より）
- [ ] JSON 構文 valid（`py -3 -m json.tool` で検証）
- [ ] `eval "git commit --no-verify"` / `bash -c "git commit --no-verify"` が明示パース経路で拒否される（副作用依存ではない）
- [ ] README にマトリクスと残存リスク（関数経由 bypass）が明記される
- [ ] 既存の Phase 1 回帰テスト群（`.hooks/` 下の bats や tests/）が通る
- [ ] Windows + Git Bash 実機で `cat .env` が sandbox により deny されるか**人間検証可**な手順を verification.md に追記（実機検証そのものは人間が行う）

## 権限
- git commit: 可
- PR 作成・push: 不可（窓口経由）

## Codex セルフレビュー（full 必須）

commit 完了後・完了報告前に **必ず** 実行:

```bash
codex exec --skip-git-repo-check "このブランチの main からの差分をレビュー。Issue #79 の受け入れ条件を満たすか、JSON / shell の正当性、eval/bash-c 明示パースの網羅性、README マトリクスの論理的整合性を Blocker/Major/Minor/Nit で分類し、指摘に根拠を添えて日本語で簡潔に"
```

- Blocker / Major は修正コミットを積み、再レビュー
- 同一指摘カテゴリ 3 ラウンド消せない → 即完了報告して窓口に判断仰ぐ
- Minor / Nit は原則残置、README 末尾に既知制限として明記

### 禁止事項
`codex:rescue` は使用禁止（過去ハング実害あり）

## 作業完了時（必須）

1. **完了報告**: `mcp__ccmux-peers__send_message(to_id="secretary", message="...")`
   - 完了した内容のサマリ
   - 変更ファイル一覧
   - commit SHA（複数あれば全部）
   - Codex ラウンド数と残 Minor/Nit
   - 実機検証が必要な手順（sandbox の動作確認手順）
2. **振り返り記録**: `C:/Users/iwama/Documents/work/aainc-ops/knowledge/raw/2026-04-25-{topic}.md` に再利用可能な学びを記録（sandbox の挙動知見、unwrap_eval_and_bashc の設計判断等）

## SUSPEND対応
"SUSPEND:" 受信で即中断、進捗・未コミット・次の一手・ブロッカーを報告。
