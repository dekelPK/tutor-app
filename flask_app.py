#!/usr/bin/env python3
"""
flask_app.py — PythonAnywhere WSGI entry point
מסד נתונים: SQLite (tutor.db)
מיקום: /home/DekelPA/mysite/flask_app.py
"""
from flask import Flask, jsonify, request, session, send_from_directory
import sqlite3, json, os, hashlib, secrets, uuid, hmac, subprocess, urllib.request, urllib.error
from datetime import datetime, timedelta
try:
    from zoneinfo import ZoneInfo
    ISRAEL_TZ = ZoneInfo('Asia/Jerusalem')
except Exception:
    ISRAEL_TZ = None  # falls back to naive UTC date — a few hours off around midnight

BASE_DIR = '/home/DekelPA/mysite'
DB_PATH  = os.path.join(BASE_DIR, 'tutor.db')

app = Flask(__name__, static_folder=BASE_DIR)
app.permanent_session_lifetime = timedelta(days=30)

# ── Set to True only when you want to allow new registrations ─────────────────
REGISTRATION_OPEN = False

# ── Push notifications (Web Push / VAPID) ──────────────────────────────────────
# Public key only — safe to hardcode, it's meant to be public. The matching
# PRIVATE key never lives on this server; it's kept as a GitHub Actions secret
# and used only by the scheduled job that actually sends the push (see
# .github/workflows/daily-lesson-reminders.yml and scripts/send_reminders.py).
# This keeps PythonAnywhere's free-tier outbound-internet whitelist out of the
# picture entirely — this server only ever stores subscriptions and answers
# "who needs a reminder today", it never calls out to Apple's push service.
VAPID_PUBLIC_KEY = 'BItvzT-o_02tFQo_a61eRWe3lZ29jS6X6jKtWtwx3nFKJbzmslGFG9IKKiMCxuPfew88jBDeWefZMjv9XJIzczQ'

# Shared secret the scheduled GitHub Actions job presents to /api/push/due-today.
# Must match the CRON_SECRET GitHub Actions secret exactly. Generated once and
# persisted to disk (same pattern as .secret_key) so it survives restarts.
_cs_file = os.path.join(BASE_DIR, '.cron_secret')
if os.path.exists(_cs_file):
    CRON_SECRET = open(_cs_file).read().strip()
else:
    CRON_SECRET = secrets.token_hex(24)
    open(_cs_file, 'w').write(CRON_SECRET)

# ── Auto-deploy webhook config (GitHub push → git pull → PythonAnywhere reload)
# Lives in .deploy_config.json, which is gitignored and created once by hand on
# the server (never committed — it holds a real API token). Shape:
#   {"webhook_secret": "...", "pa_api_token": "...", "pa_username": "...", "pa_domain": "..."}
# If missing, /deploy-webhook just responds 'not configured' — everything else
# in the app works fine either way.
_deploy_cfg_file = os.path.join(BASE_DIR, '.deploy_config.json')
def load_deploy_config():
    if not os.path.exists(_deploy_cfg_file):
        return None
    try:
        with open(_deploy_cfg_file) as f:
            return json.load(f)
    except Exception:
        return None

# ── Secret key (persist across restarts) ──────────────────────────────────────
_sk_file = os.path.join(BASE_DIR, '.secret_key')
if os.path.exists(_sk_file):
    app.secret_key = open(_sk_file).read().strip()
else:
    app.secret_key = secrets.token_hex(32)
    open(_sk_file, 'w').write(app.secret_key)

# ── DB helpers ────────────────────────────────────────────────────────────────
def get_db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn

