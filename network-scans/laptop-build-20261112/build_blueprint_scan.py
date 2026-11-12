# C.Perry
# Chris Perry
"""Build the BlueprintRF batch of the TechTrack network scan loader (NetworkScanImport.gs).

Usage: python3 -I build_blueprint_scan.py <scan date YYYY-MM-DD>
Reads  network-scans/<date>/blueprint/ (blueprint-device-inventory.csv, blueprint-circuits.csv,
       blueprint-alert-summary.csv) and /tmp/tt/pm.json (Property Master, from refresh_techtrack.py).
Writes network-scans/<date>/blueprint/out/NetworkScanImport.gs and blueprint-normalized.json.

The loader only touches Source = "BlueprintRF" rows (NSI_SOURCES), so Skyway and Cloud5 rows are
never flagged "Not in latest scan" by a Blueprint batch, and the reverse. Devices dedupe the same way
as Skyway: Source + Vendor Device ID, then Source + Hotel Code + Serial, then Source + Hotel Code + Device Name.
Vendor Device ID is "BP-" + MAC (Blueprint has no device id; MAC is unique and stable).
"""
import csv, json, os, re, sys
from collections import Counter, defaultdict
from datetime import datetime

BASE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, BASE)
SCAN = sys.argv[1] if len(sys.argv) > 1 else '2026-11-06'
SRC_DIR = os.path.join(BASE, SCAN, 'blueprint')
OUT = os.path.join(SRC_DIR, 'out')
os.makedirs(OUT, exist_ok=True)
BP = 'BlueprintRF'
PORTAL = 'https://dash.blueprintrf.com'
PULLED = SCAN + ' 02:05'

# Blueprint site code -> TechTrack Hotel Code (Hotel Key). Blueprint "SpringHill Suites Hampton"
# is the Marriott SpringHill, Hotel Key PHFHC-SHS (never mixed with the PHFHC Hilton Garden Inn).
CODE_OVERRIDE = {'PHFHC': 'PHFHC-SHS'}
TYPE_MAP = {'AccessPoint': 'Access Point', 'Switch': 'Switch', 'Firewall': 'Firewall',
            'WLANController': 'Wireless Controller', 'Server': 'Gateway'}


def clean(v):
    return re.sub(r'\s*[—–]\s*', ' - ', str(v or '')).strip()


def nice_date(iso):
    d = datetime.strptime(iso[:10], '%Y-%m-%d')
    return d.strftime('%b ') + str(d.day) + d.strftime(' %Y')


def rows(name):
    with open(os.path.join(SRC_DIR, name), encoding='utf-8-sig') as f:
        return list(csv.DictReader(f))


def location(name, site):
    """ABICYAP07-02-230 -> Floor 2, Room 230. CMHDH-GIA-MDF-AP024-230 -> Room 230."""
    m = re.match(r'^' + re.escape(site) + r'[A-Z]{2}\d+-(\d+)-(.+)$', name)
    if m:
        fl, spot = m.group(1), m.group(2)
        spot = 'Room ' + spot if spot.isdigit() else spot
        return 'Floor %d, %s' % (int(fl), spot)
    last = name.split('-')[-1]
    if last and not re.match(r'^AP\d+$', last):
        return 'Room ' + last if last.isdigit() else last
    return ''


# ------------------------------------------------------------------ TechTrack Property Master
pm = {}
for r in json.load(open(os.path.join(BASE, 'pm.json') if os.path.exists(os.path.join(BASE, 'pm.json')) else '/tmp/tt/pm.json')):
    key = str(r.get('Hotel Key') or r.get('Hotel Code') or '').strip().upper()
    if key:
        pm[key] = r

inv = rows('blueprint-device-inventory.csv')
circ = rows('blueprint-circuits.csv')
def load_util():
    """Circuit over-80% summary per site. Accepts the summary shape (Site Code, Days Over 80,
    Times Over 80, Last Date) or the per-day detail shape the laptop pull now saves
    (Site Code, Alert Type, Date, Count), in blueprint-alert-summary.csv or blueprint-alerts.csv."""
    src = []
    for name in ('blueprint-alert-summary.csv', 'blueprint-alerts.csv'):
        if os.path.exists(os.path.join(SRC_DIR, name)):
            src = rows(name)
            if src and 'Days Over 80' in src[0]:
                return {r['Site Code']: r for r in src}
            if src and 'Date' in src[0]:
                break
    agg = {}
    for r in src:
        if '80' not in (r.get('Alert Type') or ''):
            continue
        sc = (r.get('Site Code') or '').strip()
        if not sc:
            continue
        a = agg.setdefault(sc, {'Site Code': sc, 'days': set(), 'Times Over 80': 0, 'Last Date': ''})
        d = (r.get('Date') or '')[:10]
        a['days'].add(d)
        try:
            a['Times Over 80'] += int(float(r.get('Count') or 1))
        except ValueError:
            a['Times Over 80'] += 1
        a['Last Date'] = max(a['Last Date'], d)
    out = {}
    for sc, a in agg.items():
        out[sc] = {'Site Code': sc, 'Days Over 80': str(len(a['days'])),
                   'Times Over 80': str(a['Times Over 80']), 'Last Date': a['Last Date']}
    return out


