"""sports/jsonstream.py (soccer-board-1125): one large JSON object read in bounded memory must give exactly what
json.loads gives - at every chunk boundary - and must never hand back a partial body."""
import json
import random

import pytest

from sports import jsonstream as js

BS, QT = chr(92), chr(34)


def chunks_of(data, sizes):
    i = 0
    while i < len(data):
        n = next(sizes)
        yield data[i:i + n]
        i += n


def const(n):
    return iter(lambda: n, 0)


def rnd(rng, lo, hi):
    return (rng.randint(lo, hi) for _ in iter(int, 1))


TRICKY = "Mbapp" + chr(0xe9) + " " + chr(0x107) + " " + BS + " " + QT + " " + chr(0x1F600) + " " + chr(0x11f)
DOC = {"data": [{"id": "1", "n": 12345, "f": -1.5e-3, "t": True, "x": None, "s": TRICKY}, 7, 12, [1, [2, 3]], "a,]}", 3.25, 1e5,
                -0.0, 10],
       "meta": {"a": [1, 2, {"b": "}"}]}, "empty": [], "num": 120, "flt": 3.5, "exp": 2e-7, "included": [{"k": chr(0xe9)}],
       "z": "end", "last": 99}


@pytest.mark.parametrize("ensure_ascii", [True, False])
def test_every_chunk_size_gives_what_json_loads_gives(ensure_ascii):
    plain = json.dumps(DOC, ensure_ascii=ensure_ascii).encode("utf-8")
    for body in (plain, b"  \n" + plain + b"\n  ", json.dumps(DOC, ensure_ascii=ensure_ascii, indent=2).encode("utf-8"),
                 json.dumps(DOC, ensure_ascii=ensure_ascii, separators=(",", ":")).encode("utf-8")):
        for n in list(range(1, 41)) + [len(body)]:
            got, counts = js.read(chunks_of(body, const(n)))
            # repr: -0.0 vs 0.0, 1 vs True and key order must match too
            assert repr(got) == repr(DOC), (ensure_ascii, n)
            assert counts == {"data": [9, 9], "empty": [0, 0], "included": [1, 1]}


def test_a_number_cut_between_two_chunks_is_never_taken_short():
    # "3" + ".25", "1" + "e5", "12" + "0": the scanner alone would answer 3, 1 and 12
    for doc in ({"a": [3.25, 1e5, 120]}, {"a": 3.25, "b": 1e5, "c": 120}, {"a": [-0.5e-3]}, {"a": [1e+5, 2E-3]}):
        body = json.dumps(doc).encode()
        for n in range(1, len(body) + 1):
            assert repr(js.read(chunks_of(body, const(n)))[0]) == repr(doc), (doc, n)


def test_keep_drops_elements_and_counts_them():
    doc = {"data": [{"type": "projection", "attributes": {"odds_type": t}, "id": str(i)}
                    for i, t in enumerate(["standard", "demon", "goblin", "demon", "standard", None])],
           "included": [{"type": "new_player", "id": "9"}], "meta": {"total_pages": 1}}
    body = json.dumps(doc).encode()

    def keep(key, element):
        return not (key == "data" and element["attributes"]["odds_type"] in ("demon", "goblin"))

    for n in (1, 7, 64, len(body)):
        got, counts = js.read(chunks_of(body, const(n)), keep)
        assert [x["id"] for x in got["data"]] == ["0", "4", "5"]
        assert got["included"] == doc["included"] and got["meta"] == doc["meta"]
        assert counts == {"data": [6, 3], "included": [1, 1]}
        assert list(got) == ["data", "included", "meta"]


def test_a_cut_answer_always_raises_never_a_partial_body():
    body = json.dumps(DOC).encode()
    for cut in range(len(body)):
        for n in (1, 5, len(body)):
            with pytest.raises(ValueError):
                js.read(chunks_of(body[:cut], const(n)))


BAD = [b"", b"   ", b"[]", b"[1,2]", b'"x"', b"12", b"null", b'{"a":1}x', b'{"a":1}{"b":2}', b'{"a" 1}', b'{"a":[1 2]}',
       b'{"a":[1,]}', b"{,}", b'{"a":1,}', b"{1:2}", b'{"a":[1,2}', b'{"a":tru}', b'\xff\xfe{"a":1}',
       b'{"a":NaN1}', b'{"a":3.x}', b'{"a":[3.x]}', b'{"a":01}', b'{"a":[01,2]}', b'{"a":1e+}', b'{"a":[1e+,2]}',
       b'{"a":"b""c"}', b'{"a":[1]]}', b'{"a":1.}', b'{"a":[1.]}', b'{"a":-}', b'{"a":[1,2]', b'{"a":[1,2],', b'{"a"}',
       b'{"a":}', b'{"a":[}', b'{"a":1 "b":2}', b'{"a":[1,,2]}', b'{"a":[,1]}', b'{"a":1,,"b":2}', b'{"a":[1]"b":2}']


