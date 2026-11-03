"""Hook tests must not write hook_denied rows into the production state.db (Issue #1234).

A hook that exits 2 records ``hook_denied`` through ``.hooks/lib/deny-record.sh`` ->
``tools/journal_append.py``. Without ``STATE_DB_PATH`` the DB path is discovered from the
repo layout, i.e. the production ``<main checkout>/.state/state.db``. ``tests/run-all.sh``
pins a throwaway DB, but running a hook test directly (``bash tests/test-*.sh``) or a
tools test under pytest did not.

Each case copies the repo into a temp "production" checkout, so discovery resolves to
``<copy>/.state/state.db`` (the production-equivalent DB), runs a hook test through its
usual entrance with ``STATE_DB_PATH`` unset, and checks that the production-equivalent
DB got no hook_denied row while a throwaway DB under ``$TMPDIR`` did.
"""
from __future__ import annotations

import os
import shutil
import sqlite3
import subprocess
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent


def _hook_denied(db: Path) -> int:
    if not db.exists():
        return 0
    conn = sqlite3.connect(db)
    try:
        return conn.execute("SELECT COUNT(*) FROM events WHERE kind = 'hook_denied'").fetchone()[0]
    except sqlite3.DatabaseError:
        return 0
    finally:
        conn.close()


def _throwaway_denied(tmpdir: Path) -> int:
    return sum(_hook_denied(p) for p in tmpdir.rglob("*.db"))


@pytest.fixture
def prod(tmp_path):
    """A copy of the repo that discovery treats as the production checkout."""
    root = tmp_path / "prod"
    root.mkdir()
    ignore = shutil.ignore_patterns("__pycache__", ".pytest_cache")
    for name in (".hooks", "tools", "tests"):
        shutil.copytree(REPO / name, root / name, ignore=ignore, symlinks=True)
    shutil.copy2(REPO / "pyproject.toml", root / "pyproject.toml")
    (root / ".state").mkdir()
    tmpdir = tmp_path / "tmp"
    tmpdir.mkdir()
    env = {k: v for k, v in os.environ.items() if k != "STATE_DB_PATH"}
    env["TMPDIR"] = str(tmpdir)
    return root, tmpdir, env


def test_direct_shell_hook_test_keeps_production_db_clean(prod):
    root, tmpdir, env = prod
    prod_db = root / ".state" / "state.db"
    before = _hook_denied(prod_db)
    subprocess.run(["bash", str(root / "tests" / "test-hooks-payload-fail-closed.sh")],
                   cwd=root, env=env, capture_output=True, text=True, timeout=600)
    assert _hook_denied(prod_db) == before, "hook test wrote hook_denied into the production DB"
    assert _throwaway_denied(tmpdir) > 0, "denials were not recorded in a throwaway DB"


def test_every_shell_hook_test_isolates_state_db():
    """Every shell test that drives hooks pins a throwaway DB, as the case above does."""
    missing = [p.name for p in sorted((REPO / "tests").glob("test-*.sh"))
               if ".hooks" in p.read_text(encoding="utf-8")
               and "lib/isolate-state-db.sh" not in p.read_text(encoding="utf-8")]
    assert missing == []


def test_pytest_tools_hook_test_keeps_production_db_clean(prod):
    root, tmpdir, env = prod
    probe = root / "tools" / "test_zz_hook_deny_probe.py"
    probe.write_text(
        "import subprocess\n"
        "from pathlib import Path\n"
        "ROOT = Path(__file__).resolve().parent.parent\n"
        "def test_deny():\n"
        "    r = subprocess.run(['bash', str(ROOT / '.hooks' / 'block-git-push.sh')],\n"
        "                       input='', capture_output=True, text=True)\n"
        "    assert r.returncode == 2\n",
        encoding="utf-8",
    )
    prod_db = root / ".state" / "state.db"
    before = _hook_denied(prod_db)
    res = subprocess.run([sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider", str(probe)],
                         cwd=root, env=env, capture_output=True, text=True, timeout=300)
    assert res.returncode == 0, res.stdout + res.stderr
    assert _hook_denied(prod_db) == before, "tools test wrote hook_denied into the production DB"
    assert _throwaway_denied(tmpdir) > 0, "denial was not recorded in a throwaway DB"


def test_production_denial_still_recorded(prod):
    """A real (non-test) hook invocation still records into the production DB."""
    root, _tmpdir, env = prod
    res = subprocess.run(["bash", str(root / ".hooks" / "block-git-push.sh")], input="",
                         cwd=root, env=env, capture_output=True, text=True, timeout=60)
    assert res.returncode == 2
    assert _hook_denied(root / ".state" / "state.db") == 1
