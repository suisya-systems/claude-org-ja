---
name: wsl-windows-chrome-screenshot
owner: worker
description: >
  WSL のワーカーから Windows 版 Chrome を headless で叩き、WSL 側に立てたプレビュー
  サーバの画面を PNG で撮るレシピ。サーバ起動と撮影を 1 つの Bash 呼び出しにまとめ、
  その呼び出しだけ sandbox を外す・1 枚目は捨て撮り・出力先は UNC でワークツリー、
  という固定手順と、撮れていないときの症状の弁別（`ss -ltnp` / WSL 側 curl）を含む。
  「WSL から Windows Chrome で画面を撮る」「rondo の画面のスクリーンショットを撮る」
  「before/after を撮り比べる」「ライト / ダークを撮り分ける」等で発動。
  撮影を伴わない UI 実装・テストには発動しない。
triggers:
  - WSL のワーカーから Windows Chrome でスクリーンショットを撮る
  - rondo など WSL 側で立てたプレビューサーバの画面を撮る
  - 変更の before / after を画で比較する
  - prefers-color-scheme のライト / ダークを撮り分ける
  - 撮った PNG が真っ白 / ERR_CONNECTION_REFUSED / 0 バイトになる
---

# WSL から Windows Chrome で画面を撮る

Linux 側の Chrome は共有ライブラリ不足で起動しない環境なので、Windows 版 Chrome を
headless で使う（rondo#318 の申し送り、`knowledge/raw/archive/2026-09-20-wsl-windows-chrome-file-url-screenshots.md`）。
以下は rondo#314 / #318 / #350 / #351 / #352 で繰り返し踏んだ手順を固定したもの。
**どの失敗もエラーが原因を指さず、PNG が「撮れてしまう」ことが多い**。

## 1. サーバ起動と撮影は 1 つの Bash 呼び出しに入れ、その呼び出しだけ sandbox を外す

sandbox は Bash 呼び出しごとに PID / network 名前空間を分けるため、

- 別呼び出しで起こしたサーバは**その呼び出しの終了と同時に死ぬ**（`setsid` しても畳まれる。
  `run_in_background: true` なら生きるが、次の呼び出しの curl からも Windows Chrome からも届かない）
- Chrome 側も sandbox 内から叩くと `<3>WSL (N - ) ERROR: UtilConnectUnix:526: socket failed 1`
  だけを出して **PNG を 1 バイトも書かない**（WSL interop の unix socket が bwrap に塞がれる。
  エラー終了はしない）

よって**サーバ起動・ヘルスチェック・撮影・停止をまとめた 1 呼び出しを
`dangerouslyDisableSandbox: true` で流す**。build / verify / git は sandbox 内のままでよい。

```bash
CHROME="/mnt/c/Program Files/Google/Chrome/Application/chrome.exe"
UNC='\\wsl.localhost\Ubuntu-24.04\home\happy_ryo\work\org\workers\<proj>\.worktrees\<task>\.worker-scratch\shots'
PORT=7361

shoot() {  # shoot <name> <scheme 0=dark|1=light> <url>
  "$CHROME" --headless=new --disable-gpu --hide-scrollbars \
    --blink-settings=preferredColorScheme=$2 --window-size=2560,1440 \
    --virtual-time-budget=4000 --screenshot="$UNC\\$1.png" "$3"
}

mkdir -p .worker-scratch/shots
node .worker-scratch/preview-$PORT.mjs > .worker-scratch/preview.log 2>&1 &
SRV=$!
for i in $(seq 1 20); do curl -fsS -o /dev/null "http://127.0.0.1:$PORT/?lang=en" && break; sleep 1; done
shoot warmup   1 "http://127.0.0.1:$PORT/?lang=en"   # 捨て撮り（§2）
shoot after-en 1 "http://127.0.0.1:$PORT/?lang=en"
shoot after-ja 1 "http://127.0.0.1:$PORT/?lang=ja"
kill $SRV
tail -5 .worker-scratch/preview.log
```

`&` で起こしたプロセスはその呼び出しの終了で死ぬので後始末も自然に済み、
`pgrep -f` / `pkill -f` を使わずに済む（Bash ツールは `zsh -c '<コマンド全文>'` で
起動するため、**同じコマンド内に殺したいパターン文字列があると `pgrep -f` が
その zsh 自身を拾って自殺する**。`Exit code 144` だけが返る。`[3]` のブラケット trick も
同一コマンド内に生の文字列がある限り効かない）。

## 2. サーバ起動直後の 1 枚目は必ず `ERR_CONNECTION_REFUSED` になる

同一呼び出し内で curl が 200 を返した後でも、**Chrome の 1 枚目だけ**
「このサイトにアクセスできません / ERR_CONNECTION_REFUSED」の PNG になる
（2 枚目以降は同じ URL で正常。独立した 2 回の実行で再現）。WSL2 の localhost forwarding が
Windows 側からの初回接続で張られるまでのラグ。**curl のヘルスチェックを強化しても検出できない**
ので、撮影ループの先頭に捨て撮りを 1 枚置く。判別はバイト数でよい
（2560x1440 のエラー画面はこの環境で 33,866 bytes 固定）。

## 3. 出力先は UNC でワークツリーへ書く

