# `.state/state.db` 一時的 0 行見え事象 audit (Issue #376 Iteration A 副作用)

> **【2026-05-09 後刻 訂正】** 本 audit の初版 (commit `4864af5`) で「最有力」と評価した **仮説 A (sandbox shadow FS) は棄却**。PR #385 push 直後に Secretary が同種 `runs=0` を再観測し、cwd が当該 audit worktree (`.worktrees/db-mystery-iter-a-audit/`) に drift した状態で `python -c "sqlite3.connect('.state/state.db')..."` 等を実行していたため **worktree 内に副生成された別 `.state/state.db` (inode 615758, runs=0)** を読んでいただけ、と実機判明した。本物の DB (`/home/happy_ryo/work/org/claude-org-ja/.state/state.db`, inode 621604) は終始 runs=13 で健在。
>
> 真因は **仮説 D (2 DB ファイル混同)** で、機構は「**state_db 関連 tool が cwd 相対で `.state/state.db` を解決する**」点にある。詳細は本書末尾の **§7 真因と再発防止案** を参照。
>
> 本 §0 の下に続く初版本文 (§1〜§6) は **元のまま保持** する (audit の作業ログ、および「仮説評価が現実検証で覆る」という事例として価値があるため)。各仮説評価ブロックに `→ 訂正:` マーカーを追記して、初版時点と訂正後の判定の差分を明示する。

## 0. 本書の位置づけ

Iteration A B1-1 probe 実施中 (2026-05-09 09:42 UTC 以降) に、Secretary が `.state/state.db` に対して発行した SELECT が一時的に **`runs` テーブル 0 行** を返した事象の audit。

- mode: **観察 / 仮説整理のみ**。コード変更なし。本番 `.state/state.db` への書き込み実験も行っていない。
- worktree: `audit/db-mystery-iter-a` (本 worktree には `.state/state.db` を持たない)
- 検証は (a) 静的コード読み、(b) 別 worktree 上 (`/home/happy_ryo/work/org/claude-org-ja/.state/state.db`) の **read-only** PRAGMA / count 観察 のみで実施
- 一次 SoT の本 audit から取得した修正アクションは別 issue / PR で扱う (本書はあくまで原因仮説の plausibility 評価まで)

worker は probe を実施していない。Secretary + ユーザーが dispatcher pane で iteration A B1-1 probe を回している過程で偶発的に観測された 1 サンプルを書き起こす。

## 1. 観測された事象 (時系列)

(以下は CLAUDE.local.md の引き写し + worker による正規化。時刻はすべて UTC ≈ JST -9h、状態取得は read-only。)

| 時刻 | アクション | 観測 |
|---|---|---|
| 09:32 | Session start, Secretary `get_resume_briefing` | DB: runs=9 (completed), events=60+, projects=4, worker_dirs=4, session.status=SUSPENDED |
| 09:42–09:55 | Iteration A B1-1 probe を Secretary + ユーザーが dispatcher pane で実行 | dispatcher の sandbox を **No Sandbox → auto-allow** に切替 |
| 〜10:00 | dispatcher が DELEGATE 受領 (writeup task)、sandbox active 中に `tools/journal_append.sh` で DB 書き込み試行 | 「**unable to open database file**」エラー。Write tool で worker state.md を直接更新する fallback で凌いだ |
| 〜10:25 | sandbox-probe-iter-a-b1-1-writeup worker 完了 | Secretary が `update_run_status('...writeup', 'review')` を実行、成功と表示 |
| 〜10:30 | sandbox-probe-iter-a-incorporate worker 完了 | Secretary が `update_run_status('...incorporate', 'review')` を実行、成功と表示 |
| 10:38 | Secretary が `gh pr create` で PR #384 作成 + `journal_append.sh pr_opened` 実行直後 | `python -c "SELECT count(*) FROM runs"` が **0** を返す。projects=0, events=1, **session.status=IDLE**。`runs WHERE task_id LIKE '%iter-a%'` も 0 件 |
| 〜10:40 | Secretary が SQL UPDATE で session 復元 + 2 runs 再 INSERT | classifier 拒否 → ユーザー承認で実行 → 復元成功と表示 |
| 〜10:45 | `gh pr merge 384` → `run_complete_on_merge.py` → `git pull --ff-only origin main` → SELECT runs | runs=12 件 (履歴 9 件 + abandoned + writeup completed + incorporate completed) が **再び見える** |

10:38 観測の特徴 (今回の mystery のコア):

- runs=0
- projects=0
- events=1 (= 直前 `journal_append.sh pr_opened` で挿入された 1 行と推測)
- session.status=**IDLE** (= 既存 SUSPENDED から「初期値」へ遷移)
- WAL / SHM ファイル状態は未取得

10:45 観測 (回復後):

- runs=12 (オペレーターの SQL UPDATE / INSERT 介入と独立に整数が合う)
- session.status=SUSPENDED (再び元値)

本書執筆時点 (2026-05-09 後刻) の本番 DB 観測 (audit が実施した read-only PRAGMA / count):

