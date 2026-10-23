"""Esports-under-sports load test with REAL process isolation (sports/RUNBOOK.md sections 1-2; budgets registered
before the runs). Three OS processes - esports server, sports service, this load generator - plus an ingestion
writer for "ingesting".

usage: python load_sports.py <code_dir> <store_dir> <out.jsonl> <config>[,<config>...] [--runs 5] [--cool 40]
configs: off | inprocess-enabled | {service,proxy}-{enabled,ingesting,failing,broken,recovering}
  failing = sports service process DOWN; broken = service up on a corrupt store (every query raises).
Each run appends one JSON line to out.jsonl. 700 simultaneous members; each makes the foundation's 3 esports requests,
plus (sports on) its sports traffic: service = token (esports) + 1 sports request to the service; proxy / inprocess =
1 sports request to esports. Esports traffic is identical in every mode; sports traffic runs in parallel threads.
"""
import http.client
import json
import os
import random
import secrets
import shutil
import subprocess
import sys
import tempfile
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import psutil

HERE = Path(__file__).resolve().parent
MEMBERS = 700
ESPORTS = ["/api/moneylines", "/auth/me", "/", "/api/board"]
SPORTS = ["/api/sports/status", "/api/sports/offers?league=nba&limit=200", "/api/sports/offers?league=nfl&stat=rec"]
ORIGIN = "https://cs2-scout.example.test"
SECRET = secrets.token_hex(24)
ADMIN = secrets.token_hex(16)


def _free_port():
    import socket
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    p = s.getsockname()[1]
    s.close()
    return p


def _call(port, path, headers=None):
    t0 = time.time()
    tries = 0
    while True:
        c = http.client.HTTPConnection("127.0.0.1", port, timeout=60)
        try:
            c.request("GET", path, headers=headers or {})
            r = c.getresponse()
            body = r.read()
            c.close()
            return r.status, time.time() - t0, body, tries
        except ConnectionRefusedError:
            c.close()
            return -1, time.time() - t0, b"", tries          # nothing listens (failing scenario): not retried
        except OSError:
            c.close()
            tries += 1                                     # backlog overflow / ephemeral ports: retried, counted
            if tries > 20:
                return -2, time.time() - t0, b"", tries
            time.sleep(0.25 * tries)


class Procs:
    def __init__(self, code, cfg, store_dir, work):
        self.code, self.cfg, self.store_dir, self.work = code, cfg, store_dir, work
        self.mode, _, self.scenario = cfg.partition("-")
        self.esp = self.svc = self.ing = None
        self.svc_port = _free_port()

    def env(self):
        e = {k: v for k, v in os.environ.items() if not k.startswith("SPORTS_")}
        e.update({"PYTHONIOENCODING": "utf-8", "SPORTS_TOKEN_SECRET": SECRET, "ADMIN_TOKEN": ADMIN,
                  "SPORTS_ALLOWED_ORIGINS": ORIGIN})
        return e

    def sports_dir(self):
        if self.scenario == "broken":
            d = Path(self.work) / "broken"
            d.mkdir(exist_ok=True)
            (d / "canonical.db").write_bytes(b"this is not a sqlite database " * 1000)
            return str(d)
        d = Path(self.work) / "store"
        if not d.exists():
            d.mkdir()
            shutil.copy2(Path(self.store_dir) / "canonical.db", d / "canonical.db")
        return str(d)

    def start_esports(self):
        e = self.env()
        if self.mode != "off":
            e.update({"SPORTS_API": "1", "SPORTS_UI": "1", "SPORTS_API_MODE": self.mode,
                      "SPORTS_DATA_DIR": self.sports_dir(), "SPORTS_API_BASE": f"http://127.0.0.1:{self.svc_port}",
                      "SPORTS_UPSTREAM": f"127.0.0.1:{self.svc_port}"})
        data = Path(self.work) / "esports"
        data.mkdir(exist_ok=True)
        self.esp = subprocess.Popen([sys.executable, str(HERE / "load_sports_esports.py"), self.code, str(data)],
                                    env=e, stdout=subprocess.PIPE, stderr=open(Path(self.work) / "esports.err", "w"),
                                    stdin=subprocess.DEVNULL, text=True)
        line = self.esp.stdout.readline()
        assert line.startswith("PORT"), line
        self.esp_port = int(line.split()[1])

    def start_service(self):
        e = self.env()
        e.update({"SPORTS_API": "1", "SPORTS_DATA_DIR": self.sports_dir(), "PYTHONPATH": self.code})
        self.svc = subprocess.Popen([sys.executable, "-m", "sports.service", "--host", "127.0.0.1",
                                     "--port", str(self.svc_port)], cwd=self.code, env=e,
                                    stdout=subprocess.DEVNULL, stderr=open(Path(self.work) / "service.err", "a"),
                                    stdin=subprocess.DEVNULL)
        t0 = time.time()
        while time.time() - t0 < 30:
            st = _call(self.svc_port, "/api/sports/health")[0]
            if st == 200:
                return time.time() - t0
            time.sleep(0.05)
        raise RuntimeError("sports service did not start")

    def start_ingest(self):
        self.ing = subprocess.Popen([sys.executable, str(HERE / "load_sports_ingest.py"), self.code, self.sports_dir(),
                                     "300", "0.5"], stdout=open(Path(self.work) / "ingest.out", "w"),
                                    stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL)
        time.sleep(3)                                      # writer warmed and committing

    def stop(self):
        for p in (self.ing, self.svc, self.esp):
            if p and p.poll() is None:
                p.kill()
                p.wait(10)


