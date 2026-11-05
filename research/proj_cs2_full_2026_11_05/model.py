"""pd-1: full-history CS2 projection DISTRIBUTION (research candidate; never imported by the live grader).

Per (stat, scope): a LightGBM Poisson model for the MEAN of the total (Map 1 / Maps 1-2 / Maps 1-3 kills or headshots) on
every pre-match feature in features.py, and a LightGBM gamma model for the VARIANCE RATIO v = Var / mean, fitted on
OUT-OF-TIME residuals (the mean model refitted without the last VAR_DAYS days predicts those days; v learns from them), so
the spread is not the shrunken in-sample one. Predictive pmf = negative binomial (mean mu, variance v mu) or the live
family (discretised normal, sd = sqrt(v mu)) - chosen per (stat, scope) on the development walk-forward only.

No chance shrink layer, no line input, no side intercept: the chance at a line is read off this pmf."""
from __future__ import annotations

import lightgbm as lgb
import numpy as np
import pandas as pd

import dist

TARGET = {("kills", "1"): "k1", ("kills", "12"): "k12", ("kills", "123"): "k123",
          ("hs", "1"): "hs1", ("hs", "12"): "hs12", ("hs", "123"): "hs123"}
EXCLUDE = {"matchid", "playerid", "teamid", "oppid", "day", "date", "lan", "event", "team", "opponent", "maps_played",
           "k1", "k12", "k123", "hs1", "hs12", "hs123", "index"}
TRAIN_FROM = pd.Timestamp("2024-04-01").toordinal()     # first 12 weeks = history warm-up only
VAR_DAYS = 90
MEAN_PARAMS = dict(objective="poisson", learning_rate=0.05, num_leaves=31, min_data_in_leaf=300, feature_fraction=0.8,
                   bagging_fraction=0.8, bagging_freq=1, lambda_l2=10.0, verbose=-1, num_threads=16, seed=7)
MEAN_ROUNDS = 400
VAR_PARAMS = dict(objective="gamma", learning_rate=0.05, num_leaves=15, min_data_in_leaf=800, feature_fraction=0.8,
                  lambda_l2=10.0, verbose=-1, num_threads=16, seed=7)
VAR_ROUNDS = 150


def feature_cols(df):
    return [c for c in df.columns if c not in EXCLUDE and not c.startswith(("live_", "full_", "cm2_"))
            and pd.api.types.is_numeric_dtype(df[c])]


def _fit_mean(X, y):
    return lgb.train(MEAN_PARAMS, lgb.Dataset(X, y, free_raw_data=True), MEAN_ROUNDS)


def fit(train, stat, scope, cols=None):
    """train: rows with day < cutoff (the caller enforces the cutoff). Returns the fitted model dict."""
    tcol = TARGET[(stat, scope)]
    d = train[train[tcol].notna() & (train.day >= TRAIN_FROM)]
    cols = cols or feature_cols(d)
    X = d[cols].to_numpy(np.float32)
    y = d[tcol].to_numpy(float)
    mean = _fit_mean(X, y)
    cut = d.day.max() - VAR_DAYS
    early = d.day.to_numpy() <= cut
    m_early = _fit_mean(X[early], y[early])
    mu_late = np.maximum(m_early.predict(X[~early]), 0.3)
    z = np.maximum((y[~early] - mu_late) ** 2 / mu_late, 1e-3)
    Xv = np.column_stack([X[~early], np.log(mu_late)])
    var = lgb.train(VAR_PARAMS, lgb.Dataset(Xv, z), VAR_ROUNDS)
    return {"stat": stat, "scope": scope, "cols": cols, "mean": mean, "var": var, "n_train": int(len(d)),
            "n_var": int((~early).sum())}


def predict(M, df):
    X = df[M["cols"]].to_numpy(np.float32)
    mu = np.maximum(M["mean"].predict(X), 0.3)
    v = M["var"].predict(np.column_stack([X, np.log(mu)]))
    return mu, np.clip(v, 1.05, 8.0)


def pmf(mu, v, scope, family="negbin"):
    ymax = dist.YMAX[scope]
    if family == "negbin":
        return dist.negbin_pmf(mu, v, ymax)
    return dist.norm_pmf(mu, np.sqrt(v * mu), ymax)
