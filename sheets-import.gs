/**
 * dynasty -> Google Sheets
 *
 * Pulls this sport's outputs from Drive into tabs of the current spreadsheet.
 * Runs as YOU, so the files stay private and there are no credentials to store.
 *
 * ONE SHEET PER SPORT. Paste this into each sheet and set SPORT below.
 * Keeping them separate means a broken import in one can't blank the others.
 *
 * SETUP (once per sheet):
 *   1. Open the spreadsheet -> Extensions -> Apps Script.
 *   2. Delete the placeholder code, paste this file, set SPORT, Save.
 *   3. Run `importRankings` once and approve the authorization prompt.
 *      (Google warns the app is "unverified" because you wrote it.
 *       Advanced -> Go to <project> is the way through.)
 *   4. Run `createDailyTrigger` once to schedule it.
 *
 * Already set up from an earlier version? Just re-paste this file and Save.
 * The entry point is still called `importRankings`, so your existing trigger
 * keeps working -- it now fills the roster tab as well.
 */

var SPORT = 'nba';                                    // <-- 'nba' | 'nfl' | 'mlb'
var FOLDER_PATH = ['Documents', 'Claude', 'dynasty', 'output'];

// Each output gets its own tab. A missing file is skipped with a note rather
// than failing the run, so a sport with no roster yet still imports its board.
var IMPORTS = [
  { prefix: 'combined_rankings_', tab: 'Rankings' },
  { prefix: 'rosters_',           tab: 'Rosters'  }
];


function importRankings() {
  var book = SpreadsheetApp.getActiveSpreadsheet();
  var folder = findFolder_();
  var done = [];

  IMPORTS.forEach(function (spec) {
    var name = spec.prefix + SPORT + '.csv';
    var files = folder.getFilesByName(name);
    if (!files.hasNext()) {
      Logger.log('skipped %s - not found in %s', name, FOLDER_PATH.join('/'));
      return;
    }
    var file = files.next();
    var values = parseCsv_(file);
    if (!values.length) {
      Logger.log('skipped %s - parsed to zero rows', name);
      return;
    }
    writeTab_(book, spec.tab, values);
    done.push(spec.tab + ' ' + (values.length - 1));
  });

  if (!done.length) {
    throw new Error('Nothing imported. Check FOLDER_PATH and that SPORT ("'
                    + SPORT + '") matches the file names in Drive.');
  }
  book.toast(done.join(', '), SPORT.toUpperCase() + ' imported', 5);
  Logger.log('imported: %s', done.join(', '));
}


/** CSV -> rows, with the BOM stripped and numerics restored. */
function parseCsv_(file) {
  // getDataAsString defaults to UTF-8, which keeps Dončić / Şengün intact.
  var text = file.getBlob().getDataAsString('UTF-8');

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


/** Walk My Drive down FOLDER_PATH. */
function findFolder_() {
  var folder = DriveApp.getRootFolder();
  for (var i = 0; i < FOLDER_PATH.length; i++) {
    var next = folder.getFoldersByName(FOLDER_PATH[i]);
    if (!next.hasNext()) {
      throw new Error('Folder not found: ' + FOLDER_PATH.slice(0, i + 1).join('/'));
    }
    folder = next.next();
  }
  return folder;
}


/** Schedule a daily refresh. Safe to re-run -- it clears its own duplicates. */
function createDailyTrigger() {
  ScriptApp.getProjectTriggers().forEach(function (t) {
    if (t.getHandlerFunction() === 'importRankings') {
      ScriptApp.deleteTrigger(t);
    }
  });
  // 9am, comfortably after the machine-side refresh has had its chances.
  ScriptApp.newTrigger('importRankings').timeBased().atHour(9).everyDays(1).create();
  Logger.log('Daily trigger created for 9am.');
}
