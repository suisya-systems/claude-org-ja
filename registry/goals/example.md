# example-org/example-repo のゴール（記入例）

このファイルは記入例で、台帳としては読まれない（`registry/goals/` 直下のファイルは読まれない）。
実際の台帳は `registry/goals/<owner>/<repo>.md` に置く。例: `registry/goals/example-org/example-repo.md`。

## G1 利用者がセットアップで詰まらない
初回セットアップが README の手順だけで完了する。

- unmet if: README の手順どおりに進めて失敗する報告が open のまま残っている
- unmet if: セットアップ手順に手作業の設定ファイル編集が残っている

## G2 リリースが週 1 回出せる
- unmet if: main の CI が赤のまま 1 日以上放置されている
- unmet if: リリース作業に手動の手順が 3 つ以上ある
