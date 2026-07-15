"""Runs the verdict-block assertions (test-regime-explainer.mjs) under the documented
`python3 -m pytest` entry point, so the dashboard's regime prose is covered by the same
command as the fetcher. The logic under test is browser JS, hence the hop through node.
"""

import shutil
import subprocess
from pathlib import Path

import pytest

TEST_JS = Path(__file__).parent / "test-regime-explainer.mjs"


@pytest.mark.skipif(shutil.which("node") is None, reason="node not installed")
def test_regime_explainer():
    proc = subprocess.run(
        ["node", str(TEST_JS)], capture_output=True, text=True, timeout=60
    )
    assert proc.returncode == 0, f"{proc.stdout}\n{proc.stderr}"
