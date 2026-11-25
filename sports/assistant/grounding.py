"""Number grounding: the model may only state numbers that appear in tool results (owner brief 11).

Index(results) collects every numeric field of every tool result with the id of the record it belongs to (the nearest
dict carrying record_id / agg_id / forecast_id), every citable id, and the dates / clock times the results contain.
check(text) then reads an answer piece and

  - keeps a number only if it equals a tool value at the precision written (21.5 matches 21.48 written as "21.5"),
    or a probability written as a percent (0.613 -> "61.3%"), or a number the member typed in the question, or a date /
    time present in the results. Everything else is replaced by REMOVED and reported in flags.
  - keeps a citation [ref:<id>] only if <id> is in the results (a forged / unknown id is removed and flagged)
  - appends a citation to a sentence whose numbers came from records it does not already cite
Identifiers (L10, Q1, record ids, fc-..., 3-pointers) are not numbers. Grounding cannot judge whether a true number is
used sensibly - that is the prompt's job; it guarantees no number appears that Grader's data does not contain.
"""
from __future__ import annotations

import re
from datetime import timedelta

from ..contracts import parse_utc
from ..identity import eastern_offset_hours
from .dates import MONTH_WORD          # real month words only (bug sweep 2026-11-06 #3: "declined 4" was read as a date)

REMOVED = "[number removed]"
ID_KEYS = ("forecast_id", "agg_id", "record_id")
_CITE = re.compile(r"\[ref:\s*([^\]\s]{1,160})\s*\]")
_ISO_DATE = re.compile(r"\b(\d{4})-(\d{2})-(\d{2})(?:T\d{2}:\d{2}(?::\d{2})?Z?)?\b")
_MONTHS = ("jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec")
_MDAY = re.compile(r"\b(" + MONTH_WORD + r")\.?\s+(\d{1,2})(?:st|nd|rd|th)?(?:,?\s+(\d{4}))?\b", re.I)
_CLOCK = re.compile(r"\b(\d{1,2}):(\d{2})\s*(am|pm|a\.m\.|p\.m\.)?(?:\s*(ET|EDT|EST|UTC|Z))?", re.I)
_IDS = re.compile(r"\b(?:fc|agg|cg)-[0-9a-f]{6,}\b|\b[a-z]+:[A-Za-z0-9_:.|\-]+\b|\b\w*[A-Za-z_]\d[\w]*\b")
_VOCAB = re.compile(r"\b(?:3|three)[- ]?(?:pointers?|point(?:ers)?|pt|pm|s)\b|\b3PT\b|\b[1-4](?:st|nd|rd|th)\s+"
                    r"(?:quarter|half)\b|\bpts\+", re.I)
_NUM = re.compile(r"(?<![\w.,])([-+−])?(\d{1,3}(?:,\d{3})+|\d+)(\.\d+)?(\s?%|\s?percent\b)?")


def _walk(obj, ref, out, ids):
    if isinstance(obj, dict):
        here = next((obj[k] for k in ID_KEYS if isinstance(obj.get(k), str)), None) or ref
        for k in ID_KEYS:
            if isinstance(obj.get(k), str):
                ids.add(obj[k])
        for k in ("source_record_ids", "offer_snapshot_ids"):
            for x in obj.get(k) or ():
                if isinstance(x, str):
                    ids.add(x)
        for k, v in obj.items():
            _walk(v, here, out, ids)
    elif isinstance(obj, (list, tuple)):
        for v in obj:
            _walk(v, ref, out, ids)
    elif isinstance(obj, bool):
        return
    elif isinstance(obj, (int, float)):
        out.append((float(obj), ref))
    elif isinstance(obj, str):
        out.append((obj, ref))


class Index:
    def __init__(self, question_numbers=()):
        self.values = []                 # (float, ref)
        self.ids = set()
        self.dates = set()               # ISO dates (UTC and US Eastern) present in the results
        self.clocks = set()              # "H:MM" (12h and 24h, UTC and Eastern) of every timestamp
        self.free = {float(x) for x in question_numbers}

    def add(self, result: dict):
        found = []
        _walk(result, None, found, self.ids)
        for v, ref in found:
            if isinstance(v, float):
                self.values.append((v, ref))
            else:
                self._strings(v)
        return self

    def _strings(self, s):
        for m in _ISO_DATE.finditer(s):
            self.dates.add(m.group(0)[:10])
        m = re.fullmatch(r"(\d{4}-\d{2}-\d{2})T(\d{2}):(\d{2}):\d{2}(?:\.\d+)?Z", s)
        if m:
            t = parse_utc(s)
            et = t + timedelta(hours=eastern_offset_hours(t))
            for x in (t, et):
                self.dates.add(x.date().isoformat())
                h = x.hour % 12 or 12
                self.clocks |= {f"{x.hour}:{x.minute:02d}", f"{h}:{x.minute:02d}"}

    # ------------------------------------------------------------------------------------------------ matching
    def match(self, value: float, decimals: int, percent: bool):
        """-> ref id (or "" for an allowed number with no record, e.g. typed in the question) / None = unsupported."""
        tol = 0.5 * 10 ** (-decimals) + 1e-9
        if not percent and value in self.free:
            return ""
        for v, ref in self.values:
            if percent:
                if 0.0 <= v <= 1.0 and abs(round(v * 100, decimals) - value) < 1e-9:
                    return ref or ""
                continue
            if abs(round(v, decimals) - value) < 1e-9 or abs(round(abs(v), decimals) - value) < 1e-9:
                return ref or ""
            if decimals == 0 and abs(v - value) < tol and float(v).is_integer():
                return ref or ""
        return None


