"""The 1Time grader content book (owner 2026-10-19): 1,000 ad / content ideas a video creator can shoot from, plus a LIVE
section rebuilt on every load from today's real lines.

Live numbers come ONLY from posts.drafts() - the latest saved forecast of a line the platform offers now at that number and
side (the owner's 2026-10-16 posting rule) - plus facts the board itself carries (the other platform's line for the same
prop, a line's opening value). Nothing is invented: no record, no win rate, no profit figure is ever attached to the
grader, and every chance is worded as a model estimate. Display / marketing only - never a grading input.

  python content_book.py --md content/CONTENT_BOOK.md      write the static library (the 1,000 ideas)
"""
import argparse
import html
import itertools
from datetime import datetime, timezone

RULES = [
    "Every pick shown on camera comes from today's live section (verified saved forecast, line offered right now). Never type a pick in by hand.",
    "Say the chance as the grader's model estimate (\"62.4% model estimate\"), never as a hit rate, a lock or a guarantee.",
    "No profit, ROI or record claims about the grader. If you show results, show the Results tab exactly as it is, losses included.",
    "Never pick a stat window because it sounds strong (\"hit in 9 of his last 10\"). The only allowed context is what the live script gives you.",
    "Lean and 0-unit lines are not picks. Show them only as examples of the grader saying \"pass\".",
    "Every video ends with: \"Research tool, not betting advice. 21+. Gambling problem? Call 1-800-GAMBLER.\" (check your state's rules).",
    "Lines move. Film the line check (the platform showing the same number) and post the same day; if the line moved, re-pull the live section.",
    "Never target or feature anyone under 21; no school settings, no cartoon styles aimed at kids.",
]

FORMATS = {"talk": "talking head", "screen": "screen recording", "green": "green-screen over the grader", "text": "text-on-screen + voiceover",
           "duet": "duet / stitch", "pov": "POV skit", "list": "countdown list", "white": "whiteboard / iPad sketch",
           "split": "split screen", "story": "story / 3 slides", "live": "live-stream segment", "carousel": "carousel (6-8 slides)"}

