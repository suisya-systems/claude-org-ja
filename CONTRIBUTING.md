# Contributing to claude-org

claude-org は Claude Code をベースにした自己組織化マルチエージェントフレームワークです。窓口・フォアマン・キュレーター・ワーカーの 4 ロールを ccmux 上で連携させ、個人〜小規模チームでの運用テンプレートを提供することを主旨としています。

このリポジトリは **個人運用のテンプレート** という性格が強いため、機能追加よりも以下の貢献を歓迎します:

- バグ報告・再現手順
- ドキュメント改善（特に他環境での導入ハマりどころ）
- ロール設定・hook・スキルの汎用化（個人化された運用パターンの抽象化）
- 安全性に関する指摘（permission / hook の抜け、sandbox 回避経路など）

## セットアップ前提

開発に着手する前に [README.md](README.md) の「前提条件」と「インストール」セクションを満たしてください。最低限必要なのは:

- Claude Code
- ccmux 0.18.0 以上
- Python 3.8+
- Git / GitHub CLI

初回 clone 後は `ccmux --layout ops` で窓口ペインを起動した上で、Claude Code 上で **`/org-setup` を一度実行** してロール別 `settings.local.json` を生成してください（未実行だと開発中に許可プロンプトが多発します）。詳細は [docs/getting-started.md](docs/getting-started.md#インストール) を参照。

## バグ報告

GitHub Issues に投げてください。可能なら以下を含めてください:

- 再現手順（最小ケース）
- 期待した挙動 / 実際の挙動
- OS / Claude Code バージョン / ccmux バージョン
- 関連する hook やロール設定の差分

## Pull Request の出し方

1. このリポジトリを fork
2. `main` から作業ブランチを切る（例: `feat/xxx`, `fix/yyy`, `docs/zzz`）
3. 適切な粒度で commit を分ける（1 commit 1 テーマ）
4. PR を作成。CI が green であることを確認
5. レビュー指摘に対応

### コミットメッセージ規約

プレフィックスを付ける Conventional Commits 風の形式を採用しています。直近の `git log --oneline -20` を参考にスタイルを揃えてください。

- `feat:` 新機能
- `fix:` バグ修正
- `docs:` ドキュメントのみの変更
- `refactor:` 機能を変えないリファクタ
- `chore:` ビルド・依存・雑務
- `test:` テスト追加・修正
- `ci:` CI 設定の変更

例:

```
feat(hooks): add PreToolUse guardrails for verify-bypass flags
fix(org-state): handle missing workers/ directory gracefully
docs(readme): correct ccmux baseline version
```

## 検証要件

PR を出す前に以下が green であることを確認してください:

```bash
# ロール設定（settings.json / permissions / hooks）の整合性チェック
python tools/check_role_configs.py --include-local

# Python テスト
python -m pytest tests/
```

新規 hook を追加する場合は対応するテストも追加してください。

## スコープ外

以下は PR では扱いません。Issue で議論してください:

- 個人化された運用パターン（特定ユースケース向けスキルの追加など）
- 特定環境への最適化（パスや shell 前提を強く仮定する変更）
- 既存ロール構造を破壊する大規模リアーキ

不明点があれば Issue で相談してから PR 化することを推奨します。
