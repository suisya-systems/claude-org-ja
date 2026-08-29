#!/usr/bin/env python3
"""Group B (``close_pane`` / ``set_pane_identity``) の相対セレクタ再混入チェッカー。

契約 [`docs/contracts/backend-interface-contract.md`] T-§4.2 "Fail-safe
consequence for Group B" は、この 2 ツールの宛先指定を **数値 pane_id** に
限る（相対セレクタ = リテラル ``"focused"`` / 裸の name を使わない）。
本スクリプトは canonical な手順ドキュメントにその相対セレクタ形が
**再混入**するのを機械的に弾く。

## 何を検査するか

* 対象拡張子は ``.md`` / ``.md.in`` のみ（手順 prose が住む面）。
* **canonical source を検査する**: ``X.md.in`` が在る場合は ``X.md.in`` だけを
  検査し、生成物 ``X.md`` は対象から外す。同一の論理呼び出しを source と
  生成物で二重計上しないためで、契約台帳の exclusion rule (1)（generated
  mirror は row を持たない）と同じ規律。``.md.in`` を持たない手保守ファイル
  （``.claude/skills/org-start/SKILL.md`` / ``.dispatcher/CLAUDE.md`` /
  ``.dispatcher/references/*.md`` / ``docs/verification.md`` 等）は本体を検査する。

## 何を違反とするか

``close_pane(...)`` / ``set_pane_identity(...)`` の ``target=`` 実引数を取り出し、

* 数値リテラル（``target=3`` / ``target="3"`` / ``target=%3``）→ 適合
* プレースホルダ（``target=<pane_id>`` / ``target="<RENGA_PANE_ID の値>"`` の
  ように**全体**が ``<...>`` で括られた形）→ 適合
* それ以外で ASCII 英字を含むもの（``target="focused"`` / ``target="curator"`` /
  ``target="worker-{task_id}"`` / ``target="pr-watch-<PR>"``）→ **違反**

``target=`` は**第 1 引数に限定しない**。``close_pane(name=..., target="curator")``
のように順序を入れ替えて書いても同じ相対セレクタなので、実引数リスト全体から
探す（第 1 引数だけを見ると、順序違いが素通りする）。

``set_pane_identity(...)`` に ``target=`` が**無い**呼び出しも違反とする。
契約 T-§4.2 の caller pane id 取得規則が書くとおり、このツールの ``target`` は
**既定が ``"focused"``** なので、リテラルを書かない caller も同じハザード
（フォーカスされたペイン = 人間が最後に選んだペインを改名する）に到達する。
``close_pane`` には同種の既定が契約に無いので、こちらは省略形を違反にしない。

``mcp__renga-peers__`` / ``mcp__org-broker__`` / ``{{FQ}}`` のいずれのプレフィックス
形でも拾う（``{{FQ}}`` は transport 中立 source の render トークン）。

## allowlist（DD-2 stale-binding carve-out）

「登録簿に name binding だけが stale に残り、``list_panes`` に出ないので
**列挙から数値 pane_id を取り直せない**」経路だけは、transport 条件付きで
裸 name の ``close_pane`` を許す（契約 T-§4.2 の stale-binding 行が求める
「使った mechanism」がこの allowlist）。該当箇所は :data:`ALLOWLIST` に
**ファイルパス + 一致すべき文脈文字列**で持つ。行番号では持たない
（行番号は編集のたびにドリフトし、ドリフトした瞬間に allowlist が
別の行を免罪しうるため）。

allowlist エントリがどの違反にも一致しなくなった場合（prose の書き換え /
ファイル移動 / 数値化済み）も **stale allowlist** として報告し exit 1 にする。
免罪符だけが実体を失って残ると、次に同じ場所へ相対セレクタが戻ったときに
黙って通してしまうため。

## 除外

台帳・履歴・作業領域は検査しない（違反形を**引用**するのが役目のファイル）:

* ``docs/contracts/backend-interface-contract.md`` — 台帳が旧形を逐語引用する
* ``CHANGELOG.md`` — 過去の改訂記録
* ``notes/2026-08-05-renga2-org-audit.md`` — 当時の観測を凍結保存する監査ノート
  （冒頭 erratum が「本文は一切書き換えていない / この論点の正本は契約側」と
  宣言している。``notes/`` の他のファイルは生きた設計 SoT なので検査対象に残す）
* ``knowledge/`` — 蓄積ノート（当時の観測の保存が目的）
* ``tmp/`` — 作業スクラッチ
* ``*.local.md`` — operator 私物の machine-local ドキュメント（``.gitignore``
  の ``.local.md`` 規約。ワーカー brief ``CLAUDE.local.md`` を含む）
* ネストした別チェックアウト（走査ルート以外で直下に ``.git`` を持つ
  ディレクトリ = worktree / clone）— このリポジトリの正準手順ではない。
  ディレクトリ名は任意なので名前ではなく構造で判定する

## 終了コード

* ``0`` — 違反ゼロ かつ stale allowlist ゼロ
* ``1`` — 違反あり、または stale allowlist あり（詳細を stdout に出す）
* ``2`` — 引数エラー等（argparse 既定）
"""

