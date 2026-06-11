"""ja 側の transport surface アクセサ — runtime descriptor を読む単一シーム。

Epic #6 次段 D (ja#513): ja 統合シーム。設計 SoT は transport-lab の
``docs/design/ja-migration-plan.md`` §5.1(flag) / §5.2(i 生成系シーム)。

なぜこのモジュールが要るか (§5.2 (i) 単一 SoT):
ja の renga ツール参照は複数の生成器 (``tools/gen_delegate_payload.py`` /
``tools/gen_worker_brief.py`` + テンプレート) が別々に同じ transport
プレフィックス (``mcp__renga-peers__`` / ``mcp__org-broker__``) と spawn 注入
flag を必要とする。各所にハードコードすると runtime と ja で二重管理になり
drift する。そこで **flag → {server 名, 注入 flag, ロール別 tool 集合} を返す
runtime の transport surface descriptor
(:mod:`claude_org_runtime.transport`) を唯一の SoT とし、ja 側生成器は本
モジュール 1 つを経由してそれを読む**。runtime には一切変更を加えず、ja は
pin (``>=0.1.17``) で consume するだけ。

非破壊の絶対条件 (§5.1 / §5.3):
- transport flag の所在は環境変数 ``ORG_TRANSPORT`` (``renga`` | ``broker``)。
- **既定 (無設定) = ``renga``** で現行と bit 等価 (既存生成物が 1 byte も
  変わらない)。``broker`` は ``ORG_TRANSPORT=broker`` を明示した時のみ。
- 解決順は runtime と整合: explicit 引数 > ``ORG_TRANSPORT`` env > 既定 renga。
- renga 経路は削除せず併存 (opt-in / 切戻し可)。
"""
from __future__ import annotations

from typing import Mapping, Optional

# runtime 0.1.17 で公開された transport surface descriptor。ja はこれを
# 一次 SoT として consume する (ハードコードしない, §5.2)。
from claude_org_runtime.transport import (  # noqa: F401  (re-export)
    DEFAULT_TRANSPORT,
    ENV_KEY,
    TRANSPORTS,
    TransportSurface,
    get_surface,
    resolve_transport,
)

# 報告呼び出しに使う bare tool 名。``send_message`` は両 transport 共通の
# 動詞で、transport 固有なのは server / プレフィックス (これらは descriptor が
# 所有)。とはいえ tool 集合の SoT も descriptor (``tools_for_role``) なので、
# この名前が descriptor の公開集合から外れていないかを参照時に検証し、将来の
# rename / 削除を黙って取り逃さない (drift 防止, §5.2)。
_SEND_MESSAGE_TOOL = "send_message"

__all__ = [
    "DEFAULT_TRANSPORT",
    "ENV_KEY",
    "TRANSPORTS",
    "TransportSurface",
    "resolve",
    "surface",
    "fq_prefix",
    "server_name",
    "send_message_call",
    "spawn_inject",
    "allow_entries",
    "rewrite_allow_entries",
]


def resolve(
    explicit: Optional[str] = None,
    *,
    env: Optional[Mapping[str, str]] = None,
) -> str:
    """有効な transport flag (``renga`` | ``broker``) を返す。

    解決順は runtime の ``transport_allowlist`` と整合: explicit 引数 >
    ``ORG_TRANSPORT`` env > 既定 ``renga`` (§5.1)。``env=None`` は
    ``os.environ`` を読む。空文字列・未知値は ``ValueError``。
    """
    return resolve_transport(explicit, env=env)


def surface(
    flag: Optional[str] = None,
    *,
    env: Optional[Mapping[str, str]] = None,
) -> TransportSurface:
    """transport flag の :class:`TransportSurface` を返す (flag=None は解決)。"""
    return get_surface(flag, env=env)


def server_name(
    flag: Optional[str] = None,
    *,
    env: Optional[Mapping[str, str]] = None,
) -> str:
    """MCP サーバー名 (``renga-peers`` / ``org-broker``) を返す。

    生成物の中で「窓口がコピーする send_message 呼び出しの輸送名」など、FQ
    プレフィックスではなくサーバー名そのものを差し込む箇所に使う。
    """
    return surface(flag, env=env).server


def fq_prefix(
    flag: Optional[str] = None,
    *,
    env: Optional[Mapping[str, str]] = None,
) -> str:
    """FQ MCP tool プレフィックス (``mcp__renga-peers__`` / ``mcp__org-broker__``)。"""
    return surface(flag, env=env).fq_prefix