util = load_util()

sites = {}
for r in inv:
    sc = r['Site Code'].strip().upper()
    if sc not in sites:
        code = CODE_OVERRIDE.get(sc, sc)
        p = pm.get(code)
        sites[sc] = {'site': sc, 'code': code, 'id': r['Site ID'], 'portal_name': clean(r['Site Name']),
                     'name': clean(p['Hotel Name']) if p else clean(r['Site Name']),
                     'brand': clean(p.get('Brand')) if p else '', 'fund': clean(p.get('Fund')) if p else '',
                     'note': '' if p else 'Not in TechTrack Property Master (Blueprint name: %s)' % clean(r['Site Name'])}

# ------------------------------------------------------------------ devices + device alerts
devices, alerts = [], []
scan_dt = datetime.strptime(SCAN, '%Y-%m-%d')
for r in inv:
    s = sites[r['Site Code'].strip().upper()]
    mac = r['MAC'].strip().upper()
    dtype = TYPE_MAP.get(r['Type'], clean(r['Type']))
    devices.append({
        'Source': BP, 'Hotel Code': s['code'], 'Hotel Name': s['name'], 'Device Name': clean(r['Device Name']),
        'Device Type': dtype, 'Manufacturer': clean(r['Manufacturer']), 'Model': clean(r['Model']),
        'Serial Number': clean(r['Serial']), 'MAC Address': mac, 'IP Address': clean(r['IP']), 'Firmware': '',
        'Status': 'Online' if r['Status'] == 'Online' else 'Offline', 'Install Date': '', 'End of Sale': '',
        'End of Life': '', 'EOL Flag': '', 'Switch Port': '', 'Location': location(r['Device Name'], s['site']),
        'Vendor Site ID': s['id'], 'Vendor Device ID': 'BP-' + mac, 'Last Synced': PULLED})
    if r['Status'] != 'Online':
        changed = clean(r['Last Status Change'])[:16]
        try:
            age = (scan_dt - datetime.strptime(changed[:10], '%Y-%m-%d')).days
        except ValueError:
            age = 999
        sev = 2 if age <= 2 else (3 if age <= 30 else 4)
        desc = '%s offline since %s.' % (dtype, nice_date(changed) if changed else 'unknown')
        if age > 30:
            desc += ' Offline over 30 days: confirm with BlueprintRF whether it is a real outage or a stale monitor.'
        alerts.append({'Source': BP, 'Hotel Code': s['code'], 'Hotel Name': s['name'], 'Alert': 'Device Offline',
                       'Severity': sev, 'Detected': changed, 'Device Name': clean(r['Device Name']),
                       'Model': clean(r['Model']), 'Serial Number': clean(r['Serial']), 'Device Status': 'Offline',
                       'Description': desc, 'Alert ID': 'BP-DEV-' + mac})

# ------------------------------------------------------------------ circuit alerts
circ_by = defaultdict(list)
for c in circ:
    sc = c['Site Code'].strip().upper()
    circ_by[sc].append(c)
    if c['Status'] != 'Online' and sc in sites:
        s = sites[sc]
        alerts.append({'Source': BP, 'Hotel Code': s['code'], 'Hotel Name': s['name'], 'Alert': 'Circuit Offline',
                       'Severity': 1, 'Detected': '', 'Device Name': c['WAN'] + ' ' + clean(c['Provider']),
                       'Model': '', 'Serial Number': '', 'Device Status': 'Offline',
                       'Description': '%s %s circuit (%s Mbps) offline in the BlueprintRF portal on %s.' % (
                           c['WAN'], clean(c['Provider']), c['Download Speed'], nice_date(SCAN)),
                       'Alert ID': 'BP-WAN-%s-%s' % (s['code'], c['WAN'])})
for sc, u in util.items():
    if sc not in sites:
        continue
    s = sites[sc]
    alerts.append({'Source': BP, 'Hotel Code': s['code'], 'Hotel Name': s['name'], 'Alert': 'Circuit Over 80%',
                   'Severity': 3, 'Detected': u['Last Date'], 'Device Name': '', 'Model': '', 'Serial Number': '',
                   'Device Status': '',
                   'Description': 'Circuit went over 80%% %s times on %s of the last 30 days (latest %s).' % (
                       u['Times Over 80'], u['Days Over 80'], nice_date(u['Last Date'])),
                   'Alert ID': 'BP-UTIL-' + s['code']})

# ------------------------------------------------------------------ hotel status
status = []
dev_by = defaultdict(list)
for d in devices:
    dev_by[d['Hotel Code']].append(d)
