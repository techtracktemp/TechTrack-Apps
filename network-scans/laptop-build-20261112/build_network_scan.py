# C.Perry
# Chris Perry
"""Build the weekly hotel network scan outputs (Allbridge Skyway + Cloud5).

Inputs : network-scans/<date>/skyway/*.csv, network-scans/<date>/cloud5/*.csv,
         /tmp/tt/{pm,projects,updates,checklist}.json (TechTrack extracts)
Outputs: network-scans/<date>/out/
         NetworkScanImport.gs         TechTrack loader (Loader Hub: preview / apply)
         Network Scan Report <date>.html   The Works dashboard
         Network Scan <date>.xlsx          Blue Box workbook
         Network Scan Dashboard <date>.eml Email as dashboard (draft, unsent)
Usage  : python3 -I build_network_scan.py 2026-10-31
"""
import csv, json, re, sys, os, datetime as dt, collections, base64, html

SCAN = sys.argv[1] if len(sys.argv) > 1 else '2026-10-31'
BASE = os.path.dirname(os.path.abspath(__file__))
D = os.path.join(BASE, SCAN)
OUT = os.path.join(D, 'out')
os.makedirs(OUT, exist_ok=True)
TT = '/tmp/tt'
SCAN_D = dt.date.fromisoformat(SCAN)
MMDD = SCAN_D.strftime('%m%d')
MONTHS = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec']
SKY = 'Allbridge Skyway'
C5 = 'Cloud5'
C5_EXCLUDE = {'ABQAP'}          # Chris 10/31: Allbridge took over ABQAP, ignore it in Cloud5
SKY_EXCLUDE = {'ATLDN'}         # Chris 11/6: ATLDN left Allbridge (SafetyNet, Oct 2026 cutover); ignore it in Skyway
REDACT = re.compile(r'rebecca\s+shand|\brebecca\b|\bshand\b|rbavaro\S*', re.I)   # Chris 10/31: never name the shared login user


def nice_date(s):
    if not s:
        return ''
    try:
        d = dt.date.fromisoformat(str(s)[:10])
    except ValueError:
        return str(s)
    return '%s %d %d' % (MONTHS[d.month - 1], d.day, d.year)


def clean(v):
    if v is None:
        return ''
    s = str(v).replace('—', ' - ').replace('–', ' - ').replace('﻿', '')
    s = REDACT.sub('', s)
    s = re.sub(r'[ \t]{2,}', ' ', s).strip()
    return '' if s.lower() in ('n/a', 'none', 'null', 'not available') else s


def rd(p):
    with open(p, encoding='utf-8-sig') as f:
        return list(csv.DictReader(f))


def iso_mdy(s):
    """'10/28/2026 3:26 PM' -> '2026-10-28 15:26'"""
    s = clean(s)
    if not s or s == '-':
        return ''
    for fmt in ('%m/%d/%Y %I:%M %p', '%m/%d/%Y'):
        try:
            return dt.datetime.strptime(s, fmt).strftime('%Y-%m-%d %H:%M')
        except ValueError:
            pass
    return s


def iso_dmy(s):
    s = clean(s)
    try:
        return dt.datetime.strptime(s, '%d-%m-%Y').strftime('%Y-%m-%d')
    except ValueError:
        return s


def kbps_to_mbps(s):
    m = re.match(r'(\d+)', clean(s))
    return round(int(m.group(1)) / 1024) if m else ''


def hms_text(s):
    """'00:07:00' -> '7 min', '26:30:00' -> '26 hr 30 min'. Plain text so Sheets keeps it as text."""
    s = clean(s)
    m = re.match(r'^(\d+):(\d{2})(?::(\d{2}))?$', s)
    if not m:
        return '' if s == '-' else s
    h, mi = int(m.group(1)), int(m.group(2))
    if h and mi:
        return '%d hr %d min' % (h, mi)
    if h:
        return '%d hr' % h
    return '%d min' % max(mi, 1 if (m.group(3) and int(m.group(3))) else 0)


# ------------------------------------------------------------------ TechTrack context
pm = json.load(open(os.path.join(TT, 'pm.json')))
PM = {p['Hotel Code']: p for p in pm if p.get('Hotel Code')}
projects = json.load(open(os.path.join(TT, 'projects.json')))
updates = json.load(open(os.path.join(TT, 'updates.json')))
checklist = json.load(open(os.path.join(TT, 'checklist.json')))

# ------------------------------------------------------------------ hotel map
sky_props = rd(os.path.join(D, 'skyway/skyway-properties.csv'))
SKY_OVERRIDE = {'7988': 'MSPMD'}      # Chambers: TechTrack code NOCODE, Cloud5 code MSPMD
TT_NAME_OVERRIDE = {'MSPMD': 'Chambers Minneapolis MN', 'PSDNA': 'Pasadena Hotel and Pool CA'}


def sky_code(p):
    if p['property_id'] in SKY_OVERRIDE:
        return SKY_OVERRIDE[p['property_id']]
    w = p.get('website') or ''
    for rx in (r'/travel/([a-z]{5})-', r'/hotels/([a-z]{5})-', r'/hotels/([a-z]{5})[a-z]{2}-'):
        m = re.search(rx, w)
        if m:
            return m.group(1).upper()
    for c, x in PM.items():
        if x.get('Address') and p['address'] and str(x['Address']).split()[0] == p['address'].split()[0] \
                and str(x.get('City') or '').lower() == p['city'].lower() and len(c) == 5:
            return c
    return ''


