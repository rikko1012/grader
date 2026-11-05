"""Chronological walk-forward evaluation of the CS2 projection candidates against the EXACT live chances.

Rows: the 6,317 settled P1 board units (latest standard board line logged >= 30 min before start, Oct 11 - Nov 4,
pushes excluded), pulled read-only from /admin/learning/p1_units.json (paged) on 2026-11-05.
Live = the logged chance of that forecast (unchanged). Candidate = P(Over) at the same line from the candidate's
projection; spread = the live row's own spread (recovered from its chance) unless the candidate changes it.

Walk-forward: each test day D (2026-10-19 .. 2026-11-04) is predicted by models fitted ONLY on rows whose match
started >= 6 h before D 00:00 UTC; the fit is redone every day. Nothing settled on or after D enters D's fit.
Graded pick = chance on the chosen side >= 55% (the live letter floor). Side = the side of the row's own chance.

CIs: 95% bootstrap, resampling whole matches (canonical match id), paired live vs candidate.

Pass rule (all): log-loss difference CI entirely < 0; graded stated - hit within 2.5 pts overall, for Overs and for
Unders; graded Over share within 5 pts of the test rows' realized Over rate; graded count >= 90% of live.

Run: python research/proj_cs2_2026_11_05/evaluate.py  (writes result_2026-11-05.json next to this file)
"""
from __future__ import annotations

import collections
import datetime as dt
import gzip
import json
import math
import os
import random
import sys

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import proj_candidates as pc  # noqa: E402

DATA = os.path.join(HERE, 'p1_units_2026-11-05.json.gz')
TEST_FROM = '2026-10-19'
BUFFER_H = 6
GRADE = 0.55
B = 1000


def load(path=DATA):
    with gzip.open(path, 'rt') as f:
        d = json.load(f)
    rows = d['rows']
    for x in rows:
        x['day'] = x['start'][:10]
        x['t'] = dt.datetime.fromisoformat(x['start'][:19])
        x['sd_live'] = pc.implied_sd(x['lam'], x['line'], x['p'], x['prop'], x['scope'])
    return rows


def walk_forward(rows):
    days = sorted({x['day'] for x in rows if x['day'] >= TEST_FROM})
    out = []
    for d in days:
        cut = dt.datetime.fromisoformat(d + 'T00:00:00') - dt.timedelta(hours=BUFFER_H)
        train = [x for x in rows if x['t'] <= cut]
        test = [x for x in rows if x['day'] == d]
        assert all(t['t'] < dt.datetime.fromisoformat(d + 'T00:00:00') for t in train)
        Fst = pc.fit_st(train)
        Wmi = pc.fit_w(train)
        Wmi_c = pc.fit_w(train, seg=pc.seg_cell)
        st_mean = lambda x: pc.lam_st(x, Fst)  # noqa: E731
        Wmx = pc.fit_w(train, mean_fn=st_mean)
        Wmx_c = pc.fit_w(train, mean_fn=st_mean, seg=pc.seg_cell)
        mx_mean = lambda x: pc.lam_mi(x, Wmx, Wmx_c, pc.lam_st(x, Fst))  # noqa: E731
        Smx = pc.fit_sd_scale(train, mx_mean, lambda x: x['sd_live'])
        Sst = pc.fit_sd_scale(train, st_mean, lambda x: x['sd_live'])
        for x in test:
            sd = x['sd_live']
            m_st = pc.lam_st(x, Fst)
            m_mi = pc.lam_mi(x, Wmi, Wmi_c, x['lam'])
            m_mx = pc.lam_mi(x, Wmx, Wmx_c, m_st)
            po = {
                'live': x['p'],
                'live_recon': pc.pover(x['lam'], sd, x['line']),
                'st-1': pc.pover(m_st, sd, x['line']),
                'st-1+sd-1': pc.pover(m_st, sd * Sst.get(pc.seg_cell(x), 1.0), x['line']),
                'mi-2': pc.pover(m_mi, sd, x['line']),
                'mx-1': pc.pover(m_mx, sd, x['line']),
                'mx-1+sd-1': pc.pover(m_mx, sd * Smx.get(pc.seg_cell(x), 1.0), x['line']),
            }
            out.append(dict(match=x['match'], day=d, actual=x['actual'], prop=x['prop'], scope=x['scope'], over=x['over'],
                            line=x['line'], lam=x['lam'], m_st=m_st, m_mi=m_mi, m_mx=m_mx, po=po,
                            agree=pc.agree(x), version=x['version']))
    return out


