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
_HE_MONTHS = ['ינואר', 'פברואר', 'מרץ', 'אפריל', 'מאי', 'יוני', 'יולי', 'אוגוסט',
              'ספטמבר', 'אוקטובר', 'נובמבר', 'דצמבר']
_HE_MONTHS_SHORT = ['ינו׳', 'פבר׳', 'מרץ', 'אפר׳', 'מאי', 'יוני', 'יולי', 'אוג׳', 'ספט׳', 'אוק׳', 'נוב׳', 'דצמ׳']
APP_BLUE = '#2563eb'


def _esc(x):
    return html_mod.escape(str(x if x is not None else ''))


def _fmt_date(iso):
    try:
        y, m, d = iso.split('-')
        return f'{d}/{m}/{y}'
    except Exception:
        return iso or ''


def _parse(iso):
    try:
        return datetime.strptime(iso, '%Y-%m-%d')
    except Exception:
        return None


def _weekday(dt):
    return _HE_DAYS[(dt.weekday() + 1) % 7]


def _long_date(iso):
    d = _parse(iso)
    return f'יום {_weekday(d)}, {d.day} ב{_HE_MONTHS[d.month - 1]}' if d else iso


def _time_range(lesson, student):
    start = _to_min(lesson.get('time') or '16:00')
    if start is None:
        return _esc(lesson.get('time'))
    per = student.get('lessonDurationMinutes') or 50
    end = start + int((lesson.get('durationHours') or 1) * per)
    # dir=ltr isolate: otherwise RTL bidi flips it to "18:00–17:00"
    return f'<span class="tr" dir="ltr">{_fmt_min(start)}–{_fmt_min(end % (24 * 60))}</span>'


def _countdown(iso, today):
    d, t = _parse(iso), _parse(today)
    if not d or not t:
        return ''
    n = (d - t).days
    if n == 0:
        return 'היום!'
    if n == 1:
        return 'מחר'
    if n == 2:
        return 'מחרתיים'
    return f'בעוד {n} ימים'


def _date_tile(iso, extra_cls=''):
    d = _parse(iso)
    if not d:
        return ''
    return (f'<div class="tile {extra_cls}"><span class="tile-wd">{_weekday(d)}</span>'
            f'<span class="tile-day">{d.day}</span><span class="tile-mon">{_HE_MONTHS_SHORT[d.month - 1]}</span></div>')


