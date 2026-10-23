"""UT-1 report: Under concentration, projection decomposition, bias vs line / actual, printed vs won, mechanism tests.
Every interval is a match-grouped percentile bootstrap (B=2000 resamples of whole matches, seed fixed). Pushes on the board line
are excluded from scoring (P1 rule) and counted. usage: python report.py <scratch dir> > report.md"""
import collections
import json
import random
import sys
from pathlib import Path

B, SEED = 2000, 20261022
VORDER = []          # version tags in order of their first P1 start


def boot(rows, f, g=None, key="match"):
    """Match-grouped bootstrap of mean(f) (ratio of sums over resampled matches); g: optional denominator weight."""
    grp = collections.defaultdict(lambda: [0.0, 0.0])
    for r in rows:
        v = f(r)
        if v is None:
            continue
        w = g(r) if g else 1.0
        grp[r[key]][0] += v
        grp[r[key]][1] += w
    s = list(grp.values())
    if not s:
        return None, (None, None)
    n = len(s)
    rnd, res = random.Random(SEED), []
    for _ in range(B):
        a = b = 0.0
        for _ in range(n):
            x = s[rnd.randrange(n)]
            a += x[0]
            b += x[1]
        res.append(a / b if b else float("nan"))
    res.sort()
    tot = sum(x[0] for x in s) / sum(x[1] for x in s)
    return tot, (res[int(0.025 * B)], res[int(0.975 * B) - 1])


def fmt(v, d=2):
    return "-" if v is None else f"{v:+.{d}f}" if isinstance(v, float) else str(v)


def ci(t, d=2, pct=False):
    m, (lo, hi) = t
    if m is None:
        return "-"
    if pct:
        return f"{100 * m:.1f}% [{100 * lo:.1f}, {100 * hi:.1f}]"
    return f"{m:+.{d}f} [{lo:+.{d}f}, {hi:+.{d}f}]"


def nm(rows):
    return f"{len(rows)} / {len({r['match'] for r in rows})}"


SCOPE_NAME = {"1": "Map 1", "12": "Maps 1-2", "123": "Maps 1-3"}


def groups(us):
    for prop in ("kills", "hs"):
        for sc in ("1", "12", "123"):
            g = [u for u in us if u["prop"] == prop and u["scope"] == sc]
            if g:
                yield f"{prop} {SCOPE_NAME[sc]}", g


def t_under_share(us, out):
    out.append("\n## 1. Under concentration (P1 units; graded = favoured side chance >= 55%)\n")
    out.append("| group | P1 lines / matches | Under share, all lines | graded lines / matches | Under share, graded [95% CI] | graded Unders: mean chance |")
    out.append("|---|---|---|---|---|---|")
    for lab, g in groups(us):
        gr = [u for u in g if u["conf"] >= 0.55]
        und = [u for u in gr if u["side"] == "Under"]
        out.append(f"| {lab} | {nm(g)} | {100 * sum(u['side'] == 'Under' for u in g) / len(g):.1f}% | {nm(gr)} | "
                   f"{ci(boot(gr, lambda u: float(u['side'] == 'Under')), pct=True) if gr else '-'} | "
                   f"{(100 * sum(u['conf'] for u in und) / len(und)) if und else 0:.1f}% |")
    out.append("\nBy model version tag (graded lines only), in release order:\n")
    out.append("| version | first .. last start | source(s) | kills M1-2 graded: Under share | HS M1-2 graded: Under share | Map 1 (both stats) | Maps 1-3 (both) |")
    out.append("|---|---|---|---|---|---|---|")
    for v in VORDER:
        g = [u for u in us if u["version"] == v]
        gr = [u for u in g if u["conf"] >= 0.55]
        src = collections.Counter(u["source"] for u in g).most_common(3)

        def sh(x):
            return f"{100 * sum(u['side'] == 'Under' for u in x) / len(x):.0f}% ({len(x)})" if x else "-"
        out.append(f"| {v} | {min(u['start'] for u in g)[:10]} .. {max(u['start'] for u in g)[:10]} | "
                   f"{', '.join(f'{a} {b}' for a, b in src)} | {sh([u for u in gr if u['prop'] == 'kills' and u['scope'] == '12'])} | "
                   f"{sh([u for u in gr if u['prop'] == 'hs' and u['scope'] == '12'])} | {sh([u for u in gr if u['scope'] == '1'])} | "
                   f"{sh([u for u in gr if u['scope'] == '123'])} |")
    out.append("\nBy match day (graded lines, Maps 1-2):\n")
    out.append("| day | kills graded | kills Under share | HS graded | HS Under share |")
    out.append("|---|---|---|---|---|")
    for d in sorted({u["start"][:10] for u in us}):
        k = [u for u in us if u["start"][:10] == d and u["conf"] >= 0.55 and u["scope"] == "12" and u["prop"] == "kills"]
        h = [u for u in us if u["start"][:10] == d and u["conf"] >= 0.55 and u["scope"] == "12" and u["prop"] == "hs"]
        out.append(f"| {d} | {len(k)} | {100 * sum(u['side'] == 'Under' for u in k) / len(k) if k else 0:.0f}% | {len(h)} | "
                   f"{100 * sum(u['side'] == 'Under' for u in h) / len(h) if h else 0:.0f}% |")