# ---------------- metrics ----------------
def _side(po, over):
    s = po >= 0.5
    return s, (po if s else 1 - po), (over if s else 1 - over)


def ll_rows(rs, k):
    e = 1e-6
    return [-(x['over'] * math.log(max(e, x['po'][k])) + (1 - x['over']) * math.log(max(e, 1 - x['po'][k]))) for x in rs]


def summary(rs, k):
    sides = [_side(x['po'][k], x['over']) for x in rs]
    g = [s for s in sides if s[1] >= GRADE]
    go = [s for s in g if s[0]]
    gu = [s for s in g if not s[0]]

    def sh(lst):
        if not lst:
            return dict(n=0)
        st = float(np.mean([s[1] for s in lst]))
        h = float(np.mean([s[2] for s in lst]))
        return dict(n=len(lst), stated=round(st, 4), hit=round(h, 4), gap=round(st - h, 4))
    bins = {}
    for lab, lst in (('over', go), ('under', gu)):
        bb = collections.defaultdict(list)
        for s in lst:
            lo = min(int(s[1] * 20) * 5, 95)
            bb[f'{lo}-{lo + 5}'].append(s)
        bins[lab] = {b: sh(v) for b, v in sorted(bb.items())}
    return dict(
        n=len(rs), logloss=round(float(np.mean(ll_rows(rs, k))), 5),
        brier=round(float(np.mean([(x['po'][k] - x['over']) ** 2 for x in rs])), 5),
        all_sides=sh(sides), graded=sh(g), graded_over=sh(go), graded_under=sh(gu),
        over_share_all=round(float(np.mean([s[0] for s in sides])), 4),
        over_share_graded=round(float(np.mean([s[0] for s in g])), 4) if g else None,
        bins_graded=bins)


def cboot(rs, fn, seed=7, b=B):
    g = collections.defaultdict(list)
    for x in rs:
        g[x['match']].append(x)
    ks = list(g)
    rnd = random.Random(seed)
    vals = []
    for _ in range(b):
        s = []
        for _ in ks:
            s += g[ks[rnd.randrange(len(ks))]]
        v = fn(s)
        if v is not None:
            vals.append(v)
    vals.sort()
    return [round(vals[int(0.025 * len(vals))], 5), round(vals[int(0.975 * len(vals)) - 1], 5)]


def gap_fn(k, side=None):
    def f(s):
        z = [_side(x['po'][k], x['over']) for x in s]
        z = [v for v in z if v[1] >= GRADE and (side is None or v[0] == side)]
        if not z:
            return None
        return float(np.mean([v[1] for v in z]) - np.mean([v[2] for v in z]))
    return f


def hit_fn(k):
    def f(s):
        z = [_side(x['po'][k], x['over']) for x in s]
        z = [v[2] for v in z if v[1] >= GRADE]
        return float(np.mean(z)) if z else None
    return f


def evaluate(out, cands, base='live'):
    over_rate = float(np.mean([x['over'] for x in out]))
    res = {'_meta': dict(rows=len(out), matches=len({x['match'] for x in out}),
                         days=[min(x['day'] for x in out), max(x['day'] for x in out)],
                         realized_over_rate=round(over_rate, 4))}
    sb = summary(out, base)
    res[base] = sb
    res[base]['gap_ci'] = cboot(out, gap_fn(base))
    res[base]['graded_hit_ci'] = cboot(out, hit_fn(base))
    for k in cands:
        s = summary(out, k)
        dll = cboot(out, lambda z: float(np.mean(ll_rows(z, k)) - np.mean(ll_rows(z, base))))
        dbr = cboot(out, lambda z: float(np.mean([(x['po'][k] - x['over']) ** 2 - (x['po'][base] - x['over']) ** 2 for x in z])))
        s['dlogloss'] = round(s['logloss'] - sb['logloss'], 5)
        s['dlogloss_ci'] = dll
        s['dbrier'] = round(s['brier'] - sb['brier'], 5)
        s['dbrier_ci'] = dbr
        s['gap_ci'] = cboot(out, gap_fn(k))
        s['graded_hit_ci'] = cboot(out, hit_fn(k))
        s['gap_over_ci'] = cboot(out, gap_fn(k, True))
        s['gap_under_ci'] = cboot(out, gap_fn(k, False))
        gr = s['graded']
        go, gu = s['graded_over'], s['graded_under']
        checks = dict(
            logloss_ci_below_0=dll[1] < 0,
            gap_overall_within_2_5=gr.get('n', 0) > 0 and abs(gr['gap']) <= 0.025,
            gap_over_within_2_5=go.get('n', 0) > 0 and abs(go['gap']) <= 0.025,
            gap_under_within_2_5=gu.get('n', 0) > 0 and abs(gu['gap']) <= 0.025,
            over_share_within_5_of_data=s['over_share_graded'] is not None and abs(s['over_share_graded'] - over_rate) <= 0.05,
            graded_count_ge_90pct_live=gr.get('n', 0) >= 0.9 * sb['graded']['n'],
        )
        s['checks'] = checks
        s['passes'] = all(checks.values())
        res[k] = s
    return res