@pytest.mark.parametrize("bad", BAD)
def test_malformed_or_non_object_answers_raise(bad):
    for n in (1, 2, 3, 1000):
        with pytest.raises(ValueError):
            js.read(chunks_of(bad, const(n)))


GOOD = [b"{}", b" { } ", b'{"a":[]}', b'{"a":[[]]}', b'{"a":{}}', b'{"a":[1],"a":[2,3]}', b'{"a":[1],"a":5}',
        b'{"a":5,"a":[1]}', b'{"a":[1],"b":2,"a":[9]}', b'{"a":NaN}', b'{"a":[Infinity,-Infinity]}', b'{"a":1e5}',
        b'{"a":[0]}', b'{"a":0}', b'{"":[""]}', ('{"a":"' + BS + "ud83d" + BS + 'ude00"}').encode(),
        b'{"a":[true,false,null]}', b'{"a":[1.5,2e3,-3,0.0]}', b'{"a":1.5,"b":2e3}', b'{"a":[1],"a":[]}',
        b'{"a":[],"a":[1]}', b'\n{\n"a"\n:\n[\n1\n,\n2\n]\n,\n"b"\n:\n3\n}\n']


@pytest.mark.parametrize("good", GOOD)
def test_edge_documents_agree_with_json_loads(good):
    for n in (1, 2, 3, 1000):
        assert repr(js.read(chunks_of(good, const(n)))[0]) == repr(json.loads(good)), n


def test_random_documents_and_random_cuts():
    rng = random.Random(3)
    leaves = [0, 1, -1, 12, 3.25, -0.5, 1e10, 1e-7, 123456789012, True, False, None, "", "x", "a,b]}",
              chr(0xe9) + chr(0x107) + chr(0x1F600), BS + QT + BS, " "]

    def value(depth=0):
        k = rng.random()
        if depth > 3 or k < 0.35:
            return rng.choice(leaves)
        if k < 0.65:
            return [value(depth + 1) for _ in range(rng.randint(0, 4))]
        return {rng.choice(["a", "b", "data", "", chr(0xe9)]) + str(rng.randint(0, 3)): value(depth + 1)
                for _ in range(rng.randint(0, 4))}

    for _ in range(600):
        doc = {"k%d" % i: value() for i in range(rng.randint(0, 5))}
        body = json.dumps(doc, ensure_ascii=rng.random() < 0.5, indent=rng.choice([None, None, 1]),
                          separators=rng.choice([None, (",", ":")])).encode()
        got, _ = js.read(chunks_of(body, rnd(rng, 1, rng.choice([1, 2, 7, 60]))))
        assert repr(got) == repr(json.loads(body)), body
        if len(body) > 2:
            with pytest.raises(ValueError):
                js.read(chunks_of(body[:rng.randrange(len(body))], rnd(rng, 1, 9)))


def test_one_huge_broken_element_fails_instead_of_buffering_the_whole_answer(monkeypatch):
    monkeypatch.setattr(js, "MAX_ELEMENT_CHARS", 2000)
    body = b'{"data":[{"a":"' + b"x" * 50_000        # a string that never closes
    seen = []

    def chunks():
        for i in range(0, len(body), 500):
            seen.append(i)
            yield body[i:i + 500]

    with pytest.raises(ValueError, match="too large"):
        js.read(chunks())
    assert len(seen) < 30                              # it stopped reading long before the end of the input


def test_memory_holds_one_chunk_not_the_body():
    """Structure, not a byte threshold: while a 20,000-element array streams by with every element refused, the text
    still held never grows past a couple of chunks."""
    element = json.dumps({"type": "projection", "id": "1", "attributes": {"odds_type": "demon", "pad": "p" * 900}})
    n_elements, chunk = 20_000, 4096
    held = []

    def chunks():
        yield b'{"data":['
        buf = (element + ",").encode() * 50
        for _ in range(n_elements // 50 - 1):
            for i in range(0, len(buf), chunk):
                yield buf[i:i + chunk]
        yield (element + "]").encode() + b',"included":[]}'

    real = js._Text.more

    def more(self):
        held.append(len(self.s) - self.i)
        return real(self)

    js._Text.more = more
    try:
        got, counts = js.read(chunks(), lambda key, el: False)
    finally:
        js._Text.more = real
    assert got == {"data": [], "included": []}
    assert counts["data"][0] == n_elements - 49 and counts["data"][1] == 0
    assert max(held) < 4 * chunk + len(element)
