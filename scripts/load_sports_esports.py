"""The esports server alone in its own process, with the same fixture upstreams as scripts/load_sports700.py (no
network, fixture boards, Whop sessions faked as cookie "good-<member>"). Used by scripts/load_sports.py.
usage: python load_sports_esports.py <code_dir> <data_dir>   -> prints "PORT <n>" then serves until killed.
The sports flags / SPORTS_API_MODE / SPORTS_TOKEN_SECRET / SPORTS_UPSTREAM come from the environment."""
import os
import sys
import threading
from http.server import ThreadingHTTPServer
from pathlib import Path

code, data = sys.argv[1], sys.argv[2]
sys.path.insert(0, code)
os.chdir(code)
os.environ["CS2_DATA_DIR"] = data

import hltv_store  # noqa: E402
hltv_store.DB_PATH = Path(data) / "hltv_store.db"
import kalshi, moneyline, polymarket_ml, server, slip_builder, underdog, whop_auth  # noqa: E402,E401
from tests.test_api_moneyline_slips import Boards  # noqa: E402
from tests.test_moneyline import kalshi_raw, poly_raw  # noqa: E402

whop_auth.enabled = lambda: True
whop_auth.session = lambda v: {"user": v[5:], "name": "", "ok": True} if v and v.startswith("good-") else None
slip_builder.DB_PATH = Path(data) / "slip_studio.db"
b = Boards(n_matches=30)
server.board_payload = b.payload
underdog.board = b.feed
srcs = {"polymarket": moneyline.Source("polymarket", poly_raw, polymarket_ml.normalize, canonical_fn=moneyline._fallback_canonical),
        "kalshi": moneyline.Source("kalshi", kalshi_raw, kalshi.normalize, canonical_fn=moneyline._fallback_canonical)}
for s in srcs.values():
    s.refresh_once()
moneyline.SOURCES = srcs
ThreadingHTTPServer.request_queue_size = 2048          # before bind/listen (as server.py's __main__ does)
ThreadingHTTPServer.daemon_threads = True
httpd = ThreadingHTTPServer(("127.0.0.1", 0), server.Handler)
print(f"PORT {httpd.server_address[1]}", flush=True)
httpd.serve_forever()
