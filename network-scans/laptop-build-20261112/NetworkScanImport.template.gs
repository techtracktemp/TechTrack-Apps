// C.Perry
// Chris Perry
/**
 * NetworkScanImport.gs (fixed name: each weekly batch replaces this file)
 * Loads the __SCAN_NICE__ hotel network scan (__SOURCE_TEXT__) into TechTrack.
 *
 * Run it from Tech Projects > Loader Hub: Preview, then Apply.
 * (Or from the editor: previewNetworkScanImport, then applyNetworkScanImport.)
 *
 * Tabs it maintains (created on first run, columns always found by header name):
 *   Network Hotel Status  one row per hotel, per source, per scan date (weekly trend)
 *   Network Devices       full device inventory, upsert by Source + Vendor Device ID
 *   Network Alerts        active alerts and devices down; alerts gone from the scan are marked Cleared
 *   Network Tickets       vendor ticket history, upsert by Source + Ticket #
 *   Network Scan Log      one line per source per applied scan
 * It also appends devices past or near end of life to End of Life Devices (skips serials already there).
 *
 * Re-registered devices (11/6/2026): when Skyway gives a device a new Vendor Device ID (Courtyard
 * Oxford, 81 devices, 11/5), the row is matched by Source + Hotel Code + Serial Number, then by
 * Source + Hotel Code + Device Name (only when that name is unique on both sides and the models
 * agree), and updated in place with the new ID instead of added twice. Old copies already on the
 * tab that match a device in the scan get Scan Status "Duplicate (re-registered)". Nothing is deleted.
 *
 * Hotels that left a vendor (11/6/2026): ATLDN moved off Allbridge to SafetyNet (Oct 2026). Its old
 * Skyway device rows get Scan Status "No longer on Allbridge (SafetyNet)". Rows are kept, not deleted.
 *
 * Safe by design: no email, no triggers, no setNumberFormat, no fixed column letters.
 * Only the columns listed here are written; any other column on these tabs is left alone.
 */

var NSI_SCAN_DATE = '__SCAN_DATE__';
var NSI_SOURCES = __SOURCES__;  // filled by the builder: only the sources in this batch (BlueprintRF added 11/6/2026)
var NSI_EOL_TAB = 'End of Life Devices';
var NSI_LOG_TAB = 'Network Scan Log';
var NSI_TABS = [
  { id: 'status', name: 'Network Hotel Status', headers: __STATUS_COLS__,
    key: ['Scan Date', 'Source', 'Vendor Site ID'], mode: 'snapshot' },
  { id: 'devices', name: 'Network Devices', headers: __DEVICE_COLS__,
    key: ['Source', 'Vendor Device ID'], mode: 'inventory',
    extra: ['First Seen', 'Last Seen', 'Scan Status'] },
  { id: 'alerts', name: 'Network Alerts', headers: __ALERT_COLS__,
    key: ['Source', 'Alert ID'], mode: 'current',
    extra: ['Alert State', 'First Seen', 'Last Seen', 'Cleared On'] },
  { id: 'tickets', name: 'Network Tickets', headers: __TICKET_COLS__,
    key: ['Source', 'Ticket #'], mode: 'history',
    extra: ['First Loaded', 'Last Updated'] }
];
var NSI_DATE_HEADERS = ['Scan Date', 'Install Date', 'End of Sale', 'End of Life', 'Last Synced', 'Detected',
  'Opened', 'Closed', 'Contract Expiration', 'First Seen', 'Last Seen', 'Cleared On', 'First Loaded', 'Last Updated',
  'HW EOL Date', 'Last Discovered'];
var NSI_DATA = __DATA__;
// Source -> Hotel Code -> Scan Status for hotels that left that vendor (rows kept, never deleted).
var NSI_RETIRED = { 'Allbridge Skyway': { 'ATLDN': 'No longer on Allbridge (SafetyNet)' } };

