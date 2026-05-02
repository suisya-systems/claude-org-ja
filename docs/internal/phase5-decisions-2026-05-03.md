# Phase 5 (Layer 3 = orchestration glue) — Lead 回答記録

- 日付: 2026-05-03
- 関連 Issue: ja#（Layer 3 抽出可否、未起票）
- 入力 doc:
  - `docs/internal/phase5-questions-2026-05-03.md`（12 問の Q&A 草案）
  - `docs/internal/phase4-decisions-2026-05-02.md`（Layer 2 決定との整合確認）
- セッション: Lead live Q&A（2026-05-03）にて 12 問の回答を確定。
- 性質: Lead 判断の **永続化レコード**。Phase 4 と同様に、本 doc 1 枚を後続 worker / Lead が参照すれば Phase 5 の進路（kill / defer / extract）が確定する状態を目的とする。

---

## Phase 5 status: **deferred (kill gate 未評価)**

- Q1=b により、Layer 3 抽出は **現時点で kill にも extract にも進めず保留**。
- **Next action**: 2026-08-03 頃（= 本決定から 1 quarter 後）に、Q2=a で挙げた measurement (i) consumer 候補 inventory + (ii) skill churn を取得し、Q1 の kill 判定を再評価する。
  - (i) 実施主体: Lead 手作業（gh search + Slack 観察、想定 30 min）。
  - (ii) 実施主体: worker 派遣（`git log --since=...` 集計、想定 半日）。
  - 自動 routine（cron / scheduled agent）は不要と判断。
- 再評価で proceed が出た場合は本 doc Q3〜Q12 の回答（narrow scope = `org-delegate` + `org-start` の 2 skill、`claude-org-skills` を MIT で GitHub Release のみ publish 等）が **そのまま設計入力** となる。kill が出た場合は本 doc が結論として残り、Layer 3 は claude-org-ja に永続的に残置する。

---

## English summary (one paragraph)

Phase 5 (Layer 3 = orchestration glue) extraction is **deferred**. The Lead chose Q1=b: defer the kill / extract decision by one quarter and revisit on or around 2026-08-03 after taking the two measurements that matter most — (i) inventory of real third-party consumers of `.claude/skills/org-*`, and (ii) churn of `.claude/skills/` over the past six months — under Q2=a. If the kill gate clears, Layer 3 will be extracted with a **narrow scope** (Q3=a): only `org-delegate` and `org-start` as the minimal "worker dispatch reference". The same Layer-2-style breaking policy applies (Q4=a, "0.x breaking allowed"); prompts stay in Layer 2 with claude-org-ja keeping its Japanese-rich `.dispatcher/` / `.curator/` files as consumer-side overrides (Q5=a); org-shaped hooks (Q6=b) and the dashboard (Q7=a, upholds Phase 4 Q9=c) stay in claude-org-ja; the Layer 2 enum catalog remains the single source of truth for events / states (Q8=a); the new Layer 3 repo becomes the SoT with ja/en as consumers (Q9=a, mirrors Phase 4 Q8=a); claude-org-ja remains as a "thin shim + 内部試験場" (Q10=b) — the Phase 5 DoD = `claude-org-skills` v0.1 release **plus** one merged ja PR that rewrites `.claude/skills/org-delegate` and `org-start` as override / consumer wrappers. Distribution is **public OSS via GitHub Release only** (no PyPI publish), consumed via git submodule / subtree / `pip install git+...` (Q11=b), under the name `claude-org-skills`, MIT license, governance shared with Layer 1 / Layer 2 / claude-org-ja under a single Lead-as-maintainer model (Q12=a).

---

## 各 Q への回答

### Q1. 抽出根拠の測定 — kill ゲート