def init_db():
    with get_db() as db:
        db.executescript('''
            CREATE TABLE IF NOT EXISTS users (
                id            TEXT PRIMARY KEY,
                email         TEXT UNIQUE NOT NULL,
                password_hash TEXT NOT NULL,
                name          TEXT,
                created_at    TEXT
            );
            CREATE TABLE IF NOT EXISTS students (
                id      TEXT PRIMARY KEY,
                user_id TEXT,
                data    TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS lessons (
                id        TEXT PRIMARY KEY,
                user_id   TEXT,
                studentId TEXT NOT NULL,
                date      TEXT NOT NULL,
                data      TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS payments (
                id        TEXT PRIMARY KEY,
                user_id   TEXT,
                studentId TEXT NOT NULL,
                date      TEXT NOT NULL,
                data      TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS push_subscriptions (
                id         TEXT PRIMARY KEY,
                user_id    TEXT NOT NULL,
                endpoint   TEXT NOT NULL UNIQUE,
                p256dh     TEXT NOT NULL,
                auth       TEXT NOT NULL,
                created_at TEXT
            );
        ''')
        # Add user_id column to existing tables if missing (safe to run multiple times)
        for tbl in ('students', 'lessons', 'payments'):
            cols = [r[1] for r in db.execute(f'PRAGMA table_info({tbl})').fetchall()]
            if 'user_id' not in cols:
                db.execute(f'ALTER TABLE {tbl} ADD COLUMN user_id TEXT')
        # cal_token: a per-user secret for the calendar-subscription URL (see
        # calendar_ics()) — external calendar apps can't send our session
        # cookie, so the feed needs its own token-based auth instead.
        user_cols = [r[1] for r in db.execute('PRAGMA table_info(users)').fetchall()]
        if 'cal_token' not in user_cols:
            db.execute('ALTER TABLE users ADD COLUMN cal_token TEXT')

    # One-time migration from data.json if it exists
    json_path = os.path.join(BASE_DIR, 'data.json')
    if os.path.exists(json_path):
        try:
            with open(json_path, 'r', encoding='utf-8') as f:
                data = json.load(f)
            with get_db() as db:
                for s in data.get('students', []):
                    db.execute('INSERT OR IGNORE INTO students(id,data) VALUES (?,?)',
                               [s['id'], json.dumps(s, ensure_ascii=False)])
                for l in data.get('lessons', []):
                    db.execute('INSERT OR IGNORE INTO lessons(id,studentId,date,data) VALUES (?,?,?,?)',
                               [l['id'], l.get('studentId',''), l.get('date',''),
                                json.dumps(l, ensure_ascii=False)])
                for p in data.get('payments', []):
                    db.execute('INSERT OR IGNORE INTO payments(id,studentId,date,data) VALUES (?,?,?,?)',
                               [p['id'], p.get('studentId',''), p.get('date',''),
                                json.dumps(p, ensure_ascii=False)])
            os.rename(json_path, json_path + '.migrated')
        except Exception as e:
            print(f'Migration error: {e}')

init_db()

def rows_to_list(rows):
    return [json.loads(r['data']) for r in rows]

def hash_pw(pw):
    return hashlib.sha256(pw.encode('utf-8')).hexdigest()

def require_auth():
    if 'user_id' not in session:
        return jsonify({'error': 'Unauthorized'}), 401
    return None

def uid():
    return session.get('user_id')

# ── Serve HTML ────────────────────────────────────────────────────────────────
@app.route('/')
def index():
    with open(os.path.join(BASE_DIR, 'tutor-app.html'), 'r', encoding='utf-8') as f:
        return f.read(), 200, {'Content-Type': 'text/html; charset=utf-8'}

# ── Auth ──────────────────────────────────────────────────────────────────────
@app.route('/api/auth/me')
def auth_me():
    if 'user_id' not in session:
        return jsonify({'error': 'Unauthorized'}), 401
    return jsonify({'id': session['user_id'], 'name': session.get('user_name',''), 'email': session.get('user_email','')})

