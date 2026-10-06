#!/usr/bin/env python3
from flask import Flask, jsonify, request, session, send_from_directory
from werkzeug.security import generate_password_hash, check_password_hash
import json, os, threading, webbrowser, sqlite3, hashlib, secrets, uuid
from datetime import datetime, timedelta

BASE_DIR  = os.path.dirname(os.path.abspath(__file__))
USERS_DB  = os.path.join(BASE_DIR, 'users.db')
DATA_FILE = os.path.join(BASE_DIR, 'data.json')   # legacy – migrated to first user

app = Flask(__name__, static_folder=BASE_DIR)

# ── Set to True only when you want to allow new registrations ─────────────────
REGISTRATION_OPEN = True

# ── Persistent secret key ──────────────────────────────────────────────────────
_sk_file = os.path.join(BASE_DIR, '.secret_key')
if os.path.exists(_sk_file):
    app.secret_key = open(_sk_file).read().strip()
else:
    app.secret_key = secrets.token_hex(32)
    open(_sk_file, 'w').write(app.secret_key)

# ── SQLite – users ─────────────────────────────────────────────────────────────
def get_db():
    c = sqlite3.connect(USERS_DB)
    c.row_factory = sqlite3.Row
    return c

def init_db():
    c = get_db()
    c.execute('''CREATE TABLE IF NOT EXISTS users (
        id            TEXT PRIMARY KEY,
        email         TEXT UNIQUE NOT NULL,
        password_hash TEXT NOT NULL,
        name          TEXT,
        created_at    TEXT
    )''')
    cols = [r[1] for r in c.execute('PRAGMA table_info(users)').fetchall()]
    if 'cal_token' not in cols:
        c.execute('ALTER TABLE users ADD COLUMN cal_token TEXT')
    if 'approved' not in cols:
        c.execute('ALTER TABLE users ADD COLUMN approved INTEGER DEFAULT 1')
    if 'reset_token' not in cols:
        c.execute('ALTER TABLE users ADD COLUMN reset_token TEXT')
    if 'reset_token_expires' not in cols:
        c.execute('ALTER TABLE users ADD COLUMN reset_token_expires TEXT')
    c.commit()
    c.close()

init_db()

def hash_pw(pw):
    # Salted PBKDF2 (werkzeug default) — every new/rehashed password uses this.
    return generate_password_hash(pw)

def verify_pw(stored_hash, pw):
    if stored_hash and (stored_hash.startswith('pbkdf2:') or stored_hash.startswith('scrypt:')):
        return check_password_hash(stored_hash, pw)
    # Legacy unsalted SHA-256 from before the security upgrade — rehashed on
    # next successful login (see auth_login()), so this fades out over time.
    return stored_hash == hashlib.sha256(pw.encode('utf-8')).hexdigest()

# ── Per-user data files ────────────────────────────────────────────────────────
def user_data_file(uid):
    return os.path.join(BASE_DIR, f'data_{uid}.json')

def empty_data():
    return {'students': [], 'lessons': [], 'payments': []}

def read_data(uid=None):
    uid = uid or session.get('user_id')
    f = user_data_file(uid)
    if not os.path.exists(f):
        return empty_data()
    with open(f, encoding='utf-8') as fp:
        return json.load(fp)

def write_data(data, uid=None):
    uid = uid or session.get('user_id')
    with open(user_data_file(uid), 'w', encoding='utf-8') as fp:
        json.dump(data, fp, ensure_ascii=False, indent=2)

# ── Auth helper ────────────────────────────────────────────────────────────────
def require_auth():
    if 'user_id' not in session:
        return jsonify({'error': 'Unauthorized'}), 401
    return None

ADMIN_EMAIL = 'dekelkartel@gmail.com'
def require_admin():
    err = require_auth()
    if err: return err
    if (session.get('user_email') or '').lower() != ADMIN_EMAIL.lower():
        return jsonify({'error': 'Forbidden'}), 403
    return None