def tt_info(code, vendor_name):
    x = PM.get(code)
    name = TT_NAME_OVERRIDE.get(code) or (x['Hotel Name'] if x else '')
    if not x and name:
        x = next((p for p in pm if p['Hotel Name'] == name), None)
    note = '' if x else 'Not in TechTrack Property Master'
    return {
        'code': code, 'tt_name': name or vendor_name, 'vendor_name': vendor_name,
        'brand': (x or {}).get('Brand') or '', 'fund': (x or {}).get('Fund') or '',
        'gm': (x or {}).get('General Manager Email') or '', 'note': note,
    }


HOTELS = {}   # (source, site_id) -> info
for p in sky_props:
    code = sky_code(p)
    h = tt_info(code, p['name'])
    if not h['brand']:
        h['brand'] = p['brand']
    h.update(source=SKY, site_id=p['property_id'], portal=p['portal_url'], health=p['health'],
             slug=p['slug'], rooms=p['room_count'])
    HOTELS[(SKY, p['property_id'])] = h
SKY_BY_NAME = {p['name']: (SKY, p['property_id']) for p in sky_props}
SKY_BY_SLUG = {p['slug']: (SKY, p['property_id']) for p in sky_props}

c5_locs = [l for l in rd(os.path.join(D, 'cloud5/cloud5-locations.csv')) if l['location_code'] not in C5_EXCLUDE]
for l in c5_locs:
    h = tt_info(l['location_code'], l['location'])
    h.update(source=C5, site_id=l['cloud5_location_id'], portal='https://dashboard.cloud5.com', health='',
             slug='', rooms='')
    HOTELS[(C5, l['cloud5_location_id'])] = h
C5_BY_CODE = {l['location_code']: (C5, l['cloud5_location_id']) for l in c5_locs}

# ------------------------------------------------------------------ devices
devices = []
for d in rd(os.path.join(D, 'skyway/skyway-device-inventory.csv')):
    h = HOTELS[(SKY, d['property_id'])]
    eol = clean(d['end_of_life'])
    flag = ''
    if eol:
        if eol < SCAN:
            flag = 'Past EOL'
        elif eol < (SCAN_D + dt.timedelta(days=365)).isoformat():
            flag = 'EOL within 12 months'
    devices.append({
        'Source': SKY, 'Hotel Code': h['code'], 'Hotel Name': h['tt_name'], 'Vendor Site ID': h['site_id'],
        'Device Name': clean(d['device_name']), 'Device Type': clean(d['device_type']),
        'Manufacturer': clean(d['model']).split(' ')[0] if clean(d['model']) else '',
        'Model': clean(d['vendor_model']) or clean(d['model']), 'Serial Number': clean(d['serial_number']),
        'MAC Address': '', 'IP Address': clean(d['ip_addresses']),
        'Firmware': clean(d['firmware_version']) or clean(d['software_version']),
        'Status': clean(d['status']).title(), 'Install Date': '', 'End of Sale': clean(d['end_of_sale']),
        'End of Life': eol, 'EOL Flag': flag, 'Switch Port': '', 'Location': '',
        'Vendor Device ID': 'SKY-' + d['skyway_device_id'], 'Last Synced': clean(d['last_synced'])[:16],
    })
c5_inv = [d for d in rd(os.path.join(D, 'cloud5/cloud5-asset-inventory.csv')) if d['location_code'] not in C5_EXCLUDE]
seen = collections.Counter()
for d in c5_inv:
    h = HOTELS[C5_BY_CODE[d['location_code']]]
    inst = clean(d['Date Installed'])
    if inst.startswith('1969') or inst.startswith('1970'):
        inst = ''
    key = 'C5-%s-%s' % (d['location_code'], clean(d['Serial Number']) or clean(d['MAC Address']))
    seen[key] += 1
    if seen[key] > 1:
        key += '-%d' % seen[key]
    loc = ' / '.join(x for x in (clean(d['Floor Installed']), clean(d['Installation Location'])) if x)
    devices.append({
        'Source': C5, 'Hotel Code': h['code'], 'Hotel Name': h['tt_name'], 'Vendor Site ID': h['site_id'],
        'Device Name': clean(d['Device Name']), 'Device Type': clean(d['Device Type']),
        'Manufacturer': clean(d['Manufacturer']), 'Model': clean(d['Device Model']),
        'Serial Number': clean(d['Serial Number']), 'MAC Address': clean(d['MAC Address']),
        'IP Address': clean(d['IP Address']), 'Firmware': '', 'Status': clean(d['Status']),
        'Install Date': inst, 'End of Sale': '', 'End of Life': '', 'EOL Flag': '',
        'Switch Port': clean(d['Switch Name/Port #']), 'Location': loc,
        'Vendor Device ID': key, 'Last Synced': '',
    })

