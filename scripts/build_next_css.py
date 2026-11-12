"""UI_NEXT (redesign 2026-11-11): builds web/upgrade-next-base.css from web/upgrade-hub.css.

hub.css carries ~440 literal colours. The redesign needs every one of them on a token so the light and dark themes are two token
sets and nothing else. This script rewrites each colour to the token for its family (neutral, accent, over, under, warn, alt) and its
role in the declaration (text, border, surface), clamps radii to the token radius, drops blurred shadows, backdrop blur and flat
decorative gradients, and maps the two font stacks to the token fonts. Layout rules pass through untouched, so every hub function keeps
its structure. web/upgrade-next.css (hand written) then sets the tokens and the new components on top; web/upgrade-next-grade.css (the
"Behind the grade" panel) is appended.

usage: python scripts/build_next_css.py [--check]     (--check exits 1 when the built file is out of date; tests run it)
"""
import colorsys
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SRC, GRADE, OUT = ROOT / "web" / "upgrade-hub.css", ROOT / "web" / "upgrade-next-grade.css", ROOT / "web" / "upgrade-next-base.css"
HEX = re.compile(r"#([0-9a-fA-F]{8}|[0-9a-fA-F]{6}|[0-9a-fA-F]{4}|[0-9a-fA-F]{3})(?![0-9a-zA-Z_-])")
RGBA = re.compile(r"rgba?\(\s*(\d+)\s*,\s*(\d+)\s*,\s*(\d+)\s*(?:,\s*([\d.]+%?)\s*)?\)")
TEXT_PROPS = ("color", "fill", "stroke", "caret-color", "-webkit-text-fill-color", "text-decoration-color", "text-decoration")
LINE_PROPS = ("border", "outline", "box-shadow", "column-rule")
NESTED = r"(?:[^()]|\((?:[^()]|\([^()]*\))*\))*"


def parse_hex(h):
    if len(h) in (3, 4):
        h = "".join(c * 2 for c in h)
    r, g, b = (int(h[i:i + 2], 16) / 255 for i in (0, 2, 4))
    a = int(h[6:8], 16) / 255 if len(h) == 8 else 1.0
    return r, g, b, a


def family(r, g, b):
    hue, lum, sat = colorsys.rgb_to_hls(r, g, b)
    hue *= 360
    if sat < 0.12 or lum > 0.97 or lum < 0.03:
        return "n"
    if 195 <= hue < 250:
        need = 0.36 if lum < 0.6 else 0.5
        return "acc" if sat >= need else "n"
    if 75 <= hue < 195:
        return "over" if sat >= 0.15 else "n"
    if hue >= 335 or hue < 18:
        return "under" if sat >= 0.2 else "n"
    if 18 <= hue < 75:
        return "warn" if sat >= 0.25 else "n"
    return "alt" if sat >= 0.25 else "n"


def role(prop):
    p = prop.strip().lower()
    if p in TEXT_PROPS:
        return "text"
    if p.startswith(LINE_PROPS):
        return "line"
    return "bg"


def token(r, g, b, prop):
    lum = colorsys.rgb_to_hls(r, g, b)[1]
    fam, rl = family(r, g, b), role(prop)
    if fam == "n":
        if rl == "text":
            return "t1" if lum >= 0.85 else "t2" if lum >= 0.66 else "t3" if lum >= 0.42 else "on" if lum < 0.2 else "t4"
        if rl == "line":
            return "line" if lum < 0.24 else "line2" if lum < 0.45 else "line3"
        return "bg" if lum < 0.075 else "s1" if lum < 0.108 else "s2" if lum < 0.145 else "s3" if lum < 0.2 else "s4" if lum < 0.32 else "s5" if lum < 0.55 else "inv"
    if rl == "text":
        return f"{fam}-on" if lum < 0.3 else f"{fam}-t" if lum >= 0.6 else fam
    if rl == "line":
        return f"{fam}-line2" if lum < 0.36 else f"{fam}-line"
    return f"{fam}-bg" if lum < 0.3 else f"{fam}-fill" if lum < 0.58 else f"{fam}-hi"


def colour(r, g, b, a, prop):
    lum = colorsys.rgb_to_hls(r, g, b)[1]
    if a < 1 and lum < 0.09:                            # dark veils: a dialog backdrop or a drop shadow
        return "var(--n-scrim)"
    v = f"var(--n-{token(r, g, b, prop)})"
    return v if a >= 0.995 else f"color-mix(in srgb,{v} {round(a * 100)}%,transparent)"