def question_numbers(q: str):
    return [float(m.group(2).replace(",", "") + (m.group(3) or "")) for m in _NUM.finditer(q or "")]


def _protect(text, pattern, spans):
    for m in pattern.finditer(text):
        spans.append((m.start(), m.end()))


def check(text: str, idx: Index):
    """-> (checked_text, flags, refs_used). Pure: the same text + index always give the same output."""
    flags, used, repl = [], [], []                      # repl: (start, end, replacement)
    for m in _CITE.finditer(text):
        rid = m.group(1)
        if rid in idx.ids:
            used.append(rid)
        else:
            repl.append((m.start(), m.end(), ""))
            flags.append({"kind": "unknown_citation", "value": rid[:60]})
    taken = [(a, b) for a, b, _ in repl] + [(m.start(), m.end()) for m in _CITE.finditer(text)]
    for m in _ISO_DATE.finditer(text):
        taken.append((m.start(), m.end()))
        if m.group(0)[:10] not in idx.dates:
            repl.append((m.start(), m.end(), REMOVED))
            flags.append({"kind": "unsupported_date", "value": m.group(0)})
    for m in _MDAY.finditer(text):
        if any(a <= m.start() < b for a, b in taken):
            continue
        taken.append((m.start(), m.end()))
        mon, day = _MONTHS.index(m.group(1).lower()[:3]) + 1, int(m.group(2))
        ok = any(d[5:] == f"{mon:02d}-{day:02d}" and (not m.group(3) or d[:4] == m.group(3)) for d in idx.dates)
        if not ok:
            repl.append((m.start(), m.end(), REMOVED))
            flags.append({"kind": "unsupported_date", "value": m.group(0)})
    for m in _CLOCK.finditer(text):
        if any(a <= m.start() < b for a, b in taken):
            continue
        taken.append((m.start(), m.end()))
        if f"{int(m.group(1))}:{m.group(2)}" not in idx.clocks:
            repl.append((m.start(), m.end(), REMOVED))
            flags.append({"kind": "unsupported_time", "value": m.group(0).strip()})
    _protect(text, _IDS, taken)
    _protect(text, _VOCAB, taken)
    for m in _NUM.finditer(text):
        if any(a <= m.start() < b for a, b in taken):
            continue
        digits, frac, pct = m.group(2).replace(",", ""), m.group(3) or "", bool(m.group(4))
        value = float(digits + frac)
        ref = idx.match(value, len(frac) - 1 if frac else 0, pct)
        if ref is None:
            repl.append((m.start(), m.end(), REMOVED))
            flags.append({"kind": "unsupported_number", "value": m.group(0).strip()})
        elif ref:
            used.append(ref)
    out = text
    for a, b, r in sorted(repl, key=lambda x: x[0], reverse=True):
        out = out[:a] + r + out[b:]
    out = re.sub(r"[ \t]{2,}", " ", out)
    return out, flags, used


def cite(sentence: str, refs, idx: Index, max_refs=2):
    """Append citations for records a sentence's numbers came from but which it does not cite yet."""
    have = set(_CITE.findall(sentence))
    add = []
    for r in refs:
        if r and r not in have and r not in add and r in idx.ids:
            add.append(r)
    if not add:
        return sentence
    tag = " " + " ".join(f"[ref:{r}]" for r in add[:max_refs])
    m = re.search(r"([.!?:;]*)(\s*)$", sentence)
    return sentence[:m.start()] + tag + m.group(1) + m.group(2)


class SentenceBuffer:
    """Streams text out in whole sentences (so each is grounded before a member sees it)."""
    _END = re.compile(r"(?<=[.!?])\s+(?=[A-Z0-9\[(\"'*\-#])|\n+")

    def __init__(self):
        self.buf = ""

    def feed(self, s: str):
        self.buf += s
        out = []
        while True:
            m = self._END.search(self.buf)
            if not m:
                break
            out.append(self.buf[:m.end()])
            self.buf = self.buf[m.end():]
        return out

    def flush(self):
        s, self.buf = self.buf, ""
        return [s] if s else []