(globalThis.LOADER_HUB = globalThis.LOADER_HUB || {})['network-scan'] = {
  name: 'Network Scan (__SOURCE_TEXT__)',
  batch: '__SCAN_NICE__',
  file: 'NetworkScanImport.gs',
  order: 30,
  description: 'Hotel network status, devices, alerts and vendor tickets into the Network tabs; near EOL devices to End of Life Devices.',
  items: NSI_DATA.status.length + NSI_DATA.devices.length + NSI_DATA.alerts.length + NSI_DATA.tickets.length + NSI_DATA.eol.length,
  preview: 'previewNetworkScanImport',
  apply: 'applyNetworkScanImport'
};

/* ------------------------------------------------------------------ entry points */

function previewNetworkScanImport(opts) {
  var plan = nsiPlan_();
  if (opts && opts.quiet) return nsiResult_(plan, false);
  nsiShow_(plan, false);
  return plan.summary;
}

function applyNetworkScanImport(opts) {
  var lock = LockService.getDocumentLock();
  if (!lock.tryLock(30000)) throw new Error('Another TechTrack script is running. Try again in a minute.');
  try {
    var plan = nsiPlan_();
    plan.tabs.forEach(function (t) { nsiApplyTab_(t); });
    nsiApplyEol_(plan.eol);
    nsiLog_(plan);
    SpreadsheetApp.flush();
    if (opts && opts.quiet) return nsiResult_(plan, true);
    nsiShow_(plan, true);
    return plan.summary;
  } finally {
    lock.releaseLock();
  }
}

/* ------------------------------------------------------------------ planning */

function nsiPlan_() {
  var ss = SpreadsheetApp.getActive();
  var tabs = NSI_TABS.map(function (cfg) { return nsiPlanTab_(ss, cfg, NSI_DATA[cfg.id]); });
  var eol = nsiPlanEol_(ss);
  var summary = tabs.map(function (t) {
    return { tab: t.cfg.name, exists: !!t.sheet, adds: t.adds.length, updates: t.updates.length,
      unchanged: t.unchanged, flagged: t.flags.length, newHeaders: t.missing.length,
      relinked: t.relinked || 0, dupes: (t.dupes || []).length, retired: (t.retired || []).length };
  });
  summary.push({ tab: NSI_EOL_TAB, exists: !!eol.sheet, adds: eol.adds.length, updates: 0,
    unchanged: eol.skipped.length, flagged: 0, newHeaders: 0, relinked: 0, dupes: 0, retired: 0, missingHeaders: eol.missingHeaders });
  return { tabs: tabs, eol: eol, summary: summary };
}