- **回答**: **(b)** defer。1 quarter 後（= 2026-08-03 頃）に measurement (i) + (ii) を取得して kill / proceed を再判定。現時点ではどちらの結論にも進めない。
- **根拠**:
  - Layer 1 (core-harness) と Layer 2 (claude-org-runtime) には「framework primitives」「runtime SoT」という明確な抽出動機があったが、Layer 3 = orchestration glue は **積極理由が現時点で曖昧**。consumer 候補も churn 実績も未測定の状態で kill (a) に倒すと「測らずに諦めた」記録になり、proceed (c) に倒すと「需要なき extract」になる。
  - Layer 2 v0.1 release が glue 層需要に与える影響（dispatcher_runner が PyPI 化された後、第三者が skill 層も欲しがるか）が読めない時期なので、measurement の前に判断を切る合理性が乏しい。
  - 1 quarter は Layer 2 v0.1 が世に出て consumer の反応が見え始めるのに必要十分な期間として設定。
- **トレードオフ**: 3 ヶ月間 Layer 3 の進路が宙吊りになる。ただし claude-org-ja の運用は現状維持で問題なく、機会損失は限定的。
- **DoD 接続**: Phase 5 そのものの DoD は Q10 で確定（後述）。本 Q では「2026-08-03 頃の再評価セッション開催」が次の milestone。

### Q2. measurement-first の具体プラン

- **回答**: **(a)** (i) consumer 候補 inventory + (ii) skill churn を最優先で取り、Q1 kill 判定を下す。kill されなかった場合のみ (iii)〜(viii) を extract 着手後に追加で取る（Phase 4 Q12=b 流派）。
- **根拠**:
  - Q1=b の判定材料として必要なのは「需要 (i)」と「不安定度 (ii)」の 2 軸のみ。残り 6 指標（dependency graph / prompt diff / hooks 改訂 / dashboard 利用 / ja↔en drift / 残余 LOC 予測）は **設計判断には効くが kill 判定には効かない**。kill 判定前にすべて取るのは worker 負荷の無駄。
  - (b) 全測定は Layer 3 の不確実性に対しては richer information を提供するが、kill が出た場合 (iii)〜(viii) は完全に sunk cost になる。
  - (c) (i) のみで proceed まで決め切るのは churn 情報なしに「stable / unstable」が判断できないため拙速。
- **実施主体**:
  - (i) consumer 候補 inventory: **Lead 手作業**（gh search + Slack 観察、想定 30 min）。worker 派遣するほどの作業量ではなく、判断基準（「実際に読んでいる」の閾値）が Lead 内にしかないため。
  - (ii) skill churn: **worker 派遣**（`git log --since=2026-02-03 -- .claude/skills/` 集計 + 追加/削除/変更行数の skill 別分類、想定 半日）。機械的集計のため worker 適性が高い。
  - 自動 routine（cron / scheduled agent）は **不要**。1 quarter 後の単発測定であり、定常 monitoring の対象ではない。
- **DoD 接続**: 2026-08-03 頃の再評価セッションまでに (i) Lead 手作業 + (ii) worker 報告 doc が揃っていること。

### Q3. 抽出する場合のスコープ境界

- **回答**: **(a)** narrow。`org-delegate` + `org-start` の 2 skill のみを起点とする。最小可動部 = 「ワーカー派遣の reference」。
- **根拠**:
  - Q1=b で defer している前提だが、proceed した場合の起点は narrow に倒す。理由は Layer 2 (Q1=c wide) と思想を分けるべきだから: Layer 2 の最小可動部は「役割が立ち上がる reference 一式」だが、Layer 3 の最小可動部は「**1 ユースケースが手で動く** reference」で十分。
  - wide (b) で 10 skill + dispatcher/curator prompts + dashboard + hooks をまとめて extract すると、kill 判定が defer された段階の不確実性に対して投資が大きすぎる。
  - split (c) で 3 リポに割るのは consumer がまだ 0 の段階で release cycle を 3 つ管理するコストが正当化できない。narrow が後から拡張する余地を残し、wide / split に進むのは v0.1 release 後の measurement 次第とする。
- **トレードオフ**: `org-curate` / `org-suspend` / `org-resume` 等を使いたい第三者は claude-org-ja を直接参照するか、独自 port を維持することになる。narrow scope の段階では許容。
- **DoD 接続**: Phase 5 v0.1 = この 2 skill が外部 consumer の `.claude/skills/` に置かれて Claude Code に直読される状態。

