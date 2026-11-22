"""The hooks read commands with unbalanced quotes instead of refusing them (Issue 1298).

``shlex`` raises on an unbalanced quote (an apostrophe inside a heredoc body is
enough), and both hooks that read commands with ``tools/presubmit.py``'s
reader failed closed on it, refusing harmless commands. The reader now drops
the bodies of heredocs that are only data before parsing (a heredoc that is
run, like ``cat <<EOF | bash``, is still read as commands); text it still
cannot parse is refused as before.

Uses the fixtures of tests/test_presubmit.py and tests/test_test_commit_guard.py,
loaded by path so the ``tests`` package __init__ does not run here.
"""

from __future__ import annotations

import importlib.util
import os
import sys
import unittest
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_ROOT))


def _load(name: str, path: str):
    spec = importlib.util.spec_from_file_location(name, _ROOT / path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


pre = _load("_presubmit_fixture3", "tests/test_presubmit.py")
guard = _load("_guard_fixture3", "tests/test_test_commit_guard.py")

HEREDOC = "cat > notes.md <<'EOF'\nit's the org's rule, don't push yet\nEOF"


@unittest.skipIf(os.name == "nt", "the fixtures are POSIX only")
class PresubmitHookQuotesTests(pre.PresubmitTests):
    def test_unbalanced_quotes(self):
        wdir = self.worker("q1")
        self.assertEqual(self.hook(HEREDOC, wdir), "ALLOW")
        self.assertTrue(self.hook("echo it's; git push origin feat/q1", self.root).startswith("DENY"))
        self.assertTrue(self.hook(HEREDOC + f"\nbash -c 'git -C {wdir} push origin HEAD'", self.root)
                        .startswith("DENY"))
        self.assertTrue(self.hook(HEREDOC + "\necho `git push origin feat/q1`", self.root).startswith("DENY"))
        self.assertTrue(self.hook(HEREDOC + '\ngit pu""sh origin feat/q1', self.root).startswith("DENY"))
        # A heredoc that is run is read as commands.
        self.assertTrue(self.hook("cat <<'EOF' | bash\ngit push origin feat/q1\nEOF", self.root)
                        .startswith("DENY"))
        self.assertEqual(self.hook("cat <<'EOF' > notes.md\ngit push origin feat/q1\nEOF", self.root), "ALLOW")
        # A quoted runner, or a command that is a variable, may run the body.
        self.assertTrue(self.hook("\"bash\" <<'EOF'\ngit push origin feat/q1\nEOF", self.root).startswith("DENY"))
        self.assertTrue(self.hook("$SH <<'EOF'\ngit push origin feat/q1\nEOF", self.root).startswith("DENY"))
        # An unquoted delimiter still runs $(...) in the body.
        self.assertTrue(self.hook("cat <<EOF\n$(git push origin feat/q1)\nEOF", self.root).startswith("DENY"))
        # `<<` inside quotes or a comment is not a heredoc.
        self.assertTrue(self.hook('echo "<<\'EOF\'"\ngit push origin feat/q1', self.root).startswith("DENY"))
        self.assertTrue(self.hook("# see <<'EOF'\ngit push origin feat/q1", self.root).startswith("DENY"))


@unittest.skipIf(os.name == "nt", "the fixtures are POSIX only")
class GuardHookQuotesTests(guard.TestCommitGuardTests):
    def test_unbalanced_quotes(self):
        self.commit(self.wdir, "fix", "fix.txt")
        self.assertAllowed(HEREDOC)
        self.assertDenied("echo it's; git rebase origin/main")


for _cls, _base in ((PresubmitHookQuotesTests, pre.PresubmitTests),
                   (GuardHookQuotesTests, guard.TestCommitGuardTests)):
    for _name in [n for n in dir(_base) if n.startswith("test_")]:
        if _name not in _cls.__dict__:
            setattr(_cls, _name, None)
del _cls, _base

if __name__ == "__main__":
    unittest.main()