from __future__ import annotations

import argparse
import os
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable, Iterator, Sequence

REPO_ROOT = Path(__file__).resolve().parent.parent

#: 検査対象の拡張子（長い方を先に判定する）。
SCAN_SUFFIXES = (".md.in", ".md")

#: 別チェックアウトのルートを指す印（worktree ではファイル、clone ではディレクトリ）。
CHECKOUT_MARKER = ".git"

#: どの階層でも降りないディレクトリ名。
SKIP_DIR_NAMES = frozenset(
    {
        ".git",
        ".mypy_cache",
        ".pytest_cache",
        ".ruff_cache",
        ".venv",
        "__pycache__",
        "node_modules",
        "venv",
    }
)

#: 除外するディレクトリ（repo 相対 posix パス）と理由。
EXCLUDED_DIRS: dict[str, str] = {
    "knowledge": "accumulated notes: they preserve past observations verbatim",
    "tmp": "scratch area, not a canonical procedure",
}

#: 除外するファイル（repo 相対 posix パス）と理由。
EXCLUDED_FILES: dict[str, str] = {
    "docs/contracts/backend-interface-contract.md": (
        "the Group B ledger quotes the pre-migration call forms verbatim"
    ),
    "CHANGELOG.md": "revision history of past changes",
    "notes/2026-08-05-renga2-org-audit.md": (
        "frozen audit record: its erratum states the body is kept unrewritten "
        "and that the contract, not this note, is the SoT for Group B"
    ),
}

#: machine-local な operator 私物ドキュメントの接尾辞（.gitignore の規約）。
LOCAL_DOC_SUFFIXES = (".local.md", ".local.md.in")


@dataclass(frozen=True)
class AllowlistEntry:
    """相対セレクタを許す 1 箇所。行番号ではなく文脈文字列で束縛する。"""

    path: str
    """repo 相対 posix パス（canonical source 側 = ``.md.in``）。"""

    context: str
    """違反行に含まれていなければならない文脈文字列。"""

    target: str
    """許す呼び出しの selector 値。**1 entry = 1 呼び出し**に束縛するための鍵。

    文脈文字列だけで束縛すると、許可済み呼び出しと同じ行に別の危険な呼び出しを
    書き足したときに巻き添えで免除されてしまう（``close_pane(target="curator")``
    を許可済みの ``pr-watch-<PR>`` の隣に置く形）。selector 値まで一致を要求し、
    かつ entry は 1 回しか消費しないことで、その混入を違反として拾う。
    """

    reason: str
    """なぜ許すか（ASCII で書く: cp932 コンソールへ出るため）。"""


