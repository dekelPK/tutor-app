"""
portal_core.py — logic shared by flask_app.py (production) and server.py (local)
for the public student/parent portal: free-slot computation for self-booking,
punch-card (כרטיסייה) usage, and the portal page HTML itself.

Pure functions only — no Flask, no DB. Each backend loads the data its own way
and passes plain dicts/lists in.

Data shapes (all stored as JSON blobs, same as students/lessons/payments):
  booking settings (one per teacher):
    {enabled, windows: [{day: 0-6 (0=Sunday, JS getDay), start: 'HH:MM', end: 'HH:MM'}],
     extraSlots: [{id, date: 'YYYY-MM-DD', start, end}], blockedDates: ['YYYY-MM-DD'],
     minNoticeHours, horizonDays, stepMinutes}
  booking request:
    {id, studentId, date, time, durationMinutes, note, status: pending|approved|rejected|cancelled,
     createdAt, decidedAt, lessonId, rejectReason}
  package (punch card):
    {id, studentId, date (purchase), totalUnits, price, expiresAt, notes}
  A lesson consumed from a card carries packageId; it uses durationHours units.
"""
import html as html_mod
import json
from datetime import datetime, timedelta

try:
    from zoneinfo import ZoneInfo
    _IL_TZ = ZoneInfo('Asia/Jerusalem')
except Exception:
    _IL_TZ = None

DEFAULT_BOOKING_SETTINGS = {
    'enabled': False,
    'windows': [],
    'extraSlots': [],
    'blockedDates': [],
    'minNoticeHours': 24,
    'horizonDays': 21,
    'stepMinutes': 30,
}

MAX_PENDING_PER_STUDENT = 3


def now_local():
    """Naive datetime in Israel time — lesson dates/times are stored naive, local."""
    if _IL_TZ:
        return datetime.now(_IL_TZ).replace(tzinfo=None)
    return datetime.utcnow()


def normalize_settings(raw):
    s = dict(DEFAULT_BOOKING_SETTINGS)
    if isinstance(raw, dict):
        s.update({k: v for k, v in raw.items() if k in DEFAULT_BOOKING_SETTINGS})
    return s


def _to_min(hhmm):
    try:
        h, m = str(hhmm).split(':')[:2]
        return int(h) * 60 + int(m)
    except Exception:
        return None


def _fmt_min(m):
    return f'{m // 60:02d}:{m % 60:02d}'


def lesson_interval(lesson, students_by_id):
    """(start_min, end_min) of a lesson on its own date."""
    start = _to_min(lesson.get('time') or '16:00')
    if start is None:
        return None
    s = students_by_id.get(lesson.get('studentId'), {})
    per = s.get('lessonDurationMinutes') or 50
    units = lesson.get('durationHours') or 1
    return start, start + int(units * per)


def compute_free_slots(settings, student, students, lessons, bookings, now=None):
    """Free start times for `student` (one lesson unit of their own length).

    Returns [{'date': 'YYYY-MM-DD', 'time': 'HH:MM'}] sorted. A slot is free when
    it fits inside an availability window (weekly or one-off) on a non-blocked
    date, is at least minNoticeHours away, and doesn't overlap any of the
    teacher's lessons (any student) or any still-pending booking request.
    """
    settings = normalize_settings(settings)
    if not settings.get('enabled'):
        return []
    now = now or now_local()
    duration = int(student.get('lessonDurationMinutes') or 50)
    step = max(5, int(settings.get('stepMinutes') or 30))
    horizon = max(1, min(90, int(settings.get('horizonDays') or 21)))
    earliest = now + timedelta(hours=float(settings.get('minNoticeHours') or 0))
    blocked = set(settings.get('blockedDates') or [])
    students_by_id = {s['id']: s for s in students}

    busy = {}
    for l in lessons:
        iv = lesson_interval(l, students_by_id)
        if iv:
            busy.setdefault(l.get('date'), []).append(iv)
    for b in bookings:
        if b.get('status') == 'pending':
            st = _to_min(b.get('time'))
            if st is not None:
                busy.setdefault(b.get('date'), []).append((st, st + int(b.get('durationMinutes') or duration)))

    windows_by_day = {}
    for w in settings.get('windows') or []:
        try:
            windows_by_day.setdefault(int(w.get('day')), []).append((_to_min(w.get('start')), _to_min(w.get('end'))))
        except Exception:
            pass
    extra_by_date = {}
    for e in settings.get('extraSlots') or []:
        extra_by_date.setdefault(e.get('date'), []).append((_to_min(e.get('start')), _to_min(e.get('end'))))

    out = []
    today = now.date()
    for i in range(horizon + 1):
        d = today + timedelta(days=i)
        ds = d.isoformat()
        if ds in blocked:
            continue
        js_day = (d.weekday() + 1) % 7  # Python Monday=0 → JS Sunday=0
        wins = windows_by_day.get(js_day, []) + extra_by_date.get(ds, [])
        starts = set()
        for ws, we in wins:
            if ws is None or we is None:
                continue
            t = ws
            while t + duration <= we:
                starts.add(t)
                t += step
        day_busy = busy.get(ds, [])
        for t in sorted(starts):
            slot_dt = datetime(d.year, d.month, d.day) + timedelta(minutes=t)
            if slot_dt < earliest:
                continue
            if any(t < be and t + duration > bs for bs, be in day_busy):
                continue
            out.append({'date': ds, 'time': _fmt_min(t)})
    return out


