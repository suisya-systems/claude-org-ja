# Attention notification design

> ステータス: **design only**。本ドキュメントは実装方針と Issue 分割を定義する。実装は `claude-org-runtime` 側と `claude-org-ja` 側の別 Issue / PR で行う。
> 対象: AI worker が人間の反応を必要とした瞬間を desktop notification / sound で通知する attention layer。
> 結論: 実装本体は **Layer 2 = `claude-org-runtime`** に置く。`claude-org-ja` は日本語既定設定・導線・文書を持つ。`core-harness` と `renga` は初回実装では触らない。

---

## 1. 背景

`claude-org-ja` は worker の承認待ち、判断待ち、CI 失敗、silent deadlock などを既に検出・記録する仕組みを持つ。

- dispatcher は `inspect_pane` / `check_messages` / `poll_events` を使い、`APPROVAL_BLOCKED`、`ERROR_DETECTED`、`relay_gap_suspected`、`pane_output_without_peer_msg` などを検出する。
- Secretary は worker escalation を `.state/pending_decisions.json` に記録する。
- `tools/pr_watch.py` は CI 結果を `ci_completed` event として記録し、renga peer にも best-effort 通知する。
- `state.db` は post-M4 の SoT であり、events / runs / worker_dirs / sessions を保持する。

しかし、現在の通知は主に renga-peers の channel message と Secretary pane 内の表示に閉じている。人間が terminal を見ていない場合、実際に重要な以下の状態を見落としやすい。

- worker が tool approval 待ちで停止した。
- worker が判断を仰いだが、人間が気づいていない。
- 人間が返答済みなのに Secretary から worker へ転送されていない。
- CI が失敗した。
- worker が完了して review 待ちになった。
- pane には出力があるが peer message が飛んでいない。

この価値は dashboard で「見に行く」より、attention notification で「呼び戻す」方が強い。したがって主機能は dashboard ではなく、**人間の反応が必要な瞬間を OS 通知・音・fallback bell で知らせる watcher** として設計する。

---

## 2. 目的

`claude-org-runtime attention watch` を追加し、`.state/state.db` と `.state/pending_decisions.json` を監視して、人間の反応が必要な状態を desktop notification / sound / terminal bell で通知する。

期待するコマンド形:

```bash
claude-org-runtime attention scan --state-dir .state --dry-run
claude-org-runtime attention watch --state-dir .state
claude-org-runtime attention watch --state-dir .state --config .state/attention.json
```

`scan` は 1 回だけ評価して終了する。`watch` は polling で継続監視する。

---

## 3. レイヤー判断

### 3.1 Layer 2 `claude-org-runtime` に置く理由

attention notification の本体は OS 通知そのものではなく、**claude-org の実行状態をどう解釈するか**である。

runtime は既に以下を担当している。

- dispatcher CLI
- worker settings 生成
- role schema の bundle
- worker 起動・状態記録に近い deterministic operation
- Layer 4 から切り出された運用ロジック

attention watcher が読む入力は、runtime の責務に近い。

- `.state/state.db`
- `.state/pending_decisions.json`
- `notify_sent`
- `ci_completed`
- `worker_completed`
- `pr_merged`
- `relay_gap_suspected`
- `pane_output_without_peer_msg`

これらは `core-harness` の安全 primitive ではなく、claude-org の runtime semantics である。したがって実装本体は `claude-org-runtime` が妥当。

### 3.2 Layer 4 `claude-org-ja` に残すもの

`claude-org-ja` は consumer / reference distribution として以下を持つ。

- 日本語 notification template
- 既定 config
- README / getting-started / verification の導線
- `/org-start` から attention watcher 起動を案内する文書
- ja 固有の troubleshooting

### 3.3 `core-harness` に入れない理由

`core-harness` は permission schema、validator、hook framework、dangerous git/no-verify block、audit primitive など、Claude Code harness の低レベル安全・監査基盤である。

desktop notification / sound / operator attention は UX / runtime operation 寄りであり、core safety primitive ではない。ここに入れると `core-harness` の責務が広がりすぎる。

将来、複数 harness が共通で使う `AttentionEvent` envelope や dedup/cooldown primitive が必要になった場合だけ、限定的に Layer 1 抽出を検討する。

### 3.4 `renga` に入れない理由