class Sampler(threading.Thread):
    def __init__(self, procs):
        super().__init__(daemon=True)
        self.procs = {k: psutil.Process(p.pid) for k, p in procs.items() if p and p.poll() is None}
        self.peak = {k: 0.0 for k in self.procs}
        self.threads = {k: 0 for k in self.procs}
        self.start_rss = {k: self._rss(p) for k, p in self.procs.items()}
        self.stop_ev = threading.Event()
        self.host_cpu = []                                 # whole-machine CPU % (other sessions' load shows here)
        self.others_cpu_before = self._others()

    def _others(self):
        """CPU % of python processes that are NOT part of this run (other sessions' tests share the workstation)."""
        mine = {p.pid for p in self.procs.values()} | {os.getpid()}
        tot = 0.0
        for p in psutil.process_iter(["pid", "name"]):
            if p.info["pid"] in mine or "python" not in (p.info["name"] or "").lower():
                continue
            try:
                tot += p.cpu_percent(interval=None)
            except psutil.Error:
                pass
        return tot

    @staticmethod
    def _rss(p):
        try:
            return p.memory_info().rss / 1e6
        except psutil.Error:
            return 0.0

    def run(self):
        psutil.cpu_percent(interval=None)
        while not self.stop_ev.is_set():
            self.host_cpu.append(psutil.cpu_percent(interval=None))
            for k, p in self.procs.items():
                self.peak[k] = max(self.peak[k], self._rss(p))
                try:
                    self.threads[k] = max(self.threads[k], p.num_threads())
                except psutil.Error:
                    pass
            self.stop_ev.wait(0.02)

    def report(self):
        end = {k: self._rss(p) for k, p in self.procs.items()}
        out = {"host_cpu_pct_mean": round(sum(self.host_cpu[1:]) / max(1, len(self.host_cpu) - 1), 1)}
        return out | {k: {"rss_start_mb": round(self.start_rss[k], 1), "rss_peak_mb": round(self.peak[k], 1),
                    "rss_end_mb": round(end[k], 1), "threads_peak": self.threads[k]} for k in self.procs}


