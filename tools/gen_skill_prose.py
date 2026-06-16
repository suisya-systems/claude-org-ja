"""transport-neutral skill prose generator (Epic #586 Phase 3' G0).

設計 SoT: ``notes/broker-skill-generator-design.md`` (#1-#9 全件 ratified
2026-06-16)。本モジュールはその案 b を実装する **G0 バッチ = ツール新設のみ**で、
既存スキルの source 化・反転・rendered SKILL.md の生成は **一切行わない**
(それは G1 以降の別タスク, 設計 §7.1)。

## 何を解くか (設計 §0.2 / §0.4)

1 つの SKILL.md には **2 つの render 基底** が同居する (§0.4):

- 本文 prose + dual-system ヘッダ → ``DEFAULT_TRANSPORT`` (= broker) 面で render。
  人間が読む面で、既定フリップ後は broker が literal・renga は opt-in 併記。
- frontmatter ``allowed-tools`` (ツール認可面) → **per-transport render**。
  Claude Code がディスクから読みツール認可をゲートするため、broker 面では
  broker ツールのみを認可し renga ツールは出さない (auth 迂回防止, §9.2 #9)。

本モジュールは中立 source (トークン + 名前付きフラグメント) を ``flag`` から
**1 パスで render** し、ヘッダと本文が構造的に同一 transport 面へ落ちることで
自己矛盾 (ヘッダは broker 宣言・本文は renga) を設計レベルで消す (§0.2)。

## 三層 SoT (設計 §6, 二重 SoT を作らない)

    [runtime] claude_org_runtime.transport      transport 機構の単一 SoT
       |  pin で consume (ハードコードしない)
    [ja seam] tools/transport.py                ja の単一アクセサ
       |  import で consume
    [ja gen]  tools/gen_skill_prose.py (本体)   prose render のオーケストレーションのみ
       |  + フラグメント SoT = promotion-plan 1.1/1.2 由来の単一導出コピー
    [出力]    .claude/skills/**/SKILL.md ほか    commit + drift CI で byte 固定 (G1+)

generator は transport 事実 (server 名・プレフィックス・既定値・ツール集合) を
**一切定義せず**、全て :mod:`tools.transport` 経由で runtime descriptor から
consume する。

## 中立著述形式 (設計 §1.3, 案 1B)

(a) render 面トークン (本文の散在ツール参照, render transport に追従):

    {{FQ}}           -> mcp__org-broker__   / mcp__renga-peers__
    {{SERVER}}       -> org-broker          / renga-peers
    {{CHANNEL_SRC}}  -> org-broker          / renga-peers
                        (<channel source="..."> のタグ値。channel sidecar の
                         MCP サーバー名 org-broker-channel とは別物 = §1.3 の
                         重要な区別。混同すると broker 受信 cue が契約と矛盾する)

(b) 既定値リテラルトークン (コード相当, DEFAULT_TRANSPORT に追従 = render 面と別系統):

    {{DEFAULT_TRANSPORT}} -> transport.DEFAULT_TRANSPORT

(c) 名前付きフラグメント (4 局所差異, per-transport):

    {{> dual-system-header-short }}  {{> dual-system-header-long }}
    {{> spawn-ritual }}              {{> surface-omissions }}

各フラグメントは ``<name>.<flag>.md`` (per-transport 面) を持ち、transport
非依存のものは ``<name>.md`` (両面同一) で持つ (§5 surface-omissions)。

## frontmatter allowed-tools の per-transport render (設計 §0.4 / §2.2 ※3-※5)

skill frontmatter は **per-entry 接頭辞リネーム** (skill 固有サブセットを保存)
であって ``rewrite_allow_entries`` (role-tier 置換) は **使わない** (※3。
role-tier を当てると ``send_message`` 1 個のスキルに ops tier 全部が付く =
per-skill 認可の過剰拡大)。

- 明示 per-tool エントリ: ``mcp__renga-peers__<tool>`` -> ``mcp__org-broker__<tool>``
  へ server 接頭辞だけリネーム。リネーム後に broker descriptor surface に存在
  するか検証し、broker 省略ツール (``focus_pane`` / ``new_tab``) は **drop** し
  drift ログに記録 (※4)。
- ワイルドカード ``mcp__renga-peers__*``: broker ``*`` のまま写してはならない
  (broker 固有 / 将来ツールまで広げ subset 保存を破る, ※5)。renga source surface
  (``surface("renga").tools_for_role(role)``) で **明示展開** -> per-tool リネーム
  + descriptor 検証にかけ、broker 側は「source 集合の像」の明示リストとして出力。
- renga 面 (TEMPLATE_TRANSPORT) は **恒等** (source をそのまま返す)。
  ``ORG_TRANSPORT=renga`` 再生成が byte 等価になる rollback byte 安定の根拠 (§3.2)。

``permissions.md`` (org-setup) のみ機構が異なり ``rewrite_allow_entries``
(role-tier 置換) を使う (``identity-anchor`` モード, §4.2(2))。
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable, Mapping, Optional

# ja seam 経由でのみ transport 事実を consume する (設計 §6, ハードコードしない)。
import tools.transport as transport

# ---------------------------------------------------------------------------
# 定数 / 正規表現
# ---------------------------------------------------------------------------

# 名前付きフラグメント参照 ``{{> fragment-name }}``。
_FRAGMENT_RE = re.compile(r"\{\{>\s*([a-z0-9][a-z0-9-]*)\s*\}\}")
# render 面 / 既定値トークン ``{{TOKEN}}`` (フラグメント参照を除く)。
_TOKEN_RE = re.compile(r"\{\{\s*([A-Z][A-Z0-9_]*)\s*\}\}")
# フラグメント注入の最大段数 (フラグメントが別フラグメントを参照する場合の安全弁)。
_MAX_FRAGMENT_DEPTH = 8

# manifest 処理モード (設計 §4.1)。生成モードと非生成アンカーを区別する。
MODE_TEMPLATE = "template"
MODE_TEMPLATE_FRAGMENT = "template+fragment"
MODE_SURGICAL_FRAGMENT = "surgical-fragment"
MODE_CODE_LITERAL = "code-literal"
MODE_IDENTITY_ANCHOR = "identity-anchor"
GENERATING_MODES = frozenset(
    {MODE_TEMPLATE, MODE_TEMPLATE_FRAGMENT, MODE_SURGICAL_FRAGMENT, MODE_CODE_LITERAL}
)
ALL_MODES = GENERATING_MODES | {MODE_IDENTITY_ANCHOR}

# frontmatter allowlist の処理機構 (設計 §2.2 / §4.1)。
ALLOWLIST_PER_ENTRY = "per-entry-rename"  # skill frontmatter (subset 保存, ※3)
ALLOWLIST_ROLE_TIER = "role-tier"  # permissions.md (rewrite_allow_entries, identity-anchor)
ALLOWLIST_NONE = "none"  # frontmatter allowlist を持たない (references / CLAUDE.md 等)
ALLOWLIST_KINDS = frozenset({ALLOWLIST_PER_ENTRY, ALLOWLIST_ROLE_TIER, ALLOWLIST_NONE})

# role 集合 (設計 §2.2 ※2)。user_common は org-setup の user-common ブロックに対応。
ROLES = ("user_common", "worker", "curator", "dispatcher", "secretary")


class GenError(RuntimeError):
    """generator の構造的エラー (未解決トークン / 欠落フラグメント / source 正規化違反等)。"""


# ---------------------------------------------------------------------------
# トークン render (設計 §1.3 (a)/(b))
# ---------------------------------------------------------------------------


def render_tokens(
    text: str,
    flag: str,
    *,
    env: Optional[Mapping[str, str]] = None,
) -> str:
    """render 面 / 既定値トークンを解決する。

    ``{{FQ}}`` / ``{{SERVER}}`` / ``{{CHANNEL_SRC}}`` は render transport (``flag``)
    に追従し、``{{DEFAULT_TRANSPORT}}`` は ``transport.DEFAULT_TRANSPORT`` (既定値
    そのもの) に追従する (§1.3 (c) の混同防止 = 別系統トークン)。

    未知の ``{{UPPER}}`` トークンが残れば :class:`GenError` (silent な穴を作らない)。
    フラグメント参照 ``{{> name }}`` は本関数の対象外 (先に注入済みである前提)。
    """
    surf = transport.surface(flag, env=env)
    values = {
        "FQ": surf.fq_prefix,
        "SERVER": surf.server,
        # <channel source="..."> のタグ値 = サーバー名 (sidecar 名 org-broker-channel
        # ではない, §1.3)。意味は SERVER と同値だが、generator は token を分離して
        # sidecar 名との混同を構造的に防ぐ。
        "CHANNEL_SRC": surf.server,
        "DEFAULT_TRANSPORT": transport.DEFAULT_TRANSPORT,
    }

    def _sub(match: re.Match) -> str:
        name = match.group(1)
        if name not in values:
            raise GenError(f"unknown render token {{{{{name}}}}}")
        return values[name]

    return _TOKEN_RE.sub(_sub, text)


# ---------------------------------------------------------------------------
# フラグメント注入 (設計 §1.3 (c))
# ---------------------------------------------------------------------------


def load_fragment(name: str, flag: str, fragments_dir: Path) -> str:
    """フラグメント ``name`` の ``flag`` 面の本文を返す。

    per-transport フラグメントは ``<name>.<flag>.md``、transport 非依存
    (surface-omissions 等, §5) は ``<name>.md`` (両面同一)。前者を優先し、
    無ければ後者にフォールバック。どちらも無ければ :class:`GenError`。
    """
    per_transport = fragments_dir / f"{name}.{flag}.md"
    neutral = fragments_dir / f"{name}.md"
    if per_transport.is_file():
        return per_transport.read_text(encoding="utf-8")
    if neutral.is_file():
        return neutral.read_text(encoding="utf-8")
    raise GenError(
        f"fragment {name!r} not found for transport {flag!r} "
        f"(looked for {per_transport.name} and {neutral.name} in {fragments_dir})"
    )


def inject_fragments(text: str, flag: str, fragments_dir: Path) -> str:
    """``{{> name }}`` を ``flag`` 面のフラグメント本文へ展開する。

    フラグメントが別フラグメントを参照する場合に備え安定するまで反復する
    (``_MAX_FRAGMENT_DEPTH`` を超えたら循環参照とみなし :class:`GenError`)。
    フラグメント本文末尾の改行はトリムして注入点の体裁を呼び出し側に委ねる。
    """
    for _ in range(_MAX_FRAGMENT_DEPTH):
        if not _FRAGMENT_RE.search(text):
            return text

        def _sub(match: re.Match) -> str:
            name = match.group(1)
            return load_fragment(name, flag, fragments_dir).rstrip("\n")

        text = _FRAGMENT_RE.sub(_sub, text)
    raise GenError(
        "fragment injection did not converge within "
        f"{_MAX_FRAGMENT_DEPTH} passes (cyclic {{> ...}} reference?)"
    )


# ---------------------------------------------------------------------------
# frontmatter allowed-tools の per-transport render (設計 §0.4 / §2.2 ※3-※5)
# ---------------------------------------------------------------------------


def _broker_tool_universe(*, env: Optional[Mapping[str, str]] = None) -> frozenset:
    """全ロールにわたる broker descriptor のツール和集合。

    明示 per-tool エントリ (role 不在) の drop 判定に使う。broker が**どのロール
    でも**公開しないツール (``focus_pane`` / ``new_tab``) はこの和集合に含まれない
    ため drop される。``spawn_pane`` 等の role 限定ツールは和集合には含まれるので、
    source が明示列挙していれば保存される (subset 保存)。
    """
    surf = transport.surface("broker", env=env)
    universe: set = set()
    for role in ROLES:
        universe.update(surf.tools_for_role(role))
    return frozenset(universe)


def _broker_tools_for(role: Optional[str], *, env: Optional[Mapping[str, str]] = None) -> frozenset:
    """drop 判定に使う broker ツール集合。

    role 指定時はそのロールの broker tier、role 不在時 (明示 per-tool only skill)
    は全ロール和集合 (設計 §2.2 ※4: 明示エントリは role 非依存で決定的)。
    """
    if role is None:
        return _broker_tool_universe(env=env)
    return frozenset(transport.surface("broker", env=env).tools_for_role(role))


@dataclass
class AllowlistRender:
    """frontmatter allowlist の per-transport render 結果。"""

    entries: list  # render 後の allowlist エントリ (順序保存)
    dropped: list = field(default_factory=list)  # drop した (tool, 理由) 記録 (drift ログ用)


def _split_server_prefix(entry: str):
    """MCP エントリ ``mcp__<server>__<tool>`` を (server, tool) に分解。非 MCP は None。"""
    if not entry.startswith("mcp__"):
        return None
    rest = entry[len("mcp__"):]
    sep = rest.find("__")
    if sep < 0:
        return None
    return rest[:sep], rest[sep + len("__"):]


def assert_source_allowlist_normalized(
    entries: Iterable[str],
    *,
    env: Optional[Mapping[str, str]] = None,
) -> None:
    """source allowlist が renga/template 面に正規化されているか検証する (§2.2 ※1)。

    source は renga 面で著述する原則 (§0.3 TEMPLATE_TRANSPORT) ゆえ、broker
    プレフィックス (``mcp__org-broker__``) の混入は opt-in 時代の残骸であり過剰
    認可の元。混入を検出したら :class:`GenError` (drift CI が assert する不変条件
    を生成時にも前倒しで強制)。
    """
    broker_prefix = transport.surface("broker", env=env).fq_prefix
    bad = [e for e in entries if isinstance(e, str) and e.startswith(broker_prefix)]
    if bad:
        raise GenError(
            "source allowlist must be authored on the renga/template surface "
            f"(no {broker_prefix!r} entries); found: {bad}. "
            "Strip opt-in-era broker entries from the source frontmatter."
        )


def render_frontmatter_allowlist(
    entries: list,
    flag: str,
    *,
    role: Optional[str] = None,
    allowlist: str = ALLOWLIST_PER_ENTRY,
    env: Optional[Mapping[str, str]] = None,
) -> AllowlistRender:
    """frontmatter ``allowed-tools`` を per-transport render する (設計 §0.4)。

    ``allowlist`` 機構:

    - ``per-entry-rename`` (skill frontmatter, ※3): server 接頭辞だけ broker へ
      リネームし skill 固有サブセットを保存。ワイルドカードは renga source surface
      へ展開 (※5)、broker 省略ツールは drop (※4)。``rewrite_allow_entries`` は使わない。
    - ``role-tier`` (permissions.md, identity-anchor): ``rewrite_allow_entries``
      (role-tier 置換) に委譲。``role`` 必須。
    - ``none``: allowlist を持たないエントリ。素通し。

    renga 面 (TEMPLATE_TRANSPORT) は per-entry-rename / role-tier いずれも **恒等**
    (source をそのまま返す = rollback byte 安定, §3.2)。
    """
    src = list(entries)
    assert_source_allowlist_normalized(src, env=env)

    if allowlist == ALLOWLIST_NONE:
        return AllowlistRender(entries=src)

    if allowlist == ALLOWLIST_ROLE_TIER:
        if role is None:
            raise GenError("allowlist 'role-tier' requires a role (permissions.md tier 置換)")
        rewritten = transport.rewrite_allow_entries(src, role, flag=flag, env=env)
        return AllowlistRender(entries=list(rewritten))

    if allowlist != ALLOWLIST_PER_ENTRY:
        raise GenError(f"unknown allowlist kind {allowlist!r}")

    resolved = transport.resolve(flag, env=env)
    # renga (template 面) は恒等 = byte 安定の構造保証 (§3.2)。
    if resolved == transport.TEMPLATE_TRANSPORT:
        return AllowlistRender(entries=src)

    return _render_per_entry_broker(src, role=role, env=env)


def _render_per_entry_broker(
    entries: list,
    *,
    role: Optional[str],
    env: Optional[Mapping[str, str]],
) -> AllowlistRender:
    """broker 面の per-entry リネーム (§2.2 ※3-※5)。subset を保存し ``*`` を出さない。"""
    renga_surf = transport.surface("renga", env=env)
    broker_surf = transport.surface("broker", env=env)
    renga_server = renga_surf.server
    broker_prefix = broker_surf.fq_prefix
    keep_set = _broker_tools_for(role, env=env)

    out: list = []
    dropped: list = []
    seen: set = set()

    def _emit(fq: str) -> None:
        if fq not in seen:
            seen.add(fq)
            out.append(fq)

    for entry in entries:
        if not isinstance(entry, str):
            out.append(entry)
            continue
        parsed = _split_server_prefix(entry)
        if parsed is None:
            # 非 MCP エントリ (Bash(...) / Read 等) は順序保存で素通し。
            out.append(entry)
            continue
        server, tool = parsed
        if server != renga_server:
            # renga 以外の MCP server は per-entry リネーム対象外 (想定外だが温存)。
            out.append(entry)
            continue
        if tool == "*":
            # ワイルドカードは renga source surface へ明示展開 (※5)。role 必須。
            if role is None:
                raise GenError(
                    "wildcard 'mcp__renga-peers__*' requires a role for source-surface "
                    "expansion (設計 §2.2 ※5 / §4.1 (b))"
                )
            for src_tool in sorted(renga_surf.tools_for_role(role)):
                if src_tool in keep_set:
                    _emit(f"{broker_prefix}{src_tool}")
                else:
                    dropped.append((src_tool, "omitted from broker surface"))
        else:
            # 明示 per-tool: 接頭辞リネーム + descriptor 検証 (※4)。
            if tool in keep_set:
                _emit(f"{broker_prefix}{tool}")
            else:
                dropped.append((tool, "omitted from broker surface"))
    return AllowlistRender(entries=out, dropped=dropped)


# ---------------------------------------------------------------------------
# frontmatter 分解 (allowed-tools ブロックの最小パーサ)
# ---------------------------------------------------------------------------


@dataclass
class Frontmatter:
    """SKILL.md の YAML frontmatter を allowed-tools ブロック単位で分解した結果。"""

    pre_lines: list  # frontmatter 内 allowed-tools より前の行 (--- は含まない)
    allowed_tools: Optional[list]  # allowed-tools のリスト要素 (なければ None)
    post_lines: list  # allowed-tools ブロックより後の行
    body: str  # frontmatter 以降の本文
    has_frontmatter: bool


_FRONTMATTER_DELIM = "---"
_ALLOWED_TOOLS_KEY = "allowed-tools:"
_LIST_ITEM_RE = re.compile(r"^(\s*)-\s+(.*?)\s*$")


def split_frontmatter(text: str) -> Frontmatter:
    """先頭 ``---`` で区切られた YAML frontmatter を分解する。

    ``allowed-tools:`` のブロックリスト形式 (``- entry`` 列) のみ構造化し、それ以外
    の frontmatter 行は ``pre_lines`` / ``post_lines`` に温存する。frontmatter 無し
    or allowed-tools 無しは ``allowed_tools=None``。本パーサは G0 が扱う SKILL.md
    形 (ブロックリスト) に限定 (フロー ``[a, b]`` は未対応で素通し)。
    """
    lines = text.split("\n")
    if not lines or lines[0].strip() != _FRONTMATTER_DELIM:
        return Frontmatter([], None, [], text, has_frontmatter=False)

    # 終端 --- を探す。
    end = None
    for i in range(1, len(lines)):
        if lines[i].strip() == _FRONTMATTER_DELIM:
            end = i
            break
    if end is None:
        # 終端なし: frontmatter として扱わない (壊れた入力を勝手に解釈しない)。
        return Frontmatter([], None, [], text, has_frontmatter=False)

    fm_lines = lines[1:end]
    body = "\n".join(lines[end + 1:])

    # allowed-tools: の位置とそのリスト範囲を特定。
    at_idx = None
    for i, ln in enumerate(fm_lines):
        if ln.strip() == _ALLOWED_TOOLS_KEY.rstrip() or ln.rstrip() == _ALLOWED_TOOLS_KEY:
            at_idx = i
            break
    if at_idx is None:
        return Frontmatter(fm_lines, None, [], body, has_frontmatter=True)

    items: list = []
    j = at_idx + 1
    while j < len(fm_lines):
        m = _LIST_ITEM_RE.match(fm_lines[j])
        if m is None:
            break
        items.append(_strip_inline_comment(m.group(2)))
        j += 1
    pre = fm_lines[: at_idx + 1]
    post = fm_lines[j:]
    return Frontmatter(pre, items, post, body, has_frontmatter=True)


def _strip_inline_comment(entry: str) -> str:
    """allowlist エントリ末尾の ``# comment`` を除く (例: org-start の opt-in 注記)。

    ``Bash(... # ...)`` 等の括弧内 ``#`` は誤除去しないよう、括弧の外の ``#`` のみ
    対象にする。
    """
    depth = 0
    for i, ch in enumerate(entry):
        if ch in "([":
            depth += 1
        elif ch in ")]":
            depth = max(0, depth - 1)
        elif ch == "#" and depth == 0:
            return entry[:i].rstrip()
    return entry.rstrip()


def reassemble_frontmatter(fm: Frontmatter, rendered_tools: Optional[list]) -> str:
    """:func:`split_frontmatter` の逆。render 後の allowed-tools で frontmatter を再構築。"""
    if not fm.has_frontmatter:
        return fm.body
    out = [_FRONTMATTER_DELIM]
    out.extend(fm.pre_lines)
    if rendered_tools is not None:
        out.extend(f"  - {t}" for t in rendered_tools)
    out.extend(fm.post_lines)
    out.append(_FRONTMATTER_DELIM)
    rendered = "\n".join(out)
    if fm.body:
        return rendered + "\n" + fm.body
    return rendered + "\n"


# ---------------------------------------------------------------------------
# 1 ソースの render オーケストレーション
# ---------------------------------------------------------------------------


@dataclass
class RenderResult:
    text: str
    dropped_tools: list = field(default_factory=list)


def render_source(
    source_text: str,
    flag: str,
    *,
    fragments_dir: Path,
    mode: str = MODE_TEMPLATE,
    role: Optional[str] = None,
    allowlist: str = ALLOWLIST_PER_ENTRY,
    env: Optional[Mapping[str, str]] = None,
) -> RenderResult:
    """中立 source 1 件を ``flag`` 面へ render する (本文 + frontmatter を 1 パス)。

    本文: フラグメント注入 -> トークン render (ヘッダと本文が同一 flag から展開され
    自己矛盾不可能, §0.2)。frontmatter ``allowed-tools``: ``allowlist`` 機構で
    per-transport render (§0.4)。``identity-anchor`` モードは render 対象外として
    入力をそのまま返す (permissions.md の renga byte 不変, §4.2(2))。
    """
    if mode not in ALL_MODES:
        raise GenError(f"unknown manifest mode {mode!r} (valid: {sorted(ALL_MODES)})")
    if allowlist not in ALLOWLIST_KINDS:
        raise GenError(f"unknown allowlist kind {allowlist!r}")

    if mode == MODE_IDENTITY_ANCHOR:
        # render 対象外 = 構造的に触らない (恒等射影で renga byte 不変を保証)。
        return RenderResult(text=source_text)

    fm = split_frontmatter(source_text)

    # 本文: フラグメント注入 -> トークン render。
    body = inject_fragments(fm.body, flag, fragments_dir)
    body = render_tokens(body, flag, env=env)

    # frontmatter allowed-tools の per-transport render。
    rendered_tools = fm.allowed_tools
    dropped: list = []
    if fm.allowed_tools is not None:
        ar = render_frontmatter_allowlist(
            fm.allowed_tools, flag, role=role, allowlist=allowlist, env=env
        )
        rendered_tools = ar.entries
        dropped = ar.dropped

    if not fm.has_frontmatter:
        return RenderResult(text=body, dropped_tools=dropped)

    fm.body = body
    return RenderResult(text=reassemble_frontmatter(fm, rendered_tools), dropped_tools=dropped)


# ---------------------------------------------------------------------------
# manifest スキーマ / ロード (設計 §4.1)
# ---------------------------------------------------------------------------


@dataclass
class ManifestEntry:
    source: str
    mode: str
    allowlist: str
    output: Optional[str] = None
    role: Optional[str] = None


@dataclass
class Manifest:
    entries: list  # ManifestEntry
    exclude: list = field(default_factory=list)  # 据え置き (generator 対象外, §7.1)


def _schema_path() -> Path:
    return Path(__file__).resolve().parent / "skill_src" / "manifest.schema.json"


def load_manifest_schema() -> dict:
    """同梱の manifest JSON スキーマを読む (drift CI / 外部 validator が consume)。"""
    return json.loads(_schema_path().read_text(encoding="utf-8"))


def validate_manifest_obj(obj: dict) -> Manifest:
    """生 dict を不変条件 (設計 §4.1) で検証し :class:`Manifest` を返す。

    JSON スキーマ (構造) + 設計固有の意味検証 (role 必須条件 §2.2 ※2) の二段。
    意味検証はスキーマで表現しきれない条件 (mode/allowlist と role の連動) を担う。
    """
    if not isinstance(obj, dict):
        raise GenError("manifest must be a JSON object")
    raw_entries = obj.get("entries")
    if not isinstance(raw_entries, list):
        raise GenError("manifest 'entries' must be a list")
    entries: list = []
    for i, raw in enumerate(raw_entries):
        if not isinstance(raw, dict):
            raise GenError(f"manifest entries[{i}] must be an object")
        try:
            source = raw["source"]
            mode = raw["mode"]
            allowlist = raw["allowlist"]
        except KeyError as exc:
            raise GenError(f"manifest entries[{i}] missing required field {exc}") from exc
        if mode not in ALL_MODES:
            raise GenError(f"manifest entries[{i}] invalid mode {mode!r}")
        if allowlist not in ALLOWLIST_KINDS:
            raise GenError(f"manifest entries[{i}] invalid allowlist {allowlist!r}")
        role = raw.get("role")
        if role is not None and role not in ROLES:
            raise GenError(f"manifest entries[{i}] invalid role {role!r} (valid: {ROLES})")
        # role-tier (rewrite_allow_entries) は permissions.md = identity-anchor 専用
        # (設計 §2.2 ※3 / §4.2(2))。生成モードの skill frontmatter に role-tier を
        # 当てると frontmatter が役割の全 tier へ拡大し、本 generator が防ぐべき
        # per-skill 認可の過剰拡大を再生産する。よって組み合わせ自体を拒否する。
        if allowlist == ALLOWLIST_ROLE_TIER and mode != MODE_IDENTITY_ANCHOR:
            raise GenError(
                f"manifest entries[{i}] allowlist 'role-tier' is reserved for "
                f"identity-anchor (permissions.md); mode {mode!r} must use "
                "'per-entry-rename' or 'none' (設計 §2.2 ※3 / §4.2(2))"
            )
        # role 必須条件 (設計 §2.2 ※2 / §4.1): (a) role-tier (identity-anchor),
        # (b) ワイルドカードを含む skill。ここでは (a) を強制し、(b) は render 時に
        # source を見て検証 (wildcard 展開で role 不在なら GenError)。
        if allowlist == ALLOWLIST_ROLE_TIER and role is None:
            raise GenError(
                f"manifest entries[{i}] allowlist 'role-tier' requires 'role' "
                "(permissions.md identity-anchor, 設計 §2.2 ※2)"
            )
        # 生成モードは output 必須 (drift CI カバレッジから黙って漏れるのを防ぐ,
        # 設計 §7.2-1)。スキーマも「生成モードでは output 必須」と記すが、
        # JSON スキーマでは mode との条件付き required を表現しないため意味検証で強制。
        output = raw.get("output")
        if mode in GENERATING_MODES and not output:
            raise GenError(
                f"manifest entries[{i}] mode {mode!r} is a generating mode and "
                "requires 'output' (drift CI が render(source)==committed を byte 比較する, "
                "設計 §7.2-1)"
            )
        entries.append(
            ManifestEntry(
                source=source,
                mode=mode,
                allowlist=allowlist,
                output=output,
                role=role,
            )
        )
    exclude = obj.get("exclude", [])
    if not isinstance(exclude, list):
        raise GenError("manifest 'exclude' must be a list")
    return Manifest(entries=entries, exclude=list(exclude))


def load_manifest(path: Path) -> Manifest:
    return validate_manifest_obj(json.loads(Path(path).read_text(encoding="utf-8")))


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def _resolve_paths(entry: ManifestEntry, base: Path):
    source = (base / entry.source).resolve()
    output = (base / entry.output).resolve() if entry.output else None
    return source, output


def _render_entry(entry: ManifestEntry, base: Path, fragments_dir: Path, flag: str) -> RenderResult:
    source, _ = _resolve_paths(entry, base)
    text = source.read_text(encoding="utf-8")
    return render_source(
        text,
        flag,
        fragments_dir=fragments_dir,
        mode=entry.mode,
        role=entry.role,
        allowlist=entry.allowlist,
    )


def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="gen_skill_prose",
        description=(
            "transport-neutral skill prose generator (Epic #586 Phase 3' G0). "
            "Renders neutral source (tokens + per-transport fragments) for a "
            "transport flag. G0 ships the tool only - no skill source is migrated."
        ),
    )
    p.add_argument(
        "--manifest",
        type=Path,
        help="manifest JSON path (entries to render).",
    )
    p.add_argument(
        "--transport",
        choices=list(transport.TRANSPORTS),
        default=None,
        help="render transport flag (default: resolve via ORG_TRANSPORT / DEFAULT_TRANSPORT).",
    )
    p.add_argument(
        "--fragments-dir",
        type=Path,
        default=None,
        help="fragment SoT dir (default: tools/skill_src/fragments).",
    )
    p.add_argument(
        "--check",
        action="store_true",
        help="drift check: compare render(source) to committed output, exit 1 on diff.",
    )
    p.add_argument(
        "--print-schema",
        action="store_true",
        help="print the manifest JSON schema and exit.",
    )
    return p


def main(argv: Optional[list] = None) -> int:
    args = _build_parser().parse_args(argv)

    if args.print_schema:
        print(json.dumps(load_manifest_schema(), indent=2, ensure_ascii=False))
        return 0

    flag = transport.resolve(args.transport)
    fragments_dir = args.fragments_dir or (Path(__file__).resolve().parent / "skill_src" / "fragments")

    if not args.manifest:
        print(
            "gen_skill_prose: nothing to do (G0 ships the tool only; no manifest "
            "entries are wired yet). Use --manifest <path> to render, or "
            "--print-schema to inspect the manifest schema.",
            file=sys.stderr,
        )
        return 0

    manifest = load_manifest(args.manifest)
    # manifest 内の source / output は manifest ファイル自身からの相対 (cwd 非依存)。
    base = args.manifest.resolve().parent
    rc = 0
    for entry in manifest.entries:
        if entry.mode not in GENERATING_MODES:
            # identity-anchor は render しない (非生成アンカー)。
            continue
        result = _render_entry(entry, base, fragments_dir, flag)
        _, output = _resolve_paths(entry, base)
        if output is None:
            # 生成モードは validate_manifest_obj で output 必須を強制済みなので
            # ここに来ない想定。万一来たら check では drift 扱いにし、生成では
            # stdout へ出して silent skip にしない (設計 §7.2-1 のカバレッジ漏れ防止)。
            if args.check:
                print(f"DRIFT: {entry.source} (generating mode) has no output to check", file=sys.stderr)
                rc = 1
            else:
                print(result.text)
            continue
        if args.check:
            committed = output.read_text(encoding="utf-8") if output.exists() else None
            if committed != result.text:
                print(f"DRIFT: {entry.output} differs from render(source)", file=sys.stderr)
                rc = 1
        else:
            output.write_text(result.text, encoding="utf-8")
            print(f"wrote {entry.output}")
    return rc


if __name__ == "__main__":
    raise SystemExit(main())