@app.route('/api/auth/login', methods=['POST'])
def auth_login():
    body  = request.json or {}
    email = (body.get('email') or '').strip().lower()
    pw    = body.get('password', '')
    if not email or not pw:
        return jsonify({'error': 'נא למלא אימייל וסיסמה'}), 400
    with get_db() as db:
        user = db.execute('SELECT * FROM users WHERE email=? AND password_hash=?',
                          [email, hash_pw(pw)]).fetchone()
    if not user:
        return jsonify({'error': 'אימייל או סיסמה שגויים'}), 401
    session.permanent = True
    session['user_id']    = user['id']
    session['user_name']  = user['name']
    session['user_email'] = user['email']
    return jsonify({'id': user['id'], 'name': user['name'], 'email': user['email']})

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
    with get_db() as db:
        if db.execute('SELECT 1 FROM users WHERE email=?', [email]).fetchone():
            return jsonify({'error': 'כתובת האימייל כבר רשומה במערכת'}), 409
        is_first = db.execute('SELECT COUNT(*) FROM users').fetchone()[0] == 0
        new_id   = uuid.uuid4().hex[:12]
        db.execute('INSERT INTO users (id,email,password_hash,name,created_at) VALUES (?,?,?,?,?)',
                   [new_id, email, hash_pw(pw), name, datetime.now().isoformat()])
        if is_first:
            # Associate all existing data (no user_id) with this first user
            for tbl in ('students', 'lessons', 'payments'):
                db.execute(f'UPDATE {tbl} SET user_id=? WHERE user_id IS NULL', [new_id])
    session.permanent = True
    session['user_id']    = new_id
    session['user_name']  = name
    session['user_email'] = email
    return jsonify({'id': new_id, 'name': name, 'email': email}), 201

@app.route('/api/auth/logout', methods=['POST'])
def auth_logout():
    session.clear()
    return '', 204

# ── Push notifications ───────────────────────────────────────────────────────
@app.route('/sw.js')
def service_worker():
    with open(os.path.join(BASE_DIR, 'sw.js'), 'r', encoding='utf-8') as f:
        return f.read(), 200, {'Content-Type': 'application/javascript; charset=utf-8'}

@app.route('/manifest.json')
def web_manifest():
    with open(os.path.join(BASE_DIR, 'manifest.json'), 'r', encoding='utf-8') as f:
        return f.read(), 200, {'Content-Type': 'application/manifest+json; charset=utf-8'}

@app.route('/icons/<path:filename>')
def app_icons(filename):
    return send_from_directory(os.path.join(BASE_DIR, 'icons'), filename)

# ── Auto-deploy webhook ──────────────────────────────────────────────────────
@app.route('/deploy-webhook', methods=['POST'])
def deploy_webhook():
    cfg = load_deploy_config()
    if not cfg:
        return jsonify({'error': 'not configured'}), 503

    # Verify this really came from GitHub (HMAC-SHA256 over the raw body,
    # using the webhook secret set on both sides) — never git-pull on an
    # unauthenticated request.
    sig = request.headers.get('X-Hub-Signature-256', '')
    expected = 'sha256=' + hmac.new(cfg['webhook_secret'].encode(), request.get_data(), hashlib.sha256).hexdigest()
    if not hmac.compare_digest(sig, expected):
        return jsonify({'error': 'bad signature'}), 401

    event = request.headers.get('X-GitHub-Event', '')
    if event == 'ping':
        return jsonify({'ok': True, 'msg': 'pong'})
    if event != 'push':
        return jsonify({'ok': True, 'msg': f'ignored event: {event}'})

    body = request.json or {}
    if body.get('ref') != 'refs/heads/main':
        return jsonify({'ok': True, 'msg': 'ignored non-main push'})

    try:
        pull = subprocess.run(['git', 'pull', 'origin', 'main'], cwd=BASE_DIR,
                               capture_output=True, text=True, timeout=60)
        pull_ok = pull.returncode == 0
    except Exception as e:
        return jsonify({'ok': False, 'step': 'git pull', 'error': str(e)}), 500
    if not pull_ok:
        return jsonify({'ok': False, 'step': 'git pull', 'stdout': pull.stdout, 'stderr': pull.stderr}), 500

    # Reload the web app via the PythonAnywhere API (outbound to
    # pythonanywhere.com itself, which free accounts can always reach).
    try:
        url = f"https://www.pythonanywhere.com/api/v0/user/{cfg['pa_username']}/webapps/{cfg['pa_domain']}/reload/"
        req = urllib.request.Request(url, method='POST', headers={'Authorization': f"Token {cfg['pa_api_token']}"})
        with urllib.request.urlopen(req, timeout=30) as resp:
            reload_ok = 200 <= resp.status < 300
    except urllib.error.HTTPError as e:
        return jsonify({'ok': False, 'step': 'reload', 'pulled': True, 'status': e.code, 'body': e.read().decode(errors='replace')}), 500
    except Exception as e:
        return jsonify({'ok': False, 'step': 'reload', 'pulled': True, 'error': str(e)}), 500

    return jsonify({'ok': True, 'pulled': True, 'reloaded': reload_ok, 'commit': pull.stdout.strip()})