# ------------------------------------------------------------------ alerts
alerts = []
for a in rd(os.path.join(D, 'skyway/skyway-device-alerts-unresolved.csv')):
    h = HOTELS[SKY_BY_NAME[a['property']]]
    alerts.append({
        'Source': SKY, 'Hotel Code': h['code'], 'Hotel Name': h['tt_name'], 'Alert ID': 'SKY-' + a['alert_id'],
        'Alert': clean(a['alert']), 'Severity': int(a['severity'] or 0), 'Detected': clean(a['detected_at'])[:16],
        'Device Name': clean(a['device_name']), 'Model': clean(a['device_model']),
        'Serial Number': clean(a['device_serial']), 'Device Status': clean(a['device_status']).title(),
        'Description': clean(a['description']).replace('AP [] has', 'AP has'),
    })
for a in rd(os.path.join(D, 'cloud5/cloud5-devices-down.csv')):
    if a['location_code'] in C5_EXCLUDE:
        continue
    h = HOTELS[C5_BY_CODE[a['location_code']]]
    alerts.append({
        'Source': C5, 'Hotel Code': h['code'], 'Hotel Name': h['tt_name'],
        'Alert ID': 'C5-%s-%s' % (a['location_code'], clean(a['Device Name'])),
        'Alert': 'Device Down', 'Severity': 3, 'Detected': clean(a['Last Status Change'])[:16],
        'Device Name': clean(a['Device Name']), 'Model': '', 'Serial Number': '',
        'Device Status': clean(a['Current Status']).title(),
        'Description': 'Down since %s' % nice_date(a['Last Status Change'][:10]),
    })

# ------------------------------------------------------------------ tickets
tickets = []
for t in rd(os.path.join(D, 'skyway/skyway-tickets-all-history.csv')):
    k = SKY_BY_SLUG.get(t['slug']) or SKY_BY_NAME.get(t['property'])
    h = HOTELS[k] if k else {'code': '', 'tt_name': clean(t['property'])}
    tickets.append({
        'Source': SKY, 'Ticket #': clean(t['case_number']), 'Hotel Code': h['code'], 'Hotel Name': h['tt_name'],
        'Opened': clean(t['created'])[:16], 'Closed': '', 'Status': clean(t['status']),
        'Open': 'No' if t['is_closed'] == 'True' else 'Yes', 'Priority': clean(t['priority']),
        'Type': clean(t['type']), 'Category': clean(t['origin']), 'Subject': clean(t['subject']),
        'Contact': clean(t['contact']), 'Agent': '',
        'Resolution Time': (clean(t['time_to_resolution']) + ' hrs') if clean(t['time_to_resolution']) else '',
        'Issue': '', 'Repair': '',
    })
for t in rd(os.path.join(D, 'cloud5/cloud5-portal-export_tickets_all.csv')):
    code = clean(t['Property/User ID'])
    if code in C5_EXCLUDE:
        continue
    h = HOTELS[C5_BY_CODE[code]] if code in C5_BY_CODE else {'code': code, 'tt_name': clean(t['Property/User'])}
    state = clean(t['Ticket State'])
    tickets.append({
        'Source': C5, 'Ticket #': clean(t['Ticket #']), 'Hotel Code': h['code'], 'Hotel Name': h['tt_name'],
        'Opened': iso_mdy(t['Opened']), 'Closed': iso_mdy(t['Closed']), 'Status': state,
        'Open': 'No' if state == 'Resolved' else 'Yes', 'Priority': clean(t['Priority']),
        'Type': clean(t['Product Type']), 'Category': clean(t['Category']), 'Subject': clean(t['Subject']),
        'Contact': '', 'Agent': clean(t['Agent Name']),
        'Resolution Time': hms_text(t['Resolution Time']),
        'Issue': clean(t['Issue']), 'Repair': clean(t['Repair']),
    })
D90 = (SCAN_D - dt.timedelta(days=90)).isoformat()


def dedupe(recs, key, suffix=False):
    """Keep one row per key. suffix=True keeps every row and numbers repeats (same device name twice)."""
    out, seen_k = [], collections.Counter()
    for r in recs:
        k = tuple(r[x] for x in key)
        seen_k[k] += 1
        if seen_k[k] > 1:
            if not suffix:
                continue
            r[key[-1]] = '%s-%d' % (r[key[-1]] or 'blank', seen_k[k])
        out.append(r)
    return out


alerts = dedupe(alerts, ('Source', 'Alert ID'), suffix=True)
tickets = dedupe(tickets, ('Source', 'Ticket #'))
devices = dedupe(devices, ('Source', 'Vendor Device ID'))
_sky_out = lambda r: r['Source'] == SKY and r.get('Hotel Code') in SKY_EXCLUDE
alerts = [r for r in alerts if not _sky_out(r)]
tickets = [r for r in tickets if not _sky_out(r)]
devices = [r for r in devices if not _sky_out(r)]

# ------------------------------------------------------------------ hotel status rows
sky_status = {s['property']: s for s in rd(os.path.join(D, 'skyway/skyway-device-status-by-property.csv'))}
circ = {c['MARSHA Code']: c for c in rd(os.path.join(D, 'cloud5/cloud5-circuits-aug-oct2026.csv'))}
contracts = {c['Marsha Code']: c for c in rd(os.path.join(D, 'cloud5/cloud5-installed-base-contracts.csv'))}
by_hotel_t = collections.defaultdict(list)
for t in tickets:
    by_hotel_t[(t['Source'], t['Hotel Code'] or t['Hotel Name'])].append(t)