# ── Serve HTML ─────────────────────────────────────────────────────────────────
@app.route('/')
def index():
    with open(os.path.join(BASE_DIR, 'tutor-app.html'), encoding='utf-8') as f:
        return f.read(), 200, {'Content-Type': 'text/html; charset=utf-8'}

# ── PWA static files (manifest/icons/service worker) ────────────────────────────
# Note: push-notification API routes are production-only (see flask_app.py) since
# they need the real public APP_URL the GitHub Actions cron job calls. Locally the
# bell button will 404 if clicked — everything else works the same.
@app.route('/manifest.json')
def web_manifest():
    with open(os.path.join(BASE_DIR, 'manifest.json'), 'r', encoding='utf-8') as f:
        return f.read(), 200, {'Content-Type': 'application/manifest+json; charset=utf-8'}

@app.route('/icons/<path:filename>')
def app_icons(filename):
    return send_from_directory(os.path.join(BASE_DIR, 'icons'), filename)

@app.route('/sw.js')
def service_worker():
    with open(os.path.join(BASE_DIR, 'sw.js'), 'r', encoding='utf-8') as f:
        return f.read(), 200, {'Content-Type': 'application/javascript; charset=utf-8'}

# ── Auth endpoints ─────────────────────────────────────────────────────────────
@app.route('/api/auth/me')
def auth_me():
    if 'user_id' not in session:
        return jsonify({'error': 'Unauthorized'}), 401
    return jsonify({
        'id':    session['user_id'],
        'name':  session.get('user_name', ''),
        'email': session.get('user_email', ''),
        'isAdmin': (session.get('user_email') or '').lower() == ADMIN_EMAIL.lower(),
    })

@app.route('/api/auth/login', methods=['POST'])
def auth_login():
    body  = request.json or {}
    email = (body.get('email') or '').strip().lower()
    pw    = body.get('password', '')
    if not email or not pw:
        return jsonify({'error': 'נא למלא אימייל וסיסמה'}), 400
    c    = get_db()
    user = c.execute('SELECT * FROM users WHERE email=?', [email]).fetchone()
    if not user or not verify_pw(user['password_hash'], pw):
        c.close()
        return jsonify({'error': 'אימייל או סיסמה שגויים'}), 401
    if not (user['password_hash'].startswith('pbkdf2:') or user['password_hash'].startswith('scrypt:')):
        c.execute('UPDATE users SET password_hash=? WHERE id=?', [hash_pw(pw), user['id']])
        c.commit()
    c.close()
    if not user['approved']:
        return jsonify({'error': 'החשבון שלך ממתין לאישור מנהל המערכת'}), 403
    session['user_id']    = user['id']
    session['user_name']  = user['name']
    session['user_email'] = user['email']
    return jsonify({'id': user['id'], 'name': user['name'], 'email': user['email'],
                     'isAdmin': user['email'].lower() == ADMIN_EMAIL.lower()})