function nsiPlanTab_(ss, cfg, rows) {
  var allHeaders = cfg.headers.concat(cfg.extra || []);
  var sh = ss.getSheetByName(cfg.name);
  var values = sh && sh.getLastRow() > 0 ? sh.getDataRange().getValues() : [];
  var head = values.length ? values[0].map(function (h) { return String(h).trim(); }) : [];
  var missing = allHeaders.filter(function (h) { return head.indexOf(h) < 0; });
  var width = head.length + missing.length;
  var colOf = {};
  head.concat(missing).forEach(function (h, i) { if (h && colOf[h] === undefined) colOf[h] = i; });

  var existing = {};
  for (var r = 1; r < values.length; r++) {
    var k = cfg.key.map(function (h) { return colOf[h] < head.length ? nsiKeyPart_(values[r][colOf[h]]) : ''; }).join('|');
    if (k.replace(/\|/g, '')) existing[k] = r;
  }

  var plan = { cfg: cfg, sheet: sh, head: head, missing: missing, width: width, colOf: colOf,
    values: values, adds: [], updates: [], flags: [], dupes: [], retired: [], relinked: 0, unchanged: 0 };
  var inScan = {};
  var scanDate = nsiToValue_('Scan Date', NSI_SCAN_DATE);
  var recs = rows.map(function (arr) {
    var rec = {};
    cfg.headers.forEach(function (h, i) { rec[h] = nsiToValue_(h, arr[i]); });
    return rec;
  });
  var incomingKeys = {};
  recs.forEach(function (rec) { incomingKeys[cfg.key.map(function (h) { return nsiKeyPart_(rec[h]); }).join('|')] = true; });
  var alt = cfg.mode === 'inventory' ? nsiAltIndex_(values, head, colOf, cfg, existing, recs) : null;
  var claimed = {};

  recs.forEach(function (rec) {
    var k = cfg.key.map(function (h) { return nsiKeyPart_(rec[h]); }).join('|');
    inScan[k] = true;
    var at = existing[k];
    if (at === undefined && alt) {
      at = nsiAltMatch_(alt, rec, incomingKeys, claimed);
      if (at !== undefined) plan.relinked++;
    }
    if (at !== undefined) claimed[at] = true;
    if (cfg.mode === 'inventory') { rec['Last Seen'] = scanDate; rec['Scan Status'] = 'In latest scan'; }
    if (cfg.mode === 'current') { rec['Alert State'] = 'Active'; rec['Last Seen'] = scanDate; rec['Cleared On'] = ''; }
    if (cfg.mode === 'history') { rec['Last Updated'] = scanDate; }
    if (at === undefined) {
      if (cfg.mode === 'inventory' || cfg.mode === 'current') rec['First Seen'] = scanDate;
      if (cfg.mode === 'history') rec['First Loaded'] = scanDate;
      plan.adds.push(rec);
      return;
    }
    var row = values[at];
    var changed = cfg.headers.some(function (h) {
      return colOf[h] >= head.length || !nsiSame_(row[colOf[h]], rec[h]);
    });
    if (cfg.mode === 'current' && colOf['Alert State'] < head.length && row[colOf['Alert State']] !== 'Active') changed = true;
    if (cfg.mode === 'inventory' && colOf['Scan Status'] < head.length && row[colOf['Scan Status']] !== 'In latest scan') changed = true;
    if (changed) {
      if (cfg.mode === 'history' && colOf['First Loaded'] < head.length) rec['First Loaded'] = row[colOf['First Loaded']] || scanDate;
      if ((cfg.mode === 'inventory' || cfg.mode === 'current') && colOf['First Seen'] < head.length) rec['First Seen'] = row[colOf['First Seen']] || scanDate;
      plan.updates.push({ row: at, rec: rec });
    } else {
      plan.unchanged++;
    }
  });

  if (cfg.mode === 'inventory' || cfg.mode === 'current') {
    var srcCol = colOf['Source'];
    Object.keys(existing).forEach(function (k) {
      if (inScan[k] || claimed[existing[k]]) return;
      var row = values[existing[k]];
      if (srcCol >= head.length || NSI_SOURCES.indexOf(String(row[srcCol])) < 0) return;
      if (cfg.mode === 'inventory' && colOf['Hotel Code'] < head.length && colOf['Scan Status'] < head.length) {
        var gone = (NSI_RETIRED[String(row[srcCol])] || {})[String(row[colOf['Hotel Code']]).trim().toUpperCase()];
        if (gone) {
          if (row[colOf['Scan Status']] !== gone) plan.retired.push({ row: existing[k], status: gone });
          return;
        }
      }
      if (alt && nsiAltSeen_(alt, existing[k])) {
        if (colOf['Scan Status'] < head.length && row[colOf['Scan Status']] === NSI_DUPE_STATUS) return;
        plan.dupes.push(existing[k]);
        return;
      }
      if (cfg.mode === 'inventory' && colOf['Scan Status'] < head.length && row[colOf['Scan Status']] === 'Not in latest scan') return;
      if (cfg.mode === 'current' && colOf['Alert State'] < head.length && row[colOf['Alert State']] === 'Cleared') return;
      plan.flags.push(existing[k]);
    });
  }
  return plan;
}

