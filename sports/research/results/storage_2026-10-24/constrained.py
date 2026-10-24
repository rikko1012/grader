"""Constrained real-cycle test of one scheduler job (sandbox only, real public feeds, nothing on Render).

usage: python constrained.py <repo> <data_dir> <label> <cgroup_mb> <vm_mb> <out.jsonl> -- <worker args...>
The child gets the Starter scheduler's child environment (sports/scheduler.py _child_env: single-threaded BLAS,
MALLOC_ARENA_MAX=2) and setup (nice 10, oom_score_adj 900), SPORTS_WORKER_CONCURRENCY=1, and runs inside a cgroup-v1
memory group limited to <cgroup_mb> (physical memory, like the container / the job's RSS cap) with RLIMIT_AS =
<vm_mb> via SPORTS_WORKER_MAX_VM_MB (virtual address space, applied by sports.worker itself).
Records: exit code, job states, MemoryError count, VmPeak / VmHWM (peak RSS) sampled from /proc every 0.1 s,
rusage maxrss, cgroup max usage / OOM kills, wall time.
"""
import json
import os
import subprocess
import sys
import threading
import time
from pathlib import Path

repo, data_dir, label, cg_mb, vm_mb, out = sys.argv[1:7]
args = sys.argv[sys.argv.index("--") + 1:]
CG = Path(f"/sys/fs/cgroup/memory/sports_{label}")
CG.mkdir(exist_ok=True)
(CG / "memory.limit_in_bytes").write_text(str(int(cg_mb) * 1024 * 1024))
try:
    (CG / "memory.max_usage_in_bytes").write_text("0")
except OSError:
    pass
oom_before = int(dict(l.split() for l in (CG / "memory.oom_control").read_text().splitlines()).get("oom_kill", 0))

env = {k: v for k, v in os.environ.items()
       if not k.startswith("SPORTS_") and k not in ("ODDS_API_KEY", "ODDSPAPI_API_KEY", "odds_papi_api")}
env.update({"OPENBLAS_NUM_THREADS": "1", "OMP_NUM_THREADS": "1", "MKL_NUM_THREADS": "1", "MALLOC_ARENA_MAX": "2",
            "PYTHONUNBUFFERED": "1", "SPORTS_WORKER_CONCURRENCY": "1", "SPORTS_INGEST": "1",
            "SPORTS_DATA_DIR": data_dir, "SPORTS_WORKER_MAX_VM_MB": str(vm_mb), "SPORTS_WORKER_MAX_MB": str(cg_mb),
            "SPORTS_STORE_MAX_MB": "4000", "PYTHONPATH": repo})
if os.environ.get("CONS_NO_ARENA"):                 # bracket: the child env WITHOUT the scheduler's arena limit
    env.pop("MALLOC_ARENA_MAX", None)


def setup():
    (CG / "cgroup.procs").write_text(str(os.getpid()))
    os.nice(10)
    try:
        Path("/proc/self/oom_score_adj").write_text("900")
    except OSError:
        pass


Path(data_dir).mkdir(parents=True, exist_ok=True)
log = Path(data_dir) / f"{label}.log"
t0 = time.time()
with open(log, "w") as fh:
    p = subprocess.Popen([sys.executable, "-m", "sports.worker", *args], cwd=repo, env=env, stdin=subprocess.DEVNULL,
                         stdout=fh, stderr=subprocess.STDOUT, preexec_fn=setup)
    vm = {}

    def sample():
        while p.poll() is None:
            try:
                for line in open(f"/proc/{p.pid}/status"):
                    if line.startswith(("VmPeak", "VmHWM")):
                        k, v = line.split(":")
                        vm[k] = int(v.split()[0]) // 1024
            except OSError:
                pass
            time.sleep(0.1)
    th = threading.Thread(target=sample, daemon=True)
    th.start()
    _, status, ru = os.wait4(p.pid, 0)
    p.returncode = os.waitstatus_to_exitcode(status)
    th.join(2)
text = log.read_text(errors="replace")
import re
states = dict(re.findall(r'"(nba|nfl)": \{\s*"state": "(\w+)"', text))
oom_after = int(dict(l.split() for l in (CG / "memory.oom_control").read_text().splitlines()).get("oom_kill", 0))
res = {"label": label, "args": args, "cgroup_mb": int(cg_mb), "vm_cap_mb": int(vm_mb), "exit": p.returncode,
       "wall_s": round(time.time() - t0, 1), "job_states": states, "memory_errors": text.count("MemoryError"),
       "vm_peak_mb": vm.get("VmPeak"), "rss_peak_mb": vm.get("VmHWM"), "maxrss_mb": round(ru.ru_maxrss / 1024, 1),
       "cgroup_max_usage_mb": round(int((CG / "memory.max_usage_in_bytes").read_text()) / 1048576, 1),
       "cgroup_oom_kills": oom_after - oom_before}
with open(out, "a") as fh:
    fh.write(json.dumps(res) + "\n")
print(json.dumps(res))