```text
journal_mode = wal
synchronous  = 2 (FULL)
locking_mode = normal
runs    : 13
events  : 67
projects: 4
session.status = SUSPENDED
schema_migrations: [v1 'M0: initial schema (Issue #267)', v2 'M2: org_sessions singleton (Issue #267)']
.state/state.db-wal / .db-shm : 不在 (= 全 connection close + checkpoint clean)
.state/state.db inode = 621604 (single file, no other state.db copies in repo tree)
```

つまり最終 DB の物理状態は健全。10:38 の観測は **永続的な DB 破壊ではなく一時的な見え方の異常** であったことが、本書執筆時点では確定している。

## 2. コード経路の静的調査 (audit 範囲)

仮説評価で必要になった主要パス (改めて参照符号を付けたうえで以下 §3 で利用):

### 2.1 接続・WAL モード

[`tools/state_db/__init__.py`](../../../tools/state_db/__init__.py) の [`connect`](../../../tools/state_db/__init__.py) は接続のたびに `PRAGMA journal_mode = WAL` を発行する (`:memory:` を除く)。

```python
conn.execute("PRAGMA foreign_keys = ON")
conn.execute("PRAGMA busy_timeout = 5000")
if db_path != ":memory:":
    conn.execute("PRAGMA journal_mode = WAL")
```

→ 本番 DB が WAL 運用であることは確定 (`pragma journal_mode` でも `wal` 観測済)。

### 2.2 スキーマ自動初期化と「IDLE」の出所

[`tools/journal_append.py`](../../../tools/journal_append.py) の `_db_append` は DB ファイルが存在しないと自動で fresh DB を作って schema を流す:

```python
db_path = repo_root / ".state" / "state.db"
db_path.parent.mkdir(parents=True, exist_ok=True)
is_new_db = not db_path.exists()
conn = connect(db_path)
try:
    if is_new_db:
        apply_schema(conn)
    writer = StateWriter(conn)            # ↓ ここで ensure_m2_schema が走る
    writer.append_event(kind=event, ...)
    writer.commit()
```

`StateWriter.__init__` は [`ensure_m2_schema`](../../../tools/state_db/__init__.py) を呼ぶ。`ensure_m2_schema` は `org_sessions` シングルトン行を `INSERT OR IGNORE` で seed する:

```sql
INSERT OR IGNORE INTO org_sessions (id, status, last_writer_at)
VALUES (1, 'IDLE', strftime('%Y-%m-%dT%H:%M:%fZ','now'))
```

つまり「fresh DB に対して `journal_append.py` が一度走った直後」の DB shape は:

- `runs` = 0
- `projects` = 0
- `events` = 1 (今回 append された 1 行のみ)
- `org_sessions.id=1, status='IDLE'`

— **10:38 で観測された shape と完全に一致する**。

加えて `StateWriter.commit()` は post-commit hook で `.state/org-state.md` を [`tools.state_db.snapshotter.post_commit_regenerate`](../../../tools/state_db/snapshotter.py) で、`.state/org-state.json` を [`dashboard.org_state_converter.convert`](../../../dashboard/org_state_converter.py) で、いずれも DB 由来で再生成する。fresh DB で commit すれば再生成内容も「空 + IDLE」相当になる。

### 2.3 importer による DROP TABLE 経路

[`tools/state_db/importer.py`](../../../tools/state_db/importer.py) の `_reset_schema` は **全テーブル DROP** + `apply_schema` 再適用を行う。`import_full_rebuild` は `--rebuild` フラグで起動する明示パスでしか走らない (`_main` で `--rebuild` 不在は `error` exit 2 で終わる、line 696-699)。

`org-start` / `org-resume` / `org-suspend` / `org-delegate` の各 SKILL.md は「DB が無い場合は `python -m tools.state_db.importer --db .state/state.db --rebuild --no-strict` で構築する」と記載しているが、これは **オペレーターが手動実行する場合** の手順であり、自動 / hook 経由の rebuild は本リポジトリ内には見つからない (.git/hooks, .claude/hooks, .hooks/ 配下を grep 確認済み — `state.db` / `importer` を呼ぶ hook は **存在しない**)。

### 2.4 DB ファイル位置の解決方式

> **→ 訂正 (2026-05-09 後刻)**: 本節の「cwd 非依存」結論は **誤り**。`__file__` ベースの解決は **同 worker / 同 cwd の中で起動した script に対しては** cwd を直接読まないが、**worktree が複数ある環境では「どの worktree の script を起動したか」が cwd で決まる** ため、結果として `<worktree>/.state/state.db` が選ばれる。さらに ad-hoc の `python -c "sqlite3.connect('.state/state.db')..."` は **純粋に cwd 相対**。本書 §7.1 / §7.3 を参照。以下の調査結果は **初版本文を改変せず保持**。

