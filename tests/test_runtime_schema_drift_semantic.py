"""Semantic golden drift tests for ``tools/check_runtime_schema_drift.py``.

The byte-identical schema check that lives in
``check_runtime_schema_drift`` validates that
``tools/org_extension_schema.json`` and the schema bundled with
``claude-org-runtime`` agree as JSON. That is necessary but not
sufficient: the runtime's ``render_role_with_metadata()`` evaluator
may change the shape of the rendered explain JSON (suppression
reason wording, ``sandbox_read_roots`` order, structured-anchor
substitution, etc.) without touching the schema bytes at all.

These tests render each fixture under
``tests/fixtures/runtime_schema_drift/sandbox_intent/`` through the
fixture-supplied fake ``realpath`` shim and assert that the rendered
``SandboxMetadata.to_jsonable()`` matches the committed
``expected_explain``. Discovery is glob-based so adding a fixture
requires no test wiring.

The check intentionally bypasses the pin-window guard in
``check_runtime_schema_drift.main`` because the fixtures depend on
the *currently importable* runtime, not on whatever pin range
``pyproject.toml`` declares. The pin guard exists to prevent a
contributor's locally-previewed runtime from blocking unrelated PRs;
inside the test runner we run against whatever runtime is installed
and surface drift as a hard failure.
"""

from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "tools"))

import check_runtime_schema_drift as csd  # noqa: E402


FIXTURE_DIR = (
    REPO_ROOT / "tests" / "fixtures" / "runtime_schema_drift" / "sandbox_intent"
)


def _fixture_paths() -> list[Path]:
    return sorted(FIXTURE_DIR.glob("*.json"))


class FixtureExplainGoldenTest(unittest.TestCase):
    """Each fixture's ``expected_explain`` matches what the installed
    ``claude_org_runtime.settings.generator.render_role_with_metadata``
    actually produces for the fixture inputs."""

    def test_fixture_directory_is_populated(self) -> None:
        # Smoke check: at least one fixture exists, otherwise the per-
        # fixture loop below would silently pass with zero cases.
        self.assertTrue(
            _fixture_paths(),
            f"No semantic fixtures found in {FIXTURE_DIR}; the semantic "
            "drift check would be a no-op.",
        )

    def test_fixture_explain_matches_runtime(self) -> None:
        for fixture_path in _fixture_paths():
            with self.subTest(fixture=fixture_path.name):
                fixture = json.loads(fixture_path.read_text(encoding="utf-8"))
                expected = fixture["expected_explain"]
                actual = csd._render_fixture_explain(fixture)
                self.assertEqual(
                    actual,
                    expected,
                    f"Semantic drift in {fixture_path.name}:\n"
                    f"expected: "
                    f"{json.dumps(expected, indent=2, sort_keys=True)}\n"
                    f"actual:   "
                    f"{json.dumps(actual, indent=2, sort_keys=True)}",
                )

    def test_fixture_does_not_carry_verification_depth_field(self) -> None:
        # ``verification_depth`` is a delegate-payload convention, not a
        # sandbox enforcement dimension. Fixtures must not introduce it
        # into either ``inputs`` or ``expected_explain`` so the check
        # keeps a tight enforcement surface.
        for fixture_path in _fixture_paths():
            with self.subTest(fixture=fixture_path.name):
                fixture = json.loads(fixture_path.read_text(encoding="utf-8"))
                for section in ("inputs", "expected_explain"):
                    self.assertNotIn(
                        "verification_depth",
                        fixture.get(section, {}),
                        f"{fixture_path.name}: 'verification_depth' must not "
                        f"appear in '{section}'; depth is convention-only "
                        "and not part of the sandbox semantic contract.",
                    )


class RealpathShimTest(unittest.TestCase):
    """Unit-level tests for the fake-realpath helper."""

    def test_prefix_replacement(self) -> None:
        fn = csd._build_realpath_fn(
            [{"prefix": "/home/u/wd", "replacement": "/mnt/c/Users/u/wd"}]
        )
        self.assertEqual(fn("/home/u/wd"), "/mnt/c/Users/u/wd")
        self.assertEqual(
            fn("/home/u/wd/secrets.env"),
            "/mnt/c/Users/u/wd/secrets.env",
        )
        self.assertEqual(fn("/home/u/wdother"), "/home/u/wdother")
        self.assertEqual(fn("/elsewhere"), "/elsewhere")

    def test_first_match_wins(self) -> None:
        fn = csd._build_realpath_fn(
            [
                {"prefix": "/a/b", "replacement": "/X"},
                {"prefix": "/a", "replacement": "/Y"},
            ]
        )
        self.assertEqual(fn("/a/b/c"), "/X/c")
        self.assertEqual(fn("/a/c"), "/Y/c")


if __name__ == "__main__":
    unittest.main()