def render_portal(token, student, lessons, payments, packages=None, bookings=None,
                  free_slots=None, booking_enabled=False, teacher_name=''):
    packages = packages or []
    bookings = bookings or []
    free_slots = free_slots or []
    esc = _esc
    now = now_local()
    today = now.strftime('%Y-%m-%d')
    upcoming = sorted([l for l in lessons if l['date'] >= today], key=lambda l: (l['date'], l.get('time', '')))
    past = sorted([l for l in lessons if l['date'] < today], key=lambda l: (l['date'], l.get('time', '')), reverse=True)[:8]

    total_lessons = sum(l.get('amount', 0) or 0 for l in lessons)
    total_lesson_paid = sum(l.get('paidAmount', 0) or 0 for l in lessons)
    total_payments = sum(p.get('amount', 0) or 0 for p in payments)
    balance = total_payments + total_lesson_paid - total_lessons

    name = student.get('name') or ''
    first_name = name.split()[0] if name.split() else name
    # Same blue as the app (its --primary), so the portal feels like part of it.
    accent = APP_BLUE

    # ── Hero: greeting + the next lesson, front and centre ──
    if upcoming:
        nl = upcoming[0]
        topic = f'<div class="next-topic">📘 {esc(nl.get("topic"))}</div>' if nl.get('topic') else ''
        next_html = f'''<div class="next">
          <div class="next-label">השיעור הבא שלך</div>
          <div class="next-row">
            <div><div class="next-date">{esc(_long_date(nl["date"]))}</div>
              <div class="next-time">🕓 {_time_range(nl, student)}</div>{topic}</div>
            <div class="countdown">{esc(_countdown(nl["date"], today))}</div>
          </div>
        </div>'''
    else:
        cta = '<a class="next-cta" href="#book">לקביעת שיעור ↓</a>' if booking_enabled else ''
        next_html = f'''<div class="next next-empty">
          <div class="next-label">השיעור הבא שלך</div>
          <div class="next-date">עוד לא נקבע שיעור</div>{cta}
        </div>'''
    teacher_line = (f'<div class="hero-sub">השיעורים שלך אצל {esc(teacher_name)}</div>' if teacher_name
                    else '<div class="hero-sub">השיעורים, הכרטיסייה והתשלומים שלך</div>')

    # ── My lessons: upcoming lessons + my pending/declined requests, by date ──
    rows = []
    for l in upcoming:
        topic = f'<div class="li-sub">📘 {esc(l.get("topic"))}</div>' if l.get('topic') else ''
        card = '<span class="pill pill-card">🎟 מהכרטיסייה</span>' if l.get('packageId') else ''
        rows.append((l['date'], l.get('time', ''), f'''<div class="li">
          {_date_tile(l["date"])}
          <div class="li-main"><div class="li-title">{_time_range(l, student)}</div>{topic}</div>
          {card}
        </div>'''))
    week_ago = (now - timedelta(days=7)).isoformat()
    for b in bookings:
        if b.get('status') == 'pending' and b.get('date', '') >= today:
            rows.append((b['date'], b.get('time', ''), f'''<div class="li li-pending">
              {_date_tile(b["date"], 'tile-ghost')}
              <div class="li-main"><div class="li-title">{esc(b.get("time"))} · בקשה שנשלחה</div>
                <div class="li-sub">⏳ מחכה לאישור המורה</div></div>
              <button class="li-x" onclick="cancelReq('{esc(b["id"])}')">ביטול</button>
            </div>'''))
        elif b.get('status') == 'rejected' and (b.get('decidedAt') or '') >= week_ago:
            reason = f'<div class="li-sub">💬 {esc(b.get("rejectReason"))}</div>' if b.get('rejectReason') else ''
            rows.append((b['date'], b.get('time', ''), f'''<div class="li li-declined">
              {_date_tile(b["date"], 'tile-ghost')}
              <div class="li-main"><div class="li-title">{esc(b.get("time"))} · המועד לא התאים</div>{reason}</div>
            </div>'''))
    rows.sort(key=lambda r: (r[0], r[1]))
    lessons_html = ''.join(r[2] for r in rows) or '<div class="empty">אין שיעורים קרובים כרגע</div>'

    # ── Book a lesson: day strip → time grid → bottom-sheet confirm ──
    by_date = {}
    for sl in free_slots:
        by_date.setdefault(sl['date'], []).append(sl['time'])
    booking_section = ''
    if booking_enabled:
        if by_date:
            days_html = ''
            for d in by_date:
                dt = _parse(d)
                days_html += (f'<button type="button" class="day" data-date="{esc(d)}" onclick="pickDay(this)">'
                              f'<span class="day-wd">{_weekday(dt)}</span><span class="day-num">{dt.day}</span>'
                              f'<span class="day-mon">{_HE_MONTHS_SHORT[dt.month - 1]}</span></button>')
            picker = f'''<div class="days" id="days">{days_html}</div>
              <div class="times-label" id="times-label"></div>
              <div class="times" id="times"></div>'''
        else:
            picker = '<div class="empty">אין כרגע מועדים פנויים — כדאי להציץ שוב בהמשך 🙂</div>'
        booking_section = f'''<section class="sec" id="book">
          <div class="sec-head"><h2>🗓️ לקבוע שיעור לבד</h2></div>
          <p class="sec-desc">בוחרים יום ושעה שנוחים לך — המורה מאשר/ת והשיעור נכנס לרשימה למעלה.</p>
          {picker}
        </section>'''
    # Server-generated dates/times only; '</' escaped anyway so it can't close the <script>.
    slots_json = json.dumps(by_date).replace('</', '<\\/')

    # ── Punch card + balance ──
    # Every card with units left; otherwise the most recent one so "10/10 used" stays visible.
    all_cards = [(p, package_status(p, lessons, today)) for p in sorted(packages, key=lambda p: p.get('date', ''))]
    live = [(p, st) for p, st in all_cards if st['remaining'] > 0 and not st['expired']]
    cards_html = ''
    for p, st in (live or all_cards[-1:]):
        total = st['total']
        if 0 < total <= 30:
            holes = ''.join(
                f'<span class="hole used">✓</span>' if i < st['used'] else f'<span class="hole">{i + 1}</span>'
                for i in range(total))
            visual = f'<div class="holes">{holes}</div>'
        else:
            pct = int(100 * st['used'] / total) if total else 0
            visual = f'<div class="bar"><div style="width:{pct}%"></div></div>'
        exp = f'<div class="muted">בתוקף עד {esc(_fmt_date(p["expiresAt"]))}</div>' if p.get('expiresAt') else ''
        left_cls = 'low' if st['remaining'] <= 1 else ''
        cards_html += f'''<div class="punch">
          <div class="punch-head"><div><div class="punch-title">🎟 הכרטיסייה שלי</div>
            <div class="muted">נוצלו {st["used"]} מתוך {total}</div></div>
            <div class="punch-left {left_cls}"><b>{st["remaining"]}</b><span>נותרו</span></div></div>
          {visual}{exp}
        </div>'''
    balance_label = 'לתשלום' if balance < 0 else ('זכות' if balance > 0 else 'הכול מאוזן')
    balance_class = 'debt' if balance < 0 else ('credit' if balance > 0 else 'even')
    balance_amount = f'₪{abs(balance):,.0f}' if balance else '✓'
    money_section = f'''<section class="sec">
      <div class="sec-head"><h2>💳 כרטיסייה ותשלומים</h2></div>
      {cards_html}
      <div class="balance {balance_class}"><span>{balance_label}</span><b>{balance_amount}</b></div>
    </section>'''

    # ── What we learned ──
    hw = next((l for l in past if l.get('homework')), None)
    hw_html = (f'<div class="hw"><div class="hw-label">📝 שיעורי הבית האחרונים · {esc(_fmt_date(hw["date"]))}</div>'
               f'<div class="hw-text">{esc(hw.get("homework"))}</div></div>') if hw else ''

    def past_row(l):
        if l.get('packageId'):
            badge = '<span class="pill pill-card">🎟 כרטיסייה</span>'
        else:
            paid = l.get('isPaid')
            partial = (l.get('paidAmount') or 0) > 0
            label = 'שולם' if paid else ('שולם חלקית' if partial else 'ממתין לתשלום')
            cls = 'paid' if paid else ('partial' if partial else 'unpaid')
            badge = f'<span class="pill pill-{cls}">{label}</span>'
        topic = f'<div class="li-sub">📘 {esc(l.get("topic"))}</div>' if l.get('topic') else ''
        homework = f'<div class="li-sub">📝 {esc(l.get("homework"))}</div>' if l.get('homework') else ''
        return f'''<div class="li li-past">{_date_tile(l["date"], 'tile-soft')}
          <div class="li-main"><div class="li-title">{_time_range(l, student)}</div>{topic}{homework}</div>{badge}</div>'''

    past_html = ''.join(past_row(l) for l in past) or '<div class="empty">עוד אין שיעורים קודמים</div>'

    token_js = json.dumps(token)
    return PAGE_TEMPLATE.format(
        accent=accent, title=esc(name), initial=esc(name[:1] or '?'), first_name=esc(first_name),
        teacher_line=teacher_line, next_html=next_html, lessons_html=lessons_html,
        booking_section=booking_section, money_section=money_section, hw_html=hw_html,
        past_html=past_html, token_js=token_js, slots_json=slots_json)