### Q4. API 安定性ゲート

- **回答**: **(a)** Layer 2 と同じ「0.x 期間 breaking 許容」流派。
- **根拠**:
  - Q3=a で skill 数が 2 に縮小されるため、per-skill semver (c) はナンセンス（2 skill で個別 release cycle を持つ over-engineering）。
  - Layer 2 を exact pin する (b) は Layer 2 自身が 0.x breaking 許容（Phase 4 Q2=b）のため、Layer 3 だけ厳格にしても整合しない。Layer 2 を bump するたび Layer 3 を手で追従するコストが per-skill semver と同質の負荷を生む。
  - Layer 2 と Layer 3 の breaking が同一リズムで動くことで、consumer 側（claude-org-ja / 第三者）の追従コストも 1 軸に集約される。
- **トレードオフ**: Layer 2 の breaking が即 Layer 3 に波及するため、Layer 3 単体での「安定保証」は提供できない。0.x の段階では許容。
- **DoD 接続**: v0.1 release 時に CHANGELOG に "0.x: breaking changes allowed (mirrors claude-org-runtime policy)" を明記。

### Q5. Phase 4 で bundle 済みの prompt template との関係整理

- **回答**: **(a)** prompt は Layer 2 にすべて寄せる。Layer 3 は narrow (Q3=a) のため prompt template を持たない。claude-org-ja の `.dispatcher/CLAUDE.md` / `.curator/CLAUDE.md`（日本語 rich）は Layer 2 英語 reference の **consumer-side override** として残る。
- **根拠**:
  - Q3=a で `org-delegate` + `org-start` の 2 skill だけ extract する以上、dispatcher / curator の prompt template は Layer 3 のスコープ外。Phase 4 Q5=b（Python runner + 英語版 prompt template を Layer 2 にバンドル）が SoT として既に確立しているので、Layer 3 で再度 prompt を持つ二重所属を避ける。
  - (b) Layer 2 minimal + Layer 3 rich は Layer 2 を「reference として弱い」状態に格下げすることになり、Phase 4 Q1=c（wide MVP）と矛盾する。
  - (c) Phase 4 Q5 の見直しは Phase 4 決定を遡って書き換えるコストが高く、現時点で正当化する measurement もない。
- **トレードオフ**: Layer 2 の英語 reference prompt を「最小起動可能」と「ja の rich 版」の両方の役割で使い回す形になるが、Phase 4 Q1=c の wide scope 解釈と整合する。
- **DoD 接続**: Phase 5 では prompt template を一切扱わない。Layer 2 v0.1 リリース後の prompt 改訂は Phase 4 ライン側の責務。

### Q6. org-shaped hooks の去就

- **回答**: **(b)** claude-org-ja 残置。`block-workers-delete.sh` / `block-dispatcher-out-of-scope.sh` / `block-org-structure.sh` / `block-git-push.sh` の 4 ファイルは Layer 3 に持ち込まない。
- **根拠**:
  - これら hooks は `registry/org-config.md` / `.dispatcher/` / `.state/` 等の **物理 path に強く結び付いて** いる。Layer 3 で抽出するには path 抽象化レイヤ（Layer 2 が org-shaped hooks の生成 / 検証 API を提供する Q6 選択肢 (c)）が前提となるが、Q3=a の narrow scope と整合せず over-engineering。
  - (a) Layer 3 に skill と一緒に含めると、Layer 3 が claude-org-ja の物理構造を前提にする状態になり「reference として一般化された skill」と矛盾する。
  - (c) Layer 2 に hooks 抽象 API を入れる案は Layer 2 v0.1 のスコープを膨らませるため、現段階では棄却。将来 hooks の OSS 化需要が顕在化した時点で再検討する余地はある。
- **トレードオフ**: 第三者 consumer が org-shaped hooks 相当の安全策を欲しがった場合、claude-org-ja を参考に各自実装する必要がある。narrow scope の段階では許容。
- **DoD 接続**: Phase 5 では hooks を Layer 3 リポに含めない。claude-org-ja の `.hooks/` 配下構造は変更しない。