`renga` は端末多重化器 + MCP server であり、pane control / peer messaging / screen inspection / event polling を提供する Layer 3 である。

attention watcher の入力は renga の live event stream ではなく、claude-org が永続化した `state.db` / `pending_decisions.json` で足りる。OS 通知は backend terminal multiplexer の責務ではない。

将来 `renga notify` のような TUI-integrated notification primitive が必要になったら別 Issue とする。

---

## 4. Non-goals

- renga 本体に OS 通知を実装しない。
- Secretary / Dispatcher の prompt に OS 通知を直接やらせない。
- dashboard を主 UI にしない。
- Slack / Discord / ntfy / Pushover などの外部通知を初回必須にしない。
- progress event をすべて通知しない。
- 通知本文に secret、diff、full command、長いログを含めない。
- `core-harness` の責務を UX / OS integration へ広げない。

---

## 5. Runtime Issue: attention scan/watch CLI

### Issue title

`claude-org-runtime`: add attention scan/watch CLI for human-required events

### 実装対象

`claude-org-runtime` に以下を追加する。

```text
claude_org_runtime/attention/
  __init__.py
  cli.py
  config.py
  classifier.py
  readers.py
  dedup.py
  notify.py
  platform.py
```

CLI:

```bash
claude-org-runtime attention scan --state-dir .state --dry-run
claude-org-runtime attention watch --state-dir .state --config .state/attention.json
```

### 入力

- `.state/state.db`
- `.state/pending_decisions.json`
- optional config JSON (`.state/attention.json`)

### 出力

- desktop notification
- urgent sound
- terminal bell fallback
- stdout log
- dedup state file (`.state/attention_notified.json`)

### Attention event model

Runtime 内部では以下のような正規化 event に変換する。

```python
@dataclass(frozen=True)
class AttentionEvent:
    key: str
    kind: str
    severity: Literal["urgent", "normal"]
    title: str
    body: str
    source: str
    task_id: str | None = None
    worker: str | None = None
    created_at: str | None = None
```

`key` は dedup に使う安定 ID とする。

- DB event 由来: `event:<events.id>`
- pending decision 由来: `pending:<task_id>:<kind>`

### 分類ルール

| 入力 | 条件 | Attention kind | Severity |
|---|---|---|---|
| `events` | `event='notify_sent'` and `kind='approval_blocked'` | `approval_blocked` | urgent |
| `events` | `event='notify_sent'` and `kind='relay_gap_suspected'` | `relay_gap_suspected` | urgent |
| `events` | `event='notify_sent'` and `kind='pane_output_without_peer_msg'` | `silent_worker_output` | urgent |
| `events` | `event='ci_completed'` and `status in ('failed','canceled','incomplete')` | `ci_failed` | urgent |
| `events` | `event='worker_completed'` | `worker_completed` | normal |
| `events` | `event='pr_merged'` | `pr_merged` | normal |
| `pending_decisions.json` | pending older than threshold | `pending_decision` | urgent |
| `pending_decisions.json` | user replied but not forwarded older than threshold | `user_reply_not_forwarded` | urgent |

通知しないもの:

- progress-only event
- `heartbeat`
- raw `anomaly_observed` without notification path
- duplicate `notify_sent`
- normal worker report

### Notification backend

追加 dependency なしで実装する。subprocess 呼び出しは timeout 付きにする。

| 環境 | Desktop | Sound |
|---|---|---|
| macOS | `osascript display notification` | `afplay` if configured, else bell |
| Linux | `notify-send` | `paplay` / `canberra-gtk-play` / bell |
| Windows | PowerShell notification or console fallback | PowerShell beep |
| WSL | `powershell.exe` via Windows host | PowerShell beep |
| fallback | stdout | terminal bell `\a` |

Desktop notification backend が利用できない場合も watch は落ちない。stdout + bell に fallback する。

### Config

runtime は config schema と default を持つ。

```json
{
  "desktop": true,
  "sound": "urgent-only",
  "cooldown_sec": 300,
  "poll_interval_sec": 10,
  "pending_decision_min": 15,
  "user_replied_min": 15,
  "max_title_chars": 80,
  "max_body_chars": 240,
  "notify": {
    "approval_blocked": "urgent",
    "relay_gap_suspected": "urgent",
    "silent_worker_output": "urgent",
    "ci_failed": "urgent",
    "pending_decision": "urgent",
    "user_reply_not_forwarded": "urgent",
    "worker_completed": "normal",
    "pr_merged": "normal"
  }
}
```

