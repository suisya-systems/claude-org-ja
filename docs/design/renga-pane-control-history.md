# renga のペイン制御・エラーコードの経緯

[`.claude/skills/org-delegate/references/renga-error-codes.md`](../../.claude/skills/org-delegate/references/renga-error-codes.md) と [`.claude/skills/org-delegate/references/pane-layout.md.in`](../../.claude/skills/org-delegate/references/pane-layout.md.in) から外した経緯と検証の記録。手順はどちらも元のファイルにあり、ここは「なぜそうなっているか」を辿るときだけ読む。

## 1. renga-error-codes.md の節の分け方

renga 2.0 系で**追加された**コードは、既存の Known codes 表に導入バージョン列を足すのではなく、`## renga 2.0 系 — 追加コードと意味論の変更` に節を分けて置いた（backend / 世代ごとに節を分ける形は broker 節で既に取っている前例で、既存行を 1 行も書き換えずに追記できるため）。2.0 で**意味論が変わった / 適用範囲が読み替わりうる**既存コード（`pane_not_found` / `pane_vanished` / `last_pane` / `split_refused`）は新規追加ではないので、Known codes 表の当該行に直接併記した。

capability の 4 トークンは renga 側でも 4 定数で、`renga/src/ipc/mod.rs` の `SERVER_CAPABILITIES` は `CAP_CALLER_SCOPE` / `CAP_CROSS_TAB_PEERS` / `CAP_SPAWN_TAB` / `CAP_CALLER_SCOPE_CLOSE_IDENTITY` を並べる。4 つとも renga CHANGELOG の `## [2.0.0] — 2026-08-07` という単一リリースで出ているが、mcp-peer は 4 トークンを別々に要求するので、判定も別々に行う。

## 2. renga 1.x の「フォーカス中のタブしか見えない」制約

1.x 系では `list_panes` / `focus_pane` / `send_message` / `inspect_pane` が、現在フォーカス中のタブのペインしか見なかった。別タブ（`new_tab` 由来）のワーカーは `pane_not_found` で返るので、org-delegate は全ワーカーを同一タブ内の `spawn_pane` で起動した（suisya-systems/renga#71）。

2.0 系では、この前提が `caller_scope`（対象は caller のタブで、フォーカスに依存しない）と `cross_tab_peers`（messaging はタブ横断・数値 id 宛）で置き換わった。同一タブ起動の根拠のうち messaging とフォーカス追従の部分は解消した。pane 制御は caller のタブ内に留まるので、オーケストレータが操作するペインを同一タブに置く規律は残る。

## 3. `last_pane` の 2.0 系での適用範囲の根拠

一次情報は renga `v2.0.0` の実装。

- `src/app/layout_ops.rs:660-669`: `is_only_pane` かつ `self.workspaces.len() <= 1` のときだけ `LAST_PANE` を返し、偽なら `self.close_tab(ws_index)` して `Ok(pane_id)`
- `src/ipc/mod.rs:241-242` の `Request::Close` の doc 「the whole tab is closed. Fails with `last_pane` if it's the last pane of the only remaining tab」と、`:789` の `LAST_PANE` 定数も同旨

## 4. pane 操作を Group A / Group B に分ける理由

上流は pane 操作を 2 群に割り、同一リリースの別トークンとして出した（4 トークンは `renga/src/ipc/mod.rs`:123-128 の `SERVER_CAPABILITIES` に並び、リリースも 1 つ）。分けている理由はリリース時期ではなく、サーバー世代のスキューである。先行 3 トークン（`caller_scope` / `cross_tab_peers` / `spawn_tab`）を広告する `#290` 世代の server は、未知の `from_pane` を落として `close` / `set_pane_identity` をユーザーが見ているタブに解決し、不可逆にペインを閉じる。そのため上流は `caller_scope_close_identity` を "a token of its own" として立てた（`renga/docs/api-surface-v1.0.md`:576-582）。