def package_used_units(pkg, lessons):
    return sum((l.get('durationHours') or 1) for l in lessons if l.get('packageId') == pkg.get('id'))


def package_status(pkg, lessons, today=None):
    today = today or now_local().strftime('%Y-%m-%d')
    total = pkg.get('totalUnits') or 0
    used = package_used_units(pkg, lessons)
    expired = bool(pkg.get('expiresAt')) and pkg['expiresAt'] < today
    return {'total': total, 'used': used, 'remaining': max(0, total - used), 'expired': expired}


def validate_booking_request(body, free_slots):
    """Returns (date, time, note) or raises ValueError with a Hebrew message."""
    date = str(body.get('date') or '')
    time = str(body.get('time') or '')
    note = str(body.get('note') or '').strip()[:300]
    if not any(s['date'] == date and s['time'] == time for s in free_slots):
        raise ValueError('המועד הזה כבר לא פנוי — רעננו את העמוד ובחרו מועד אחר')
    return date, time, note


# ── Portal page ─────────────────────────────────────────────────────────────────
_HE_DAYS = ['ראשון', 'שני', 'שלישי', 'רביעי', 'חמישי', 'שישי', 'שבת']


def _esc(x):
    return html_mod.escape(str(x if x is not None else ''))


def _fmt_date(iso):
    try:
        y, m, d = iso.split('-')
        return f'{d}/{m}/{y}'
    except Exception:
        return iso or ''


def _day_label(iso):
    try:
        d = datetime.strptime(iso, '%Y-%m-%d')
        return f'יום {_HE_DAYS[(d.weekday() + 1) % 7]} · {d.day}/{d.month}'
    except Exception:
        return iso