def send_message_call(
    flag: Optional[str] = None,
    *,
    env: Optional[Mapping[str, str]] = None,
) -> str:
    """ワーカー / 窓口の報告に使う FQ ``send_message`` ツール名を返す。

    renga 既定では ``mcp__renga-peers__send_message`` (現行と bit 等価)、
    ``broker`` では ``mcp__org-broker__send_message``。プレフィックスは
    role 非依存 (server ベース) なので role を取らない。

    ``send_message`` が descriptor の公開 tool 集合に存在することを検証し、
    runtime 側で rename / 削除があれば即座に ``ValueError`` で顕在化させる
    (ハードコード名が descriptor から drift するのを防ぐ, §5.2)。
    """
    surf = surface(flag, env=env)
    if _SEND_MESSAGE_TOOL not in surf.tools_for_role("worker"):
        raise ValueError(
            f"transport {surf.flag!r} descriptor no longer exposes "
            f"{_SEND_MESSAGE_TOOL!r} for the worker tier; ja generators "
            "must be re-derived from the runtime transport descriptor "
            "(claude_org_runtime.transport)."
        )
    return f"{surf.fq_prefix}{_SEND_MESSAGE_TOOL}"


def spawn_inject(
    flag: Optional[str] = None,
    *,
    broker_mcp_config: Optional[str] = None,
    env: Optional[Mapping[str, str]] = None,
) -> str:
    """子ペイン spawn 時にランチャが注入する flag 文字列を返す。

    renga は固定 ``--dangerously-load-development-channels
    server:renga-peers``、broker は ``--mcp-config <broker>``
    (``broker_mcp_config`` で具体化、未指定なら ``<broker>`` プレースホルダ)。
    """
    return surface(flag, env=env).spawn_inject(broker_mcp_config=broker_mcp_config)


def allow_entries(
    role: str,
    *,
    flag: Optional[str] = None,
    env: Optional[Mapping[str, str]] = None,
) -> list:
    """role の ``mcp__<server>__<tool>`` allowlist エントリ (transport 解決込み)。

    §5.3 (allowlist の flag-aware 化) の単一 SoT。runtime の
    :func:`claude_org_runtime.settings.generator.transport_allowlist`
    (= descriptor 駆動) を consume する。``flag`` 解決順は他のアクセサと整合:
    explicit > ``ORG_TRANSPORT`` env > 既定 ``renga``。

    既定 ``renga`` では全ロールが required-14 surface
    (``mcp__renga-peers__*``)、``broker`` ではロールの auth tier に応じた
    ``mcp__org-broker__*`` 集合 (worker/curator=4・dispatcher/secretary=ops)
    を返す。未知ロールは broker で messaging tier (4) に落ちる
    (descriptor の default-deny 既定)。
    """
    # runtime の生成器 API を一次 SoT として lazy import (ja は consume のみ、
    # ハードコードしない, §5.2 (i) / §5.3)。
    from claude_org_runtime.settings.generator import transport_allowlist

    return list(transport_allowlist(role, transport=flag, env=env))


def rewrite_allow_entries(
    entries,
    role: str,
    *,
    flag: Optional[str] = None,
    env: Optional[Mapping[str, str]] = None,
) -> list:
    """allow / required_allow 等の文字列リスト中の renga MCP ブロックを、
    解決した transport の role-tier 集合へ置換して返す。

    **非破壊の核 (§5.3)**: 既定 ``renga`` (``DEFAULT_TRANSPORT``) では
    **入力をそのまま返す (恒等)** ので、既存の生成物・schema 期待は 1 byte も
    変わらない (bit 等価)。``broker`` 等の非既定 flag のときだけ、renga の FQ
    プレフィックス (``mcp__renga-peers__``) で始まるエントリ群を除去し、**最初に
    現れた位置へ** transport の tier エントリを挿入する。``Bash(...)`` 等の
    非 MCP エントリは順序を保って残す。

    renga ブロックが 1 つも無いリスト (例: mcp を持たないロールの per-role
    テンプレート) は非既定 flag でも素通し (tier エントリを勝手に注入しない =
    「renga ブロックの swap」に限定し、無いものは足さない)。
    """
    resolved = resolve(flag, env=env)
    if resolved == DEFAULT_TRANSPORT:
        # 既定 renga: 恒等。byte 等価を構造的に保証する。
        return list(entries)
    renga_prefix = surface(DEFAULT_TRANSPORT, env=env).fq_prefix
    tier = allow_entries(role, flag=resolved, env=env)
    out: list = []
    inserted = False
    for entry in entries:
        if isinstance(entry, str) and entry.startswith(renga_prefix):
            if not inserted:
                out.extend(tier)
                inserted = True
            # renga エントリ本体は drop (tier で置換済み)。
            continue
        out.append(entry)
    return out
