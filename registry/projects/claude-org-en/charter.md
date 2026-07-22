# claude-org-en 憲章

## このプロジェクトは何か

claude-org-ja の **英語 auto-mirror**。runtime 系ファイルは機械的にミラーされ、docs / skills は翻訳パスで追随する。機械ミラーである以上、EN 側ローカル資産や EN 固有適応との skew が定期的に CI red を生むのが常態であり、それを直すのがこのプロジェクトの主な仕事である。

## 何をもって良しとするか

- **ja との差分を小さく保つ**こと。EN 適応は必要最小限に留め、将来のミラー diff を増やさない。
- CI red は系統ごとに切り分けてから定型修正を当てること（`notes/mirror-ci-failure-triage.md`）。

## 制約と慣習（非交渉）

### repo 名の罠: origin は `claude-org`（`-ja` なし）

作業ツリー名・プロジェクト名は `claude-org-ja` 系だが、実 origin は
`https://github.com/suisya-systems/claude-org.git` である。`-ja` を付けた GitHub URL は **404 する**。

- URL を書く前に必ず `git remote get-url origin` で実 remote に合わせること。
- v1.0.0 の CHANGELOG で実害が出ている（`-ja` 付き URL を出荷した）。
- **Codex レビューはこの種の誤りを検出しない**（コード正当性の欠陥ではないため）。人間側で担保する。

### EN 適応は意図的に最小限

EN 化するのは **clone URL のみ**。banner / コメント / `TARGET_DIR` は `claude-org-ja` のまま据え置く。
修理ついでに他の文字列を「直さない」— 将来のミラー diff を小さく保つための意図的な判断である。

### reverse-drift 則: ja-canonical ファイルは EN 側で直さない

機械 import された ja-canonical ファイル内に Codex 指摘が出ても、**EN 側で修正しない**（byte-parity と reverse-drift ルールが壊れる）。正ルートは:

1. blob SHA 比較で ja main に同じ欠陥が在ることを確認する
2. Lead に報告する
3. PR の Known limitations に記録する
4. ja 側の Issue を提案する（auto-mirror で修正が還流する）

## Lead 側の段取り（ワーカーの実行設定ではない）

以下はマージゲート / 計画の話であり、実行プロファイルの knob にはしない。1 タスクを実行するワーカーは観測も行動もできないため:

- **マージ直列制約**: EN は branch up-to-date 必須かつ auto-merge 無効。N 件のマージは
  「update-branch → CI 再実行（約 1.5 分）→ merge」× N の直列になる。Lead 側の処理時間として見込む。
- 並列度、失敗ジョブ種別によるワーカー分割の判断。
- 委譲先を決める前の `sync_classifier` によるパイプライン所在判定。
- `CI_COMPLETED indeterminate` の扱い（`knowledge/curated/pr-ci-monitoring.md` を参照。ここに回避策をコピーしない — 挙動自体が修正対象のため陳腐化する）。

## 観測済み・未プロファイルの作業類型

実行プロファイルは「同一類型の 2 回目の実測」で作る（成長則）。以下は 1 回しか観測されていないため、**まだプロファイル化しない**:

- `release`（v1.1.0 昇格。なおタグ発行は Lead の仕事でワーカースコープ外）
- `canonical-name-sweep`

プロファイル済みの類型は `profiles/ci-fix.toml` と `profiles/translation-pass.toml`。

## 台帳の使い方

- 実行設定は `profiles/`、運用知見は `notes/`、手順書は skill。この三分を崩さない。
- `contracts/` は**人間承認済みの独立契約への参照のみ**。実体を置かない（`contracts/README.md`）。