al_by = Counter(a['Hotel Code'] for a in alerts)
for sc, s in sites.items():
    ds = dev_by[s['code']]
    on = sum(1 for d in ds if d['Status'] == 'Online')
    off = len(ds) - on
    cs = circ_by.get(sc, [])
    att = []
    if ds and off / len(ds) >= 0.25:
        att.append('%d of %d devices offline' % (off, len(ds)))
    elif off:
        att.append('%d devices offline' % off)
    att += ['%s %s circuit offline' % (c['WAN'], clean(c['Provider'])) for c in cs if c['Status'] != 'Online']
    if sc in util:
        att.append('circuit over 80%% on %s of last 30 days' % util[sc]['Days Over 80'])
    notes = [s['note']] if s['note'] else []
    if cs:
        notes.append('Circuits: ' + ', '.join('%s %s %s Mbps%s' % (c['WAN'], clean(c['Provider']), c['Download Speed'],
                                                                    '' if c['Status'] == 'Online' else ' (offline)') for c in cs))
    notes.append('BlueprintRF has no ticket list, EOL or firmware data in its portal.')
    status.append({
        'Scan Date': SCAN, 'Source': BP, 'Hotel Code': s['code'], 'Hotel Name': s['name'], 'Brand': s['brand'],
        'Fund': s['fund'], 'Vendor Site ID': s['id'], 'Vendor Health': '', 'Total Devices': len(ds), 'Online': on,
        'Offline or Down': off, 'Access Points': sum(1 for d in ds if d['Device Type'] == 'Access Point'),
        'Switches': sum(1 for d in ds if d['Device Type'] == 'Switch'),
        'Firewalls and Routers': sum(1 for d in ds if d['Device Type'] in ('Firewall', 'Gateway')),
        'Past EOL': '', 'EOL Within 12 Months': '', 'Active Alerts': al_by[s['code']], 'Open Tickets': '',
        'Tickets Last 90 Days': '', 'Tickets All Time': '',
        'Circuit Mbps': sum(float(c['Download Speed']) for c in cs) if cs else '',
        'Circuit 95th Pct Mbps': '', 'Circuit Pct Used': '', 'Contract Expiration': '', 'Contract Status': '',
        'Last Synced': PULLED, 'Attention': '; '.join(att), 'Notes': ' '.join(notes), 'Portal Link': PORTAL})

# ------------------------------------------------------------------ loader
import ast  # column lists come straight from build_network_scan.py so both builders always match
_cols = {}
for _n in ast.parse(open(os.path.join(BASE, 'build_network_scan.py')).read()).body:
    if isinstance(_n, ast.Assign) and isinstance(_n.targets[0], ast.Name) and _n.targets[0].id.endswith('_COLS'):
        try:
            _cols[_n.targets[0].id] = ast.literal_eval(_n.value)
        except ValueError:
            pass
STATUS_COLS, DEVICE_COLS, ALERT_COLS, TICKET_COLS = (_cols[k] for k in ('STATUS_COLS', 'DEVICE_COLS', 'ALERT_COLS', 'TICKET_COLS'))


def rows_of(recs, cols):
    return [[r.get(c, '') for c in cols] for r in recs]


gs_data = {'status': rows_of(status, STATUS_COLS), 'devices': rows_of(devices, DEVICE_COLS),
           'alerts': rows_of(alerts, ALERT_COLS), 'tickets': [], 'eol': []}
tpl = open(os.path.join(BASE, 'NetworkScanImport.template.gs')).read()
gs = (tpl.replace('__SCAN_DATE__', SCAN).replace('__MMDD__', SCAN[5:7] + SCAN[8:10])
      .replace('__SCAN_NICE__', nice_date(SCAN))
      .replace('__SOURCES__', json.dumps([BP])).replace('__SOURCE_TEXT__', BP)
      .replace('__STATUS_COLS__', json.dumps(STATUS_COLS)).replace('__DEVICE_COLS__', json.dumps(DEVICE_COLS))
      .replace('__ALERT_COLS__', json.dumps(ALERT_COLS)).replace('__TICKET_COLS__', json.dumps(TICKET_COLS))
      .replace('__DATA__', json.dumps(gs_data, ensure_ascii=True, separators=(',', ':'))))
leftover = re.findall(r'__[A-Z][A-Z_]+__', gs)
if leftover:
    raise SystemExit('Unfilled placeholders: %s' % sorted(set(leftover)))
open(os.path.join(OUT, 'NetworkScanImport.gs'), 'w').write(gs)
json.dump({'status': status, 'devices': devices, 'alerts': alerts}, open(os.path.join(OUT, 'blueprint-normalized.json'), 'w'))

print('hotels', len(status), 'devices', len(devices), 'offline', sum(1 for d in devices if d['Status'] != 'Online'),
      'alerts', len(alerts), Counter(a['Alert'] for a in alerts), 'sev', Counter(a['Severity'] for a in alerts))
print('not in Property Master:', [s['site'] for s in sites.values() if s['note']])
print('serial blank:', sum(1 for d in devices if not d['Serial Number']), 'location blank:', sum(1 for d in devices if not d['Location']))
