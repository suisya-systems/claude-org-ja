# herdr-app mod 開発の計測記録（herdr-app 0.1.13）

> ステータス: **計測記録のみ**。herdr-app の mod 開発（複製 → 変更 → 検証 → 描画確認）が回るかを実測した記録で、設計の決定や実装を含まない。既存の設計書・規範文書・スキルは本書によって変わらない。

## 1. 目的と範囲

herdr-app を claude-org-ja の面（画面）に使えるかを判断する材料のうち、「mod を書いて確かめる開発の輪が回るか」だけを測る。

- 対象: 既定の `agents` mod（サイドバーのエージェント一覧）を複製した `org-agents` に、各エージェント行へ core 端末のラベル `task_id` を表示する変更を 1 行だけ足す。
- 確かめ方: `herdr-app check` で検証し、`herdr-app shot` で実際の描画を PNG に撮って見る。
- 範囲外: 窓口・ワーカーの画面設計、既定 layout の変更、mod を組織の運用に組み込むこと。

mod 本体は `experiments/herdr-app-mods/org-agents/`（[`experiments/herdr-app-mods/org-agents/main.js`](../../experiments/herdr-app-mods/org-agents/main.js) と `mod.json`）に置く。

## 2. 判定基準（実測の前に固定）

以下は計測を始める前に書いたもので、結果を見て書き換えない。

| # | 基準 | 成功 | 失敗 |
| --- | --- | --- | --- |
| 基準 1 | `herdr-app check` が通る | `herdr-app check <mods/org-agents>` が終了コード 0 で終わる | 0 以外で終わる、またはエラーを出す |
| 基準 2 | shot に task_id が見える | `--label task_id=<値>` を付けて起動したエージェントの行に、その値が文字として描かれているのを shot の PNG で確かめられる | 行が出ない、または値が描かれない |
| 基準 3 | 変更から確認までの所要時間 | 記録するだけで合否は付けない。変更ファイルの配置から shot の PNG が得られるまでを、ホスト実行スクリプトの時刻で測る | — |

基準 3 の比較の目安として、引き継ぎ資料（`notes/herdr-core-app-handover-2026-11-08.md`「最初にやること」3）は「半日で確かめる」としている。

## 3. 環境

| 項目 | 値 | 出典 |
| --- | --- | --- |
| herdr-app | `herdr-app 0.1.13`（Windows 版、`C:\Users\iwama\AppData\Local\Programs\herdr-app\herdr-app.exe`） | `herdr-app --version` |
| hc | `hc 0.1.18`（Windows 版） | `hc.exe --version` |
| 表示 | layout `main`、zoom 1.2。shot の PNG は 1280×820 | `herdr-app status`、shot の PNG |
| 実行場所 | ワーカーは WSL2 の sandbox 内で、`/mnt/c` は読めるが書けず `.exe` も実行できない。Windows 側の操作はスクリプトに書き、窓口がホストで実行した | 作業指示 |
| 実行日 | 2026-11-08 | |

ユーザーの core・既存の端末（`t1`）・既定の mod と layout には触れていない。表示用に起動した端末は毎回 `hc kill` / `hc rm` で消し、最後の `hc ls --json` は `t1` だけを返した。

## 4. 手順

ホスト実行は 4 回。各回のスクリプトはコマンドと出力を時刻付きでログに残した（スクリプト・ログ・PNG は作業用フォルダに置き、コミットしていない）。

1. **複製**: app 自身のコマンド `herdr-app do mod.clone '{"name":"agents","as":"org-agents"}'` で `mods/org-agents/` を作り、変更前のまま `herdr-app check` を通す。
2. **変更**: 複製の `main.js` に 1 行を足す（下記）。repo の [`experiments/herdr-app-mods/org-agents/main.js`](../../experiments/herdr-app-mods/org-agents/main.js) を正本にし、ホストのスクリプトが `mods/org-agents/` へ上書きする。
3. **表示用エージェント**: `hc.exe run --bg --no-parent --name orgmeas --label task_id=herdr-app-mod-measurement --label herdr.space=<今のスペース> --label herdr.tab=orgmeas -- <状態を申告するプログラム>`。`herdr.tab` に app が知らない id を渡すと、今のスペースに新しいタブができる（AGENTS.md「An id Herdr doesn't know becomes a new space or tab」）。
4. **表示と撮影**: `herdr-app do mod.open '{"name":"org-agents"}'` で dialog として開き、変更前の `main.js` で shot → 変更後を上書き → `check` → shot。
5. **後片付け**: `mod.open` をもう一度呼んで閉じ、`hc kill` / `hc rm`。

足した 1 行（`main.js` 362 行目。行の名前の後、経過時間の前に、その端末のラベル `task_id` を小さな淡い字で出す。`agents()` の要素はラベルを持たないので、同じ `render` で取得済みの `terminals()` を address で引く）:

```js
.child(div().flex_none().text_size(10).text_color(t.dim).child(all.find((x) => x.address === a.address)?.labels?.task_id ?? ""))
```

## 5. 結果

### 5.1 判定