@app.route('/api/auth/register', methods=['POST'])
def auth_register():
    if not REGISTRATION_OPEN:
        return jsonify({'error': 'ההרשמה סגורה. לפתיחת חשבון יש לפנות למנהל המערכת.'}), 403
    body  = request.json or {}
    email = (body.get('email') or '').strip().lower()
    pw    = body.get('password', '')
    name  = (body.get('name') or '').strip()
    if not email or not pw:
        return jsonify({'error': 'נא למלא אימייל וסיסמה'}), 400
    if len(pw) < 6:
        return jsonify({'error': 'הסיסמה חייבת להכיל לפחות 6 תווים'}), 400
    is_admin_account = email.lower() == ADMIN_EMAIL.lower()
    c = get_db()
    if c.execute('SELECT 1 FROM users WHERE email=?', [email]).fetchone():
        c.close()
        return jsonify({'error': 'כתובת האימייל כבר רשומה במערכת'}), 409
    # First user gets the legacy data.json migrated automatically
    is_first = c.execute('SELECT COUNT(*) FROM users').fetchone()[0] == 0
    uid = uuid.uuid4().hex[:12]
    approved = 1 if (is_first or is_admin_account) else 0
    c.execute('INSERT INTO users (id,email,password_hash,name,created_at,approved) VALUES (?,?,?,?,?,?)',
              [uid, email, hash_pw(pw), name, datetime.now().isoformat(), approved])
    c.commit()
    c.close()
    if is_first and os.path.exists(DATA_FILE):
        with open(DATA_FILE, encoding='utf-8') as f:
            legacy = json.load(f)
        write_data(legacy, uid)
    else:
        write_data(empty_data(), uid)
    if not approved:
        return jsonify({'pending': True,
                         'message': 'ההרשמה התקבלה! החשבון ימתין לאישור מנהל המערכת לפני שתוכל/י להתחבר.'}), 202
    session['user_id']    = uid
    session['user_name']  = name
    session['user_email'] = email
    return jsonify({'id': uid, 'name': name, 'email': email,
                     'isAdmin': is_admin_account}), 201

@app.route('/api/auth/logout', methods=['POST'])
def auth_logout():
    session.clear()
    return '', 204

@app.route('/api/auth/forgot-password', methods=['POST'])
def auth_forgot_password():
    email = ((request.json or {}).get('email') or '').strip().lower()
    generic = jsonify({'message': 'אם כתובת האימייל קיימת במערכת, נשלח אליה קישור לאיפוס סיסמה.'})
    if not email:
        return generic
    c = get_db()
    user = c.execute('SELECT id FROM users WHERE email=?', [email]).fetchone()
    if not user:
        c.close()
        return generic
    token = secrets.token_urlsafe(32)
    expires = (datetime.now() + timedelta(hours=1)).isoformat()
    c.execute('UPDATE users SET reset_token=?, reset_token_expires=? WHERE id=?', [token, expires, user['id']])
    c.commit()
    c.close()
    # Local dev has no email sending (same reason push-broadcast is a stub
    # here) — print the reset link so you can test the flow by hand.
    print(f'\n[local dev] password reset link: http://localhost:8080/?reset={token}\n')
    return generic

@app.route('/api/auth/reset-password', methods=['POST'])
def auth_reset_password():
    body  = request.json or {}
    token = (body.get('token') or '').strip()
    pw    = body.get('password', '')
    if not token:
        return jsonify({'error': 'קישור לא תקין'}), 400
    if len(pw) < 6:
        return jsonify({'error': 'הסיסמה חייבת להכיל לפחות 6 תווים'}), 400
    c = get_db()
    user = c.execute('SELECT id, reset_token_expires FROM users WHERE reset_token=?', [token]).fetchone()
    if not user or not user['reset_token_expires'] or datetime.fromisoformat(user['reset_token_expires']) < datetime.now():
        c.close()
        return jsonify({'error': 'הקישור פג תוקף או כבר נוצל. נא לבקש קישור חדש.'}), 400
    c.execute('UPDATE users SET password_hash=?, reset_token=NULL, reset_token_expires=NULL WHERE id=?',
              [hash_pw(pw), user['id']])
    c.commit()
    c.close()
    return jsonify({'ok': True})

# ── Students ───────────────────────────────────────────────────────────────────
@app.route('/api/students', methods=['GET'])
def get_students():
    err = require_auth()
    if err: return err
    return jsonify(read_data()['students'])

@app.route('/api/students', methods=['POST'])
def add_student():
    err = require_auth()
    if err: return err
    data = read_data()
    student = request.json
    data['students'].append(student)
    write_data(data)
    return jsonify(student), 201

@app.route('/api/students/<sid>', methods=['PUT'])
def update_student(sid):
    err = require_auth()
    if err: return err
    data = read_data()
    data['students'] = [request.json if s['id'] == sid else s for s in data['students']]
    write_data(data)
    return jsonify(request.json)