# ---- the product guide: everything the creator must know before filming (facts from the code, 2026-10-19)
GUIDE = [
    ("What 1Time is", [
        "A research tool for CS2 and League of Legends player props on Underdog and PrizePicks, sold as a Whop membership (whop.com/1timeesports).",
        "It grades every posted line: our projected number, the chance of each side, a letter grade and a suggested stake.",
        "It is research, not betting advice and not a tipster service. It never places bets.",
    ]),
    ("Where the numbers come from (CS2)", [
        "Match history from HLTV (every official map), refreshed every 2 hours. BO3.gg fills in only when HLTV has no history for a player.",
        "Lines are read straight from the platforms: Underdog every 5 minutes, PrizePicks every 2 minutes.",
        "Each line is graded automatically within minutes of appearing; a moved line is regraded before it is shown again.",
    ]),
    ("How a CS2 grade is made", [
        "Projection: the player's kills (or headshots) per round, weighted toward recent maps, adjusted for the opponent, the match "
        "environment (LAN or online) and expected rounds - for the exact maps in the prop (Map 1, Maps 1-2 or Maps 1-3).",
        "Chance: the probability of Over and Under at that exact line from the projection's spread. Whole-number lines can push; "
        "the push chance is shown separately.",
        "Grade from the chance of the chosen side: A+ 80%+, A 74%+, A- 68%+, B+ 63%+, B 57%+, B- 55%+. Under 55% = Lean (not a pick).",
        "Kills chances are capped at 78% (very high printed kills chances did not hold up), so kills can't show A+.",
        "Stake: A+ 3U, A 2U, A- 1.5U, B+ 1U, B and B- 0.5U. Caps: stand-in max 1U; fewer than 5 recent maps = 0U; "
        "back from a long break max 1U; a weak betting market for the match can cap the stake too.",
        "Every grade carries the model version that made it. Grades are saved before the match so results can be checked honestly.",
    ]),
    ("What members see and use", [
        "Board: every graded line, filterable by platform, stat, maps, Overs/Unders; sorted by grade, chance or start time.",
        "Chips on a line: 'bumped from X' (the platform moved the line since it opened) and '≠ other platform X' (the other app "
        "lists a different line for the same prop) - which side the gap helps is shown on hover.",
        "Matches: every line of one match together, with a 'Research match' view.",
        "Compare: the same prop on both platforms side by side.",
        "Player card: Overview, History, Match and Markets tabs; the distribution chart (how likely each total is); nearby lines.",
        "Slips: add picks, see the chance all legs hit (pushes counted correctly), and a recheck that refuses a line that moved, "
        "was pulled or changed platform. Slip studio builds slips from graded picks with overlap rules; Slip history shows how they did.",
        "Results: how the board's picks did - the average printed chance next to how often they actually won (a push counts as won).",
        "Moneylines: series-winner prices from exchanges (market prices, not our prediction).",
        "Tools: Single prop (grade any line you type) and Slate (paste a whole slate).",
        "League of Legends: kills, assists and deaths lines with a projection, chance and grade per game scope; the card lists every "
        "factor (role, form, team strength, lane opponent, game pace, patch, rest...) and the team's win chance (model and market). "
        "LoL stakes are 0U until enough real lines have settled.",
    ]),
    ("What the grader is NOT", [
        "Not a guarantee: a 65% grade loses about 1 time in 3. Losing days happen.",
        "Not a record or profit service: never quote a win rate, ROI or $ figure for the grader. The Results tab is the only results source.",
        "Not a lock picker: never say lock, sure thing, free money, can't lose.",
        "Not aware of everything: late stand-ins, the draft (LoL) and live game events are not known before the match.",
    ]),
    ("Words to use / avoid", [
        "Use: 'graded', 'model estimate', 'the grader projects', 'research', 'the line', 'Lean = pass'.",
        "Avoid: 'lock', 'guaranteed', 'easy money', 'hit rate' (for a single pick), 'we're up X units', anything aimed at under-21s.",
        "Always end: 'Research tool, not betting advice. 21+. Gambling problem? Call 1-800-GAMBLER.'",
    ]),
]

# ---- family 1: the daily slate - shot from the live section (each needs {LIVE} data)
DAILY = [
    ("Today's top grade", "The grader's highest-graded line on the board right now is…", "screen", "Open Board → sort Best grade → zoom the top row → open the card → show the chance"),
    ("Grade reveal countdown", "3 lines, 3 grades - guess before I reveal", "list", "Show 3 live lines without grades, pause, reveal each grade + chance"),
    ("Over or Under? The grader decides", "I asked a model, not my gut", "green", "Line on screen → your guess → the grader's side and chance"),
    ("Same prop, two lines", "PrizePicks and Underdog don't agree on this one", "split", "Both platforms' lines side by side (live discrepancy) → which side the gap helps"),
    ("Bumped line alert", "This line just moved - here's what the grader says now", "screen", "Show 'bumped from X' chip → card → the grade is for the current line"),
    ("Headshot line of the day", "Headshots are a different stat - here's today's best graded one", "talk", "Live HS line → projection vs line → chance"),
    ("Map 1 only", "One map, one number", "text", "Live Map 1 line → why Map 1 swings more → chance"),
    ("Projection vs the line", "The line says {line}. The grader projects {proj}.", "white", "Draw the line and the projection, then the chance"),
    ("Chance meter", "How sure is the grader? Here's the meter", "text", "Animated 0-100% bar filling to the live chance"),
    ("Pass of the day", "The grader says don't touch this one", "talk", "A live Lean / 0U line → why a pass is a result too"),
    ("60-second slate", "Today's whole slate in 60 seconds", "screen", "Scroll the board, pause on 3 live grades"),
    ("Match of the day", "One match, every graded line", "screen", "Matches tab → one match group → its lines"),
    ("Stake sizing explained live", "Why this one is 1U and that one is 0.5U", "screen", "Two live lines with different stakes → the stake rules"),
    ("Grade vs vibes", "My gut said Over. The grader said…", "pov", "Skit: you vs the grader on a live line"),
    ("Underdog vs PrizePicks today", "Which platform has the better number for this prop?", "split", "Live line discrepancy → easier line for each side"),
    ("Lines starting soonest", "Starting in under 3 hours", "screen", "Board sorted Starting soonest → first 3 graded lines"),
    ("Kills vs headshots, same player", "Two props, two different reads", "split", "Same player's kills and HS lines from the live section"),
    ("The A-grade club", "Only these lines earned an A today", "list", "Live A-range lines only (if none: say so - that's honest content)"),
    ("Chance check before you build", "Before you add it to a slip, check this", "screen", "Live line → card → chance → slip panel 'chance all hit'"),
    ("What changed since this morning", "Lines moved. Grades moved. Here's the update.", "talk", "Re-pull live section, compare to your morning video"),
]

