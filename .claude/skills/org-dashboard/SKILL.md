---
name: org-dashboard
description: >
  組織のダッシュボードを更新してブラウザで開く。
  「ダッシュボード見せて」「状況を可視化して」「プロジェクト一覧見たい」
  「全体像を見せて」等で発動。
effort: low
allowed-tools:
  - Bash(python3 dashboard/server.py)
  - Bash(python3 dashboard/server.py *)
  - Bash(python3 tools/stop_dashboard.py)
  - Bash(open http://localhost:*)
  - Bash(xdg-open http://localhost:*)
  - Bash(explorer.exe http://localhost:*)
---

# org-dashboard: ダッシュボードを開く

ライブダッシュボードサーバー（`dashboard/server.py`）を起動してブラウザで開く。
サーバーが起動中であれば、ブラウザを開くだけでよい。データ生成は不要（サーバーが自動でリアルタイム配信する）。

サーバーは 8099 → 8100 → 8101 の順で空いているポートを使う（`dashboard/server.py` の `PORTS`）。同じホストの別の組織が 8099 を使っていることがあるので、URL は必ず `--status` の出力から取る。8099 を決め打ちしたり、ポートを順に叩いて探したりしない（別の組織のダッシュボードを開いてしまう）。

本スキルの起動・確認コマンドは sandbox 外で実行する（理由は org-start Block C の sandbox 注記）。

## Step 1: サーバー状態確認

```bash
python3 dashboard/server.py --status
```

`--status` は bind 後にサーバーが書く `.state/dashboard.json`（実際のポートと pid）を読み、そのポートで応答しているのがこの clone のサーバーかを HTTP で確かめる。終了コードで分岐する:

- `0` → 標準出力の URL を使って Step 3 へ
- `1`（停止中。記録が無い / 記録の pid が消えている）→ Step 2 へ
- `3`（ポート記録を持たない旧版サーバーがこの組織で稼働中）→ 止めてから Step 2 へ。Mac/Linux は `python3 tools/stop_dashboard.py`（cmdline を照合してから止める）、Windows native は [`.claude/skills/org-down/SKILL.md`](../org-down/SKILL.md) Step 2 の PowerShell 手順（`Get-CimInstance` で CommandLine を照合してから `Stop-Process`）を使う。Windows native では `py -3 tools/stop_dashboard.py` を使わない（内部の `os.kill(pid, 0)` が Windows ではプロセスを終了させるため、照合前に無関係なプロセスを止めうる）。停止できなかったら、再起動が必要な旨を人間に報告して終える
- `2`（記録が壊れている / 記録のポートで別のプロセスが応答している / 応答しない）→ 標準エラーの理由を添えて人間に報告して終える。URL は案内しない

## Step 2: サーバー起動と起動待ち

```bash
python3 dashboard/server.py &
python3 dashboard/server.py --status --wait 15
```

`--wait 15` は最大 15 秒、起動を待って確かめる。`0` なら標準出力の URL で Step 3 へ。`0` 以外なら起動失敗として理由（標準エラー）を人間に報告し、URL は案内しない（候補ポートが全部使用中、ポート記録が書けない等。`python3 dashboard/server.py` を前景で実行するとエラーが見える）。画面は開くのにデータが出ないときは `/api/state` の失敗（state.db の破損等）を疑う。

## Step 3: ブラウザで開く

環境に合うコマンドを 1 つ使う（`<url>` は Step 1 か Step 2 で `--status` が出力した URL）:

```bash
open <url>         # Mac
xdg-open <url>     # Linux
explorer.exe <url> # WSL（Windows 側の既定ブラウザで開く）
```

どれも無い環境では開かずに URL だけを案内する。ユーザーには「ダッシュボードを開きました → <url>」と案内する。

## 補足

- ダッシュボードはリアルタイムで状態を反映する（state.db と workers / knowledge の変更を自動検知）
- サーバーは org-start で自動起動、org-suspend で自動停止される
