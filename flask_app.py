#!/usr/bin/env python3
"""
flask_app.py — PythonAnywhere WSGI entry point
מסד נתונים: SQLite (tutor.db)
מיקום: /home/DekelPA/mysite/flask_app.py
"""
from flask import Flask, jsonify, request, session, send_from_directory
from werkzeug.security import generate_password_hash, check_password_hash
import sqlite3, json, os, sys, hashlib, secrets, uuid, hmac, subprocess, urllib.request, urllib.error, shutil, glob
from datetime import datetime, timedelta
try:
    from zoneinfo import ZoneInfo
    ISRAEL_TZ = ZoneInfo('Asia/Jerusalem')
except Exception:
    ISRAEL_TZ = None  # falls back to naive UTC date — a few hours off around midnight

# Overridable only so tests can run against a throwaway directory.
BASE_DIR = os.environ.get('TUTOR_APP_BASE_DIR', '/home/DekelPA/mysite')
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import portal_core
import verticals
import demo_accounts
import pricing
import landing_contact
import subscription
DB_PATH  = os.path.join(BASE_DIR, 'tutor.db')

app = Flask(__name__, static_folder=BASE_DIR)
app.permanent_session_lifetime = timedelta(days=30)

# ── Set to True only when you want to allow new registrations ─────────────────
REGISTRATION_OPEN = True

# ── Push notifications (Web Push / VAPID) ──────────────────────────────────────
# Public key only — safe to hardcode, it's meant to be public. The matching
# PRIVATE key never lives on this server; it's kept as a GitHub Actions secret
# and used only by the scheduled job that actually sends the push (see
# .github/workflows/daily-lesson-reminders.yml and scripts/send_reminders.py).
# This keeps PythonAnywhere's free-tier outbound-internet whitelist out of the
# picture entirely — this server only ever stores subscriptions and answers
# "who needs a reminder today", it never calls out to Apple's push service.
VAPID_PUBLIC_KEY = 'BItvzT-o_02tFQo_a61eRWe3lZ29jS6X6jKtWtwx3nFKJbzmslGFG9IKKiMCxuPfew88jBDeWefZMjv9XJIzczQ'

# ── Admin ────────────────────────────────────────────────────────────────────
# The one account allowed to see the admin screen (announcements + broadcast
# push). Not a real roles system — fine for a single-owner app like this one.
ADMIN_EMAIL = 'dekelkartel@gmail.com'

def require_admin():
    err = require_auth()
    if err: return err
    if (session.get('user_email') or '').lower() != ADMIN_EMAIL.lower():
        return jsonify({'error': 'Forbidden'}), 403
    return None

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
# the server (never committed — it holds real API tokens). Shape:
#   {"webhook_secret": "...", "pa_api_token": "...", "pa_username": "...",
#    "pa_domain": "...", "github_pat": "..."}
# If webhook_secret/pa_* are missing, /deploy-webhook just responds 'not
# configured' — the rest of the app still works. github_pat is separate: it's
# only needed for admin push-broadcast and the new-signup admin notification
# (trigger_github_workflow below), which need a GitHub PAT with the
# "repo" scope (classic) or "Actions: write" (fine-grained) to fire
# workflow_dispatch. Without it those two features 503 with a Hebrew
# "github_pat not configured" message — everything else is unaffected.
_deploy_cfg_file = os.path.join(BASE_DIR, '.deploy_config.json')
def load_deploy_config():
    if not os.path.exists(_deploy_cfg_file):
        return None
    try:
        with open(_deploy_cfg_file) as f:
            return json.load(f)
    except Exception:
        return None

