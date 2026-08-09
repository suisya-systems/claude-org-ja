DELEGATE: 以下のワーカーを派遣してください。

タスク一覧:
- snap-a-background: add a sparkline
  - ワーカーディレクトリ: <SANDBOX>/workers/clock-app（CLAUDE.md・設定配置済み）
  - ディレクトリパターン: A: プロジェクトディレクトリ
  - プロジェクト: clone or reuse: -
  - ブランチ (planned): feat/snap-a-background
  - Permission Mode: auto
  - 検証深度: full
  - 指示内容: 詳細は `<SANDBOX>/workers/clock-app/CLAUDE.md` を参照。要約: add a sparkline

配置 (placement): background_tab（spawn-flow 3-1d。6 条件を満たさなくなったら既定の同一タブ経路に倒すこと）
窓口の報告先: `to_id="1"`（数値 pane id。背景タブの worker からは pane 名 `secretary` が解決しないため、brief も同じ数値 id で生成済み）