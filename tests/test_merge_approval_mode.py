import contextlib
import io
import tempfile
import unittest
from pathlib import Path

from tools.merge_approval_mode import main, read_mode

REPO_ROOT = Path(__file__).resolve().parent.parent

CASES = [
    (None, "human", False),                                         # no file
    ("# Organization Config\n", "human", False),                    # no key
    ("merge_approval_mode: human\n", "human", False),
    ("merge_approval_mode: acceptance\n", "acceptance", False),
    ("merge_approval_mode:  ACCEPTANCE \n", "acceptance", False),
    ("merge_approval_mode: auto\n", "human", True),
    ("merge_approval_mode:\n", "human", True),
    ("merge_approval_mode: acceptance human\n", "human", True),     # whole value is validated
    ("- merge_approval_mode: acceptance\n", "human", False),        # not column 0
    ("`merge_approval_mode: acceptance`\n", "human", False),
]


def _root(tmp: str, body: str | None) -> Path:
    root = Path(tmp)
    if body is not None:
        (root / "registry").mkdir()
        (root / "registry" / "org-config.md").write_text(body, encoding="utf-8")
    return root


class ReadModeTest(unittest.TestCase):
    def test_cases(self):
        for body, mode, warns in CASES:
            with self.subTest(body=body), tempfile.TemporaryDirectory() as tmp:
                got, warning = read_mode(_root(tmp, body))
                self.assertEqual(got, mode)
                self.assertEqual(warning is not None, warns)

    def test_main_prints_one_line(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = _root(tmp, "merge_approval_mode: bogus\n")
            out, err = io.StringIO(), io.StringIO()
            with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
                self.assertEqual(main(["--root", str(root)]), 0)
        self.assertEqual(out.getvalue(), "human\n")
        self.assertIn("invalid value", err.getvalue())

    def test_repo_config_is_acceptance(self):
        # The organisation runs in acceptance mode; a missing key or file falls back to human (CASES).
        self.assertEqual(read_mode(REPO_ROOT), ("acceptance", None))


if __name__ == "__main__":
    unittest.main()
