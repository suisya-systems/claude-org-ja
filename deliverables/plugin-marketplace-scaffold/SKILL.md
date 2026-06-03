---
name: plugin-marketplace-scaffold
description: >
  Claude Code skill 集を GitHub plugin marketplace 配布に対応させる（既存 ZIP / 手動 install からの
  移行・新設どちらも）定型 scaffold。marketplace.json + plugins/<name>/.claude-plugin/plugin.json +
  skills 同梱 + helper script の ${CLAUDE_PLUGIN_ROOT} 参照化 + CI offline schema validation job を
  一式で組む。「skill 集を plugin marketplace で配りたい」「claude plugin validate を CI に組みたい」
  「plugin 同梱 skill から host 絶対パスを排除したい」タスクで使う。ユーザーが「marketplace」と
  明示しなくても、「skill を plugin として配布したい」「/plugin install で入れられるようにしたい」
  「skill 集の配布を ZIP / 手動 install から移行したい」という依頼ならこのスキルを使う。
type: implementation
triggers:
  - "skill 集を ZIP / 手動 install から GitHub plugin marketplace 配布へ移行する（または marketplace を新設する）"
  - "リポジトリに marketplace.json / plugin.json を立てて claude plugin validate を CI に組み込む"
  - "plugin 同梱 skill の helper script 参照から host 絶対パス（~/.claude/... 等）を排除する"
origin:
  task_id: "wp-marketplace-poc"
  date: "2026-06-04"
---

# plugin-marketplace-scaffold: Claude Code plugin marketplace 配布の scaffold

既存の skill 集リポジトリに GitHub plugin marketplace 配布（`/plugin marketplace add <owner>/<repo>` →
`/plugin install <plugin>@<marketplace>`）を純粋追加で立ち上げ、offline schema validation を CI に組むまでを
1 つの定型 PR として実行する。

## 背景

workplace-skillls #81 / PR #89（merge 32423d4）で、ZIP 2 本柱配布と並走する plugin marketplace 配布 PoC を
構築した際のパターン。レビューで「認証できないのに CI で `claude plugin validate` が動くのか?」という疑問が
出たが、validate は Anthropic API を一切呼ばない offline 静的スキーマ検証であることを正負ペアの実証で裏取り
し、その根拠を CI コメントとして残す形が確立した。description を利用者向け文言に統一する指摘も同レビューで
確定した。

## 前提条件

- 対象リポジトリに配布したい skill（`SKILL.md`）が 1 つ以上あること
- Claude Code CLI がローカルに導入済みであること（`claude plugin validate` を使う。2026-06 時点の v2.1.161 で確認）
- CI は GitHub Actions を想定（他 CI でも `npm install -g @anthropic-ai/claude-code` → validate の 2 step で移植可）

## 手順

### Step 1: marketplace.json をリポジトリ root の `.claude-plugin/` に新設

`source` は **リポジトリ内相対パス**で plugin ディレクトリを指す。配置先は
`.claude-plugin/marketplace.json`（JSON はコメント不可のためパスは本文側に書く）:

```json
{
  "name": "<marketplace-name>",
  "owner": { "name": "<owner 表示名>" },
  "description": "<利用者向け 1-3 文>",
  "version": "0.1.0",
  "plugins": [
    {
      "name": "<plugin-name>",
      "source": "./plugins/<plugin-name>",
      "description": "<利用者向け 1-3 文>"
    }
  ]
}
```

**description は利用者向け文言で書く**: 「何ができるか・何をしないか（read-only 等の安全性）」を
利用者の言葉で書く。「PoC」「SOT」「cut-over」「§3.3 step1」のような開発者向け情報・内部用語は
README / 設計 doc 側に書き、marketplace.json / plugin.json には載せない（`/plugin` UI にそのまま
露出するため）。

### Step 2: plugin 本体 `plugins/<name>/` を組む（plugin.json + skills 同梱）

```
plugins/<plugin-name>/
├── .claude-plugin/plugin.json   # name / description / version / author / homepage / repository / license
├── README.md                    # 開発者向けの位置づけ・検証手順はこちらに書く
├── scripts/                     # skill が参照する helper script の同梱コピー
└── skills/<skill-name>/SKILL.md # 配布する skill 本文
```

既存配布（ZIP / install script）がある場合は**置換せず並走の純粋追加**にする。skill 本文の SOT は
既存位置に残し、plugin 配下はそこからの機械的書き換えコピーと README に明記する（全面 cut-over は
別タスクに切る）。plugin skill は `/<plugin-name>:<skill-name>` に強制 namespace されるため、既存の
flat 名と衝突しない点も README に記す。

### Step 3: helper script 参照を `${CLAUDE_PLUGIN_ROOT}/scripts/` に書き換え（host パス排除）

plugin 配下の SKILL.md では、canonical 版から次の 2 点だけを機械的に書き換える:

- helper script 参照: `~/.claude/skills/.../scripts/foo.py` → `${CLAUDE_PLUGIN_ROOT}/scripts/foo.py`。
  `${CLAUDE_PLUGIN_ROOT}` は plugin の install / cache 先に runtime が解決するため、host の絶対パスに
  依存しない。参照される script は `plugins/<name>/scripts/` に同梱する（cut-over までは canonical と同期）。
- リポジトリ相対リンク: `../../../docs/...` → `https://github.com/<owner>/<repo>/blob/main/docs/...` の
  絶対 URL。plugin 単独配布では相対パスが辿れないため。

runtime データ（state file 等）や個人 override のパスは helper script ではないので書き換え対象外
（現状維持で良い）。

### Step 4: ローカル検証（正負ペアで validate の実体を確認）

