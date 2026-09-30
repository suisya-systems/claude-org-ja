"""Unit tests for tools/work_discovery_goals.py — Phase 5 goal stage (design §12).

The judge is always faked through the ``runner`` seam (or, once, a stub
python script run through the real subprocess runner); the real ``claude``
CLI is never invoked.
"""

from __future__ import annotations

import io
import json
import os
import stat
import sys
import tempfile
import math
import unittest
from unittest import mock
from contextlib import redirect_stdout
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from tools import work_discovery_goals as wdg  # noqa: E402

NOW = datetime(2026, 9, 30, 12, 0, 0, tzinfo=timezone.utc)

LEDGER = """# Goals

## G1 Ship login
Users can log in.
- unmet if: login is broken

## G2 Fast CI
- unmet if: CI takes > 10 min
"""


def _cand(slug, n, *, title="t", body="b", labels=(), updated="2026-09-01T00:00:00Z"):
    return {"repo": slug, "issue": n, "title": title, "summary": f"s{n}", "priority": "medium",
            "rank": None, "_real_repo": slug, "_body": body, "_labels": list(labels),
            "_updated_at": updated}


def _envelope(judgements, *, cost=0.1, subtype="success", is_error=False):
    return json.dumps({"type": "result", "subtype": subtype, "is_error": is_error,
                       "total_cost_usd": cost, "structured_output": {"judgements": judgements}})


def _j(key, clause="G1", why="because", op=None):
    return {"key": key, "clause": clause, "why": why, "request": "do it",
            "open_points": op if op is not None else []}


def _material_from_argv(argv):
    spf = argv[argv.index("--system-prompt-file") + 1]
    lines = Path(spf).read_text(encoding="utf-8").strip().split("\n")
    return json.loads(lines[-1])


class FakeRunner:
    """Answers each sent candidate via ``decide(key) -> clause`` unless
    ``stdout`` / ``timed_out`` / ``rc`` override the whole reply."""

    def __init__(self, decide=None, stdout=None, rc=0, timed_out=False, cost=0.1):
        self.decide = decide or (lambda key: "G1")
        self.stdout, self.rc, self.timed_out, self.cost = stdout, rc, timed_out, cost
        self.calls = []

    def __call__(self, argv, cwd, timeout):
        mat = _material_from_argv(argv)
        self.calls.append({"argv": argv, "cwd": cwd, "timeout": timeout, "material": mat})
        if self.timed_out:
            return None, "", True
        if self.stdout is not None:
            return self.rc, self.stdout, False
        js = [_j(c["key"], self.decide(c["key"])) for c in mat["candidates"]]
        return self.rc, _envelope(js, cost=self.cost), False


def _key(c):
    return (c["issue"],)


class Base(unittest.TestCase):
    def setUp(self):
        self._td = tempfile.TemporaryDirectory()
        self.root = Path(self._td.name)
        self.goals = self.root / "goals"
        self.state = self.root / "state"
        self.clock = [NOW]

    def tearDown(self):
        self._td.cleanup()

    def write_ledger(self, slug, text=LEDGER):
        p = self.goals / f"{slug}.md"
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text, encoding="utf-8")
        return p

    def cfg(self, runner=None, **kw):
        kw.setdefault("judge_cmd", sys.executable)  # a path: no PATH lookup
        return wdg.GoalConfig(goals_dir=self.goals, state_dir=self.state,
                              now=lambda: self.clock[0], runner=runner, **kw)

    def rank(self, pool, runner=None, display=lambda s: s, **kw):
        return wdg.apply_goal_rank(pool, legacy_key=_key, cap_key=_key,
                                   display_repo=display, config=self.cfg(runner, **kw))

    def rank_err(self, pool, runner=None, **kw):
        with self.assertRaises(wdg.GoalStageError) as cm:
            self.rank(pool, runner, **kw)
        return cm.exception


# ----------------------------------------------------------------------
# Ledger grammar (§12.3)
# ----------------------------------------------------------------------


class TestParseLedger(unittest.TestCase):
    def test_basic(self):
        cl = wdg.parse_ledger(LEDGER)
        self.assertEqual([c["id"] for c in cl], ["G1", "G2"])
        self.assertEqual(cl[0]["heading"], "Ship login")
        self.assertEqual(cl[0]["body"], "Users can log in.")
        self.assertEqual(cl[0]["unmet_if"], ["login is broken"])
        self.assertEqual(cl[1]["index"], 2)

    def test_bom_and_crlf(self):
        cl = wdg.parse_ledger("\ufeff" + LEDGER.replace("\n", "\r\n"))
        self.assertEqual(cl[0]["heading"], "Ship login")
        self.assertEqual(cl[1]["unmet_if"], ["CI takes > 10 min"])

    def test_fenced_block_ignored(self):
        text = "## G1 A\n```\n## G2 fake\n- unmet if: fake\n```\n- unmet if: real\n"
        cl = wdg.parse_ledger(text)
        self.assertEqual(len(cl), 1)
        self.assertEqual(cl[0]["unmet_if"], ["real"])
        self.assertNotIn("fake", cl[0]["body"])

    def test_other_heading_ends_clause(self):
        text = "## G1 A\n- unmet if: x\n## Goals overview\nignored text\n- unmet if: y\n"
        cl = wdg.parse_ledger(text)
        self.assertEqual(cl[0]["unmet_if"], ["x"])
        self.assertEqual(cl[0]["body"], "")

    def test_h1_ends_clause_and_h3_is_body(self):
        text = "## G1 A\n### detail\n- unmet if: x\n# Top\n- unmet if: y\n"
        cl = wdg.parse_ledger(text)
        self.assertEqual(cl[0]["body"], "### detail")
        self.assertEqual(cl[0]["unmet_if"], ["x"])

    def test_unmet_after_ending_heading_does_not_count(self):
        with self.assertRaisesRegex(wdg.LedgerError, "unmet"):
            wdg.parse_ledger("## G1 A\n## Notes\n- unmet if: x\n")

    def test_empty_title_violation(self):
        for bad in ("## G1\n- unmet if: x\n", "## G1   \n- unmet if: x\n",
                    "## G01 A\n- unmet if: x\n", "## G1x A\n- unmet if: x\n"):
            with self.assertRaisesRegex(wdg.LedgerError, "malformed", msg=bad):
                wdg.parse_ledger(bad)

    def test_lowercase_g_is_malformed(self):
        for bad in ("## g1 A\n- unmet if: x\n",
                    "## G1 A\n- unmet if: x\n## g2 B\n- unmet if: y\n"):
            with self.assertRaisesRegex(wdg.LedgerError, "malformed", msg=bad):
                wdg.parse_ledger(bad)
        # A prose heading starting with G is still just another heading.
        self.assertEqual(len(wdg.parse_ledger("## G1 A\n- unmet if: x\n## Goals\n")), 1)

    def test_gap_and_duplicate_and_order(self):
        for bad in ("## G1 A\n- unmet if: x\n## G3 C\n- unmet if: y\n",
                    "## G1 A\n- unmet if: x\n## G1 B\n- unmet if: y\n",
                    "## G2 A\n- unmet if: x\n## G1 B\n- unmet if: y\n"):
            with self.assertRaisesRegex(wdg.LedgerError, "numbering", msg=bad):
                wdg.parse_ledger(bad)

    def test_zero_clauses(self):
        with self.assertRaisesRegex(wdg.LedgerError, "no clauses"):
            wdg.parse_ledger("# Goals\njust prose\n")

    def test_missing_unmet(self):
        with self.assertRaisesRegex(wdg.LedgerError, "G2"):
            wdg.parse_ledger("## G1 A\n- unmet if: x\n## G2 B\nprose\n")

    def test_unmet_variants(self):
        cl = wdg.parse_ledger("## G1 A\n  * Unmet IF:  spaced  \n- unmet if:\n")
        self.assertEqual(cl[0]["unmet_if"], ["spaced"])
        self.assertEqual(cl[0]["body"], "- unmet if:")