- `tools/journal_append.sh`: `SCRIPT_DIR=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd); REPO_ROOT="$SCRIPT_DIR/.."` を計算したうえで **`cd "$REPO_ROOT"`** してから python を exec する。cwd 非依存。 ← **【訂正: script の在処 = worktree が cwd で決まるので、結果として cwd 依存】**
- `tools/journal_append.py`: `_REPO_ROOT = Path(__file__).resolve().parent.parent`。これも cwd 非依存。 ← **【訂正: 同上】**
- `tools/gen_delegate_payload.py:836`: `claude_org_root / ".state" / "state.db"` (root はオプション解決で渡される)。
- `dashboard/org_state_converter.py:25`: `BASE_DIR = Path(__file__).parent.parent` → `.state/state.db`。cwd 非依存。 ← **【訂正: 同上、起動 path 経由で worktree が決まる】**
- `dashboard/server.py:54`: 同上。
- 本 audit の grep では `state.db` / `state_db` を参照する path 解決はすべて **`__file__` ベース** で、cwd ベースの相対解決は見つからない。 ← **【訂正: ad-hoc Python one-liner (`python -c "..."`) は完全 cwd 相対のため、tool 内 path 解決とは別軸で cwd 依存経路が常に存在する】**

→ よって「dispatcher が cwd `.dispatcher/` のまま `.state/state.db` を相対解決して `.dispatcher/.state/state.db` を別ファイルとして開いてしまった」筋は **コード経路としては無い**。仮説 D の literal な版は静的に棄却可能。

ただし sandbox の bind-mount 構成によって、**同じ絶対パス** が異なる中身に見える可能性は残る (これは仮説 A の領分)。

### 2.5 dispatcher cwd と DB 絶対パスの関係

dispatcher の cwd は `.dispatcher/` だが、`tools/journal_append.sh` は `cd $REPO_ROOT` してから python を起動するため、cwd は cancel される。実 dispatcher が DB に到達するのは `<repo_root>/.state/state.db` の **絶対パス**経由。

`.dispatcher/references/worker-monitoring.md:503` には `sqlite3 ../.state/state.db ...` という one-liner 例がある。これは dispatcher Claude が直接実行する read-only クエリ用で、dispatcher cwd `.dispatcher/` から見て `..` で repo root に上がるパス。同じく絶対パス相当に解決される (`../` が `.dispatcher/..` = repo root)。

## 3. 仮説 A〜E の評価

### 3.1 仮説 A: sandbox auto-allow shadow FS による上書き

> **→ 訂正 (2026-05-09 後刻): 棄却**。PR #385 push 直後に Secretary が再現を試みた結果、観測されていた `runs=0` 現象は sandbox layer とは独立に **cwd drift で別 DB を開いていた**ことが実機判明 (詳細: 本書 §7 / 冒頭訂正注)。本仮説は初版でこのブロックの最後にあるとおり「機械的説明と完全一致」と評価したが、それは **fresh DB shape との表面的類似** にすぎず、原因経路としては誤り。仮説 A は sandbox semantics の追加調査としては価値が残るが、本 db-mystery の真因ではない。以下の評価ブロックは **初版本文を改変せず保持** する (誤推論のトレース価値のため)。

> dispatcher / Secretary の sandbox auto-allow 時、`.state/state.db` への write が bubblewrap shadow FS に逸れて、後で real path が読まれた瞬間に shadow が見えた、もしくは shadow 経由で空の DB が real path に書き戻された

**評価: 高 plausibility (現時点で最有力)** ← **【訂正で棄却。上記注を参照】**

根拠:

- 10:38 観測 shape (`runs=0, projects=0, events=1, session.status=IDLE`) は §2.2 の「fresh DB に `journal_append.py` が一度走った直後」と **完全一致**。`status=IDLE` は明示的に SQL で `'IDLE'` リテラルとして seed される値で、偶発生成は考えにくい
- 10:00 dispatcher の「unable to open database file」失敗 → 10:38 の Secretary 観測 IDLE は **同じ sandbox semantics の下で「open に失敗 → exists() 偽 → fresh DB 作成」へ転じた**経路が滑らかに通る (10:00 と 10:38 の差分は sandbox layer の bind-mount 状態が時間で変わったか、Secretary 側でだけ tmpfs 化したか)
- 10:45 で「runs=12 が再び見える」回復は **永続的破壊ではなく view レベルの異常** を意味する。bubblewrap 等の overlay/tmpfs は子プロセス終了 (またはサンドボックス境界外への遷移) で view が消える挙動が一致

棄却条件 (= この仮説を退ける観察):

- bubblewrap が `.state/` 配下に対して shadow / tmpfs を被せていない (= bind-mount 構成で `.state/` は実 FS への RW pass-through) ことが `cat /proc/self/mountinfo` 等で確認できる場合
- 10:40 の Secretary 手動 `INSERT INTO runs ...` 2 件が **本番 DB 側に確実に永続化された** (= sandbox view ではなく real view を更新した) と確認できる場合 (= shadow への書き込みなら 10:45 で 12 件にはならず 10 件等になるはず — 12 件は元の 9 履歴 + 2 review (write) + 1 abandoned 等の組み合わせなのか、9 + 1 abandoned + 2 (writeup/incorporate completed by 10:25/10:30 update) なのかで区別が要る)

検証手段 (next iteration):