# ---- family 2: explainers (evergreen) - topic x format
TOPICS = [
    ("grade letters A+ to B-", "What does an A+ actually mean?"), ("Lean", "Why the grader says 'Lean' instead of a grade"),
    ("units / stake sizing", "Why the stake is 0.5U, not 3U"), ("the projection", "Where the grader's projected number comes from"),
    ("model estimate vs hit rate", "A 62% estimate is not '62% of the time he hits'"), ("the 80% range", "The range matters more than the average"),
    ("pushes on whole-number lines", "What happens when he lands exactly on the line"), ("Maps 1-2 vs Map 1", "Why Map 1 lines are a different game"),
    ("Maps 1-3", "Maps 1-3: what if map 3 isn't played?"), ("opponent strength", "Same player, different opponent, different number"),
    ("LAN vs online", "Why LAN matches are graded on LAN form"), ("recency weighting", "Why last month counts more than last spring"),
    ("headshot rate", "Headshots aren't just kills x a percentage"), ("AWPers", "Why AWPers get treated differently on headshot lines"),
    ("IGLs", "Why in-game leaders often post lower numbers"), ("stand-ins", "What the grader does with a stand-in"),
    ("long breaks", "A player back from a long break - what changes"), ("thin history", "What 'limited history' really means"),
    ("roster changes", "New roster, old numbers?"), ("line discrepancies", "When two platforms disagree on the same prop"),
    ("bumped lines", "What a 'bump' is and why it matters"), ("correlation in slips", "Two players from one match aren't independent"),
    ("slip chance math", "Why two 60% picks are not a 60% slip"), ("goblin & demon lines", "Goblins and demons aren't standard lines"),
    ("the Compare tab", "Compare: one prop, both platforms"), ("the Matches tab", "Research a whole match in one view"),
    ("Slip studio", "Let the studio build slips from graded picks"), ("the line recheck", "Why the grader rechecks a line before you copy"),
    ("the Results tab", "How the grader is doing - the honest version"), ("moneylines", "What the Moneylines tab shows (and doesn't)"),
    ("the Slate tool", "Paste a slate, get every grade"), ("single prop lookup", "Grade any prop in 10 seconds"),
    ("data sources", "Where the numbers come from (HLTV)"), ("model versions", "Why every grade carries a version"),
    ("variance", "Good picks lose. Here's why that's normal"), ("losing streaks", "What a normal losing streak looks like at 60%"),
    ("bankroll basics", "Why flat stakes beat chasing"), ("the difference between a pick and a grade", "A grade is research, not a bet slip"),
    ("CS2 kills per round", "Kills per round: the number behind the number"), ("rounds per map", "Stomps vs overtime: why rounds swing kills"),
    ("LoL roles", "LoL: why the ADC and the support live in different worlds"), ("LoL game length", "LoL: long games, big numbers"),
    ("LoL lane matchups", "LoL: the guy across the lane matters"), ("LoL team win odds", "LoL: favourites, underdogs and kill counts"),
    ("LoL kill participation", "LoL: kill participation explained"), ("LoL Maps 1-2 vs 1-3", "LoL: BO3 vs BO5 lines"),
    ("why picks get removed", "Why a line disappears from the board"), ("started matches", "Why you can't add a started match"),
    ("time zones and start times", "Two platforms, two start times - which one is right?"), ("the distribution chart", "Read the distribution chart in 20 seconds"), ("the 'Data complete' badge", "What 'Data complete' means"),
]
EXPLAIN_FORMATS = [("talk", "30-45s explainer"), ("white", "whiteboard breakdown"), ("text", "myth vs fact"), ("screen", "show it in the grader")]

