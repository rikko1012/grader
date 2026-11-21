"""The member gate (whop_auth): new members get in shortly after paying without signing in again; only the member's
own memberships count; statuses; a plan we don't list still works through Whop's paid-experience check; a Whop outage
never locks anyone out. Whop is mocked - no network."""
import os, sys, time
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import whop_auth as w

WEEKLY, LIFETIME = "plan_4zOzRl7roBDFf", w.LIFETIME_PLAN


class Resp:
    def __init__(self, code, data):
        self.status_code, self._d, self.text = code, data, ""
    def json(self):
        return self._d


def _whop(monkeypatch, memberships=None, access=False, fail=False):
    """memberships: list of rows Whop returns (NOT filtered by user - like an API ignoring the filter)."""
    state = {"memberships": memberships or [], "access": access, "fail": fail}
    def get(url, params=None, headers=None, timeout=None):
        if state["fail"]:
            return Resp(503, {"error": "down"})
        if url == w.MEMBERSHIPS:
            return Resp(200, {"data": state["memberships"]})
        return Resp(200, {"has_access": state["access"], "access_level": "customer" if state["access"] else "no_access"})
    monkeypatch.setattr(w.requests, "get", get)
    monkeypatch.setenv("WHOP_CLIENT_ID", "app_x"); monkeypatch.setenv("WHOP_API_KEY", "k"); monkeypatch.setenv("SESSION_SECRET", "s")
    w._ACCESS_CACHE.clear()
    return state


def test_statuses(monkeypatch):
    for status, plan, ok in (("active", WEEKLY, True), ("trialing", WEEKLY, True), ("canceling", WEEKLY, True),
                             ("past_due", WEEKLY, False), ("expired", WEEKLY, False), ("completed", LIFETIME, True),
                             ("completed", WEEKLY, False), ("active", "plan_free_thing", False)):
        _whop(monkeypatch, [{"user_id": "user_a", "plan_id": plan, "status": status}])
        assert w.has_access("user_a", force=True) is ok, (status, plan)


def test_only_the_members_own_rows_count(monkeypatch):
    # Whop ignoring the filter and returning someone else's live membership must not let user_b in
    _whop(monkeypatch, [{"user_id": "user_a", "plan_id": WEEKLY, "status": "active"}])
    assert w.has_access("user_b", force=True) is False and w.has_access("user_a", force=True) is True
    _whop(monkeypatch, [{"user": {"id": "user_c"}, "plan_id": WEEKLY, "status": "active"}])        # nested shape
    assert w.has_access("user_c", force=True) is True


def test_new_member_gets_in_after_paying_without_signing_in_again(monkeypatch):
    st = _whop(monkeypatch, [])
    now = int(time.time())
    cookie = w._sign({"u": "user_new", "n": "new", "exp": now + 7 * 86400, "chk": now, "ok": False})   # signed in before paying
    assert w.session(cookie)["ok"] is False
    st["memberships"] = [{"user_id": "user_new", "plan_id": WEEKLY, "status": "active"}]              # pays on Whop
    assert w.session(cookie)["ok"] is False                                                          # cached "no" (1 min)
    t, ok = w._ACCESS_CACHE["user_new"]
    w._ACCESS_CACHE["user_new"] = (t - 61, ok)                                                       # a minute later
    assert w.session(cookie)["ok"] is True                                                           # in - same cookie


def test_unlisted_plan_still_gets_in_through_the_paid_experience(monkeypatch):
    _whop(monkeypatch, [{"user_id": "user_p", "plan_id": "plan_brand_new_promo", "status": "active"}], access=True)
    assert w.has_access("user_p", force=True) is True


def test_whop_outage_never_locks_anyone_out(monkeypatch):
    st = _whop(monkeypatch, [{"user_id": "user_a", "plan_id": WEEKLY, "status": "active"}])
    assert w.has_access("user_a", force=True) is True
    st["fail"] = True
    assert w.has_access("user_a", force=True) is True                     # keeps what it knew
    w._ACCESS_CACHE.clear()
    assert w.has_access("user_z", force=True, last_known=True) is True    # a signed-in member with no cache: last known
    assert w.has_access("user_y", force=True, last_known=False) is False


def test_cookie_tampering_is_refused(monkeypatch):
    _whop(monkeypatch, [])
    now = int(time.time())
    c = w._sign({"u": "user_x", "exp": now + 100, "chk": now, "ok": True})
    raw, mac = c.rsplit(".", 1)
    assert w.session(raw + "." + ("0" * len(mac))) is None and w.session("garbage") is None
    assert w.session(w._sign({"u": "user_x", "exp": now - 1, "chk": now, "ok": True})) is None     # expired session


def test_free_trial_and_every_plays_plan_get_access_1120(monkeypatch):
    """Owner 2026-11-20: free-trial members (and every other pricing option on the Plays product) get the grader."""
    import whop_auth as wa
    rows = lambda *ms: [dict(user_id="u1", **m) for m in ms]
    ok = wa._row_paid
    assert ok(dict(plan_id="plan_trial_1day", product_id="prod_lrOIT837iGaIE", status="trialing"))
    assert ok(dict(plan_id="plan_25_month", product_id="prod_lrOIT837iGaIE", status="active"))
    assert ok(dict(plan_id="plan_x", product={"id": "prod_lrOIT837iGaIE"}, status="canceling"))
    assert not ok(dict(plan_id="plan_trial_1day", product_id="prod_lrOIT837iGaIE", status="expired"))
    assert not ok(dict(plan_id="plan_free", product_id="prod_FREEACCESS", status="active"))      # the free product never
    assert ok(dict(plan_id=wa.LIFETIME_PLAN, product_id="prod_lrOIT837iGaIE", status="completed"))
    assert ok(dict(plan_id="plan_8d2GVuU7671mD", status="active"))                               # listed plan, no product field
    monkeypatch.setenv("WHOP_PAID_PRODUCT_IDS", "prod_extra")
    assert ok(dict(plan_id="p", product_id="prod_extra", status="active"))

    class R:
        status_code = 200
        def __init__(self, d): self._d = d
        def json(self): return {"data": self._d}
    monkeypatch.setattr(wa.requests, "get", lambda *a, **k: R(rows(dict(plan_id="plan_trial", product_id="prod_lrOIT837iGaIE", status="trialing"))))
    assert wa.plan_access("u1") is True
    monkeypatch.setattr(wa.requests, "get", lambda *a, **k: R([dict(user_id="someone_else", plan_id="plan_trial", product_id="prod_lrOIT837iGaIE", status="trialing")]))
    assert wa.plan_access("u1") is False                                                         # only this member's rows
