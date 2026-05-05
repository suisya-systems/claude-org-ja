DELEGATE: 以下のワーカーを派遣してください。

タスク一覧:
- snap-c-gitignored: redact gitignored notes
  - ワーカーディレクトリ: <HOSTREPO>（CLAUDE.local.md・設定配置済み）
  - ディレクトリパターン: C: gitignored サブモード (registered repo 直接編集)
  - プロジェクト: 既存 repo 直接編集: <HOSTREPO>
  - ブランチ (planned): (Pattern C: 既存 repo の現在ブランチで作業 / 新規 branch なし)
  - Permission Mode: auto
  - 検証深度: full
  - 指示内容: 詳細は `<HOSTREPO>/CLAUDE.local.md` を参照。要約: redact gitignored notes

窓口ペイン名: `secretary`（renga layout で登録済み）