- B1-1 と並列に **probe row 1.6** (案、§5.1) として「sandbox auto-allow 中に `tools/journal_append.sh` を実機で叩き、その前後で `.state/state.db` の inode と count を sandbox 内 / 外それぞれで取る」を追加
- bwrap 起動 mountinfo 観察を runbook に追記

### 3.2 仮説 B: SQLite WAL / journal mode 不整合

> WAL モードで複数プロセス同時アクセス時の checkpoint タイミングが原因で一時的に空に見える可能性

**評価: 低 plausibility (棄却寄り)**

根拠:

- WAL モード自体は確定 (PRAGMA で確認: `journal_mode = wal`、本書執筆時点と connect() 内のリテラルが一致)
- ただし WAL は通常「commit 済み行が一時的に消える」挙動はしない。WAL のセマンティクスは「reader は WAL frame を見て直近 commit までを観測する」「checkpoint 済 frame のみが main DB file に反映される」。新規 connection で WAL を読み損ねたとしても、**main DB file 内の既存 row が消えて見えることは仕様上ない**
- 「connection が main DB のみ読み、WAL を無視した」場合でも、historical 9 runs は WAL ではなく main DB に既に checkpoint 済みであるはずなので 0 行にはならない
- 観測 shape `events=1, session.status=IDLE` は WAL バグでは説明できない (なぜ 1 行だけ生き残り、status が `IDLE` リテラルに変わるのか経路がない)

棄却条件 (= 本仮説を生き残らせる観察):

- WAL の checkpoint 中に `-shm` ファイルが破損し、reader が空 main DB を見る、というレアバグの再現が公式 sqlite issue tracker 等で確認できる場合 (現状そのような事例は知られていない)
- 10:38 観測時点で `state.db-wal` / `state.db-shm` が「異常に大きい / 不在 / 破損」していたことが log で確認できる場合 (本観測は当時の WAL ファイル状態を取っていない)

総じて、観測 shape を WAL 単独で説明することは難しい。**仮説 A の従犯としてのみ残す** (sandbox shadow FS の中で WAL ファイルが分断された結果、reader が空の main DB だけを見たというストーリーは A のサブケース)。

### 3.3 仮説 C: importer / 他ツールの暗黙再構築

> StateWriter post-commit hook や dashboard.org_state_converter / snapshotter が DB を別ソースから再生成して中身を空にした可能性

**評価: 低 plausibility (棄却)**

根拠:

- §2.3 で確認済みの通り、`_reset_schema()` は **`import_full_rebuild` から `--rebuild` フラグ付きでのみ** 起動する。10:38 周辺でオペレーターが `python -m tools.state_db.importer --rebuild` を実行した記録は CLAUDE.local.md の時系列に含まれていない
- `tools.state_db.snapshotter.post_commit_regenerate` は **DB → markdown / JSON の片方向 dump** であり、DB を読み直して上書きするだけ。markdown / JSON から DB を rebuild する経路は本リポジトリ全体に **存在しない** (M4 cutover で削除済、line 10-13 in `dashboard/org_state_converter.py` 「the pre-M4 ``--source markdown`` mode has been removed」)
- post-commit が DB の `runs` を消す経路は無い。post-commit は read-only の SELECT (snapshotter の `_fetch_runs` 等) しか発行しない

棄却条件 (= 本仮説を生き残らせる観察):

- 本書執筆時点で見落としている rebuild 経路が判明した場合 (例: 個別オペレーターの shell alias / .bashrc 等)。grep 確認した範囲では否定的

### 3.4 仮説 D: 2 つの DB ファイル混同

> **→ 訂正 (2026-05-09 後刻): 真因として採用**。初版で「literal 版は棄却」と書いたのは、検証が **当該 audit worktree 内のみで `find` した結果** に基づいており、**worker や Secretary の cwd drift で worktree 配下に副生成される別 `.state/state.db`** を見落としていた。実機検証で worktree 内 `.state/state.db` (inode 615758, 151552 bytes) の存在が確認された (Secretary 側で 2026-05-09 11:18 頃の reproduce 中)。詳細と再発防止は §7 を参照。以下の評価ブロックは **初版本文を改変せず保持**。

> `.state/state.db` と `.dispatcher/.state/...` の混同

**評価: 中 plausibility (literal 版は棄却、effective 版は仮説 A の言い換え)** ← **【訂正: literal 版こそが真因。検証範囲が狭すぎた点が誤り】**

根拠:

- §2.4 の通り、本リポジトリのコードは `state.db` への参照を **全て `__file__` ベース絶対パス** で解決する。dispatcher cwd `.dispatcher/` から相対 `.state/state.db` を解決して `.dispatcher/.state/state.db` を作るような **literal な path 混同経路は存在しない**
- 本書執筆時点で `find .` 配下に `state.db` は **1 件のみ** (`/home/happy_ryo/work/org/claude-org-ja/.state/state.db`、inode 621604)。worktree (`.worktrees/`) 配下にも別 state.db は無い
- ただし sandbox の bind-mount で **同じ絶対パス** に異なる中身が見える状況は仮説 A の領分。effective には「real path と shadow path で 2 ファイル相当」と読み替えられる