by_hotel_a = collections.defaultdict(list)
for a in alerts:
    by_hotel_a[(a['Source'], a['Hotel Code'] or a['Hotel Name'])].append(a)
by_hotel_d = collections.defaultdict(list)
for d in devices:
    by_hotel_d[(d['Source'], d['Hotel Code'] or d['Hotel Name'])].append(d)

status = []
for (src, sid), h in HOTELS.items():
    if src == SKY and h['code'] in SKY_EXCLUDE:
        continue
    k = (src, h['code'] or h['tt_name'])
    tk, al, dv = by_hotel_t[k], by_hotel_a[k], by_hotel_d[k]
    row = {
        'Scan Date': SCAN, 'Source': src, 'Hotel Code': h['code'], 'Hotel Name': h['tt_name'],
        'Brand': h['brand'], 'Fund': h['fund'], 'Vendor Site ID': sid, 'Vendor Health': h['health'].title(),
        'Total Devices': 0, 'Online': 0, 'Offline or Down': 0, 'Access Points': 0, 'Switches': 0,
        'Firewalls and Routers': 0, 'Past EOL': 0, 'EOL Within 12 Months': 0,
        'Active Alerts': len(al), 'Open Tickets': sum(1 for t in tk if t['Open'] == 'Yes'),
        'Tickets Last 90 Days': sum(1 for t in tk if t['Opened'][:10] >= D90), 'Tickets All Time': len(tk),
        'Circuit Mbps': '', 'Circuit 95th Pct Mbps': '', 'Circuit Pct Used': '',
        'Contract Expiration': '', 'Contract Status': '', 'Last Synced': '', 'Portal Link': h['portal'],
        'Attention': '', 'Notes': h['note'],
    }
    if src == SKY:
        s = sky_status.get(h['vendor_name'])
        if s:
            row.update({'Total Devices': int(s['total_devices']), 'Online': int(s['online']),
                        'Offline or Down': int(s['offline']) + int(s['unreachable']),
                        'Access Points': int(s['access_points']), 'Switches': int(s['switches']),
                        'Firewalls and Routers': int(s['routers_firewalls']), 'Past EOL': int(s['past_eol']),
                        'EOL Within 12 Months': sum(1 for d in dv if d['EOL Flag'] == 'EOL within 12 months'),
                        'Last Synced': s['last_synced'][:16]})
        else:
            row['Notes'] = '; '.join(x for x in (row['Notes'], 'No device monitoring (Auvik) linked in Skyway') if x)
    else:
        l = next(x for x in c5_locs if x['location_code'] == h['code'])
        mon, down = int(l['monitored_devices'] or 0), int(l['devices_down'] or 0)
        row.update({'Total Devices': max(int(l['assets'] or 0), mon), 'Online': max(mon - down, 0), 'Offline or Down': down,
                    'Access Points': sum(1 for d in dv if d['Device Type'] == 'Access Point'),
                    'Switches': sum(1 for d in dv if d['Device Type'] in ('Managed PoE', 'Managed')),
                    'Firewalls and Routers': sum(1 for d in dv if d['Device Type'] in ('Firewall',))})
        c = circ.get(h['code'])
        if c:
            row.update({'Circuit Mbps': kbps_to_mbps(c['Circuit Size']),
                        'Circuit 95th Pct Mbps': kbps_to_mbps(c['95th Percentile']),
                        'Circuit Pct Used': int(re.sub(r'\D', '', c['% BUI']) or 0)})
        ct = contracts.get(h['code'])
        if ct:
            exp = iso_dmy(ct['Latest contract expiration date'])
            row['Contract Expiration'] = exp
            row['Contract Status'] = 'Expired' if exp < SCAN else ('Expires within 12 months' if exp < (SCAN_D + dt.timedelta(days=365)).isoformat() else 'Active')
    att = []
    if row['Past EOL']:
        att.append('%d past EOL' % row['Past EOL'])
    if row['Total Devices'] and row['Offline or Down'] >= max(5, 0.1 * row['Total Devices']):
        att.append('%d devices offline' % row['Offline or Down'])
    if any(a['Severity'] <= 2 for a in al):
        att.append('%d severity 2 alerts' % sum(1 for a in al if a['Severity'] <= 2))
    if row['Circuit Pct Used'] != '' and row['Circuit Pct Used'] >= 80:
        att.append('circuit at %d%%' % row['Circuit Pct Used'])
    if row['Contract Status'] == 'Expired':
        att.append('support contract expired')
    if src == SKY and not row['Total Devices']:
        att.append('no device monitoring')
    row['Attention'] = '; '.join(att)
    status.append(row)
status.sort(key=lambda r: (-(r['Offline or Down']), r['Hotel Name']))

eol_rows = [d for d in devices if d['EOL Flag']]

# ------------------------------------------------------------------ TechTrack context per hotel
codes = {h['code'] for h in HOTELS.values() if h['code']}
open_proj = [p for p in projects if p.get('Hotel Code') in codes and str(p.get('Project Status') or '') not in ('Complete', 'Completed', 'Cancel', 'Cancelled')]
upd_by = collections.defaultdict(list)
for u in updates:
    upd_by[u['Hotel Code']].append(u)