DEC = (("L_raw", "his raw rate x R - line"), ("d_prior", "prior shrink"), ("d_env", "environment blend"), ("d_opp", "opponent factor"),
       ("d_cm2", "cm-2 replaces perf-2"), ("d_pmf", "HS pmf mean / rounding"))


def t_decomp(us, out):
    out.append("\n## 2. Decomposition of projection - line (every perf-family P1 line; kills or HS units)\n")
    out.append("Steps add up exactly: (raw x R - line) + prior + env + opp + cm-2 + pmf = published projection - line. "
               "R = 21.74 rounds per map x maps (fixed - the rounds row in section 4 tests it). Mean [95% CI] over lines.\n")
    for subset, flt in (("all lines", lambda u: True), ("graded Unders (>= 55%)", lambda u: u["conf"] >= 0.55 and u["side"] == "Under"),
                        ("graded Overs (>= 55%)", lambda u: u["conf"] >= 0.55 and u["side"] == "Over")):
        out.append(f"\n### {subset}\n")
        out.append("| group | source | lines / matches | raw x R - line | prior shrink | env blend | opponent | cm-2 swap | pmf/rounding | = projection - line |")
        out.append("|---|---|---|---|---|---|---|---|---|---|")
        for lab, g in groups(us):
            for src in ("perf-2", "perf-cm2", "perf-2-bo3"):
                x = [u for u in g if u["source"] == src and flt(u) and u.get("L_raw") is not None]
                if len(x) < 5:
                    continue
                cells = [ci(boot(x, lambda u, k=k: u[k] - (u["line"] if k == "L_raw" else 0.0))) for k, _ in DEC]
                out.append(f"| {lab} | {src} | {nm(x)} | " + " | ".join(cells) + f" | {ci(boot(x, lambda u: u['lam'] - u['line']))} |")


def t_bias(us, out):
    out.append("\n## 3. Projection vs line vs actual (pushes excluded; match-grouped 95% CI)\n")
    out.append("| group | source / version | lines / matches | mean(proj - line) | mean(actual - line) | mean(actual - proj) | Over rate | mean P(Over) printed |")
    out.append("|---|---|---|---|---|---|---|---|")
    for lab, g in groups(us):
        rows = [("all", g)] + [(f"src {s}", [u for u in g if u["source"] == s]) for s in ("perf-2", "perf-cm2", "perf-2-bo3", "legacy")] + \
               [(f"v {v}", [u for u in g if u["version"] == v]) for v in VORDER]
        for name, x in rows:
            if len(x) < 15:
                continue
            out.append(f"| {lab} | {name} | {nm(x)} | {ci(boot(x, lambda u: u['lam'] - u['line']))} | {ci(boot(x, lambda u: u['actual'] - u['line']))} | "
                       f"{ci(boot(x, lambda u: u['actual'] - u['lam']))} | {ci(boot(x, lambda u: float(u['over'])), pct=True)} | "
                       f"{100 * sum(u['p'] for u in x) / len(x):.1f}% |")


