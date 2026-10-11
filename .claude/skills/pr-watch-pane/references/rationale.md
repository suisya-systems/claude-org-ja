# pr-watch-pane — 規律の由来（経緯・実測ログ）

[`.claude/skills/pr-watch-pane/SKILL.md`](../SKILL.md) 本文から移設した「なぜこの規律があるか」の記録。規律そのもの（MUST / 禁止 / 条件）は本文に残してあり、ここにあるのはその由来（どの事故で・いつ・どの Issue で決まったか）と理由の説明だけ。**手順を実行するために読む必要はない**。

目次:

1. 自己 close が効かない環境のゾンビ残留
2. なぜ固定アンカーをやめたか
3. env 前置注入の由来
4. `nocorrect true 2>/dev/null || true;` 前置の実証
5. 全画面 inspect と副作用ベース生存確認の由来
6. 裸 name close の carve-out が契約から導かれる経緯
7. pre-capability renga の legacy 解決が作る誤 close hazard
8. peer push が best-effort である理由
9. 自己 close の 2 分類（backend 別）
10. self-close で socket を固定しない理由

## 1. 自己 close が効かない環境のゾンビ残留（前提 節 / Issue #751 / Issue #911）

実測は 2 系統: 2026-07-22 に PR #154 / #749 / #750 の watcher 3 枚が herdr backend で残留し `close_pane`(id 指定) で掃除した件と、**2026-08-09 の PR #908 監視で renga のペインが残留し窓口が `close_pane` で手動掃除した件**（renga ペインでは `printenv TMUX TMUX_PANE` がどちらも未設定を返す ＝ `kill-pane -t ""` に展開されて無言失敗する）。

self-close の無言失敗と違い、**残留が報告として可視化される**のが窓口イベント駆動 close への載せ替えの眼目である。

## 2. なぜ固定アンカーをやめたか（Step 3-0 / Step 3-1 / Refs #335）

旧版は `target="dispatcher"` 固定だった。2026-08-30、幅 24 まで細った dispatcher に張ろうとして `[split_refused]` になり、397x53 の secretary ペインが丸ごと空いているのに窓口が「タブ満杯」と誤読して worker 派遣を待機させる実害が出た。`[split_refused]` は**その target を割れないという局所の事実**であって、タブ全体の容量を語らない。よって張り先は毎回 `list_panes` の幾何から決定的に選び直す（renga 側のコード分離は suisya-systems/renga#335）。

direction を窓口が決めない規律も同じ日の実害由来: 2026-08-30 に 397x53 の secretary へ `horizontal` で張って窓口ペインを 397x13 まで潰した（幅広の rect では `vertical` が選ばれる）。

secretary を張り先に使うのはユーザー承認済み（2026-08-30）。

## 3. env 前置注入の由来（Step 1 / Step 3-1 / Refs #653 #658 / Refs #941 / PR #73）

env 注入が無いと spawn されたペインの `tools/peer_notify.py` が `ORG_TRANSPORT` 未設定で silent no-op に落ちる。これが PR #73 障害の直接原因だった: broker queue に `CI_COMPLETED` が 1 件も入らず窓口が idle のまま気付かなかった（events テーブルへの `ci_completed` 書き込みは成功するのに窓口へ届かない）。

`ORG_TRANSPORT` を空 / 未設定なら export ごと省く規律の理由付けは Refs #941 で書き換わっている: `peer_notify` は `tools/transport.py: resolve()` で分岐するので、未設定を渡された watcher ペインは窓口ペインと同じく `DEFAULT_TRANSPORT`（= `broker`）へ解決する。**かつての「未設定のまま渡す ＝ renga にフォールバック」という理由付けは失効している** — 未設定は今や renga ではなく broker を意味する。renga を使いたい場合は `ORG_TRANSPORT=renga` が窓口ペインで**明示**されているはずで、その値はこの前置注入でそのまま複製されるため、opt-in 経路は引き続き壊れない。

