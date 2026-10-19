"""
subscription.py — free trial + manually-recorded payments. Shared by both backends.

New signups get TRIAL_DAYS of full access. After that the app is blocked until
the admin records a payment (paid by Bit, outside the app), which extends
paid_until by the paid period. Accounts with neither a trial nor a payment on
record — the founding users, the admin, demo accounts — are free and never blocked.
"""
import json
import re
from datetime import datetime, timedelta, date

TRIAL_DAYS = 7
PERIODS = {'month': 1, 'year': 12}   # months per period; 'custom' passes months directly
DEMO_DOMAIN = '@luzi.test'


def new_trial_end(now=None):
    return ((now or datetime.now()) + timedelta(days=TRIAL_DAYS)).isoformat(timespec='seconds')


def normalize_phone(raw):
    """Israeli mobile/landline as 05X-XXXXXXX-ish digits, or ValueError with a Hebrew reason."""
    digits = re.sub(r'\D', '', str(raw or ''))
    if digits.startswith('972'):
        digits = '0' + digits[3:]
    if not re.fullmatch(r'0\d{8,9}', digits):
        raise ValueError('נא להזין מספר טלפון תקין')
    return digits


def _row(u, key):
    try:
        return u[key]
    except (KeyError, IndexError):
        return None


def status(u, admin_email='', now=None):
    """The account's subscription state for the UI and the access check.
    state: free | trial | active | expired. `u` is a users row (or dict)."""
    now = now or datetime.now()
    email = (_row(u, 'email') or '').lower()
    trial_ends, paid_until = _row(u, 'trial_ends'), _row(u, 'paid_until')
    out = {'trialEnds': trial_ends, 'paidUntil': paid_until, 'requestedAt': _row(u, 'sub_request_at')}
    if email == (admin_email or '').lower() or email.endswith(DEMO_DOMAIN) or not (trial_ends or paid_until):
        return {**out, 'state': 'free'}
    if paid_until and paid_until >= now.date().isoformat():
        days = (date.fromisoformat(paid_until) - now.date()).days
        return {**out, 'state': 'active', 'daysLeft': days}
    if trial_ends and not paid_until and trial_ends > now.isoformat(timespec='seconds'):
        left = datetime.fromisoformat(trial_ends) - now
        return {**out, 'state': 'trial', 'daysLeft': max(0, left.days + (1 if left.seconds else 0))}
    return {**out, 'state': 'expired', 'reason': 'subscription' if paid_until else 'trial'}


def blocked(u, admin_email='', now=None):
    return status(u, admin_email, now)['state'] == 'expired'


def add_months(d, months):
    m = d.month - 1 + months
    y, m = d.year + m // 12, m % 12 + 1
    last = [31, 29 if y % 4 == 0 and (y % 100 or y % 400 == 0) else 28, 31, 30, 31, 30, 31, 31, 30, 31, 30, 31][m - 1]
    return date(y, m, min(d.day, last))


def record_payment(u, body, now=None):
    """Admin recorded a payment → (billing_json, paid_until). Raises ValueError (Hebrew).
    body: amount, period (month|year|custom), months (custom), note, paidAt (YYYY-MM-DD)."""
    now = now or datetime.now()
    try:
        amount = round(float(body.get('amount')), 2)
    except (TypeError, ValueError):
        raise ValueError('נא להזין סכום')
    if amount < 0:
        raise ValueError('נא להזין סכום')
    period = body.get('period') if body.get('period') in ('month', 'year', 'custom') else 'month'
    try:
        months = PERIODS[period] if period != 'custom' else int(body.get('months'))
    except (TypeError, ValueError):
        raise ValueError('נא לבחור לכמה חודשים')
    if not 1 <= months <= 36:
        raise ValueError('נא לבחור לכמה חודשים')
    try:
        paid_at = date.fromisoformat(body.get('paidAt') or now.date().isoformat())
    except ValueError:
        paid_at = now.date()
    # Renewing early stacks on the current period; after a gap it starts from the payment day.
    current = _row(u, 'paid_until')
    start = date.fromisoformat(current) + timedelta(days=1) if current and current >= paid_at.isoformat() else paid_at
    until = add_months(start, months) - timedelta(days=1)
    billing = json.loads(_row(u, 'billing') or '[]')
    billing.append({'amount': amount, 'period': period, 'months': months, 'paidAt': paid_at.isoformat(),
                    'from': start.isoformat(), 'until': until.isoformat(),
                    'note': str(body.get('note') or '').strip()[:200], 'recordedAt': now.isoformat(timespec='seconds')})
    return json.dumps(billing, ensure_ascii=False), until.isoformat()


def request_text(user, plan_name, price, note):
    """The contact_messages row for an in-app "I want to continue" request."""
    contact = ' · '.join(x for x in (user.get('phone'), user.get('email')) if x)
    msg = f'💳 רוצה להמשיך — {plan_name or "מנוי"}' + (f' · ₪{price} לחודש' if price is not None else '')
    if note:
        msg += '\n' + str(note).strip()[:500]
    return {'name': user.get('name') or user.get('email'), 'contact': contact, 'message': msg}


def payment_request_message(name, plan_name, price, settings):
    """WhatsApp text the admin sends to ask for payment by Bit."""
    first = (name or '').split(' ')[0]
    lines = [f'היי {first}! 💙' if first else 'היי! 💙', 'תודה שבחרת להמשיך עם לוזי.', '']
    if plan_name:
        lines.append(f'מסלול {plan_name}' + (f' · ₪{price} לחודש' if price is not None else ''))
    link, phone = (settings or {}).get('payLink'), (settings or {}).get('bitPhone')
    if link:
        lines.append(f'לתשלום בביט: {link}')
    elif phone:
        lines.append(f'אפשר להעביר בביט למספר {phone}')
    lines += ['', 'אחרי שהתשלום יתקבל אפתח לך את המנוי 🙏']
    return '\n'.join(lines)


def clean_settings(body):
    link = str(body.get('payLink') or '').strip()[:300]
    if link and not re.match(r'^https?://', link):
        link = 'https://' + link
    phone = re.sub(r'[^\d-]', '', str(body.get('bitPhone') or ''))[:15]
    return {'payLink': link, 'bitPhone': phone}
