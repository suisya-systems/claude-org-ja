# renga-peers MCP error codes — Dispatcher / Secretary reference

renga 0.14.0+ の `renga-peers` MCP サーバは、エラー応答に安定した machine-readable な code を載せる。ディスパッチャー / キュレーター / 窓口は message 文字列の substring match ではなく **code で分岐する**のを推奨する。

> **版数の読み分け（1.x 系 / 2.0 系）** — 本文書の既定の前提は **renga 0.14.0+（1.x 系）**で、`## Known codes` の表と `## シェル側のハンドリング例` はこの世代を正典として読む。renga 2.0 系で**追加された**コードは、既存 12 行に導入バージョン列を足すのではなく **`## renga 2.0 系 — 追加コードと意味論の変更` に節を分けて**置いた（backend / 世代ごとに節を分ける形は broker 節で既に取っている前例で、既存行を 1 行も書き換えずに追記できるため）。一方 2.0 で**意味論が変わった / 適用範囲が読み替わりうる**既存コード（`pane_not_found` / `pane_vanished` / `last_pane` / `split_refused`）は新規追加ではないので、Known codes 表の当該行に適用世代を明記したうえで直接併記してある。いま自分がどの面に繋がっているかは **capability ごとに個別に**判定する（契約 T-§cap は `caller_scope` / `cross_tab_peers` / spawn tab placement を**独立**と規定しており、1 つが無いことは他が無いことを意味しない）。判定は capability query か契約の observable marker で行い、**`server_too_old` を「接続先は 1.x 系」の証拠に使ってはならない** — それが証明するのは「要求したその capability が無い」ことだけで、同じ server が別の capability（例えば `cross_tab_peers`）を備えて全タブ列挙を返していることはありうる。1 つの拒否から世代を推定すると、全タブ列挙に単一タブ前提を当てる危険な取り違えになる。

## Wire format

MCP ツール（`mcp__renga-peers__*`）が失敗すると、JSON-RPC error の human-readable message 先頭に `[<code>] <human message>` が埋まる。renga 側の `fmt_code` 関数がこの形式を保証。

```
mcp__renga-peers__send_message(to_id="worker-nonexistent", message="hi")
→ renga refused send: [pane_not_found] pane not found: Name("worker-nonexistent")
```

抽出方法: tool result text を substring match（`[pane_not_found]` 等で case 分岐）。

## Known codes

