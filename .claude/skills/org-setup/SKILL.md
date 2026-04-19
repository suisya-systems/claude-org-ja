---
name: org-setup
description: |
  組織の全ロール（窓口・フォアマン・キュレーター・ワーカー）に必要な
  Claude Code の許可設定・環境変数を一括で配置・更新するスキル。
  「設定して」「許可設定を更新して」「セットアップして」
  「permissions設定」「org-setup」等で発動する。
---

# org-setup: 組織の許可設定を一括配置

組織の各ロールが必要とする permissions allow と環境変数を、
正しいスコープの settings ファイルに配置する。

## 設定ファイルの配置先とスコープ

Claude Code は**起動ディレクトリの `.claude/` 配下**から設定を読み込む。
サブディレクトリで起動した場合、親ディレクトリの設定は**読み込まれない**。
そのため、ロールごとに独立した設定ファイルが必要になる。

| スコープ | ファイルパス | 対象 |
|---|---|---|
| ユーザー共通 | `~/.claude/settings.json` | 全プロジェクト・全ロール |
| 窓口 | `<repo>/.claude/settings.local.json` | リポジトリルートで起動した窓口 |
| フォアマン | `<repo>/.foreman/.claude/settings.local.json` | `.foreman/` で起動したフォアマン |
| キュレーター | `<repo>/.curator/.claude/settings.local.json` | `.curator/` で起動したキュレーター |
| ワーカー | ワーカーディレクトリの `.claude/settings.local.json` | org-delegate が動的に作成 |

## 各ロールの必要設定

**references/permissions.md** に全ロールのJSON定義がある。以下の手順でこれを参照する。

## 実行手順

### Step 1: 現在の設定を読み取る

以下の4ファイルを読み取る（存在しない場合は空オブジェクト扱い）:

1. `~/.claude/settings.json`
2. `<repo>/.claude/settings.local.json`
3. `<repo>/.foreman/.claude/settings.local.json`
4. `<repo>/.curator/.claude/settings.local.json`

### Step 2: 差分を特定する

各ファイルについて、上記「各ロールの必要設定」と比較し、不足しているエントリを特定する。

### Step 3: マージして書き込む

不足分を追加する。既存の設定は**絶対に削除しない**。
`permissions.allow` は配列なので、既存エントリを保持しつつ新規エントリを追加する。
`env` はオブジェクトなので、既存キーを保持しつつ新規キーを追加する。

### Step 4: 結果を報告する

変更があった場合:
```
設定を更新しました:
- ~/.claude/settings.json: wezterm, claude-peers の許可を追加
- .foreman/.claude/settings.local.json: claude 起動コマンドの許可を追加
- (変更なし: .curator/.claude/settings.local.json)
```

変更がなかった場合:
```
全ての設定は最新です。変更はありません。
```

## 注意事項

- `settings.local.json` は `.gitignore` に入っている前提（個人設定のため）
- ユーザーレベルの `~/.claude/settings.json` は既存の設定（plugins 等）を壊さないよう注意する
- ワーカーの設定はこのスキルでは配置しない（org-delegate が担当）