class TestLoadGoals(Base):
    def test_ok_and_lowercases_slug(self):
        self.write_ledger("acme/app")
        g = wdg.load_goals(self.goals, "Acme/App")
        self.assertEqual((g["status"], g["repo"], len(g["clauses"])), ("ok", "acme/app", 2))

    def test_unset(self):
        self.assertEqual(wdg.load_goals(self.goals, "acme/app")["status"], "unset")
        (self.goals / "acme").mkdir(parents=True)
        self.assertEqual(wdg.load_goals(self.goals, "acme/app")["status"], "unset")

    def test_missing_goals_dir_is_unset(self):
        r = wdg.load_goals(self.root / "nope", "o/r")
        self.assertEqual(r["status"], "unset")

    def test_unlistable_goals_dir_is_error_not_unset(self):
        # A regular file where the goals dir belongs (or a permission error)
        # must surface as goal_errors, not as "write your goals" (Codex P2).
        not_a_dir = self.root / "goals-file"
        not_a_dir.write_text("x", encoding="utf-8")
        r = wdg.load_goals(not_a_dir, "o/r")
        self.assertEqual(r["status"], "error")
        self.assertIn("goals dir unreadable", r["error"])
        self.write_ledger("o/r")
        with mock.patch.object(Path, "iterdir", side_effect=PermissionError("denied")):
            r = wdg.load_goals(self.goals, "o/r")
        self.assertEqual(r["status"], "error")
        self.assertIn("PermissionError", r["error"])

    def test_case_mismatch_file(self):
        self.write_ledger("acme/App")
        g = wdg.load_goals(self.goals, "acme/app")
        self.assertEqual(g["status"], "error")
        self.assertTrue(g["error"].startswith("case mismatch: "), g["error"])
        self.assertIn("App.md", g["error"])

    def test_case_mismatch_owner_dir(self):
        self.write_ledger("Acme/app")
        g = wdg.load_goals(self.goals, "acme/app")
        self.assertEqual(g["status"], "error")
        self.assertIn("case mismatch", g["error"])

    def test_ledger_error_and_bad_slug(self):
        self.write_ledger("acme/app", "nothing here\n")
        self.assertEqual(wdg.load_goals(self.goals, "acme/app")["error"], "no clauses")
        for bad in ("noslash", "a/b/c", "../x", "a/.."):
            self.assertEqual(wdg.load_goals(self.goals, bad)["status"], "error", bad)

    def test_unreadable_utf8(self):
        p = self.write_ledger("acme/app")
        p.write_bytes(b"## G1 \xff\n")
        self.assertIn("unreadable", wdg.load_goals(self.goals, "acme/app")["error"])


# ----------------------------------------------------------------------
# Refs / put-aside (§12.5)
# ----------------------------------------------------------------------


