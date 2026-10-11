---
name: org-dashboard
description: >
  組織のダッシュボードを更新してブラウザで開く。
  「ダッシュボード見せて」「状況を可視化して」「プロジェクト一覧見たい」
  「全体像を見せて」等で発動。
effort: low
allowed-tools:
  - Bash(cat .state/dashboard.pid)
  - Bash(kill -0 *)
  - Bash(python3 dashboard/server.py *)
  - Bash(curl -sf http://localhost:*)
  - Bash(open http://localhost:*)
  - Bash(xdg-open http://localhost:*)
  - Bash(explorer.exe http://localhost:*)
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
python3 dashboard/server.py &
```

## Step 2: ポートを確かめる

サーバーは 8099 → 8100 → 8101 の順で空いているポートを使う（`dashboard/server.py` の `PORTS`）。別のサービスが 8099 を使っていることがあるので、ページのタイトルでダッシュボードと確かめたポートを URL にする:

```bash
for p in 8099 8100 8101; do curl -sf --max-time 2 http://localhost:$p/ | grep -q 'claude-org — Ops Center' && echo $p && break; done
```

どのポートも応答しなければ、起動失敗として人間に報告する。画面は開くのにデータが出ないときは `/api/state` の失敗（state.db の破損等）を疑う。

## Step 3: ブラウザで開く

環境に合うコマンドを 1 つ使う（`<port>` は Step 2 の値）:

```bash
open http://localhost:<port>         # Mac
xdg-open http://localhost:<port>     # Linux
explorer.exe http://localhost:<port> # WSL（Windows 側の既定ブラウザで開く）
```

どれも無い環境では開かずに URL だけを案内する。ユーザーには「ダッシュボードを開きました → http://localhost:<port>」と案内する。

## 補足

- ダッシュボードはリアルタイムで状態を反映する（state.db と workers / knowledge の変更を自動検知）
- サーバーは org-start で自動起動、org-suspend で自動停止される