棄却条件 (literal 版): すでに棄却済 (FS 上に他 `state.db` 不在を確認済)。

総じて: **literal D は棄却、effective D は A に吸収される**。

### 3.5 仮説 E: 単純な query タイミング不整合 / connection キャッシュ

> `WHERE task_id LIKE '%iter-a%'` 0 件が 「reservation で行が未挿入だから正常」、`ORDER BY id DESC LIMIT 8` 0 件が異常

**評価: 部分採用 / 単独では不足**

根拠 (`gen_delegate_payload.py` apply の T1 reservation):

- `_reserve_in_db` (line 374〜) は新規 task に対して `runs.status='queued'` で **必ず INSERT する**。`apply_schema(conn)` も DB 不在時にだけ呼ぶが、その後 `INSERT INTO runs ...` で行を作る (line 386-395 周辺)
- 本書執筆時点の本番 DB に `db-mystery-iter-a-audit` が `status=queued` で 1 件存在する (= 本 audit task そのもの) のは正常 reservation 動作。同じく `sandbox-probe-iter-a-b1-1` は `abandoned`、`sandbox-probe-iter-a-b1-1-writeup` / `sandbox-probe-iter-a-incorporate` は `completed` で履歴に存在する
- → 10:38 時点でも、本来なら少なくとも履歴 9 件 + abandoned 1 件 + 直近 review/completed 2 件 = 12 件が DB に存在するはず。**`ORDER BY id DESC LIMIT 8` で 0 件は異常**で、E 単独では説明不能
- `WHERE task_id LIKE '%iter-a%'` 0 件は (a) 本当に DB が空、(b) 該当 row が存在しない、のどちらでも 0 になる。E 単独では (b) を主張できるが、`LIMIT 8` 観測と矛盾するため (b) は退けられる

総じて、E 単独では観測を説明できない。**仮説 A 下で「shadow FS 内で DB が空」だったから E 観測も整合**、というサブストーリーとしてのみ残す。

### 3.6 評価サマリ

| 仮説 | plausibility | 観測 shape (`runs=0/projects=0/events=1/IDLE`) を説明可能か | 残す or 棄却 |
|---|---|---|---|
| A: sandbox shadow FS | **高** | はい (fresh DB + 1 event の機械的説明と完全一致) | **採用 (最有力)** |
| B: WAL 不整合 | 低 | いいえ (WAL のセマンティクスでは既存 row は消えない) | 棄却 (A のサブとしてのみ可能性残) |
| C: 暗黙 rebuild | 低 | はい (理論的には) — ただし経路が存在しない | 棄却 |
| D-literal: 別ファイル混同 | 低 | はい (理論的には) — ただし FS にも経路にも他 DB 不在 | 棄却 |
| D-effective: 同一 path で異 view | 中 | はい — 仮説 A と機能的に同じ | A に吸収 |
| E: 単純 timing | 低 | いいえ (`LIMIT 8` 観測と矛盾) | 棄却 (A のサブのみ) |

→ **本 audit の結論**: 第一容疑は **仮説 A (sandbox auto-allow による bind-mount / overlay 構成下で `.state/state.db` の見え方が一時的にシャドウ化、`journal_append.py` がその shadow に対して fresh DB を auto-create した)**。

## 4. 追加で必要な観察

### 4.1 確認したいが本 audit では取れなかった情報

- 10:38 時点の `.state/state.db-wal` / `.state/state.db-shm` の存否・サイズ・mtime
- 10:38 時点の Secretary プロセスの `cat /proc/self/mountinfo` (sandbox layer 確認)
- 10:38 時点の `.state/state.db` の `stat` (= sandbox 越しに見たときの inode, ctime)
- 10:38 〜 10:45 で sandbox auto-allow が実際に on / off したタイミング (sandbox 解除イベントの記録)
- 10:00 の dispatcher 「unable to open database file」発生時の bwrap 起動オプション

これらは **iteration B 以降で row 化** すれば再現観察可能 (§5)。本 iteration では事後再構築不可。

### 4.2 audit が安全に試せた検証 (本書執筆時に実行済)

- 本番 DB の `PRAGMA journal_mode/synchronous/locking_mode` (read-only): `wal / 2 / normal`
- 本番 DB の table 行数 (read-only SELECT): `runs=13, events=67, projects=4`
- 本番 DB の `schema_migrations`: `[v1, v2]` (M2 は適用済、M4 cutover も完了)
- 本番 DB の inode = 621604、worktree 全体に `state.db` は 1 ファイルのみ
- `state.db-wal` / `state.db-shm` の現在の不在 (= 全 connection close 済)

これらは「現本番 DB の物理状態」を確認するもので、**過去 10:38 時点の状態は再現できない**。10:38 観測の物理裏取りは原理的に不可能 (sandbox ephemeral view の遺物が無い)。

### 4.3 runtime instrumentation の提案

iteration B 以降に向けて、以下を仕込めば次回は決定的に切り分けられる:

