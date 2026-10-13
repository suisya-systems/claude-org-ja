# org-start: 設計メモと経緯

[`.claude/skills/org-start/SKILL.md`](../SKILL.md) の手順から外した、理由・経緯・内部設計の説明。起動の手順そのものは SKILL.md だけで完結する。

目次:
- [キュレーターを起動しない理由](#キュレーターを起動しない理由)
- [MCP ツールのタブ scope 分類](#mcp-ツールのタブ-scope-分類)
- [spawn_claude_pane 引数の詳細](#spawn_claude_pane-引数の詳細)
- [Block C2: runtime drift 検出の設計](#block-c2-runtime-drift-検出の設計)
- [Block C4: role config drift 検出の設計](#block-c4-role-config-drift-検出の設計)
- [Stage A / Stage B の wall-clock 効果](#stage-a--stage-b-の-wall-clock-効果)

## キュレーターを起動しない理由

常駐キュレーター（spawn + `/loop 30m /org-curate`）は廃止された。キュレーターはディスパッチャーが worker ペインクローズ時に `tools/check_curate_threshold.py` の閾値超過を検出したときだけ一時起動される（[`.dispatcher/references/pane-close.md`](../../../../.dispatcher/references/pane-close.md) Step 5）。worker close が発生しない期間の閾値チェック飢餓ケースへの補助トリガー（org-start バックストップ）は [Issue #502](https://github.com/suisya-systems/claude-org-ja/issues/502) でバックログ化されている。

## MCP ツールのタブ scope 分類

org が運用で行使する `mcp__renga-peers__*` 14 種はタブ scope が一様ではないので、主分類 3 つ + 例外で読む（各 capability トークンは独立判定 = 契約 T-§cap の independence rule。分類の正本は [`docs/contracts/backend-interface-contract.md`](../../../../docs/contracts/backend-interface-contract.md) T-§cap / T-§4.2）:

- **Group A（`caller_scope`）= pane 制御**: `spawn_pane` / `spawn_claude_pane` / `focus_pane` / `list_panes` / `inspect_pane` / `send_keys`。この capability を確立できた経路では **caller のタブ内**（フォーカス非依存）に解決され、`list_panes` は caller のタブのみを返す（契約 T-§cap の Group A は `spawn_codex_pane` を含む 7 種。ここでは org の 14 種に現れる 6 種を挙げている）
- **Group B（`caller_scope_close_identity`）= 別ゲート**: `close_pane` / `set_pane_identity`。`caller_scope` からは**導出してはならない**（`close_pane` は不可逆なので特に混同しない）
- **messaging（`cross_tab_peers`）= タブ横断**: `send_message` / `list_peers`。`list_peers` は全タブ列挙、`send_message` は**数値 id 宛ならタブ横断**・**stable name は送信側タブ内でのみ**解決する。契約 T-§4.2 は messaging 到達性から pane 制御到達性を推論することを **MUST** で禁じている
- **上記 3 分類に入らない 4 種（例外）**: `new_tab` は Group A ではなく独立 capability **`spawn_tab`** 側（tab 生成 / tab 指定 spawn）。`poll_events` は pane 制御でも messaging でもなく **lifecycle surface** で、そのタブ scope は契約 T-§3.1 が別に固定する。`set_summary` / `check_messages` は自ペイン / 自キューに対する操作であって、**タブ横断 addressing の話ではない**

誤読時の分岐手順（`pane_not_found` の pane 制御文脈 / messaging 文脈）は [`.claude/skills/org-delegate/references/renga-error-codes.md`](../../org-delegate/references/renga-error-codes.md) を参照。

ここでの 14 種は **org が運用で行使する surface** であって、プリフライトの必須集合とは別の数である: [`tools/check_renga_compat.py`](../../../../tools/check_renga_compat.py) は capability probe 用の `server_info` を加えた **15 種**を必須（subset 判定、余剰ツールは正常）として検査する。`server_info` は probe 専用で、`.claude/settings.json` の allowlist には意図的に未追加である（理由は [`docs/design/renga-decoupling.md`](../../../../docs/design/renga-decoupling.md) を参照）。

## spawn_claude_pane 引数の詳細

Block A の dispatcher spawn の引数の意味と落とし穴:

- `target`: 分割対象のペイン。dispatcher は `target="focused"`（窓口ペインを分割）
- `direction`: `"horizontal"` = 上下分割（既存ペイン=上 / 新ペイン=下）、`"vertical"` = 左右分割（既存ペイン=左 / 新ペイン=右）
- `role`: `mcp__renga-peers__list_panes` で役割識別できるラベル
- `name`: 後続の `send_message(to_id="dispatcher", ...)` 等で参照する安定名。**renga-peers は全桁数字の name を id として解釈するので、英字を含む名前を必ず付ける**
- `cwd`: caller ペイン（= 窓口）の cwd を基点に相対解決される。`cd X && claude ...` を `command` に埋める旧方式は禁止（auto-upgrade が発動せず channel push が失われる落とし穴）
- `permission_mode` / `model`: renga が `claude --permission-mode {mode} --model {model} --dangerously-load-development-channels server:renga-peers` を合成して実行する
- 戻り値: `"Spawned pane id=N."` テキスト。エラーは `[<code>] <msg>` 形式（例: `[split_refused]` / `[pane_not_found]` / `[cwd_invalid]`）。code 一覧と分岐は `.claude/skills/org-delegate/references/renga-error-codes.md` を参照
- `.dispatcher/CLAUDE.md` / `.curator/CLAUDE.md` にそれぞれのロール用の指示が入っている（Secretary の CLAUDE.md とは別）

## Block C2: runtime drift 検出の設計

**sandbox 内では PyPI に届かない（#119 の真因）**: このチェックを Claude Code の Bash sandbox 内で実行するとネットワーク隔離で PyPI に到達できない。旧実装はこの到達不能を **silent skip（何も出さず exit 0）** していたため、pin ラグ（venv が古いまま）を「最新」と誤読し、既修正バグを worker に委譲する phantom dispatch を招いた（2026-07-08 #119: runtime 0.1.36 で既修正のバグを 0.1.34 venv で踏んだ）。根治として (1) スクリプトは到達不能を **exit 2 + stderr 診断**で顕在化し、(2) Block C2 は **`dangerouslyDisableSandbox: true` を付けてホスト実行する**（sandbox 内でしか回せない場合は exit 2 =「PyPI 未確認」となり、ネットワーク到達可能なホストで再実行するまで drift 未検証と扱う）。

設計メモ:
- latest 取得は PyPI JSON API (`https://pypi.org/pypi/claude-org-runtime/json`) を urllib.request で叩く (timeout 8s)。`pip index versions` は experimental で stderr に warning を吐くため採用しない
- **pin window**: ja の `pyproject.toml` の `claude-org-runtime` 依存制約を regex で動的に読み取り、PyPI releases からその制約を満たす最新のみを latest として比較する（`packaging.SpecifierSet` 利用）。上位 major / minor が出ても窓外 upgrade を促さない。`packaging` 未インストール環境は **exit 2（未確認・stderr 診断）** に落ちる — 旧実装の silent skip は廃止した
- **local install 検出 (Issue #747)**: installed が `file://` パス / VCS チェックアウト / editable から入っている場合、その version はリリース版と対応する保証がない（ローカルビルドは任意の version を名乗れる）。PEP 610 `direct_url.json`（`importlib.metadata.Distribution.read_text` で読む。dist-info の直接 glob ではない）で判定し、該当時は PyPI 到達前に **exit 2 + stderr 診断（`local install -- PyPI 照合不能`）** に落とす。`file://` install を「最新・drift なし」と誤報告した事故の根治。https アーカイブ等の非ローカル direct URL は通常の PyPI 照合に委ねる
- yanked release / prerelease は候補から除外する（pip が通常選ばないバージョンを latest にしないため）
- 「drift = 古い」も「drift = preview 入り (installed > latest の release channel ずれ)」も同じく **exit 1** の 1 行で通知する。auto-upgrade はせず、対応はユーザー判断に委ねる
- **stdout は drift 行のみ**（Step 4 へ verbatim 転記できる）。offline / 未確認 / 未インストールの診断、および `測定 interpreter` の provenance 行はすべて **stderr** に出す（spliceable な stdout を汚さない）
- **interpreter provenance**: `測定 interpreter` 行は outcome に依らず毎回 stderr の先頭に出る（exit 0 も含む）。up to date も drift と同じく「測った Python に相対な判定」なので、片方だけ silent にすると同じ取り違えが exit 0 側で起きるため。判定そのものには影響しない開示専用の 1 行で、exit code 契約は不変
- 警告コマンドには `pyproject.toml` から読み取った pin 制約をそのまま埋め込むので、ユーザーが貼り付けても窓外への upgrade にはならない
- スクリプト本体: [`tools/check_runtime_version.py`](../../../../tools/check_runtime_version.py)

## Block C4: role config drift 検出の設計

**なぜ起動時に見る必要があるのか（guard の無言死）**: `settings.local.json` は gitignored なので、CI の [`.github/workflows/tests.yml`](../../../../.github/workflows/tests.yml) が回す `tools/check_role_configs.py --include-worker-settings .` は `--include-local` を付けておらず、**tracked ファイルしか検証しない**。したがって hook パスのドリフト（`.hooks/*.sh` の rename / 移動で hook command が実在しないパスを指し、guard が発火しないまま無言で無効化される）や必須 allow / deny の欠落、さらには **settings ファイルそのものの不在**（そのロールが hook 層ゼロで動く。`bypassPermissions` のディスパッチャーでは hook が唯一の強制層）は **CI では一切検出されない**。この盲点を塞ぐ唯一の定期的な契機が org-start なので、起動のたびに `--include-local` で 1 回検証する。

**検出される drift の種類**: (a) schema 宣言済みの settings ファイルが**存在しない**（`settings file not found` — `/org-setup` 未実行 / 配布漏れ）、(b) 必須 allow / deny の欠落、(c) schema 未登録の allow エントリ混入、(d) 必須 hook の欠落、(e) hook command が実在しないパスを指す。個別 drift の対処フローは [`docs/getting-started.md`](../../../../docs/getting-started.md) の該当節にある。

設計メモ:
- **stdout は転記用の 1 行と `[ERROR]` 明細のみ**（spliceable に保つ）。件数サマリ `role_configs: N error(s)` と exit 2 の traceback / 診断はすべて **stderr** に出す。stdout への出力は cp932 / `PYTHONIOENCODING=ascii` 等の非 UTF-8 コンソールでも落ちないよう ASCII 置換フォールバックを通す（転記用の 1 行が `UnicodeEncodeError` で消えると drift を検出したのに warning が付かない、という最悪形になるため）
- **exit 1 と exit 2 を分けている理由**: 分けないと「checker が壊れて動かなかった」が「drift を検出した」と同じ signal になり、逆に traceback を素通りさせると壊れた guard が clean と読まれる。#818 が塞ぎたい失敗モードそのものなので、契約として [`tests/test_check_role_configs.py`](../../../../tests/test_check_role_configs.py) の `ExitCodeContractTests` で lock している
- ネットワークに触れないので Block C2 と違い sandbox 内実行でも判定自体は正しい
- スクリプト本体: [`tools/check_role_configs.py`](../../../../tools/check_role_configs.py)。drift の修復は [`.claude/skills/org-setup/SKILL.md`](../../org-setup/SKILL.md)（additive-only なので既存設定を壊さない）、個別の drift 種別ごとの対処は [`docs/getting-started.md`](../../../../docs/getting-started.md) の該当節を参照

## Stage A / Stage B の wall-clock 効果

Step 0 完了直後に Block A の spawn を発火し、Block B / C と Claude の boot wait を overlap させる並列化（Issue #410 / Stage B）の効果:

| stage | 変更内容 | wall-clock |
|---|---|---|
| 改修前 | 状態復元 → ダッシュボード起動 → dispatcher 起動 (spawn+Enter+poll+greet+DB+snapshot) → curator 起動 (同様) を serial | 〜180s |
| Stage A 後 | dispatcher / curator 起動を 1 つの並列 block にまとめ、両 spawn / Enter / poll / greet / DB write / snapshot を一括化 | 〜90s |
| Stage A+B 後 | 上に加え、Step 0 完了直後に Block A の spawn を発火、Block B (state 復元) / Block C (dashboard 起動) と並列に Claude の boot wait を overlap | 〜35s |
| curator オンデマンド化後 | 起動対象は dispatcher のみ（curator の spawn / Enter / poll / greet が消滅） | さらに短縮 |