def burst(P, seed):
    """Esports arrivals are IDENTICAL in every mode (700 members x the same 3 sequential esports requests, all
    started together); with sports on, each member's sports traffic runs in a parallel thread over the same burst."""
    def esports(i):
        cookie = {"Cookie": f"cs2s=good-u{i}"}
        out = []
        for k in range(3):
            p = ESPORTS[(i + k) % len(ESPORTS)]
            st, dt, _, tr = _call(P.esp_port, p, cookie)
            out.append(("esports", p, st, dt, tr))
        return out

    def sports(i):
        cookie = {"Cookie": f"cs2s=good-u{i}"}
        out = []
        p = SPORTS[i % len(SPORTS)]
        if P.mode == "service":
            st, dt, body, tr = _call(P.esp_port, "/api/sports/token", cookie)
            out.append(("token", "/api/sports/token", st, dt, tr))
            tok = json.loads(body)["token"] if st == 200 else "none"
            st, dt2, _, tr = _call(P.svc_port, p, {"Authorization": f"Bearer {tok}", "Origin": ORIGIN})
            out.append(("sports", p, st, dt2, tr))
            out.append(("sports_flow", p, st, dt + dt2, 0))
        else:
            st, dt, _, tr = _call(P.esp_port, p, cookie)
            out.append(("sports", p, st, dt, tr))
        return out
    t0 = time.time()
    n = MEMBERS * (2 if P.mode != "off" else 1)
    with ThreadPoolExecutor(max_workers=n) as ex:
        futs = [ex.submit(esports, i) for i in range(MEMBERS)]
        if P.mode != "off":
            futs += [ex.submit(sports, i) for i in range(MEMBERS)]
        res = [x for f in futs for x in f.result()]
    return res, time.time() - t0


def pct(xs, q):
    xs = sorted(xs)
    return round(xs[min(len(xs) - 1, int(q * len(xs)))] * 1000, 1) if xs else None


def summarize(res):
    out = {}
    for grp in ("esports", "token", "sports", "sports_flow"):
        rows = [x for x in res if x[0] == grp]
        if not rows:
            continue
        lat = [x[3] for x in rows]
        codes = {}
        for x in rows:
            codes[str(x[2])] = codes.get(str(x[2]), 0) + 1
        out[grp] = {"requests": len(rows), "errors_5xx": sum(1 for x in rows if x[2] >= 500),
                    "non_200": sum(1 for x in rows if x[2] != 200), "p50_ms": pct(lat, .5), "p95_ms": pct(lat, .95),
                    "p99_ms": pct(lat, .99), "max_ms": pct(lat, 1.0), "codes": codes,
                    "connect_retries": sum(x[4] for x in rows)}
    both = [x for x in res if x[0] in ("esports", "token")]
    if any(x[0] == "token" for x in res):
        out["esports_incl_token"] = {"requests": len(both), "p95_ms": pct([x[3] for x in both], .95),
                                     "p99_ms": pct([x[3] for x in both], .99)}
    return out


def admin_stats(P):
    o = {}
    if P.mode in ("service", "proxy") and P.svc and P.svc.poll() is None:
        st, _, body, _ = _call(P.svc_port, "/api/sports/admin/ops", {"X-Admin-Token": ADMIN})
        if st == 200:
            d = json.loads(body)
            o["service_ops"] = {"service": d["service"], "db_pool": d["db_pool"], "cache": d["cache"],
                                "rss_mb": d["rss_mb"], "threads": d["threads"]}
    if P.mode in ("service", "proxy"):
        st, _, body, _ = _call(P.esp_port, "/api/sports/admin/gateway", {"X-Admin-Token": ADMIN})
        if st == 200:
            o["gateway"] = json.loads(body)
    return o