function nsiPlanEol_(ss) {
  var sh = ss.getSheetByName(NSI_EOL_TAB);
  var out = { sheet: sh, adds: [], skipped: [], missingHeaders: [] };
  if (!sh) { out.adds = []; out.missingHeaders = ['tab not found']; return out; }
  var lastCol = sh.getLastColumn();
  var head = sh.getRange(1, 1, 1, lastCol).getValues()[0].map(function (h) { return String(h).trim(); });
  var serialCol = head.indexOf('Serial Number');
  if (serialCol < 0) { out.missingHeaders = ['Serial Number']; return out; }
  var lastRow = sh.getLastRow();
  var serials = {};
  if (lastRow > 1) {
    sh.getRange(2, serialCol + 1, lastRow - 1, 1).getValues().forEach(function (r) {
      var s = String(r[0]).trim().toUpperCase();
      if (s) serials[s] = true;
    });
  }
  NSI_DATA.eol.forEach(function (d) {
    var s = String(d['Serial Number'] || '').trim().toUpperCase();
    if (!s || serials[s]) { out.skipped.push(d); return; }
    serials[s] = true;
    out.adds.push(d);
  });
  out.head = head;
  out.missingHeaders = Object.keys(NSI_DATA.eol[0] || {}).filter(function (h) { return head.indexOf(h) < 0; });
  return out;
}

/* ------------------------------------------------------------------ applying */

function nsiApplyTab_(plan) {
  var ss = SpreadsheetApp.getActive();
  var cfg = plan.cfg;
  var sh = plan.sheet;
  if (!sh) {
    sh = ss.insertSheet(cfg.name);
    var hdr = sh.getRange(1, 1, 1, plan.missing.length);
    hdr.setValues([plan.missing]);
    hdr.setBackground('#224061').setFontColor('#FFFFFF').setFontWeight('bold').setWrap(true)
      .setVerticalAlignment('middle').setHorizontalAlignment('center');
    sh.setFrozenRows(1);
  } else if (plan.missing.length) {
    var add = sh.getRange(1, plan.head.length + 1, 1, plan.missing.length);
    add.setValues([plan.missing]);
    add.setBackground('#224061').setFontColor('#FFFFFF').setFontWeight('bold').setWrap(true);
  }
  var colOf = plan.colOf;
  var dataRows = Math.max(plan.values.length - 1, 0);
  var total = dataRows + plan.adds.length;
  if (!total) return;
  var scanDate = nsiToValue_('Scan Date', NSI_SCAN_DATE);
  var cols = cfg.headers.concat(cfg.extra || []);

  // Column by column so formulas or notes in other columns are never touched.
  cols.forEach(function (h) {
    var c = colOf[h];
    var column = [];
    for (var r = 1; r <= dataRows; r++) column.push([c < plan.head.length ? plan.values[r][c] : '']);
    plan.updates.forEach(function (u) { if (u.rec[h] !== undefined) column[u.row - 1][0] = u.rec[h]; });
    plan.flags.forEach(function (rowIdx) {
      if (cfg.mode === 'inventory' && h === 'Scan Status') column[rowIdx - 1][0] = 'Not in latest scan';
      if (cfg.mode === 'current' && h === 'Alert State') column[rowIdx - 1][0] = 'Cleared';
      if (cfg.mode === 'current' && h === 'Cleared On') column[rowIdx - 1][0] = scanDate;
    });
    (plan.dupes || []).forEach(function (rowIdx) {
      if (h === 'Scan Status') column[rowIdx - 1][0] = NSI_DUPE_STATUS;
    });
    (plan.retired || []).forEach(function (x) {
      if (h === 'Scan Status') column[x.row - 1][0] = x.status;
    });
    plan.adds.forEach(function (rec) { column.push([rec[h] === undefined ? '' : rec[h]]); });
    var nd = (plan.dupes || []).length + (plan.retired || []).length;
    var touched = plan.updates.length || plan.flags.length || plan.adds.length || nd;
    if (!touched) return;
    if (!plan.updates.length && !plan.flags.length && !nd) {
      sh.getRange(dataRows + 2, c + 1, plan.adds.length, 1).setValues(column.slice(dataRows));
    } else {
      sh.getRange(2, c + 1, column.length, 1).setValues(column);
    }
  });
}