### Q7. dashboard の去就 — Phase 4 Q9=c との接続

- **回答**: **(a)** Phase 4 Q9=c を維持。dashboard は claude-org-ja に残し、Layer 3 は dashboard を持たない。
- **根拠**:
  - Phase 4 Q9=c で「dashboard SPA は claude-org-ja、Layer 2 は schema (`org-state.json`) のみ」が確定済。Phase 5 で書き換える積極理由はない（Q3=a narrow との整合、en port 側の独自 dashboard との二重所属回避）。
  - (b) dashboard を Layer 3 に統合すると、Phase 4 決定の書き換え + Layer 3 release cycle が SPA build に律速される + en port との重複を Layer 3 が抱える、の 3 重コスト。
  - (c) Layer 3.5 = `claude-org-dashboard` リポを別出しする split 案は Q3=c と同じ理由で棄却（consumer 0 段階で release cycle を増やさない）。
- **トレードオフ**: dashboard 改修と skill 改修が別 PR / 別リポに分かれることで、両方を触る変更（schema 拡張等）の coordination コストが発生。Layer 2 schema 経由で疎結合化されているので致命傷ではない。
- **DoD 接続**: Phase 5 では dashboard 関連ファイル（`dashboard/` 配下）を Layer 3 リポに含めない。

### Q8. State / event catalog の正規化 — どこを SoT にするか

- **回答**: **(a)** Layer 2 enum がすべての SoT。Layer 3 narrow 2 skill が必要とする event は既に Layer 2 catalog (Phase 4 Q7=a の `Enum` + JSON schema、35 種カタログ) に揃っている。
- **根拠**:
  - Phase 4 Q7=a で「workflow_status / journal event / anomaly kind を Python `Enum` + JSON schema で固定」が確定済。Layer 3 の `org-delegate` + `org-start` が呼ぶ event（worker_dispatched, worker_started 等）は Layer 2 の 35 種カタログに包含されている前提（measurement 上は (iii) skill 間 dependency graph で要確認だが、kill 判定後の確認で十分）。
  - (b) plugin event 機構は Layer 2 に `register_event(name, schema)` API を追加する必要があり、Q3=a の narrow との不整合 + Layer 2 の API surface 拡張で v0.1 がさらに膨らむ。
  - (c) Layer 3 内 string 緩い運用は Phase 4 Q7=a の Enum 化決定を Layer 3 内で部分的に巻き戻す形になり、SoT 矛盾を生む。
- **トレードオフ**: Layer 3 で新 event が必要になった場合、Layer 2 PR を経由する 2 step 追加コストが発生。narrow scope の段階では新 event 需要は低い見込み。
- **DoD 接続**: Phase 5 で新 event を導入しない。proceed 時に Layer 2 catalog の不足が判明したら Layer 2 v0.x bump で吸収。

### Q9. ja↔en 同期戦略との接続 — Layer 3 の SoT

- **回答**: **(a)** Layer 3 リポを新 SoT、ja / en は consumer。skill は英語 SoT、ja は日本語訳 consumer（Phase 4 Q8=a と同流派）。
- **根拠**:
  - Phase 4 Q8=a で「`claude-org-runtime` を新 SoT、ja / en は consumer に降格」が確定済。Layer 3 でも同じ路線を採らないと、Layer 2 と Layer 3 で SoT 階層の解釈が分裂する。
  - (b) claude-org-ja を Layer 3 SoT として残す案は Phase 4 Q8=a で却下した case を Layer 3 で復活させることになり整合しない。
  - (c) bilingual SoT は ja / en を両方 SoT 扱いする fork-and-sync 方式で、translate コストが mirror 方式の 2 倍に膨らむ。Phase 4 で却下した発想を Layer 3 で採用する積極理由がない。
  - claude-org-ja の `.dispatcher/CLAUDE.md` / `.curator/CLAUDE.md`（日本語 rich）は Q5=a により consumer-side override として残るので、ja の日本語性は維持される。
