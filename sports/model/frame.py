"""A minimal column frame on numpy (production images carry numpy, not pandas / scipy - test_sports_isolation).

Frame: dict of equal-length numpy arrays + attrs. Grouped helpers work on frames SORTED by (group key, time):
  group_starts(keys)        index of each row's group start
  gshift(x, gs, k)          value k rows earlier in the same group (NaN at the start)
  ew_prior(x, gs, hl)       exponentially weighted mean of the group's PRIOR rows (NaN values skipped) and its weight sum
  roll_prior(x, gs, n, mp)  mean / sum of the last n prior rows (min_periods mp)
  lookup(keys_left, keys_right) -> index into the right table (-1 = no match)
"""
from __future__ import annotations


import numpy as np


class Frame:
    def __init__(self, cols=None, attrs=None):
        self.cols = dict(cols or {})
        self.attrs = dict(attrs or {})

    @classmethod
    def from_sql(cls, con, sql, args=()):
        cur = con.execute(sql, args)
        names = [d[0] for d in cur.description]
        rows = cur.fetchall()
        cols = {}
        for i, n in enumerate(names):
            vals = [r[i] for r in rows]
            if all(v is None or isinstance(v, (int, float)) for v in vals):
                cols[n] = np.array([np.nan if v is None else float(v) for v in vals], dtype=float)
            else:
                cols[n] = np.array(["" if v is None else str(v) for v in vals], dtype=object)
        return cls(cols)

    def __len__(self):
        return len(next(iter(self.cols.values()))) if self.cols else 0

    def __contains__(self, k):
        return k in self.cols

    def __getitem__(self, k):
        if isinstance(k, str):
            return self.cols[k]
        idx = np.asarray(k)
        return Frame({c: v[idx] for c, v in self.cols.items()}, self._sub_attrs(idx))

    def _sub_attrs(self, idx):
        a = dict(self.attrs)
        if "lags" in a:
            a["lags"] = {m: v[idx] for m, v in a["lags"].items()}
        return a

    def __setitem__(self, k, v):
        v = np.asarray(v)
        if v.ndim == 0:
            v = np.full(len(self), v.item())
        self.cols[k] = v

    def copy(self):
        return Frame({c: v.copy() for c, v in self.cols.items()}, dict(self.attrs))

    def f(self, k):
        """float view of a column."""
        return np.asarray(self.cols[k], dtype=float)

    def sort(self, *keys):
        idx = np.lexsort(tuple(np.asarray(self.cols[k]) for k in reversed(keys)))
        return self[idx]


def group_starts(keys):
    keys = np.asarray(keys)
    n = len(keys)
    if n == 0:
        return np.zeros(0, int)
    new = np.ones(n, bool)
    new[1:] = keys[1:] != keys[:-1]
    starts = np.flatnonzero(new)
    return starts[np.cumsum(new) - 1]


def gshift(x, gs, k=1):
    x = np.asarray(x, float)
    i = np.arange(len(x))
    out = np.full(len(x), np.nan)
    ok = i - k >= gs
    out[ok] = x[i[ok] - k]
    return out


def lag_matrix(x, gs, k):
    return np.column_stack([gshift(x, gs, j + 1) for j in range(k)]) if k else np.zeros((len(x), 0))


def ew_prior(x, gs, hl):
    """EW mean over the group's prior rows (newest weight 1, decay 0.5 per hl rows; NaN rows skipped but still age the
    weights - pandas ewm(halflife, adjust=True, ignore_na=False) semantics) -> (mean, weight sum of the non-NaN
    prior rows)."""
    x = np.asarray(x, float)
    lam = 0.5 ** (1.0 / hl)
    n = len(x)
    mean = np.full(n, np.nan)
    wsum = np.zeros(n)
    s = w = 0.0
    for i in range(n):
        if i == gs[i]:
            s = w = 0.0
        mean[i] = s / w if w > 0 else np.nan
        wsum[i] = w
        s *= lam
        w *= lam
        if x[i] == x[i]:
            s += x[i]
            w += 1.0
    return mean, wsum


def roll_prior(x, gs, n, min_periods=1, how="mean"):
    """mean (or sum) of up to n prior rows of the group (NaN skipped), NaN when fewer than min_periods values."""
    x = np.asarray(x, float)
    ok = np.isfinite(x)
    cs = np.concatenate([[0.0], np.cumsum(np.where(ok, x, 0.0))])
    cc = np.concatenate([[0], np.cumsum(ok)])
    i = np.arange(len(x))
    lo = np.maximum(gs, i - n)
    s = cs[i] - cs[lo]
    c = cc[i] - cc[lo]
    out = np.where(c >= min_periods, s / np.maximum(c, 1) if how == "mean" else s, np.nan)
    return out


def key_of(*cols):
    return np.array(["|".join(str(c[i]) for c in cols) for i in range(len(cols[0]))], dtype=object) \
        if len(cols[0]) else np.zeros(0, dtype=object)


def lookup(left_keys, right_keys):
    pos = {k: i for i, k in enumerate(right_keys)}
    return np.array([pos.get(k, -1) for k in left_keys], dtype=int)


def take(x, idx, fill=np.nan):
    x = np.asarray(x)
    out = np.full(len(idx), fill, dtype=float if x.dtype != object else object)
    ok = idx >= 0
    out[ok] = x[idx[ok]]
    return out


def group_sum(keys, *vals):
    """keys -> (unique keys, sums per value array)."""
    u, inv = np.unique(np.asarray(keys), return_inverse=True)
    return u, [np.bincount(inv, weights=np.nan_to_num(np.asarray(v, float)), minlength=len(u)) for v in vals]
