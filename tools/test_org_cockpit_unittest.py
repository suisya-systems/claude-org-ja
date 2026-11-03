"""Run the pytest-style tools/test_org_cockpit.py under CI's `unittest discover`."""

import importlib.util
import unittest
from pathlib import Path


def load_tests(loader, tests, pattern):
    path = Path(__file__).with_name("test_org_cockpit.py")
    spec = importlib.util.spec_from_file_location("test_org_cockpit_wrapped", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    suite = unittest.TestSuite()
    for name in sorted(vars(mod)):
        if name.startswith("test_"):
            suite.addTest(unittest.FunctionTestCase(getattr(mod, name)))
    return suite
