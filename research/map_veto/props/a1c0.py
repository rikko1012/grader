"""A1 vs C0 (maps alone), paired, on the HLTV per-row outputs of eval_dev.py. usage: python a1c0.py <scratch veto/vprops dir>"""
import sys, numpy as np, pandas as pd
sys.path.insert(0, r"C:\Users\tikic\cs2_scout\.claude\worktrees\map-veto\research\cs2_accuracy_2y\candidates")
import lib
S=sys.argv[1]
for sc in ("1","12","123"):
    d=pd.read_csv(f"{S}/hltv_vm1_dev_{sc}.csv.gz")
    h=d[d.h_ls_A1.notna()]
    cols={"k_ls_A1-C0":(d.k_ls_A1-d.k_ls_C0).values,"k_ls_A2-C0":(d.k_ls_A2-d.k_ls_C0).values,
          "k_absA1-absC0":(np.abs(d.K-d.lamv_A1)-np.abs(d.K-d.lamv_C0)).values,
          "k_absA2-absC0":(np.abs(d.K-d.lamv_A2)-np.abs(d.K-d.lamv_C0)).values,
          "R_A1-C0":(d.R_A1-d.R_C0).values}
    bt,G=lib.boot(cols,d.match.values,b=1000)
    bh,_=lib.boot({"h_ls_A1-C0":(h.h_ls_A1-h.h_ls_C0).values,"h_ls_A2-C0":(h.h_ls_A2-h.h_ls_C0).values},h.match.values,b=1000)
    print(sc,G,{k:"%+.5f [%+.5f, %+.5f]"%v for k,v in {**bt,**bh}.items()}, "sd(R_A1-R_C0) per match %.3f"%d.groupby('match').apply(lambda g:(g.R_A1-g.R_C0).iloc[0]).std())