多層防御の位置づけ: この env 注入は **path A（低遅延 push）の修理**であって単独の保証ではない。仮に注入漏れ / push 失敗が起きても、(B) ディスパッチャーの `event_deliveries` outbox relay が `ci_completed` 等の canonical event を直接 scan して窓口へ確実に relay するため「見逃しゼロ」は成立する（[`.dispatcher/references/worker-monitoring.md`](../../../../.dispatcher/references/worker-monitoring.md) の relay scan ステップ参照）。さらに push が失敗した場合は `pr_watch` が `notify_failed` イベントを fail-loud で記録する（silent no-op の全廃）。

## 4. `nocorrect true 2>/dev/null || true;` 前置の実証（Step 3-1 / Issue #825）

correctall による無言停止は 2026-08-06 の PR #824 監視で実発生・再現確認済み。行頭の `nocorrect` が zsh の precommand modifier（reserved word）としてパース前に解釈され、その行全体の全 word のスペル訂正を無効化することは zshmisc PRECOMMAND MODIFIERS「interpreted immediately, before any parsing is done」に拠り、tmux 上の対話 zsh 5.9 で `;` 越しの抑止を実機確認済み。

3 要素それぞれの実証:

- **犠牲文として独立させる**: `nocorrect export ...` と payload に直結すると、bash 等が login shell の pane では `nocorrect: command not found` で export 文ごと失敗する（bash 実機で export 消失を確認済み）。非対話シェル実行（`zsh -c` 等）でも no-op で副作用はない（zshmisc「It has no effect in non-interactive shells」）。
- **`2>/dev/null`**: bash で無音 + 後続 export 生存、zsh 対話で correctall 抑止に影響なしを実機確認済み。
- **`|| true`**: errexit 有効 bash で後続実行の生存、zsh 対話で correctall 抑止に影響なしをともに実機確認済み。

3 要素それぞれが省略できない理由（SKILL.md Step 3-1 から移設）:

- **payload を持たない犠牲文として独立させる理由（非 zsh 互換）**: `nocorrect` は zsh 以外の
  シェルにコマンドとして存在しない。`nocorrect export ...` と payload に直結すると、bash 等が
  login shell の pane では `nocorrect: command not found` で **export 文ごと失敗**し、transport
  env 注入（Step 3-1 の env 前置注入）が silent に失われる。単独文
  `nocorrect true 2>/dev/null || true;` なら zsh では行全体の訂正抑止・bash 等では犠牲文
  だけが無音で失敗し、`;` 以降の後続は全て実行される。非対話シェル実行（`zsh -c` 等）でも
  no-op で副作用はない。
- **`2>/dev/null` は省略不可（Step 4 の誤殺防止）**: 非 zsh シェルでは犠牲文が
  `nocorrect: command not found` を stderr に出す。この行が画面に残ると Step 4 の
  negative-signal 判定（`command not found` = 起動失敗）に誤マッチし、**正常起動した
  watcher を健全なまま close する false positive** になるため、犠牲文の stderr は行内で
  捨てる。
- **`|| true` も省略不可（errexit 耐性）**: 非 zsh シェルの rc で `set -e`（errexit）が
  有効だと、犠牲文の失敗（status 127）でシェルごと終了し **export / mkdir / watcher 本体が
  一切走らないまま pane が死ぬ**。`|| true` で犠牲文を非致命化する。

## 5. 全画面 inspect と副作用ベース生存確認の由来（Step 4 / Issue #825）

2026-08-06 の correctall 停止は、末尾 15-40 行だけの読み取りで「画面上部の確認プロンプト + 下部空白」を静かな正常起動と誤読して見逃された。

同じインシデントで `pgrep -f "pr-watch"` の偽陽性も起きている: spawn されたペインのラッパーシェルの argv にコマンド全文が乗るため、確認プロンプト停止中でパイプラインが一度も走っていなくてもシェル自身にマッチし、2026-08-06 にこの誤認で「監視本体が稼働中」と誤読した。

## 6. 裸 name close の carve-out が契約から導かれる経緯（Step 5 (b) / 契約 T-§4.2）

