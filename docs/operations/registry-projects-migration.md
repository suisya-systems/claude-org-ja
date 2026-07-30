# registry/projects.md の operator-local 化 — 移行手順 (Issue #811)

`registry/projects.md` は tracked ファイルから **operator-local な生成ファイル**へ移行した。リポジトリにコミットされるのは列スキーマ・仕様説明・一般サンプル行だけを持つテンプレート [`registry/projects.example.md`](../../registry/projects.example.md) で、実体の `registry/projects.md` は `.gitignore` 済み・[`/org-start`](../../.claude/skills/org-start/SKILL.md) が初回に生成する。

**なぜ**: 実体ファイルには operator 固有の登録行（顧客名・非公開リポジトリ URL）が入る。tracked のままだと、暗黙の commit 境界で公開履歴に載る事故が起きうる。実際 2026-07-31 に PR #810 で operator 固有 2 行が履歴に入りかけた。

**読み手ツールは変わらない**。`dashboard/server.py` / [`tools/registry_parser.py`](../../tools/registry_parser.py) / [`tools/gen_delegate_payload.py`](../../tools/gen_delegate_payload.py) / [`tools/work_discovery_repos.py`](../../tools/work_discovery_repos.py) / [`tools/resolve_worker_layout.py`](../../tools/resolve_worker_layout.py) / [`tools/state_db/importer.py`](../../tools/state_db/importer.py) は従来どおり `registry/projects.md` を読む。インターフェース変更は無い。

## 1. 先に知っておくべき git の挙動（ここが唯一の危険点）

**`.gitignore` に足してもローカルファイルは守られない。** gitignore は untracked ファイルにしか効かない。tracked だったパスを `git rm --cached` + gitignore で untrack するコミットは、**pull した側では通常の削除として伝播**し、working tree のファイルが消える。

移行を行った checkout ではファイルが残るのに pull 側では消える、というこの非対称が事故の元なので、実 git で挙動を固定してある（[`tests/test-registry-projects-migration.sh`](../../tests/test-registry-projects-migration.sh)、10 assert）。確定した事実:

| checkout の状態 | 移行コミットを pull した結果 | 危険度 |
|---|---|---|
| 移行を行った checkout | `git rm --cached` はインデックスからのみ外すので**ファイルは残る**。以後 gitignore が効き `git status` はクリーン | 安全 |
| `registry/projects.md` が **clean**（HEAD と同一） | **ファイルが削除される** | **危険 — 事前退避が必須** |
| `registry/projects.md` が **dirty**（未コミットの登録行あり） | git が **pull 自体を拒否**（exit≠0）。ファイルも登録行も無傷 | 安全（loud failure） |

dirty ケースが安全なのは、git が未コミット変更を持つファイルの削除を拒否するため。silent loss ではなく「pull が止まる」形で気付ける。

> 実運用の多くはこの dirty ケースに当たる。ワーカー派遣で自動登録された行は commit されないまま working tree に溜まるので、`registry/projects.md` は常態的に dirty である。

## 2. 移行手順

### 2-1. 移行コミットを pull する前に（全 checkout 共通・1 回だけ）

退避しておく。1 コマンドで済み、clean / dirty のどちらでも無害:

```bash
cp registry/projects.md registry/projects.md.bak
```

Windows native (PowerShell):

```powershell
copy registry\projects.md registry\projects.md.bak
```

### 2-2. pull する

```bash
git pull
```

- **pull が拒否された場合**（dirty ケース）: `error: Your local changes to the following files would be overwritten by merge` が出る。ファイルは無傷なので、`git checkout -- registry/projects.md` で HEAD に戻してから pull し直し、2-3 で退避ファイルから復元する。退避が済んでいるので登録行は失われない。
- **pull が通った場合**（clean ケース）: `registry/projects.md` は消えている。2-3 で復元する。

### 2-3. 復元する

```bash
[ -f registry/projects.md ] || cp registry/projects.md.bak registry/projects.md
```

Windows native (PowerShell):

```powershell
if (!(Test-Path registry\projects.md)) { copy registry\projects.md.bak registry\projects.md }
```

復元後は gitignore が効くので `git status` はクリーンになる（= 二度と履歴に入らない）。確認:

```bash
git status --porcelain registry/projects.md   # 何も出なければ OK
```

退避ファイルは削除してよい。残したままでも `registry/projects.md.bak*` は `.gitignore` 済みなので履歴には入らない（退避ファイルは実体と同じ operator 固有データを持つため、実体と同様に commit 不可にしてある）。**別名を付けて退避する場合はこのパターンから外れる**ので、リポジトリ外へ置くこと。

### 2-4. 退避を忘れて clean checkout で pull してしまったら

コミット済みの内容は履歴から取り出せる:

```bash
git show HEAD~1:registry/projects.md > registry/projects.md
```

移行コミットが `HEAD~1` でない場合は `git log --oneline -- registry/projects.md` で最後にファイルが在ったコミットを特定する。**未コミットだった登録行はこの方法では戻らない**（が、その状態なら pull は拒否されているはずなので、そもそもこの経路に入らない）。

いずれも駄目なら、テンプレートから作り直して登録行を手で足す:

```bash
python3 tools/ensure_projects_registry.py
```

## 3. 新規 checkout / fresh clone

何もしなくてよい。[`/org-start`](../../.claude/skills/org-start/SKILL.md) の Step 0 が [`tools/ensure_projects_registry.py`](../../tools/ensure_projects_registry.py) を呼び、テンプレートから `registry/projects.md` を生成する。既に存在する場合は**絶対に上書きしない**。

手動で生成したい場合:

```bash
python3 tools/ensure_projects_registry.py          # 生成（既存があれば触らない）
python3 tools/ensure_projects_registry.py --check  # 状態確認のみ（書き込まない）
```

## 4. 列スキーマが増えたとき

テンプレートの列が増えても、既存の `registry/projects.md` は上書きされない（上書きしない契約が優先する）。代わりに `/org-start` のたびに `tools/ensure_projects_registry.py` がヘッダーを突き合わせ、手元ファイルに足りない列があると **warning を出す**（非 fatal・org-start は止めない）:

```
header drift: registry/projects.md
  the template declares columns your local registry lacks: base_branch
  add them to the table header (and separator row) of registry/projects.md.
  Existing rows may leave the new cells empty - an empty cell keeps the previous behaviour.
```

対処は表ヘッダー行とセパレーター行に列を足すだけ。既存のデータ行は編集不要で、空セルは従来動作を保つ（[`tools/registry_parser.py`](../../tools/registry_parser.py) は header 名で列を対応付け、列が足りない行も legacy として読む）。列の意味は [`registry/projects.example.md`](../../registry/projects.example.md) を参照。

CI 等で drift を fail にしたい場合は `--strict`（drift で exit 3）。既定は exit 0 の warning で、組織の起動を止めない。

## 5. 関連

- テンプレート本体: [`registry/projects.example.md`](../../registry/projects.example.md)
- 生成器: [`tools/ensure_projects_registry.py`](../../tools/ensure_projects_registry.py)
- 移行挙動のテスト: [`tests/test-registry-projects-migration.sh`](../../tests/test-registry-projects-migration.sh)
- 生成器のテスト: [`tests/test_ensure_projects_registry.py`](../../tests/test_ensure_projects_registry.py)
- 同型の先行事例（`.state/attention.json` をテンプレートから配置）: [`docs/operations/attention-watch.md`](./attention-watch.md)