| Code | 意味 | Dispatcher の推奨挙動 |
|---|---|---|
| `pane_not_found` | 指定した pane 名 / id / Focused が存在しない | **呼び出し文脈で分岐する（1.x / 2.0 共通で同じ手順を使ってよい）**。**pane 制御文脈**（`close_pane` / `inspect_pane` / `focus_pane` / `send_keys` / `list_panes` の target）= そのワーカーは既に閉じた扱い: `.state/workers/worker-*.md` の status を `pane_closed` に遷移、`WORKER_PANE_EXITED` を窓口に通知、リトライしない。**ただし対象が他タブに居ると分かっているピア（`list_peers` が `same_tab: false` を返したピア）は例外** — pane 制御は caller のタブ内に閉じたままなので、生存していても `pane_not_found` が返る。この場合は閉鎖と読まず「停止できない残存」として journal + 窓口 escalate に回す（下記 2.0 節）。**messaging 文脈**（`send_message`。broker では `peer_not_found`）= **即閉鎖扱いにしない**: `list_peers` 再取得 → 得られた**数値 peer id** で 1 回だけ再送 → **数値 id でも失敗したときにだけ** lifecycle 確認（`list_panes` / `poll_events` で消滅を裏取りしてから `pane_closed` 遷移 + `WORKER_PANE_EXITED`）へ落とす。手順の正本は「renga 2.0 系 — 追加コードと意味論の変更」節の messaging 分岐。**注意（1.x 系）**: `list_panes` / `focus_pane` / `send_message` / `inspect_pane` は現在フォーカス中のタブのペインしか見えない。別タブ (`new_tab` 由来) のワーカーは本 code で返るので、org-delegate では全ワーカーを同一タブ内 `spawn_pane` で起動する (suisya-systems/renga#71)。**2.0 系での supersede**: この「フォーカス中のタブしか見えない」前提は `caller_scope`（対象は caller のタブ = フォーカス非依存）と `cross_tab_peers`（messaging はタブ横断・数値 id 宛）で置き換わり、同一タブ起動の根拠のうち **messaging / フォーカス追従の部分は解消された**（pane 制御は caller タブ内に留まるので、オーケストレータが操作するペインを同一タブに置く規律自体は残る） |
| `pane_vanished` | resolve 成功後に消えたレース | **pane 制御文脈では `pane_not_found` と同等扱い**（閉じた扱い・リトライしない）。**messaging 文脈でも閉鎖扱いのまま**で、`pane_not_found` の messaging 分岐には**乗せない**: resolve は成功しているので、2.0 で `pane_not_found` に生じた「別タブに生存している peer を名前解決できず取り違える」誤検知クラスに当たらない（= 同等扱いなのは `pane_not_found` の **pane 制御文脈の枝**に対して、と読む）。ハンドラを共通化したい場合に閉鎖確定前の `list_peers` を 1 回挟むのは任意（居れば数値 id で再送、居なければ従来どおり閉鎖確定。呼び出しが 1 回増えるだけで害はない） |
| `last_pane` | `close_pane` で唯一のタブの唯一のペインを閉じようとした | 通常のワーカー停止では発生しない (窓口/ディスパッチャー/キュレーターが同タブに同居するため)。`org-suspend` 末端で残った最後のペイン (通常は窓口) に対して発生した場合、そのペインは自分自身で `exit` して自然終了させる。強制再試行はしない。**2.0 系での適用範囲は未確認** — 「唯一のタブの唯一のペイン」という 1.x の定義が 2.0 でタブ単位 (そのタブの最後のペイン) に読み替えられたかは、一次情報 (`suisya-systems/renga#287` 系の epic 要約) に記載が無い。他タブが残っている状況で本コードが保護に働くかは前提にせず、**安全網として当てにしない**（`org-suspend` の窓口自己 close 禁止は本コードに依存しない構造的根拠で書いてある） |
| `split_refused` | `spawn_pane` / `spawn_claude_pane` が MAX_PANES / too small で拒否 | ワーカー起動 (`org-delegate` Step 3) で balanced split のいずれかのステップが 16 ペイン上限 / `MIN_PANE_WIDTH` / `MIN_PANE_HEIGHT` で拒否された場合、キュレーター → 窓口に escalate。典型シナリオは (a) 9 並列以上に到達、(b) ターミナル幅が balanced split の要件 (W ≥ 160) を満たさない、(c) ワーカー退役後の再派遣でレイアウト tree が想定と乖離。`new_tab` フォールバックは tab-scoped 制約のため不可 (suisya-systems/renga#71)。**2.0 系**: 同一タブ配置の要求自体は契約 T-§4.2 で retained (pane 制御は caller のタブ内に留まるため、別タブに置いた子は `inspect_pane` / `close_pane` / `list_panes` から見えなくなる)。タブ容量側の対応コードは下記 2.0 節の `tab_limit_reached` |
| `cwd_invalid` | `spawn_pane` / `spawn_claude_pane` / `new_tab` の `cwd` が存在しないか、ディレクトリでない | renga 0.16.0+ で追加。ペイン作成前に reject されるので half-mutated layout にはならない。Dispatcher 側では窓口に escalate し、ワーカーディレクトリ準備（org-delegate Step 1.5）が完了しているか、相対パスの解決基準（caller pane の cwd）を取り違えていないか確認 |
| `invalid-params` | `spawn_claude_pane` の `args[]` に conflicting flag を含めた / `send_keys` の `keys[]` に未知のキー名を含めた等、JSON-RPC レベルの input 検証失敗 | `spawn_claude_pane` では `--dangerously-load-development-channels` / `--permission-mode` / `--model` を `args[]` に入れると rejected。構造化フィールド（`permission_mode` / `model`）で渡す。発生したらコード側のバグなので journal 記録 + 窓口 escalate |
| `name_in_use` | `set_pane_identity` で既存の別ペインが使用中の name を割り当てようとした | `/org-start` Step 0 の secretary 識別修復では、この code を拾ってユーザーに「永続修復には `/org-suspend` → 再起動」を提示する。短期回避は numeric pane id 運用 |
| `name_invalid` | `set_pane_identity` で全桁数字 / 禁止文字を含む name を指定した | 許可文字は `[A-Za-z0-9_-]`。全桁数字は numeric pane id と曖昧化するため拒否される。バグなので journal 記録 |
| `io_error` | PTY write / spawn / OS レベル失敗 | 1 サイクル spin して再試行。2 連続で同じ worker に出たら窓口に `IO_ERROR_DETECTED` で escalate |
| `shutting_down` | renga 本体がシャットダウン中 | 監視ループを **即停止** する。窓口 (`secretary`) に renga-peers で `FOREMAN_STOPPING` を通知（best-effort — renga 自体が落ちる場合は届かない） |
| `app_timeout` | renga 内部 App スレッドが応答しなかった | 1 サイクル spin (renga 再起動は管理者判断)。連続発生なら窓口にログ |
| `parse` / `protocol` | 通常出ない (MCP が正しく組み立てる前提) | 発生時はバグ。journal に記録して窓口に `IPC_PROTOCOL_ERROR` で報告 |
| `internal` | renga 内部不変条件違反 (parser lock poison 等) | `app_timeout` と同じ扱い |

## renga 2.0 系 — 追加コードと意味論の変更

> **適用範囲**: 本節は、pane / peer ツールの意味論を作り替えた renga の epic `suisya-systems/renga#287`（2026-08-05 実装）**以降の server** にだけ適用する。renga 側 CHANGELOG は BREAKING 扱いで、リリース番号は 2.0.0 が見込まれる（番号は未確定なので本節では「2.0 系」と呼ぶ）。**`server_too_old` を除き、0.14.0〜0.16.x（1.x 系）に繋がっている間は本節のコードが 1 つも発生しない** — その場合は上の `## Known codes` と本節の `server_too_old` 行だけを読めばよい。
>
> **`server_too_old` だけは 1.x 系に繋いでいるときにこそ出る**（発行元が backend ではなく **client 側の capability gate** だからで、2.0 対応クライアントが 1.x server に capability を要求した瞬間に fail closed する）。「本節は 2.0 系専用」と読んで本節ごと読み飛ばすと、**このコードが実際に出る唯一の環境で**非再試行 / escalate の分岐を落とすことになる。

### 2.0 系の capability と、それが変える前提

| capability | 何が変わるか | 由来 |
|---|---|---|
| `caller_scope` | pane 操作 7 ツールの対象が「**フォーカス中のタブ**」から「**caller のペインが属するタブ**」に変わる（フォーカス非依存）。`list_panes` は自タブのみ返す。旧 server へは fail closed | `suisya-systems/renga#288` |
| `cross_tab_peers` | peer messaging がタブ横断になる。**名前解決は送信者のタブ内限定**／**数値 peer id 宛はタブ横断可**／`list_peers` は**全タブ列挙**。解決できない宛先は `pane_not_found` を返す（silent 成功をやめた fail loud 化）。`PeerInfo` に optional の `tab` / `tab_name` / `same_tab` が載る | `suisya-systems/renga#289` |
| spawn の tab 指定（`CAP_SPAWN_TAB`） | `spawn_*` が tagged tab selector で配置先タブを取れる（背景タブ生成を含む）。tab 系エラー 4 種はここで出る | `suisya-systems/renga#290` |

`#291`（org サイドバー、`Ctrl+B` / `[ui] org_sidebar`）は UI 機能でエラーコード面の影響はない。

### 2.0 系で追加されたコード

| Code | 導入 | 意味 | 出る操作 | 復旧方針（再試行の可否まで） |
|---|---|---|---|---|
| `server_too_old` | 2.0 系クライアント | クライアントが要求した 2.0 capability（`caller_scope` / `cross_tab_peers` / spawn の tab 指定）を接続先 server が備えておらず fail closed した | 当該 capability を要求する全操作 | **非一時障害。自動再試行を禁止する**（同じ server は何度呼んでも古いまま。spin もバックオフも無意味）。**renga 本体の upgrade 要求**として窓口に escalate し、upgrade されるまではその機能を使う経路自体を止めて 1.x 系の運用（全ペイン同一タブ・名前宛 messaging）に倒す。journal に記録する |
| `tab_not_found` | 2.0（`suisya-systems/renga#290`） | spawn / tab 指定の selector（tag / id / name）が解決できない | tab 指定つき `spawn_*` / `new_tab` | 呼出側が持っている **tab snapshot が古い**とみなす。**tab 一覧を取り直してから 1 回だけ再試行**する。snapshot を更新しない素の再試行はしない（同じ結果になる）。更新後も解決できなければ selector 側の設定ミスとして窓口に escalate |
| `tab_ambiguous` | 2.0（`suisya-systems/renga#290`） | tab の**名前**指定が複数のタブに一致した | 同上 | **同じ名前での再試行はしない**（曖昧さは時間で解けない）。tab 一覧から**一意な tab id** を取り、id 指定に切り替えて再試行する（pane の all-digit-is-id 規則と同じ「曖昧なら id」方針） |
| `tab_limit_reached` | 2.0（`suisya-systems/renga#290`） | タブ数が上限に達して新規タブを作れない | `new_tab` / 背景タブ生成を伴う `spawn_*` | **同一要求の自動再試行を禁止する**（上限は待っても空かない）。**capacity escalation** として窓口に上げ、既存タブへの配置に切り替えるか、ワーカー退役でタブを空けるかは人間判断。pane 側の `split_refused` と同じ扱いの容量エラー |
| `target_tab_mismatch` | 2.0（`suisya-systems/renga#290`） | 呼出側が指定した対象タブと、実際に解決されたペイン / タブが食い違う | tab 指定を取る操作 | **呼出側のバグとして扱う。再試行しない**（自動復旧させるとタブ跨ぎの誤操作を隠す）。journal に記録して窓口に escalate し、selector を組み立てているコード側を直す |

### `pane_not_found` の messaging 分岐（2.0 系で意味論が変わった箇所）

2.0 では **名前解決が送信者のタブ内に限定**された（`cross_tab_peers`、`suisya-systems/renga#289`）。したがって `send_message` の名前宛が `[pane_not_found]` で返っても、**そのワーカーが別タブで生きている**可能性がある。名前が同一タブ内に無いときのエラー化自体は 1.x 系でも起きていた（冒頭 Wire format の例）が、2.0 では解決範囲がタブに固定されたぶん、この誤検知クラスが新たに現実的になった。messaging で受けた `[pane_not_found]` を即 `pane_closed` に落とすと、**生きているワーカーを閉鎖済みと誤記録する**。

```
1. send_message(名前宛) が [pane_not_found] を返す（broker では [peer_not_found]）
2. list_peers を引き直す（2.0 の list_peers は全タブ列挙）
3. **名前だけで宛先を選び直してはならない**。2.0 の全タブ列挙では `name` が一意でなく
   （契約 T-§2.2。`worker-{task_id}` 等の予約名は 2 org 並走で構造的に衝突する）、
   **別 org の同名ワーカーを掴んで再送しうる**。同定は次の順で行う:
   a. 送信時に控えていた **peer id が list_peers に残っていれば、その id で** 1 回だけ再送する
      （id はこの列挙の中で一意 — 契約 T-§2.1 のタブ横断一意性）
   b. peer id が消えている場合、名前が一致するレコードは**候補にすぎない**。
      `same_tab: true` であることを確認できたレコードだけを採用する
      （同一タブ内では名前が一意 — 契約 §1.8）
   c. 名前一致が他タブのレコードしか無い場合は**再送しない**。それは別 org のワーカーでありうる
   → 再送に成功したら lifecycle は動かさない（別タブではなく同タブ内で id が変わっただけ）。
      以後そのワーカーへは数値 id を使う
4. list_peers に居ない、または数値 id 宛の再送も [pane_not_found] で失敗した
   → ここで lifecycle "確認" に進む。**この時点ではまだ閉鎖確定にしない**:
      a. 同タブのピア: pane_exited の観測をもって消滅を確定する。list_panes の不在を
         根拠にする場合は、その列挙が呼び出し側のものだと確認できているときに限る —
         caller_scope 非広告の backend (renga 1.x を含む) では list_panes は
         フォーカス中のタブに追随するので、確認シーケンスの途中でフォーカスが別タブへ
         移ると、生存中のピアでも「不在」になり誤って閉鎖確定する。確認できなければ
         pane_exited の観測を待つか、c の indeterminate に倒す
      b. 他タブのピア（same_tab: false）: list_panes には原理的に出ないので不在は証拠に
         ならない。pane_exited の観測か、後続 list_peers からの消失のどちらかで確定する
      c. a / b のどちらも取れない → **indeterminate** として journal + 窓口 escalate。
         「閉じた」に倒さない
5. 消滅が確定したら lifecycle 処理: status を pane_closed に遷移し WORKER_PANE_EXITED を窓口に通知
```

- 再送は **1 回だけ**（ループにしない）。2 回目以降も同じ結果になるうえ、閉鎖検知が遅れる。
- Step 4 の裏取りを省いて 3 → 5 に直行しない。契約 T-§2.1 の復旧順は「数値 id 送信も失敗したときに初めて lifecycle 条件として扱い、**pane 制御 / event 面（§1.5・§3.1）で確認する**」で、確認段が手順の一部である。
- **status の書き手**: worker 状態ファイルの `Status: pane_closed` を書くのは**ディスパッチャー**（[`docs/contracts/state-semantics-contract.md`](../../../../docs/contracts/state-semantics-contract.md) Set B の terminal transition 規定）。窓口面のフロー（`/org-suspend` 等）が本分岐を回した場合は、窓口は状態ファイルの `Status` を書かず、pending 集合から外して journal に記録し、terminal transition はディスパッチャー / 既存の遷移経路に委ねる。
- **pane 制御文脈**（`close_pane` / `inspect_pane` / `focus_pane` / `send_keys` / `list_panes` の target）は**同タブのピアについては従来どおり「既に閉じた」扱いでよい**。pane 操作は 2.0 でも caller のタブ内に閉じている（`caller_scope`）ため、別タブ生存の救済は原理的に効かない。**他タブのピア（`same_tab: false`）は例外**: 契約 T-§4.2 が「タブ横断の pane-addressed control は `pane_not_found` を返す」と規定しているため、**生存中でも同じコードが返る**。他タブのピアに対する `close_pane` の `pane_not_found` は「閉じた」の証拠にならないので、閉鎖確定させず「停止できない残存」として journal + 窓口 escalate に回す（org-suspend が全ペイン停止を偽って完了報告するのを防ぐ）。分岐の第一軸は**文脈**であり、世代ではない。第二軸が**同タブ / 他タブ**である。
- この手順は **1.x 系に対してもそのまま実行してよい**。1.x の `list_peers` は現在タブしか列挙しないので救済が効かないだけで、余計な誤閉鎖は起こさない。ハンドラを世代で分岐させる必要はない。
- `pane_vanished` はこの分岐に**入れない**。resolve が成功した後のレースなので、上記の「名前が別タブで解決できなかった」クラスとは別物（Known codes 表の当該行を参照）。

### broker（`ORG_TRANSPORT=broker`）との関係

broker は `cross_tab_peers` を広告せず、`new_tab` も提供しない（契約 Surface 4.3 は `new_tab` を OPTIONAL とし、broker amendment で omit 済み）。したがって本節の **2.0 限定コードは broker 面では 1 つも発生しない** — `server_too_old` も tab 系 4 種も出ない。broker の messaging 宛先解決失敗は下の broker 節の **`peer_not_found`** で、broker は同一タブ規則を維持するため「別タブに生存」の誤検知クラスが原理的に存在せず、従来どおり**閉じた peer 扱い**でよい（`list_peers` 再取得 → 数値 id 再送の救済は broker では不要。transport 非依存に書きたい場合に実行しても、余分な呼び出しが 1 回増えるだけで害はない）。

### 契約側の正本

契約は [`docs/contracts/backend-interface-contract.md`](../../../../docs/contracts/backend-interface-contract.md#proposed-amendment-renga-20-multi-tab-capability-conditional-cross-tab-addressing)（"Proposed amendment (renga 2.0 multi-tab): capability-conditional cross-tab addressing"）を正本とする。そこでの規則は **capability 条件付き**で起案されている: `cross_tab_peers` を**広告する backend にだけ**「名前解決は送信者タブ内限定 / 数値 pane id 宛はタブ横断可 / `list_peers` は全タブ列挙」を適用し、**広告しない backend（現行の `org-broker` と、capability 以前の全 backend）は従来の同一タブ規則を維持する**（Surface 2.1 の同一タブ宛 send / Surface 2.2 の現在タブ列挙 / Surface 4.2 の SINGLE-TAB MUST を条件付き supersede する追記型 amendment。`PeerInfo` の `same_tab` / `tab` / `tab_name` の型と nullability、本節の追加エラーコードと `pane_not_found` の messaging 側の意味論も同 amendment が規定する）。**capability が判定できないときのフェイルセーフ既定は「広告していない」側**（＝従来の同一タブ規則）である点に注意。この非広告側には **conformance MUST** が伴う: `list_peers` が caller のタブ外のピアを返す backend は `cross_tab_peers` を広告し marker を載せなければならず、したがって**全レコードが marker を欠く列挙は caller のタブのピアだけを含む**（cross-tab 列挙かつ marker 無しの backend は非適合。契約 T-§cap）。ただし「旧版と判定した」が licence するのはそこまでで、列挙されたタブが**呼び出し側のタブである**ことまでは保証しない（`caller_scope` 非広告の backend ではフォーカス中のタブに追随するため）。所属の判定はこの前提を確認したうえで harness 側の規律として別途行う。この amendment は**起案（PROPOSED）で ratify 前**なので、本節の運用手順と契約本文が食い違った場合は契約側が正本。

## Broker（`ORG_TRANSPORT=broker`）の追加コードとツール名射影

本ファイルは **既定 `renga`**（`ORG_TRANSPORT` 無設定）の error code を正典として記述する。`ORG_TRANSPORT=broker`（opt-in・切戻し可）では MCP サーバー名が `org-broker` になり、ツールの **完全修飾名が `mcp__renga-peers__*` → `mcp__org-broker__*`** に機械置換される（wire format `[<code>] <message>` の抽出方法・分岐方針は同一）。broker は上表の共有コードのうち **`pane_not_found` / `last_pane` / `invalid-params` を意味が一致するものとして再利用**し、以下の broker 固有コードを **加算**する（renga harness は影響を受けず、broker harness も未知コードは default-branch で扱う）。契約の正本は [`docs/contracts/backend-interface-contract.md`](../../../../docs/contracts/backend-interface-contract.md) Surface 8（broker auth & delivery、ratified 2026-06-14）§8.7。

| Code | 意味 | 出る操作 | 推奨挙動 |
|---|---|---|---|
| `token_invalid` | bind token が未知 / malformed / revoked | 認証が要る全操作 | バグ or session 喪失。journal 記録 + 窓口 escalate |
| `session_invalid` | この agent の broker session が消えた（daemon 再起動 / bind drop） | 認証が要る全操作 | spawn からやり直し（再 bind）が必要。窓口 escalate |
| `tool_not_authorized` | caller の `auth_role` tier に当該ツールが含まれない（§8.3 tier gating） | tier 制限のある操作 | 設定ミス。tier 設計を見直し窓口 escalate（renga には無い概念） |
| `peer_not_found` | `send_message` / messaging の宛先 id / name が解決できない | messaging 操作 | renga の `pane_not_found`（messaging 文脈）相当。broker は `cross_tab_peers` を広告せず同一タブ規則を維持するので、renga 2.0 のような「別タブに生存している peer」の誤検知クラスが無く、**従来どおり閉じた peer 扱い**でよい（`list_peers` 再取得 → 数値 id 再送の救済は不要） |
| `name_taken` | spawn / `set_pane_identity` の name 衝突 | spawn 系 / `set_pane_identity` | renga の `name_in_use` の broker 綴り。同じ扱い（numeric id 運用 or 永続修復） |
| `no_backend` | terminal adapter（tmux/WezTerm）が利用不可 = "adapter_unavailable" | pane 制御操作 | adapter 環境を確認し窓口 escalate。renga の `io_error` 近傍 |
| `nudge_failed` | pull 用ナッジを宛先ペインに配送できなかった | `send_message` | 本文はキュー済だが受信側が気付けない。再送 or 窓口 escalate |
| `unknown_tool` | broker surface に存在しないツールを呼んだ（`new_tab` / `focus_pane` 等） | 全操作 | broker は `new_tab` / `focus_pane` を意図的に omit。呼び出し側のバグ |

> **renga 2.0 系コードとの関係**: broker は `cross_tab_peers` を広告せず `new_tab` も提供しないため、`server_too_old` および tab 系 4 コード（`tab_not_found` / `tab_ambiguous` / `tab_limit_reached` / `target_tab_mismatch`）は **broker 面では発生しない**。broker の messaging 宛先解決失敗は上表の `peer_not_found` のみで、同一タブ規則のまま（「renga 2.0 系 — 追加コードと意味論の変更」節を参照）。

> **設計 §5.2(ii) の命名との対応**: `token_*` = `token_invalid` + `session_invalid`、`adapter_unavailable` = `no_backend`、tier gating の追加が `tool_not_authorized`。`name_taken` は共有 `name_in_use` の broker 綴り。

> **broker の ok-return / 配送差**: 後述の renga 例外（`list_peers` / `send_message` の renga 非接続時 ok-text shim）は **broker には無い**（§6.3 carve-out 不適用）。broker では transport loss も §8.7 のエラーコードで返る。受信モデルは **push 一次（`claude/channel`）/ pull フォールバック**に再設計済（runtime push-first 0.1.24+、transport-lab `docs/design/broker-native-roles.md` §9）: 各ペイン同居の channel sidecar（`server:org-broker-channel`）が `notifications/claude/channel` で本文を idle に注入し、sidecar 不在 / unhealthy / channel 非対応時は pull フォールバックへ降格する。フォールバックの受信契機は各役割の能動 cadence poll（`mcp__org-broker__check_messages`）が正路で、daemon の pane-local ナッジは出れば契機になりうるが idle を起こさないため依存しない（既存「ナッジを見たら check_messages」prose は撤回せずこの fallback cadence として読む。§9.6）。`nudge_failed` は**フォールバック pull のナッジ**配送失敗を指すコードで、push 一次経路では本文配送は channel sidecar が担う。契約面: Surface 8 / §8.4 / §8.8 は **push 一次が ratified 済み**（2026-06-15、S3。§8.4 を push 一次へ・§8.8 を `receive_mode="push"` へ。broker pull-only を supersede し pull は fallback として retain。詳細は [`docs/contracts/backend-interface-contract.md`](../../../../docs/contracts/backend-interface-contract.md)・既存 ratified 本文は不変更）。

## MCP ツール特有の ok-return ルール

以下 2 つの MCP ツールは、renga 到達不可でも **JSON-RPC error にせず ok-text で返す** 例外扱い。

- `mcp__renga-peers__list_peers`: renga 本体未起動 / detached mode → `"(no peers — renga not reachable: <reason>)"`
- `mcp__renga-peers__send_message`: 同上 → `"(message dropped — renga not reachable: <reason>)"`

他の renga-peers ツール (`spawn_pane` / `close_pane` / `list_panes` / `focus_pane` / `new_tab` /
`check_messages` / `set_summary` / `poll_events` / `inspect_pane` / `send_keys`) は `require_connected` で非接続時に JSON-RPC error になる。この 2 つだけは**ハンドリング分岐を `[code]` パターンだけでなく `(no peers` / `(message dropped` 接頭辞**でも見るべき。

> **2.0 系との切り分け**: この ok-text 例外は「renga 本体に**繋がらない**」場合の話で、2.0 系でも変わらない。**繋がっているが宛先が解決できない** `send_message` は別クラスで、`suisya-systems/renga#289` が silent 成功の経路を潰して `[pane_not_found]` を返すようにした（fail loud 化）。したがって 2.0 系では「送ったのに届かない」が黙って成功していた分がエラーとして表に出るので、messaging の case 分岐は `(message dropped` だけでなく `[pane_not_found]` も必ず拾い、後者は「renga 2.0 系 — 追加コードと意味論の変更」節の messaging 分岐に回す。

## シェル側のハンドリング例

MCP ツール呼び出し結果テキスト (`content[0].text` or JSON-RPC error message) に対する case 分岐。`pane_not_found` は**呼び出し文脈**で挙動が分かれるので、呼び出し側が文脈を渡す:

```
# MCP ツール呼び出し後、返ってきたテキストを $out に入れた状態を想定
# $ctx = 呼び出し文脈: "messaging" (send_message) /
#        "pane_ctl" (close_pane / inspect_pane / focus_pane / send_keys / list_panes の target)
# $same_tab = そのピアの list_peers 上の same_tab ("true" / "false" / "" = 不明)
# $legacy_single_tab = その列挙が marker 全欠落 (= 契約 T-§cap により単一タブ) で、かつ
#                      それが呼び出し側のタブだとフォーカス前提を確認できた場合に "true"
case "$out" in
  *"[pane_not_found]"*|*"[peer_not_found]"*)   # peer_not_found は broker の messaging 綴り
    case "$ctx" in
      pane_ctl)
        # 分岐は「同タブと確認できた (true)」を条件にする。not-"false" で括ると
        # 不明 ("") が閉鎖側に落ち、他タブで生存中のワーカーを誤って retire する
        if [ "$same_tab" = "true" ] || [ "$legacy_single_tab" = "true" ]; then
          # 同タブと確認できた (または marker 無し列挙 = 契約 T-§cap の conformance MUST により
          # 単一タブ、かつフォーカス前提を確認済み) → 従来どおり閉鎖扱いで lifecycle 処理へ
          mark_worker_pane_closed worker-foo
        elif [ "$same_tab" = "false" ]; then
          # 他タブのピアはタブ横断 pane 制御が原理的に不可 (契約 T-§4.2)。
          # 生存中でも同じコードが返るので、閉鎖の証拠にしない
          log_journal "cross-tab pane control refused (not a closure signal): $out"
          escalate_secretary "worker left running in another tab (cannot close): $out"
        else
          # same_tab 不明 ("")。契約 T-§2.2-fields は欠落を unknown と規定しており
          # legacy の証拠ではない。他タブで生存している可能性があるので閉鎖に倒さない
          log_journal "tab scope unknown — not treating pane_not_found as closure: $out"
          escalate_secretary "worker lifecycle indeterminate (tab scope unknown): $out"
        fi
        ;;
      messaging|*)
        # 既定は安全側 (= messaging 扱い、閉鎖確定しない)。$ctx 未設定 / 未知の文脈もここへ落とす
        [ "$ctx" = "messaging" ] || log_journal "unknown ctx '$ctx' — messaging (safe side) で処理"
        # 2.0 系: 名前解決は送信者タブ内限定。別タブ生存の可能性があるので閉鎖確定しない。
        # list_peers 再取得 → 数値 peer id で 1 回だけ再送 → それも駄目なら消滅の裏取り。
        # (1.x 系でも安全に実行できる: 救済が効かないだけで誤閉鎖はしない)
        # 名前ではなく「控えておいた peer id」で引き直す。id が消えていれば same_tab:true の
        # レコードだけを候補にする (名前一致は他タブの別 org を掴みうる — 上記 3-a/b/c)
        peer_id=$(reresolve_peer_id --prefer-known-id "$known_peer_id" --name worker-foo --require-same-tab)
        if [ -n "$peer_id" ] && retry_send_by_numeric_id "$peer_id"; then
          : # 同タブ内で id が変わっただけ — lifecycle は動かさない。以後このワーカーへは数値 id を使う
        elif confirm_pane_gone worker-foo; then
          # list_panes 不在 (同タブ) / pane_exited 観測 / list_peers からの消失 で裏取りできた
          mark_worker_pane_closed worker-foo
        else
          # 裏取りできない = indeterminate。閉鎖に倒さず escalate
          log_journal "worker lifecycle indeterminate (no exit evidence): $out"
          escalate_secretary "worker lifecycle indeterminate: $out"
        fi
        ;;
    esac
    ;;
  *"[pane_vanished]"*)
    # resolve 成功後のレース。別タブ生存クラスではないので閉鎖扱いのまま
    mark_worker_pane_closed worker-foo
    ;;
  *"[server_too_old]"*)
    # 2.0 capability に対して server が古い。非一時障害 — 再試行もバックオフもしない
    disable_2x_feature_path   # その機能を使う経路を止め、1.x 系運用 (同一タブ) に倒す
    escalate_secretary "renga upgrade required (no auto-retry): $out"
    ;;
  *"[tab_not_found]"*)
    # tab snapshot が古い。取り直してから 1 回だけ再試行（素の再試行はしない）
    refresh_tab_snapshot && retry_once_with_fresh_tab_selector
    ;;
  *"[tab_ambiguous]"*)
    # 名前が複数タブに一致。同じ名前で再試行せず、一意な tab id に切り替える
    retry_once_with_unique_tab_id
    ;;
  *"[tab_limit_reached]"*)
    # 容量上限。同一要求の自動再試行を禁止 — capacity escalation として窓口へ
    escalate_secretary "tab capacity exhausted (no auto-retry): $out"
    ;;
  *"[target_tab_mismatch]"*)
    # 呼出側のバグ。再試行せず journal + escalate（自動復旧はタブ跨ぎ誤操作を隠す）
    log_journal "caller bug — tab selector mismatch: $out"
    escalate_secretary "target_tab_mismatch (caller bug): $out"
    ;;
  *"[last_pane]"*)
    # org-suspend 末端で最後のペインを閉じようとした
    # 強制クローズしない。当該ペインは自分自身で exit
    echo "last pane — leave for self-exit"
    ;;
  *"[shutting_down]"*)
    echo "renga halting — dispatcher stopping"
    exit 0
    ;;
  *"[io_error]"*|*"[app_timeout]"*|*"[internal]"*)
    log_journal "transient renga error: $out"
    ;;
  *"(no peers"*|*"(message dropped"*)
    # list_peers / send_message の renga 非接続時の ok-text
    log_journal "renga peer unreachable: $out"
    ;;
  *)
    log_journal "unexpected renga error: $out"
    ;;
esac
```

## なぜ code か、substring ではなく

- メッセージ本文は human-facing。理由なしで変更される可能性がある
  (e.g. "pane not found: Id(3)" → "pane 3 does not exist")
- renga 側の契約については、以下を正本として参照する (このリポジトリ内では検証不能な前提なので **外部依存** として扱うこと):
  - `renga/src/ipc/mod.rs::err_code` の doc コメント — 公開 code の一覧と ABI 安定性 (rename は deprecation window 付き) の明文
  - `renga/src/mcp_peer/mod.rs::fmt_code` — MCP 経由の `[<code>] <message>` 成形ロジック
  - renga `Response::Err { message, code }` の wire schema — `code` は `Option<String>` で、`skip_serializing_if = "Option::is_none"`
- 未知の code は必ず非致命扱いにする — 将来 renga が新 code を追加してもディスパッチャーが落ちないようにデフォルトブランチ必須

## Event stream — `poll_events` MCP

pane lifecycle (`pane_started` / `pane_exited` / `events_dropped` / `heartbeat` / forward-compat variants) は `mcp__renga-peers__poll_events` で cursor-based long-poll する:

```
mcp__renga-peers__poll_events(
  since=<前回の next_since、初回は省略>,
  timeout_ms=5000,
  types=["pane_exited", "events_dropped"]
)
```

戻り値の `events[]` は renga 面では `type` / `role` / `name` / `id` / `ts` を含む。ディスパッチャーは `role == "worker"` で絞り込んで `WORKER_PANE_EXITED` 通知する。`next_since` を次回 `since` に流用して idempotent resume。

> **per-event フィールドは backend 差がある — 突合キーを 1 つに決め打ちしない**: ratified §3.1 が固定しているのは cursor セマンティクスと `type` 語彙だけで、per-event のフィールド名を規定していない（そのため broker のように `pane_id` / `agent_id` を載せ、`role` / `name` を載せない形もありうる）。proposed amendment **T-§3.1 は canonical 名を pane 側 `id` / peer 側 `peer_id` に固定**しており、alias は追加でのみ許される。突合は **(a) pane 識別子（`list_panes` の `id` と同じ空間）** と **(b) peer 識別子（`list_peers` の `id` と同じ空間）** の**いずれか一方でも一致したら hit** とする形で書き、フィールド名は **canonical を先に**見る（契約 T-§3.1 が canonical 名を pane 側 `ev.id` / peer 側 `ev.peer_id` に固定している。例: pane 側 `ev.id` → `ev.pane_id`、peer 側 `ev.peer_id` → `ev.agent_id`）。**alias を先に読んではならない** — 両方載っている場合に backend 固有 / 古い `agent_id` が canonical 値を隠し、終了したピアが pending に残る。**`name` 一致だけを突合キーにしてはならない** — 2.0 の名前はタブ内一意でしかないので、別タブの同名ペインの `pane_exited` で自組織の生存ワーカーを誤って retire する。`name` フォールバックは「同タブと確定済みのペイン」に限って使う。契約側の規定は [`docs/contracts/backend-interface-contract.md`](../../../../docs/contracts/backend-interface-contract.md#proposed-amendment-renga-20-multi-tab-capability-conditional-cross-tab-addressing) の T-§3.1。

### type 別の扱い

| type | 扱い |
|---|---|
| `pane_started` | 現状 skip (将来必要になれば追加) |
| `pane_exited` | `role == "worker"` に絞って `WORKER_PANE_EXITED` 通知 |
| `events_dropped` | DB の events テーブルに drop 件数を記録 (監視が追いついていないシグナル) |
| `heartbeat` | 通常 `poll_events` のバッファに入らない (subscribe 内部で消化される) |

### `types` フィルタの挙動

`types` filter は cursor を全 type で advance させるので重複 scan なし。ただし **filter 不一致イベント到着で long-poll が early return** し、`events: []` + 進んだ cursor が返る (renga PR #120 参照)。Dispatcher 監視ループでは空応答時に spin せず、`next_since` を保持したまま次のサイクルで再呼び出しする。

### 初回呼び出しのセマンティクス

`since` 省略で「今以降のイベントだけ」を返す（過去の履歴を flood しない）。旧 `renga events --timeout` と同じ契約。

## Raw キー入力 — `send_keys` MCP

raw PTY キー送信は `mcp__renga-peers__send_keys` を使う。論理メッセージ配送の `send_message` とは**別物**（PTY に生バイトを書き込むので、そのペインで走っているアプリケーション側に見える）:

```
mcp__renga-peers__send_keys(
  target: string,           # pane name or id (list_panes と同じ解決規則)
  text?: string,            # 送信するテキスト（optional）
  keys?: string[],          # 特殊キー名の配列（optional、text と併用可、text の後に送られる）
  enter?: boolean           # 末尾に Enter (CR, 0x0D) を付ける（optional、keys の後に送られる）
)
```

### 対応キー語彙

- `Enter` / `Return` (CR, `\r` = 0x0D。`enter: true` と byte-identical)
- `Tab`
- `Shift+Tab` / `BackTab`
- `Esc` / `Escape`
- `Backspace`
- `Delete` / `Del`
- `Up` / `Down` / `Left` / `Right`
- `Home` / `End`
- `PageUp` / `PageDown`
- `Space`
- `Ctrl+<A-Z>`（例: `Ctrl+C`）

未知の key 名は `-32602 invalid-params` error が返る。

### 典型的な呼び出しパターン

| 用途 | 呼び出し |
|---|---|
| 空 Enter（プロンプトへの返答） | `send_keys(target="X", enter=true)` |
| "yes" + Enter（確認プロンプトへの応答など） | `send_keys(target="X", text="yes", enter=true)` |
| Shift+Tab（permission mode 切替） | `send_keys(target="X", keys=["Shift+Tab"])` |
| Esc（モーダル escape） | `send_keys(target="X", keys=["Esc"])` |
| Ctrl+C（走行中プロセス中断） | `send_keys(target="X", keys=["Ctrl+C"])` |

> **注意 — Claude Code の入力欄（❯ 行）へは text + `enter=true` の同一呼び出しを使わない**: 上表の「"yes" + Enter」型は raw CLI の確認プロンプト等に対する用法。Claude Code ペインの入力欄に対して text と `enter=true` を同一呼び出しに載せると、text が貼り付け扱いになり末尾 Enter が貼り付けに吸収されて **submit されず、未送信 draft として滞留する**。Claude Code ペインへの打鍵は (1) `send_keys(text=...)` のみ → (2) `inspect_pane` で入力欄に乗ったことを確認 → (3) 単独 `send_keys(enter=true)` の 2 段に分ける（打鍵注入手順の共有 SoT: [`.dispatcher/references/spawn-flow.md`](../../../../.dispatcher/references/spawn-flow.md) 3-5a）。また復旧打鍵の前の Escape は、対象ペインが busy だと走行中ターンを中断させるため、必ず `inspect_pane` で idle を確認してから送る。