class TestPutAside(Base):
    def test_normalize_ref(self):
        self.assertEqual(wdg.normalize_ref(" Owner/Repo#12 "), "owner/repo#12")
        for bad in ("owner/repo", "repo#1", "o/r#0", "o/r#x", "o/r/x#1", "", "../r#1"):
            with self.assertRaises(ValueError, msg=bad):
                wdg.normalize_ref(bad)

    def test_append_and_latest(self):
        wdg.append_put_aside(self.state, "A/B#1", "n1", now=NOW - timedelta(days=2))
        e = wdg.append_put_aside(self.state, "a/b#1", "later", now=NOW)
        self.assertEqual(e, {"ref": "a/b#1", "at": "2026-09-30T12:00:00Z", "note": "later"})
        wdg.append_put_aside(self.state, "a/b#1", "older", now=NOW - timedelta(days=5))
        with open(self.state / "put_aside.jsonl", "a", encoding="utf-8") as fh:
            fh.write("{broken\n")
            fh.write(json.dumps({"ref": "bad", "at": "2026-01-01T00:00:00Z"}) + "\n")
        latest, sigs = wdg.load_put_aside(self.state)
        self.assertEqual(latest, {"a/b#1": NOW})
        self.assertEqual(len(sigs), 2)

    def test_missing_file(self):
        self.assertEqual(wdg.load_put_aside(self.state), ({}, []))

    def test_put_aside_comparison_in_rank(self):
        self.write_ledger("a/b")
        wdg.append_put_aside(self.state, "a/b#1", "x", now=NOW - timedelta(days=1))
        wdg.append_put_aside(self.state, "a/b#2", "x", now=NOW - timedelta(days=1))
        wdg.append_put_aside(self.state, "a/b#3", "x", now=NOW - timedelta(days=1))
        wdg.append_put_aside(self.state, "a/b#5", "x", now=NOW - timedelta(days=1))
        pool = [
            _cand("a/b", 1, updated="2026-09-01T00:00:00Z"),   # older -> put aside
            _cand("a/b", 2, updated="2026-09-30T00:00:00Z"),   # updated after -> kept
            _cand("a/b", 3, updated=""),                        # unknown -> kept
            _cand("a/b", 4),                                    # no record -> kept
            _cand("a/b", 5, updated="2026-09-29T12:00:00+00:00"),  # equal -> put aside
        ]
        r = self.rank(pool, FakeRunner())
        self.assertEqual(r["goal_rank"]["put_aside_count"], 2)
        ex = [(e["issue"], e["reason"]) for e in r["excluded_goal"]]
        self.assertEqual(ex, [(1, "put_aside"), (5, "put_aside")])
        self.assertEqual(r["goal_rank"]["judge"]["candidates_sent"], 3)

    def test_cli(self):
        buf = io.StringIO()
        with redirect_stdout(buf):
            rc = wdg.main(["put-aside", "--ref", "X/Y#3", "--note", "later", "--state-dir", str(self.state)])
        self.assertEqual(rc, 0)
        self.assertEqual(json.loads(buf.getvalue())["ref"], "x/y#3")
        buf = io.StringIO()
        with redirect_stdout(buf):
            rc = wdg.main(["put-aside", "--ref", "nope", "--claude-org-root", str(self.root)])
        self.assertEqual(rc, 2)
        self.assertIn("error", json.loads(buf.getvalue()))
        self.assertFalse((self.root / ".state").exists())

    def test_cli_stdout_is_ascii(self):
        buf = io.StringIO()
        with redirect_stdout(buf):
            wdg.main(["put-aside", "--ref", "x/y#1", "--note", "今はやらない", "--state-dir", str(self.state)])
        self.assertTrue(buf.getvalue().isascii())
        self.assertEqual(json.loads(buf.getvalue())["note"], "今はやらない")

    def test_cli_usage_error_is_json(self):
        for argv in ([], ["put-aside"], ["put-aside", "--ref", "x/y#1", "--bogus"]):
            buf = io.StringIO()
            with redirect_stdout(buf), self.assertRaises(SystemExit) as cm:
                wdg.main(argv)
            self.assertEqual(cm.exception.code, 2, argv)
            self.assertIn("error", json.loads(buf.getvalue()), argv)


class TestValidateCli(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        self.goals = self.root / "registry" / "goals"

    def tearDown(self):
        self._tmp.cleanup()

    def run_cli(self, *argv):
        buf = io.StringIO()
        with redirect_stdout(buf):
            rc = wdg.main(["validate", *argv])
        self.assertTrue(buf.getvalue().isascii())
        return rc, json.loads(buf.getvalue())

    def write(self, rel, text):
        p = self.root / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text, encoding="utf-8")
        return p

    def test_file_ok(self):
        rc, out = self.run_cli("--file", str(self.write("draft.md", LEDGER)))
        self.assertEqual((rc, out["status"]), (0, "ok"))
        self.assertEqual([c["id"] for c in out["clauses"]], ["G1", "G2"])

    def test_file_error_uses_scan_parser(self):
        bad = LEDGER.replace("- unmet if: CI takes > 10 min", "")
        rc, out = self.run_cli("--file", str(self.write("draft.md", bad)))
        self.assertEqual((rc, out["status"]), (1, "error"))
        self.assertIn("G2", out["error"])

    def test_file_missing(self):
        rc, out = self.run_cli("--file", str(self.root / "nope.md"))
        self.assertEqual(rc, 1)
        self.assertIn("unreadable", out["error"])

    def test_repo_ok_unset_and_default_goals_dir(self):
        rc, out = self.run_cli("--repo", "a/b", "--claude-org-root", str(self.root))
        self.assertEqual((rc, out["status"]), (1, "unset"))
        self.write("registry/goals/a/b.md", LEDGER)
        rc, out = self.run_cli("--repo", "A/B", "--claude-org-root", str(self.root))
        self.assertEqual((rc, out["status"], out["repo"]), (0, "ok", "a/b"))

    def test_repo_goals_dir_override(self):
        self.write("elsewhere/a/b.md", "## G2 x\n- unmet if: y\n")
        rc, out = self.run_cli("--repo", "a/b", "--goals-dir", str(self.root / "elsewhere"))
        self.assertEqual((rc, out["status"]), (1, "error"))

    def test_usage_needs_exactly_one_target(self):
        for argv in (["validate"], ["validate", "--file", "x", "--repo", "a/b"]):
            buf = io.StringIO()
            with redirect_stdout(buf), self.assertRaises(SystemExit) as cm:
                wdg.main(argv)
            self.assertEqual(cm.exception.code, 2, argv)
            self.assertIn("error", json.loads(buf.getvalue()), argv)


# ----------------------------------------------------------------------
# Structural check (§12.4 items 1-5)
# ----------------------------------------------------------------------