# ---- family 3: product tutorials / screen recordings
FEATURES = ["Board filters", "Kills / Headshots toggle", "Overs-only / Unders-only", "Best-grade sort", "Starting-soonest sort",
            "Bets-only toggle", "Hide flagged", "Player search", "Compare tab", "Matches tab", "Research match dialog", "Player card tabs",
            "History tab on the card", "Match tab on the card", "Markets tab on the card", "Distribution chart", "Nearby lines",
            "Add to slip", "Copy pick", "Slip panel chance", "Line recheck", "Slip studio settings", "Slip studio 'Use board filters'",
            "Slip history", "Results tab", "Moneylines tab", "Slate tool", "Single prop lookup", "Compact view", "LoL board", "LoL card factors",
            "Bump chip", "Line-discrepancy chip", "Phone layout tour", "Desktop layout tour"]
TUTORIAL_ANGLES = [("How to", "Step-by-step in under 40s"), ("Hidden feature", "Most people miss this"), ("Speedrun", "From open to graded slip in 30s")]

# ---- family 4: hooks (the first 2 seconds) - each a separate idea
HOOKS = [
    "Stop guessing your props.", "I let a model grade my slate.", "This line is lying to you.", "Two platforms, two different numbers.",
    "Your gut vs the grader.", "The grader said no. Here's why.", "Most people read this number wrong.", "60% doesn't mean what you think.",
    "This line moved overnight.", "Don't build a slip until you see this.", "One stat that changes every CS2 prop.", "Kills are a team stat.",
    "Why stomps kill kill props.", "This is what an A grade looks like.", "A Lean is not a pick.", "Same player. Different number.",
    "The map pool changes everything.", "Your favourite player isn't always the play.", "This is how the grader thinks.",
    "PrizePicks vs Underdog: who's higher?", "The overtime factor.", "Never chase a bumped line blind.", "The number behind the number.",
    "Here's a pick I'm NOT taking.", "Watch the grader change its mind.", "Every prop on tonight's match, graded.", "AWPers break headshot lines.",
    "LAN players are different players.", "Back from a break? Careful.", "New roster, same expectations? No.", "Map 1 is chaos.",
    "This is why parlays are hard.", "Two picks from one match? Read this first.", "Pushes are real. Plan for them.",
    "The honest results video.", "I lost. Here's what the grader said.", "What 'model estimate' means.", "The line is set to make you guess.",
    "One minute, whole slate.", "Underdog payouts aren't odds.", "Goblin lines aren't free.", "Demon lines explained in 20 seconds.",
    "LoL: the support kills trap.", "LoL: why the ADC line is higher.", "LoL: the stomp problem.", "LoL: favourites and kills.",
    "This tab saves you 20 minutes.", "Research a match in 10 seconds.", "The slip studio built this.", "Copying a pick? It rechecks first.",
]
HOOK_STYLES = [("talk", "cold open to camera"), ("text", "big text over the board"), ("green", "green-screen over the card"), ("pov", "POV skit")]

