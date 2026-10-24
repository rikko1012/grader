"""In-process RSS profile of one ingest cycle: each time RSS rises by >= 4 MB, record the sports/ stacks of the
worker threads. usage (env SPORTS_DATA_DIR / SPORTS_INGEST set by caller): python profile_rss.py <out.json> [sources]"""
import json
import os
import sys
import threading
import time
from pathlib import Path

out = sys.argv[1]
srcs = sys.argv[2] if len(sys.argv) > 2 else None
peaks, done = [], []


def rss():
    with open("/proc/self/statm") as fh:
        return int(fh.read().split()[1]) * os.sysconf("SC_PAGE_SIZE") / 1e6


def sample():
    best = 0.0
    me = threading.get_ident()
    while not done:
        r = rss()
        if r >= best + 4:
            best = r
            desc = []
            for tid, f in sys._current_frames().items():
                if tid == me:
                    continue
                st = []
                while f is not None:
                    fn = f.f_code.co_filename
                    if "/sports/" in fn and "profile_rss" not in fn:
                        st.append(f"{Path(fn).parent.name}/{Path(fn).name}:{f.f_code.co_name}:{f.f_lineno}")
                    f = f.f_back
                if st:
                    desc.append(" < ".join(st[:5]))
            peaks.append({"t": round(time.time() - t0, 1), "rss": round(r), "stacks": desc})
        time.sleep(0.05)


t0 = time.time()
th = threading.Thread(target=sample, daemon=True)
th.start()
from sports import worker  # noqa: E402

try:
    worker.main(["ingest", "nba", "nfl"] + (["--sources", srcs] if srcs else []))
finally:
    done.append(1)
    th.join()
    vm = {l.split(":")[0]: l.split(":")[1].strip() for l in open("/proc/self/status") if l.startswith(("VmPeak", "VmHWM"))}
    Path(out).write_text(json.dumps({"peak": max(p["rss"] for p in peaks), "vm": vm, "peaks": peaks}, indent=1))