| 基準 | 結果 |
| --- | --- |
| 基準 1: `check` が通る | **成功**。変更前・変更後とも `check passed: C:\Users\iwama\.config\herdr-app\mods\org-agents`、終了コード 0。`herdr-app status` の `errors` も毎回空 |
| 基準 2: shot に task_id が見える | **成功**（4 回目）。変更後の shot で、dialog の「Working 1」の下の行 `~ · t5` の右、経過時間 `<1m` とスピナーの手前に `herdr-app-mod-measurement` が淡い小さな字で描かれていた。変更前の shot では同じ行にこの文字は無く、それ以外の描画は同じ。左サイドバーの既定 `agents` mod の同じ行には出ていない（既定 mod は変えていないので期待どおり） |
| 基準 3: 所要時間 | 変更後のファイルの上書き（19:37:08.020）から変更後の shot の完了（19:37:09.673）まで **1.65 秒**。内訳は `check` 1.5 秒、再読込を待つための固定の `sleep 1`、shot 0.1 秒。つまずきを含めた全体は、作業の着手（19:28 頃）から 4 回目の shot まで約 9 分、ホスト実行の 1 回目開始（19:30:54）から数えて約 6 分 |

### 5.2 つまずき（4 回に分かれた理由）

1. **Windows の Claude Code が状態を申告しなかった（2 回目）**。表示用に `claude.exe`（2.1.309）をタスク無しで起動したが、63 秒待っても `hc get` が `"reported": false` のままで、Agents の一覧に出なかった。3 回目に画面を `hc read --lines 0` で読むと、フォルダ信頼の確認（「Quick safety check: Is this a project you created or one you trust?」、選択肢は「❯ No, exit / Yes, I trust this folder」）で止まっていた。この画面にいる間は OSC 7501 を出さない。herdr-app の一覧に載るのは状態を申告した端末だけ（AGENTS.md「Agents are terminals whose program reports a status (OSC 7501)」）なので、ここで止まったエージェントは一覧から見えない。
2. **表示用エージェントを、状態を自分で申告する小さなプログラムに替えた（3 回目以降）**。OSC 7501 の書式は Claude Code 2.1.319 本体のコードから読み取った（`ESC ] 7501 ; state=<状態>:app=<名前>[:kind=…][:msg=<base64>] BEL`）。`pwsh.exe -NoProfile -Command '[Console]::Write([char]27 + "]7501;state=working:app=org-measure" + [char]7); Start-Sleep 300'` で、起動から 1 秒ほどで `hc get` が `"reported": true`、`"status": {"app": "org-measure", "state": "working"}` になり、既定サイドバーの Agents に「Working 1」として出た。mod の計測にはエージェント本体は要らず、この 1 行で足りる。
3. **`mod.open` の dialog で一覧の高さが 0 になった（3 回目）**。既定 `agents` の `mod.json` は `{"place":"none","title":"Agents"}` で、`overlay` の指定が無い。`mod.open` は「the overlay its mod.json describes」を開く（`herdr-app commands`）ので、大きさは中身に合わせて決まる。一覧の本体は残りの高さを埋める作り（`flex_1().min_h(0)`）なので高さが 0 に潰れ、変更前も変更後も見出しと時計のアイコンしか描かれなかった（2 回目に「No agent status yet …」の文が見えたのは、その文が高さを持つ部品だから）。複製の `mod.json` に `"overlay": {"kind": "dialog", "width": 320, "height": 360}` を足して解消した。`main.js` には関係しない、表示のしかただけの変更。`check` はこれを検出しない（AGENTS.md「It does not lay out the mod」のとおり、配置まではしない）。shot で見て初めて分かった。

### 5.3 その他の観察

- `mod.clone` は複製の中の自分の名前も書き換える。`main.js` の `openOverlay({ mod: "agents", … })` が `mod: "org-agents"` になり、`mod.json` に `"cloned_from": {"name": "agents", "version": "0.1.13"}` が付いた。`.d.ts` と `jsconfig.json` も一緒に複製された。手でフォルダを写すと、この書き換えを自分でやる必要がある。
- `place: "none"` の複製は、作っただけでは画面のどこにも出ない。見るには layout から参照するか `mod.open` する。今回はユーザーの layout を変えないために `mod.open` を使った。
- 保存しただけで、開いている dialog は描き直された（`check` 後の `sleep 1` で shot に反映済み）。再起動も開き直しも要らない。
- `herdr-app shot` は WSL 側のパス（`\\wsl.localhost\…`）にもそのまま書けた。shot は app のウィンドウだけを撮り、ポインタやキーの自動操作は要らなかった。
- 2 回目に作ったタブ `orgmeas` は、中の端末を `hc kill` / `hc rm` すると消えた。

## 6. まとめ

mod 開発の輪（複製 → 1 行の変更 → `check` → 保存で再描画 → `shot` で確認）は herdr-app 0.1.13 で回った。変更から確認までは 2 秒弱だった。引き継ぎ資料の目安の「半日」に対して、つまずきを含めても 10 分以内に収まった。

組織の面として使うときに効く点:

- `check` は配置を見ないので、高さの潰れのような見た目の誤りは shot でしか分からない。mod を変えたら毎回 shot を撮って見る前提にする。
- 一覧に出るのは状態を申告している端末だけ。フォルダ信頼の確認のような起動時の画面で止まった Claude Code は、`blocked` として現れるのではなく一覧に出ない。この状態は app の一覧では検出できないので、別の手段で見つける必要がある。