#: DD-2 stale-binding carve-out。3 条件（Group B を単一タブモデルで解決する
#: backend / 再 spawn が [name_taken] / その name が list_panes に出ない）が
#: 揃うときに限り裸 name の close_pane を許す経路と、それを説明する同期 prose。
#: carve-out を取る手順は契約が記録するとおり 3 つ（pr-watch-pane の Step 3 と
#: Step 5 (b)、org-pull-request の post-merge cleanup）だけで、4 つ目を無記録で
#: 足さない。pr-watch-pane が 3 件なのは、call site 2 件に加えて --self-close の
#: 説明が同じ許可形を逐語で引くため。
ALLOWLIST: tuple[AllowlistEntry, ...] = (
    AllowlistEntry(
        path=".claude/skills/pr-watch-pane/SKILL.md.in",
        context="そこで撃つ",
        target="pr-watch-<PR>",
        reason=(
            "sync prose for the --self-close note: it quotes the stale-binding "
            "carve-out call form and defers to Step 5 (b) for the conditions"
        ),
    ),
    AllowlistEntry(
        path=".claude/skills/pr-watch-pane/SKILL.md.in",
        context="で name 解決させて登録簿を",
        target="pr-watch-<PR>",
        reason=(
            "Step 3 [name_taken] self-recovery: the pane is gone from list_panes, "
            "so no numeric pane_id can be re-derived from the enumeration"
        ),
    ),
    AllowlistEntry(
        path=".claude/skills/pr-watch-pane/SKILL.md.in",
        context="する（broker が name →",
        target="pr-watch-<PR>",
        reason=(
            "Step 5 case (b) manual stale-binding cleanup: harness-side SoT of "
            "the three conditions the other carve-out sites defer to"
        ),
    ),
    AllowlistEntry(
        path=".claude/skills/org-pull-request/SKILL.md.in",
        context="で登録簿を pop する",
        target="pr-watch-<PR>",
        reason=(
            "post-merge cleanup, stale-binding-only branch: the retained pane_id "
            "is already gone and list_panes cannot supply a numeric id"
        ),
    ),
)

#: close_pane / set_pane_identity の呼び出しを実引数リストごと拾う。
#: プレフィックス（mcp__*__ / {{FQ}}）は前置きなので非アンカーで拾える。
#: 実引数中の 1 段だけの括弧（``target=<pane_id (数値)>`` 等）は許し、
#: ``close`` が None の一致は「その行で閉じていない = 実引数を読み切れて
#: いない」ことを表す（省略形の判定はそのとき行わない）。
#: 実引数は**改行をまたいでよい**。手順 doc は長い呼び出しを複数行に割る書式を
#: 慣用にしており、行単位で走査すると裸 name の close_pane が黙って素通りする。
#: 暴走を避けるため繰り返しは有界にする（1 呼び出しの実引数が 500 文字を超える
#: 書式はこの doc 群に無く、超える場合は close が None になって不一致扱いになる）。
#: ツール名の直前は「識別子文字でない」か「MCP プレフィックスの ``__``」のどちらか。
#: 境界が無いと ``safe_close_pane(...)`` / ``my_set_pane_identity(...)`` のような
#: **別の関数**を Group B 呼び出しと誤検出し、その例を書いただけの doc が
#: リポジトリ回帰テスト経由で CI を落とす。一方 ``_`` を単純に境界へ含めると
#: ``mcp__renga-peers__close_pane`` が拾えなくなるので、``__`` 直後だけを別に許す
#: （``{{FQ}}`` は ``}`` 終わりなので非識別子側で拾える）。
_CALL_RE = re.compile(
    r"(?:(?<![0-9A-Za-z_])|(?<=__))"
    r"(?P<tool>close_pane|set_pane_identity)"
    r"\s*\((?P<args>(?:[^()]|\([^()]*\)){0,500})(?P<close>\))?"
)

#: 実引数リストから target= を取り出す。**第 1 引数に限定しない**
#: （``close_pane(name=..., target="curator")`` のような順序違いを取り逃さない）。
#: 直前が識別子文字でないことを要求して ``pane_target=`` 等の部分一致を避ける。
_TARGET_RE = re.compile(
    r"(?:^|[^0-9A-Za-z_])target\s*=\s*"
    r"(?P<value>\"[^\"\n]*\"|'[^'\n]*'|[^,)\n]*)"
)

#: target= を省いた set_pane_identity が実際に撃つ先（契約が書く既定値）。
IMPLICIT_TARGET = "focused"

#: 数値 pane id（``%3`` のような tmux 表記も含む）。
_NUMERIC_RE = re.compile(r"%?\d+")

#: 全体が <...> で括られたプレースホルダ。
_PLACEHOLDER_RE = re.compile(r"<[^<>]*>")

