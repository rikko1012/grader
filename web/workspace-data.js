/* Research workspace: the fixed names the page draws with. No row of any kind lives here: every line, chance and result on the
   page comes from /preview/workspace/api/*. A book a board sends beyond these is added from that board's own answer. */
window.WORKSPACE_DATA = (function () {
  "use strict";
  var SPORTS = { cs2: "CS2", lol: "LoL", nba: "NBA", nfl: "NFL" };
  var PLATFORMS = {
    prizepicks: { label: "PrizePicks", short: "PP", slip: true, kind: "modifier", types: [["power", "Power"], ["flex", "Flex"]] },
    underdog: { label: "Underdog", short: "UD", slip: true, kind: "multiplier", types: [["standard", "Standard"], ["flex", "Flex"]] }
  };
  var PRESETS = [
    { id: "p-today", name: "All today", state: { chip: "all", q: "", f: { day: "today" } } },
    { id: "p-cs2", name: "CS2 kills", state: { chip: "cs2", q: "", f: { day: "all", market: "Kills" } } },
    { id: "p-lol", name: "LoL kills", state: { chip: "lol", q: "", f: { day: "all", market: "Kills" } } },
    { id: "p-nba", name: "NBA points", state: { chip: "nba", q: "", f: { day: "today", market: "Points" } } },
    { id: "p-nfl", name: "NFL this week", state: { chip: "nfl", q: "", f: { day: "all" } } },
    { id: "p-gaps", name: "Books differ", state: { chip: "all", q: "", f: { day: "all", gapsOnly: true } } },
    { id: "p-watch", name: "Watchlist", state: { chip: "all", q: "", f: { day: "all", watchOnly: true } } }
  ];
  return { sports: SPORTS, platforms: PLATFORMS, presets: PRESETS };
})();