# Kept as a plain template (not an f-string) so the CSS/JS braces stay readable;
# literal braces are doubled for str.format.
PAGE_TEMPLATE = '''<!doctype html>
<html lang="he" dir="rtl">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1, viewport-fit=cover">
<meta name="theme-color" content="{accent}">
<title>{title} — השיעורים שלי</title>
<link rel="preconnect" href="https://fonts.googleapis.com">
<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link href="https://fonts.googleapis.com/css2?family=Rubik:wght@400;500;600;700;800&display=swap" rel="stylesheet">
<style>
  :root {{
    --accent: {accent};
    --accent-deep: color-mix(in srgb, var(--accent) 62%, #0b1033);
    --accent-soft: color-mix(in srgb, var(--accent) 12%, transparent);
    --bg: #f4f5fb; --card: #ffffff; --text: #141a2e; --muted: #6b7390; --line: #e8eaf3;
    --ok: #0f9d6b; --ok-soft: #dcf7ec; --warn: #b45309; --warn-soft: #fff1d6; --bad: #d92d3a; --bad-soft: #ffe3e5;
    --card-c: #7c3aed; --card-soft: #efe7ff;
    --shadow: 0 1px 2px rgba(20,26,46,.04), 0 8px 24px rgba(20,26,46,.06);
  }}
  @media (prefers-color-scheme: dark) {{
    :root {{ --bg: #0d1020; --card: #171b2e; --text: #eef0fa; --muted: #9aa2c0; --line: #262b44;
      --ok: #34d399; --ok-soft: #0f3326; --warn: #fbbf24; --warn-soft: #3a2a0c; --bad: #f87171; --bad-soft: #3d1419;
      --card-c: #a78bfa; --card-soft: #2a1c4d;
      --accent-soft: color-mix(in srgb, var(--accent) 22%, transparent);
      --shadow: 0 1px 2px rgba(0,0,0,.3), 0 8px 24px rgba(0,0,0,.25); }}
  }}
  * {{ box-sizing: border-box; }}
  html {{ scroll-behavior: smooth; }}
  body {{ margin: 0; font-family: 'Rubik', -apple-system, BlinkMacSystemFont, 'Segoe UI', Arial, sans-serif; background: var(--bg); color: var(--text); -webkit-font-smoothing: antialiased; }}
  .wrap {{ max-width: 520px; margin: 0 auto; padding: 0 16px 40px; }}

  .hero {{ position: relative; overflow: hidden; color: #fff; padding: 28px 20px 22px; margin: 0 -16px 18px;
    background: var(--accent); background: linear-gradient(150deg, var(--accent) 0%, var(--accent-deep) 100%);
    border-radius: 0 0 28px 28px; }}
  .hero::before, .hero::after {{ content: ''; position: absolute; border-radius: 50%; background: rgba(255,255,255,.09); pointer-events: none; }}
  .hero::before {{ width: 220px; height: 220px; top: -90px; left: -60px; }}
  .hero::after {{ width: 140px; height: 140px; bottom: -50px; right: -30px; }}
  .hero-top {{ display: flex; align-items: center; gap: 14px; position: relative; }}
  .avatar {{ width: 54px; height: 54px; border-radius: 18px; background: rgba(255,255,255,.2); border: 1px solid rgba(255,255,255,.35);
    display: flex; align-items: center; justify-content: center; font-size: 24px; font-weight: 700; }}
  .hello {{ font-size: 24px; font-weight: 800; letter-spacing: -.3px; }}
  .hero-sub {{ font-size: 13.5px; opacity: .85; margin-top: 2px; }}
  .next {{ position: relative; margin-top: 20px; padding: 16px; border-radius: 20px; background: rgba(255,255,255,.14);
    border: 1px solid rgba(255,255,255,.25); backdrop-filter: blur(6px); -webkit-backdrop-filter: blur(6px); }}
  .next-label {{ font-size: 12px; font-weight: 600; opacity: .85; }}
  .next-row {{ display: flex; justify-content: space-between; align-items: center; gap: 10px; margin-top: 6px; }}
  .next-date {{ font-size: 19px; font-weight: 700; margin-top: 4px; }}
  .next-time {{ font-size: 15px; margin-top: 4px; opacity: .95; }}
  .next-topic {{ font-size: 13px; margin-top: 6px; opacity: .9; }}
  .countdown {{ flex-shrink: 0; background: #fff; color: var(--accent-deep); font-weight: 800; font-size: 14px; padding: 8px 12px; border-radius: 14px; }}
  .next-cta {{ display: inline-block; margin-top: 12px; background: #fff; color: var(--accent-deep); font-weight: 700; padding: 9px 16px; border-radius: 12px; text-decoration: none; font-size: 14px; }}

  .sec {{ background: var(--card); border-radius: 22px; padding: 18px 16px; margin-bottom: 14px; box-shadow: var(--shadow); scroll-margin-top: 12px; }}
  .sec-head {{ display: flex; justify-content: space-between; align-items: center; margin-bottom: 10px; }}
  .sec h2 {{ font-size: 16px; margin: 0; font-weight: 700; }}
  .sec-desc {{ margin: -4px 0 14px; color: var(--muted); font-size: 13px; line-height: 1.5; }}
  .muted {{ color: var(--muted); font-size: 12.5px; }}
  .tr {{ unicode-bidi: isolate; display: inline-block; }}
  .empty {{ color: var(--muted); font-size: 13.5px; text-align: center; padding: 14px 0 6px; }}

  .li {{ display: flex; align-items: center; gap: 12px; padding: 10px 0; border-bottom: 1px solid var(--line); }}
  .li:last-child {{ border-bottom: none; }}
  .li-main {{ flex: 1; min-width: 0; }}
  .li-title {{ font-weight: 600; font-size: 15px; }}
  .li-sub {{ color: var(--muted); font-size: 12.5px; margin-top: 3px; line-height: 1.45; }}
  .tile {{ width: 52px; flex-shrink: 0; border-radius: 14px; background: var(--accent); background: linear-gradient(160deg, var(--accent), var(--accent-deep)); color: #fff;
    display: flex; flex-direction: column; align-items: center; padding: 6px 0 7px; line-height: 1.05; }}
  .tile-wd, .tile-mon {{ font-size: 10.5px; opacity: .9; }}
  .tile-day {{ font-size: 20px; font-weight: 800; margin: 2px 0; }}
  .tile-ghost {{ background: transparent; color: var(--accent); border: 2px dashed var(--accent); }}
  .tile-soft {{ background: var(--accent-soft); color: var(--text); }}
  .li-pending .li-title {{ color: var(--accent); }}
  .li-declined {{ opacity: .7; }}
  .li-declined .tile {{ border-color: var(--muted); color: var(--muted); }}
  .li-x {{ background: none; border: 1px solid var(--line); color: var(--bad); border-radius: 10px; padding: 6px 10px; font: inherit; font-size: 12.5px; cursor: pointer; }}
  .pill {{ flex-shrink: 0; font-size: 11.5px; font-weight: 700; padding: 4px 10px; border-radius: 999px; white-space: nowrap; }}
  .pill-card {{ background: var(--card-soft); color: var(--card-c); }}
  .pill-paid {{ background: var(--ok-soft); color: var(--ok); }}
  .pill-partial {{ background: var(--warn-soft); color: var(--warn); }}
  .pill-unpaid {{ background: var(--bad-soft); color: var(--bad); }}

  .days {{ display: flex; gap: 8px; overflow-x: auto; padding: 4px 2px 10px; margin: 0 -2px; scrollbar-width: none; scroll-snap-type: x proximity; }}
  .days::-webkit-scrollbar {{ display: none; }}
  .day {{ flex: 0 0 auto; width: 62px; scroll-snap-align: start; border: 1.5px solid var(--line); background: var(--card); color: var(--text); border-radius: 16px;
    padding: 8px 0; display: flex; flex-direction: column; align-items: center; gap: 2px; cursor: pointer; font: inherit; transition: all .15s; }}
  .day-wd, .day-mon {{ font-size: 11.5px; color: var(--muted); }}
  .day-num {{ font-size: 20px; font-weight: 800; }}
  .day.on {{ background: var(--accent); border-color: var(--accent); color: #fff; box-shadow: 0 6px 16px color-mix(in srgb, var(--accent) 35%, transparent); transform: translateY(-2px); }}
  .day.on .day-wd, .day.on .day-mon {{ color: rgba(255,255,255,.85); }}
  .times-label {{ font-size: 13px; font-weight: 600; color: var(--muted); margin: 8px 0; }}
  .times {{ display: grid; grid-template-columns: repeat(auto-fill, minmax(78px, 1fr)); gap: 8px; }}
  .slot {{ border: 1.5px solid var(--line); background: var(--bg); color: var(--text); border-radius: 12px; padding: 11px 0; font: inherit; font-size: 15px; font-weight: 600; cursor: pointer; direction: ltr; transition: all .12s; }}
  .slot:hover {{ border-color: var(--accent); }}
  .slot.on {{ background: var(--accent); border-color: var(--accent); color: #fff; }}

  .sheet {{ position: fixed; inset-inline: 0; bottom: 0; z-index: 20; display: flex; justify-content: center; pointer-events: none;
    transform: translateY(110%); visibility: hidden; transition: transform .28s cubic-bezier(.2,.8,.2,1), visibility 0s .28s; }}
  .sheet.show {{ transform: none; visibility: visible; transition: transform .28s cubic-bezier(.2,.8,.2,1); }}
  .sheet-in {{ pointer-events: auto; width: 100%; max-width: 520px; background: var(--card); border-radius: 24px 24px 0 0; padding: 16px 16px calc(16px + env(safe-area-inset-bottom));
    box-shadow: 0 -10px 40px rgba(20,26,46,.18); }}
  .sheet-grip {{ width: 40px; height: 4px; background: var(--line); border-radius: 4px; margin: 0 auto 12px; }}
  .sheet-when {{ font-size: 17px; font-weight: 700; }}
  .sheet-when small {{ display: block; font-size: 12.5px; font-weight: 400; color: var(--muted); margin-top: 2px; }}
  .sheet textarea {{ width: 100%; margin: 12px 0 10px; border: 1.5px solid var(--line); background: var(--bg); color: var(--text); border-radius: 12px; padding: 10px; font: inherit; font-size: 14px; resize: none; }}
  .sheet textarea:focus {{ outline: none; border-color: var(--accent); }}
  .btn {{ width: 100%; border: none; border-radius: 14px; padding: 14px; font: inherit; font-size: 16px; font-weight: 700; cursor: pointer; background: var(--accent); color: #fff; }}
  .btn:disabled {{ opacity: .6; }}
  .btn-ghost {{ background: transparent; color: var(--muted); font-weight: 500; font-size: 14px; padding: 10px; }}

  .punch {{ border-radius: 18px; padding: 14px; margin-bottom: 12px; background: var(--card-soft); }}
  .punch-head {{ display: flex; justify-content: space-between; align-items: center; }}
  .punch-title {{ font-weight: 700; font-size: 15px; color: var(--card-c); margin-bottom: 2px; }}
  .punch-left {{ text-align: center; background: var(--card); border-radius: 14px; padding: 6px 12px; line-height: 1.1; }}
  .punch-left b {{ display: block; font-size: 22px; color: var(--card-c); }}
  .punch-left span {{ font-size: 11px; color: var(--muted); }}
  .punch-left.low b {{ color: var(--bad); }}
  .holes {{ display: grid; grid-template-columns: repeat(5, 1fr); gap: 8px; margin: 14px 0 8px; }}
  .hole {{ aspect-ratio: 1; max-width: 46px; width: 100%; justify-self: center; border-radius: 50%; border: 2px dashed color-mix(in srgb, var(--card-c) 45%, transparent);
    display: flex; align-items: center; justify-content: center; font-size: 12px; color: color-mix(in srgb, var(--card-c) 60%, transparent); font-weight: 600; }}
  .hole.used {{ border: none; background: var(--card-c); color: #fff; font-size: 16px; transform: rotate(-8deg); box-shadow: inset 0 0 0 3px rgba(255,255,255,.25); }}
  .bar {{ height: 10px; background: var(--card); border-radius: 8px; margin: 14px 0 8px; overflow: hidden; }}
  .bar div {{ height: 100%; background: var(--card-c); border-radius: 8px; }}
  .balance {{ display: flex; justify-content: space-between; align-items: center; border-radius: 16px; padding: 14px 16px; font-weight: 600; }}
  .balance b {{ font-size: 20px; }}
  .balance.debt {{ background: var(--bad-soft); color: var(--bad); }}
  .balance.credit {{ background: var(--ok-soft); color: var(--ok); }}
  .balance.even {{ background: var(--bg); color: var(--muted); }}

  .hw {{ border-radius: 16px; padding: 14px; margin-bottom: 8px; background: var(--warn-soft); border-inline-start: 4px solid #f59e0b; }}
  .hw-label {{ font-size: 12.5px; font-weight: 700; color: var(--warn); }}
  .hw-text {{ font-size: 15px; margin-top: 6px; line-height: 1.5; white-space: pre-wrap; }}

  .toast {{ position: fixed; top: 16px; inset-inline: 16px; max-width: 488px; margin: 0 auto; z-index: 40; background: #141a2e; color: #fff; border-radius: 14px; padding: 12px 16px;
    font-size: 14px; box-shadow: 0 10px 30px rgba(0,0,0,.25); transform: translateY(-160%); transition: transform .25s; }}
  .toast.show {{ transform: none; }}
  .done {{ position: fixed; inset: 0; z-index: 30; background: color-mix(in srgb, var(--bg) 92%, transparent); backdrop-filter: blur(6px); -webkit-backdrop-filter: blur(6px);
    display: none; align-items: center; justify-content: center; padding: 24px; text-align: center; }}
  .done.show {{ display: flex; }}
  .done > div {{ max-width: 340px; }}
  .done-ring {{ width: 84px; height: 84px; border-radius: 50%; background: var(--accent); color: #fff; font-size: 40px; display: flex; align-items: center; justify-content: center;
    margin: 0 auto 16px; animation: pop .45s cubic-bezier(.2,1.6,.4,1); }}
  .done h3 {{ font-size: 22px; margin: 0 0 6px; }}
  .done p {{ color: var(--muted); margin: 0 0 20px; font-size: 14.5px; line-height: 1.5; }}
  @keyframes pop {{ from {{ transform: scale(.3); opacity: 0; }} to {{ transform: scale(1); opacity: 1; }} }}
  .footer {{ text-align: center; color: var(--muted); font-size: 11.5px; margin-top: 22px; }}
  @media (prefers-reduced-motion: reduce) {{ * {{ transition: none !important; animation: none !important; }} }}
</style>
</head>
<body>
  <div class="wrap">
    <header class="hero">
      <div class="hero-top">
        <div class="avatar">{initial}</div>
        <div><div class="hello">היי {first_name} 👋</div>{teacher_line}</div>
      </div>
      {next_html}
    </header>

    <section class="sec">
      <div class="sec-head"><h2>📅 השיעורים שלי</h2></div>
      {lessons_html}
    </section>

    {booking_section}

    {money_section}

    <section class="sec">
      <div class="sec-head"><h2>📚 מה למדנו לאחרונה</h2></div>
      {hw_html}
      {past_html}
    </section>

    <div class="footer">העמוד מתעדכן אוטומטית · כדאי לשמור אותו במסך הבית 📌</div>
  </div>

  <div class="sheet" id="sheet"><div class="sheet-in">
    <div class="sheet-grip"></div>
    <div class="sheet-when" id="sheet-when"></div>
    <textarea id="book-note" rows="2" maxlength="300" placeholder="רוצה להוסיף משהו למורה? (לא חובה)"></textarea>
    <button id="book-btn" class="btn" onclick="submitBooking()">שליחת בקשה</button>
    <button class="btn btn-ghost" onclick="closeSheet()">ביטול</button>
  </div></div>

  <div class="done" id="done"><div>
    <div class="done-ring">✓</div>
    <h3>הבקשה נשלחה!</h3>
    <p id="done-text"></p>
    <button class="btn" onclick="location.reload()">מעולה</button>
  </div></div>
  <div class="toast" id="toast"></div>

<script>
  const TOKEN = {token_js};
  const SLOTS = {slots_json};
  const DAYS = ['ראשון','שני','שלישי','רביעי','חמישי','שישי','שבת'];
  let chosen = null;
  function toast(msg) {{
    const t = document.getElementById('toast');
    t.textContent = msg; t.classList.add('show');
    clearTimeout(t._h); t._h = setTimeout(() => t.classList.remove('show'), 3500);
  }}
  function niceDate(iso) {{
    const d = new Date(iso + 'T00:00:00');
    return 'יום ' + DAYS[d.getDay()] + ', ' + d.getDate() + '/' + (d.getMonth() + 1);
  }}
  function pickDay(btn) {{
    document.querySelectorAll('.day.on').forEach(b => b.classList.remove('on'));
    btn.classList.add('on');
    const date = btn.dataset.date;
    document.getElementById('times-label').textContent = 'שעות פנויות ב' + niceDate(date);
    document.getElementById('times').innerHTML = (SLOTS[date] || []).map(t =>
      '<button type="button" class="slot" data-date="' + date + '" data-time="' + t + '" onclick="pickSlot(this)">' + t + '</button>').join('');
    closeSheet();
  }}
  function pickSlot(btn) {{
    document.querySelectorAll('.slot.on').forEach(b => b.classList.remove('on'));
    btn.classList.add('on');
    chosen = {{ date: btn.dataset.date, time: btn.dataset.time }};
    document.getElementById('sheet-when').innerHTML = niceDate(chosen.date) + ' · <span dir="ltr">' + chosen.time + '</span><small>השיעור ייקבע אחרי אישור המורה</small>';
    document.getElementById('sheet').classList.add('show');
  }}
  function closeSheet() {{
    document.getElementById('sheet').classList.remove('show');
    document.querySelectorAll('.slot.on').forEach(b => b.classList.remove('on'));
    chosen = null;
  }}
  async function submitBooking() {{
    if (!chosen) return;
    const btn = document.getElementById('book-btn');
    btn.disabled = true; btn.textContent = 'שולח...';
    try {{
      const r = await fetch('/portal/' + encodeURIComponent(TOKEN) + '/book', {{
        method: 'POST', headers: {{ 'Content-Type': 'application/json' }},
        body: JSON.stringify({{ date: chosen.date, time: chosen.time, note: document.getElementById('book-note').value }})
      }});
      const j = await r.json().catch(() => ({{}}));
      if (!r.ok) {{
        toast(j.error || 'שגיאה בשליחת הבקשה');
        btn.disabled = false; btn.textContent = 'שליחת בקשה';
        if (r.status === 409) setTimeout(() => location.reload(), 1800);
        return;
      }}
      document.getElementById('sheet').classList.remove('show');
      document.getElementById('done-text').textContent = 'ביקשת שיעור ב' + niceDate(chosen.date) + ' בשעה ' + chosen.time + '. ברגע שהמורה יאשר/תאשר, השיעור יופיע ברשימת השיעורים שלך.';
      document.getElementById('done').classList.add('show');
    }} catch (e) {{ toast('שגיאת תקשורת, נסו שוב'); btn.disabled = false; btn.textContent = 'שליחת בקשה'; }}
  }}
  async function cancelReq(id) {{
    if (!confirm('לבטל את הבקשה?')) return;
    const r = await fetch('/portal/' + encodeURIComponent(TOKEN) + '/cancel/' + encodeURIComponent(id), {{ method: 'POST' }});
    if (!r.ok) {{ toast('לא ניתן לבטל — ייתכן שהבקשה כבר טופלה'); setTimeout(() => location.reload(), 1500); return; }}
    location.reload();
  }}
  const firstDay = document.querySelector('.day');
  if (firstDay) pickDay(firstDay);
</script>
</body>
</html>'''
