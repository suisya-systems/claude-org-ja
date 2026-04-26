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
| フォアマン | `<repo>/.dispatcher/.claude/settings.local.json` | `.dispatcher/` で起動したフォアマン |
| キュレーター | `<repo>/.curator/.claude/settings.local.json` | `.curator/` で起動したキュレーター |
| ワーカー | ワーカーディレクトリの `.claude/settings.local.json` | org-delegate が動的に作成 |

## 各ロールの必要設定

**references/permissions.md** に全ロールのJSON定義がある。以下の手順でこれを参照する。

## 実行手順

### Step 1: 現在の設定を読み取る

以下の4ファイルを読み取る（存在しない場合は空オブジェクト扱い）:

1. `~/.claude/settings.json`
2. `<repo>/.claude/settings.local.json`
3. `<repo>/.dispatcher/.claude/settings.local.json`
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
- ~/.claude/settings.json: renga, renga-peers の許可を追加
- .dispatcher/.claude/settings.local.json: claude 起動コマンドの許可を追加
- (変更なし: .curator/.claude/settings.local.json)
```

変更がなかった場合:
```
全ての設定は最新です。変更はありません。
```

### Step 5: drift を解消する（`--prune` モード）

通常の Step 1〜3 は **additive-only**（不足分を追加するだけで既存は削除しない）。
過去に蓄積した広すぎる allow や旧エントリは残り続けるため、
`permissions.md` を SOT として `settings.local.json` を**完全に書き換える** prune モードを用意している。

実行は `tools/org_setup_prune.py` を使う:

```bash
# 差分プレビュー（書き換え無し）
python tools/org_setup_prune.py --role secretary --dry-run
python tools/org_setup_prune.py --all --dry-run

# 実行（タイムスタンプ付き .bak を自動生成 → 書き換え）
python tools/org_setup_prune.py --role secretary
python tools/org_setup_prune.py --all
```

対象ロール: `secretary` / `dispatcher` / `curator`。
（`user_common` は `~/.claude/settings.json` で他プラグインと同居するため対象外。
ワーカーは org-delegate が動的生成するため対象外。）

#### user 拡張の保護: `settings.local.override.json`

prune は `permissions.md` の role テンプレートで丸ごと書き換えるため、
個人で追加した allow / env / hook をそのままにすると消えてしまう。
これを避けるため、各 settings ファイルと**同じディレクトリ**に
`settings.local.override.json` を置くと、prune 時に deep-merge される。

例: 窓口で `Bash(my-private-tool:*)` を恒久的に許可したい場合は
`.claude/settings.local.override.json` に以下を書く（このファイルは
prune ツールが読むだけで、書き換えはしない）:

```json
{
  "permissions": {
    "allow": ["Bash(my-private-tool:*)"]
  }
}
```

マージ規則:
- `permissions.allow` / `permissions.deny`: base 順を保ったまま和集合
- `env`: キー単位 merge（override 側が勝つ）
- `hooks.PreToolUse[]` 等: 等値判定で重複排除した上で append
- それ以外のスカラー: override が勝つ

`.gitignore` 対象（個人設定のため）。チームで共有したい設定は
`permissions.md` 側に追加し、schema (`tools/role_configs_schema.json`) も同時に更新する。

#### dispatcher の `{claude_org_path}` 解決

dispatcher テンプレートには `{claude_org_path}` プレースホルダがある。
prune ツールは以下の優先順で解決する:

1. `--claude-org-path <abs>` 引数
2. 既存 `settings.local.json` の `env.CLAUDE_ORG_PATH`
3. 既存 hook command 内の `bash "<abs>/.hooks/..."` の `<abs>`

いずれも取れない場合（fresh install など）は `--claude-org-path` を明示する:

```bash
python tools/org_setup_prune.py --role dispatcher --claude-org-path "C:/Users/me/work/claude-org"
```

#### バックアップ

書き換え前に `settings.local.json.bak.YYYYMMDD-HHMMSS` を同ディレクトリに作成する。
失敗時はこの `.bak` を `mv` で戻せば原状復帰できる。
不要であれば `--no-backup` で抑止できる。

## 注意事項

- `settings.local.json` は `.gitignore` に入っている前提（個人設定のため）
- ユーザーレベルの `~/.claude/settings.json` は既存の設定（plugins 等）を壊さないよう注意する
- ワーカーの設定はこのスキルでは配置しない（org-delegate が担当）
- prune の挙動は `tools/org_setup_prune.py` の docstring と `tools/test_org_setup_prune.py` のテストが正典