function nsiApplyEol_(eol) {
  if (!eol.sheet || !eol.adds.length) return;
  var sh = eol.sheet;
  var head = eol.head;
  var lastRow = sh.getLastRow();
  var start = lastRow + 1;
  var n = eol.adds.length;
  Object.keys(eol.adds[0]).forEach(function (h) {
    var c = head.indexOf(h);
    if (c < 0) return;
    var column = eol.adds.map(function (d) { return [nsiToValue_(h, d[h])]; });
    sh.getRange(start, c + 1, n, 1).setValues(column);
  });
  // Brand (and any other formula column) follows the row above, the same way earlier rows were added.
  for (var c = 0; c < head.length; c++) {
    if (head[c] === '' || eol.adds[0][head[c]] !== undefined) continue;
    var src = sh.getRange(lastRow, c + 1);
    if (src.getFormula()) src.copyTo(sh.getRange(start, c + 1, n, 1), SpreadsheetApp.CopyPasteType.PASTE_FORMULA, false);
  }
}

function nsiLog_(plan) {
  var ss = SpreadsheetApp.getActive();
  var sh = ss.getSheetByName(NSI_LOG_TAB);
  var headers = ['Scan Date', 'Source', 'Hotels', 'Devices', 'Active Alerts', 'Open Tickets',
    'Tickets Added', 'Tickets Updated', 'EOL Rows Added', 'Applied At', 'Applied By'];
  if (!sh) {
    sh = ss.insertSheet(NSI_LOG_TAB);
    sh.getRange(1, 1, 1, headers.length).setValues([headers])
      .setBackground('#224061').setFontColor('#FFFFFF').setFontWeight('bold');
    sh.setFrozenRows(1);
  }
  var head = sh.getRange(1, 1, 1, sh.getLastColumn()).getValues()[0].map(String);
  var tk = plan.tabs.filter(function (t) { return t.cfg.id === 'tickets'; })[0];
  var who = '';
  try { who = Session.getActiveUser().getEmail(); } catch (e) { who = ''; }
  NSI_SOURCES.forEach(function (src) {
    var srcIdx = function (id) { var cfg = NSI_TABS.filter(function (t) { return t.id === id; })[0]; return cfg.headers.indexOf('Source'); };
    var rowsOf = function (id) { var i = srcIdx(id); return NSI_DATA[id].filter(function (r) { return r[i] === src; }); };
    var tCfg = NSI_TABS.filter(function (t) { return t.id === 'tickets'; })[0];
    var openIdx = tCfg.headers.indexOf('Open');
    var rec = {
      'Scan Date': nsiToValue_('Scan Date', NSI_SCAN_DATE), 'Source': src,
      'Hotels': rowsOf('status').length, 'Devices': rowsOf('devices').length, 'Active Alerts': rowsOf('alerts').length,
      'Open Tickets': rowsOf('tickets').filter(function (r) { return r[openIdx] === 'Yes'; }).length,
      'Tickets Added': tk.adds.filter(function (r) { return r['Source'] === src; }).length,
      'Tickets Updated': tk.updates.filter(function (u) { return u.rec['Source'] === src; }).length,
      'EOL Rows Added': src === 'Allbridge Skyway' ? plan.eol.adds.length : 0,
      'Applied At': new Date(), 'Applied By': who
    };
    sh.appendRow(head.map(function (h) { return rec[h] === undefined ? '' : rec[h]; }));
  });
}

/* ------------------------------------------------------------------ helpers */