- **トレードオフ**: skill の SoT が英語に動くため、ja に直接日本語で書き加える運用は禁止になる（必ず Layer 3 英語 SoT 経由）。#171 auto-mirror の射程を Layer 3 まで広げる必要があるかは別途検討（Phase 4 未決事項 §3 と同じ未決）。
- **DoD 接続**: Layer 3 リポの skill ファイルは英語 Markdown。ja consumer は override / 翻訳 layer を `.claude/skills/org-delegate` 等に配置。

### Q10. Phase 5 抽出後に claude-org-ja に残るもの (DoD)

- **回答**: **(b)** thin shim。抽出後 claude-org-ja は narrow 抽出 (Q3=a) のため大半が残る（残置: 8 skill (`org-curate` / `org-dashboard` / `org-resume` / `org-retro` / `org-setup` / `org-suspend` / `skill-audit` / `skill-eligibility-check`) + dashboard + hooks + 日本語 rich prompt）。
- **Phase 5 DoD**: 以下 2 つが揃った時点で Phase 5 完了。
  1. `claude-org-skills` v0.1 release（`org-delegate` + `org-start` の英語 SoT 版が GitHub Release として配布される、Q11/Q12 参照）。
  2. claude-org-ja の `.claude/skills/org-delegate` + `.claude/skills/org-start` が **override / consumer 構造に書き換わった PR が 1 件 merge** される。
- **根拠**:
  - Q3=a narrow を採った時点で claude-org-ja は「ほぼそのまま残る」状態になるため、(a) "live demo + 知識ベース"（runtime も skill も持たない）は narrow scope と矛盾する。10 skill のうち 8 が残るので "thin shim" 表現が実態に近い。
  - (c) claude-org-ja archive 化は narrow scope の段階で打つには過激。後継 `claude-org-reference` を立ち上げるコストも正当化できない。
  - DoD として「v0.1 release + ja 1 PR merge」を取るのは Phase 4 Q11=b（in-tree 置換まで）と対称的に、「extract したけど誰も使っていない」状態を防ぐため。
- **トレードオフ**: claude-org-ja の `.claude/skills/` は extract 済みの 2 skill と未 extract の 8 skill が混在する状態になり、構造の一貫性が一時的に崩れる。narrow scope の進化過程として許容。
- **DoD 接続**: 上記 2 条件が達成された時点で Phase 5 close。

### Q11. 公開 OSS 化判断 (1/2) — 公開形態

- **回答**: **(b)** public OSS、GitHub Release のみ（PyPI publish しない）。consumer は git submodule / git subtree / `pip install git+...` で取り込む。
- **根拠**:
  - skill / prompt は **Markdown ファイルが filesystem に置かれて Claude Code に直読される** consumption pattern。PyPI wheel に同梱して `importlib.resources` 経由で取り出す (a) は「ファイルが置かれる」consumption と整合せず、PyPI 経由の意味が薄い。
  - (c) private GitHub repo は en port が既に public OSS の前提では実効性が低い（en port から実装は推測可能）し、Layer 1/2 の public OSS 路線とも分裂する。
  - GitHub Release のみで `pip install git+https://github.com/suisya-systems/claude-org-skills@v0.1.0` のような取り込み口を出せば、submodule / subtree 派にも対応できる。Phase 3 Q10=A の流派と整合。
- **トレードオフ**: PyPI search からの discoverability が落ちる。narrow scope の段階で discoverability 投資の優先度は低い。将来需要があれば PyPI publish に切り替える余地はある（Markdown wrapping wheel の追加で対応可能）。
- **DoD 接続**: GitHub Release v0.1.0 の存在 = Q10 DoD 条件 1 の判定基準。

### Q12. 公開 OSS 化判断 (2/2) — namespace / license / governance