class TestStructuralCheck(unittest.TestCase):
    IDS = {"a/b#1": {"G1", "G2"}, "a/b#2": {"G1", "G2"}}

    def check(self, js):
        return wdg.check_judgements({"judgements": js}, self.IDS)

    def bad(self, js, pat):
        with self.assertRaisesRegex(wdg.StructuralError, pat):
            self.check(js)

    def test_ok(self):
        op = [{"point": "p", "options": ["A", "B"], "recommend": "A"}]
        r = self.check([_j("a/b#1", op=op), _j("a/b#2", None)])
        self.assertIsNone(r["a/b#2"]["clause"])
        self.assertEqual(r["a/b#1"]["open_points"], op)

    def test_rule1_shape_and_lengths(self):
        with self.assertRaises(wdg.StructuralError):
            wdg.check_judgements({"judgements": {}}, self.IDS)
        with self.assertRaises(wdg.StructuralError):
            wdg.check_judgements(None, self.IDS)
        ok2 = _j("a/b#2")
        self.bad([_j("a/b#1", why="x" * 501), ok2], "why")
        self.bad([dict(_j("a/b#1"), request="x" * 1001), ok2], "request")
        self.bad([_j("a/b#1", op=[{"point": "p" * 201, "options": ["A"], "recommend": "A"}]), ok2], "point")
        self.bad([_j("a/b#1", op=[{"point": "p", "options": ["A" * 201], "recommend": "A" * 201}]), ok2],
                 "options")
        op = {"point": "p", "options": ["A"], "recommend": "A"}
        self.bad([_j("a/b#1", op=[op] * 4), ok2], "open_points")
        missing = _j("a/b#1")
        del missing["why"]
        self.bad([missing, ok2], "why")
        self.bad(["str", ok2], "not an object")

    def test_rule1_exact_fields(self):
        ok2 = _j("a/b#2")
        with self.assertRaisesRegex(wdg.StructuralError, "extra: note"):
            wdg.check_judgements({"judgements": [_j("a/b#1"), ok2], "note": "x"}, self.IDS)
        self.bad([dict(_j("a/b#1"), rank=1), ok2], "extra: rank")
        no_clause = _j("a/b#1")
        del no_clause["clause"]
        self.bad([no_clause, ok2], "missing: clause")
        op = {"point": "p", "options": ["A"], "recommend": "A", "why": "z"}
        self.bad([_j("a/b#1", op=[op]), ok2], "open_point.*extra: why")
        op = {"point": "p", "options": ["A"]}
        self.bad([_j("a/b#1", op=[op]), ok2], "open_point.*missing: recommend")

    def test_rule2_unknown_key(self):
        self.bad([_j("a/b#1"), _j("a/b#2"), _j("a/b#9")], "unknown key")

    def test_rule3_duplicate_and_missing(self):
        self.bad([_j("a/b#1"), _j("a/b#1"), _j("a/b#2")], "duplicate")
        self.bad([_j("a/b#1")], "missing")

    def test_rule4_clause(self):
        self.bad([_j("a/b#1", "G3"), _j("a/b#2")], "not a clause")
        self.bad([_j("a/b#1", 1), _j("a/b#2")], "string or null")

    def test_rule5_options(self):
        ok2 = _j("a/b#2")
        self.bad([_j("a/b#1", op=[{"point": "p", "options": ["A"], "recommend": "B"}]), ok2], "recommend")
        self.bad([_j("a/b#1", op=[{"point": "p", "options": [], "recommend": "A"}]), ok2], "options")
        self.bad([_j("a/b#1", op=[{"point": "p", "options": list("ABCDE"), "recommend": "A"}]), ok2],
                 "options")


# ----------------------------------------------------------------------
# apply_goal_rank: judging, cache, failures, ranking
# ----------------------------------------------------------------------