function nsiToValue_(header, v) {
  if (v === null || v === undefined) return '';
  if (NSI_DATE_HEADERS.indexOf(header) >= 0 && typeof v === 'string') {
    var m = v.match(/^(\d{4})-(\d{2})-(\d{2})(?:[ T](\d{2}):(\d{2}))?/);
    if (m) return new Date(+m[1], +m[2] - 1, +m[3], m[4] ? +m[4] : 0, m[5] ? +m[5] : 0);
  }
  return v;
}

/* ------------------------------------------------------------------ re-registered devices */

var NSI_DUPE_STATUS = 'Duplicate (re-registered)';

function nsiNorm_(v) { return String(v === null || v === undefined ? '' : v).trim().toUpperCase(); }

// Alternate keys for Network Devices: Source|Hotel Code|Serial and Source|Hotel Code|Device Name.
function nsiAltIndex_(values, head, colOf, cfg, existing, recs) {
  var need = ['Source', 'Hotel Code', 'Serial Number', 'Device Name'];
  if (need.some(function (h) { return colOf[h] === undefined || colOf[h] >= head.length; })) return null;
  var get = function (row, h) { return colOf[h] !== undefined && colOf[h] < head.length ? nsiNorm_(row[colOf[h]]) : ''; };
  var idx = { serial: {}, name: {}, rowKey: {}, rowSerial: {}, rowName: {}, inSerial: {}, inName: {}, inNameCount: {}, model: {} };
  Object.keys(existing).forEach(function (k) {
    var r = existing[k], row = values[r];
    var base = get(row, 'Source') + '|' + get(row, 'Hotel Code') + '|';
    var sn = get(row, 'Serial Number'), nm = get(row, 'Device Name');
    idx.rowKey[r] = k;
    idx.model[r] = get(row, 'Model');
    if (sn) { idx.rowSerial[r] = base + sn; (idx.serial[base + sn] = idx.serial[base + sn] || []).push(r); }
    if (nm) { idx.rowName[r] = base + nm; (idx.name[base + nm] = idx.name[base + nm] || []).push(r); }
  });
  recs.forEach(function (rec) {
    var base = nsiNorm_(rec['Source']) + '|' + nsiNorm_(rec['Hotel Code']) + '|';
    var sn = nsiNorm_(rec['Serial Number']), nm = nsiNorm_(rec['Device Name']);
    if (sn) idx.inSerial[base + sn] = true;
    if (nm) { idx.inName[base + nm] = true; idx.inNameCount[base + nm] = (idx.inNameCount[base + nm] || 0) + 1; }
  });
  return idx;
}

// Existing row for a device whose Vendor Device ID changed, or undefined.
function nsiAltMatch_(alt, rec, incomingKeys, claimed) {
  var base = nsiNorm_(rec['Source']) + '|' + nsiNorm_(rec['Hotel Code']) + '|';
  var free = function (r) { return !claimed[r] && !incomingKeys[alt.rowKey[r]]; };
  var sn = nsiNorm_(rec['Serial Number']);
  if (sn) {
    var bySn = (alt.serial[base + sn] || []).filter(free);
    if (bySn.length) return bySn[0];
  }
  var nm = nsiNorm_(rec['Device Name']);
  if (!nm || alt.inNameCount[base + nm] !== 1) return undefined;
  var byNm = (alt.name[base + nm] || []).filter(free);
  if (byNm.length !== 1) return undefined;
  var r = byNm[0], m = nsiNorm_(rec['Model']);
  if (m && alt.model[r] && m !== alt.model[r]) return undefined;
  if (sn && alt.rowSerial[r] && alt.rowSerial[r] !== base + sn) return undefined;
  return r;
}

// True when an old row (not in the scan) is the same device as one in the scan.
function nsiAltSeen_(alt, r) {
  return !!((alt.rowSerial[r] && alt.inSerial[alt.rowSerial[r]]) || (alt.rowName[r] && alt.inName[alt.rowName[r]] && !alt.rowSerial[r]));
}

function nsiKeyPart_(v) {
  if (v instanceof Date) return Utilities.formatDate(v, Session.getScriptTimeZone(), 'yyyy-MM-dd');
  return String(v === null || v === undefined ? '' : v).trim();
}