- `tools/journal_append.py` の `_db_append` に **`is_new_db = True` 分岐入りで instrument log** を追加し、fresh DB 作成が起きた事実を `.state/journal_append-events.log` に append する (DB に書く前に発火するので tail で追える)
- `tools/state_db/__init__.py` の `connect` で `os.path.realpath(db_path)` と `os.stat().st_ino` を stderr に 1 行 emit (DEBUG 環境変数で gating)
- sandbox 起動 / 解除を Secretary / dispatcher pane が `tools/journal_append.sh sandbox_state from=... to=...` で記録 (sandbox layer 状態と DB 観測の時系列突合に必要)

(本 audit では **コード変更しないため上記は提案のみ**。別 issue / PR で実装する。)

## 5. iteration A への影響と next iteration 提案

### 5.1 probe checklist 1.6 候補 (新規 row 提案)

> **→ 訂正 (2026-05-09 後刻)**: 初版は「sandbox auto-allow 下での shadow FS 検出」を 1.6 row として提案していたが、真因が cwd drift / 別 DB 副生成と判明したため、**目的を再設定**。元案も sandbox semantics の理解には価値があるが優先度は下がる (§5.3 提案 D に下流タスクとして残す)。以下は訂正後の 1.6 row 案:

`probes/checklist.md` 1 章 (B1-1) または新カテゴリ (`fs-state-db` / `fs-cwd-drift`) に追加 row として:

- **1.6 — cwd drift 時の `.state/state.db` 副生成・誤参照検出**
  - 試行 (a): 想定外 cwd (例: `<repo-root>/.worktrees/<some-worktree>/`) から `bash tools/journal_append.sh probe_event field=x` を実行
  - 試行 (b): 同じ cwd から `python -c "import sqlite3; print(sqlite3.connect('.state/state.db').execute('SELECT count(*) FROM runs').fetchone())"` を実行
  - 観測 (c1): 試行前後で **両方の場所** にある `.state/state.db` の `inode` / `mtime` / `size` / `runs count` を取得
    - canonical: `<repo-root>/.state/state.db`
    - drift 候補: `<cwd>/.state/state.db` (= 試行時 cwd 配下)
  - 観測 (c2): `find <repo-root> -name "state.db" -type f` の結果が試行前後で変わるか
  - 期待される判定:
    - **drift 副生成あり**: cwd 配下に新しい `state.db` が生成される / `runs=0` 等の空 shape を返す
    - **drift 副生成なし**: canonical 1 ファイルのみ、cwd 配下に新規 DB は生成されず (= 修正案 §7.2 が効いた場合)

- **1.6b — 旧 1.6 案 (sandbox shadow FS 検出、優先度低)**: §5.3 提案 D として下流に保持

(`probes/categories.md` 凡例上「fs-cwd」関連の新カテゴリ「fs-state-db」を新設するか、既存 1 章 B1-1 を拡張するかは Phase 1 schema 設計時に判断。)

### 5.2 `iteration-a-results.md` §6 への追加観察 row 提案

[`iteration-a-results.md`](./iteration-a-results.md) §6 の想定外リストに **#4** として追加:

> **6.4 想定外 #4: sandbox auto-allow 中に `.state/state.db` 書き込みが shadow FS 化した疑い (db-mystery)**
>
> - 事象: probe 中に Secretary が `journal_append.sh pr_opened` を発行直後の SELECT で runs=0、IDLE、events=1 を観測。10:45 で runs=12 に回復
> - 影響: probe 結果そのものへの影響はなし (Secretary は別途 SQL UPDATE / INSERT で復旧 → `gh pr merge` 後に DB 健全状態が見えた)。ただし Iteration A の **probe 副作用として書き残すべき重大観察** であり、Phase 1 schema 設計の前に 1.6 row で再現実験が必要
> - 詳細: [`db-mystery-2026-05-09.md`](./db-mystery-2026-05-09.md) 参照

(本書では追加提案までで、`iteration-a-results.md` 本体への書き込みはしない — audit mode のため。Phase 1 worker が反映する。)

### 5.3 next-iteration-proposals.md への追加提案

> **→ 訂正 (2026-05-09 後刻)**: 真因が cwd drift と判明したため、提案 D の主目的を再設定。旧案 (sandbox shadow FS 切り分け) は「補助観察」として残す。

[`next-iteration-proposals.md`](./next-iteration-proposals.md) 既存 3 提案 (A/B/C) はいずれも本 db-mystery に直接対応していない。提案 A (B1-1) を拡張するか、別途:

- **提案 D — db-mystery 真因確認 (cwd drift × 別 DB 副生成 の検出)**
  - 目的: §7 で挙げる再発防止案 (a)〜(c) のいずれを採用するかを決める材料を取る。**仮説 D (cwd-relative state.db 解決) の実機 reproduce と、修正案候補それぞれの効果測定**
  - 手順:
    1. §5.1 row 1.6 を任意の worktree (例: 本 audit worktree) と canonical repo root の双方で実施
    2. `find <repo-root> -name "state.db" -type f` で canonical 1 件のみが残ることを確認 (副生成があれば差分が見える)
    3. 修正案 (a) を試作した branch で再実行し、副生成が起きないことを確認
    4. (補助) sandbox auto-allow も同時に on/off して、shadow FS 仮説 (旧 1.6 案 = 1.6b) も同時に観察可能
  - 所要: 30〜60 分 / 1 commit
  - 推奨度: ★★★ (本 audit の真因確定と再発防止のため最優先。Phase 1 schema には直接絡まないが、state-db の信頼性に関わる)