`sound` の値:

- `"off"`
- `"urgent-only"`
- `"all"`

### Dedup / cooldown

`.state/attention_notified.json` を runtime が管理する。

```json
{
  "events": {
    "event:123": "2026-05-12T10:00:00Z"
  },
  "pending": {
    "pending:issue-123:user_reply_not_forwarded": "2026-05-12T10:00:00Z"
  }
}
```

要件:

- 同じ DB event id は 1 回だけ通知する。
- pending decision は `(task_id, kind)` に cooldown を適用する。
- broken JSON は warning を出して再生成する。
- dedup state の書き込みは atomic replace にする。

### Secret-safe formatting

通知本文は短く、secret-safe にする。

- full command を出さない。
- diff / log / stack trace を出さない。
- `payload_json` の arbitrary field をそのまま本文に出さない。
- task id / worker id / PR number / status 程度に留める。
- body は `max_body_chars` で切る。

### Runtime acceptance criteria

- `claude-org-runtime attention scan --state-dir <fixture> --dry-run` が fake state から attention events を出す。
- `notify_sent kind=approval_blocked` が urgent に分類される。
- `ci_completed status=failed` が urgent に分類される。
- `worker_completed` が normal に分類される。
- progress-only event は無視される。
- stale pending decision が urgent に分類される。
- user replied but not forwarded が urgent に分類される。
- event id dedup が効く。
- pending decision cooldown が効く。
- desktop backend 不在時に stdout + bell fallback になる。
- macOS / Linux / Windows / WSL backend selection が unit test される。
- `--dry-run` は OS notification subprocess を呼ばない。
- broken `.state/attention_notified.json` から復旧できる。

---

## 6. Runtime Issue: locale/template override

### Issue title

`claude-org-runtime`: support attention notification templates and locale overrides

### 背景

runtime は実装本体を持つが、日本語配布物である `claude-org-ja` に通知文面を固定したくない。runtime は中立的な default title/body を持ち、Layer 4 が locale config で上書きできる必要がある。

### 実装方針

runtime config に `templates` を追加する。

```json
{
  "templates": {
    "approval_blocked": {
      "title": "Worker approval required",
      "body": "{worker} is waiting for approval."
    },
    "ci_failed": {
      "title": "CI failed",
      "body": "PR #{pr} finished with {status}."
    }
  }
}
```

テンプレート placeholder は allowlist 方式にする。

許可 placeholder:

- `{task_id}`
- `{worker}`
- `{kind}`
- `{status}`
- `{pr}`
- `{summary}`

未知 placeholder はエラーではなく literal のまま残す、または warning + fallback template にする。初回実装では fallback template を推奨する。

### Acceptance criteria

- config の template override が title/body に反映される。
- 未知 placeholder で watcher が落ちない。
- template 由来の本文も `max_title_chars` / `max_body_chars` で truncation される。
- ja 側 config で日本語文面を提供できる。

---

## 7. ja Issue: default config and documentation

### Issue title

`claude-org-ja`: add attention watcher config, docs, and README positioning

### 実装対象

`claude-org-ja` 側に以下を追加・更新する。

```text
.state/attention.example.json
docs/operations/attention-watch.md
docs/verification.md
README.md
.claude/skills/org-start/SKILL.md
```

`.state/` は gitignored なので、example は tracked path に置く方がよい。候補:

```text
tools/templates/attention.example.json
```

または:

```text
docs/operations/attention.example.json
```

既存の template 配置に合わせるなら `tools/templates/attention.example.json` を推奨する。

### README positioning

README 冒頭の価値訴求を「AI組織運営」だけにしない。以下の痛みに寄せる。

- Claude worker が人間待ちになった瞬間に戻ってこられる。
- approval / judgment / CI failure / silent stop を見逃さない。
- 複数 worker を常時眺めなくてよい。

ただし既存の 4 層アーキテクチャや Secretary/Dispatcher/Curator/Worker は削らない。前面の入口を attention / ops に寄せ、組織構造は仕組みとして後段に置く。

### org-start guidance

`/org-start` 手順に attention watcher の起動案内を足す。