def trigger_github_workflow(workflow_file, inputs):
    """Fires a workflow_dispatch for the given .github/workflows/<workflow_file>.
    Returns (ok: bool, error_response: Flask response | None) — error_response
    is set only when the caller should return it directly (missing PAT, GitHub
    error); when it's None, `ok` tells you whether the dispatch succeeded."""
    cfg = load_deploy_config()
    pat = cfg.get('github_pat') if cfg else None
    if not pat:
        return False, (jsonify({'error': f'לא הוגדר github_pat ב-.deploy_config.json', 'manual_fallback': True}), 503)
    gh_url = f'https://api.github.com/repos/dekelPK/tutor-app/actions/workflows/{workflow_file}/dispatches'
    payload = json.dumps({'ref': 'main', 'inputs': inputs}).encode()
    req = urllib.request.Request(gh_url, data=payload, method='POST', headers={
        'Authorization': f'Bearer {pat}',
        'Accept': 'application/vnd.github+json',
        'Content-Type': 'application/json',
        'User-Agent': 'tutor-app-admin',
    })
    try:
        with urllib.request.urlopen(req, timeout=20) as resp:
            return (200 <= resp.status < 300), None
    except urllib.error.HTTPError as e:
        return False, (jsonify({'error': f'GitHub החזיר {e.code}', 'detail': e.read().decode(errors='replace')}), 502)
    except Exception as e:
        return False, (jsonify({'error': str(e)}), 502)

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
            CREATE TABLE IF NOT EXISTS packages (
                id        TEXT PRIMARY KEY,
                user_id   TEXT,
                studentId TEXT NOT NULL,
                date      TEXT NOT NULL,
                data      TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS exams (
                id        TEXT PRIMARY KEY,
                user_id   TEXT,
                studentId TEXT NOT NULL,
                date      TEXT NOT NULL,
                data      TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS bookings (
                id        TEXT PRIMARY KEY,
                user_id   TEXT,
                studentId TEXT NOT NULL,
                date      TEXT NOT NULL,
                data      TEXT NOT NULL
            );
CREATE TABLE IF NOT EXISTS contact_messages (
                id         TEXT PRIMARY KEY,
                created_at TEXT,
                name       TEXT,
                contact    TEXT,
                message    TEXT,
                handled    INTEGER DEFAULT 0
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
        if 'approved' not in user_cols:
            # Default 1 so every pre-existing account stays usable; only new
            # signups from here on start at 0 and need the admin to approve them.
            db.execute('ALTER TABLE users ADD COLUMN approved INTEGER DEFAULT 1')
        if 'reset_token' not in user_cols:
            db.execute('ALTER TABLE users ADD COLUMN reset_token TEXT')
        if 'reset_token_expires' not in user_cols:
            db.execute('ALTER TABLE users ADD COLUMN reset_token_expires TEXT')
        if 'booking_settings' not in user_cols:
            db.execute('ALTER TABLE users ADD COLUMN booking_settings TEXT')
        # Multi-profession: every pre-existing account is a (female) tutor —
        # the defaults backfill them so nothing changes for current users.
        if 'profession' not in user_cols:
            db.execute(f"ALTER TABLE users ADD COLUMN profession TEXT DEFAULT '{verticals.DEFAULT_VERTICAL}'")
        if 'gender' not in user_cols:
            db.execute(f"ALTER TABLE users ADD COLUMN gender TEXT DEFAULT '{verticals.DEFAULT_GENDER}'")
        if 'custom_fields' not in user_cols:  # NULL → the vertical's default client fields
            db.execute('ALTER TABLE users ADD COLUMN custom_fields TEXT')
        if 'services' not in user_cols:  # NULL → the vertical's default services
            db.execute('ALTER TABLE users ADD COLUMN services TEXT')
        # Subscription chosen at signup (pricing.json). NULL plan = joined before
        # paid plans existed (the founding free users).
        if 'plan' not in user_cols:
            db.execute('ALTER TABLE users ADD COLUMN plan TEXT')
        if 'coupon' not in user_cols:
            db.execute('ALTER TABLE users ADD COLUMN coupon TEXT')
        # Free trial + manually recorded payments (subscription.py). NULL trial_ends
        # and paid_until = never billed (founding users) → free, never blocked.
        for col in ('phone', 'trial_ends', 'paid_until', 'billing', 'sub_request_at'):
            if col not in user_cols:
                db.execute(f'ALTER TABLE users ADD COLUMN {col} TEXT')
        if 'kind' not in [r[1] for r in db.execute('PRAGMA table_info(contact_messages)').fetchall()]:
            db.execute("ALTER TABLE contact_messages ADD COLUMN kind TEXT DEFAULT 'landing'")

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
    # Salted PBKDF2 (werkzeug default) — every new/rehashed password uses this.
    return generate_password_hash(pw)

def verify_pw(stored_hash, pw):
    if stored_hash and (stored_hash.startswith('pbkdf2:') or stored_hash.startswith('scrypt:')):
        return check_password_hash(stored_hash, pw)
    # Legacy unsalted SHA-256 from before the security upgrade — still verified
    # so existing accounts keep working; auth_login() rehashes them on next
    # successful login, so this branch disappears over time without a forced
    # password reset.
    return stored_hash == hashlib.sha256(pw.encode('utf-8')).hexdigest()

def require_auth():
    if 'user_id' not in session:
        return jsonify({'error': 'Unauthorized'}), 401
    # Trial over / subscription lapsed → the app is locked (402) until the admin
    # records a payment. /api/auth/me and /api/subscription/request stay open.
    with get_db() as db:
        u = db.execute('SELECT * FROM users WHERE id=?', [session['user_id']]).fetchone()
    if u and subscription.blocked(u, ADMIN_EMAIL):
        return jsonify({'error': 'subscription', 'subscription': subscription.status(u, ADMIN_EMAIL)}), 402
    return None

def uid():
    return session.get('user_id')

CODE_DIR = os.path.dirname(os.path.abspath(__file__))

# ── Landing page + pricing ───────────────────────────────────────────────────
# The marketing page (/welcome) reads the plans from /api/pricing (pricing.json);
# coupon codes are never listed — /api/coupon only confirms one that was typed.
@app.route('/welcome')
@app.route('/landing')
def landing_page():
    with open(os.path.join(CODE_DIR, 'landing.html'), encoding='utf-8') as f:
        return f.read(), 200, {'Content-Type': 'text/html; charset=utf-8'}

@app.route('/landing/<path:filename>')
def landing_assets(filename):
    return send_from_directory(os.path.join(CODE_DIR, 'landing'), filename)

@app.route('/api/pricing')
def public_pricing():
    return jsonify(pricing.public())

@app.route('/api/coupon', methods=['POST'])
def check_coupon():
    body = request.json or {}
    c = pricing.check_coupon(body.get('code'), pricing.normalize_plan(body.get('plan')))
    if not c:
        return jsonify({'valid': False, 'error': 'קוד הקופון לא תקין'}), 404
    return jsonify({'valid': True, 'plan': c['plan'], 'price': c['price']})

def signup_plan(body):
    """(plan, coupon) chosen at signup — (None, None) for none; raises ValueError for a bad coupon."""
    plan = pricing.normalize_plan(body.get('plan'))
    code = (body.get('coupon') or '').strip()
    if not code:
        return plan, None
    c = pricing.check_coupon(code, plan)
    if not c:
        raise ValueError('קוד הקופון לא תקין')
    return plan or c['plan'], c['code']

# ── Contact form on the landing page ─────────────────────────────────────────
# Stored in contact_messages; the admin gets a push via notify-admin-contact.yml,
# which is given only the message id (the visitor's details stay on the server).
_contact_hits = {}

@app.route('/api/contact', methods=['POST'])
def contact_submit():
    body = request.json or {}
    try:
        msg = landing_contact.clean(body)
    except ValueError as e:
        return jsonify({'error': str(e)}), 400
    if msg is None:  # honeypot filled → pretend success
        return jsonify({'ok': True})
    ip = (request.headers.get('X-Forwarded-For') or request.remote_addr or '').split(',')[0].strip()
    if not landing_contact.allow(_contact_hits, ip):
        return jsonify({'error': 'נשלחו יותר מדי פניות — נסו שוב מאוחר יותר'}), 429
    mid = uuid.uuid4().hex[:12]
    with get_db() as db:
        db.execute('INSERT INTO contact_messages (id, created_at, name, contact, message, handled) VALUES (?,?,?,?,?,0)',
                   [mid, datetime.now().isoformat(timespec='seconds'), msg['name'], msg['contact'], msg['message']])
    try:
        trigger_github_workflow('notify-admin-contact.yml', {'message_id': mid})
    except Exception:
        pass  # the message is saved either way; the admin screen lists it
    return jsonify({'ok': True})

@app.route('/api/push/contact-notice')
def push_contact_notice():
    # Used by notify-admin-contact.yml: the admin's devices + the push text.
    if request.args.get('token') != CRON_SECRET:
        return jsonify({'error': 'Unauthorized'}), 401
    with get_db() as db:
        m = db.execute('SELECT * FROM contact_messages WHERE id=?', [request.args.get('message_id')]).fetchone()
        admin_row = db.execute('SELECT id FROM users WHERE email=?', [ADMIN_EMAIL]).fetchone()
        subs = db.execute('SELECT endpoint, p256dh, auth FROM push_subscriptions WHERE user_id=?',
                          [admin_row['id']]).fetchall() if admin_row else []
    if not m:
        return jsonify({'subscriptions': []})
    return jsonify({'subscriptions': [dict(s) for s in subs], **landing_contact.push_text(dict(m))})

@app.route('/api/admin/contact-messages')
def admin_contact_messages():
    err = require_admin()
    if err: return err
    with get_db() as db:
        rows = db.execute('SELECT * FROM contact_messages ORDER BY created_at DESC LIMIT 100').fetchall()
    return jsonify([dict(r) for r in rows])

@app.route('/api/admin/contact-messages/<mid>', methods=['PUT'])
def admin_contact_handled(mid):
    err = require_admin()
    if err: return err
    with get_db() as db:
        db.execute('UPDATE contact_messages SET handled=? WHERE id=?', [1 if (request.json or {}).get('handled') else 0, mid])
    return jsonify({'ok': True})

# ── Serve HTML ────────────────────────────────────────────────────────────────
@app.route('/')
def index():
    with open(os.path.join(BASE_DIR, 'tutor-app.html'), 'r', encoding='utf-8') as f:
        return f.read(), 200, {'Content-Type': 'text/html; charset=utf-8'}

# ── Auth ──────────────────────────────────────────────────────────────────────
def account_info(user_id):
    """The business's profession + owner gender, and the resolved vertical config
    the browser needs for terminology and feature flags (see vertical.js)."""
    with get_db() as db:
        row = db.execute('SELECT * FROM users WHERE id=?', [user_id]).fetchone()
    prof = verticals.normalize(row['profession'] if row else None)
    gender = verticals.normalize_gender(row['gender'] if row else None)
    return {'profession': prof, 'gender': gender, 'vertical': verticals.load(prof), 'brand': verticals.brand(),
            'customFields': verticals.fields_for(prof, row['custom_fields'] if row else None),
            'services': verticals.services_for(prof, row['services'] if row else None),
            'subscription': {**subscription.status(row, ADMIN_EMAIL), **plan_info(row)} if row else None}

def plan_info(u):
    plan = u['plan']
    return {'plan': plan, 'planName': (pricing.plan(plan) or {}).get('name') if plan else None,
            'price': pricing.price_for(plan, u['coupon']) if plan else None}

def owner_terms(db, user_id):
    """verticals.Terms for a business owner — server-side text in their wording."""
    row = db.execute('SELECT profession, gender FROM users WHERE id=?', [user_id]).fetchone()
    return verticals.Terms(row['profession'] if row else None, row['gender'] if row else None)

@app.route('/api/auth/me')
def auth_me():
    if 'user_id' not in session:
        return jsonify({'error': 'Unauthorized'}), 401
    return jsonify({
        'id': session['user_id'], 'name': session.get('user_name',''), 'email': session.get('user_email',''),
        'isAdmin': (session.get('user_email') or '').lower() == ADMIN_EMAIL.lower(),
        **account_info(session['user_id']),
    })

@app.route('/api/verticals')
def list_verticals():
    # Public: the signup screen shows these before there's an account.
    return jsonify({'verticals': verticals.summary(), 'default': verticals.DEFAULT_VERTICAL, 'brand': verticals.brand()})

@app.route('/api/account', methods=['PUT'])
def update_account():
    # Account menu → "העסק שלי": gender and custom client fields. The
    # profession is fixed at signup for now, so it isn't accepted here.
    err = require_auth()
    if err: return err
    body = request.json or {}
    with get_db() as db:
        if 'gender' in body:
            db.execute('UPDATE users SET gender=? WHERE id=?', [verticals.normalize_gender(body['gender']), uid()])
        if 'customFields' in body:
            db.execute('UPDATE users SET custom_fields=? WHERE id=?',
                       [json.dumps(verticals.normalize_fields(body['customFields']), ensure_ascii=False), uid()])
        if 'services' in body:
            db.execute('UPDATE users SET services=? WHERE id=?',
                       [json.dumps(verticals.normalize_services(body['services']), ensure_ascii=False), uid()])
    return jsonify(account_info(uid()))

@app.route('/vertical.js')
def vertical_js():
    # Code lives next to this file (== BASE_DIR in production, not in tests)
    with open(os.path.join(os.path.dirname(os.path.abspath(__file__)), 'vertical.js'), 'r', encoding='utf-8') as f:
        return f.read(), 200, {'Content-Type': 'application/javascript; charset=utf-8'}

@app.route('/api/auth/login', methods=['POST'])
def auth_login():
    body  = request.json or {}
    email = (body.get('email') or '').strip().lower()
    pw    = body.get('password', '')
    if not email or not pw:
        return jsonify({'error': 'נא למלא אימייל וסיסמה'}), 400
    with get_db() as db:
        user = db.execute('SELECT * FROM users WHERE email=?', [email]).fetchone()
        if not user or not verify_pw(user['password_hash'], pw):
            return jsonify({'error': 'אימייל או סיסמה שגויים'}), 401
        if not (user['password_hash'].startswith('pbkdf2:') or user['password_hash'].startswith('scrypt:')):
            db.execute('UPDATE users SET password_hash=? WHERE id=?', [hash_pw(pw), user['id']])
    if not user['approved']:
        return jsonify({'error': 'החשבון שלך ממתין לאישור מנהל המערכת'}), 403
    session.permanent = True
    session['user_id']    = user['id']
    session['user_name']  = user['name']
    session['user_email'] = user['email']
    return jsonify({'id': user['id'], 'name': user['name'], 'email': user['email'],
                     'isAdmin': user['email'].lower() == ADMIN_EMAIL.lower(), **account_info(user['id'])})

@app.route('/api/auth/register', methods=['POST'])
def auth_register():
    if not REGISTRATION_OPEN:
        return jsonify({'error': 'ההרשמה סגורה. לפתיחת חשבון יש לפנות למנהל המערכת.'}), 403
    body  = request.json or {}
    email = (body.get('email') or '').strip().lower()
    pw    = body.get('password', '')
    name  = (body.get('name') or '').strip()
    profession = verticals.normalize(body.get('profession'))
    gender = verticals.normalize_gender(body.get('gender'))
    if not email or not pw:
        return jsonify({'error': 'נא למלא אימייל וסיסמה'}), 400
    if len(pw) < 6:
        return jsonify({'error': 'הסיסמה חייבת להכיל לפחות 6 תווים'}), 400
    is_admin_account = email.lower() == ADMIN_EMAIL.lower()
    try:
        plan, coupon = signup_plan(body)
        phone = subscription.normalize_phone(body.get('phone')) if not is_admin_account or body.get('phone') else None
    except ValueError as e:
        return jsonify({'error': str(e)}), 400
    with get_db() as db:
        if db.execute('SELECT 1 FROM users WHERE email=?', [email]).fetchone():
            return jsonify({'error': 'כתובת האימייל כבר רשומה במערכת'}), 409
        is_first = db.execute('SELECT COUNT(*) FROM users').fetchone()[0] == 0
        new_id   = uuid.uuid4().hex[:12]
        # Everyone starts right away on a free trial (subscription.TRIAL_DAYS);
        # the app locks itself when it ends unless the admin recorded a payment.
        trial_ends = None if (is_first or is_admin_account) else subscription.new_trial_end()
        db.execute('INSERT INTO users (id,email,password_hash,name,created_at,approved,profession,gender,plan,coupon,phone,trial_ends) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)',
                   [new_id, email, hash_pw(pw), name, datetime.now().isoformat(), 1, profession, gender, plan, coupon, phone, trial_ends])
        if is_first:
            # Associate all existing data (no user_id) with this first user
            for tbl in ('students', 'lessons', 'payments'):
                db.execute(f'UPDATE {tbl} SET user_id=? WHERE user_id IS NULL', [new_id])
    if trial_ends:
        # Best-effort: a push to the admin's own device and the welcome email —
        # a signup is never blocked by these failing (no PAT, GitHub hiccup, etc).
        for wf, inputs in (('notify-admin-signup.yml', {'name': name or '(ללא שם)', 'email': email}),
                           ('send-welcome-email.yml', {'email': email, 'name': name, 'trial_ends': trial_ends[:10]})):
            try:
                trigger_github_workflow(wf, inputs)
            except Exception:
                pass
    session.permanent = True
    session['user_id']    = new_id
    session['user_name']  = name
    session['user_email'] = email
    return jsonify({'id': new_id, 'name': name, 'email': email,
                     'isAdmin': is_admin_account, **account_info(new_id)}), 201

@app.route('/api/auth/logout', methods=['POST'])
def auth_logout():
    session.clear()
    return '', 204

@app.route('/api/auth/forgot-password', methods=['POST'])
def auth_forgot_password():
    email = ((request.json or {}).get('email') or '').strip().lower()
    # Always the same response whether or not the email exists — never reveal
    # which emails are registered.
    generic = jsonify({'message': 'אם כתובת האימייל קיימת במערכת, נשלח אליה קישור לאיפוס סיסמה.'})
    if not email:
        return generic
    with get_db() as db:
        user = db.execute('SELECT id, name FROM users WHERE email=?', [email]).fetchone()
        if not user:
            return generic
        token = secrets.token_urlsafe(32)
        expires = (datetime.now() + timedelta(hours=1)).isoformat()
        db.execute('UPDATE users SET reset_token=?, reset_token_expires=? WHERE id=?',
                   [token, expires, user['id']])
    try:
        trigger_github_workflow('send-password-reset.yml',
                                 {'email': email, 'name': user['name'] or '', 'token': token})
    except Exception:
        pass
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
    with get_db() as db:
        user = db.execute('SELECT id, reset_token_expires FROM users WHERE reset_token=?', [token]).fetchone()
        if not user or not user['reset_token_expires'] or datetime.fromisoformat(user['reset_token_expires']) < datetime.now():
            return jsonify({'error': 'הקישור פג תוקף או כבר נוצל. נא לבקש קישור חדש.'}), 400
        db.execute('UPDATE users SET password_hash=?, reset_token=NULL, reset_token_expires=NULL WHERE id=?',
                   [hash_pw(pw), user['id']])
    return jsonify({'ok': True})

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
            bt = owner_terms(db, sub['user_id'])
            if len(lesson_list) == 1:
                s = students.get(lesson_list[0]['studentId'], {})
                body = bt.t('reminder_one', name=s.get('name', bt.t('client')), time=lesson_list[0].get('time', ''))
            else:
                body = bt.t('reminder_many', count=len(lesson_list))
            out.append({
                'endpoint': sub['endpoint'],
                'p256dh': sub['p256dh'],
                'auth': sub['auth'],
                'title': bt.t('reminder_title'),
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

@app.route('/api/push/admin-subscriptions')
def push_admin_subscriptions():
    # Used by notify-admin-signup.yml to reach ONLY the admin's own device(s)
    # when a new registration comes in — token-protected the same way as the
    # other GitHub-Action-facing endpoints, never exposed to a regular session.
    if request.args.get('token') != CRON_SECRET:
        return jsonify({'error': 'Unauthorized'}), 401
    with get_db() as db:
        admin_row = db.execute('SELECT id FROM users WHERE email=?', [ADMIN_EMAIL]).fetchone()
        if not admin_row:
            return jsonify([])
        subs = db.execute('SELECT endpoint, p256dh, auth FROM push_subscriptions WHERE user_id=?',
                           [admin_row['id']]).fetchall()
    return jsonify([dict(s) for s in subs])

@app.route('/api/push/exams-due')
def push_exams_due():
    # Called by exam-reminders.yml, which runs at both 05:00 and 06:00 UTC so
    # one of them lands on 08:00 Israel time whether it's summer or winter
    # time. We answer only from 08:00 local, and hand out each exam once
    # (notifiedAt) — so the earlier run in winter and the later run in summer
    # are no-ops, and a late-running cron still sends.
    if request.args.get('token') != CRON_SECRET:
        return jsonify({'error': 'Unauthorized'}), 401
    now = portal_core.now_local()
    if now.hour < 8:
        return jsonify([])
    today = now.strftime('%Y-%m-%d')
    out = []
    with get_db() as db:
        rows = db.execute('SELECT id, user_id, data FROM exams WHERE date=?', [today]).fetchall()
        for r in rows:
            e = json.loads(r['data'])
            if e.get('notifiedAt'):
                continue
            bt = owner_terms(db, r['user_id'])
            if not bt.has('exams'):
                continue
            srow = db.execute('SELECT data FROM students WHERE id=? AND user_id=?', [e['studentId'], r['user_id']]).fetchone()
            name = json.loads(srow['data']).get('name', 'תלמיד/ה') if srow else 'תלמיד/ה'
            subj = f" ב{e['subject']}" if e.get('subject') else ''
            subs = db.execute('SELECT endpoint, p256dh, auth FROM push_subscriptions WHERE user_id=?', [r['user_id']]).fetchall()
            for sub in subs:
                out.append({**dict(sub), 'title': f'📝 היום ל{name} יש מבחן{subj}',
                            'body': f"{bt.g('לחצי', 'לחץ')} כדי לשלוח הודעת בהצלחה בווצאפ 🍀",
                            'url': f'/?exam={e["id"]}', 'tag': f'exam-{e["id"]}'})
            e['notifiedAt'] = now.isoformat(timespec='seconds')
            db.execute('UPDATE exams SET data=? WHERE id=?', [json.dumps(e, ensure_ascii=False), r['id']])
    return jsonify(out)

@app.route('/api/push/booking-notice')
def push_booking_notice():
    # Used by notify-booking-request.yml: given a booking id, returns the
    # owning teacher's push subscriptions plus the notification text, so the
    # student's name/time never pass through GitHub workflow inputs or logs.
    if request.args.get('token') != CRON_SECRET:
        return jsonify({'error': 'Unauthorized'}), 401
    bid = request.args.get('booking_id')
    with get_db() as db:
        row = db.execute('SELECT user_id, data FROM bookings WHERE id=?', [bid]).fetchone()
        if not row:
            return jsonify({'subscriptions': []})
        b = json.loads(row['data'])
        srow = db.execute('SELECT data FROM students WHERE id=? AND user_id=?', [b['studentId'], row['user_id']]).fetchone()
        name = json.loads(srow['data']).get('name', 'תלמיד/ה') if srow else 'תלמיד/ה'
        subs = db.execute('SELECT endpoint, p256dh, auth FROM push_subscriptions WHERE user_id=?',
                          [row['user_id']]).fetchall()
        bt = owner_terms(db, row['user_id'])
    try:
        y, m, d = b['date'].split('-')
        when = f'{d}/{m} בשעה {b["time"]}'
    except Exception:
        when = b.get('date', '')
    units = int(b.get('units') or 1)
    what = bt.t('appointment') if units == 1 else f"{units} {bt.t('appointments')} ברצף"
    return jsonify({'subscriptions': [dict(s) for s in subs],
                    'title': f"🗓️ בקשה חדשה ל{bt.t('appointment')}",
                    'body': f"{name} מבקש/ת {what} ב-{when}. {bt.g('לחצי', 'לחץ')} לאישור."})

# ── Daily DB backup ───────────────────────────────────────────────────────────
# PythonAnywhere's free tier doesn't include Scheduled Tasks (that needs a
# paid plan), so this is triggered the same way as everything else that needs
# "run this once a day": a GitHub Actions cron job (see backup-db.yml) hits
# this endpoint daily. Copies tutor.db into backups/ with a timestamp and
# prunes anything older than BACKUP_KEEP_DAYS — all on the server itself,
# so no personal data ever leaves it.
BACKUP_DIR = os.path.join(BASE_DIR, 'backups')
BACKUP_KEEP_DAYS = 14

@app.route('/api/admin/run-backup', methods=['POST'])
def run_backup():
    if request.args.get('token') != CRON_SECRET:
        return jsonify({'error': 'Unauthorized'}), 401
    if not os.path.exists(DB_PATH):
        return jsonify({'ok': False, 'error': 'no DB found'}), 404
    os.makedirs(BACKUP_DIR, exist_ok=True)
    stamp = datetime.now().strftime('%Y%m%d_%H%M%S')
    dest = os.path.join(BACKUP_DIR, f'tutor_{stamp}.db')
    shutil.copy2(DB_PATH, dest)

    cutoff = datetime.now() - timedelta(days=BACKUP_KEEP_DAYS)
    removed = 0
    for path in glob.glob(os.path.join(BACKUP_DIR, 'tutor_*.db')):
        if datetime.fromtimestamp(os.path.getmtime(path)) < cutoff:
            os.remove(path)
            removed += 1
    return jsonify({'ok': True, 'backup': os.path.basename(dest), 'removed_old': removed})

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

# ── Admin screen ──────────────────────────────────────────────────────────────
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
    body = request.json or {}
    title = (body.get('title') or '').strip() or '📢 עדכון חדש'  # same default the admin form previews
    text = (body.get('body') or '').strip()
    if not text:
        return jsonify({'error': 'נא לכתוב את טקסט ההתראה'}), 400

    ok, err_resp = trigger_github_workflow('broadcast-notification.yml', {'title': title, 'body': text})
    if err_resp: return err_resp
    return jsonify({'ok': ok})

@app.route('/api/admin/stats')
def admin_stats():
    err = require_admin()
    if err: return err
    month_prefix = datetime.now().strftime('%Y-%m')
    with get_db() as db:
        active_users    = db.execute('SELECT COUNT(*) FROM users WHERE approved=1').fetchone()[0]
        pending_users   = db.execute('SELECT COUNT(*) FROM users WHERE approved=0').fetchone()[0]
        all_students    = rows_to_list(db.execute('SELECT data FROM students').fetchall())
        active_students = sum(1 for s in all_students if s.get('isActive') is not False)
        lessons_month   = db.execute('SELECT COUNT(*) FROM lessons WHERE date LIKE ?', [f'{month_prefix}%']).fetchone()[0]
    return jsonify({
        'activeUsers': active_users,
        'pendingUsers': pending_users,
        'activeStudents': active_students,
        'totalStudents': len(all_students),
        'lessonsThisMonth': lessons_month,
    })

@app.route('/api/admin/pending-users')
def admin_pending_users():
    err = require_admin()
    if err: return err
    with get_db() as db:
        rows = db.execute('SELECT id, name, email, created_at, profession, plan, coupon, phone FROM users WHERE approved=0 ORDER BY created_at').fetchall()
    return jsonify([{**dict(r), 'monthlyPrice': pricing.price_for(r['plan'], r['coupon']) if r['plan'] else None} for r in rows])

@app.route('/api/admin/demo-account', methods=['POST'])
def admin_demo_account():
    # Admin screen → "חשבון ניסיון": creates (or resets) the test account for a
    # profession with sample data, and returns its login so the admin can see
    # the app the way that profession does. Same address each time → re-running
    # resets the password and the data.
    err = require_admin()
    if err: return err
    prof = verticals.normalize((request.json or {}).get('profession'))
    email, password = demo_accounts.email_for(prof), demo_accounts.new_password()
    data = demo_accounts.build(prof)
    label = verticals.load(prof)['label']['f']
    with get_db() as db:
        row = db.execute('SELECT id FROM users WHERE email=?', [email]).fetchone()
        if row:
            user_id = row['id']
            for tbl in ('students', 'lessons', 'payments', 'packages', 'exams', 'bookings', 'push_subscriptions'):
                db.execute(f'DELETE FROM {tbl} WHERE user_id=?', [user_id])
            db.execute('UPDATE users SET password_hash=?, approved=1, profession=?, gender=?, custom_fields=NULL, services=NULL WHERE id=?',
                       [hash_pw(password), prof, verticals.DEFAULT_GENDER, user_id])
        else:
            user_id = uuid.uuid4().hex[:12]
            db.execute('INSERT INTO users (id,email,password_hash,name,created_at,approved,profession,gender) VALUES (?,?,?,?,?,?,?,?)',
                       [user_id, email, hash_pw(password), f'ניסיון · {label}', datetime.now().isoformat(), 1, prof, verticals.DEFAULT_GENDER])
        db.execute('UPDATE users SET booking_settings=? WHERE id=?',
                   [json.dumps(data['bookingSettings'], ensure_ascii=False) if data['bookingSettings'] else None, user_id])
        for s in data['students']:
            db.execute('INSERT INTO students(id,user_id,data) VALUES (?,?,?)', [s['id'], user_id, json.dumps(s, ensure_ascii=False)])
        for tbl in ('lessons', 'payments', 'packages', 'exams', 'bookings'):
            for r in data[tbl]:
                db.execute(f'INSERT INTO {tbl}(id,user_id,studentId,date,data) VALUES (?,?,?,?,?)',
                           [r['id'], user_id, r['studentId'], r['date'], json.dumps(r, ensure_ascii=False)])
    return jsonify({'email': email, 'password': password, 'profession': prof})

@app.route('/api/admin/users')
def admin_users():
    err = require_admin()
    if err: return err
    with get_db() as db:
        rows = db.execute('SELECT * FROM users WHERE approved=1 ORDER BY created_at DESC').fetchall()
        out = []
        for u in rows:
            students = rows_to_list(db.execute('SELECT data FROM students WHERE user_id=?', [u['id']]).fetchall())
            lesson_count = db.execute('SELECT COUNT(*) FROM lessons WHERE user_id=?', [u['id']]).fetchone()[0]
            today = portal_core.now_local().strftime('%Y-%m-%d')
            month_count = db.execute('SELECT COUNT(*) FROM lessons WHERE user_id=? AND date LIKE ?', [u['id'], today[:7] + '%']).fetchone()[0]
            last = db.execute('SELECT MAX(date) FROM lessons WHERE user_id=? AND date<=?', [u['id'], today]).fetchone()[0]
            d = {k: u[k] for k in ('id', 'name', 'email', 'created_at', 'profession', 'gender', 'plan', 'coupon', 'phone')}
            d['subscription'] = subscription.status(u, ADMIN_EMAIL)
            d['billing'] = json.loads(u['billing'] or '[]')
            d['lessonsThisMonth'] = month_count
            d['lastLessonDate'] = last
            d['studentCount'] = len(students)
            d['activeStudentCount'] = sum(1 for s in students if s.get('isActive') is not False)
            d['lessonCount'] = lesson_count
            d['isAdmin'] = u['email'].lower() == ADMIN_EMAIL.lower()
            d['monthlyPrice'] = pricing.price_for(u['plan'], u['coupon']) if u['plan'] else None
            out.append(d)
    return jsonify(out)

@app.route('/api/admin/users/<target_id>/students')
def admin_user_students(target_id):
    # Diagnostic view — lists exactly which student rows are being counted for
    # a given user, including their raw student-table id (not shown anywhere
    # in the normal UI), so a mismatch between this count and what the tutor
    # actually sees on their own Students page can be tracked down directly.
    err = require_admin()
    if err: return err
    with get_db() as db:
        students = rows_to_list(db.execute('SELECT data FROM students WHERE user_id=?', [target_id]).fetchall())
    out = [{'id': s.get('id'), 'name': s.get('name'), 'isActive': s.get('isActive') is not False} for s in students]
    return jsonify(out)

@app.route('/api/admin/users/<target_id>/approve', methods=['POST'])
def admin_approve_user(target_id):
    err = require_admin()
    if err: return err
    with get_db() as db:
        user = db.execute('SELECT email, name, plan, trial_ends, paid_until FROM users WHERE id=?', [target_id]).fetchone()
        db.execute('UPDATE users SET approved=1 WHERE id=?', [target_id])
        if user and user['plan'] and not (user['trial_ends'] or user['paid_until']):
            # Signed up for a paid plan before the trial existed — the trial starts now.
            db.execute('UPDATE users SET trial_ends=? WHERE id=?', [subscription.new_trial_end(), target_id])
    if user:
        # Best-effort — approval always succeeds even if the notification fails.
        try:
            trigger_github_workflow('send-approval-email.yml', {'email': user['email'], 'name': user['name'] or ''})
        except Exception:
            pass
    return jsonify({'ok': True})

@app.route('/api/admin/users/<target_id>/reject', methods=['POST'])
def admin_reject_user(target_id):
    err = require_admin()
    if err: return err
    with get_db() as db:
        # Only ever deletes an unapproved signup — never touches an active account,
        # even if someone passes a stale/wrong id.
        db.execute('DELETE FROM users WHERE id=? AND approved=0', [target_id])
    return jsonify({'ok': True})

@app.route('/api/admin/users/<target_id>/suspend', methods=['POST'])
def admin_suspend_user(target_id):
    # Moves an already-approved user back to "pending" (approved=0) without
    # deleting their row or data — reversible via the same approve button
    # used for new signups. Never lets the admin account itself be suspended,
    # even by its own owner, so there's no way to accidentally lock everyone out.
    err = require_admin()
    if err: return err
    with get_db() as db:
        db.execute('UPDATE users SET approved=0 WHERE id=? AND email!=?', [target_id, ADMIN_EMAIL])
    return jsonify({'ok': True})

# ── Subscription (trial → Bit payment recorded by the admin) ─────────────────
BILLING_SETTINGS_PATH = os.path.join(BASE_DIR, 'billing_settings.json')

def billing_settings():
    if not os.path.exists(BILLING_SETTINGS_PATH):
        return {'payLink': '', 'bitPhone': ''}
    with open(BILLING_SETTINGS_PATH, encoding='utf-8') as f:
        return json.load(f)

@app.route('/api/subscription/request', methods=['POST'])
def subscription_request():
    # The locked screen's "I want to continue" — open even when the trial is
    # over (no require_auth). Lands in the admin's messages + a push.
    if 'user_id' not in session:
        return jsonify({'error': 'Unauthorized'}), 401
    with get_db() as db:
        u = db.execute('SELECT * FROM users WHERE id=?', [session['user_id']]).fetchone()
        if not u:
            return jsonify({'error': 'Unauthorized'}), 401
        info = plan_info(u)
        m = subscription.request_text(dict(u), info['planName'], info['price'], (request.json or {}).get('message'))
        mid = uuid.uuid4().hex[:12]
        now = datetime.now().isoformat(timespec='seconds')
        db.execute("INSERT INTO contact_messages (id, created_at, name, contact, message, handled, kind) VALUES (?,?,?,?,?,0,'subscription')",
                   [mid, now, m['name'], m['contact'], m['message']])
        db.execute('UPDATE users SET sub_request_at=? WHERE id=?', [now, u['id']])
    try:
        trigger_github_workflow('notify-admin-contact.yml', {'message_id': mid})
    except Exception:
        pass
    return jsonify({'ok': True, 'requestedAt': now})

@app.route('/api/admin/billing-settings', methods=['GET', 'PUT'])
def admin_billing_settings():
    err = require_admin()
    if err: return err
    if request.method == 'PUT':
        with open(BILLING_SETTINGS_PATH, 'w', encoding='utf-8') as f:
            json.dump(subscription.clean_settings(request.json or {}), f, ensure_ascii=False)
    return jsonify(billing_settings())

@app.route('/api/admin/users/<target_id>/payment-request')
def admin_payment_request(target_id):
    # The WhatsApp text asking this user to pay by Bit (opened by the admin screen).
    err = require_admin()
    if err: return err
    with get_db() as db:
        u = db.execute('SELECT * FROM users WHERE id=?', [target_id]).fetchone()
    if not u:
        return jsonify({'error': 'not found'}), 404
    info = plan_info(u)
    return jsonify({'phone': u['phone'], 'settings': billing_settings(),
                    'text': subscription.payment_request_message(u['name'], info['planName'], info['price'], billing_settings())})

@app.route('/api/admin/users/<target_id>/subscription', methods=['POST'])
def admin_set_subscription(target_id):
    # action: payment (record a Bit payment → extends paid_until) | free | trial (restart N days)
    err = require_admin()
    if err: return err
    body = request.json or {}
    with get_db() as db:
        u = db.execute('SELECT * FROM users WHERE id=?', [target_id]).fetchone()
        if not u:
            return jsonify({'error': 'not found'}), 404
        action = body.get('action')
        if action == 'payment':
            try:
                billing, until = subscription.record_payment(u, body)
            except ValueError as e:
                return jsonify({'error': str(e)}), 400
            db.execute('UPDATE users SET billing=?, paid_until=?, sub_request_at=NULL WHERE id=?', [billing, until, target_id])
        elif action == 'free':
            db.execute('UPDATE users SET trial_ends=NULL, paid_until=NULL WHERE id=?', [target_id])
        elif action == 'trial':
            days = max(1, min(60, int(body.get('days') or subscription.TRIAL_DAYS)))
            db.execute('UPDATE users SET trial_ends=?, paid_until=NULL WHERE id=?',
                       [(datetime.now() + timedelta(days=days)).isoformat(timespec='seconds'), target_id])
        else:
            return jsonify({'error': 'bad action'}), 400
        u = db.execute('SELECT * FROM users WHERE id=?', [target_id]).fetchone()
    return jsonify({'subscription': subscription.status(u, ADMIN_EMAIL), 'billing': json.loads(u['billing'] or '[]')})

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
        db.execute('DELETE FROM packages WHERE studentId=? AND user_id=?', [sid, uid()])
        db.execute('DELETE FROM bookings WHERE studentId=? AND user_id=?', [sid, uid()])
        db.execute('DELETE FROM exams    WHERE studentId=? AND user_id=?', [sid, uid()])
    return '', 204

@app.route('/api/students/<sid>/share-link', methods=['GET'])
def student_share_link(sid):
    # Lazily generates and persists a long random token the student/parent
    # portal uses to find this student with no login — same pattern as the
    # calendar-subscription token.
    err = require_auth()
    if err: return err
    with get_db() as db:
        row = db.execute('SELECT data FROM students WHERE id=? AND user_id=?', [sid, uid()]).fetchone()
        if not row:
            return jsonify({'error': 'Not found'}), 404
        s = json.loads(row['data'])
        if not s.get('shareToken'):
            s['shareToken'] = secrets.token_urlsafe(20)
            db.execute('UPDATE students SET data=? WHERE id=? AND user_id=?',
                       [json.dumps(s, ensure_ascii=False), sid, uid()])
    return jsonify({'token': s['shareToken']})

# ── Parent/student portal (public, no login — found by share token) ─────────────
# Page rendering, free-slot computation and punch-card math live in
# portal_core.py, shared with server.py so both backends behave the same.
def find_student_by_token(db, token):
    if not token:
        return None, None
    for r in db.execute('SELECT * FROM students').fetchall():
        data = json.loads(r['data'])
        if data.get('shareToken') == token:
            return r['user_id'], data
    return None, None

def load_booking_settings(db, user_id):
    row = db.execute('SELECT booking_settings FROM users WHERE id=?', [user_id]).fetchone()
    raw = None
    if row and row['booking_settings']:
        try: raw = json.loads(row['booking_settings'])
        except Exception: raw = None
    return portal_core.normalize_settings(raw)

def free_slots_for(db, owner_id, student, settings):
    students = rows_to_list(db.execute('SELECT data FROM students WHERE user_id=?', [owner_id]).fetchall())
    today = portal_core.now_local().strftime('%Y-%m-%d')
    lessons = rows_to_list(db.execute('SELECT data FROM lessons WHERE user_id=? AND date>=?', [owner_id, today]).fetchall())
    bookings = rows_to_list(db.execute('SELECT data FROM bookings WHERE user_id=? AND date>=?', [owner_id, today]).fetchall())
    return portal_core.compute_free_slots(settings, student, students, lessons, bookings)

@app.route('/portal/<token>')
def student_portal(token):
    with get_db() as db:
        owner_id, student = find_student_by_token(db, token)
        if not student:
            return 'הקישור לא נמצא או שפג תוקפו', 404
        sid_ = student['id']
        lessons = rows_to_list(db.execute(
            'SELECT data FROM lessons WHERE user_id=? AND studentId=? ORDER BY date', [owner_id, sid_]).fetchall())
        payments = rows_to_list(db.execute(
            'SELECT data FROM payments WHERE user_id=? AND studentId=?', [owner_id, sid_]).fetchall())
        packages = rows_to_list(db.execute(
            'SELECT data FROM packages WHERE user_id=? AND studentId=?', [owner_id, sid_]).fetchall())
        bookings = rows_to_list(db.execute(
            'SELECT data FROM bookings WHERE user_id=? AND studentId=?', [owner_id, sid_]).fetchall())
        exams = rows_to_list(db.execute(
            'SELECT data FROM exams WHERE user_id=? AND studentId=?', [owner_id, sid_]).fetchall())
        settings = load_booking_settings(db, owner_id)
        booking_enabled = settings['enabled'] and student.get('isActive') is not False
        slots = free_slots_for(db, owner_id, student, settings) if booking_enabled else []
        owner = db.execute('SELECT name FROM users WHERE id=?', [owner_id]).fetchone()
        bt = owner_terms(db, owner_id)
    page = portal_core.render_portal(token, student, lessons, payments, packages, bookings, slots, booking_enabled,
                                     teacher_name=(owner['name'] if owner else '') or '', exams=exams,
                                     vertical=bt.vertical, gender=bt.gender)
    return page, 200, {'Content-Type': 'text/html; charset=utf-8'}

@app.route('/portal/<token>/book', methods=['POST'])
def portal_book(token):
    body = request.json or {}
    with get_db() as db:
        owner_id, student = find_student_by_token(db, token)
        if not student:
            return jsonify({'error': 'הקישור לא נמצא'}), 404
        settings = load_booking_settings(db, owner_id)
        if not settings['enabled'] or student.get('isActive') is False:
            return jsonify({'error': 'קביעת שיעורים דרך הקישור כבויה כרגע'}), 403
        mine = rows_to_list(db.execute('SELECT data FROM bookings WHERE user_id=? AND studentId=?',
                                       [owner_id, student['id']]).fetchall())
        if sum(1 for b in mine if b.get('status') == 'pending') >= portal_core.MAX_PENDING_PER_STUDENT:
            return jsonify({'error': f'אפשר להחזיק עד {portal_core.MAX_PENDING_PER_STUDENT} בקשות ממתינות בו-זמנית'}), 429
        try:
            date, time_, units, note = portal_core.validate_booking_request(
                body, free_slots_for(db, owner_id, student, settings))
        except ValueError as e:
            return jsonify({'error': str(e)}), 409
        b = {'id': uuid.uuid4().hex[:16], 'studentId': student['id'], 'date': date, 'time': time_,
             'units': units, 'durationMinutes': units * int(student.get('lessonDurationMinutes') or 50), 'note': note,
             'status': 'pending', 'createdAt': portal_core.now_local().isoformat(timespec='seconds')}
        db.execute('INSERT INTO bookings(id,user_id,studentId,date,data) VALUES (?,?,?,?,?)',
                   [b['id'], owner_id, student['id'], date, json.dumps(b, ensure_ascii=False)])
    # Best effort push to the teacher's own device(s) — only the booking id
    # goes to GitHub; the script fetches the text from us with CRON_SECRET.
    try:
        trigger_github_workflow('notify-booking-request.yml', {'booking_id': b['id']})
    except Exception:
        pass
    return jsonify({'ok': True}), 201

@app.route('/portal/<token>/cancel/<bid>', methods=['POST'])
def portal_cancel_booking(token, bid):
    with get_db() as db:
        owner_id, student = find_student_by_token(db, token)
        if not student:
            return jsonify({'error': 'הקישור לא נמצא'}), 404
        row = db.execute('SELECT data FROM bookings WHERE id=? AND user_id=? AND studentId=?',
                         [bid, owner_id, student['id']]).fetchone()
        if not row:
            return jsonify({'error': 'Not found'}), 404
        b = json.loads(row['data'])
        if b.get('status') != 'pending':
            return jsonify({'error': 'הבקשה כבר טופלה'}), 409
        b['status'] = 'cancelled'
        b['decidedAt'] = portal_core.now_local().isoformat(timespec='seconds')
        db.execute('UPDATE bookings SET data=? WHERE id=?', [json.dumps(b, ensure_ascii=False), bid])
    return jsonify({'ok': True})

# ── Booking settings & requests (teacher side) ───────────────────────────────────
@app.route('/api/booking-settings', methods=['GET'])
def get_booking_settings():
    err = require_auth()
    if err: return err
    with get_db() as db:
        return jsonify(load_booking_settings(db, uid()))

@app.route('/api/booking-settings', methods=['PUT'])
def put_booking_settings():
    err = require_auth()
    if err: return err
    s = portal_core.normalize_settings(request.json or {})
    with get_db() as db:
        db.execute('UPDATE users SET booking_settings=? WHERE id=?', [json.dumps(s, ensure_ascii=False), uid()])
    return jsonify(s)

@app.route('/api/bookings', methods=['GET'])
def get_bookings():
    err = require_auth()
    if err: return err
    with get_db() as db:
        rows = db.execute('SELECT data FROM bookings WHERE user_id=? ORDER BY date', [uid()]).fetchall()
    return jsonify(rows_to_list(rows))

@app.route('/api/bookings/<bid>/approve', methods=['POST'])
def approve_booking(bid):
    # The client builds the lesson (same code path as a manual "add lesson",
    # including punch-card assignment); we insert it and flip the request in
    # one transaction so a request can never yield two lessons.
    err = require_auth()
    if err: return err
    lesson = (request.json or {}).get('lesson') or {}
    if not lesson.get('id'):
        return jsonify({'error': 'missing lesson'}), 400
    with get_db() as db:
        row = db.execute('SELECT data FROM bookings WHERE id=? AND user_id=?', [bid, uid()]).fetchone()
        if not row:
            return jsonify({'error': 'Not found'}), 404
        b = json.loads(row['data'])
        if b.get('status') != 'pending':
            return jsonify({'error': 'הבקשה כבר טופלה או בוטלה'}), 409
        db.execute('INSERT INTO lessons(id,user_id,studentId,date,data) VALUES (?,?,?,?,?)',
                   [lesson['id'], uid(), lesson.get('studentId', ''), lesson.get('date', ''),
                    json.dumps(lesson, ensure_ascii=False)])
        b.update(status='approved', lessonId=lesson['id'],
                 decidedAt=portal_core.now_local().isoformat(timespec='seconds'))
        db.execute('UPDATE bookings SET data=? WHERE id=?', [json.dumps(b, ensure_ascii=False), bid])
    return jsonify({'booking': b, 'lesson': lesson})

@app.route('/api/bookings/<bid>/reject', methods=['POST'])
def reject_booking(bid):
    err = require_auth()
    if err: return err
    reason = str((request.json or {}).get('reason') or '').strip()[:300]
    with get_db() as db:
        row = db.execute('SELECT data FROM bookings WHERE id=? AND user_id=?', [bid, uid()]).fetchone()
        if not row:
            return jsonify({'error': 'Not found'}), 404
        b = json.loads(row['data'])
        if b.get('status') != 'pending':
            return jsonify({'error': 'הבקשה כבר טופלה או בוטלה'}), 409
        b.update(status='rejected', rejectReason=reason,
                 decidedAt=portal_core.now_local().isoformat(timespec='seconds'))
        db.execute('UPDATE bookings SET data=? WHERE id=?', [json.dumps(b, ensure_ascii=False), bid])
    return jsonify(b)

# ── Punch cards (כרטיסיות) ───────────────────────────────────────────────────────
@app.route('/api/packages', methods=['GET'])
def get_packages():
    err = require_auth()
    if err: return err
    with get_db() as db:
        rows = db.execute('SELECT data FROM packages WHERE user_id=? ORDER BY date', [uid()]).fetchall()
    return jsonify(rows_to_list(rows))

@app.route('/api/packages', methods=['POST'])
def add_package():
    err = require_auth()
    if err: return err
    p = request.json
    with get_db() as db:
        db.execute('INSERT INTO packages(id,user_id,studentId,date,data) VALUES (?,?,?,?,?)',
                   [p['id'], uid(), p.get('studentId', ''), p.get('date', ''), json.dumps(p, ensure_ascii=False)])
    return jsonify(p), 201

@app.route('/api/packages/<pid>', methods=['PUT'])
def update_package(pid):
    err = require_auth()
    if err: return err
    p = request.json
    with get_db() as db:
        db.execute('UPDATE packages SET studentId=?, date=?, data=? WHERE id=? AND user_id=?',
                   [p.get('studentId', ''), p.get('date', ''), json.dumps(p, ensure_ascii=False), pid, uid()])
    return jsonify(p)

@app.route('/api/packages/<pid>', methods=['DELETE'])
def delete_package(pid):
    err = require_auth()
    if err: return err
    with get_db() as db:
        db.execute('DELETE FROM packages WHERE id=? AND user_id=?', [pid, uid()])
    return '', 204

# ── Exams (מבחנים) ───────────────────────────────────────────────────────────────
# {id, studentId, date, subject, notes, createdAt, notifiedAt, wishedAt}
# notifiedAt: set when the 8:00 push for it was handed out (see push_exams_due).
# wishedAt: set when the teacher opened the "good luck" WhatsApp for it.
@app.route('/api/exams', methods=['GET'])
def get_exams():
    err = require_auth()
    if err: return err
    with get_db() as db:
        rows = db.execute('SELECT data FROM exams WHERE user_id=? ORDER BY date', [uid()]).fetchall()
    return jsonify(rows_to_list(rows))

@app.route('/api/exams', methods=['POST'])
def add_exam():
    err = require_auth()
    if err: return err
    e = request.json
    with get_db() as db:
        db.execute('INSERT INTO exams(id,user_id,studentId,date,data) VALUES (?,?,?,?,?)',
                   [e['id'], uid(), e.get('studentId', ''), e.get('date', ''), json.dumps(e, ensure_ascii=False)])
    return jsonify(e), 201

@app.route('/api/exams/<eid>', methods=['PUT'])
def update_exam(eid):
    err = require_auth()
    if err: return err
    e = request.json
    with get_db() as db:
        db.execute('UPDATE exams SET studentId=?, date=?, data=? WHERE id=? AND user_id=?',
                   [e.get('studentId', ''), e.get('date', ''), json.dumps(e, ensure_ascii=False), eid, uid()])
    return jsonify(e)

@app.route('/api/exams/<eid>', methods=['DELETE'])
def delete_exam(eid):
    err = require_auth()
    if err: return err
    with get_db() as db:
        db.execute('DELETE FROM exams WHERE id=? AND user_id=?', [eid, uid()])
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
        bt = owner_terms(db, cal_user_id)
    stamp = datetime.utcnow().strftime('%Y%m%dT%H%M%SZ')
    lines = ['BEGIN:VCALENDAR','VERSION:2.0',f"PRODID:-//{verticals.brand()['name']}//HE",
             'CALSCALE:GREGORIAN','METHOD:PUBLISH',f"X-WR-CALNAME:{bt.t('calendar_name')}",
             'X-WR-TIMEZONE:Asia/Jerusalem','REFRESH-INTERVAL;VALUE=DURATION:PT1H']
    for l in lessons:
        s    = students.get(l['studentId'], {})
        name = s.get('name', bt.t('client'))
        date = l['date'].replace('-', '')
        hh, mm = map(int, (l.get('time','16:00')).split(':'))
        start_min = hh*60 + mm
        end_min   = start_min + int((l.get('durationHours',1) or 1)*portal_core.unit_minutes(l, s))
        sh,sm = str(start_min//60).zfill(2), str(start_min%60).zfill(2)
        eh,em = str((end_min//60)%24).zfill(2), str(end_min%60).zfill(2)
        lines += ['BEGIN:VEVENT', f'UID:{l["id"]}@tutor-app', f'DTSTAMP:{stamp}',
                  f'DTSTART;TZID=Asia/Jerusalem:{date}T{sh}{sm}00',
                  f'DTEND;TZID=Asia/Jerusalem:{date}T{eh}{em}00',
                  f"SUMMARY:{l['serviceName'] + ' - ' + name if l.get('serviceName') else bt.t('calendar_event', name=name)}",
                  f'DESCRIPTION:{l.get("notes","") or ""}', 'END:VEVENT']
    lines.append('END:VCALENDAR')
    return '\r\n'.join(lines), 200, {
        'Content-Type': 'text/calendar; charset=utf-8',
        # ASCII only — HTTP headers can't carry raw Hebrew text (RFC 7230),
        # and the Hebrew filename that used to be here crashed the response
        # mid-stream on every single request to this endpoint.
        'Content-Disposition': 'inline; filename="lessons.ics"',
    }