## 6. 付録: 副次的な sandbox 観察 — bwrap が `~/.aws/.env` に触ろうとして fail する

(本 audit 中に Secretary から共有された情報。db-mystery 本筋とは独立だが sandbox semantics の理解補強として記録。)

- 事象: 本 audit worker が Bash tool を sandbox 経由で起動する都度、bwrap が `Can't create file at /home/happy_ryo/.aws/.env: No such file or directory` で fail する
- 原因 (推定): claude-code 親レイヤーの permissions deny list に `~/.aws/*` が含まれており、bwrap がそれを bind-mount 用 dummy file として準備しようとするが、`~/.aws/.env` という file (実体)が存在しないために mkdir / touch に失敗している
- 影響: sandbox 内で Bash が起動できず、本 audit は `dangerouslyDisableSandbox=true` で実行することで回避した。Secretary 側でも同様事象が頻発しており、本リポジトリ環境では既知 quirks
- 含意: bwrap の bind setup が本リポジトリ環境で **partial に壊れた状態でも sandbox 起動は試行される**。bind 用 dummy file が用意できないとき、bwrap が fail-fast するか fail-soft してその path を素通しするかの挙動分岐は本 audit では未確認 — これは仮説 A 検討時に sandbox 内 `.state/` の bind 状態が想定通りか怪しむ追加根拠
- 推奨フォロー: 提案 D の row 1.6 に `/proc/self/mountinfo` 観察を必須化。`~/.aws/.env` の存否と sandbox 起動 succeess / fail の相関を 1 サンプルでよいので取る

## 7. 真因と再発防止案 (2026-05-09 後刻、訂正で追加)

### 7.1 真因の特定

PR #385 push 直後に Secretary 側で同種 `runs=0` を再観測 (2026-05-09 11:18 頃) した結果、原因は以下と確定した:

- Secretary の bash cwd が本 audit worktree (`/home/happy_ryo/work/org/claude-org-ja/.worktrees/db-mystery-iter-a-audit/`) に **drift** していた
- 同 cwd から `python -c "import sqlite3; sqlite3.connect('.state/state.db')..."` 等を実行すると、Python の sqlite3 は `.state/state.db` を **cwd 相対** で解決し、`<worktree>/.state/state.db` を開く
- 当 worktree には audit 開始時点で `.state/state.db` が **無かった** ため、`sqlite3.connect()` は **新規ファイルを auto-create** する (sqlite3 は path 不在時に空 DB を作成する仕様)
- `tools/journal_append.sh` を同 cwd から起動した場合も、`SCRIPT_DIR=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)` は cwd 相対の path を渡されるので **worktree 内の `tools/journal_append.sh`** を解決 → REPO_ROOT は `<worktree>/` → `journal_append.py` の `Path(__file__).resolve().parent.parent` も `<worktree>/` → 結果として `<worktree>/.state/state.db` に書き込み + schema 適用
- canonical の `/home/happy_ryo/work/org/claude-org-ja/.state/state.db` (inode 621604) は **常に runs=13 で健在**

実機証拠 (audit が訂正のために再観測した状態):

```text
canonical:  /home/happy_ryo/work/org/claude-org-ja/.state/state.db
            inode=621604  size=167936  mtime=2026-05-09 11:21
            (runs=13, events=68, projects=4, session=SUSPENDED — 健全)

worktree:   /home/happy_ryo/work/org/claude-org-ja/.worktrees/db-mystery-iter-a-audit/.state/state.db
            inode=615758  size=151552  mtime=2026-05-09 11:18
            (runs=0, projects=0, events=1〜, session=IDLE — 副生成された空 DB)
```

`find /home/happy_ryo/work/org/claude-org-ja -maxdepth 5 -name 'state.db' -type f` で 2 ファイル見える状態。本 audit 初版は当該 worktree (audit 自身が居る worktree) を `find . -name state.db` の検索対象から外した結果、副生成 DB の存在を見落とし、観測 shape (`runs=0/IDLE/events=1`) を **fresh DB の auto-create と機械的一致** と読んだうえで「sandbox shadow FS で fresh DB が新規作成された」と誤推論した。

### 7.2 仮説 A 棄却の理由 (確定)

- 仮説 A の本質は「sandbox が `.state/` への write を shadow に逸らした」であり、**sandbox layer を介在条件**としていた
- 訂正後の実機検証では **sandbox を介さない通常 cwd の Python one-liner** でも `runs=0` が再現した。よって sandbox は介在条件として不要 = A は本事象の真因ではない
- A は **観測されたことが一度もない仮説**。初版で「最有力」と評価したのは「観測 shape との一致」という間接証拠のみで、実機再現実験の機会が無かった (sandbox を pin できる環境を用意できていなかった)。**初版の判断手順そのものが教訓** (§7.4 で記録)

