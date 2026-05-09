# `.state/state.db` 一時的 0 行見え事象 audit (Issue #376 Iteration A 副作用)

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

- `tools/journal_append.sh`: `SCRIPT_DIR=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd); REPO_ROOT="$SCRIPT_DIR/.."` を計算したうえで **`cd "$REPO_ROOT"`** してから python を exec する。cwd 非依存。
- `tools/journal_append.py`: `_REPO_ROOT = Path(__file__).resolve().parent.parent`。これも cwd 非依存。
- `tools/gen_delegate_payload.py:836`: `claude_org_root / ".state" / "state.db"` (root はオプション解決で渡される)。
- `dashboard/org_state_converter.py:25`: `BASE_DIR = Path(__file__).parent.parent` → `.state/state.db`。cwd 非依存。
- `dashboard/server.py:54`: 同上。
- 本 audit の grep では `state.db` / `state_db` を参照する path 解決はすべて **`__file__` ベース** で、cwd ベースの相対解決は見つからない。

→ よって「dispatcher が cwd `.dispatcher/` のまま `.state/state.db` を相対解決して `.dispatcher/.state/state.db` を別ファイルとして開いてしまった」筋は **コード経路としては無い**。仮説 D の literal な版は静的に棄却可能。

ただし sandbox の bind-mount 構成によって、**同じ絶対パス** が異なる中身に見える可能性は残る (これは仮説 A の領分)。

### 2.5 dispatcher cwd と DB 絶対パスの関係

dispatcher の cwd は `.dispatcher/` だが、`tools/journal_append.sh` は `cd $REPO_ROOT` してから python を起動するため、cwd は cancel される。実 dispatcher が DB に到達するのは `<repo_root>/.state/state.db` の **絶対パス**経由。

`.dispatcher/references/worker-monitoring.md:503` には `sqlite3 ../.state/state.db ...` という one-liner 例がある。これは dispatcher Claude が直接実行する read-only クエリ用で、dispatcher cwd `.dispatcher/` から見て `..` で repo root に上がるパス。同じく絶対パス相当に解決される (`../` が `.dispatcher/..` = repo root)。

## 3. 仮説 A〜E の評価

### 3.1 仮説 A: sandbox auto-allow shadow FS による上書き

> dispatcher / Secretary の sandbox auto-allow 時、`.state/state.db` への write が bubblewrap shadow FS に逸れて、後で real path が読まれた瞬間に shadow が見えた、もしくは shadow 経由で空の DB が real path に書き戻された

**評価: 高 plausibility (現時点で最有力)**

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

> `.state/state.db` と `.dispatcher/.state/...` の混同

**評価: 中 plausibility (literal 版は棄却、effective 版は仮説 A の言い換え)**

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

`probes/checklist.md` 1 章 (B1-1) に追加 row として:

- **1.6 — sandbox auto-allow 下での `.state/state.db` write 観測**
  - 試行 (a): `bash tools/journal_append.sh sandbox_probe_test field=x` を sandbox auto-allow 中に実行
  - 観測 (b1): 直前 / 直後で `python -c "import sqlite3; print(sqlite3.connect('.state/state.db').execute('SELECT count(*) FROM runs').fetchone())"` の値
  - 観測 (b2): `ls -la .state/state.db .state/state.db-wal .state/state.db-shm`
  - 観測 (b3): `stat -c '%i %Y %s' .state/state.db` (inode / mtime / size)
  - 観測 (b4): sandbox 解除後に同じ 3 観測を再取得し、b1〜b3 と差分をとる
  - 期待される判定:
    - **shadow FS 仮説 (本 audit 仮説 A)** が真なら、sandbox 内 view と sandbox 外 view で inode / count が **異なる**
    - 仮説 A が偽なら、sandbox 内外で inode / count が一致

(`probes/categories.md` 凡例上「fs-cwd」or 別 category として「fs-state-db」を新設するか、既存 1 章 B1-1 を拡張するかは Phase 1 schema 設計時に判断。本書では row 案だけ提示。)

### 5.2 `iteration-a-results.md` §6 への追加観察 row 提案

[`iteration-a-results.md`](./iteration-a-results.md) §6 の想定外リストに **#4** として追加:

> **6.4 想定外 #4: sandbox auto-allow 中に `.state/state.db` 書き込みが shadow FS 化した疑い (db-mystery)**
>
> - 事象: probe 中に Secretary が `journal_append.sh pr_opened` を発行直後の SELECT で runs=0、IDLE、events=1 を観測。10:45 で runs=12 に回復
> - 影響: probe 結果そのものへの影響はなし (Secretary は別途 SQL UPDATE / INSERT で復旧 → `gh pr merge` 後に DB 健全状態が見えた)。ただし Iteration A の **probe 副作用として書き残すべき重大観察** であり、Phase 1 schema 設計の前に 1.6 row で再現実験が必要
> - 詳細: [`db-mystery-2026-05-09.md`](./db-mystery-2026-05-09.md) 参照

(本書では追加提案までで、`iteration-a-results.md` 本体への書き込みはしない — audit mode のため。Phase 1 worker が反映する。)

### 5.3 next-iteration-proposals.md への追加提案

[`next-iteration-proposals.md`](./next-iteration-proposals.md) 既存 3 提案 (A/B/C) はいずれも本 db-mystery に直接対応していない。提案 A (B1-1) を拡張するか、別途:

- **提案 D — db-mystery 再現 (sandbox × `.state/state.db` 書き込みの shadow FS 切り分け)**
  - 目的: 本 audit 仮説 A の plausibility を実機 probe で確定 / 棄却
  - 手順: §5.1 row 1.6 を dispatcher pane (auto-allow active) と Secretary pane の双方で実施。`/proc/self/mountinfo` の bwrap / overlay / tmpfs 設定を併記
  - 所要: 30 分 / 1 commit
  - 推奨度: ★★ (Phase 1 schema には直接絡まないが、sandbox semantics の理解を確実にするため Phase 0 の補強として有用)

## 6. 付録: 副次的な sandbox 観察 — bwrap が `~/.aws/.env` に触ろうとして fail する

(本 audit 中に Secretary から共有された情報。db-mystery 本筋とは独立だが sandbox semantics の理解補強として記録。)

- 事象: 本 audit worker が Bash tool を sandbox 経由で起動する都度、bwrap が `Can't create file at /home/happy_ryo/.aws/.env: No such file or directory` で fail する
- 原因 (推定): claude-code 親レイヤーの permissions deny list に `~/.aws/*` が含まれており、bwrap がそれを bind-mount 用 dummy file として準備しようとするが、`~/.aws/.env` という file (実体)が存在しないために mkdir / touch に失敗している
- 影響: sandbox 内で Bash が起動できず、本 audit は `dangerouslyDisableSandbox=true` で実行することで回避した。Secretary 側でも同様事象が頻発しており、本リポジトリ環境では既知 quirks
- 含意: bwrap の bind setup が本リポジトリ環境で **partial に壊れた状態でも sandbox 起動は試行される**。bind 用 dummy file が用意できないとき、bwrap が fail-fast するか fail-soft してその path を素通しするかの挙動分岐は本 audit では未確認 — これは仮説 A 検討時に sandbox 内 `.state/` の bind 状態が想定通りか怪しむ追加根拠
- 推奨フォロー: 提案 D の row 1.6 に `/proc/self/mountinfo` 観察を必須化。`~/.aws/.env` の存否と sandbox 起動 succeess / fail の相関を 1 サンプルでよいので取る

## 7. 関連資料

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