```bash
claude plugin validate . --strict                       # marketplace 検証
claude plugin validate ./plugins/<plugin-name> --strict # plugin 検証
claude --plugin-dir ./plugins/<plugin-name>             # 開発ロードで /<plugin>:<skill> 起動確認
```

`--strict` は警告もエラー化する CI 推奨フラグ。初回構築時は**負の対照**も取る: 壊れた manifest
（owner 欠落等）を一時 dir に置いて validate が fail することを確認する。「認証なしで pass した」
だけでは no-op 素通りの可能性を排除できないため、正負ペアで「offline で検証が実体を持つ」ことを
示す（レビュー回答にもこのペアで答えると強い）。

```bash
# 負の対照の例: 認証情報を全て外しても壊れた manifest は fail する
BAD=$(mktemp -d); mkdir -p "$BAD/.claude-plugin"
printf '{ "name": "x", "plugins": [ { "source": "./nope" } ] }' > "$BAD/.claude-plugin/marketplace.json"
TMPHOME=$(mktemp -d)
env -i PATH="$PATH" HOME="$TMPHOME" ANTHROPIC_API_KEY= CLAUDE_CODE_OAUTH_TOKEN= \
    CLAUDE_CONFIG_DIR="$TMPHOME/.claude" claude plugin validate "$BAD" --strict
# → "✘ Found N errors" / Validation failed（= 認証の有無に依らず検証が実体を持つ）
```

### Step 5: CI に offline schema validation job を純粋追加（根拠コメント付き）

既存 lint job とは独立した job として追加する（既存 CI への影響ゼロ）。**「なぜ認証なしで通るか」の
根拠コメントを job 直上に残す**ことで、将来のレビュアーが同じ疑問を自己解決できる。

```yaml
  # ★ なぜ認証なしで CI が通るか:
  #   `claude plugin validate <path>` は marketplace.json / plugin.json (および skill frontmatter
  #   や hooks) をローカルで読んで JSON スキーマ検証するだけの offline コマンドで、Anthropic API
  #   を一切呼ばない。よって API キー / ログインなしで動く。実証: 認証 env を全て外しても正常
  #   manifest は exit 0、壊れた manifest はスキーマエラーで fail する (= 検証が実体を持つ)。
  plugin-validate:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-node@v4
        with:
          node-version: '20'
      - name: Install Claude Code CLI
        run: npm install -g @anthropic-ai/claude-code
      - name: Validate marketplace + plugin manifests (offline schema validation, no auth/network)
        env:
          # CI を hermetic に保つ雑音抑止 (validate 自体は元々 API を呼ばないので認証回避ではない)
          DISABLE_AUTOUPDATER: '1'
          DISABLE_TELEMETRY: '1'
          CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC: '1'
        run: |
          claude plugin validate . --strict
          claude plugin validate ./plugins/<plugin-name> --strict
```

実測 14s で green（secret 不要）。

## 成果物

- `.claude-plugin/marketplace.json` — marketplace マニフェスト（相対パス source、利用者向け description）
- `plugins/<name>/.claude-plugin/plugin.json` — plugin マニフェスト
- `plugins/<name>/skills/**/SKILL.md` — 同梱 skill（`${CLAUDE_PLUGIN_ROOT}` 参照化済み）
- `plugins/<name>/scripts/` — helper script 同梱コピー
- `plugins/<name>/README.md` — 位置づけ（並走 PoC か SOT か）・ローカル検証手順
- CI workflow への `plugin-validate` job 追加（offline 根拠コメント付き）

## 判断基準・閾値

| 基準 | 値 | 根拠 |
|---|---|---|
| validate の CI フラグ | `--strict` | 警告もエラー化。公式 docs が CI 用と明記 |
| 既存配布への影響 | ゼロ（純粋追加） | 既存 ZIP / install 配布を無改変で維持し、cut-over は別タスクに切る |
| description の読者 | 利用者のみ | `/plugin` UI に露出するため。開発者向け情報は README / docs へ |
| offline 実証の形式 | 正常 pass + 壊れた manifest fail の正負ペア | pass だけでは no-op 素通りを排除できない |
| host パス残存 | 0 件 | plugin cache 先で解決できない `~/.claude/...` 等の絶対パスは全て `${CLAUDE_PLUGIN_ROOT}` か絶対 URL に置換 |

## 応用・バリエーション

- **新設リポジトリ（既存配布なし）**: 並走コピーは不要。`plugins/<name>/skills/` を最初から SOT に
  して Step 2 の「機械的書き換えコピー」節を省略する。
- **複数 plugin**: `marketplace.json` の `plugins[]` に追記し、`plugins/<name2>/` を同型で増やす。
  validate は plugin ごとに 1 行追加。
- **GitHub Actions 以外の CI**: 「node 導入 → `npm install -g @anthropic-ai/claude-code` → validate」の
  3 step を移植すればよい。auth / network / secret は不要。

## 注意点

- `claude plugin validate` が offline であることはバージョンアップで変わり得る。CI が突然 fail / 認証を
  要求し始めたら、根拠コメントの実証手順（正負ペア）を再実行して前提を確認する。
- marketplace.json の `source` を GitHub URL 等で書くと同一リポジトリ内 plugin の検証が repo 単体で
  閉じなくなる。同一リポジトリ配布なら相対パス `./plugins/<name>` にする。
- plugin 配下に skill をコピーする並走構成では、canonical 版との drift が起きる。README に「SOT は
  どちらか」「同期する範囲」を明記し、cut-over タスクを別 Issue で追跡する。
- 完了前のセルフレビューゲートに codex exec を使う運用の場合、codex CLI が ChatGPT アカウント auth だと
  全モデル HTTP 400（`not supported ... ChatGPT account`）で実質 unavailable。最初の 1 モデルで 400 を
  確認したら即 skip し、手動セルフレビューで代替した旨を報告に明記する。