@app.route('/api/push/vapid-public-key')
def push_vapid_key():
    return jsonify({'publicKey': VAPID_PUBLIC_KEY})

@app.route('/api/push/subscribe', methods=['POST'])
def push_subscribe():
    err = require_auth()
    if err: return err
    body = request.json or {}
    endpoint = body.get('endpoint')
    keys = body.get('keys') or {}
    p256dh, auth_key = keys.get('p256dh'), keys.get('auth')
    if not endpoint or not p256dh or not auth_key:
        return jsonify({'error': 'Invalid subscription'}), 400
    with get_db() as db:
        db.execute('''INSERT INTO push_subscriptions(id,user_id,endpoint,p256dh,auth,created_at)
                      VALUES (?,?,?,?,?,?)
                      ON CONFLICT(endpoint) DO UPDATE SET user_id=excluded.user_id,
                        p256dh=excluded.p256dh, auth=excluded.auth''',
                   [uuid.uuid4().hex[:12], uid(), endpoint, p256dh, auth_key, datetime.now().isoformat()])
    return jsonify({'ok': True}), 201

@app.route('/api/push/unsubscribe', methods=['POST'])
def push_unsubscribe():
    err = require_auth()
    if err: return err
    endpoint = (request.json or {}).get('endpoint')
    with get_db() as db:
        db.execute('DELETE FROM push_subscriptions WHERE endpoint=? AND user_id=?', [endpoint, uid()])
    return '', 204

@app.route('/api/push/due-today')
def push_due_today():
    # Secret-protected: called by the scheduled GitHub Actions job, not by the browser.
    if request.args.get('token') != CRON_SECRET:
        return jsonify({'error': 'Unauthorized'}), 401
    today = datetime.now(ISRAEL_TZ).strftime('%Y-%m-%d') if ISRAEL_TZ else datetime.utcnow().strftime('%Y-%m-%d')
    with get_db() as db:
        subs = db.execute('SELECT * FROM push_subscriptions').fetchall()
        out = []
        for sub in subs:
            lessons = db.execute(
                'SELECT data FROM lessons WHERE user_id=? AND date=?', [sub['user_id'], today]
            ).fetchall()
            if not lessons:
                continue
            students = {s['id']: s for s in rows_to_list(
                db.execute('SELECT data FROM students WHERE user_id=?', [sub['user_id']]).fetchall())}
            lesson_list = rows_to_list(lessons)
            if len(lesson_list) == 1:
                s = students.get(lesson_list[0]['studentId'], {})
                body = f"שיעור עם {s.get('name','תלמיד')} בשעה {lesson_list[0].get('time','')}"
            else:
                body = f"{len(lesson_list)} שיעורים מתוכננים היום"
            out.append({
                'endpoint': sub['endpoint'],
                'p256dh': sub['p256dh'],
                'auth': sub['auth'],
                'title': 'תזכורת: יש לך שיעור היום 📚',
                'body': body,
                'url': '/',
            })
    return jsonify(out)