# ---- family 5: engagement / community
ENGAGE = [("Guess the grade", "Viewers guess A/B/Lean in comments, reveal tomorrow"), ("Over/Under poll", "Story poll on a live line, reveal the grader's side"),
          ("Comment reply: 'is this legit?'", "Answer honestly: research tool, model estimates, Results tab"), ("Stitch a pick video", "Grade someone else's posted pick on camera (no insults)"),
          ("Ask me a player", "Comment a player → grade them on the next video"), ("Viewer slip review", "Grade a viewer's slip, leg by leg"),
          ("Myth-buster Monday", "One betting myth per week"), ("Weekly Q&A", "Answer 5 questions about the grader"),
          ("Before/after the match", "Same line: grade before, result after (win or loss)"), ("Line-watch live", "Live stream: watch lines move pre-match"),
          ("Team of the week", "Grade every line of one team this week"), ("Duet a bad take", "React to 'just take every Over'"),
          ("This or that", "Two live lines, which grade is higher?"), ("Grade the influencer", "Run a public pick through the grader"),
          ("Member spotlight", "A member shows how they use it (consent required, no profit claims)"), ("Newcomer tour", "First 60 seconds for new members"),
          ("Result recap (honest)", "Yesterday's graded lines: what won, what lost"), ("Prediction thread", "Pin tomorrow's top live line, revisit after"),
          ("Spot the trap", "Show a line that looks easy; grader explains why it isn't"), ("Rank the slate", "Rank today's lines yourself, then the grader's order")]
ENGAGE_FORMATS = [("talk", "short"), ("story", "story"), ("live", "live"), ("carousel", "carousel"), ("duet", "duet/stitch")]

# ---- family 6: seasonal / event tie-ins
EVENTS = ["a Major week", "IEM", "BLAST", "ESL Pro League", "PGL", "Thunderpick World Championship", "playoffs day", "a qualifier weekend",
          "the start of a new season", "a roster-shuffle week", "LoL Worlds", "MSI", "LCK playoffs", "LPL playoffs", "LEC finals",
          "a new CS2 patch / map-pool change", "a holiday weekend slate", "Friday night slate", "Sunday finals", "an upset day"]
EVENT_ANGLES = [("What the grader sees for {e}", "screen"), ("{e}: the lines to watch", "list"), ("{e}: one pass, one play", "talk"),
                ("{e}: platform line differences", "split"), ("{e}: honest recap after", "text")]

# ---- family 7: transparency / trust (no claims - facts only)
TRUST = ["How the grader is built (no secret sauce, just data)", "What the grader can't know (draft, stand-ins announced late)",
         "Why grades change during the day", "Why we show the Lean lines too", "What happens when the data source is down",
         "Why we don't show 'last 10' cherry-picks", "Why there's no profit chart", "How a line gets graded in under a minute",
         "What a model version is and why it's on every grade", "What 'research tool, not betting advice' really means",
         "How we check the line before you copy", "The day the grader passed on everything", "Why stakes are 0U on LoL right now",
         "How results are counted (pushes, voids)", "Why a 70% pick still loses 3 in 10", "What we changed this week (plain words)",
         "Questions people ask in DMs", "Our responsible-play rules", "Why we'll never say 'lock'", "Reading the Results tab with me"]
TRUST_FORMATS = [("talk", "talking head"), ("screen", "screen walk-through"), ("text", "text post / carousel"), ("white", "whiteboard")]