def render_portal(token, student, lessons, payments, packages=None, bookings=None,
                  free_slots=None, booking_enabled=False):
    packages = packages or []
    bookings = bookings or []
    free_slots = free_slots or []
    today = now_local().strftime('%Y-%m-%d')
    upcoming = sorted([l for l in lessons if l['date'] >= today], key=lambda l: (l['date'], l.get('time', '')))
    past = sorted([l for l in lessons if l['date'] < today], key=lambda l: (l['date'], l.get('time', '')), reverse=True)[:8]

    total_lessons = sum(l.get('amount', 0) or 0 for l in lessons)
    total_lesson_paid = sum(l.get('paidAmount', 0) or 0 for l in lessons)
    total_payments = sum(p.get('amount', 0) or 0 for p in payments)
    balance = total_payments + total_lesson_paid - total_lessons

    esc = _esc

    def lesson_row(l, show_status):
        topic = esc(l.get('topic'))
        homework = esc(l.get('homework'))
        extra = ''
        if topic:
            extra += f'<div class="portal-lesson-extra">📘 {topic}</div>'
        if homework:
            extra += f'<div class="portal-lesson-extra">📝 שיעורי בית: {homework}</div>'
        status_html = ''
        if l.get('packageId'):
            status_html = '<span class="portal-badge card">🎟 כרטיסייה</span>'
        elif show_status:
            paid = l.get('isPaid')
            partial = (l.get('paidAmount') or 0) > 0
            label = 'שולם' if paid else ('שולם חלקית' if partial else 'ממתין לתשלום')
            cls = 'paid' if paid else ('partial' if partial else 'unpaid')
            status_html = f'<span class="portal-badge {cls}">{label}</span>'
        return f'''<div class="portal-lesson">
          <div class="portal-lesson-head">
            <span class="portal-lesson-date">{esc(_fmt_date(l["date"]))} {esc(l.get("time",""))}</span>
            {status_html}
          </div>
          {extra}
        </div>'''

    upcoming_html = ''.join(lesson_row(l, False) for l in upcoming) or '<p class="portal-empty">אין שיעורים קרובים כרגע</p>'
    past_html = ''.join(lesson_row(l, True) for l in past) or '<p class="portal-empty">אין שיעורים קודמים</p>'
    balance_label = 'לתשלום' if balance < 0 else ('זכות' if balance > 0 else 'מאוזן')
    balance_abs = abs(balance)
    balance_class = 'debt' if balance < 0 else ('credit' if balance > 0 else 'even')

    # Punch cards: every card with units left, plus the most recent used-up one
    # so the parent can still see "used 10 of 10" right after it runs out.
    cards_html = ''
    pkgs_sorted = sorted(packages, key=lambda p: p.get('date', ''))
    shown = []
    for p in pkgs_sorted:
        st = package_status(p, lessons, today)
        if st['remaining'] > 0 and not st['expired']:
            shown.append((p, st))
    if not shown and pkgs_sorted:
        p = pkgs_sorted[-1]
        shown.append((p, package_status(p, lessons, today)))
    for p, st in shown:
        pct = int(100 * st['used'] / st['total']) if st['total'] else 0
        exp = f' · בתוקף עד {esc(_fmt_date(p["expiresAt"]))}' if p.get('expiresAt') else ''
        left_cls = 'low' if st['remaining'] <= 1 else ''
        cards_html += f'''<div class="portal-card">
          <div class="portal-card-head"><span>🎟 כרטיסייה של {st["total"]} שיעורים</span>
            <span class="portal-card-left {left_cls}">נותרו {st["remaining"]}</span></div>
          <div class="portal-bar"><div style="width:{pct}%"></div></div>
          <div class="portal-card-sub">נוצלו {st["used"]} מתוך {st["total"]}{exp}</div>
        </div>'''
    cards_section = f'<div class="portal-section">{cards_html}</div>' if cards_html else ''

    # Booking requests: pending ones (cancellable) + rejections from the last week.
    week_ago = (now_local() - timedelta(days=7)).isoformat()
    req_rows = ''
    for b in sorted(bookings, key=lambda b: (b.get('date', ''), b.get('time', ''))):
        if b.get('status') == 'pending' and b.get('date', '') >= today:
            req_rows += f'''<div class="portal-lesson"><div class="portal-lesson-head">
              <span class="portal-lesson-date">{esc(_fmt_date(b["date"]))} {esc(b.get("time"))}</span>
              <span class="portal-badge partial">⏳ ממתין לאישור</span></div>
              <button class="portal-link-btn" onclick="cancelReq('{esc(b["id"])}')">ביטול הבקשה</button></div>'''
        elif b.get('status') == 'rejected' and (b.get('decidedAt') or '') >= week_ago:
            reason = f'<div class="portal-lesson-extra">{esc(b.get("rejectReason"))}</div>' if b.get('rejectReason') else ''
            req_rows += f'''<div class="portal-lesson"><div class="portal-lesson-head">
              <span class="portal-lesson-date">{esc(_fmt_date(b["date"]))} {esc(b.get("time"))}</span>
              <span class="portal-badge unpaid">המועד לא אושר</span></div>{reason}</div>'''

    booking_section = ''
    if booking_enabled:
        by_date = {}
        for s in free_slots:
            by_date.setdefault(s['date'], []).append(s['time'])
        if by_date:
            days_html = ''
            for d, times in by_date.items():
                chips = ''.join(f'<button type="button" class="slot" data-date="{esc(d)}" data-time="{esc(t)}" onclick="pickSlot(this)">{esc(t)}</button>' for t in times)
                days_html += f'<div class="slot-day"><div class="slot-day-label">{esc(_day_label(d))}</div><div class="slot-chips">{chips}</div></div>'
            picker = f'''{days_html}
              <div id="book-box" class="book-box" style="display:none">
                <div id="book-chosen" class="book-chosen"></div>
                <textarea id="book-note" rows="2" maxlength="300" placeholder="הערה למורה (לא חובה)"></textarea>
                <button id="book-btn" class="book-btn" onclick="submitBooking()">שליחת בקשה</button>
                <div class="portal-card-sub">השיעור ייקבע רק אחרי אישור המורה</div>
              </div>'''
        else:
            picker = '<p class="portal-empty">אין כרגע מועדים פנויים — כדאי לבדוק שוב בהמשך</p>'
        pending_block = f'<div class="req-list"><h3>הבקשות שלי</h3>{req_rows}</div>' if req_rows else ''
        booking_section = f'''<div class="portal-section">
          <h2>🗓️ קביעת שיעור</h2>
          {pending_block}
          {picker}
        </div>'''
    elif req_rows:
        booking_section = f'<div class="portal-section"><h2>🗓️ הבקשות שלי</h2>{req_rows}</div>'

    token_js = json.dumps(token)
    page = f'''<!doctype html>
<html lang="he" dir="rtl">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{esc(student.get("name"))} — מעקב שיעורים</title>
<style>
  :root {{ --primary: #2563eb; --bg: #f1f5f9; --card: #fff; --text: #1e293b; --muted: #64748b; --border: #e2e8f0; }}
  * {{ box-sizing: border-box; }}
  body {{ margin:0; font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Arial, sans-serif; background: var(--bg); color: var(--text); padding: 16px; }}
  .portal-wrap {{ max-width: 480px; margin: 0 auto; }}
  .portal-header {{ text-align:center; margin: 12px 0 20px; }}
  .portal-header .avatar {{ width: 56px; height:56px; border-radius:50%; background:var(--primary); color:white; display:flex; align-items:center; justify-content:center; font-size:24px; font-weight:700; margin: 0 auto 10px; }}
  .portal-header h1 {{ font-size: 20px; margin: 0 0 4px; }}
  .portal-header p {{ color: var(--muted); font-size: 13px; margin:0; }}
  .portal-balance {{ background: var(--card); border-radius: 14px; padding: 18px; text-align:center; margin-bottom:18px; box-shadow: 0 2px 10px rgba(0,0,0,0.06); }}
  .portal-balance .amount {{ font-size: 28px; font-weight: 700; margin-top:4px; }}
  .portal-balance.debt .amount {{ color: #dc2626; }}
  .portal-balance.credit .amount {{ color: #16a34a; }}
  .portal-balance.even .amount {{ color: var(--muted); }}
  .portal-section {{ background: var(--card); border-radius: 14px; padding: 16px; margin-bottom: 16px; box-shadow: 0 2px 10px rgba(0,0,0,0.06); }}
  .portal-section h2 {{ font-size: 14px; margin: 0 0 10px; }}
  .portal-lesson {{ padding: 10px 0; border-bottom: 1px solid var(--border); }}
  .portal-lesson:last-child {{ border-bottom: none; }}
  .portal-lesson-head {{ display:flex; justify-content:space-between; align-items:center; }}
  .portal-lesson-date {{ font-weight:600; font-size: 13.5px; }}
  .portal-lesson-extra {{ font-size: 12.5px; color: var(--muted); margin-top: 4px; }}
  .portal-badge {{ font-size: 11px; font-weight:700; padding: 2px 9px; border-radius: 10px; }}
  .portal-badge.paid {{ background:#dcfce7; color:#166534; }}
  .portal-badge.partial {{ background:#fef3c7; color:#92400e; }}
  .portal-badge.unpaid {{ background:#fee2e2; color:#991b1b; }}
  .portal-badge.card {{ background:#ede9fe; color:#5b21b6; }}
  .portal-empty {{ color: var(--muted); font-size: 13px; text-align:center; padding: 10px 0; }}
  .portal-footer {{ text-align:center; color: var(--muted); font-size: 11.5px; margin-top: 20px; }}
  .portal-card + .portal-card {{ margin-top: 14px; }}
  .portal-card-head {{ display:flex; justify-content:space-between; align-items:center; font-weight:600; font-size:14px; }}
  .portal-card-left {{ font-size:12px; background:#ede9fe; color:#5b21b6; padding:2px 9px; border-radius:10px; }}
  .portal-card-left.low {{ background:#fee2e2; color:#991b1b; }}
  .portal-bar {{ height:8px; background:var(--border); border-radius:6px; margin:10px 0 6px; overflow:hidden; }}
  .portal-bar div {{ height:100%; background:#7c3aed; border-radius:6px; }}
  .portal-card-sub {{ font-size:12px; color:var(--muted); }}
  .slot-day {{ margin-bottom: 12px; }}
  .slot-day-label {{ font-size: 12.5px; font-weight: 600; color: var(--muted); margin-bottom: 6px; }}
  .slot-chips {{ display:flex; flex-wrap:wrap; gap:6px; }}
  .slot {{ border:1px solid var(--border); background:var(--bg); color:var(--text); border-radius:8px; padding:7px 12px; font-size:13.5px; font-family:inherit; cursor:pointer; direction:ltr; }}
  .slot.selected {{ background:var(--primary); border-color:var(--primary); color:#fff; font-weight:700; }}
  .book-box {{ position: sticky; bottom: 10px; background: var(--card); border:2px solid var(--primary); border-radius: 12px; padding: 12px; margin-top: 8px; box-shadow: 0 6px 20px rgba(0,0,0,0.12); }}
  .book-chosen {{ font-weight:700; margin-bottom:8px; }}
  .book-box textarea {{ width:100%; border:1px solid var(--border); border-radius:8px; padding:8px; font-family:inherit; font-size:13.5px; margin-bottom:8px; resize: vertical; }}
  .book-btn {{ width:100%; background:var(--primary); color:#fff; border:none; border-radius:8px; padding:11px; font-size:15px; font-weight:700; font-family:inherit; cursor:pointer; margin-bottom:6px; }}
  .book-btn:disabled {{ opacity:.6; }}
  .req-list {{ margin-bottom: 14px; padding-bottom: 6px; border-bottom: 1px dashed var(--border); }}
  .req-list h3 {{ font-size: 13px; margin: 0; color: var(--muted); }}
  .portal-link-btn {{ background:none; border:none; color:#dc2626; font-size:12.5px; padding:4px 0 0; cursor:pointer; font-family:inherit; }}
</style>
</head>
<body>
  <div class="portal-wrap">
    <div class="portal-header">
      <div class="avatar">{esc((student.get("name") or "?")[0])}</div>
      <h1>{esc(student.get("name"))}</h1>
      <p>מעקב שיעורים ותשלומים</p>
    </div>
    <div class="portal-balance {balance_class}">
      <div>{balance_label}</div>
      <div class="amount">₪{balance_abs:,.0f}</div>
    </div>
    {cards_section}
    {booking_section}
    <div class="portal-section">
      <h2>📅 שיעורים קרובים</h2>
      {upcoming_html}
    </div>
    <div class="portal-section">
      <h2>📚 שיעורים אחרונים</h2>
      {past_html}
    </div>
    <div class="portal-footer">עמוד זה מתעדכן אוטומטית · ניהול שיעורים פרטיים</div>
  </div>
<script>
  const TOKEN = {token_js};
  let chosen = null;
  function pickSlot(btn) {{
    document.querySelectorAll('.slot.selected').forEach(b => b.classList.remove('selected'));
    btn.classList.add('selected');
    chosen = {{ date: btn.dataset.date, time: btn.dataset.time }};
    const [y, m, d] = chosen.date.split('-');
    document.getElementById('book-chosen').textContent = 'מועד שנבחר: ' + d + '/' + m + ' בשעה ' + chosen.time;
    document.getElementById('book-box').style.display = '';
  }}
  async function submitBooking() {{
    if (!chosen) return;
    const btn = document.getElementById('book-btn');
    btn.disabled = true;
    try {{
      const r = await fetch('/portal/' + encodeURIComponent(TOKEN) + '/book', {{
        method: 'POST', headers: {{ 'Content-Type': 'application/json' }},
        body: JSON.stringify({{ date: chosen.date, time: chosen.time, note: document.getElementById('book-note').value }})
      }});
      const j = await r.json().catch(() => ({{}}));
      if (!r.ok) {{ alert(j.error || 'שגיאה בשליחת הבקשה'); btn.disabled = false; if (r.status === 409) location.reload(); return; }}
      alert('הבקשה נשלחה! השיעור ייקבע אחרי אישור המורה.');
      location.reload();
    }} catch (e) {{ alert('שגיאת תקשורת, נסו שוב'); btn.disabled = false; }}
  }}
  async function cancelReq(id) {{
    if (!confirm('לבטל את הבקשה?')) return;
    const r = await fetch('/portal/' + encodeURIComponent(TOKEN) + '/cancel/' + encodeURIComponent(id), {{ method: 'POST' }});
    if (!r.ok) alert('לא ניתן לבטל');
    location.reload();
  }}
</script>
</body>
</html>'''
    return page