def recolour(value, prop):
    value = HEX.sub(lambda m: colour(*parse_hex(m.group(1)), prop), value)

    def rgba(m):
        a = m.group(4)
        a = 1.0 if a is None else float(a[:-1]) / 100 if a.endswith("%") else float(a)
        return colour(int(m.group(1)) / 255, int(m.group(2)) / 255, int(m.group(3)) / 255, a, prop)
    return RGBA.sub(rgba, value)


def radius(value):
    def px(m):
        return m.group(0) if float(m.group(1)) <= 3 else "var(--n-r)"
    return re.sub(r"(\d+(?:\.\d+)?)px", px, value)


def split_commas(value):
    out, depth, cur = [], 0, ""
    for ch in value:
        depth += ch == "("
        depth -= ch == ")"
        if ch == "," and depth == 0:
            out.append(cur)
            cur = ""
        else:
            cur += ch
    return out + [cur]


def shadow(value):
    """Keeps inset bars and spread-only rings (focus, selection); a blurred drop shadow is dropped."""
    keep = []
    for part in split_commas(value):
        bare = re.sub(r"(?:var|color-mix|rgba?)\(" + NESTED + r"\)", "", part)
        nums = re.findall(r"(?<![\w.#-])-?\d*\.?\d+(?:px)?(?![\w%])", bare)
        blur = float(nums[2].replace("px", "")) if len(nums) > 2 else 0.0
        if "inset" in part or blur == 0:
            keep.append(part.strip())
    return ",".join(keep) or "none"


def gradient(value):
    """A flat decorative gradient becomes its first colour; one driven by a variable (a meter) or fading to nothing stays."""
    def flat(m):
        body = m.group(0)
        if "var(--hit" in body or "calc(" in body or "currentColor" in body or "transparent" in body:
            return body
        c = re.search(r"var\(--n-[\w-]+\)|color-mix\(" + NESTED + r"\)", body)
        return c.group(0) if c else body
    return re.sub(r"(?:repeating-)?(?:linear|radial|conic)-gradient\(" + NESTED + r"\)", flat, value)


def fonts(value):
    value = re.sub(r"'Outfit'\s*,\s*system-ui(?:\s*,\s*sans-serif)?", "var(--n-font)", value)
    value = re.sub(r"'DM Mono'\s*,\s*(?:ui-monospace\s*,\s*)?monospace", "var(--n-mono)", value)
    return value.replace("'Bebas Neue'", "var(--n-display)")


def declaration(prop, value):
    p = prop.strip().lower()
    if p.startswith("--u-"):                            # the shared slip / dialog tokens are set by upgrade-next.css
        return prop, value
    value = fonts(recolour(value, prop))
    if p.endswith("radius"):
        value = value if "50%" in value else radius(value)
    elif p == "box-shadow":
        value = shadow(value)
    elif p in ("backdrop-filter", "-webkit-backdrop-filter"):
        value = "none"
    elif p in ("background", "background-image"):
        value = gradient(value)
    return prop, value


DECL = re.compile(r"([{;]\s*)([\w-]+)(\s*:\s*)([^;{}]+)")


def build():
    css = SRC.read_text(encoding="utf-8")
    css = re.sub(r"/\*.*?\*/", "", css, flags=re.S)

    def fix(m):
        prop, value = declaration(m.group(2), m.group(4))
        return f"{m.group(1)}{prop}{m.group(3)}{value}"
    css = DECL.sub(fix, css)
    css = "\n".join(line.rstrip() for line in css.splitlines() if line.strip())
    head = "/* BUILT by scripts/build_next_css.py from upgrade-hub.css + upgrade-next-grade.css. Do not edit: change the source and rebuild. */\n"
    grade = GRADE.read_text(encoding="utf-8") if GRADE.exists() else ""
    return head + css + "\n" + grade


if __name__ == "__main__":
    out = build()
    if "--check" in sys.argv:
        sys.exit(0 if OUT.exists() and OUT.read_text(encoding="utf-8") == out else 1)
    OUT.write_text(out, encoding="utf-8", newline="\n")
    left = len(HEX.findall(re.sub(r"--u-[\w-]+:[^;}]+", "", out)))
    print(f"{OUT.name}: {len(out):,} bytes, literal colours left: {left}")