function nsiSame_(a, b) {
  // Sheets turns some text into dates or times on write (for example "00:07:00").
  // A stored Date next to plain non-ISO text is the same value we wrote before.
  if (a instanceof Date && typeof b === 'string' && b && !/^\d{4}-\d{2}-\d{2}/.test(b)) return true;
  if (b instanceof Date && typeof a === 'string' && a && !/^\d{4}-\d{2}-\d{2}/.test(a)) return true;
  if (a instanceof Date || b instanceof Date) {
    var ta = a instanceof Date ? a.getTime() : (a ? new Date(a).getTime() : 0);
    var tb = b instanceof Date ? b.getTime() : (b ? new Date(b).getTime() : 0);
    return Math.abs(ta - tb) < 60000;
  }
  if (typeof a === 'number' || typeof b === 'number') {
    if (a === '' && b === '') return true;
    return Number(a) === Number(b);
  }
  return String(a === null || a === undefined ? '' : a).trim() === String(b === null || b === undefined ? '' : b).trim();
}

function nsiEsc_(s) {
  return String(s === null || s === undefined ? '' : s).replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;');
}

function nsiFmt_(n) { return String(n).replace(/\B(?=(\d{3})+(?!\d))/g, ','); }

function nsiResult_(plan, applied) {
  var s = plan.summary;
  var sum = function (k) { return s.reduce(function (a, r) { return a + r[k]; }, 0); };
  return {
    html: nsiHtml_(plan, applied),
    text: sum('adds') + ' added, ' + sum('updates') + ' updated, ' + sum('flagged') + ' flagged, ' + sum('unchanged') + ' unchanged, ' +
      sum('relinked') + ' re-registered devices matched, ' + sum('dupes') + ' old duplicates marked, ' + sum('retired') + ' rows marked no longer on vendor',
    counts: { adds: sum('adds'), updates: sum('updates'), flagged: sum('flagged'), unchanged: sum('unchanged'),
      relinked: sum('relinked'), dupes: sum('dupes'), retired: sum('retired') }
  };
}

function nsiShow_(plan, applied) {
  var out = HtmlService.createHtmlOutput(nsiHtml_(plan, applied)).setWidth(820).setHeight(560);
  SpreadsheetApp.getUi().showModelessDialog(out, (applied ? 'Network Scan Applied' : 'Network Scan Preview') + ' - __SCAN_NICE__');
}