#: プレースホルダの中身が**数値 pane id を指している**ことを示すトークン。
#: ``<pane_id>`` / ``<照合済みの数値 pane_id>`` / ``<RENGA_PANE_ID の値>`` /
#: ``<そのエントリの id>`` / ``<N>`` を適合形として通す。
_ID_TOKEN_RE = re.compile(
    r"pane_id|(?:^|[^0-9A-Za-z_])id(?:$|[^0-9A-Za-z_])|^%?\d+$|^N$",
    re.IGNORECASE,
)

#: id トークンを含んでいても**相対セレクタを指している**プレースホルダ。
#: ``<focused pane>`` / ``<worker name>`` のように、宛先が名前や focus で
#: 決まる形は、括弧で括られていても契約が禁じる相対セレクタである。
_NON_ID_TOKEN_RE = re.compile(r"focused|name", re.IGNORECASE)


def _is_pane_id_placeholder(inner: str) -> bool:
    """``<...>`` の中身が数値 pane id を指しているか。

    中身を見ずに ``<...>`` を一律適合とすると、``target="<worker name>"`` や
    ``target=<focused pane>`` のような**相対セレクタのプレースホルダ**が
    guard を素通りする（Group B の不変条件そのものが検査できなくなる）。
    """
    inner = inner.strip()
    if not inner:
        return False
    if _NON_ID_TOKEN_RE.search(inner):
        return False
    return bool(_ID_TOKEN_RE.search(inner))


@dataclass(frozen=True)
class Finding:
    """相対セレクタで書かれた呼び出し 1 件。"""

    path: str
    lineno: int
    tool: str
    value: str
    text: str
    implicit: bool = False
    """``target=`` を書かず既定の ``"focused"`` に落ちている呼び出しか。"""

    def location(self) -> str:
        return f"{self.path}:{self.lineno}"


@dataclass
class ScanResult:
    """1 回のスキャン結果。"""

    violations: list[Finding] = field(default_factory=list)
    allowed: list[tuple[Finding, AllowlistEntry]] = field(default_factory=list)
    stale: list[tuple[AllowlistEntry, str]] = field(default_factory=list)
    scanned_files: int = 0

    def ok(self) -> bool:
        return not self.violations and not self.stale


def _strip_quotes(raw: str) -> str:
    value = raw.strip()
    if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
        value = value[1:-1].strip()
    return value


def is_relative_selector(raw_value: str) -> bool:
    """``target=`` の実引数が相対セレクタなら True。

    数値 pane id と ``<...>`` プレースホルダは適合形として False を返す。
    残りのうち ASCII 英字を含むものだけを違反とする（``target=`` の直後が
    空・記号のみのような判定不能形は違反にしない = 偽陽性を出さない）。
    """
    value = _strip_quotes(raw_value)
    if not value:
        return False
    if _NUMERIC_RE.fullmatch(value):
        return False
    if _PLACEHOLDER_RE.fullmatch(value):
        # 中身が数値 pane id を指しているものだけ適合。名前 / focus を指す
        # プレースホルダは括弧で括られていても相対セレクタ。
        return not _is_pane_id_placeholder(value[1:-1])
    # ここまでで弾かれなかった値は数値でもプレースホルダでもない = 安定 name。
    # 「ASCII 英字を含むもの」に絞ると `target="---"` / `target="_"` /
    # `target="123-4"` のような**英字を含まない安定 name** を見逃す（backend の
    # name 規則はこれらを許す）。相対セレクタの再混入を防ぐのが目的なので、
    # **適合形と積極的に判定できなかったものはすべて違反**にする。
    return True


def _span_text(text: str, start: int, end: int) -> str:
    """一致が跨いだ**行全体**を 1 行に畳んで返す。

    呼び出しだけを切り出すと周囲の prose が落ちる。allowlist は
    「ファイルパス + 文脈文字列」で束縛する設計（行番号はドリフトするため）
    なので、その文脈が同じ行に載っている必要がある。
    """
    line_start = text.rfind("\n", 0, start) + 1
    line_end = text.find("\n", end)
    if line_end == -1:
        line_end = len(text)
    return " ".join(text[line_start:line_end].split())


