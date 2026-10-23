"""Dump the HLTV DB schema (tables, columns, approx row counts, sizes) to schema_hltv.json."""
import json, sys, os
sys.path.insert(0, os.path.dirname(__file__))
from db import q
tabs = q("""select c.relname t, c.reltuples::bigint est, pg_total_relation_size(c.oid) bytes
            from pg_class c join pg_namespace n on n.oid=c.relnamespace
            where n.nspname='public' and c.relkind in ('r','p','m','v') order by 1""")
cols = q("""select table_name, column_name, data_type from information_schema.columns
            where table_schema='public' order by table_name, ordinal_position""")
out = {}
for t in tabs:
    out[t["t"]] = {"est_rows": t["est"], "bytes": t["bytes"],
                   "columns": [(c["column_name"], c["data_type"]) for c in cols if c["table_name"] == t["t"]]}
json.dump(out, open(os.path.join(os.path.dirname(__file__), "schema_hltv.json"), "w"), indent=1)
for t, v in out.items():
    print(f"{t}  rows~{v['est_rows']}  {v['bytes']/1e6:.1f}MB")
    print("   ", ", ".join(f"{a}:{b.split(' ')[0]}" for a, b in v["columns"]))