`/mnt/c/Users/<user>/AppData/Local/Temp/...` への `mkdir` は sandbox の write allowlist 外で
`Read-only file system` になる。Chrome には UNC でワークツリーを渡すのが通る（上の `$UNC`）。
既存の `Temp` 直下へ直接置くなら**ディレクトリを作らず** `r<issue>-<name>.png` のように
タスク識別子を名前に入れる（並走ワーカーと衝突しない）。

成功時は `NNNNN bytes written to file ...` を stdout に吐く。
**この行が出ないときは撮れていない**（ファイルの有無より先にこの行を見る）。

## 4. ライト / ダークは `preferredColorScheme`、値は 0=dark / 1=light

headless Chrome は既定で Windows 側の OS テーマ（この環境では dark）に従う。
`--blink-settings=preferredColorScheme=1` でライト固定。**`2` を渡すとエラーにならず
ライトのまま撮れる**ので、「ダークのつもりでライトを撮った」が無言で起きる。
ライトとダークのバイト数はほぼ必ず違うので、**`md5sum` が一致したら固定できていない**。

## 5. 症状の弁別

| 見えるもの | 切り分け | 実体 |
| --- | --- | --- |
| Chrome が `ERR_CONNECTION_REFUSED` の PNG、WSL 側 curl は通る | 2 枚目が正常に撮れるか | §2 の初回ラグ。捨て撮りで解決 |
| Chrome も WSL 側 curl（別呼び出し）も届かない | 別の Bash 呼び出しで `ss -ltnp \| grep <port>` が空 | サーバが呼び出し終了で死んでいる。§1 の 1 呼び出し化 |
| PNG が生成されない / `UtilConnectUnix: socket failed` | — | 撮影側が sandbox 内。§1 で sandbox を外す |
| `curl` は 200 だがログ末尾が `EADDRINUSE` | ログ末尾を読む | 別ワーカーの残骸が同じポートで応答している。§6 のとおりポートをずらす |
| 真っ白 / 無スタイルの PNG | 対象が `file://` か | Windows Chrome は WSL パスを `file://` で開けない。HTML と CSS を Windows 側 Temp へコピーしてから撮る |

ポート判定は「curl が通るか」ではなく**別の Bash 呼び出しで `ss -ltnp` に残っているか**で行う
（curl は同一呼び出し内なら死ぬ前に通ってしまう）。

## 6. before/after は `git archive` した別ツリーを別ポートで立てる

ワーカーは `git stash` 変更系も `git checkout -- <path>` も使えない。ワークツリーを触らずに
「前」を出す:

```bash
W=<worktree>
mkdir -p "$W/.worker-scratch/base"
git -C "$W" archive origin/main | tar -x -C "$W/.worker-scratch/base"
ln -sfn "$W/node_modules" "$W/.worker-scratch/base/node_modules"        # install は 1 回で済む
sed -i 's/7334/7434/g' "$W/.worker-scratch/base/scripts/page-preview.mjs"  # ポートを退避
npm --prefix "$W/.worker-scratch/base" run build
```

`scripts/page-preview.mjs` の `new URL("../dist/...", import.meta.url)` は**スクリプトの位置から
解決される**ので、コピー先の `dist/` を見る。よって「前」と「後」が同時に立ち、同じ Chrome
ループで撮り分けられる。`cd` は使わず `npm --prefix` で回す。

**「後」側もコピーしてポートを変える**こと。tracked ファイルを `sed -i` で書き換えると
`git diff` に混ざる。混ぜてしまったら `git show HEAD:<path> > <path>` で戻し、
`git status --short` が空であることを確認する。

ポートは `scripts/page-preview.mjs` が 7334 決め打ち。削除済み worktree のプレビューが
生き残って掴んでいることがあるので、**他ワーカーのプロセスは撃たず、複製してポートを
ずらす**（`ls -l /proc/<pid>/cwd` が `(deleted)` なら残骸）。

## 7. 撮った PNG は必ず 1 回見る

- `--window-size` の高さが足りないと下が切れた PNG がそのまま出る（エラーにならない）。
- 判定対象が画面下端の要素（`sticky bottom-*` 等）のときは、**高さ違いを 2 枚撮る**
  （例 2560,1440 と 2560,1700）。低い方では下端に貼り付いて必ず見え、高い方では
  静的位置に落ちるので、2 枚あると「貼り付いているのか、たまたま収まっているのか」が区別できる。
- 案と現物を比較する用途では、**同じ `--window-size`（= 同じ fold）で撮る**。
- 言語は cookie に残る。英語で並べたいなら `?lang=en` を明示する。
- SPA のハッシュ遷移（`#narrow/...`）は headless の 1 発撮りでは効かず、トップが撮れる
  （バイト数がトップと完全一致するのが手がかり）。撮れないものは「撮れなかった」と報告に書く。

## 出典

- `knowledge/raw/archive/2026-09-21-wsl-chrome-screenshot-and-pgrep.md`（rondo#314）
- `knowledge/raw/archive/2026-09-21-windows-chrome-first-shot-refused.md`（rondo#318）
- `knowledge/raw/archive/2026-09-21-before-after-shots-need-two-builds-and-no-sandbox.md`（rondo#350）
- `knowledge/raw/archive/2026-09-21-screenshot-server-and-chrome-in-one-bash-call.md`（rondo#351）
- `knowledge/raw/archive/2026-09-21-wsl-chrome-shots-need-sandbox-off-on-both-legs.md`（rondo#352）
- `knowledge/raw/archive/2026-09-20-wsl-windows-chrome-file-url-screenshots.md`（rondo#319、`file://` とウィンドウ高さ）