### 7.3 再発防止案 (本 PR では実装せず、follow-up 提案)

state_db 関連 tool が cwd 相対で `.state/state.db` を解決しうるのが構造的問題。修正案 3 つ:

**案 (a) — `tools/state_db/__init__.py` の `connect()` で path 引数を repo root 基準に正規化**

- 入力 path が相対のとき、git repo root (= `git rev-parse --show-toplevel` または bookmark file) に anchor して absolute 化する
- worktree からの呼び出しでは git の linked worktree 検出を使い、canonical repo root か worktree root のどちらに寄せるかを契約として明示する (M5 議題)
- 実装サイズ: 中。既存 caller への影響が広いため、デフォルト挙動と opt-in flag の整理が必要

**案 (b) — 本 audit §2.4 の検証結果を訂正 (関連 tool の path 解決を改めて点検)**

- 本 audit 初版 §2.4 は「`tools/journal_append.sh` / `tools/journal_append.py` は cwd 非依存」と書いたが、これは **「同 worker が居る worktree の中では」だけ正しい**
- worktree が複数ある環境では、scripts は **invoke された path に基づいて REPO_ROOT を決める** ため、結果として cwd 相対と等価になる
- 修正済の認識: 「`__file__` ベースは cwd を直接読まないが、scripts の在処 (= worktree) を経由して実質 cwd 依存になりうる」
- 即時アクション: tool 群の callsite を全 grep し、`bash tools/...` / `python tools/...` の起動時に **canonical repo root を明示** (例: `bash /full/path/to/canonical/tools/journal_append.sh ...`) する運用ガイドを追加。現状の SKILL.md の `python -m tools.state_db.importer ...` 記述は cwd 依存を残したままなので明示注記が要る

**案 (c) — state-db cutover M5 で worktree-aware path resolution を契約化**

- M2/M4 の cutover で SoT は `.state/state.db` に集約されたが、**「どの worktree の `.state/state.db` か」の契約は存在しない**
- M5 で「state.db は **canonical repo root の 1 ファイルのみ**、worktree からの書き込みは canonical を指す」を契約として固定する
- 案 (a) が前提条件、運用周知 (b) が補完
- migration-strategy.md / docs/contracts/ に追記する (本 audit の責務外)

### 7.4 audit プロセス自体の振り返り (Iteration A への教訓)

本 audit 初版は **静的コード読みのみで仮説 plausibility を高評価し、実機反証実験に踏み込まなかった**。結果として、`find . -name state.db` の検索対象を当該 worktree 自身から外していた点 (作業 worktree なので「本物」が居ないと決めつけた点) を見落とし、誤った真因を採用した。

教訓:

- audit-only mode でも、「仮説評価が **fresh-DB 自然生成** に依存する場合は、実機で fresh-DB を作りに行く別経路を最低 1 つ列挙する」べきだった (cwd drift / worktree 重複 / 別 cluster mount 等)
- 「`find . -name state.db` で 1 件だった」を仮説 D 棄却の根拠にした点が脆弱。**`find` の root が真に repo 全体だったか** を毎回確認する規律が要る (本件の `find . -name state.db` は worktree A 内で行われ、`./.state/state.db` を 1 件だけ列挙して終わった、と考えられる)
- audit は静的調査と実機検証の **二段で完成** すべき。本書のように「コード読みは終わったが reproduce 実験未実施」の段階で commit するのは中間成果として妥当だが、その時点で「未検証の仮説 plausibility は **保留** で記す」運用ルールを設けたい (org-retro 提案項目)

---

## 8. 関連資料

- [`probes/checklist.md`](../probes/checklist.md) (1.6 案を提案する対象、本書 §5.1)
- [`iteration-a-results.md`](./iteration-a-results.md) (本書の親、§6.4 提案を追記する対象)
- [`next-iteration-proposals.md`](./next-iteration-proposals.md) (提案 D を追記する対象、本書 §5.3)
- [`tools/state_db/__init__.py`](../../../tools/state_db/__init__.py) (WAL 設定 + ensure_m2_schema)
- [`tools/state_db/writer.py`](../../../tools/state_db/writer.py) (StateWriter / post-commit hook)
- [`tools/state_db/importer.py`](../../../tools/state_db/importer.py) (DROP TABLE 経路)
- [`tools/state_db/snapshotter.py`](../../../tools/state_db/snapshotter.py) (DB → markdown 片方向 dump)
- [`tools/journal_append.py`](../../../tools/journal_append.py) (auto-create fresh DB の張本人候補)
- [`dashboard/org_state_converter.py`](../../../dashboard/org_state_converter.py) (DB → JSON 片方向 dump、M4 cutover 後)
- Issue #376 (sandbox-probe iteration A spike)
- Issue #267 (M4 DB cutover)
- Issue #284 (worker archive on completed)