契約 [`docs/contracts/backend-interface-contract.md`](../../../../docs/contracts/backend-interface-contract.md) T-§4.2 の Group B 台帳は stale-binding 行を「数値化だけでは discharge できない」とし、follow-up が **使った mechanism を台帳に記録する**ことを求めている。本 skill が採る mechanism がこの allowlist である。契約 T-§4.2 の carve-out 自体も条件を "the backend resolves Group B in a single-tab model" と **backend の性質**で書いており、env 変数の綴りでは書いていない。

判定に積極証拠を要求し `DEFAULT_TRANSPORT` からの推定を禁じる理由:

- `ORG_TRANSPORT` 無設定は**運用既定 renga の構成でもありうる**（root [`CLAUDE.md`](../../../../CLAUDE.md)「輸送層（transport）両系」の二フレーム注記 — 運用既定 renga / コード既定 broker は指す対象が違う）。一方 [`tools/transport.py`](../../../../tools/transport.py) の `resolve()` は無設定を**コード既定 `broker` に解決する**。したがって「無設定 → resolve() → broker」でこの条件を通すと、**実際には renga が Group B を駆動している環境で裸 name の `close_pane` を撃つ**ことになり、pre-capability renga では他タブへフォールスルーして**別 org の同名 watcher を不可逆に閉じうる** — この carve-out がまさに防ぐための hazard を、carve-out 自身が引き起こす。
- **失敗方向が非対称**: 証拠を要求して外すと「自己回復が発火せず人手待ち」（不便・可逆）で済むが、推定して外すと「別 org のペインを不可逆に close」になる。安全側は前者しかない。
- Step 1 が `printenv` の結果で `export ORG_TRANSPORT` を省くのは、[`tools/peer_notify.py`](../../../../tools/peer_notify.py) に窓口ペインと同じ解決を継がせるため（同 helper は Refs #941 で raw env 判定をやめ `resolve()` 経由になった）で、判定基準の異なる別論点である。**この carve-out の MUST NOT は影響を受けない** — 上の禁止は「`resolve()` の結果から Group B の駆動系を推定するな」であって、`peer_notify` がどう分岐するかとは独立である。

## 7. pre-capability renga の legacy 解決が作る誤 close hazard（Step 5 (a)/(b)）

pre-capability の renga では `list_panes` が**フォーカス中**のタブに解決し、`pr-watch-<PR>` は 2 org 並走で構造的に衝突するので、照合を通っても**別 org の同名 watcher**を閉じうる。

裸 name の場合はさらに悪く、renga の legacy 解決は active タブ（＝ユーザーが見ているタブ）を先に引き、miss したら他タブを index 順にフォールスルーして先勝ちする。したがって「live pane が無いので誤 close の余地が無い」という前提自体が `list_panes`（＝ユーザー可視タブ）からしか立てられず、別タブに同名の live pane が居れば前提は偽で、close はそのペインに当たる（`close_pane` は不可逆でエラーも出ない）。

Step 5 (a) でも自タブ確立が要る理由（SKILL.md Step 5 から移設）:

- **(a) は「live pane が列挙に出るから安全」ではない**: (b) が carve-out を要するのは
  *列挙から数値 id を取り直せない*ためであって、(a) が数値 id を取れることは MUST の
  **片方**（相対セレクタでないこと）を満たすにすぎない。もう片方の「その列挙が自タブの
  ものか」は (a) でも別に確立しなければならず、未確立の列挙では `name` / `role` 照合の
  **結果そのもの**を信用できない（renga の legacy 解決が作る誤 close hazard の機序は上記）。

## 8. peer push が best-effort である理由（冒頭の注記）

SKILL.md 冒頭の注記から移設した全文:

> **peer push は best-effort**: `pr_watch.py` は CI 確定・マージ時に窓口へ `CI_COMPLETED` /
> `PR_MERGED` の peer message を送ろうとするが、これは `tools/peer_notify.py` 経由の
> best-effort（broker send CLI 不在 / 解決先 transport が未構成の pane では
> no-op）。daemon が非既定 state dir（herdr dogfood 等）で動く環境では、pane env に
> `ORG_BROKER_STATE_DIR` が無いと broker send が既定 `.state/broker` を掴んで push が欠落する
> （欠落しても canonical の events DB 行には影響しない）。**待つべき正路は上記 events DB 行と
> 可視ペイン**であり、push の到達を merge gate の
> 前提にしない（org-pull-request の CI/merge gate は events DB を一次ソースにする）。

