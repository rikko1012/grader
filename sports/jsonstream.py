"""One large JSON object read in bounded memory (soccer-board-1125).

PrizePicks' soccer tab answered 18.7 MB / 15,491 projections on 2026-11-25 (it was ~3,000 the day before): parsed whole
it needs ~160-195 MB and the capped quotes job was killed on every read. A JSON:API answer is one object whose big members
are arrays of small objects ("data", "included"), so it is read member by member, array element by element, from the
byte chunks as they arrive: memory is one chunk plus the elements the caller keeps - never the body, its text or its
whole tree.

    body, counts = read(chunks, keep)      # keep(key, element) -> bool, asked for every element of a top-level array

`body` is what json.loads would give with the refused array elements left out (other members whole; a repeated key:
the last one wins, as in json.loads). A cut, malformed or non-object answer raises ValueError - never a partial body.
"""
import codecs
import json

MAX_ELEMENT_CHARS = 4_000_000   # one array element / one non-array member; a larger one fails the read (ValueError)
_WS = " \t\n\r"
_AFTER = " \t\n\r,]}:"       # what may follow a complete value (":" after a key)
_decode = json.JSONDecoder().raw_decode


class _Text:
    """The undecoded tail of the input as text: `s[i:]` is what is left of the chunks read so far."""

    def __init__(self, chunks):
        self._chunks, self._utf8 = iter(chunks), codecs.getincrementaldecoder("utf-8")(errors="replace")
        self.s, self.i, self.eof = "", 0, False

    def more(self):
        """Append the next non-empty chunk -> True; False once the input has ended."""
        while not self.eof:
            try:
                text = self._utf8.decode(next(self._chunks))
            except StopIteration:
                self.eof = True
                text = self._utf8.decode(b"", final=True)     # a cut multi-byte character raises here
            except UnicodeDecodeError as e:
                raise ValueError("bad json: not utf-8") from e
            if text:
                self.s, self.i = self.s[self.i:] + text, 0
                return True
        return False

    def peek(self):
        """The next non-blank character ('' at the end of the input), not consumed."""
        while True:
            s, i, n = self.s, self.i, len(self.s)
            while i < n and s[i] in _WS:
                i += 1
            self.i = i
            if i < n:
                return s[i]
            if not self.more():
                return ""

    def take(self, char):
        if self.peek() != char:
            raise ValueError(f"bad json: expected {char!r}")
        self.i += 1

    def value(self):
        """The JSON value at the cursor. It is taken only when what follows it is known: a delimiter, or the real
        end of the input. A number at the end of what was read so far may continue in the next chunk ("3" + ".25",
        "1" + "e5"), so the scanner's own answer is not enough there."""
        if not self.peek():
            raise ValueError("bad json: cut")
        tried = 0
        while True:
            s, i = self.s, self.i
            try:
                obj, end = _decode(s, i)
            except ValueError:
                end = None
            if end is not None:
                rest = len(s) - end
                if (rest and s[end] in _AFTER) or (not rest and self.eof):
                    self.i = end
                    return obj
                if rest > 2 or self.eof:                  # "1e+" is the longest tail a cut number can leave
                    raise ValueError("bad json: malformed value")
            elif self.eof:
                raise ValueError("bad json: cut or malformed")
            tried = len(s) - i
            if tried > MAX_ELEMENT_CHARS:
                raise ValueError("bad json: element too large")
            # read on until the text has doubled (or ended): a large element is re-scanned a few times, not per chunk
            while self.more() and len(self.s) - self.i < 2 * tried:
                pass


def members(chunks):
    """-> (key, in_array, value) for every member of the top-level object, in order. An array member gives
    (key, True, START) first and then one item per element; any other member gives one (key, False, value)."""
    t = _Text(chunks)
    t.take("{")
    if t.peek() == "}":
        t.i += 1
    else:
        while True:
            key = t.value()
            if not isinstance(key, str):
                raise ValueError("bad json: key")
            t.take(":")
            if t.peek() == "[":
                t.i += 1
                yield key, True, START
                if t.peek() == "]":
                    t.i += 1
                else:
                    while True:
                        yield key, True, t.value()
                        c = t.peek()
                        if c == ",":
                            t.i += 1
                        elif c == "]":
                            t.i += 1
                            break
                        else:
                            raise ValueError("bad json: array")
            else:
                yield key, False, t.value()
            c = t.peek()
            if c == ",":
                t.i += 1
            elif c == "}":
                t.i += 1
                break
            else:
                raise ValueError("bad json: object")
    if t.peek():
        raise ValueError("bad json: trailing data")


START = object()     # members(): an array member begins (its elements follow)


def read(chunks, keep=None):
    """-> (body, counts): the top-level object with only the array elements `keep(key, element)` accepts (all of them
    without `keep`); counts = {array member: [elements seen, elements kept]}."""
    body, counts = {}, {}
    for key, in_array, value in members(chunks):
        if not in_array:
            body[key] = value
            counts.pop(key, None)
        elif value is START:                             # a repeated key starts again: the last one wins
            body[key], counts[key] = [], [0, 0]
        else:
            counts[key][0] += 1
            if keep is None or keep(key, value):
                counts[key][1] += 1
                body[key].append(value)
    return body, counts
