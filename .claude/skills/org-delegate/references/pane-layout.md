# Pane Layout Specification

WezTerm のペイン配置ルール。org-start と org-delegate が参照する。

```
┌──────────────┬──────────┬──────────┐
│              │ Worker1  │ Worker4  │
│              ├──────────┤──────────┤
│  Secretary   │ Worker2  │ Worker5  │
│              ├──────────┤──────────┤
│              │ Worker3  │ Worker6  │
├───────┬──────┤          │          │
│Foreman│Curat.│  ...     │  ...     │
└───────┴──────┴──────────┴──────────┘
```

## 配置ルール

| 対象 | 操作 | 備考 |
|---|---|---|
| フォアマン | 窓口ペインを `split-bottom --percent 20` | org-start Step 2 |
| キュレーター | フォアマンペインを `split-right --percent 50` | org-start Step 3 |
| ワーカー1人目 | 窓口ペインを `split-right` | フォアマンが実行 |
| ワーカー2〜3人目 | 右側最後のワーカーを `split-bottom` | 1列に3段まで |
| ワーカー4人目〜 | 列が3段なら `split-right` で新列開始 | 以降同じルールで積む |

- ワーカー数の把握: `.state/org-state.md` の Active Work Items を参照
- ワーカー完了時: 窓口がフォアマンに CLOSE_PANE を依頼 → `wezterm cli kill-pane --pane-id {id}`
- org-suspend 時の停止順: ワーカー → フォアマン → キュレーター