for k in upd_by:
    upd_by[k].sort(key=lambda u: str(u['Timestamp']), reverse=True)
ck_state = {}
for c in sorted(checklist, key=lambda c: str(c.get('Timestamp'))):
    ck_state[(c['Hotel Code'], c['Project'], c['Stage'], c['Item'])] = c['Checked'] in (True, 'TRUE', 'true')
ck_by = collections.defaultdict(lambda: [0, 0])
for (code, proj, stage, item), v in ck_state.items():
    ck_by[(code, proj)][1] += 1
    ck_by[(code, proj)][0] += 1 if v else 0
proj_rows = []
for p in open_proj:
    done, tot = ck_by.get((p['Hotel Code'], p['Project Name']), (0, 0))
    last = next((u for u in upd_by.get(p['Hotel Code'], []) if str(u.get('Project ID') or '') in (p['Project Name'], p['Hotel Code'])), None)
    proj_rows.append({
        'Hotel Code': p['Hotel Code'], 'Hotel Name': p.get('Hotel Name') if isinstance(p.get('Hotel Name'), str) and not str(p.get('Hotel Name')).startswith('<') else (PM.get(p['Hotel Code'], {}).get('Hotel Name') or ''),
        'Project': clean(p.get('Project Name')), 'Vendor': clean(p.get('Vendor')), 'Status': clean(p.get('Project Status')),
        'Payment Status': clean(p.get('Payment Status')), 'Cost': p.get('Cost') if isinstance(p.get('Cost'), (int, float)) else 0,
        'Coupa Capex': {'text': clean(p.get('Coupa Capex Link')), 'url': p.get('Coupa Capex Link URL')} if p.get('Coupa Capex Link URL') else clean(p.get('Coupa Capex Link')),
        'Coupa Requisition': {'text': clean(p.get('Coupa Requisition Link')), 'url': p.get('Coupa Requisition Link URL')} if p.get('Coupa Requisition Link URL') else clean(p.get('Coupa Requisition Link')),
        'Checklist': ('%d of %d (%d%%)' % (done, tot, round(100 * done / tot))) if tot else 'No checklist yet',
        'Latest Project Update': (nice_date(str(last['Timestamp'])[:10]) + ': ' + clean(last['Update'])[:220]) if last else '',
    })
proj_rows.sort(key=lambda r: -r['Cost'])
hotel_updates = {code: [{'Date': nice_date(str(u['Timestamp'])[:10]), 'Project': clean(u.get('Project ID')), 'Update': clean(u.get('Update'))[:300]} for u in upd_by.get(code, [])[:3]] for code in codes}

# ------------------------------------------------------------------ trend (tickets per month, last 12 months)
months = []
y, m = SCAN_D.year, SCAN_D.month
for i in range(12):
    months.append('%04d-%02d' % (y, m))
    m -= 1
    if m == 0:
        y, m = y - 1, 12
months.reverse()
trend = []
for src in (SKY, C5):
    r = {'Source': src}
    for mo in months:
        r[MONTHS[int(mo[5:]) - 1] + ' ' + mo[:4]] = sum(1 for t in tickets if t['Source'] == src and t['Opened'][:7] == mo)
    trend.append(r)

# ------------------------------------------------------------------ numbers for the summary
N = {
    'sky_hotels': sum(1 for h in HOTELS.values() if h['source'] == SKY),
    'c5_hotels': len(c5_locs),
    'sky_devices': sum(1 for d in devices if d['Source'] == SKY),
    'c5_devices': sum(1 for d in devices if d['Source'] == C5),
    'sky_with_dev': sum(1 for r in status if r['Source'] == SKY and r['Total Devices']),
    'past_eol': sum(1 for d in devices if d['EOL Flag'] == 'Past EOL'),
    'eol_12': sum(1 for d in devices if d['EOL Flag'] == 'EOL within 12 months'),
    'alerts': len(alerts), 'sev2': sum(1 for a in alerts if a['Severity'] <= 2),
    'c5_down': sum(1 for a in alerts if a['Source'] == C5),
    'open_t': sum(1 for t in tickets if t['Open'] == 'Yes'),
    't90': sum(1 for t in tickets if t['Opened'][:10] >= D90),
    'tall': len(tickets),
    'expired': [r['Hotel Code'] for r in status if r['Contract Status'] == 'Expired'],
    'hot_circ': [(r['Hotel Code'], r['Circuit Pct Used'], r['Circuit 95th Pct Mbps'], r['Circuit Mbps']) for r in status if r['Circuit Pct Used'] != '' and r['Circuit Pct Used'] >= 80],
    'proj': len(proj_rows),
}
top_down = [r for r in status if r['Offline or Down']][:4]
SUMMARY = [
    'Scanned %d hotels across Allbridge Skyway (%d) and Cloud5 (%d), covering %s devices and %s vendor tickets. ABQAP is counted under Allbridge only, and the Chambers (MSPMD) is in both portals.' % (
        len({h['code'] or h['tt_name'] for h in HOTELS.values()}), N['sky_hotels'], N['c5_hotels'], format(sum(r['Total Devices'] for r in status), ','), format(N['tall'], ',')),
    '%d devices are past end of life and %d more reach end of life within 12 months, all at TownePlace Suites Corpus Christi (CRPTS). They are being added to the TechTrack End of Life Devices tab.' % (N['past_eol'], N['eol_12']),
    'Offline devices are concentrated at %s.' % ', '.join('%s (%d)' % (r['Hotel Code'] or r['Hotel Name'], r['Offline or Down']) for r in top_down) + ' The Aloft Atlanta count is mostly stale "Unknown" Auvik entries, not an outage.',
    '%d active alerts (%d severity 2) and %d open vendor tickets. %d tickets were opened in the last 90 days.' % (N['alerts'], N['sev2'], N['open_t'], N['t90']),
    'Cloud5 support contracts show expired at %s. TUSTA is running its circuit at %d%% (%d of %d Mbps at the 95th percentile).' % (', '.join(N['expired']), N['hot_circ'][0][1], N['hot_circ'][0][2], N['hot_circ'][0][3]) if N['hot_circ'] else
    'Cloud5 support contracts show expired at %s.' % ', '.join(N['expired']),
    'Only %d of %d Skyway hotels have device monitoring linked, so the other %d show no devices. That is worth raising with Allbridge.' % (N['sky_with_dev'], N['sky_hotels'], N['sky_hotels'] - N['sky_with_dev']),
]