## 9. 自己 close の 2 分類（前提 節）

SKILL.md「前提」節から文言を変えずに移設した全文（Step 5 (a)/(b) の手順はこの分類を前提にする）:

- **自己 close は tmux ペインの低遅延経路であり、watcher ペインの実体が tmux ペインかどうかで
  効き方が変わる（herdr ゾンビ残留の根治, Issue #751 / renga 残留, Issue #911）**。broker は
  backend として tmux / herdr / wezterm を取りうる（解決済み backend は `daemon.json` sidecar が
  持つ。契約は
  [`docs/contracts/backend-interface-contract.md`](../../../../docs/contracts/backend-interface-contract.md)
  Surface 8）。**opt-in `renga` のペインは tmux ペインではない**ので、transport の綴りではなく
  「ペインの実体」で次の 2 分類に読み替える:
  - **自己 close が効く = broker の tmux backend**: Step 3 末尾の `tmux kill-pane -t "$TMUX_PANE"`
    が実ペインを即座に消す ＝ **監視終端で watcher ペインが自動 close される低遅延経路**
    （従来挙動。温存する）。
  - **自己 close が効かない = broker の herdr / wezterm backend（Windows native broker の別 GUI
    ウィンドウ・tmux 非経由を含む）、および opt-in `renga`**:
    watcher ペインは tmux ペインではないため `tmux kill-pane` が **no-op**（`|| true` で握り潰され
    silent）。self-close が効かず、**監視終了後もペインがゾンビとして残留する**（herdr / renga
    それぞれの実測ログは §1）。この分類では自己 close に頼れないため、
    **監視終端で窓口がイベント駆動で watcher ペインを close する経路が正路**になる
    （[`.claude/skills/org-pull-request/SKILL.md`](../../org-pull-request/SKILL.md) の post-merge
    cleanup / `PR_MERGE_WATCH_TIMEOUT` / CI 失敗確定の各終端で窓口が発火。掃除手順は SKILL.md Step 5 の
    (a)/(b) split に従う ＝ live pane は list_panes 確認済みの数値 pane_id で close、stale binding
    のみ broker 条件付き allowlist で name 指定）。`close_pane` は transport 抽象上 tmux / herdr /
    renga いずれでも呼べ、broker の tmux backend で既に
    self-close 済みなら `[pane_not_found]` が返る（自己クローズ済みで正常）。
    - **renga では窓口 close も無条件には撃てない**: Step 5 (a) の自タブ確立は **(i) backend が
      Group B を自身の single-tab モデル内で解決する（＝ `org-broker`）/ (ii) `caller_scope` を
      確立できている**のいずれかを要し（契約 T-§4.2「Fail-safe consequence for Group B」/ T-§cap）、
      renga では (i) が成立しないため **(ii) を確立できない限り close を撃たず、watcher ペインが
      残る旨を報告して人手掃除に委ねる**（相対セレクタへはフォールバックしない）。

SKILL.md Step 5-3 冒頭にあった同じ分類の再掲（renga では (a) の自タブ確立が `caller_scope` 依存で、確立できなければ close を撃たず残留を報告する ＝ 無言残留を可視な残留に変えるところまでが本経路の効果）も、上の分類と §1 に吸収した。

## 10. self-close で socket を固定しない理由（Step 3-1）

SKILL.md Step 3-1 から文言を変えずに移設:

- **socket は `$TMUX` 継承をそのまま使う（`-L` で固定しない）**: `tmux` を `-L` 無しで
  起動すると、ペインが属する tmux server の socket を `$TMUX` から自動解決する。broker では
  それが `claude-org-broker` socket、非既定 socket 構成では別 socket になるが、いずれも
  `$TMUX` が正しい server を指すので kill が当たる。ここで `-L claude-org-broker`
  と固定すると非既定 socket 下で **別 server に当たって self-close が無言で失敗**
  するため、socket は固定しない（renga では `$TMUX` 自体が無いので、そもそもこの経路に乗らない）。
