"""HTTP header case must not manufacture a CORS failure behind Render's proxy."""
from email.message import Message
from scripts import load_sports_staging_check as checker


def test_response_headers_are_case_insensitive(monkeypatch):
    class Response:
        status = 200
        headers = Message()
        headers['access-control-allow-origin'] = 'https://cs2-scout.onrender.com'
        headers['Content-Type'] = 'application/json'
        def read(self): return b'{}'
        def __enter__(self): return self
        def __exit__(self, *args): pass
    monkeypatch.setattr(checker.urllib.request, 'urlopen', lambda *a, **k: Response())
    _, _, headers, _ = checker.get('https://example.test')
    assert headers['access-control-allow-origin'] == 'https://cs2-scout.onrender.com'
    assert headers['content-type'] == 'application/json'