例:

```bash
claude-org-runtime attention watch --state-dir .state --config .state/attention.json
```

ただし自動起動は初回では必須にしない。OS 通知は環境依存が強く、ユーザーが明示的に有効化できる形にする。

### ja default template

ja config は短い日本語文面を提供する。

例:

```json
{
  "templates": {
    "approval_blocked": {
      "title": "ワーカーが承認待ちです",
      "body": "{worker} が承認待ちで停止しています。"
    },
    "ci_failed": {
      "title": "CI が失敗しました",
      "body": "PR #{pr} の CI が {status} で完了しました。"
    },
    "pending_decision": {
      "title": "判断待ちがあります",
      "body": "{task_id} が人間の判断を待っています。"
    },
    "user_reply_not_forwarded": {
      "title": "返答の転送待ちです",
      "body": "{task_id} でユーザー返答が worker に未転送です。"
    }
  }
}
```

### ja acceptance criteria

- README に attention watcher の価値と起動例が載る。
- `docs/operations/attention-watch.md` に OS 別 fallback と troubleshooting が載る。
- `docs/verification.md` に `scan --dry-run` 検証手順が載る。
- `tools/templates/attention.example.json` に ja default config が入る。
- `/org-start` docs に watcher 起動案内がある。

---

## 8. ja Issue: integration verification fixtures

### Issue title

`claude-org-ja`: add fixtures for attention watcher integration verification

### 背景

runtime 側 unit tests だけでは、ja の event 語彙と実際の `.state` 形状が drift しても気づきにくい。`claude-org-ja` 側にも semantic fixtures を置き、runtime CLI との統合を検証する。

### 実装対象

```text
tests/fixtures/attention/
  state.db
  pending_decisions.json
  expected_scan.json
tests/test_attention_runtime_integration.py
```

`state.db` fixture を binary で持つか、test 内で schema から生成するかは実装時に決める。保守性は test 内生成の方が高い。

### Acceptance criteria

- fixture の `notify_sent approval_blocked` が expected urgent event になる。
- fixture の `ci_completed failed` が expected urgent event になる。
- fixture の stale `pending_decisions.json` が expected urgent event になる。
- `claude-org-runtime attention scan --dry-run --json` の出力が golden と一致する。

---

## 9. 将来 Issue: optional external notification sinks

### Issue title

`claude-org-runtime`: optional external attention sinks for Slack/Discord/ntfy

### 背景

初回実装は local notification に閉じる。外部通知は secret / privacy / network configuration の問題があるため必須にしない。

### 方針

local notification が安定してから optional sink として追加する。

候補:

- Slack webhook
- Discord webhook
- ntfy.sh
- Gotify
- Pushover

外部 sink は必ず opt-in。通知本文は local notification よりさらに短くし、secret-safe formatting を共有する。

---

## 10. 全体 Acceptance Criteria

- runtime に `claude-org-runtime attention scan/watch` がある。
- ja repo から `claude-org-runtime attention scan --state-dir .state --dry-run` を実行できる。
- approval blocked / relay gap / silent worker output / CI failed / pending decision が urgent 通知になる。
- worker completed / pr merged は normal 通知になる。
- progress 系は通知されない。
- OS notification が使えない環境でも stdout + terminal bell に fallback する。
- urgent-only sound では urgent だけ音が鳴る。
- dedup / cooldown により同じ event が鳴り続けない。
- 通知本文は secret-safe で短い。
- ja 側に日本語 config と運用手順がある。
- core-harness / renga を変更しない。

---

## 11. Open questions

1. `watch` を `/org-start` から自動起動するか、明示起動に留めるか。
   - 初回は明示起動を推奨。OS 通知は環境依存が強く、勝手に音が鳴ると不快になりやすい。
2. `worker_completed` を normal desktop notification に含めるか。
   - 初期 default では含める。ただし sound は鳴らさない。
3. `pending_decision_min` の既定値。
   - 既存 dispatcher monitoring の 15 分に合わせる。
4. `notify_sent` event の payload schema drift にどう耐えるか。
   - runtime classifier は missing field を許容し、最低限 `event` / `kind` / `payload_json` から復元する。
5. `state.db` が無い初回起動時の挙動。
   - warning ではなく no-op。`scan` / `watch` は落ちない。

