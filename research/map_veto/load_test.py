"""700 concurrent predictions against the serving table (threads, like the web workers): latency, CPU time,
cache bound, memory. usage: python load_test.py <table.json.gz>"""
import os, random, sys, threading, time, tracemalloc
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", ".."))
import veto_model as vm  # noqa: E402

POOL = ["ancient", "anubis", "cache", "dust2", "inferno", "mirage", "nuke"]


def run(n_threads, pairs, label):
    vm._CACHE.clear()
    lat, errs = [], []
    barrier = threading.Barrier(n_threads)

    def worker(i):
        a, b, fmt = pairs[i % len(pairs)]
        barrier.wait()
        t0 = time.perf_counter()
        try:
            vm.predict(a, b, fmt, POOL, "2026-10-23")
        except Exception as e:      # noqa: BLE001
            errs.append(repr(e))
        lat.append(time.perf_counter() - t0)
    ts = [threading.Thread(target=worker, args=(i,)) for i in range(n_threads)]
    t0 = time.perf_counter()
    for t in ts:
        t.start()
    for t in ts:
        t.join()
    wall = time.perf_counter() - t0
    lat.sort()
    print(label, {"threads": n_threads, "distinct_inputs": len(pairs), "wall_s": round(wall, 2),
                  "p50_ms": round(lat[len(lat) // 2] * 1000, 1), "p95_ms": round(lat[int(len(lat) * .95)] * 1000, 1),
                  "max_ms": round(lat[-1] * 1000, 1), "errors": len(errs), "cache": len(vm._CACHE)})


def cache_mb():
    tracemalloc.start()
    vm._CACHE.clear()
    a = tracemalloc.get_traced_memory()[0]
    rnd = random.Random(2)
    teams = sorted(vm._TABLE["t"].team)
    while len(vm._CACHE) < vm.CACHE_MAX:
        x, y = rnd.sample(teams, 2)
        vm.predict(x, y, rnd.choice(["bo1", "bo3", "bo5"]), POOL, "2026-10-23")
    b = tracemalloc.get_traced_memory()[0]
    tracemalloc.stop()
    return round((b - a) / 2 ** 20, 1)


if __name__ == "__main__":
    tracemalloc.start()
    t = vm.VetoTable.load(sys.argv[1])
    base = tracemalloc.get_traced_memory()[0]
    tracemalloc.stop()
    vm.set_table(t)
    rnd = random.Random(1)
    teams = sorted(t.team, key=lambda k: -len(t.team[k]))[:200]
    board = [(rnd.choice(teams), rnd.choice(teams), rnd.choice(["bo3", "bo3", "bo3", "bo1", "bo5"])) for _ in range(60)]
    board = [x for x in board if x[0] != x[1]]
    run(700, board, "board-like (60 matches, 700 members)")
    t0 = time.perf_counter()
    lat = []
    for a, b, f in board:
        s0 = time.perf_counter(); vm.predict(a, b, f, POOL, "2026-10-23"); lat.append(time.perf_counter() - s0)
    print("board-like warm cache: max ms", round(max(lat) * 1000, 3))
    worst = [(rnd.choice(teams), rnd.choice(teams), "bo3") for _ in range(700)]
    worst = [x for x in worst if x[0] != x[1]]
    run(700, worst, "worst case (all distinct)")
    print("memory MB: table", round(base / 2 ** 20, 1), "full cache", cache_mb(), "entries", vm.CACHE_MAX)
