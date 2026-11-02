# #1226 ディスパッチャー→窓口 通知の送信ゲート（設計）

## 方針
送る側で止める。判定は `tools/notify_gate.py`（決定的コード）。手順書は `python3 ../tools/notify_gate.py --kind K ...` の JSON `{"send": bool, "reason": str}` で分岐する。
止めたときも events に記録は残す（fail loud は弱めない）。監視ループに待ちは足さない（ツールは同期・ファイル読みだけ）。

## 棚卸し（ディスパッチャー→窓口。出典は .dispatcher/references/*.md.in の send_message(to_id="secretary")）
| 通知 | 窓口が対応すべき条件 | 判定 |
|---|---|---|
| EVENT_CURSOR_RESET | 背景タブ配置（placement=background_tab）のワーカーが 1 つ以上いる。窓口の reconcile 対象は背景ワーカーだけ | **ゲート新設** `event_cursor_reset` |
| 終了済み curator の消失報告（curate-monitoring Step 5.3 (c)、`curate_result` 非 null で close だけ未確認） | curate_result が null（CURATE_* 未受領のまま消えた = クラッシュ疑い）。done/skipped を受領済みなら窓口がやることは無い | **ゲート新設** `curator_vanished` |
| OBSERVATION_UNAVAILABLE | 監視手段が劣化（失敗報告） | 現状維持（対象外） |
| WORKER_PANE_EXITED | 窓口が退役処理 | 現状維持 |
| STALL_SUSPECTED / ERROR_DETECTED / APPROVAL_BLOCKED / PANE_OUTPUT_WITHOUT_PEER_MSG | 異常・承認待ち。再通知間隔は #759 | 現状維持（対象外） |
| SECRETARY_RELAY_GAP_SUSPECTED / RELAY_SCAN_STALE / CURATOR_CLOSE_UNRESOLVED / CURATE_ERROR 転送 | 取りこぼし・失敗報告 | 現状維持（対象外） |
| relay 項目（CI_COMPLETED / PR_MERGED の [relay] 等） | 見逃しゼロの主保証。直 push と二重でも止めない | 現状維持（対象外） |
| RETRO_RECORDED | 知見増加の報告。窓口の行動は無い（curator 起動はディスパッチャー側の閾値チェックの責務） | **ゲート新設** `retro_recorded`（常に送らない・events に delivery=suppressed reason=info_only） |

ゲートは「届いても窓口がやることの無い通知」のうち、条件が機械的に決まる 2 種だけ。他は条件が人/窓口の判断に依るか失敗報告なので変えない。

## tools/notify_gate.py
- `decide_event_cursor_reset(idle_state)`: idle_state は `.state/dispatcher/worker-idle-state.json`（key=worker-{task_id}, value=record）。
  - ファイル不在 → 記録なし → send=False(no_background_worker)。
  - いずれかの record の `placement == "background_tab"` → send=True。
  - placement が `same_tab` / 欠損 → 背景ではない。未知の値 → 安全側 send=True(unknown_placement)。
  - JSON 破損・dict でない → send=True(unreadable_state)（判定不能は送る）。
- `decide_curator_vanished(curate_result)`: `done`/`skipped` → send=False(result_already_received)。`null`/`error`/未知 → send=True。
- CLI: `--kind`、`--idle-state PATH` / `--curate-result V`。stdout に 1 行 JSON。ツール失敗・出力不正は手順書側で「送る」に倒す。
- 判定は読み取りだけ。journal 書き込みは手順書の既存 journal_append を使う。

## 送らなかったときの記録（未達の誤認防止）
EVENT_CURSOR_RESET の未達判定（Step 1 (ii) / cursor-reset 手順 6）は「anomaly_observed があり、同じ (session_key, discarded_session_key) の notify_sent が無い」。
送らなかった場合は手順 5 で `notify_sent ... delivery=suppressed reason=no_background_worker` を書く（新 event 名は導入しない）。notify_sent 行が在るので (ii) は未達と読まず、手順 3 の de-dup も同じ行で効く。送った場合は `delivery=sent`（旧行は欠損＝sent）。送信失敗時は従来どおり notify_sent を書かない → 再送される。
curator_vanished も `notify_sent kind=curator_vanished delivery=suppressed` を残す。

## 実走確認
ワーカーは本番を操作できないので、再現手順をコマンド付きで完了報告に載せ、窓口が実行する。
- 送らない側: 背景ワーカー 0 で cursor 破棄を起こす → 窓口に届かず、events に delivery=suppressed が残る。
- 送る側: 背景ワーカー 1 件ありで同じ → 届く + delivery=sent。
- curator_vanished も send/suppress 両方。

## 却下した案
- 窓口側で黙殺: 応答 1 ターンが要るので消えない。
- suppressed 用の新 event 名: 「新しい event 名は導入しない」方針と、(ii) の突き合わせ変更が増えるため却下。
