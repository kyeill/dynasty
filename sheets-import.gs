/**
 * dynasty -> Google Sheets
 *
 * Pulls every board and roster from the public GitHub repo into tabs of the
 * spreadsheet this script is attached to. GitHub Actions refreshes them each
 * morning, so this depends on no local machine and no credentials.
 *
 * ONE SHEET, SIX TABS -- it does all three sports in one pass:
 *
 *     NBA Rankings   NBA Rosters
 *     NFL Rankings   NFL Rosters
 *     MLB Rankings   MLB Rosters
 *
 * It only ever touches tabs with those exact names, so it is safe to attach to
 * the same spreadsheet that holds your roster input tabs (MLB / NFL / NBA /
 * Mapping) -- those names do not collide and will not be overwritten.
 *
 * SETUP (once):
 *   1. Extensions -> Apps Script.
 *   2. Delete the placeholder code, paste this file, Save.
 *   3. Run `importAll` once and approve the authorization prompt.
 *      (Google warns the app is "unverified" because you wrote it.
 *       Advanced -> Go to <project> is the way through.)
 *   4. Run `createDailyTrigger` once to schedule it.
 */

var RAW = 'https://raw.githubusercontent.com/kyeill/dynasty/main/output/';
var SPORTS = ['NBA', 'NFL', 'MLB'];

// prefix in the repo -> suffix of the tab name here
var IMPORTS = [
  { prefix: 'combined_rankings_', suffix: 'Rankings' },
  { prefix: 'rosters_',           suffix: 'Rosters'  }
];


function importAll() {
  var book = SpreadsheetApp.getActiveSpreadsheet();
  var done = [], missed = [];

  SPORTS.forEach(function (sport) {
    IMPORTS.forEach(function (spec) {
      var file = spec.prefix + sport.toLowerCase() + '.csv';
      // cache-bust: raw.githubusercontent caches hard, and a stale copy would
      // look exactly like "the refresh didn't run".
      var resp = UrlFetchApp.fetch(RAW + file + '?t=' + Date.now(),
                                   { muteHttpExceptions: true });
      if (resp.getResponseCode() !== 200) {
        missed.push(file + ' (HTTP ' + resp.getResponseCode() + ')');
        return;
      }
      var values = parseCsv_(resp.getContentText());
      if (!values.length) {
        missed.push(file + ' (empty)');
        return;
      }
      writeTab_(book, sport + ' ' + spec.suffix, values);
      done.push(sport + ' ' + spec.suffix + ': ' + (values.length - 1));
    });
  });

  importStatus_(book);

  if (missed.length) {
    Logger.log('could not import: %s', missed.join(', '));
  }
  if (!done.length) {
    throw new Error('Nothing imported. Check ' + RAW + ' is reachable.');
  }
  book.toast(done.join('  |  '), 'dynasty imported', 6);
  Logger.log('imported: %s', done.join(', '));
}


/** Kept so triggers created against the old name still work. */
function importRankings() {
  importAll();
}


/**
 * All three sports' source status, merged into one tab.
 *
 * This is the tab to look at when a board seems wrong. A source that has gone
 * dark falls back to its last good copy rather than failing, which keeps the
 * board alive -- but means a stale column looks exactly like a fresh one. Here
 * it says so: `stale = YES` with how many days old the data is.
 */
function importStatus_(book) {
  var rows = [['sport', 'source', 'rows', 'stale', 'age_days', 'note']];
  SPORTS.forEach(function (sport) {
    var url = RAW + '_source_status_' + sport.toLowerCase() + '.csv?t=' + Date.now();
    var resp = UrlFetchApp.fetch(url, { muteHttpExceptions: true });
    if (resp.getResponseCode() !== 200) return;
    var parsed = parseCsv_(resp.getContentText());
    parsed.slice(1).forEach(function (r) {
      rows.push([sport].concat(r));
    });
  });
  if (rows.length > 1) {
    writeTab_(book, 'Source Status', rows);
  }
}


/** CSV text -> rows, with the BOM stripped and numerics restored. */
function parseCsv_(text) {
  // The CSVs carry a BOM so Excel reads accents correctly. Left in place it
  // would turn the first header into "﻿combined_rank".
  if (text.charCodeAt(0) === 0xFEFF) {
    text = text.slice(1);
  }

  var rows = Utilities.parseCsv(text);
  if (!rows.length) return [];

  // parseCsv gives strings, so ranks would sort as 1, 10, 100, 2. Convert
  // anything fully numeric back to a number; leave names and teams alone.
  var body = rows.slice(1).map(function (row) {
    return row.map(function (cell) {
      if (cell === '') return '';
      return /^-?\d+(\.\d+)?$/.test(cell) ? Number(cell) : cell;
    });
  });
  return [rows[0]].concat(body);
}


/** Overwrite one tab, creating it if needed. */
function writeTab_(book, title, values) {
  var tab = book.getSheetByName(title) || book.insertSheet(title);
  tab.clearContents();
  tab.getRange(1, 1, values.length, values[0].length).setValues(values);
  tab.setFrozenRows(1);
  tab.getRange(1, 1, 1, values[0].length).setFontWeight('bold');
  tab.autoResizeColumns(1, Math.min(values[0].length, 12));
}


/** Schedule a daily refresh. Safe to re-run -- it clears its own duplicates. */
function createDailyTrigger() {
  ScriptApp.getProjectTriggers().forEach(function (t) {
    var fn = t.getHandlerFunction();
    if (fn === 'importAll' || fn === 'importRankings') {
      ScriptApp.deleteTrigger(t);
    }
  });
  // 9am Eastern-ish, comfortably after the 7am Actions refresh.
  ScriptApp.newTrigger('importAll').timeBased().atHour(9).everyDays(1).create();
  Logger.log('Daily trigger created for 9am.');
}
