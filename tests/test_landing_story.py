"""Default landing: isolated saved demos, live proof, membership untouched.

Prior layout tests deliberately exercise SCOUT_LANDING_STORY=0 for rollback.
This suite covers the new default, including the actual HTTP/cache path.
"""
import gzip
import json
import re
import threading
from concurrent.futures import ThreadPoolExecutor
from html.parser import HTMLParser
from http.server import ThreadingHTTPServer

import pytest
import landing_v3
import server
from tests.test_landing_v3 import srv, seed, board, JOIN, WEEK, get, wait_builder, text, review


@pytest.fixture
def story(srv, monkeypatch):
    monkeypatch.setenv("SCOUT_LANDING_STORY", "1")
    seed(srv, monkeypatch, board())
    landing_v3.reset()
    yield srv
    wait_builder()
    landing_v3.reset()


class Structure(HTMLParser):
    def __init__(self):
        super().__init__(); self.ids=[]; self.links=[]; self.images=[]; self.scripts=[]
    def handle_starttag(self, tag, attrs):
        a=dict(attrs)
        if a.get('id'): self.ids.append(a['id'])
        if tag=='a': self.links.append(a.get('href',''))
        if tag=='img': self.images.append(a)
        if tag=='script' and a.get('src'): self.scripts.append(a['src'])


def render(s):
    return landing_v3.build(s, JOIN, fetch=False).decode()


def test_story_is_default_and_can_rollback(monkeypatch):
    monkeypatch.delenv('SCOUT_LANDING_STORY',raising=False)
    assert landing_v3.story_enabled()
    assert 'hero-layout' in landing_v3._template()
    for value in ('0','false','off'):
        monkeypatch.setenv('SCOUT_LANDING_STORY',value)
        assert not landing_v3.story_enabled()
        assert 'hero-layout' not in landing_v3._template()


def test_story_structure_and_checkout_are_complete(story):
    s=render(story); doc=Structure(); doc.feed(s)
    assert len(doc.ids)==len(set(doc.ids))
    assert not doc.images and not doc.scripts
    assert all(h[1:] in doc.ids for h in doc.links if h.startswith('#'))
    assert 'Every player prop.' in s and 'One board.' in s
    assert JOIN.replace('&','&amp;') in s and WEEK.replace('&','&amp;') in s
    assert '{{' not in s
    assert 'hero-grade-body' in doc.ids and 'demo-phone' in doc.ids
    assert 'feature-tab-2' in doc.ids and 'review-next' in doc.ids
    assert 'signin-error' in doc.ids


def test_proof_counts_and_reviews_are_from_shared_answers(story):
    s=render(story)
    assert '1,010 decided picks' in s  # wins + losses only; pushes stay separate
    assert '12 reviews, all published' in s and '4.8' in s
    assert '13,090' not in s and '1,439' not in s
    assert '<td class="w">300</td>' in s and '<td class="p">3</td>' in s


def test_saved_demo_never_reads_member_data_from_browser(story):
    s=render(story)
    scripts=''.join(re.findall(r'<script>(.*?)</script>',s,re.S))
    assert not re.search(r'\bfetch\s*\(|XMLHttpRequest|WebSocket',scripts)
    assert 'Saved example' in s and 'not current picks' in s
    assert 'No live recheck' in s
    assert 'localStorage' not in scripts
    assert 'Build demo slips' in s


def test_missing_proof_is_not_invented(story,monkeypatch):
    monkeypatch.setattr(landing_v3,'_inputs',lambda *a,**k:{'board':None,'record':{},'reviews':{}})
    s=render(story)
    assert 'Every player prop.' in s
    assert '13,090' not in s and '5.0 · 15' not in s
    assert '<table class="rec">' not in s
    assert '<ul class="review-track"></ul>' in s


def test_proof_escapes_untrusted_review_text(story,monkeypatch):
    doc={'summary':{'count':12,'average':4.5},'reviews':[review(91, 'reader', 'The cards explain each line clearly. <img src=x onerror=alert(1)> The stats help me research the board.')]}
    # Independently check the presentation cannot expose markup from the renderer.
    _,cards,_,_=landing_v3.wall(doc)
    assert cards and '<img src=x' not in cards
    assert '&lt;img' in cards
    s=render(story)
    assert 'ADMIN_TOKEN' not in s and 'SPORTS_TOKEN_SECRET' not in s


def test_700_story_visitors_share_cached_bytes_and_etag(story,monkeypatch):
    monkeypatch.setattr(landing_v3,'_kick',lambda *a:None)
    first=landing_v3.page(story,JOIN)
    monkeypatch.setattr(landing_v3,'build',lambda *a,**k:pytest.fail('rebuild on request'))
    httpd=ThreadingHTTPServer(('127.0.0.1',0),server.Handler)
    httpd.daemon_threads=True
    threading.Thread(target=httpd.serve_forever,daemon=True).start()
    try:
        with ThreadPoolExecutor(max_workers=48) as pool:
            replies=list(pool.map(lambda _:get(httpd.server_port,'/',{'Accept-Encoding':'gzip'}),range(700)))
        assert all(st==200 for st,_,_ in replies)
        assert len({h.get('etag') for _,h,_ in replies})==1
        for _,headers,body in replies:
            assert (gzip.decompress(body) if headers.get('content-encoding')=='gzip' else body)==first
        etag=replies[0][1]['etag']
        assert get(httpd.server_port,'/',{'If-None-Match':etag})[0]==304
    finally:httpd.shutdown()


def test_motion_and_empty_carousel_safeguards_are_present():
    js=landing_v3.STORY_ASSETS['STORY_JS'].read_text(encoding='utf-8')
    css=landing_v3.STORY_ASSETS['STORY_CSS'].read_text(encoding='utf-8')
    assert 'prefers-reduced-motion' in css and 'prefers-reduced-motion' in js
    assert 'document.hidden' in js and 'IntersectionObserver' in js
    assert 'if (!originals.length) return' in js
    assert 'copy.inert = true' in js