def ideas():
    """The 1,000 ideas: (pillar, format, title, hook, how-to, data)."""
    out = []
    # 1. daily slate x 4 platform/stat variants = 80 (live)
    variants = [("CS2 kills", "kills"), ("CS2 headshots", "headshots"), ("PrizePicks", "prizepicks"), ("Underdog", "underdog")]
    for (t, hook, f, how), (vname, _) in itertools.product(DAILY, variants):
        out.append(("Daily slate (live)", f, f"{t} - {vname}", hook, how, "LIVE: pull from today's live section"))
    # 2. explainers 51 topics x 4 = 204
    for (topic, hook), (f, fname) in itertools.product(TOPICS, EXPLAIN_FORMATS):
        out.append(("Explainer", f, f"{topic} ({fname})", hook, f"Explain {topic} in one idea; show it on a real card if the topic appears in the live section", "Evergreen (use a live card as the example when possible)"))
    # 3. tutorials 35 x 3 = 105
    for feat, (angle, how) in itertools.product(FEATURES, TUTORIAL_ANGLES):
        out.append(("Product tutorial", "screen", f"{angle}: {feat}", f"{angle} - {feat}", f"{how}; film on phone width and desktop", "Uses whatever is on the board today"))
    out.append(("Product tutorial", "screen", "The whole grader in 90 seconds", "Everything the grader does, in 90 seconds",
                "Walk the product guide top to bottom on screen: board, card, chips, matches, compare, slips, results, tools, LoL", "Uses today's board"))
    # 4. hooks 50 x 4 = 200
    for hook, (f, fname) in itertools.product(HOOKS, HOOK_STYLES):
        out.append(("Hook-led ad", f, f"\"{hook}\" ({fname})", hook, "Hook in the first 2s → one live line or one explainer point → CTA", "Pair with a live line or an explainer"))
    # 5. engagement 20 x 5 = 100
    for (t, how), (f, fname) in itertools.product(ENGAGE, ENGAGE_FORMATS):
        out.append(("Community", f, f"{t} ({fname})", t, how, "Live line when the idea needs one"))
    # 6. events 20 x 5 = 100
    for e, (a, f) in itertools.product(EVENTS, EVENT_ANGLES):
        out.append(("Event tie-in", f, a.format(e=e), a.format(e=e), "Only on days the event has lines on the board; numbers from the live section", "LIVE on event days"))
    # 7. trust 20 x 4 = 80
    for t, (f, fname) in itertools.product(TRUST, TRUST_FORMATS):
        out.append(("Transparency", f, f"{t} ({fname})", t, "Facts only - no records, no profit, no promises", "Evergreen"))
    # 8. LoL series: 13 LoL topics x 10 angles = 130 (the rest of the 1,000)
    lol = ["role lines", "kill participation", "game length", "lane matchups", "team win chance", "stomp games", "BO3 vs BO5 lines",
           "patch changes", "roster changes", "the LoL card's factor panel", "assists lines", "deaths lines", "Polymarket win prices"]
    lol_angles = [("talk", "30s explainer"), ("screen", "show it on a LoL card"), ("white", "whiteboard"), ("text", "myth vs fact"),
                  ("list", "3 things to check"), ("split", "two players compared"), ("pov", "POV skit"), ("carousel", "carousel"),
                  ("green", "green-screen"), ("story", "story poll")]
    for t, (f, fname) in itertools.product(lol, lol_angles):
        out.append(("League of Legends", f, f"LoL series - {t} ({fname})", f"LoL: {t}", "LoL stakes are 0U right now - show grades as research, never as plays",
                    "LoL card / board when lines are up"))
    return out


def _n_check():
    n = len(ideas())
    assert n == 1000, n
    return n


def markdown():
    rows = ideas()
    pillars = {}
    for r in rows:
        pillars.setdefault(r[0], 0)
        pillars[r[0]] += 1
    lines = ["# 1Time grader - content book (1,000 ideas)", "",
             "For the video creator. Shoot from this library; take every number from the **live section** "
             "(`/content-book`, rebuilt from today's real lines on every load). Formats: " +
             ", ".join(f"`{k}` {v}" for k, v in FORMATS.items()) + ".", "", "## Rules for every video", ""]
    lines += [f"{i}. {r}" for i, r in enumerate(RULES, 1)]
    lines += ["", "## Know the product (read this first)", ""]
    for head, items in GUIDE:
        lines += [f"### {head}", ""] + [f"- {x}" for x in items] + [""]
    lines += ["", "## Pillars", ""] + [f"- **{p}**: {n} ideas" for p, n in pillars.items()] + [""]
    cur = None
    for i, (p, f, title, hook, how, data) in enumerate(rows, 1):
        if p != cur:
            lines += ["", f"## {p}", ""]
            cur = p
        lines.append(f"{i}. **{title}** · _{FORMATS[f]}_ — Hook: \"{hook}\" — {how}. Data: {data}.")
    return "\n".join(lines) + "\n"