class TestJudgeCall(Base):
    def test_argv_shape_and_material(self):
        self.write_ledger("a/b")
        fr = FakeRunner()
        r = self.rank([_cand("a/b", 2, body="x\r\ny" + "z" * 700, labels=["b", "a"]),
                       _cand("a/b", 1)], fr, judge_timeout=7)
        self.assertEqual(len(fr.calls), 1)
        argv = fr.calls[0]["argv"]
        self.assertEqual(argv[0], sys.executable)
        self.assertEqual(argv[1:3], ["-p", wdg.JUDGE_ARGV_PROMPT])
        self.assertEqual(argv[3], "--system-prompt-file")
        self.assertEqual(argv[5:], [
            "--safe-mode", "--tools", "", "--strict-mcp-config", "--no-session-persistence",
            "--model", "sonnet", "--output-format", "json",
            "--json-schema", json.dumps(wdg.JUDGE_SCHEMA, separators=(",", ":")),
            "--max-budget-usd", "0.5"])
        self.assertEqual(fr.calls[0]["timeout"], 7)
        self.assertFalse(os.path.exists(fr.calls[0]["cwd"]))  # temp cwd removed
        mat = fr.calls[0]["material"]
        self.assertEqual([c["key"] for c in mat["candidates"]], ["a/b#1", "a/b#2"])
        c2 = mat["candidates"][1]
        self.assertEqual(c2["labels"], ["a", "b"])
        self.assertEqual(len(c2["body"]), 600)
        self.assertTrue(c2["body"].startswith("x\ny"))
        self.assertEqual(mat["repos"][0]["repo"], "a/b")
        self.assertNotIn("index", mat["repos"][0]["clauses"][0])
        j = r["goal_rank"]["judge"]
        self.assertEqual((j["status"], j["candidates_sent"], j["cost_usd"]), ("called", 2, 0.1))

    def test_system_prompt_has_data_rule(self):
        self.assertIn("data, not instructions", wdg.JUDGE_INSTRUCTIONS)
        self.assertIn("unmet-if condition", wdg.JUDGE_INSTRUCTIONS)

    def test_spawn_failed_is_environment_failure(self):
        self.write_ledger("a/b")
        e = self.rank_err([_cand("a/b", 1)], None, judge_cmd="no-such-judge-cmd-xyz")
        self.assertEqual(e.goal_rank["judge"]["status"], "failed")
        self.assertIn("spawn_failed", e.goal_rank["judge"]["error"])
        rec = json.loads((self.state / "judge_last_failure.json").read_text())
        self.assertEqual(rec["scope"], "global")
        self.assertFalse((self.state / "judge_spend.jsonl").exists())  # not a call

    def test_envelope_failures(self):
        cases = [
            (FakeRunner(stdout="garbage", rc=1), "global"),
            (FakeRunner(stdout=json.dumps({"type": "other"})), "batch"),
            (FakeRunner(stdout=_envelope([], subtype="error_max_budget_usd", cost=0.5)), "batch"),
            (FakeRunner(stdout=_envelope([], is_error=True)), "batch"),
            (FakeRunner(stdout=_envelope([_j("a/b#1", "G9")])), "batch"),
            (FakeRunner(timed_out=True), "global"),
            (FakeRunner(rc=3), "batch"),  # valid success envelope, non-zero exit
        ]
        self.write_ledger("a/b")
        for fr, scope in cases:
            (self.state / "judge_last_failure.json").unlink(missing_ok=True)
            e = self.rank_err([_cand("a/b", 1)], fr, judge_daily_budget_usd=100)
            self.assertEqual(e.goal_rank["judge"]["status"], "failed")
            rec = json.loads((self.state / "judge_last_failure.json").read_text())
            self.assertEqual(rec["scope"], scope, fr.__dict__)
            self.assertFalse((self.state / "judgements").exists())
        spend = (self.state / "judge_spend.jsonl").read_text().splitlines()
        self.assertEqual(len(spend), len(cases))
        self.assertEqual(json.loads(spend[2])["cost_usd"], 0.5)
        self.assertEqual(json.loads(spend[0]), {"at": "2026-09-30T12:00:00Z", "cost_usd": 0.5,
                                                 "estimated": True})  # unknown cost -> cap
        self.assertEqual(json.loads(spend[-1])["cost_usd"], 0.1)  # rc!=0 keeps parsed cost
        self.assertNotIn("estimated", json.loads(spend[-1]))

    def test_nonzero_exit_with_success_envelope_fails(self):
        self.write_ledger("a/b")
        e = self.rank_err([_cand("a/b", 1)], FakeRunner(rc=1, cost=0.2))
        j = e.goal_rank["judge"]
        self.assertEqual((j["status"], j["cost_usd"], j["candidates_sent"]), ("failed", 0.2, 1))
        self.assertIn("non-zero exit 1", j["error"])
        self.assertFalse((self.state / "judgements").exists())

    def test_any_exception_before_call_is_recorded(self):
        self.write_ledger("a/b")

        def boom(argv, cwd, timeout):
            raise RuntimeError("runner exploded")

        for patch, runner in ((None, boom),
                              (mock.patch.object(wdg.tempfile, "mkdtemp",
                                                 side_effect=ValueError("no tmp")), FakeRunner())):
            (self.state / "judge_last_failure.json").unlink(missing_ok=True)
            if patch:
                with patch:
                    e = self.rank_err([_cand("a/b", 1)], runner)
            else:
                e = self.rank_err([_cand("a/b", 1)], runner)
            j = e.goal_rank["judge"]
            self.assertEqual((j["status"], j["candidates_sent"], j["candidates_pending"]), ("failed", 0, 1))
            self.assertIn("spawn_failed", j["error"])
            rec = json.loads((self.state / "judge_last_failure.json").read_text())
            self.assertEqual(rec["scope"], "global")
            self.assertNotEqual(rec["reason"], "in_progress")
        self.assertFalse((self.state / "judge_spend.jsonl").exists())

    def test_nan_cost_never_reaches_output(self):
        self.write_ledger("a/b")
        out = json.dumps({"type": "result", "subtype": "success", "is_error": False,
                          "total_cost_usd": float("nan"),
                          "structured_output": {"judgements": [_j("a/b#1")]}})
        self.assertIn("NaN", out)
        r = self.rank([_cand("a/b", 1)], FakeRunner(stdout=out))
        self.assertIsNone(r["goal_rank"]["judge"]["cost_usd"])
        json.dumps(r["goal_rank"], allow_nan=False)
        line = json.loads((self.state / "judge_spend.jsonl").read_text())
        self.assertEqual(line, {"at": "2026-09-30T12:00:00Z", "cost_usd": 0.5, "estimated": True})

    def test_resolve_cmd_makes_paths_absolute(self):
        self.assertEqual(wdg._resolve_cmd("./judge"), os.path.abspath("judge"))
        self.assertEqual(wdg._resolve_cmd("sub/judge"), os.path.abspath("sub/judge"))
        self.assertEqual(wdg._resolve_cmd(".judge"), os.path.abspath(".judge"))
        found = wdg._resolve_cmd(os.path.basename(sys.executable))
        self.assertTrue(found is None or os.path.isabs(found))
        self.assertIsNone(wdg._resolve_cmd("no-such-judge-cmd-xyz"))

    def test_windows_kill_tree(self):
        class P:
            pid, killed = 4242, False

            def kill(self):
                self.killed = True

        ok = mock.Mock(returncode=0)
        with mock.patch.object(wdg.subprocess, "run", return_value=ok) as run:
            p = P()
            wdg._kill_tree(p, posix=False)
        args, kw = run.call_args
        self.assertEqual(args[0], ["taskkill", "/T", "/F", "/PID", "4242"])
        self.assertIs(kw["stdin"], wdg.subprocess.DEVNULL)
        self.assertTrue(kw["capture_output"])
        self.assertFalse(p.killed)
        for side in (FileNotFoundError("taskkill"), None):
            kw = {"side_effect": side} if side else {"return_value": mock.Mock(returncode=128)}
            with mock.patch.object(wdg.subprocess, "run", **kw):
                p = P()
                wdg._kill_tree(p, posix=False)
            self.assertTrue(p.killed)

    def test_real_runner_with_stub_script(self):
        if os.name == "nt":
            self.skipTest("POSIX stub script")
        self.write_ledger("a/b")
        stub = self.root / "stub_judge.py"
        stub.write_text(
            "#!" + sys.executable + "\n"
            "import json, sys\n"
            "assert sys.stdin.read() == ''\n"
            "a = sys.argv\n"
            "spf = a[a.index('--system-prompt-file') + 1]\n"
            "mat = json.loads(open(spf, encoding='utf-8').read().strip().split('\\n')[-1])\n"
            "js = [{'key': c['key'], 'clause': 'G2', 'why': 'w', 'request': 'r', 'open_points': []}\n"
            "      for c in mat['candidates']]\n"
            "print(json.dumps({'type': 'result', 'subtype': 'success', 'is_error': False,\n"
            "                  'total_cost_usd': 0.02, 'structured_output': {'judgements': js}}))\n",
            encoding="utf-8")
        stub.chmod(stub.stat().st_mode | stat.S_IEXEC)
        r = self.rank([_cand("a/b", 1)], None, judge_cmd=str(stub))
        self.assertEqual(r["candidates"][0]["goal_clause"], {"id": "G2", "heading": "Fast CI"})
        self.assertEqual(r["goal_rank"]["judge"]["cost_usd"], 0.02)

    def test_real_runner_timeout_kills_group(self):
        if os.name == "nt":
            self.skipTest("POSIX stub script")
        stub = self.root / "slow_judge.py"
        stub.write_text("#!" + sys.executable + "\nimport time\ntime.sleep(30)\n", encoding="utf-8")
        stub.chmod(stub.stat().st_mode | stat.S_IEXEC)
        rc, out, timed_out = wdg._subprocess_runner([str(stub)], str(self.root), 0.5)
        self.assertEqual((rc, timed_out), (None, True))