def run_one(code, store_dir, cfg, seed):
    work = tempfile.mkdtemp(prefix=f"ld_{cfg}_", dir=str(Path(store_dir).parent))
    P = Procs(code, cfg, store_dir, work)
    rec = {"config": cfg, "seed": seed, "members": MEMBERS, "t": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())}
    try:
        P.start_esports()
        if P.mode in ("service", "proxy") and P.scenario not in ("failing", "recovering"):
            rec["service_start_s"] = round(P.start_service(), 2)
        if P.scenario == "ingesting":
            P.start_ingest()
        for _ in range(20):                                # warm (same as the foundation script)
            _call(P.esp_port, "/api/moneylines", {"Cookie": "cs2s=good-warm"})
        S = Sampler({"esports": P.esp, "service": P.svc, "ingest": P.ing})
        time.sleep(1.0)
        S.others_cpu_before = S._others()                  # second sample = CPU % over the last second
        S.start()
        res, wall = burst(P, seed)
        rec["wall_s"] = round(wall, 2)
        rec["results"] = summarize(res)
        if P.scenario == "recovering":
            t_restart = time.time()
            rec["service_start_s"] = round(P.start_service(), 2)
            S.stop_ev.set()
            S.join()
            rec["burst1_down"] = rec.pop("results")
            S = Sampler({"esports": P.esp, "service": P.svc})
            S.start()
            res, wall = burst(P, seed + 1)
            rec["recovery_s_from_restart_to_burst2"] = round(time.time() - t_restart - wall, 2)
            rec["wall_s_burst2"] = round(wall, 2)
            rec["results"] = summarize(res)
        S.stop_ev.set()
        S.join()
        rec["processes"] = S.report()
        rec["other_python_cpu_pct_before"] = round(S.others_cpu_before, 1)
        rec.update(admin_stats(P))
        if P.ing:
            lines = (Path(work) / "ingest.out").read_text().splitlines()
            rec["ingest_last"] = json.loads(lines[-1]) if lines else None
        rec["alive"] = {k: (p.poll() is None) for k, p in (("esports", P.esp), ("service", P.svc)) if p}
    except Exception as e:                                 # noqa: BLE001
        rec["error"] = f"{type(e).__name__}: {e}"
    finally:
        P.stop()
        time.sleep(1)
        shutil.rmtree(work, ignore_errors=True)
    return rec


def time_wait():
    try:
        return sum(1 for c in psutil.net_connections(kind="tcp") if c.status == psutil.CONN_TIME_WAIT)
    except Exception:                                      # noqa: BLE001
        out = subprocess.run(["netstat", "-an"], capture_output=True, text=True).stdout
        return out.count("TIME_WAIT")


def cool_down(min_s, limit=4000, max_s=300):
    """Wait at least min_s and until fewer than `limit` (4,000) sockets are in TIME_WAIT (Windows keeps them 120 s; the
    16,384-port dynamic range is the workstation's limit - smoke run 2026-10-23 exhausted it after 5 s cool-downs)."""
    t0 = time.time()
    time.sleep(min_s)
    while time_wait() > limit and time.time() - t0 < max_s:
        time.sleep(5)
    return round(time.time() - t0), time_wait()


def main():
    code, store_dir, out = sys.argv[1], sys.argv[2], sys.argv[3]
    cfgs = sys.argv[4].split(",")
    runs = int(sys.argv[sys.argv.index("--runs") + 1]) if "--runs" in sys.argv else 5
    cool = float(sys.argv[sys.argv.index("--cool") + 1]) if "--cool" in sys.argv else 40
    for r in range(runs):                                  # interleaved: run r of every config, then run r+1
        for cfg in cfgs:
            waited, tw = cool_down(cool)
            rec = run_one(code, store_dir, cfg, seed=1000 + r)
            rec["cool_down_s"], rec["time_wait_at_start"] = waited, tw
            rec["run"] = r
            with open(out, "a", encoding="utf-8") as f:
                f.write(json.dumps(rec) + "\n")
            e = rec.get("results", {}).get("esports", {})
            print(f"{cfg} run {r}: esports p95/p99 {e.get('p95_ms')}/{e.get('p99_ms')} 5xx {e.get('errors_5xx')}"
                  f" retries {e.get('connect_retries')} {rec.get('error', '')}", flush=True)


if __name__ == "__main__":
    main()
