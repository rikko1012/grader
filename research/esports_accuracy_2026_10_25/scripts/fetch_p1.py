import sys, json
sys.path.insert(0, r"C:\Users\tikic\AppData\Local\Temp\claude\C--Users-tikic-OneDrive-Documents-cs2-scout\6847ff60-df65-4b0c-851b-1b79f55ef3bf\scratchpad")
from lq import adm
rows=[]; after=0; meta=None
while True:
    r=adm(f"/admin/learning/p1_units.json?after={after}&limit=2000")
    meta={k:v for k,v in r.items() if k!="rows"}
    rows+=r["rows"]
    if r.get("next_after") is None: break
    after=r["next_after"]
json.dump({"meta":meta,"rows":rows},open("p1.json","w"))
print(meta.get("at"), meta.get("total"), len(rows))
print(json.dumps(rows[-1])[:3000])