# ---------------------------------------------------------------- live section
def _last10(d):
    """The one allowed context line (posts.py rule): the grader's saved last-10 record at this line, Maps 1-2 lines only."""
    try:
        o, n = (int(x) for x in str(d.get("last10_saved")).split("/"))
    except (TypeError, ValueError):
        return None
    if n <= 0 or (d.get("scope") or "12") != "12":
        return None
    return f"The grader's last-{n} record at this line (Maps 1-2): {o} over, {n - o} under."


def _when(iso):
    try:
        return datetime.fromisoformat(str(iso).replace("Z", "+00:00")).astimezone(timezone.utc).strftime("%a %b %d, %H:%M UTC")
    except (TypeError, ValueError):
        return ""


def _side(s):
    return "OVER" if s == "More" else "UNDER"


def live_scripts(drafts, board_rows):
    """Ready-to-shoot scripts from today's verified drafts. board_rows (both platforms) add the facts the board carries:
    the other platform's standard line for the same player / stat / maps / match, and the line's opening value (bump)."""
    by_key = {}
    for r in board_rows or []:
        if r.get("alt"):
            continue
        by_key.setdefault((str(r.get("player_id") or r.get("nick")), r.get("match_slug"), r.get("prop"), r.get("scope") or "12"), []).append(r)
    out = []
    for d in drafts:
        nick, prop = d["player"], ("headshots" if d["prop"] == "hs" else "kills")
        chance = f"{d['chance'] * 100:.1f}%"
        book = "PrizePicks" if d["platform"] == "prizepicks" else "Underdog"
        k = (str(d.get("player_id") or nick), d.get("match_slug"), d["prop"], d.get("scope") or "12")
        same = [r for r in by_key.get(k, []) if (r.get("book") or "underdog") != d["platform"]]
        here = next((r for r in by_key.get(k, []) if (r.get("book") or "underdog") == d["platform"]), {})
        facts = []
        if same and same[0].get("line") is not None and float(same[0]["line"]) != float(d["line"]):
            o = same[0]
            facts.append(f"{'Underdog' if (o.get('book') or 'underdog') == 'underdog' else 'PrizePicks'} lists {o['line']:g} for the same prop")
        if here.get("open_line") is not None and float(here["open_line"]) != float(d["line"]):
            facts.append(f"{book} opened this line at {float(here['open_line']):g} (bumped {'up' if d['line'] > float(here['open_line']) else 'down'})")
        line = f"{nick} {_side(d['side'])} {d['line']:g} {prop}"
        scripts = [
            ("Grade reveal", f"HOOK: \"{nick}, {d['line']:g} {prop}. What did the grader say?\" → reveal Grade {d['grade']} · {_side(d['side'])} · {chance} model estimate → show the {book} line on screen."),
            ("Projection vs line", f"HOOK: \"{book} says {d['line']:g}. The grader projects {d['projection']:.1f}.\" → draw both numbers → \"{chance} model estimate on the {_side(d['side']).lower()}.\""),
            ("Chance meter", f"Text: {line} → meter fills to {chance} → \"Grade {d['grade']}. A model estimate, not a guarantee.\""),
        ]
        if facts:
            scripts.append(("Line check", f"HOOK: \"Same prop, different number.\" → {'; '.join(facts)} → the grade is for {book} {d['line']:g}."))
        rec = _last10(d)
        if rec:
            scripts.append(("Grader's own record", f"Say exactly: \"{rec}\" (the only context line allowed)."))
        out.append({"line": line, "book": book, "grade": d["grade"], "chance": chance, "match_start": d.get("match_start"),
                    "forecast_id": d["forecast_id"], "post_text": d["text"], "facts": facts, "scripts": scripts})
    return out