class TestCache(Base):
    def test_hit_skips_judge(self):
        self.write_ledger("a/b")
        self.rank([_cand("a/b", 1)], FakeRunner())
        fr = FakeRunner()
        r = self.rank([_cand("a/b", 1, updated="2026-09-29T00:00:00Z")], fr)  # updatedAt not in key
        self.assertEqual(fr.calls, [])
        j = r["goal_rank"]["judge"]
        self.assertEqual((j["status"], j["cache_hits"], j["candidates_sent"]), ("cache_only", 1, 0))
        key = r["candidates"][0]["goal_judgement_key"]
        self.assertTrue((self.state / "judgements" / f"{key}.json").exists())

    def test_material_change_misses(self):
        self.write_ledger("a/b")
        self.rank([_cand("a/b", 1)], FakeRunner())
        fr = FakeRunner()
        self.rank([_cand("a/b", 1), _cand("a/b", 2)], fr)
        self.assertEqual([c["key"] for c in fr.calls[0]["material"]["candidates"]], ["a/b#2"])
        fr = FakeRunner()
        self.rank([_cand("a/b", 1, labels=["x"])], fr)
        self.assertEqual(len(fr.calls), 1)
        fr = FakeRunner()
        self.rank([_cand("a/b", 1)], fr, judge_model="opus")
        self.assertEqual(len(fr.calls), 1)

    def test_ledger_change_misses(self):
        self.write_ledger("a/b")
        self.rank([_cand("a/b", 1)], FakeRunner())
        self.write_ledger("a/b", LEDGER.replace("Users can log in.", "Users can log in fast."))
        fr = FakeRunner()
        self.rank([_cand("a/b", 1)], fr)
        self.assertEqual(len(fr.calls), 1)

    def test_corrupt_cache_is_miss_with_signal(self):
        self.write_ledger("a/b")
        r = self.rank([_cand("a/b", 1)], FakeRunner())
        key = r["candidates"][0]["goal_judgement_key"]
        path = self.state / "judgements" / f"{key}.json"
        for bad in ("{not json", json.dumps({"cache_key": key, "model": "sonnet", "prompt_version": "1",
                                              "judgement": _j("a/b#1", "G7")})):
            path.write_text(bad, encoding="utf-8")
            fr = FakeRunner()
            r = self.rank([_cand("a/b", 1)], fr)
            self.assertEqual(len(fr.calls), 1)
            self.assertTrue(any("corrupt" in s for s in r["goal_rank"]["signals"]))
            self.assertEqual(r["candidates"][0]["goal_clause"]["id"], "G1")

    def test_cache_write_failure_is_signal(self):
        self.write_ledger("a/b")
        self.state.mkdir(parents=True)
        (self.state / "judgements").write_text("not a dir")
        r = self.rank([_cand("a/b", 1)], FakeRunner())
        self.assertEqual(len(r["candidates"]), 1)
        self.assertTrue(any("cache write failed" in s for s in r["goal_rank"]["signals"]))


