# org-curate — 規律の由来

[`.claude/skills/org-curate/SKILL.md`](../SKILL.md) 本文から移した「なぜこの規律があるか」の記録。規律そのものは本文に残してあり、ここにあるのは由来だけ。手順を実行するために読む必要はない。

## 1. curator の書き込み範囲（Step 1 / Step 4）

curator は `knowledge/raw/` 直下の active entry を書き換えない。書き込めるのは `knowledge/curated/` への新規作成・追記と、`knowledge/raw/archive/` への move（および archive 側ファイルの編集）だけ。正本は [`docs/contracts/knowledge-curation-contract.md`](../../../../docs/contracts/knowledge-curation-contract.md) の Owner / move-then-mark。Step 1 の移行 sweep も書き換えではなく move なので、この制約に触れない。

## 2. マーカーをシェルで付与しない理由（Step 4 手順 3）

「マーカー＋本文を `<entry>.md.tmp` に書いてから `mv <entry>.md.tmp <entry>.md` で上書き」というシェル流儀は、2 つの環境要因で壊れ、17 byte 壊れた原本と `.md.tmp` 残骸が 5 件たまったことがある。

- `mv` が `mv -i` に alias されていると、既存ファイルへの上書き `mv` が対話確認を出し、無人で走る curator では EOF で拒否される。マーカー無しの原本が残り、`.tmp` だけが残骸になる。
- zsh の history expansion がマーカー文字列中の `!` を解釈し、`<\!-- curated -->` のように `\` が混入する。

Write ツールでの read → prepend → write は `mv` を使わず `!` もシェルに通さないので、両方を断てる。`command mv -f` は allowed-tools（`Bash(mv knowledge/raw/*)` 系のみ）に無く、無人実行では許可プロンプトで止まるため、Write ツール経路を既定にしている。
