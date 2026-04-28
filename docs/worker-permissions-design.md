# ワーカー権限管理設計（schema-driven worker permissions）

> 関連 Issue: [#99](https://github.com/suisya-systems/claude-org-ja/issues/99)
> ステータス: Phase 1 完了 (PR #169)、Phase 2 完了（本ドキュメントの PR）

claude-org におけるワーカー Claude の `.claude/settings.local.json` 権限管理を、窓口（Secretary）の手書き JSON から **schema-driven な generator + 静的 deny + drift CI** に置き換える設計の根拠と運用ノートをまとめる。

## 背景 / 動機

従来、ワーカーの `.claude/settings.local.json` は **窓口が org-delegate フローの中で手書き JSON として生成** していた。これは構造的に **窓口の判断による permission 過大付与** の余地を残す。

### 具体ケース（2026-04-26）

`worker-strategic-memo-v5-update` を派遣する際、窓口が「念のため」として実需（`additionalDirectories` のみ）を超えて以下を追加していた:

- `Edit(.../docs/internal/**)` のワイルドカード allow
- `Write(...)` 形式の permission

この事象は PreToolUse hook によりブロックされた事象として検出され、retro で表面化した。

### 根本問題

- メモリベースの自己規律（例: `feedback_no_secretary_carveouts`）は **事後対応的** で、人間の判断に依存する
- claude-org の既存 F-d 軸（role_configs schema-driven + drift CI）と **非対称**: role_configs は schema-as-SOT で守られているが、ワーカー権限は手書き JSON のまま
- 「うっかり広く付与する」ことに対する **構造的な障壁が存在しない**

## 提案（5 段階）

### 1. schema 拡張

`tools/role_configs_schema.json` に `worker_roles` セクションを追加:

```json
"worker_roles": {
  "default": { "allow": [...], "additionalDirectories": [] },
  "claude-org-self-edit": { ... },
  "doc-audit": { ... },
  "web-research": { ... }
}
```

各 role は **決め打ちの permission set** を持ち、窓口が ad-hoc に拡張することはできない。

### 2. generator ツール

`tools/generate_worker_settings.py` を導入:

```bash
python tools/generate_worker_settings.py \
  --role doc-audit \
  --worker-dir <WORKER_DIR> \
  --claude-org-path <CLAUDE_ORG_PATH> \
  > $WORKER_DIR/.claude/settings.local.json
```

入力は role 名 + パス変数のみ。出力は schema から決定論的に生成される。

### 3. Secretary PreToolUse hook（と静的 deny）

`workers/*/.claude/settings.local.json` への直接 `Write` / `Edit` を **deny** する:

- 窓口の `.claude/settings.local.json` `permissions.deny` に追加
- 書き換えは generator (Bash 起動) のみ可

### 4. `org-delegate` Step 1.5 移行

現在の手書き JSON 生成手順を generator 呼び出しに置換する。SKILL.md 本文と journal イベントスキーマを更新する。

### 5. drift CI 拡張

`tools/check_role_configs.py` に `--include-worker-settings` を追加。`<workers_dir>/<project>/.claude/settings.local.json` 配置のワーカー（Pattern A 系）を schema に対して検証する。drift = fail。

> **現状の検査スコープ (Phase 1 時点)**: `--include-worker-settings` は `<BASE_DIR>/*/.claude/settings.local.json` のみを走査するため、Pattern B の `<BASE_DIR>/<project>/.worktrees/<task>/.claude/settings.local.json` は未検査。worktree までの recurse 拡張は Phase 3 の課題（後述）。

## メリット（7 項目）

* **権限過大付与の構造的予防**: 窓口は広範な permission を手書きで付与できない
* **再現性**: 同じ role → 同じ permission set（決定論的）
* **schema-as-SOT の延長**: 既存 F-d 軸（role_configs ↔ schema CI）と整合し、Layer 1（core-harness）プリミティブとして抽出可能
* **承認摩擦が schema 編集に集中**: 新 role 追加には schema PR が必要 → user レビューが trace される
* **多層防御を 1 段強化**: hook + tool gate + schema validation + CI = 4 層
* **OSS ポートフォリオの基盤**: claude-org から Layer 1（core-harness）を切り出す際のプリミティブ候補
* **メモリベースの事後規律からの脱却**: 「うっかり」事案を構造的障壁で防ぐ

## デメリット（7 項目）

* **初期実装コスト**: schema 拡張 + generator + hook + skill 改修 + CI ≈ 2-3 PR、合計 1 週間程度
* **新規パターンのワーカー追加に摩擦**: 一度きりの新規タスクでも schema に worker_role 追加が必要 → 緊急対応で遅くなる
* **schema が肥大化する可能性**: role が増えるとメンテコストが上がる
* **escape-hatch 設計が難しい**: 緩い `worker_roles.adhoc` を入れると障壁が崩れ、入れないと緊急対応で詰まる → トレードオフ
* **既存ワーカー派遣フローの更新コスト**: org-delegate Step 1.5 / Step 3 / org-state.md / journal イベントスキーマをすべて新方式に揃える必要
* **デバッグが難しくなる**: `settings.local.json` を直接見ても意図がすぐにわからない → generator ロジックを辿る必要
* **claude-org 自体にも制約がかかる**: dogfood が窮屈になる（メタ再帰）

## Alternatives（代替案）

* **A**: 提案どおり（schema 拡張 + generator + hook + CI、フル）
* **B**: hook 部分を落とす（schema + generator + CI のみ、hook 強制なし） → 部分的な障壁、軽量
* **C**: schema 拡張なし、テンプレートベースの generator のみ → 最軽量、最弱
* **D**: 拒否。memory + retro 強化で凌ぐ（現状維持）

## 推奨

**A（フル提案）** を最終形として推奨。ただし phasing は現実的に:

* **Phase 1**（約 1 週間）: B 相当 — schema 拡張 + generator + drift CI（PR #169 で完了）
* **Phase 2**: hook 強制を追加（A へアップグレード）（本 PR で完了）
* **Phase 3**: escape hatch 設計（例: 限定的な `worker_roles.adhoc`）、drift CI スコープ拡張（Pattern B worktree 配下まで recurse）、運用知見の蓄積（alert 経路や retro 連携）

## Acceptance Criteria

* [x] `tools/role_configs_schema.json` に `worker_roles` セクションを追加（Phase 1）
* [x] `tools/generate_worker_settings.py` を実装（unit test 込み）（Phase 1）
* [x] `org-delegate` Step 1.5 の手書き JSON 部分を generator 呼び出しに置換（Phase 2）
* [x] 窓口設定に `Write(*/workers/*/.claude/settings.local.json)` deny ルールを追加（Phase 2）
* [x] `tools/check_role_configs.py` が `--include-worker-settings` でワーカー `settings.local.json` を schema 検証（Phase 1）。ただし現状は `<BASE_DIR>/*/.claude/...` のみで、Pattern B worktree 配下は Phase 3 で追加予定
* [x] README / 内部ドキュメントに 7 メリット / 7 デメリットを明示（本ドキュメント）

## 関連

* 直接の引き金: 2026-04-26 `worker-strategic-memo-v5-update` の権限拡張事象（PreToolUse hook がキャッチ）
* 関連 memory: `feedback_secretary_generation_time_is_blocking`, `feedback_no_secretary_carveouts`
* 関連戦略ドキュメント: `docs/internal/strategic-analysis-2026-04-26.md` v5 §16（Layer 1 OSS 抽出候補）
* 関連 Issues: #70（PreToolUse hook の段階導入）, #85（role config CI 整合）, #86（fail-closed allowlist）
