# 動的ポート割当規律（並列 verify のポート衝突回避）

[`/org-conveyor`](../SKILL.md) はバックプレッシャーで **複数 worker を空き pane 分まで並列** に走らせる。各 worker は
別 worktree で verify を回すため、**固定ポート前提のアプリ**（Next.js dev server `:3000` / broker サーバー / ローカル
HTTP サービス等）を立てると **ポートが衝突** し、後から起動した verify が `EADDRINUSE` で落ちる・別 worker の
プロセスに当たって誤判定する。

Issue #637 の確定方針はこれを **動的ポート割り当て** で解く（serial verify レーン化は採らない＝並列性を殺さないため）。
規律は一語で言うと **「ポートを固定で書かない。env で受け取る」**。

## 規律

1. **app / サーバーは listen ポートを env から受け取る**。固定ポートをコードに焼かない。
   - 既定 env 名は `PORT`（多くのフレームワークの慣習）。アプリ固有の env があればそれに従う（例: Next.js は `PORT`、
     broker サーバーは自身の port env）。verify policy（[`.claude/skills/org-conveyor/references/scope-contract.md`](scope-contract.md)）に env 名を明記する。
2. **conveyor / worker は verify ごとに空きポートを動的確保し、env で app と検査側の両方へ渡す**。
   ポート番号をハードコードした再現コマンドを書かない。
3. **再現コマンドにも env 経由のポートを反映**して PR `## Test plan` に転記する（[`.claude/skills/org-conveyor/references/verify-evidence.md`](verify-evidence.md)）。
   ポート番号は実行ごとに変わるので、Test plan には **env 名で示し具体値を焼かない**（追試者が同じ規律で再確保できる）。

## 空きポートの確保（portable）

OS にエフェメラルポートを割り当てさせる（`:0` に bind して即 close、得た番号を使う）。repo 固有ツールに依存しない:

```bash
# 空きポートを 1 つ確保して env に入れる
PORT=$(python3 -c 'import socket;s=socket.socket();s.bind(("",0));print(s.getsockname()[1]);s.close()')
# app と検査の双方へ同じ env を渡す
PORT="$PORT" tools/run.sh &          # app は PORT を listen
curl -s "localhost:$PORT/health"     # 検査も同じ PORT を叩く
```

> bind→close→使用の間に他プロセスが同じポートを取る競合は理論上ありうるが、エフェメラル域はレンジが広く
> 並列数（= 空き pane 数、せいぜい数本）では実害が出にくい。確実性が要るアプリは「起動直後にポートを stdout へ
> 出させてそれを掴む」方式（フレームワークが対応していれば）に切り替えてよい。

## worker brief への載せ方

- 並列 verify を伴う重量レーン委譲では、[`/org-delegate`](../../org-delegate/SKILL.md) の `--impl-guidance` 等で
  「固定ポート禁止 / `PORT` env で受け取る / 再現コマンドは env 経由で書く」を brief に明記する。
- verify policy（スコープ契約）に env 名と確保方式を 1 行で残し、conveyor が複数 worker を投入する前提を固定する。

## 不採用案（serial verify レーン化）

verify を 1 本ずつ直列化すれば固定ポートでも衝突しないが、**ベルトの並列性（バックプレッシャー）を殺す**ため
Issue #637 では採らない。動的ポートなら並列のまま衝突を避けられる。どうしても直列必須なアプリ（共有 DB の
排他ロック等、ポート以外の資源競合）が出たら、それは scope 縁としてスコープ契約に明記し人間判断を仰ぐ。
