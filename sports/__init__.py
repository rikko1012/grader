"""sports - NBA / NFL (and WNBA, college) player props for the pick'em boards (PrizePicks, Underdog).

Self-contained like lol/: nothing here imports a CS2 or LoL module, and nothing in the CS2 app or lol/ imports this
package (sports/tests/test_sports_isolation.py). It keeps its own cache, forecast log and output folder
(sports/config.py), so a sports look-up can never touch a CS2 / LoL grade, cache or pick log.
"""