- **回答**: **(a)** 命名 `claude-org-skills`（narrow 2 skill を字義通り表現）、license **MIT**（Layer 1 / Layer 2 / claude-org-ja と統一）、governance は claude-org-ja / Layer 1 / Layer 2 と同じ maintainer 体制（**CODEOWNERS 共有、Lead 単一 maintainer**）。
- **根拠**:
  - 命名: `claude-org-glue` / `claude-org-orchestration` / `claude-org-doctrine` は Q3=a narrow scope (2 skill) に対して名前が広すぎる。`claude-org-skills` が「Claude Code skill 形式で配布される claude-org の component」という実態を最も素直に表現する。GitHub repo 名衝突は v0.1 release 直前に再点検（Phase 4 Q10 と同じ運用）。
  - License: MIT は Layer 1 (core-harness) / Layer 2 (claude-org-runtime, Phase 4 Q10) / claude-org-ja と統一されており、consumer 側の license compatibility 判定を簡素化する。Apache-2.0 (Q12 選択肢 (c) 部分) の特許条項を Layer 3 だけ追加する積極理由がない。
  - Governance: Lead 単一 maintainer + CODEOWNERS 共有は Layer 1/2 で機能している体制。Layer 3 だけ別 maintainer にする (c) は consumer 0 段階で community governance を構築する over-engineering。
  - (b) 複数リポ分割は Q3=a narrow と矛盾（2 skill を 3 リポに割る合理性がない）。
- **トレードオフ**: 単一 maintainer のため Lead 不在時の release / review がブロックする。Layer 1/2/4 でも同じ運用なので Phase 5 単独問題ではない。
- **DoD 接続**: v0.1 release 時に repo metadata（LICENSE, CODEOWNERS, README）がこの命名 / license / governance を反映していること。

---

## 後続アクション

1. **2026-08-03 頃の再評価セッション開催** — Q1=b に従い、measurement (i) Lead 手作業 + (ii) worker 派遣の結果を持ち寄って kill / proceed を判定。
2. **measurement (ii) の worker 派遣準備** — 2026-07-下旬に `git log --since=2026-02-03 -- .claude/skills/` 集計タスクを worker 起票（CLAUDE.md / 入力指示はこの時点で起こす）。
3. **proceed 判定が出た場合の Step B 相当タスク** — `claude-org-skills` リポ初期化（README / LICENSE / CODEOWNERS）+ `org-delegate` / `org-start` の英語 SoT 版作成 + claude-org-ja 側 override 構造への書き換え PR、の 2 トラックを並行起票。
4. **kill 判定が出た場合** — 本 doc を「Phase 5 結論」として確定し、`docs/internal/phase5-conclusion-killed-2026-08-XX.md` 等で永続化。Layer 3 抽出は close。

---

## 未決事項

1. **再評価セッションの正確な日時** — 「2026-08-03 頃」は目安であり、Layer 2 v0.1 release の進捗（Phase 4 完了タイミング）次第で前後する。Layer 2 v0.1 が出ていない状態で再評価しても consumer 反応が読めないため、**Layer 2 v0.1 release から最低 4 週間後** を実質下限とする。
2. **#171 auto-mirror 射程の Layer 3 拡張** — Q9=a で Layer 3 を新 SoT にする以上、auto-mirror runtime (#171) の射程を Layer 3 まで広げるか、Layer 3 リポ内で独自に翻訳保守するかが未決。Phase 4 Q8=a の未決事項 (§3 prompt template の英訳保守) と同質の問題で、まとめて #171 側で議論する。
3. **proceed 後の `claude-org-skills` PyPI publish 切替判断基準** — Q11=b で当面 GitHub Release のみだが、PyPI publish に切り替える発火条件（consumer 数 / discoverability 要望件数 等）を v0.1 release 時に CHANGELOG ないし README で明示するかは未決。
4. **claude-org-ja `.claude/skills/` の混在期間の運用ルール** — Q10=b DoD 達成後、`org-delegate` + `org-start` のみ override 構造、残り 8 skill は in-tree のままという混在状態が長期化する。混在期間中の skill 改修ルール（override layer に書く / in-tree に書く / 両方）は Phase 5 close 後の Phase 6 等で改めて決める必要がある。
