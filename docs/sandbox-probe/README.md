# sandbox-probe (Issue #376 epic Pre-Phase 0 spike)

これは Issue #376 epic の **Pre-Phase 0 spike** 成果物 (元 commit: `workers/sandbox-probe/` の 198576c, by `sandbox-probe-pre-phase-0-b1-b2` worker) を本リポジトリに取り込んだもの。

spike の目的は、audit-issue-376-2026-05-09 で挙がった以下 2 件の Blocker を **実機 probe で再現できる harness** (checklist / profile / runbook) を確定することだった。実機 probe 実行自体は本 spike のスコープ外で、次 iteration の probe-worker が引き継ぐ。

- **B1-1**: dispatcher × `bypassPermissions` × sandbox の発火可否 (Issue #377)
- **B2-1**: worker への repo-shared `.claude/settings.json` 継承状況 (Issue #379)

関連 Issue: #376 (epic) / #377 (Phase 0) / #378 (Phase 1 schema) / #379 (Phase 2 hooks) / #380 (Phase 3 環境別 fail policy)。

## ディレクトリ構成

```
docs/sandbox-probe/
├── README.md                        ← このファイル
├── notes/
│   ├── sandbox-probe-runbook.md     ← 実機 probe 再現手順 (B1-1 / B2-1)
│   ├── baseline-observations.md     ← 静的解析だけで確定した事実
│   └── next-iteration-proposals.md  ← 次 iteration の 3 案 (A: B1-1 単発 / B: B2-1+git-surface / C: secrets)
├── probes/
│   ├── README.md                    ← probe-worker 運用ルール
│   ├── categories.md                ← 7 category の背景・狙い
│   └── checklist.md                 ← 5 列 checklist (category / 試行 / 期待 / 観測 / 結論)
└── profiles/
    ├── README.md                    ← handcraft profile の位置付けと適用方法
    ├── profile-baseline.json        ← 最小防御 (Pattern A worker 想定)
    └── profile-tightened.json       ← 強化版 (git -C 形式 deny / sandbox denyWrite 拡張 等)
```

> 元 spike worker は `docs/` サブディレクトリに runbook / 観察 / 提案メモを置いていた。本 repo 取り込み時は `docs/sandbox-probe/docs/` の二重 `docs` を避けるため `notes/` にリネームした。初回コミット (5ee1089) 時点では 9 files 全て本文無変更 (元 commit 198576c と sha256 一致)。続く round1 self-review fix commit (ffe15d6) で安全性 / 用語の明確化のため `notes/sandbox-probe-runbook.md`, `probes/checklist.md`, `probes/README.md`, `profiles/README.md` を編集している (詳細は当該 commit message)。元コミット参照: `git -C /home/$USER/work/org/workers/sandbox-probe show 198576c --stat`。

## 読み順

1. [`docs/sandbox-probe/notes/baseline-observations.md`](./notes/baseline-observations.md) — 何が **静的解析だけで確定**しており、何が実機待ちかを把握する
2. [`docs/sandbox-probe/probes/categories.md`](./probes/categories.md) — 各 probe category がどの audit finding に対応するか
3. [`docs/sandbox-probe/probes/checklist.md`](./probes/checklist.md) — 実機 probe で埋める row 一覧 (本 spike 時点では全 row 「未実測」)
4. [`docs/sandbox-probe/notes/sandbox-probe-runbook.md`](./notes/sandbox-probe-runbook.md) — checklist を実機で埋めるための再現手順
5. [`docs/sandbox-probe/profiles/README.md`](./profiles/README.md) → [`docs/sandbox-probe/profiles/profile-baseline.json`](./profiles/profile-baseline.json) / [`docs/sandbox-probe/profiles/profile-tightened.json`](./profiles/profile-tightened.json) — 比較対象の handcraft profile
6. [`docs/sandbox-probe/notes/next-iteration-proposals.md`](./notes/next-iteration-proposals.md) — 次 iteration でどの組合せを優先するか (A/B/C 3 案)

## profile JSON 内の placeholder

`profiles/profile-baseline.json` および `profiles/profile-tightened.json` は worker `.claude/settings.local.json` のスーパーセットだが、`claude-org-runtime settings generate` で emit せずに **手動で worker に書き戻す** 想定の handcraft 候補のため、環境固有 path を placeholder で残している:

| placeholder | 意味 | 適用時に置換する値の例 |
|---|---|---|
| `{worker_dir}` | probe を回す worker の cwd (Pattern A) | `/home/$USER/work/org/workers/sandbox-probe-iter1` |
| `{claude_org_path}` | claude-org-ja の clone パス (hook 群と repo-shared `.claude/settings.json` がある場所) | `/home/$USER/work/org/claude-org-ja` |

実機検証時の置換例 ([`docs/sandbox-probe/notes/sandbox-probe-runbook.md`](./notes/sandbox-probe-runbook.md) §4 と同じ流れ):

```bash
sed -i "s|{worker_dir}|/home/$USER/work/org/workers/sandbox-probe-iter1|g; \
        s|{claude_org_path}|/home/$USER/work/org/claude-org-ja|g" \
       /home/$USER/work/org/workers/sandbox-probe-iter1/.claude/settings.local.json
jq empty /home/$USER/work/org/workers/sandbox-probe-iter1/.claude/settings.local.json
```

## 本 spike の運用上の前提 (重要)

- **本 spike では実機 probe を回していない**。`probes/checklist.md` の「観測結果」「結論」列は全 row 「未実測」「—」で残してある。次 iteration の probe-worker が埋める。
- 検証深度 minimal。fmt/lint は走らせず、profile JSON は `jq empty` で構文確認のみ。
- handcraft profile はいずれも `sandbox.failIfUnavailable: false` を維持 (bubblewrap 未導入の WSL2 環境で起動が落ちると検証ループが回らないため)。fail-closed への切替判断は Phase 3 (Issue #380) の別判断。
- profile を `claude-org-runtime settings generate` から自動 emit させるのは Phase 1 (Issue #378) で `role_configs_schema.json` に `sandbox` field を追加してから。本 spike 時点の bundled schema には `sandbox` field がない。

## 関連リソース

- audit-issue-376-2026-05-09.md (B0/B1/B2/B3 の詳細指摘) — claude-org-ja の repo 外: `/home/happy_ryo/work/org/workers/claude-org-ja/tmp/audit-issue-376-2026-05-09.md`
- [`docs/verification.md`](../verification.md) §sandbox 実機検証 (bubblewrap/socat 前提と現行 verification 手順)
- [`docs/worker-permissions-design.md`](../worker-permissions-design.md) (sandbox `additionalDirectories` の design 注釈)
- [`tools/org_extension_schema.json`](../../tools/org_extension_schema.json) (`worker_roles` と `forbidden_allow_exact`)
