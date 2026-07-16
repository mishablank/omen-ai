"""Runs the dashboard's node test suites under the documented `python3 -m pytest` entry
point, so the browser JS is covered by the same command as the fetcher. The logic under
test is browser JS, hence the hop through node.
"""

import shutil
import subprocess
from pathlib import Path

import pytest

HERE = Path(__file__).parent
SUITES = ["test-regime-explainer.mjs", "test-pure-helpers.mjs"]


@pytest.mark.skipif(shutil.which("node") is None, reason="node not installed")
@pytest.mark.parametrize("suite", SUITES)
def test_node_suite(suite: str):
    proc = subprocess.run(
        ["node", str(HERE / suite)], capture_output=True, text=True, timeout=60
    )
    assert proc.returncode == 0, f"{proc.stdout}\n{proc.stderr}"