def page(live, generated_at, skipped_n, results=None):
    e = html.escape
    rows = ideas()
    css = ("body{font:15px/1.55 Inter,system-ui,sans-serif;background:#0b0e14;color:#eef2f8;margin:0;padding:24px 16px 80px}"
           "main{max-width:900px;margin:auto}h1{font-size:26px;margin:0 0 4px}h2{margin:32px 0 10px;font-size:19px}"
           ".m{color:#a9b2c2}.card{background:#121722;border:1px solid #262e3b;border-radius:12px;padding:14px 16px;margin:10px 0}"
           ".g{display:inline-block;padding:1px 8px;border-radius:6px;background:#f7a45b22;color:#ffc595;font-weight:600}"
           "ol li{margin:6px 0}code,pre{white-space:pre-wrap;background:#0d1119;border:1px solid #262e3b;border-radius:8px;padding:8px;display:block;font-size:13px}"
           "details summary{cursor:pointer;color:#ffc595}.f{color:#7fd8be}")
    h = [f"<!doctype html><meta charset=utf-8><meta name=viewport content='width=device-width,initial-scale=1'><title>Content book</title><style>{css}</style><main>",
         "<h1>1Time grader · content book</h1>", f"<div class=m>Live section rebuilt {e(generated_at)} from today's verified lines · 1,000-idea library below</div>",
         "<h2>Rules for every video</h2><ol>" + "".join(f"<li>{e(r)}</li>" for r in RULES) + "</ol>",
         "<h2>Know the product</h2>" + "".join(f"<details><summary><b>{e(hd)}</b></summary><ul>" + "".join(f"<li>{e(x)}</li>" for x in it) + "</ul></details>"
                                              for hd, it in GUIDE),
         (f"<div class=card><b>Results tab right now</b> (the only results you may show): {e(results)}</div>" if results else ""),
         f"<h2>Today's real lines ({len(live)})</h2>"]
    if not live:
        h.append(f"<div class=card>No line qualifies right now ({skipped_n} checked lines were skipped - started, moved, 0U or not yet saved). "
                 "Shoot an evergreen idea today, or check back after the board regrades.</div>")
    for x in live:
        h.append(f"<div class=card><b>{e(x['line'])}</b> · {e(x['book'])} · <span class=g>{e(x['grade'])}</span> · {e(x['chance'])} model estimate"
                 f"<div class=m>forecast #{x['forecast_id']} · starts {e(_when(x['match_start']))}</div>"
                 + "".join(f"<div class=f>• {e(f)}</div>" for f in x["facts"])
                 + "<ol>" + "".join(f"<li><b>{e(t)}</b>: {e(s)}</li>" for t, s in x["scripts"]) + "</ol>"
                 + f"<details><summary>Ready caption (verified)</summary><code>{e(x['post_text'])}</code></details></div>")
    h.append("<h2>The 1,000 ideas</h2>")
    cur = None
    for i, (p, f, title, hook, how, data) in enumerate(rows, 1):
        if p != cur:
            if cur is not None:
                h.append("</ol></details>")
            n = sum(1 for r in rows if r[0] == p)
            h.append(f"<details><summary><b>{e(p)}</b> ({n})</summary><ol start={i}>")
            cur = p
        h.append(f"<li><b>{e(title)}</b> · <i>{e(FORMATS[f])}</i><br>Hook: “{e(hook)}” — {e(how)}. <span class=m>{e(data)}</span></li>")
    h.append("</ol></details></main>")
    return "".join(h)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--md", required=True)
    a = ap.parse_args()
    _n_check()
    with open(a.md, "w", encoding="utf-8") as f:
        f.write(markdown())
    print("wrote", a.md, len(ideas()), "ideas")


if __name__ == "__main__":
    main()