json.dump({'status': status, 'devices': devices, 'alerts': alerts, 'tickets': tickets, 'eol': eol_rows,
           'projects': proj_rows, 'trend': trend, 'summary': SUMMARY, 'N': N},
          open(os.path.join(OUT, 'normalized.json'), 'w'), default=str)
print('hotels', len(status), 'devices', len(devices), 'alerts', len(alerts), 'tickets', len(tickets),
      'eol', len(eol_rows), 'projects', len(proj_rows))
for s in SUMMARY:
    print(' -', s)

# ================================================================== column specs
STATUS_COLS = ['Scan Date', 'Source', 'Hotel Code', 'Hotel Name', 'Brand', 'Fund', 'Vendor Site ID', 'Vendor Health',
               'Total Devices', 'Online', 'Offline or Down', 'Access Points', 'Switches', 'Firewalls and Routers',
               'Past EOL', 'EOL Within 12 Months', 'Active Alerts', 'Open Tickets', 'Tickets Last 90 Days',
               'Tickets All Time', 'Circuit Mbps', 'Circuit 95th Pct Mbps', 'Circuit Pct Used',
               'Contract Expiration', 'Contract Status', 'Last Synced', 'Attention', 'Notes', 'Portal Link']
DEVICE_COLS = ['Source', 'Hotel Code', 'Hotel Name', 'Device Name', 'Device Type', 'Manufacturer', 'Model',
               'Serial Number', 'MAC Address', 'IP Address', 'Firmware', 'Status', 'Install Date', 'End of Sale',
               'End of Life', 'EOL Flag', 'Switch Port', 'Location', 'Vendor Site ID', 'Vendor Device ID', 'Last Synced']
ALERT_COLS = ['Source', 'Hotel Code', 'Hotel Name', 'Alert', 'Severity', 'Detected', 'Device Name', 'Model',
              'Serial Number', 'Device Status', 'Description', 'Alert ID']
TICKET_COLS = ['Source', 'Ticket #', 'Hotel Code', 'Hotel Name', 'Opened', 'Closed', 'Status', 'Open', 'Priority',
               'Type', 'Category', 'Subject', 'Contact', 'Agent', 'Resolution Time', 'Issue', 'Repair']

# ================================================================== Apps Script loader
def rows_of(recs, cols):
    return [[r.get(c, '') if not isinstance(r.get(c), dict) else r[c].get('text', '') for c in cols] for r in recs]


gs_data = {
    'status': rows_of(status, STATUS_COLS), 'devices': rows_of(devices, DEVICE_COLS),
    'alerts': rows_of(alerts, ALERT_COLS), 'tickets': rows_of(tickets, TICKET_COLS),
    'eol': [{'Hotel Code': d['Hotel Code'], 'Device Class': d['Device Type'], 'Name': d['Device Name'],
             'Serial Number': d['Serial Number'], 'Model Display Name': d['Model'], 'HW EOL Date': d['End of Life'],
             'IP Address': d['IP Address'], 'Status': 'In use' if d['Status'] == 'Online' else d['Status'],
             'Last Discovered': d['Last Synced'][:10],
             'Notes': '%s (%s Skyway scan %s, End of Sale %s)' % (d['EOL Flag'], SKY.split()[0], nice_date(SCAN), nice_date(d['End of Sale']))}
            for d in eol_rows],
}
GS_TEMPLATE = open(os.path.join(BASE, 'NetworkScanImport.template.gs')).read()
gs = (GS_TEMPLATE.replace('__SCAN_DATE__', SCAN).replace('__MMDD__', MMDD)
      .replace('__SCAN_NICE__', nice_date(SCAN))
      .replace('__SOURCES__', json.dumps(['Allbridge Skyway', 'Cloud5'])).replace('__SOURCE_TEXT__', 'Allbridge Skyway and Cloud5')
      .replace('__STATUS_COLS__', json.dumps(STATUS_COLS)).replace('__DEVICE_COLS__', json.dumps(DEVICE_COLS))
      .replace('__ALERT_COLS__', json.dumps(ALERT_COLS)).replace('__TICKET_COLS__', json.dumps(TICKET_COLS))
      .replace('__DATA__', json.dumps(gs_data, ensure_ascii=True, separators=(',', ':'))))
