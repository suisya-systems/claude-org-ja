"""Report how often each registered defense stopped something (Issue #1191).

Reads the defense register (``docs/operations/defense-register.md``) and the
``events`` rows that record a stop, and prints, for the last ``--days`` days:
stop count per defense, defenses that never stopped, and defenses with
suspected over-blocking. ``/org-retro`` attaches this output to every retro.

Over-blocking is an approximation: a stop that follows another stop of the same
defense by the same actor within ``REPEAT_WINDOW_MIN`` minutes is counted as a
suspected rephrase-and-retry. A human decides whether it was a correct
workaround or a slip-through; the report never removes anything.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

# The events row a stop leaves behind (written by the hook-deny recording in
# Issue #1194). ponytail: kind/field names are provisional until #1194 lands;
# change only these three constants.
DENY_KIND = "hook_denied"
DEFENSE_FIELD = "hook"  # payload key holding the register id
ACTOR_FIELD = "actor"   # events.actor column

REPEAT_WINDOW_MIN = 10
REGISTER = "docs/operations/defense-register.md"
SUSPECT_MIN = 2  # suspected over-blocks needed to list a defense as a candidate


def load_register(path: Path) -> "list[dict]":
    rows = []
    for line in path.read_text(encoding="utf-8").splitlines():
        cells = [c.strip() for c in line.strip().strip("|").split("|")]
        if not line.startswith("|") or len(cells) != 4 or cells[0] in ("id", "") \
                or set(cells[0]) <= {"-"}:
            continue
        rows.append({"id": cells[0], "type": cells[1],
                     "path": cells[2].strip("`"), "failure": cells[3]})
    return rows


def _ts(s: str) -> datetime:
    return datetime.fromisoformat(re.sub(r"Z$", "+00:00", s))


def collect(conn, since: datetime) -> "dict[str, list[tuple[str, datetime]]]":
    """defense id -> [(actor, time)] oldest first."""
    out: "dict[str, list]" = {}
    q = ("SELECT occurred_at, COALESCE(actor, ''), "
         "json_extract(payload_json, ?) FROM events "
         "WHERE kind = ? AND occurred_at >= ? ORDER BY occurred_at")
    for at, actor, did in conn.execute(
            q, (f"$.{DEFENSE_FIELD}", DENY_KIND, since.strftime("%Y-%m-%dT%H:%M:%SZ"))):
        if did:
            out.setdefault(did, []).append((actor, _ts(at)))
    return out


def suspects(stops: "list[tuple[str, datetime]]") -> int:
    last: "dict[str, datetime]" = {}
    n = 0
    for actor, t in stops:
        if actor in last and t - last[actor] <= timedelta(minutes=REPEAT_WINDOW_MIN):
            n += 1
        last[actor] = t
    return n


def render(register: "list[dict]", stops: "dict", days: int) -> str:
    known = {r["id"] for r in register}
    lines = [f"## 防御の集計（直近 {days} 日）", "",
             "| id | 止めた回数 | 止めすぎの疑い |", "|---|---|---|"]
    for r in register:
        s = stops.get(r["id"], [])
        lines.append(f"| {r['id']} | {len(s)} | {suspects(s)} |")
    never = [r["id"] for r in register if not stops.get(r["id"])]
    over = [r["id"] for r in register if suspects(stops.get(r["id"], [])) >= SUSPECT_MIN]
    lines += ["", "- 一度も止めていない: " + (", ".join(never) or "なし"),
              "- 止めすぎの疑い（近似。正しい回避かすり抜けかは人が見る）: "
              + (", ".join(over) or "なし")]
    unknown = sorted(set(stops) - known)
    if unknown:
        lines.append("- 一覧にない id の記録（一覧へ追加が要る）: " + ", ".join(unknown))
    return "\n".join(lines)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Per-defense stop report for /org-retro.")
    ap.add_argument("--days", type=int, default=30)
    ap.add_argument("--db", type=Path, default=None, help="state.db path")
    ap.add_argument("--register", type=Path, default=None)
    a = ap.parse_args(argv)

    from tools.state_db import connect
    from tools.state_db.discover import discover_repo_root, resolve_state_db_path

    root = discover_repo_root()
    register = load_register(a.register or root / REGISTER)
    db = resolve_state_db_path(a.db)
    if not db.is_file():
        print(f"state.db が見つかりません: {db}", file=sys.stderr)
        return 1
    since = datetime.now(timezone.utc) - timedelta(days=a.days)
    conn = connect(db)
    try:
        stops = collect(conn, since)
    finally:
        conn.close()
    print(render(register, stops, a.days))
    return 0


if __name__ == "__main__":
    sys.exit(main())