def by_segment(out, cands):
    segs = {
        'kills_maps12': lambda x: x['prop'] == 'kills' and x['scope'] != '1',
        'hs_maps12': lambda x: x['prop'] == 'hs' and x['scope'] != '1',
        'map1': lambda x: x['scope'] == '1',
        'history_disagree': lambda x: x['agree'] is False,
        'live_tag_3007c59b74': lambda x: x['version'] == '3007c59b74',
    }
    rep = {}
    for name, f in segs.items():
        sub = [x for x in out if f(x)]
        if not sub:
            continue
        rep[name] = {k: {kk: summary(sub, k)[kk] for kk in ('n', 'logloss', 'graded', 'graded_over', 'graded_under')}
                     for k in ['live'] + cands}
    return rep


def mean_accuracy(out):
    """Projection accuracy of each mean vs live lam on the test rows: dMSE (match-clustered CI) and the slope of
    (actual - line) on (mean - line) (1.0 = the distance from the line is fully real)."""
    rep = {}
    for prop in ('kills', 'hs'):
        sub = [x for x in out if x['prop'] == prop and x['scope'] != '1']
        r = {}
        for m in ('lam', 'm_st', 'm_mi', 'm_mx'):
            d = np.array([x[m] - x['line'] for x in sub]); y = np.array([x['actual'] - x['line'] for x in sub])
            slope = float(d @ y / (d @ d)) if float(d @ d) > 0 else None
            mse = float(np.mean([(x['actual'] - x[m]) ** 2 for x in sub]))
            ci = cboot(sub, lambda z, m=m: float(np.mean([(x['actual'] - x[m]) ** 2 - (x['actual'] - x['lam']) ** 2 for x in z])), b=500)
            r[m] = dict(n=len(sub), mse=round(mse, 3), dmse_vs_lam_ci=ci, slope_vs_line=round(slope, 3) if slope is not None else None)
        line_mse = float(np.mean([(x['actual'] - x['line']) ** 2 for x in sub]))
        r['line'] = dict(mse=round(line_mse, 3))
        rep[prop + '_maps12'] = r
    return rep


def main():
    rows = load()
    out = walk_forward(rows)
    cands = ['live_recon', 'st-1', 'st-1+sd-1', 'mi-2', 'mx-1', 'mx-1+sd-1']
    res = evaluate(out, cands)
    res['_segments'] = by_segment(out, cands)
    res['_mean_accuracy'] = mean_accuracy(out)
    path = os.path.join(HERE, 'result_2026-11-05.json')
    with open(path, 'w') as f:
        json.dump(res, f, indent=1)
    for k in ['live'] + cands:
        s = res[k]
        print(f"{k:12s} LL={s['logloss']:.5f} d={s.get('dlogloss', 0):+.5f} {s.get('dlogloss_ci', '')} "
              f"graded n={s['graded'].get('n')} st={s['graded'].get('stated')} hit={s['graded'].get('hit')} "
              f"O {s['graded_over'].get('n')}:{s['graded_over'].get('gap')} U {s['graded_under'].get('n')}:{s['graded_under'].get('gap')} "
              f"Oshare={s['over_share_graded']} pass={s.get('passes')}")
    print('realized over rate', res['_meta'])
    return res


if __name__ == '__main__':
    main()
