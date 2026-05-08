# profiles/

worker `.claude/settings.local.json` 形式に直接適用できる **handcraft 候補 sandbox profile**。

- 形式は Claude Code が読む `settings.local.json` のスーパーセットで、`permissions` / `hooks` / `env` に **`sandbox` ブロックを追加**。
- 本 spike 時点の `claude-org-runtime` (v0.1.2) bundled `role_configs_schema.json` には `sandbox` field が **存在しない**ため、これらのファイルをそのまま `claude-org-runtime settings generate` で emit させることは現状できない。**手動で `worker_dir/.claude/settings.local.json` に書き戻して動作確認する用** の handcraft 候補。
- Phase 1 (Issue #378) で runtime 側 schema 拡張 → ja pin window 拡張、の順で正式に schema-driven 化する想定。

## ファイル

- `profile-baseline.json` — **最小防御**。現状の worker template に repo-shared 由来の dangerous-git/no-verify hook と、`.env` / `*.pem` / credentials の sandbox denyRead を追加したもの。Pattern A 想定。
- `profile-tightened.json` — **強化版**。baseline に加えて (a) `git -C` 形式の dangerous-git deny を schema deny に追加、(b) sandbox.filesystem.denyWrite を `~/.claude/`, `~/.ssh/`, `~/.aws/` まで拡張、(c) `additionalDirectories` に `worker_dir` のみ明示。

## 適用方法 (実機検証時のみ。本 spike では適用しない)

1. probe iteration で worker dir を 1 つ用意（このディレクトリでも可）
2. `cp profiles/profile-baseline.json .claude/settings.local.json`
3. **placeholder 置換** (これを忘れると hook command が literal `{claude_org_path}` 等を指して防御層が機能しない):
   ```bash
   sed -i "s|{worker_dir}|<probe worker の絶対 path>|g; \
           s|{claude_org_path}|<claude-org-ja の絶対 path>|g" \
          .claude/settings.local.json
   jq empty .claude/settings.local.json
   ```
4. Claude Code を当該 worker dir で再起動
5. `probes/checklist.md` の対応 row を実行し、観測結果を埋める
6. 必要なら `profile-tightened.json` に切り替え (#2-3 を再実行) して差分を比較

## 注意

- 現行 hook (`block-dangerous-git.sh`, `block-no-verify.sh`) は repo-shared 配備前提なので、worker dir 経由で発火させるためには `command` の path を `{claude_org_path}/.hooks/...` で参照する。これは既存 worker_roles.default の hook 記法と同形なので drift には直接影響しない。
- `sandbox.failIfUnavailable: false` は本 spike でも維持 (bubblewrap 未導入環境で worker 起動が落ちると検証ループが回らないため)。本格運用時の fail-closed 切替は Phase 3 (Issue #380) で別判断。
- `additionalDirectories` は WSL2 上の Claude Code が cwd 以外で write を許可するための補助。Pattern A の worker は cwd === worker_dir なので、`worker_dir` を含めるのは自分自身の write 許可と等価。Pattern B/C で base_repo `.git/` を含める場合は profile を別出しにする (本 iteration では Pattern A のみ)。