@app.route('/api/push/due-today', methods=['DELETE'])
def push_remove_stale():
    # Called by the scheduled job when Apple reports a subscription as gone (410/404).
    if request.args.get('token') != CRON_SECRET:
        return jsonify({'error': 'Unauthorized'}), 401
    endpoint = (request.json or {}).get('endpoint')
    with get_db() as db:
        db.execute('DELETE FROM push_subscriptions WHERE endpoint=?', [endpoint])
    return '', 204

@app.route('/api/push/all-subscriptions')
def push_all_subscriptions():
    # Used by the manual broadcast-notification GitHub Action — every current
    # subscription, with no lesson-today filtering, so you (the developer) can
    # push an announcement to every user who's ever enabled reminders.
    if request.args.get('token') != CRON_SECRET:
        return jsonify({'error': 'Unauthorized'}), 401
    with get_db() as db:
        subs = db.execute('SELECT endpoint, p256dh, auth FROM push_subscriptions').fetchall()
    return jsonify([dict(s) for s in subs])

# ── Announcements (in-app banner to every logged-in user) ───────────────────────
@app.route('/api/announcement')
def get_announcement():
    err = require_auth()
    if err: return err
    ann_path = os.path.join(BASE_DIR, 'announcement.json')
    if not os.path.exists(ann_path):
        return jsonify(None)
    with open(ann_path, encoding='utf-8') as f:
        return f.read(), 200, {'Content-Type': 'application/json; charset=utf-8'}

# ── Students ──────────────────────────────────────────────────────────────────
@app.route('/api/students', methods=['GET'])
def get_students():
    err = require_auth()
    if err: return err
    with get_db() as db:
        rows = db.execute('SELECT data FROM students WHERE user_id=?', [uid()]).fetchall()
    return jsonify(rows_to_list(rows))

@app.route('/api/students', methods=['POST'])
def add_student():
    err = require_auth()
    if err: return err
    s = request.json
    with get_db() as db:
        db.execute('INSERT INTO students(id,user_id,data) VALUES (?,?,?)',
                   [s['id'], uid(), json.dumps(s, ensure_ascii=False)])
    return jsonify(s), 201

@app.route('/api/students/<sid>', methods=['PUT'])
def update_student(sid):
    err = require_auth()
    if err: return err
    s = request.json
    with get_db() as db:
        db.execute('UPDATE students SET data=? WHERE id=? AND user_id=?',
                   [json.dumps(s, ensure_ascii=False), sid, uid()])
    return jsonify(s)

@app.route('/api/students/<sid>', methods=['DELETE'])
def delete_student(sid):
    err = require_auth()
    if err: return err
    with get_db() as db:
        db.execute('DELETE FROM students WHERE id=? AND user_id=?', [sid, uid()])
        db.execute('DELETE FROM lessons  WHERE studentId=? AND user_id=?', [sid, uid()])
        db.execute('DELETE FROM payments WHERE studentId=? AND user_id=?', [sid, uid()])
    return '', 204

# ── Lessons ───────────────────────────────────────────────────────────────────
@app.route('/api/lessons', methods=['GET'])
def get_lessons():
    err = require_auth()
    if err: return err
    with get_db() as db:
        rows = db.execute('SELECT data FROM lessons WHERE user_id=? ORDER BY date', [uid()]).fetchall()
    return jsonify(rows_to_list(rows))

@app.route('/api/lessons', methods=['POST'])
def add_lesson():
    err = require_auth()
    if err: return err
    l = request.json
    with get_db() as db:
        db.execute('INSERT INTO lessons(id,user_id,studentId,date,data) VALUES (?,?,?,?,?)',
                   [l['id'], uid(), l.get('studentId',''), l.get('date',''),
                    json.dumps(l, ensure_ascii=False)])
    return jsonify(l), 201