def scan_text(rel_path: str, text: str) -> list[Finding]:
    """1 ファイル分のテキストから相対セレクタ呼び出しを拾う。

    行単位ではなく**テキスト全体**を走査する。行単位だと
    ``close_pane(\\n  target="curator",\\n)`` のように改行で割られた呼び出しを
    取り逃がし、guard が偽陰性を返す。
    """
    findings: list[Finding] = []
    for match in _CALL_RE.finditer(text):
        tool = match.group("tool")
        # 行番号は呼び出しの開始位置から数える（複数行呼び出しは先頭行を指す）。
        lineno = text.count("\n", 0, match.start()) + 1
        snippet = _span_text(text, match.start(), match.end())
        target = _TARGET_RE.search(match.group("args"))
        if target is None:
            # target= を省いた呼び出し。set_pane_identity は既定が
            # "focused" なので、リテラルを書かない caller も同じ
            # ハザードに到達する（契約 T-§4.2 の caller pane id 取得規則）。
            if tool != "set_pane_identity" or match.group("close") is None:
                continue
            findings.append(
                Finding(
                    path=rel_path,
                    lineno=lineno,
                    tool=tool,
                    value=IMPLICIT_TARGET,
                    text=snippet,
                    implicit=True,
                )
            )
            continue
        raw_value = target.group("value")
        if not is_relative_selector(raw_value):
            continue
        findings.append(
            Finding(
                path=rel_path,
                lineno=lineno,
                tool=tool,
                value=_strip_quotes(raw_value),
                text=snippet,
            )
        )
    return findings


def _is_excluded(rel_path: str) -> bool:
    if rel_path in EXCLUDED_FILES:
        return True
    if rel_path.endswith(LOCAL_DOC_SUFFIXES):
        return True
    return any(
        rel_path == d or rel_path.startswith(d + "/") for d in EXCLUDED_DIRS
    )


def _scan_suffix(name: str) -> str | None:
    for suffix in SCAN_SUFFIXES:
        if name.endswith(suffix):
            return suffix
    return None


def _is_nested_checkout(path: Path) -> bool:
    """``path`` 自身が別チェックアウトのルートか（直下に ``.git`` を持つか）。

    判定できないとき（読めないディレクトリ等）は ``False`` を返し、枝刈りは
    ``os.walk`` 側の既存挙動に委ねる（降りられない枝は walk が黙って飛ばす）。
    """
    try:
        return (path / CHECKOUT_MARKER).exists()
    except OSError:
        return False


def iter_scan_files(root: Path) -> Iterator[Path]:
    """検査対象のファイルを列挙する（canonical source 側だけ）。"""
    for dirpath, dirnames, filenames in os.walk(root):
        # 別チェックアウト（worktree / clone）はこのリポジトリの正準手順ではない。
        # ディレクトリ名は任意なので、``.git`` の有無で構造的に判定する。
        dirnames[:] = sorted(
            d
            for d in dirnames
            if d not in SKIP_DIR_NAMES
            and not _is_nested_checkout(Path(dirpath) / d)
        )
        here = Path(dirpath)
        for name in sorted(filenames):
            if _scan_suffix(name) is None:
                continue
            path = here / name
            rel_path = path.relative_to(root).as_posix()
            if _is_excluded(rel_path):
                continue
            # 生成物 X.md は canonical source X.md.in がある限り検査しない。
            if name.endswith(".md") and Path(str(path) + ".in").exists():
                continue
            yield path


def _read_text(path: Path) -> str | None:
    try:
        return path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None


def scan(
    root: Path, allowlist: Sequence[AllowlistEntry] = ALLOWLIST
) -> ScanResult:
    """``root`` 配下を検査して :class:`ScanResult` を返す。"""
    result = ScanResult()
    by_path: dict[str, list[AllowlistEntry]] = {}
    for entry in allowlist:
        by_path.setdefault(entry.path, []).append(entry)
    used: set[AllowlistEntry] = set()
    seen_paths: set[str] = set()

    for path in iter_scan_files(root):
        text = _read_text(path)
        if text is None:
            continue
        result.scanned_files += 1
        rel_path = path.relative_to(root).as_posix()
        seen_paths.add(rel_path)
        for finding in scan_text(rel_path, text):
            entry = _match_entry(finding, by_path.get(rel_path, ()), used)
            if entry is None:
                result.violations.append(finding)
            else:
                used.add(entry)
                result.allowed.append((finding, entry))

    for entry in allowlist:
        if entry in used:
            continue
        result.stale.append((entry, _stale_reason(root, entry, seen_paths)))
    return result


