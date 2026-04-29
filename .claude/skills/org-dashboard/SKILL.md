---
name: org-dashboard
description: >
  組織のダッシュボードを更新してブラウザで開く。
  「ダッシュボード見せて」「状況を可視化して」「プロジェクト一覧見たい」
  「全体像を見せて」等で発動。
---

# org-dashboard: ダッシュボードを開く

ライブダッシュボードサーバー（`dashboard/server.py`）を起動してブラウザで開く。
サーバーが起動中であれば、ブラウザを開くだけでよい。データ生成は不要（サーバーが自動でリアルタイム配信する）。

## Step 1: サーバー状態確認

```bash
cat .state/dashboard.pid 2>/dev/null && kill -0 $(cat .state/dashboard.pid) 2>/dev/null && echo "running" || echo "stopped"
```

- `running` → Step 2 へ
- `stopped` → Step 1.5 へ

## Step 1.5: サーバー起動（停止中の場合のみ）

```bash
python3 dashboard/server.py &   # Mac/Linux
py -3 dashboard/server.py &     # Windows
```

起動後、`http://localhost:8099` でアクセス可能になる。

## Step 2: ブラウザで開く

```bash
open http://localhost:8099    # Mac
start http://localhost:8099   # Windows
```

ユーザーには「ダッシュボードを開きました → http://localhost:8099」と案内する。

## 補足

- ダッシュボードはリアルタイムで状態を反映する（.state/ ファイルの変更を自動検知）
- data.json の手動生成は不要。サーバーが /api/state で同等データを配信する
- サーバーは org-start で自動起動、org-suspend で自動停止される
