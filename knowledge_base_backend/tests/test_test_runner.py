import shutil
import subprocess
import sys
from pathlib import Path

import pytest


@pytest.mark.skipif(sys.platform != "win32", reason="PowerShell runner is Windows-only")
@pytest.mark.parametrize("install", [False, True])
def test_powershell_runner_propagates_native_failure(tmp_path, install):
    scripts = tmp_path / "scripts"
    scripts.mkdir()
    original = Path(__file__).resolve().parents[2] / "scripts" / "test-backend.ps1"
    runner = scripts / original.name
    shutil.copy2(original, runner)
    backend = tmp_path / "knowledge_base_backend"
    # This isolated interpreter deliberately has neither pip nor pytest.
    subprocess.run([sys.executable, "-m", "venv", "--without-pip", str(backend / ".venv")],
                   check=True, capture_output=True, timeout=60)
    command = ["powershell.exe", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(runner)]
    if install:
        command.append("-Install")
    result = subprocess.run(command, capture_output=True, timeout=60)
    assert result.returncode != 0
    assert b"No module named" in result.stderr
    if install:
        assert b"Running backend test suite" not in result.stdout