bundled mcp-peer の実装もこの割り方どおりで、`renga/src/mcp_peer/mod.rs` の `handle_close_pane` / `handle_set_pane_identity` が要求するトークンは `crate::ipc::CAP_CALLER_SCOPE_CLOSE_IDENTITY`、`handle_list_panes` / `handle_focus_pane` / `handle_inspect_pane` / `handle_send_keys` が要求するのは `crate::ipc::CAP_CALLER_SCOPE` で、別の定数である。

## 5. Group B の相対セレクタ禁止が SHOULD から MUST になった経緯

2026-08-07 の人間判断（ja#823）の記録。

この条項は当初 MUST で起案されたが、人間の判断で SHOULD に格下げされた。危険自体は実在し、かつ amendment が持ち込んだものではなく以前から存在していた。しかし org 自身の正準手順が Group B を相対セレクタで撃つ箇所を当時いくつも抱えており、不可逆操作の手順を未検証のまま変える方がリスクが高いと判断された。件数の正は契約 T-§4.2 の Group-B relative-selector call-site ledger（実測 11 calls / 8 files）であり、当時の段落が挙げていた「6 箇所」の列挙は誤りだった。特に [`.claude/skills/org-attention-start/SKILL.md`](../../.claude/skills/org-attention-start/SKILL.md) を非適合として数えていたが、契約自身が「Not in the table — the attention watcher is already compliant」で既に適合であり数え違いだったと訂正している。**コード既定の輸送層 `org-broker` にはこの失敗モードが無い**（capability を一切広告せず単一タブ規約で解決する）ことも判断材料だった。Group B の呼び出し箇所が移行されれば MUST への引き上げが想定されており、その移行が gate であって amendment の ratify は gate ではない、というのが当時の整理である。

その移行は 2026-08-09 に完了し、条項は同日 in-place で MUST へ昇格した。台帳の 11 行すべてが各行の mechanism 付きで discharge され、同期 prose 8 件も discharge されている（契約 T-§4.2 の discharge condition と、同節の "That migration was carried out on 2026-08-09, and this paragraph is discharged."）。詳細は契約 [`docs/contracts/backend-interface-contract.md`](../contracts/backend-interface-contract.md) T-§4.2「Why SHOULD and not MUST — recorded decision」。

## 6. balanced split が rect ベースになった経緯（pane-layout）

固定 target や序数 `k` ベースの lookup table では、dispatcher 幅の累積半減や、ワーカーが途中で閉じた後の再派遣で想定レイアウトと実レイアウトが乖離し、早期に `split_refused` を誘発していた。rect ベースでは常に実レイアウトから target を選ぶので、「閉じた slot を詰めるとテーブル前提と乖離する」問題は起きない。

定数値の prose を pane-layout から削除したのは、runtime と doc の drift が `[split_refused]` 等の不可解な失敗を生む原因になるため。

### 手で辿った `choose_split` の参考表

`secretary 280×43 / dispatcher 140×43 / curator 140×43` の直後レイアウト（ターミナル約 280×86、org-start で secretary horizontal split → dispatcher vertical split 直後を想定）を入力にした場合の `choose_split` の挙動。curator が常駐していた頃の初期レイアウトが前提で、今の運用では curator はオンデマンド起動中だけ存在する。正準値は runtime（`claude_org_runtime.dispatcher.runner`）で、食い違ったら runtime を信じる。

| spawn | 選出 role | direction | 直感的な根拠 |
|---|---|---|---|
| 1st | secretary | vertical | secretary が分割可能サイズを満たすうちは role priority で最優先 |
| 2nd | curator | vertical | secretary が SECRETARY_MIN_WIDTH ガードで脱落、次点 priority の curator が選ばれる |
| 3rd | curator | horizontal | role priority が strict primary なので curator が MIN_PANE を割るまで curator 連続 |

curator が MIN_PANE を割って脱落した後は priority 2 の worker 群に流れる。