BANDS = (("50-55%", .5, .55), ("55-60%", .55, .6), ("60-65%", .6, .65), ("65%+", .65, 1.01))


def t_calib(us, pushes, out):
    out.append("\n## 4. Printed vs won (favoured side; pushes excluded and counted)\n")
    pc = collections.Counter((p.get("prop"), p.get("scope")) for p in pushes)
    out.append("Pushes excluded on the board line: " + ", ".join(f"{a} {SCOPE_NAME.get(b, b)} {n}" for (a, b), n in sorted(pc.items())) + "\n")
    out.append("| group | side | band | lines / matches | printed | won [95% CI] | won - printed [95% CI] |")
    out.append("|---|---|---|---|---|---|---|")
    hit = lambda u: float(u["over"] if u["side"] == "Over" else 1 - u["over"])
    for lab, g in groups(us):
        for side in ("Under", "Over"):
            for b, lo, hi in BANDS:
                x = [u for u in g if u["side"] == side and lo <= u["conf"] < hi]
                if not x:
                    continue
                out.append(f"| {lab} | {side} | {b} | {nm(x)} | {100 * sum(u['conf'] for u in x) / len(x):.1f}% | {ci(boot(x, hit), pct=True)} | "
                           f"{ci(boot(x, lambda u: hit(u) - u['conf']), d=3)} |")
            x = [u for u in g if u["side"] == side and u["conf"] >= 0.55]
            if x:
                out.append(f"| {lab} | {side} | **all 55%+** | {nm(x)} | {100 * sum(u['conf'] for u in x) / len(x):.1f}% | {ci(boot(x, hit), pct=True)} | "
                           f"{ci(boot(x, lambda u: hit(u) - u['conf']), d=3)} |")
    out.append("\nGraded (55%+) by version and side, both stats and all scopes pooled within a stat:\n")
    out.append("| stat | version | side | lines / matches | printed | won [95% CI] |")
    out.append("|---|---|---|---|---|---|")
    for prop in ("kills", "hs"):
        for v in VORDER:
            for side in ("Under", "Over"):
                x = [u for u in us if u["prop"] == prop and u["version"] == v and u["side"] == side and u["conf"] >= 0.55]
                if len(x) < 10:
                    continue
                out.append(f"| {prop} | {v} | {side} | {nm(x)} | {100 * sum(u['conf'] for u in x) / len(x):.1f}% | {ci(boot(x, hit), pct=True)} |")


def main(scr):
    d = json.load(open(Path(scr) / "units.json"))
    us = [u for u in d["units"] if u["side"] != "none"]
    first = {}
    for u in us:
        first[u["version"]] = min(first.get(u["version"], "9"), u["start"])
    VORDER.extend(sorted(first, key=first.get))
    out = [f"<!-- generated by report.py from units.json built {d['built_at']} (P1 export {d['p1_at']}, HLTV pull {d['hltv_pulled_at']}) -->",
           f"P1 units scored: {len(us)} lines, {len({u['match'] for u in us})} matches (exact 50% ties dropped: "
           f"{len(d['units']) - len(us)}); P1 exclusions: {json.dumps(d['p1_excluded'])}"]
    t_under_share(us, out)
    t_decomp(us, out)
    t_bias(us, out)
    t_calib(us, d.get("p1_pushes") or [], out)
    print("\n".join(out))


if __name__ == "__main__":
    main(sys.argv[1])