assert '—' not in gs and '–' not in gs
gs_path = os.path.join(OUT, 'NetworkScanImport.gs')   # fixed name for the Loader Hub
open(gs_path, 'w').write(gs)
print('wrote', gs_path, len(gs))

# ================================================================== shared table specs (report, xlsx, eml)
def col(key, t='text', **kw):
    d = {'key': key, 'header': key, 'type': t}
    d.update(kw)
    return d


STATUS_VIEW = [col('Source'), col('Hotel Code'), col('Hotel Name'), col('Brand'), col('Fund'), col('Vendor Health', 'status'),
               col('Total Devices', 'number', sum=True), col('Online', 'number', sum=True),
               col('Offline or Down', 'number', sum=True), col('Past EOL', 'number', sum=True),
               col('EOL Within 12 Months', 'number', sum=True), col('Active Alerts', 'number', sum=True),
               col('Open Tickets', 'number', sum=True), col('Tickets Last 90 Days', 'number', sum=True),
               col('Circuit Pct Used', 'pct'), col('Contract Status', 'status'), col('Attention'), col('Portal', 'link')]
for r in status:
    r['Portal'] = {'text': 'Open portal', 'url': r['Portal Link']}
TABLES = [
    {'id': 'status', 'name': 'Hotel Status', 'title': 'Hotel Network Status by Source', 'columns': STATUS_VIEW,
     'rows': status, 'sortKey': 'Offline or Down', 'sortDir': 'desc', 'expand': True},
    {'id': 'alerts', 'name': 'Active Alerts', 'title': 'Active Alerts and Devices Down',
     'columns': [col('Source'), col('Hotel Code'), col('Hotel Name'), col('Alert'), col('Severity', 'number'),
                 col('Detected', 'date'), col('Device Name'), col('Model'), col('Device Status', 'status'), col('Description')],
     'rows': alerts, 'sortKey': 'Severity', 'sortDir': 'asc'},
    {'id': 'open', 'name': 'Open Tickets', 'title': 'Open Vendor Tickets',
     'columns': [col('Source'), col('Ticket #'), col('Hotel Code'), col('Hotel Name'), col('Opened', 'date'),
                 col('Status', 'status'), col('Priority'), col('Type'), col('Subject'), col('Contact'), col('Agent')],
     'rows': [t for t in tickets if t['Open'] == 'Yes'], 'sortKey': 'Opened', 'sortDir': 'desc'},
    {'id': 'eol', 'name': 'EOL Devices', 'title': 'Devices Past or Near End of Life',
     'columns': [col('Source'), col('Hotel Code'), col('Hotel Name'), col('Device Name'), col('Device Type'),
                 col('Model'), col('Serial Number'), col('Status', 'status'), col('End of Sale', 'date'),
                 col('End of Life', 'date'), col('EOL Flag', 'status')],
     'rows': eol_rows, 'sortKey': 'End of Life', 'sortDir': 'asc'},
    {'id': 'circuits', 'name': 'Circuits and Contracts', 'title': 'Cloud5 Circuits and Support Contracts',
     'columns': [col('Hotel Code'), col('Hotel Name'), col('Brand'), col('Circuit Mbps', 'number'),
                 col('Circuit 95th Pct Mbps', 'number'), col('Circuit Pct Used', 'pct'),
                 col('Contract Expiration', 'date'), col('Contract Status', 'status')],
     'rows': [dict(r, **{'Source': C5}) for r in status if r['Source'] == C5], 'sortKey': 'Circuit Pct Used', 'sortDir': 'desc'},
    {'id': 'projects', 'name': 'TechTrack Projects', 'title': 'Open TechTrack Projects at Scanned Hotels',
     'columns': [col('Hotel Code'), col('Hotel Name'), col('Project'), col('Vendor'), col('Status', 'status'),
                 col('Payment Status', 'status'), col('Cost', 'money', sum=True), col('Coupa Capex', 'link'),
                 col('Coupa Requisition', 'link'), col('Checklist'), col('Latest Project Update')],
     'rows': proj_rows, 'sortKey': 'Cost', 'sortDir': 'desc'},
    {'id': 'trend', 'name': 'Ticket Trend', 'title': 'Vendor Tickets Opened per Month (Last 12 Months)',
     'columns': [col('Source')] + [col(k, 'number', sum=True) for k in trend[0] if k != 'Source'],
     'rows': trend, 'sortKey': 'Source', 'sortDir': 'asc', 'nofilter_hotel': True},
    {'id': 'devices', 'name': 'All Devices', 'title': 'All Network Devices', 'collapsed': True,
     'columns': [col(c, 'date' if c in ('Install Date', 'End of Sale', 'End of Life') else 'text') for c in
                 ['Source', 'Hotel Code', 'Hotel Name', 'Device Name', 'Device Type', 'Manufacturer', 'Model',
                  'Serial Number', 'MAC Address', 'IP Address', 'Status', 'Install Date', 'End of Life', 'Switch Port']],
     'rows': devices, 'sortKey': 'Hotel Name', 'sortDir': 'asc'},
    {'id': 'tickets90', 'name': 'Tickets 90 Days', 'title': 'Vendor Tickets Opened in the Last 90 Days', 'collapsed': True,
     'columns': [col('Source'), col('Ticket #'), col('Hotel Code'), col('Hotel Name'), col('Opened', 'date'),
                 col('Status', 'status'), col('Type'), col('Category'), col('Subject'), col('Resolution Time')],
     'rows': [t for t in tickets if t['Opened'][:10] >= D90], 'sortKey': 'Opened', 'sortDir': 'desc'},
]
for t in TABLES:
    for r in t['rows']:
        r.setdefault('Source', '')
        h = PM.get(r.get('Hotel Code')) or {}
        r.setdefault('Brand', h.get('Brand') or '')
        r.setdefault('Fund', h.get('Fund') or '')

