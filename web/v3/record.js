/* v3 record.js - owned by page group C (public record).
   Scope: the public /record page (web/record.html + record.js).
   Runs after shell.js in the same bundle, only on a v3 page (ScoutV3 is defined). Keep everything inside this IIFE: a syntax
   error here would stop the whole bundle (tests/test_ui_v3_gate.py parses every bundle file). Use the page's existing data and
   controls only (no mock data, no new API). */
(function () {
  'use strict';
  if (!window.ScoutV3) return;
})();