class TestCooldown(Base):
    def fail_once(self, fr):
        self.write_ledger("a/b")
        self.rank_err([_cand("a/b", 1)], fr)

    def test_environment_failure_is_global(self):
        self.fail_once(FakeRunner(timed_out=True))
        self.clock[0] = NOW + timedelta(minutes=30)
        fr = FakeRunner()
        e = self.rank_err([_cand("a/b", 2)], fr)  # different batch still blocked
        self.assertEqual(e.goal_rank["judge"]["status"], "cooldown")
        self.assertIn("environment", e.goal_rank["judge"]["error"])
        self.assertEqual(fr.calls, [])

    def test_material_failure_is_per_batch(self):
        self.fail_once(FakeRunner(stdout=_envelope([], is_error=True)))
        e = self.rank_err([_cand("a/b", 1)], FakeRunner())
        self.assertEqual(e.goal_rank["judge"]["status"], "cooldown")
        self.assertIn("material", e.goal_rank["judge"]["error"])
        fr = FakeRunner()
        self.rank([_cand("a/b", 2)], fr)  # different batch -> runs
        self.assertEqual(len(fr.calls), 1)
        self.assertFalse((self.state / "judge_last_failure.json").exists())  # cleared

    def test_expiry_and_negative_delta(self):
        self.fail_once(FakeRunner(timed_out=True))
        self.clock[0] = NOW + timedelta(seconds=3600)
        self.rank([_cand("a/b", 1)], FakeRunner())
        self.clock[0] = NOW
        self.rank_err([_cand("a/b", 2)], FakeRunner(timed_out=True))
        self.clock[0] = NOW - timedelta(minutes=5)  # clock went backwards
        fr = FakeRunner()
        self.rank([_cand("a/b", 2)], fr)
        self.assertEqual(len(fr.calls), 1)

    def test_in_progress_record(self):
        self.write_ledger("a/b")
        self.state.mkdir(parents=True)
        rec = self.state / "judge_last_failure.json"
        for age in (0, 60, 3599):  # any in_progress within the hour -> global cooldown
            rec.write_text(json.dumps({"scope": "global", "batch_key": "x", "reason": "in_progress",
                                       "at": wdg._fmt_utc(NOW - timedelta(seconds=age))}))
            fr = FakeRunner()
            e = self.rank_err([_cand("a/b", 1)], fr)
            self.assertEqual(e.goal_rank["judge"]["status"], "cooldown", age)
            self.assertIn("did not finish", e.goal_rank["judge"]["error"])
            self.assertEqual((fr.calls, e.goal_rank["judge"]["candidates_sent"]), ([], 0))
        rec.write_text(json.dumps({"scope": "global", "batch_key": "x", "reason": "in_progress",
                                   "at": wdg._fmt_utc(NOW - timedelta(seconds=3600))}))
        self.rank([_cand("a/b", 1)], FakeRunner())  # expired
        rec.write_text(json.dumps({"scope": "global", "batch_key": "x", "reason": "in_progress",
                                   "at": wdg._fmt_utc(NOW + timedelta(seconds=60))}))
        self.rank([_cand("a/b", 2)], FakeRunner())  # clock went backwards: ignored

    def test_in_progress_written_before_call(self):
        self.write_ledger("a/b")
        seen = []

        def runner(argv, cwd, timeout):
            seen.append(json.loads((self.state / "judge_last_failure.json").read_text())["reason"])
            return FakeRunner()(argv, cwd, timeout)

        self.rank([_cand("a/b", 1)], runner)
        self.assertEqual(seen, ["in_progress"])
        self.assertFalse((self.state / "judge_last_failure.json").exists())

    def test_cache_only_ignores_cooldown(self):
        self.write_ledger("a/b")
        self.rank([_cand("a/b", 1)], FakeRunner())
        self.rank_err([_cand("a/b", 2)], FakeRunner(timed_out=True))
        r = self.rank([_cand("a/b", 1)], FakeRunner())
        self.assertEqual(r["goal_rank"]["judge"]["status"], "cache_only")


class TestBudget(Base):
    def spend(self, *entries):
        self.state.mkdir(parents=True, exist_ok=True)
        with open(self.state / "judge_spend.jsonl", "a", encoding="utf-8") as fh:
            for at, cost in entries:
                fh.write(json.dumps({"at": wdg._fmt_utc(at), "cost_usd": cost}) + "\n")

    def test_exhausted(self):
        self.write_ledger("a/b")
        self.spend((NOW - timedelta(hours=1), 1.6))
        fr = FakeRunner()
        e = self.rank_err([_cand("a/b", 1)], fr)
        self.assertEqual(e.goal_rank["judge"]["status"], "budget_exhausted")
        self.assertEqual(fr.calls, [])

    def test_boundary_and_yesterday(self):
        self.write_ledger("a/b")
        self.spend((NOW - timedelta(hours=1), 1.5), (NOW - timedelta(days=1), 5.0))
        with open(self.state / "judge_spend.jsonl", "a", encoding="utf-8") as fh:
            fh.write("{torn\n")
        r = self.rank([_cand("a/b", 1)], FakeRunner(cost=0.3))  # 1.5 + 0.5 == 2.0 allowed
        self.assertTrue(any("malformed" in s for s in r["goal_rank"]["signals"]))
        last = json.loads((self.state / "judge_spend.jsonl").read_text().splitlines()[-1])
        self.assertEqual(last, {"at": "2026-09-30T12:00:00Z", "cost_usd": 0.3})
        e = self.rank_err([_cand("a/b", 2)], FakeRunner())  # 1.8 + 0.5 > 2.0
        self.assertEqual(e.goal_rank["judge"]["status"], "budget_exhausted")

    def test_bad_cost_lines_count_the_cap(self):
        self.write_ledger("a/b")
        self.state.mkdir(parents=True, exist_ok=True)
        with open(self.state / "judge_spend.jsonl", "w", encoding="utf-8") as fh:
            for cost in ("NaN", "Infinity", "-1", '"x"', "null"):
                fh.write('{"at": "%s", "cost_usd": %s}\n' % (wdg._fmt_utc(NOW), cost))
        sigs = []
        self.assertEqual(wdg._spent_today(self.state, NOW, sigs, 0.5), 2.5)
        self.assertEqual(len(sigs), 5)
        self.assertTrue(all("per-call cap" in s for s in sigs))
        e = self.rank_err([_cand("a/b", 1)], FakeRunner())  # 2.5 + 0.5 > 2.0
        self.assertEqual(e.goal_rank["judge"]["status"], "budget_exhausted")

    def test_unreadable_ledger_fails_closed(self):
        self.write_ledger("a/b")
        (self.state / "judge_spend.jsonl").mkdir(parents=True)
        e = self.rank_err([_cand("a/b", 1)], FakeRunner())
        self.assertEqual(e.goal_rank["judge"]["status"], "budget_exhausted")


