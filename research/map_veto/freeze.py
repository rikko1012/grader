"""Freeze the candidate: check veto_model.PARAMS equals the tuned final params, then write FROZEN.json
(version id, params sha, params, the tuning log sha). bench.py refuses to touch FINAL until this file exists.
usage: python freeze.py"""
import datetime as dt, hashlib, json, os, sys
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", ".."))
import veto_model as vm  # noqa: E402

if __name__ == "__main__":
    tl = json.load(open(os.path.join(HERE, "tuning_log.json")))
    if json.dumps(tl["final_params"], sort_keys=True) != json.dumps(vm.PARAMS, sort_keys=True):
        sys.exit("veto_model.PARAMS differs from tuning_log.json final_params - copy them first")
    raw = open(os.path.join(HERE, "tuning_log.json"), "rb").read()
    out = {"version": vm.version(), "params_sha": vm.params_sha(), "params": vm.PARAMS,
           "tuning_log_sha256": hashlib.sha256(raw).hexdigest(),
           "frozen_at": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"),
           "final_period": ["2026-08-23", "2026-10-22"], "note": "FINAL scored once after this file is committed"}
    json.dump(out, open(os.path.join(HERE, "FROZEN.json"), "w"), indent=1)
    print(out["version"])