def _match_entry(
    finding: Finding,
    entries: Iterable[AllowlistEntry],
    used: set[AllowlistEntry],
) -> AllowlistEntry | None:
    """違反 1 件に対応する allowlist entry を 1 つだけ返す。

    **entry は 1 回しか消費しない**（``used`` に入ったものは再利用しない）。
    文脈文字列は行全体に対する部分一致なので、同じ行に別の呼び出しを書き足すと
    2 件目も同じ entry に一致してしまう。selector 値の一致も要求したうえで
    1 対 1 に束縛することで、その混入を違反として拾う。
    """
    for entry in entries:
        if entry in used:
            continue
        if entry.target != finding.value:
            continue
        if entry.context in finding.text:
            return entry
    return None


def _stale_reason(
    root: Path, entry: AllowlistEntry, seen_paths: set[str]
) -> str:
    if entry.path not in seen_paths:
        return "file is missing, excluded, or no longer the canonical source"
    text = _read_text(root / entry.path)
    if text is None or entry.context not in text:
        return "context string no longer appears in the file"
    return "context is still there but no longer carries a relative selector"


def _format_report(result: ScanResult) -> str:
    lines: list[str] = []
    if result.violations:
        lines.append(
            f"Group B relative selector check: {len(result.violations)} "
            "violation(s)"
        )
        lines.append("")
        for finding in result.violations:
            if finding.implicit:
                lines.append(
                    f"  {finding.location()}: {finding.tool}(...) has no "
                    f'target= - it defaults to "{finding.value}"'
                )
            else:
                lines.append(
                    f"  {finding.location()}: {finding.tool}"
                    f'(target="{finding.value}")'
                )
            lines.append(f"    | {finding.text}")
        lines.append("")
        lines.append(
            "  close_pane / set_pane_identity must address a pane by its "
            "numeric pane_id"
        )
        lines.append(
            "  (identity-checked against list_panes right before the call), "
            "never by"
        )
        lines.append(
            '  a relative selector - literal "focused" or a bare name. See '
            "T-4.2"
        )
        lines.append("  in docs/contracts/backend-interface-contract.md.")
    if result.stale:
        if lines:
            lines.append("")
        lines.append(
            f"Stale allowlist: {len(result.stale)} entry(ies) matched nothing"
        )
        lines.append("")
        for entry, reason in result.stale:
            lines.append(f"  {entry.path}")
            lines.append(f"    context: {entry.context}")
            lines.append(f"    {reason}")
        lines.append("")
        lines.append(
            "  Remove the entry if the site is migrated, or update its context "
            "string"
        )
        lines.append(
            "  if the prose moved. A carve-out that matches nothing silently "
            "stops"
        )
        lines.append("  guarding the place it was written for.")
    if not lines:
        lines.append(
            f"Group B relative selector check: clean "
            f"({result.scanned_files} file(s) scanned, "
            f"{len(result.allowed)} allowlisted site(s))"
        )
    return "\n".join(lines)


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Check that close_pane / set_pane_identity are never spelled with "
            "a relative selector (literal \"focused\" or a bare name) in the "
            "canonical procedure docs."
        ),
    )
    parser.add_argument(
        "--root",
        default=str(REPO_ROOT),
        help="repository root to scan (default: this checkout)",
    )
    parser.add_argument(
        "--list-allowlist",
        action="store_true",
        help="print the stale-binding allowlist and exit 0",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    # cp932 コンソールでも落ちないようにする（報告する原文行は日本語 prose で、
    # em-dash 等 cp932 に無い文字を含みうる）。
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(errors="replace")  # type: ignore[union-attr]
        except (AttributeError, ValueError):
            pass

    args = _build_parser().parse_args(argv)

    if args.list_allowlist:
        print("Stale-binding allowlist (transport-conditional carve-out):")
        for entry in ALLOWLIST:
            print(f"  {entry.path}")
            print(f"    context: {entry.context}")
            print(f"    reason: {entry.reason}")
        return 0

    root = Path(args.root).resolve()
    if not root.is_dir():
        print(f"error: root is not a directory: {root}")
        return 1

    result = scan(root)
    print(_format_report(result))
    return 0 if result.ok() else 1


if __name__ == "__main__":
    raise SystemExit(main())