@app.route('/api/students/<sid>', methods=['DELETE'])
def delete_student(sid):
    err = require_auth()
    if err: return err
    data = read_data()
    data['students'] = [s for s in data['students'] if s['id'] != sid]
    data['lessons']  = [l for l in data['lessons']  if l['studentId'] != sid]
    data['payments'] = [p for p in data['payments'] if p['studentId'] != sid]
    write_data(data)
    return '', 204

@app.route('/api/students/<sid>/share-link', methods=['GET'])
def student_share_link(sid):
    err = require_auth()
    if err: return err
    data = read_data()
    student = next((s for s in data['students'] if s['id'] == sid), None)
    if not student:
        return jsonify({'error': 'Not found'}), 404
    if not student.get('shareToken'):
        student['shareToken'] = secrets.token_urlsafe(20)
        write_data(data)
    return jsonify({'token': student['shareToken']})

# ── Parent/student portal (public, no login — found by share token) ─────────────
@app.route('/portal/<token>')
def student_portal(token):
    import html as html_mod
    found = None
    for fn in os.listdir(BASE_DIR):
        if fn.startswith('data_') and fn.endswith('.json'):
            try:
                with open(os.path.join(BASE_DIR, fn), encoding='utf-8') as f:
                    d = json.load(f)
                for s in d.get('students', []):
                    if s.get('shareToken') == token:
                        found = (d, s)
                        break
            except Exception:
                pass
        if found:
            break
    if not found:
        return 'הקישור לא נמצא או שפג תוקפו', 404
    d, student = found
    sid_ = student['id']
    lessons  = [l for l in d.get('lessons', [])  if l['studentId'] == sid_]
    payments = [p for p in d.get('payments', []) if p['studentId'] == sid_]

    today = datetime.now().strftime('%Y-%m-%d')
    upcoming = sorted([l for l in lessons if l['date'] >= today], key=lambda l: (l['date'], l.get('time', '')))
    past = sorted([l for l in lessons if l['date'] < today], key=lambda l: (l['date'], l.get('time', '')), reverse=True)[:8]

    total_lessons = sum(l.get('amount', 0) or 0 for l in lessons)
    total_lesson_paid = sum(l.get('paidAmount', 0) or 0 for l in lessons)
    total_payments = sum(p.get('amount', 0) or 0 for p in payments)
    balance = total_payments + total_lesson_paid - total_lessons

    def esc(x):
        return html_mod.escape(str(x or ''))

    def fmt_date(iso):
        try:
            y, m, d = iso.split('-')
            return f'{d}/{m}/{y}'
        except Exception:
            return iso or ''

    def lesson_row(l, show_status):
        topic = esc(l.get('topic'))
        homework = esc(l.get('homework'))
        extra = ''
        if topic:
            extra += f'<div class="portal-lesson-extra">📘 {topic}</div>'
        if homework:
            extra += f'<div class="portal-lesson-extra">📝 שיעורי בית: {homework}</div>'
        status_html = ''
        if show_status:
            paid = l.get('isPaid')
            partial = (l.get('paidAmount') or 0) > 0
            label = 'שולם' if paid else ('שולם חלקית' if partial else 'ממתין לתשלום')
            cls = 'paid' if paid else ('partial' if partial else 'unpaid')
            status_html = f'<span class="portal-badge {cls}">{label}</span>'
        return f'''<div class="portal-lesson">
          <div class="portal-lesson-head">
            <span class="portal-lesson-date">{esc(fmt_date(l["date"]))} {esc(l.get("time",""))}</span>
            {status_html}
          </div>
          {extra}
        </div>'''

    upcoming_html = ''.join(lesson_row(l, False) for l in upcoming) or '<p class="portal-empty">אין שיעורים קרובים כרגע</p>'
    past_html = ''.join(lesson_row(l, True) for l in past) or '<p class="portal-empty">אין שיעורים קודמים</p>'
    balance_label = 'לתשלום' if balance < 0 else ('זכות' if balance > 0 else 'מאוזן')
    balance_abs = abs(balance)
    balance_class = 'debt' if balance < 0 else ('credit' if balance > 0 else 'even')

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
  .portal-empty {{ color: var(--muted); font-size: 13px; text-align:center; padding: 10px 0; }}
  .portal-footer {{ text-align:center; color: var(--muted); font-size: 11.5px; margin-top: 20px; }}
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
</body>
</html>'''
    return page, 200, {'Content-Type': 'text/html; charset=utf-8'}

# ── Lessons ────────────────────────────────────────────────────────────────────
@app.route('/api/lessons', methods=['GET'])
def get_lessons():
    err = require_auth()
    if err: return err
    return jsonify(read_data()['lessons'])

@app.route('/api/lessons', methods=['POST'])
def add_lesson():
    err = require_auth()
    if err: return err
    data = read_data()
    lesson = request.json
    data['lessons'].append(lesson)
    write_data(data)
    return jsonify(lesson), 201

@app.route('/api/lessons/<lid>', methods=['PUT'])
def update_lesson(lid):
    err = require_auth()
    if err: return err
    data = read_data()
    data['lessons'] = [request.json if l['id'] == lid else l for l in data['lessons']]
    write_data(data)
    return jsonify(request.json)

@app.route('/api/lessons/<lid>', methods=['DELETE'])
def delete_lesson(lid):
    err = require_auth()
    if err: return err
    data = read_data()
    data['lessons'] = [l for l in data['lessons'] if l['id'] != lid]
    write_data(data)
    return '', 204

# ── Payments ───────────────────────────────────────────────────────────────────
@app.route('/api/payments', methods=['GET'])
def get_payments():
    err = require_auth()
    if err: return err
    return jsonify(read_data()['payments'])

@app.route('/api/payments', methods=['POST'])
def add_payment():
    err = require_auth()
    if err: return err
    data = read_data()
    payment = request.json
    data['payments'].append(payment)
    write_data(data)
    return jsonify(payment), 201

@app.route('/api/payments/<pid>', methods=['DELETE'])
def delete_payment(pid):
    err = require_auth()
    if err: return err
    data = read_data()
    data['payments'] = [p for p in data['payments'] if p['id'] != pid]
    write_data(data)
    return '', 204

# ── Announcements (in-app banner) ────────────────────────────────────────────
@app.route('/api/announcement')
def get_announcement():
    err = require_auth()
    if err: return err
    ann_path = os.path.join(BASE_DIR, 'announcement.json')
    if not os.path.exists(ann_path):
        return jsonify(None)
    with open(ann_path, encoding='utf-8') as f:
        return f.read(), 200, {'Content-Type': 'application/json; charset=utf-8'}

# ── Admin screen (announcement only — push broadcast needs the real deployed
# GitHub Action, not meaningful against this local dev server) ─────────────────
ANN_PATH = os.path.join(BASE_DIR, 'announcement.json')

@app.route('/api/admin/announcement', methods=['GET'])
def admin_get_announcement():
    err = require_admin()
    if err: return err
    if not os.path.exists(ANN_PATH):
        return jsonify(None)
    with open(ANN_PATH, encoding='utf-8') as f:
        return f.read(), 200, {'Content-Type': 'application/json; charset=utf-8'}

@app.route('/api/admin/announcement', methods=['POST'])
def admin_set_announcement():
    err = require_admin()
    if err: return err
    message = (request.json or {}).get('message', '').strip()
    if not message:
        if os.path.exists(ANN_PATH):
            os.remove(ANN_PATH)
        return jsonify({'ok': True, 'cleared': True})
    ann = {'id': datetime.now().isoformat(), 'message': message}
    with open(ANN_PATH, 'w', encoding='utf-8') as f:
        json.dump(ann, f, ensure_ascii=False)
    return jsonify({'ok': True, 'announcement': ann})

@app.route('/api/admin/push-broadcast', methods=['POST'])
def admin_push_broadcast():
    err = require_admin()
    if err: return err
    return jsonify({'error': 'שידור Push לא נתמך בשרת המקומי — רק בפרודקשן', 'manual_fallback': True}), 503

@app.route('/api/admin/stats')
def admin_stats():
    err = require_admin()
    if err: return err
    c = get_db()
    active_users  = c.execute('SELECT COUNT(*) FROM users WHERE approved=1').fetchone()[0]
    pending_users = c.execute('SELECT COUNT(*) FROM users WHERE approved=0').fetchone()[0]
    c.close()
    all_students = []
    for fn in os.listdir(BASE_DIR):
        if fn.startswith('data_') and fn.endswith('.json'):
            try:
                with open(os.path.join(BASE_DIR, fn), encoding='utf-8') as f:
                    all_students += json.load(f).get('students', [])
            except Exception:
                pass
    active_students = sum(1 for s in all_students if s.get('isActive') is not False)
    month_prefix = datetime.now().strftime('%Y-%m')
    lessons_month = 0
    for fn in os.listdir(BASE_DIR):
        if fn.startswith('data_') and fn.endswith('.json'):
            try:
                with open(os.path.join(BASE_DIR, fn), encoding='utf-8') as f:
                    lessons = json.load(f).get('lessons', [])
                lessons_month += sum(1 for l in lessons if l.get('date', '').startswith(month_prefix))
            except Exception:
                pass
    return jsonify({
        'activeUsers': active_users, 'pendingUsers': pending_users,
        'activeStudents': active_students, 'totalStudents': len(all_students),
        'lessonsThisMonth': lessons_month,
    })

@app.route('/api/admin/pending-users')
def admin_pending_users():
    err = require_admin()
    if err: return err
    c = get_db()
    rows = c.execute('SELECT id, name, email, created_at FROM users WHERE approved=0 ORDER BY created_at').fetchall()
    c.close()
    return jsonify([dict(r) for r in rows])

@app.route('/api/admin/users')
def admin_users():
    err = require_admin()
    if err: return err
    c = get_db()
    rows = c.execute('SELECT id, name, email, created_at FROM users WHERE approved=1 ORDER BY created_at DESC').fetchall()
    c.close()
    out = []
    for u in rows:
        d = dict(u)
        data = read_data(u['id'])
        d['studentCount'] = len(data.get('students', []))
        d['activeStudentCount'] = sum(1 for s in data.get('students', []) if s.get('isActive') is not False)
        d['lessonCount'] = len(data.get('lessons', []))
        d['isAdmin'] = u['email'].lower() == ADMIN_EMAIL.lower()
        out.append(d)
    return jsonify(out)

@app.route('/api/admin/users/<target_id>/students')
def admin_user_students(target_id):
    err = require_admin()
    if err: return err
    data = read_data(target_id)
    out = [{'id': s.get('id'), 'name': s.get('name'), 'isActive': s.get('isActive') is not False}
           for s in data.get('students', [])]
    return jsonify(out)

@app.route('/api/admin/users/<target_id>/approve', methods=['POST'])
def admin_approve_user(target_id):
    err = require_admin()
    if err: return err
    c = get_db()
    user = c.execute('SELECT email, name FROM users WHERE id=?', [target_id]).fetchone()
    c.execute('UPDATE users SET approved=1 WHERE id=?', [target_id])
    c.commit(); c.close()
    if user:
        # Local dev has no email sending — see forgot-password for the same pattern.
        print(f"\n[local dev] would send approval email to {user['email']}\n")
    return jsonify({'ok': True})

@app.route('/api/admin/users/<target_id>/reject', methods=['POST'])
def admin_reject_user(target_id):
    err = require_admin()
    if err: return err
    c = get_db()
    c.execute('DELETE FROM users WHERE id=? AND approved=0', [target_id])
    c.commit(); c.close()
    return jsonify({'ok': True})

@app.route('/api/admin/users/<target_id>/suspend', methods=['POST'])
def admin_suspend_user(target_id):
    err = require_admin()
    if err: return err
    c = get_db()
    c.execute('UPDATE users SET approved=0 WHERE id=? AND email!=?', [target_id, ADMIN_EMAIL])
    c.commit(); c.close()
    return jsonify({'ok': True})

# ── Live ICS calendar feed ─────────────────────────────────────────────────────
@app.route('/api/calendar-token')
def get_calendar_token():
    err = require_auth()
    if err: return err
    with get_db() as c:
        row = c.execute('SELECT cal_token FROM users WHERE id=?', [session['user_id']]).fetchone()
        token = row['cal_token'] if row else None
        if not token:
            token = secrets.token_urlsafe(24)
            c.execute('UPDATE users SET cal_token=? WHERE id=?', [token, session['user_id']])
            c.commit()
    return jsonify({'token': token})

@app.route('/calendar.ics')
def calendar_ics():
    # External calendar apps subscribing by URL never send our session
    # cookie, so they authenticate via ?token=... instead (see above).
    cal_user_id = session.get('user_id')
    if not cal_user_id:
        token = request.args.get('token')
        if token:
            with get_db() as c:
                row = c.execute('SELECT id FROM users WHERE cal_token=?', [token]).fetchone()
                cal_user_id = row['id'] if row else None
    if not cal_user_id:
        return 'Unauthorized', 401
    data     = read_data(cal_user_id)
    students = {s['id']: s for s in data['students']}
    stamp    = datetime.utcnow().strftime('%Y%m%dT%H%M%SZ')
    lines = [
        'BEGIN:VCALENDAR', 'VERSION:2.0',
        'PRODID:-//שיעורים פרטיים//HE', 'CALSCALE:GREGORIAN',
        'METHOD:PUBLISH', 'X-WR-CALNAME:שיעורים - הוראה',
        'X-WR-TIMEZONE:Asia/Jerusalem',
        'REFRESH-INTERVAL;VALUE=DURATION:PT1H',
    ]
    for l in data['lessons']:
        s    = students.get(l['studentId'], {})
        name = s.get('name', 'תלמיד')
        date = l['date'].replace('-', '')
        hh, mm = map(int, (l.get('time', '16:00')).split(':'))
        start_min = hh * 60 + mm
        lesson_minutes = s.get('lessonDurationMinutes') or 50
        end_min   = start_min + int((l.get('durationHours', 1) or 1) * lesson_minutes)
        sh = str(start_min // 60).zfill(2)
        sm = str(start_min % 60).zfill(2)
        eh = str((end_min // 60) % 24).zfill(2)
        em = str(end_min % 60).zfill(2)
        lines += [
            'BEGIN:VEVENT',
            f'UID:{l["id"]}@tutor-app',
            f'DTSTAMP:{stamp}',
            f'DTSTART;TZID=Asia/Jerusalem:{date}T{sh}{sm}00',
            f'DTEND;TZID=Asia/Jerusalem:{date}T{eh}{em}00',
            f'SUMMARY:שיעור - {name}',
            f'DESCRIPTION:{l.get("notes","") or ""}',
            'END:VEVENT',
        ]
    lines.append('END:VCALENDAR')
    return '\r\n'.join(lines), 200, {
        'Content-Type': 'text/calendar; charset=utf-8',
        # ASCII only — a raw Hebrew filename here crashed the response mid-stream.
        'Content-Disposition': 'inline; filename="lessons.ics"',
    }

# ── Run ────────────────────────────────────────────────────────────────────────
if __name__ == '__main__':
    def open_browser():
        webbrowser.open('http://localhost:8080')
    threading.Timer(1.0, open_browser).start()
    print('\n✅  שרת רץ על http://localhost:8080')
    print('   לעצירה: Ctrl+C\n')
    app.run(host='0.0.0.0', port=8080, debug=False)