PREPARED = ['Prepared by: Chris Perry using TechTrack', 'Date Prepared: %s' % nice_date(SCAN),
            'Data accurate as of: %s, 12 PM CT' % nice_date(SCAN),
            'Source: Allbridge Skyway portal, Cloud5 portal, TechTrack (Property Master, Tech Projects Tracker, Project Updates, Workflow Checklist Log)']
TITLE = 'Hotel Network Scan - Allbridge Skyway and Cloud5 - %s' % nice_date(SCAN)
DETAILS = {}
for (src, sid), h in HOTELS.items():
    k = src + '|' + sid
    key = (src, h['code'] or h['tt_name'])
    DETAILS[k] = {
        'alerts': [{x: a[x] for x in ('Alert', 'Severity', 'Detected', 'Device Name', 'Description')} for a in by_hotel_a[key]][:25],
        'open': [{x: t[x] for x in ('Ticket #', 'Opened', 'Status', 'Subject')} for t in by_hotel_t[key] if t['Open'] == 'Yes'],
        'eol': [{x: d[x] for x in ('Device Name', 'Model', 'End of Life', 'EOL Flag')} for d in by_hotel_d[key] if d['EOL Flag']],
        'projects': [{x: (p[x] if not isinstance(p[x], dict) else p[x]) for x in ('Project', 'Status', 'Coupa Capex', 'Coupa Requisition', 'Checklist')} for p in proj_rows if p['Hotel Code'] == h['code']],
        'updates': hotel_updates.get(h['code'], []),
        'gm': h['gm'],
    }
for r in status:
    r['_k'] = r['Source'] + '|' + r['Vendor Site ID']

spec = {'title': TITLE, 'summary': SUMMARY, 'tables': TABLES, 'prepared': PREPARED, 'details': DETAILS, 'scan': SCAN,
        'fileName': 'Network Scan %s' % SCAN}
open(os.path.join(OUT, 'report_spec.json'), 'w').write(json.dumps(spec, default=str))

# ================================================================== HTML report
bbx = open('/mnt/project-files/techtrack-excel/BlueBoxXlsx.html').read()
bbx = bbx[bbx.index('<script>'):]          # drop the header comment and CDN tag
# 10/30 rule: Prepared by block once, on the Summary sheet only, with the Data accurate as of line.
bbx = bbx.replace("function footer_(ws, startRow, src, defs) {\n    var r = startRow;",
                  "function footer_(ws, startRow, src, defs, prep) {\n    var r = startRow;")
bbx = bbx.replace("    ws.getCell(r, 1).value = 'Prepared by: ' + PREPARED_BY;",
                  "    if (!prep) return;\n    ws.getCell(r, 1).value = 'Prepared by: ' + PREPARED_BY;")
bbx = bbx.replace("    ws.getCell(r, 1).value = 'Prepared on: ' + today();\n    ws.getCell(r, 1).font = { name: FONT, size: 11, italic: true };",
                  "    ws.getCell(r, 1).value = 'Date Prepared: ' + today();\n    ws.getCell(r, 1).font = { name: FONT, size: 11, italic: true }; r++;\n"
                  "    ws.getCell(r, 1).value = 'Data accurate as of: ' + (window.SPEC ? SPEC.prepared[2].replace(/^Data accurate as of:\\s*/, '') : today());\n"
                  "    ws.getCell(r, 1).font = { name: FONT, size: 11, italic: true };")
bbx = bbx.replace("footer_(ws, r + 2, spec.source, spec.definitions);", "footer_(ws, r + 2, spec.source, spec.definitions, true);")
assert bbx.count('prep') >= 3, 'footer patch failed'
xl = open(os.path.join(BASE, 'vendor/exceljs.min.js')).read().replace('</script', '<\\/script')
bbx = '<script>' + xl + '</script>\n' + bbx  # ExcelJS inline so Excel export works offline and from email
tpl = open(os.path.join(BASE, 'report.template.html')).read()
html_out = (tpl.replace('__TITLE__', html.escape(TITLE))
            .replace('__SPEC__', json.dumps(spec, default=str).replace('</', '<\\/'))
            .replace('__BBX__', bbx))
assert '—' not in html_out and '–' not in html_out
rep = os.path.join(OUT, 'Network Scan Report %s.html' % SCAN)
open(rep, 'w').write(html_out)
print('wrote', rep, len(html_out))