@app.route('/api/lessons/<lid>', methods=['PUT'])
def update_lesson(lid):
    err = require_auth()
    if err: return err
    l = request.json
    with get_db() as db:
        db.execute('UPDATE lessons SET studentId=?, date=?, data=? WHERE id=? AND user_id=?',
                   [l.get('studentId',''), l.get('date',''),
                    json.dumps(l, ensure_ascii=False), lid, uid()])
    return jsonify(l)

@app.route('/api/lessons/<lid>', methods=['DELETE'])
def delete_lesson(lid):
    err = require_auth()
    if err: return err
    with get_db() as db:
        db.execute('DELETE FROM lessons WHERE id=? AND user_id=?', [lid, uid()])
    return '', 204

# ── Payments ──────────────────────────────────────────────────────────────────
@app.route('/api/payments', methods=['GET'])
def get_payments():
    err = require_auth()
    if err: return err
    with get_db() as db:
        rows = db.execute('SELECT data FROM payments WHERE user_id=? ORDER BY date', [uid()]).fetchall()
    return jsonify(rows_to_list(rows))

@app.route('/api/payments', methods=['POST'])
def add_payment():
    err = require_auth()
    if err: return err
    p = request.json
    with get_db() as db:
        db.execute('INSERT INTO payments(id,user_id,studentId,date,data) VALUES (?,?,?,?,?)',
                   [p['id'], uid(), p.get('studentId',''), p.get('date',''),
                    json.dumps(p, ensure_ascii=False)])
    return jsonify(p), 201

@app.route('/api/payments/<pid>', methods=['DELETE'])
def delete_payment(pid):
    err = require_auth()
    if err: return err
    with get_db() as db:
        db.execute('DELETE FROM payments WHERE id=? AND user_id=?', [pid, uid()])
    return '', 204

# ── ICS calendar ──────────────────────────────────────────────────────────────
@app.route('/api/calendar-token')
def get_calendar_token():
    # Used by the "subscribe" modal to build a URL that works for external
    # calendar apps, which can't send our session cookie (see calendar_ics()).
    err = require_auth()
    if err: return err
    with get_db() as db:
        row = db.execute('SELECT cal_token FROM users WHERE id=?', [uid()]).fetchone()
        token = row['cal_token'] if row else None
        if not token:
            token = secrets.token_urlsafe(24)
            db.execute('UPDATE users SET cal_token=? WHERE id=?', [token, uid()])
    return jsonify({'token': token})