class TestRanking(Base):
    def test_caps_to_not_judged(self):
        self.write_ledger("a/b")
        fr = FakeRunner()
        r = self.rank([_cand("a/b", n) for n in (3, 1, 2)], fr, judge_max_candidates=2)
        self.assertEqual([c["key"] for c in fr.calls[0]["material"]["candidates"]], ["a/b#1", "a/b#2"])
        self.assertEqual([(e["issue"], e["reason"]) for e in r["excluded_goal"]], [(3, "not_judged")])

    def test_byte_cap_prefix(self):
        self.write_ledger("a/b")
        size = wdg.material_bytes(wdg.build_material(_cand("a/b", 1)))
        fr = FakeRunner()
        r = self.rank([_cand("a/b", 1), _cand("a/b", 2, body="x" * 500), _cand("a/b", 3)], fr,
                      judge_max_material_bytes=size + 10)
        self.assertEqual(len(fr.calls[0]["material"]["candidates"]), 1)
        self.assertEqual([e["issue"] for e in r["excluded_goal"]], [2, 3])

    def test_zero_cap_is_capped(self):
        self.write_ledger("a/b")
        fr = FakeRunner()
        r = self.rank([_cand("a/b", 1)], fr, judge_max_candidates=0)
        self.assertEqual(fr.calls, [])
        j = r["goal_rank"]["judge"]
        self.assertEqual((j["status"], j["candidates_pending"], j["candidates_sent"]), ("capped", 0, 0))
        self.assertEqual(r["excluded_goal"][0]["reason"], "not_judged")

    def test_no_clause(self):
        self.write_ledger("a/b")
        r = self.rank([_cand("a/b", 1), _cand("a/b", 2)],
                      FakeRunner(decide=lambda k: None if k == "a/b#1" else "G2"))
        self.assertEqual([c["issue"] for c in r["candidates"]], [2])
        self.assertEqual(r["excluded_goal"][0]["reason"], "no_clause")
        self.assertIn("because", r["excluded_goal"][0]["note"])

    def test_two_repos_ranked_by_clause_then_legacy(self):
        self.write_ledger("a/x")
        self.write_ledger("b/y")
        clauses = {"a/x#1": "G2", "a/x#5": "G1", "b/y#2": "G1", "b/y#3": "G2", "b/y#4": "G1"}
        pool = [_cand("a/x", 1), _cand("a/x", 5), _cand("b/y", 2), _cand("b/y", 3), _cand("b/y", 4)]
        r = self.rank(pool, FakeRunner(decide=clauses.get), top_n=3)
        self.assertEqual([(c["repo"], c["issue"], c["rank"]) for c in r["candidates"]],
                         [("b/y", 2, 1), ("b/y", 4, 2), ("a/x", 5, 3)])
        self.assertEqual(r["truncated_count"], 2)
        top = r["candidates"][0]
        self.assertEqual(top["goal_clause"], {"id": "G1", "heading": "Ship login"})
        self.assertEqual((top["goal_why"], top["goal_request"], top["open_points"]),
                         ("because", "do it", []))
        self.assertEqual(r["recommendation"],
                         {"repo": "b/y", "issue": 2, "reason": "G1「Ship login」: because"})
        self.assertEqual([g["repo"] for g in r["goal_rank"]["goals"]], ["a/x", "b/y"])
        self.assertIn("_body", top)  # internal fields are the scan's to strip

    def test_single_repo_display_and_unset(self):
        self.write_ledger("a/b")
        pool = [_cand("a/b", 1), _cand("c/d", 7), _cand("c/d", 8)]
        pool[0]["repo"] = None
        r = self.rank(pool, FakeRunner(decide=lambda k: None), display=lambda s: None)
        self.assertEqual(r["excluded_goal"], [{"repo": None, "issue": 1, "reason": "no_clause",
                                               "note": "どのゴール条項にも当たらない（モデル判定）: because"}])
        self.assertEqual(r["goal_rank"]["goal_unset_repos"], [{"repo": "c/d", "candidate_count": 2}])
        self.assertIsNone(r["recommendation"])
        self.assertEqual(r["candidates"], [])

    def test_repo_slug_unknown(self):
        c = _cand("a/b", 1)
        c["_real_repo"] = None
        fr = FakeRunner()
        r = self.rank([c, dict(c, issue=2)], fr)
        self.assertEqual(r["goal_rank"]["goal_errors"],
                         [{"repo": None, "path": None, "error": wdg.SLUG_UNKNOWN_ERROR}])
        self.assertEqual((r["candidates"], fr.calls), ([], []))
        self.assertEqual(r["goal_rank"]["judge"]["status"], "skipped_no_material")

    def test_ledger_error_repo_yields_nothing(self):
        self.write_ledger("a/b", "## G1\n")
        r = self.rank([_cand("a/b", 1)], FakeRunner())
        self.assertEqual(r["goal_rank"]["goal_errors"][0]["repo"], "a/b")
        self.assertEqual(r["candidates"], [])

    def test_status_cache_only_vs_capped(self):
        self.write_ledger("a/b")
        self.rank([_cand("a/b", 1)], FakeRunner())
        fr = FakeRunner()
        r = self.rank([_cand("a/b", 1), _cand("a/b", 2)], fr, judge_max_candidates=0)
        j = r["goal_rank"]["judge"]
        self.assertEqual((j["status"], j["cache_hits"], j["candidates_sent"]), ("capped", 1, 0))
        self.assertEqual([e["reason"] for e in r["excluded_goal"]], ["not_judged"])
        r = self.rank([_cand("a/b", 1)], FakeRunner())
        self.assertEqual(r["goal_rank"]["judge"]["status"], "cache_only")
        r = self.rank([], FakeRunner())
        j = r["goal_rank"]["judge"]
        self.assertEqual((j["status"], j["candidates_pending"]), ("skipped_no_material", 0))

    def test_budget_exhausted_sends_nothing(self):
        self.write_ledger("a/b")
        e = self.rank_err([_cand("a/b", 1), _cand("a/b", 2)], FakeRunner(), judge_daily_budget_usd=0.1)
        j = e.goal_rank["judge"]
        self.assertEqual((j["status"], j["candidates_pending"], j["candidates_sent"]),
                         ("budget_exhausted", 2, 0))

    def test_does_not_touch_unemitted(self):
        self.write_ledger("a/b")
        pool = [_cand("a/b", 1), _cand("a/b", 2)]
        before = json.dumps(pool[1], sort_keys=True)
        self.rank(pool, FakeRunner(decide=lambda k: None if k == "a/b#2" else "G1"))
        self.assertEqual(json.dumps(pool[1], sort_keys=True), before)


if __name__ == "__main__":
    unittest.main()