function nsiHtml_(plan, applied) {
  var s = plan.summary;
  var adds = s.reduce(function (a, r) { return a + r.adds; }, 0);
  var upd = s.reduce(function (a, r) { return a + r.updates; }, 0);
  var flg = s.reduce(function (a, r) { return a + r.flagged; }, 0);
  var unch = s.reduce(function (a, r) { return a + r.unchanged; }, 0);
  var rows = s.slice().sort(function (a, b) { return b.adds - a.adds; });
  var cell = 'border:1px solid #000;padding:4px 8px;';
  var html = [];
  html.push('<div style="font-family:Calibri,Arial,Helvetica,sans-serif;font-size:11pt;color:#000">');
  html.push('<p style="margin:0 0 6px"><b><u>Section 1: Executive Summary</u></b></p><ol style="margin-top:0">');
  html.push('<li>' + (applied ? 'Applied' : 'Preview of') + ' the __SCAN_NICE__ network scan (__SOURCE_TEXT__). ' +
    (applied ? 'TechTrack is updated.' : 'Nothing has been written yet. Click Apply in the Loader Hub to write it.') + '</li>');
  html.push('<li>' + nsiFmt_(adds) + ' rows to add, ' + nsiFmt_(upd) + ' rows to update, ' + nsiFmt_(flg) +
    ' rows to flag (cleared alerts or devices no longer in the scan), ' + nsiFmt_(unch) + ' unchanged.</li>');
  var rel = s.reduce(function (a, r) { return a + (r.relinked || 0); }, 0);
  var dup = s.reduce(function (a, r) { return a + (r.dupes || 0); }, 0);
  var ret = s.reduce(function (a, r) { return a + (r.retired || 0); }, 0);
  if (ret) html.push('<li>' + nsiFmt_(ret) + ' device rows for hotels that left the vendor (ATLDN, now SafetyNet) get Scan Status "No longer on Allbridge (SafetyNet)". They are kept, not deleted.</li>');
  if (rel || dup) html.push('<li>' + nsiFmt_(rel) + ' devices re-registered under a new vendor ID were matched to their existing row by serial or name (no double add); ' +
    nsiFmt_(dup) + ' older duplicate rows get Scan Status "' + NSI_DUPE_STATUS + '".</li>');
  html.push('<li>' + plan.eol.adds.length + ' devices past or near end of life go to the End of Life Devices tab' +
    (plan.eol.skipped.length ? ' (' + plan.eol.skipped.length + ' skipped, serial already on the tab)' : '') + '.</li>');
  var newTabs = s.filter(function (r) { return !r.exists; }).map(function (r) { return r.tab; });
  if (newTabs.length) html.push('<li>New tabs ' + (applied ? 'created' : 'to create') + ': ' + nsiEsc_(newTabs.join(', ')) + '.</li>');
  if (plan.eol.missingHeaders && plan.eol.missingHeaders.length) html.push('<li>End of Life Devices columns not found (left blank): ' + nsiEsc_(plan.eol.missingHeaders.join(', ')) + '.</li>');
  html.push('</ol>');
  html.push('<p style="margin:10px 0 6px"><b><u>Section 2: For The Weekly Reader</u></b></p>');
  html.push('<table style="border-collapse:collapse;font-size:11pt">');
  html.push('<tr><td></td><td></td><td style="' + cell + 'background:#FFFFCC;border-color:#999;text-align:center"><b><i>SORT</i></b></td><td colspan="5"></td></tr>');
  html.push('<tr>' + ['#', 'Tab', 'Adds', 'Updates', 'Flagged', 'Unchanged', 'New Columns', 'Tab Exists'].map(function (h) {
    return '<th style="' + cell + 'background:#224061;color:#fff;text-align:center">' + h + '</th>';
  }).join('') + '</tr>');
  html.push('<tr style="background:#DCE6F1;font-weight:bold"><td style="' + cell + '"></td><td style="' + cell + '">Total</td>' +
    [adds, upd, flg, unch].map(function (n) { return '<td style="' + cell + 'text-align:right">' + nsiFmt_(n) + '</td>'; }).join('') +
    '<td style="' + cell + '"></td><td style="' + cell + '"></td></tr>');
  rows.forEach(function (r, i) {
    var bg = i % 2 ? '#DCE6F1' : '#FFFFFF';
    html.push('<tr style="background:' + bg + '"><td style="' + cell + 'text-align:right">' + (i + 1) + '</td><td style="' + cell + '">' + nsiEsc_(r.tab) + '</td>' +
      [r.adds, r.updates, r.flagged, r.unchanged, r.newHeaders].map(function (n) { return '<td style="' + cell + 'text-align:right">' + nsiFmt_(n) + '</td>'; }).join('') +
      '<td style="' + cell + 'text-align:center">' + (r.exists ? 'Yes' : 'New') + '</td></tr>');
  });
  html.push('</table>');
  html.push('<p style="font-size:9pt;font-style:italic;margin-top:4px">Note: Network Hotel Status keeps one row per hotel per scan date, so it builds weekly trend history.</p>');
  html.push('<p style="font-size:9.5pt;font-style:italic;margin-top:14px">Prepared by: Chris Perry using TechTrack<br>Date Prepared: __SCAN_NICE__<br>' +
    'Data accurate as of: __SCAN_NICE__<br>Source: __SOURCE_TEXT__ portals</p>');
  html.push('</div>');
  return html.join('');
}