@app.route('/calendar.ics')
def calendar_ics():
    # Two ways in: a logged-in browser session (manual download), or a
    # ?token=... query param (external calendar apps subscribing to the
    # feed URL — they never send our session cookie, so this is the only
    # way their periodic re-fetch can ever authenticate).
    cal_user_id = session.get('user_id')
    if not cal_user_id:
        token = request.args.get('token')
        if token:
            with get_db() as db:
                row = db.execute('SELECT id FROM users WHERE cal_token=?', [token]).fetchone()
                cal_user_id = row['id'] if row else None
    if not cal_user_id:
        return 'Unauthorized', 401
    with get_db() as db:
        students = {s['id']: s for s in rows_to_list(
            db.execute('SELECT data FROM students WHERE user_id=?', [cal_user_id]).fetchall())}
        lessons  = rows_to_list(
            db.execute('SELECT data FROM lessons WHERE user_id=? ORDER BY date', [cal_user_id]).fetchall())
    stamp = datetime.utcnow().strftime('%Y%m%dT%H%M%SZ')
    lines = ['BEGIN:VCALENDAR','VERSION:2.0','PRODID:-//שיעורים פרטיים//HE',
             'CALSCALE:GREGORIAN','METHOD:PUBLISH','X-WR-CALNAME:שיעורים - הוראה',
             'X-WR-TIMEZONE:Asia/Jerusalem','REFRESH-INTERVAL;VALUE=DURATION:PT1H']
    for l in lessons:
        s    = students.get(l['studentId'], {})
        name = s.get('name', 'תלמיד')
        date = l['date'].replace('-', '')
        hh, mm = map(int, (l.get('time','16:00')).split(':'))
        start_min = hh*60 + mm
        end_min   = start_min + int((l.get('durationHours',1) or 1)*60)
        sh,sm = str(start_min//60).zfill(2), str(start_min%60).zfill(2)
        eh,em = str((end_min//60)%24).zfill(2), str(end_min%60).zfill(2)
        lines += ['BEGIN:VEVENT', f'UID:{l["id"]}@tutor-app', f'DTSTAMP:{stamp}',
                  f'DTSTART;TZID=Asia/Jerusalem:{date}T{sh}{sm}00',
                  f'DTEND;TZID=Asia/Jerusalem:{date}T{eh}{em}00',
                  f'SUMMARY:שיעור - {name}',
                  f'DESCRIPTION:{l.get("notes","") or ""}', 'END:VEVENT']
    lines.append('END:VCALENDAR')
    return '\r\n'.join(lines), 200, {
        'Content-Type': 'text/calendar; charset=utf-8',
        # ASCII only — HTTP headers can't carry raw Hebrew text (RFC 7230),
        # and the Hebrew filename that used to be here crashed the response
        # mid-stream on every single request to this endpoint.
        'Content-Disposition': 'inline; filename="lessons.ics"',
    }

# ── Import (Excel) ────────────────────────────────────────────────────────────
@app.route('/api/import', methods=['POST'])
def import_data():
    err = require_auth()
    if err: return err
    body = request.json
    mode = body.get('mode', 'merge')
    with get_db() as db:
        if mode == 'replace':
            for tbl in ('students','lessons','payments'):
                db.execute(f'DELETE FROM {tbl} WHERE user_id=?', [uid()])
            for s in body.get('students', []):
                db.execute('INSERT INTO students(id,user_id,data) VALUES (?,?,?)',
                           [s['id'], uid(), json.dumps(s, ensure_ascii=False)])
            for l in body.get('lessons', []):
                db.execute('INSERT INTO lessons(id,user_id,studentId,date,data) VALUES (?,?,?,?,?)',
                           [l['id'], uid(), l.get('studentId',''), l.get('date',''),
                            json.dumps(l, ensure_ascii=False)])
            for p in body.get('payments', []):
                db.execute('INSERT INTO payments(id,user_id,studentId,date,data) VALUES (?,?,?,?,?)',
                           [p['id'], uid(), p.get('studentId',''), p.get('date',''),
                            json.dumps(p, ensure_ascii=False)])
        else:
            existing = {s['name']: s['id'] for s in rows_to_list(
                db.execute('SELECT data FROM students WHERE user_id=?', [uid()]).fetchall())}
            id_map = {}
            for ns in body.get('students', []):
                if ns['name'] in existing:
                    id_map[ns['id']] = existing[ns['name']]
                    db.execute('UPDATE students SET data=json_patch(data,?) WHERE id=? AND user_id=?',
                               [json.dumps({'hourlyRate': ns['hourlyRate']}), existing[ns['name']], uid()])
                else:
                    id_map[ns['id']] = ns['id']
                    db.execute('INSERT OR IGNORE INTO students(id,user_id,data) VALUES (?,?,?)',
                               [ns['id'], uid(), json.dumps(ns, ensure_ascii=False)])
            existing_keys = {(r['studentId'], r['date']) for r in
                             db.execute('SELECT studentId, date FROM lessons WHERE user_id=?', [uid()]).fetchall()}
            for l in body.get('lessons', []):
                l['studentId'] = id_map.get(l['studentId'], l['studentId'])
                if (l['studentId'], l['date']) not in existing_keys:
                    db.execute('INSERT OR IGNORE INTO lessons(id,user_id,studentId,date,data) VALUES (?,?,?,?,?)',
                               [l['id'], uid(), l['studentId'], l.get('date',''),
                                json.dumps(l, ensure_ascii=False)])
    return jsonify({'ok': True})
