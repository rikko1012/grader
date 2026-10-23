"""Cross-check: re-parse the veto box of a stratified random sample of STORED HLTV match pages (raw_pages.html_gz in
the production HLTV DB, read-only, bounded: 40 pages per quarter) with the live parser's regexes (hltv._VETO /
hltv._LEFT) and compare step-by-step with the map_vetoes rows used for the dataset.

usage: python check_parse.py  -> <scratch>/veto/vmodel/check_parse.json"""
import collections, gzip, json, os, random, sys
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "cs2_accuracy_2y", "inventory"))
sys.path.insert(0, os.path.join(HERE, "..", ".."))
from db import conn  # noqa: E402  (read-only session, statement_timeout)
import hltv, veto_model as vm  # noqa: E402

SCR = r"C:\Users\tikic\AppData\Local\Temp\claude\C--Users-tikic-OneDrive-Documents-cs2-scout\6847ff60-df65-4b0c-851b-1b79f55ef3bf\scratchpad\veto\vmodel"


def main():
    with gzip.open(os.path.join(SCR, "veto_dataset.json.gz"), "rt", encoding="utf-8") as f:
        recs = json.load(f)["records"]
    rnd = random.Random(20261023)
    byq = collections.defaultdict(list)
    for r in recs:
        y, m = int(r["date"][:4]), int(r["date"][5:7])
        byq[f"{y}Q{(m - 1) // 3 + 1}"].append(r)
    sample = [r for q in sorted(byq) for r in rnd.sample(byq[q], min(40, len(byq[q])))]
    c = conn()
    names = {}
    with c.cursor() as cur:
        cur.execute("SELECT teamid, name FROM teams")
        names = {t: hltv._norm(n) for t, n in cur.fetchall()}
    res = collections.Counter()
    bad = []
    for r in sample:
        with c.cursor() as cur:
            cur.execute("SELECT html_gz FROM raw_pages WHERE matchid = %s", (r["mid"],))
            row = cur.fetchone()
        if not row:
            res["no_page"] += 1
            continue
        html = gzip.decompress(bytes(row[0])).decode("utf-8", "replace")
        box = html[html.find("veto-box"):][:6000]
        steps = {}
        for n, t, act, m in hltv._VETO.findall(box):
            steps.setdefault(int(n), [int(n), hltv._norm(t), act, vm.map_key(m)])
        for n, m in hltv._LEFT.findall(box):
            steps.setdefault(int(n), [int(n), None, "leftover", vm.map_key(m)])
        page = [steps[k] for k in sorted(steps)]
        mine = [[s, names.get(t) if t is not None else None, a, m] for s, t, a, m in r["actions"]
                if not (r.get("inferred_leftover") and a == "leftover")]
        if page == mine:
            res["identical"] += 1
        elif [x[::2] for x in page] == [x[::2] for x in mine] and [x[3] for x in page] == [x[3] for x in mine]:
            res["same_maps_team_name_differs"] += 1      # team renamed since (veto text keeps the old name)
        else:
            res["different"] += 1
            bad.append({"mid": r["mid"], "page": page, "db": mine})
    out = {"sampled": len(sample), "result": dict(res), "different": bad[:20]}
    json.dump(out, open(os.path.join(SCR, "check_parse.json"), "w"), indent=1)
    print(json.dumps(out["result"]), out["sampled"])


if __name__ == "__main__":
    main()
