"""lol - League of Legends data for the LoL grader (Maps 1-2 / Map 1 / Maps 1-3 kills, assists, deaths).

Self-contained: nothing here imports a CS2 module, and nothing in the CS2 app imports this package. LoL keeps its own
cache file, its own history database and its own output folder (lol/config.py), so a LoL sync or look-up can never
touch a CS2 grade, cache or pick log.
"""
