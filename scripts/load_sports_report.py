"""Summarise scripts/load_sports.py output: per config, median [min-max] over runs, ratio to the `off` median of the
SAME file, and the registered budget verdicts (sports/RUNBOOK.md section 1).
usage: python load_sports_report.py <runs.jsonl> [more.jsonl ...]"""
import json
import statistics
import sys


def med(xs):
    xs = [x for x in xs if x is not None]
    return (statistics.median(xs), min(xs), max(xs)) if xs else (None, None, None)


def fmt(t, unit=""):
    m, lo, hi = t
    if m is None:
        return "-"
    return f"{m:g}{unit} [{lo:g}-{hi:g}]"


def main(paths):
    for path in paths:
        rows = [json.loads(x) for x in open(path, encoding="utf-8") if x.strip()]
        by = {}
        for r in rows:
            by.setdefault(r["config"], []).append(r)
        base = by.get("off", [])
        b99 = med([r["results"]["esports"]["p99_ms"] for r in base if "results" in r])[0]
        b95 = med([r["results"]["esports"]["p95_ms"] for r in base if "results" in r])[0]
        brss = med([r["processes"]["esports"]["rss_peak_mb"] for r in base if "processes" in r])[0]
        print(f"\n### {path}\n")
        print("| Config | Runs | Esports p95 ms | Esports p99 ms | p99 vs off | Esports 5xx | Esports peak RSS MB |"
              " Other python CPU % | Token p99 ms | Sports non-200 / req | Sports p99 ms | Service peak RSS MB | Pool peak | Verdict |")
        print("|" + "---|" * 14)
        for cfg, rs in by.items():
            ok = [r for r in rs if "results" in r and "esports" in r["results"]]
            e = [r["results"]["esports"] for r in ok]
            p95, p99 = med([x["p95_ms"] for x in e]), med([x["p99_ms"] for x in e])
            rss = med([r["processes"]["esports"]["rss_peak_mb"] for r in ok])
            fivexx = sum(x["errors_5xx"] for x in e)
            tok = med([r["results"].get("token", {}).get("p99_ms") for r in ok])
            sp = [r["results"].get("sports") for r in ok if r["results"].get("sports")]
            snon = (sum(x["non_200"] for x in sp), sum(x["requests"] for x in sp)) if sp else None
            sp99 = med([x["p99_ms"] for x in sp]) if sp else (None, None, None)
            srss = med([r["processes"].get("service", {}).get("rss_peak_mb") for r in ok])
            oth = med([r.get("other_python_cpu_pct_before") for r in ok])
            pool = max([r.get("service_ops", {}).get("db_pool", {}).get("peak_in_use", 0) for r in ok] or [0])
            ratio = round(p99[0] / b99, 3) if p99[0] and b99 else None
            verdict = ""
            if cfg != "off" and ratio is not None:
                passed = (ratio <= 1.10 and p95[0] <= b95 * 1.10 and fivexx == 0
                          and (rss[0] or 0) <= (brss or 0) + 10)
                verdict = "PASS" if passed else "FAIL"
            print(f"| {cfg} | {len(ok)}/{len(rs)} | {fmt(p95)} | {fmt(p99)} | {ratio if ratio else '-'} | {fivexx} | "
                  f"{fmt(rss)} | {fmt(oth)} | {fmt(tok)} | {f'{snon[0]} / {snon[1]}' if snon else '-'} | {fmt(sp99)} | "
                  f"{fmt(srss)} | {pool} | {verdict} |")
        retries = sum(r["results"]["esports"].get("connect_retries", 0) for r in rows if "results" in r)
        errs = [r["config"] + ": " + r["error"] for r in rows if r.get("error")]
        print(f"\nconnect retries (all esports requests): {retries}; harness errors: {errs or 'none'}")


if __name__ == "__main__":
    main(sys.argv[1:])
