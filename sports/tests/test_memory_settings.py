"""The worker's address-space cap (RLIMIT_AS) is separate from its RSS ceiling: SPORTS_WORKER_MAX_VM_MB, falling back
to SPORTS_WORKER_MAX_MB so an unset value changes nothing. Measured live on Starter-like constraints (research/results/
storage_2026-10-24/constrained.jsonl): an ingest cycle with RSS ~130-190 MB needs ~240-257 MB of address space."""
import os
import subprocess
import sys
import pytest
from pathlib import Path

from sports import worker

ROOT = Path(__file__).resolve().parents[2]


def test_vm_cap_falls_back_to_the_former_single_knob():
    assert worker.vm_cap_mb({}) == 768
    assert worker.vm_cap_mb({"SPORTS_WORKER_MAX_MB": "220"}) == 220
    assert worker.vm_cap_mb({"SPORTS_WORKER_MAX_MB": "220", "SPORTS_WORKER_MAX_VM_MB": "320"}) == 320
    assert worker.vm_cap_mb({"SPORTS_WORKER_MAX_MB": "220", "SPORTS_WORKER_MAX_VM_MB": ""}) == 220


def _settle(tmp_path, **env_over):
    """`settle` on an empty store: offline, applies the worker's memory cap, imports the settlement path."""
    env = {k: v for k, v in os.environ.items() if not k.startswith("SPORTS_")}
    env.update({"SPORTS_DATA_DIR": str(tmp_path), "SPORTS_INGEST": "1", "PYTHONPATH": str(ROOT),
                # Match scheduler._env: native math thread stacks consume virtual
                # address space too. Host CPU/thread defaults are not the VM knob.
                "OPENBLAS_NUM_THREADS": "1", "OMP_NUM_THREADS": "1", "MKL_NUM_THREADS": "1",
                "MALLOC_ARENA_MAX": "2", **env_over})
    return subprocess.run([sys.executable, "-m", "sports.worker", "settle", "nba"], cwd=str(ROOT), env=env,
                          capture_output=True, text=True, timeout=120, stdin=subprocess.DEVNULL)


def test_the_rlimit_comes_from_the_vm_knob_not_the_rss_ceiling(tmp_path):
    if os.name != "posix":
        pytest.skip("RLIMIT_AS requires POSIX")
    ok = _settle(tmp_path / "a", SPORTS_WORKER_MAX_MB="50", SPORTS_WORKER_MAX_VM_MB="320")
    assert ok.returncode == 0 and '"state": "done"' in ok.stdout, ok.stderr[-500:]   # RSS 50 is not an address cap
    assert "RLIMIT_AS 320 MB" in ok.stderr
    # Read the enforced OS limit directly. Deliberately starving SQLite at 40 MB
    # produces platform-dependent failures (including disk I/O errors), which
    # do not establish which setting supplied the limit.
    env = {k: v for k, v in os.environ.items() if not k.startswith("SPORTS_")}
    env.update(SPORTS_WORKER_MAX_MB="768", SPORTS_WORKER_MAX_VM_MB="320",
               PYTHONPATH=str(ROOT), MALLOC_ARENA_MAX="2")
    probe = subprocess.run([sys.executable, "-c",
        "import resource; from sports import worker; worker.apply_memory_cap(); "
        "assert resource.getrlimit(resource.RLIMIT_AS)[0] == 320 * 1024 * 1024"],
        cwd=str(ROOT), env=env, capture_output=True, text=True, timeout=30)
    assert probe.returncode == 0, probe.stderr
