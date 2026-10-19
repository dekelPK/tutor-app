"""
landing_contact.py — the landing page's contact form: input checks, a simple
per-IP rate limit, and the admin push text. Shared by both backends.
"""
import time

MAX_NAME, MAX_CONTACT, MAX_MESSAGE = 60, 80, 1000
RATE_LIMIT, RATE_WINDOW = 5, 3600  # messages per IP per hour


def clean(body):
    """The sanitized message, None for a bot (honeypot filled), or ValueError with a Hebrew reason."""
    if (body.get('website') or '').strip():
        return None
    name = str(body.get('name') or '').strip()[:MAX_NAME]
    contact = str(body.get('contact') or '').strip()[:MAX_CONTACT]
    message = str(body.get('message') or '').strip()[:MAX_MESSAGE]
    if not name:
        raise ValueError('נא למלא שם')
    if len(contact) < 6:
        raise ValueError('נא להשאיר טלפון או אימייל כדי שנוכל לחזור אלייך')
    return {'name': name, 'contact': contact, 'message': message}


def allow(hits, ip, now=None):
    """True if this IP may send another message (and records it)."""
    now = now or time.time()
    recent = [t for t in hits.get(ip, []) if now - t < RATE_WINDOW]
    if len(recent) >= RATE_LIMIT:
        hits[ip] = recent
        return False
    hits[ip] = recent + [now]
    return True


def push_text(m):
    preview = (m.get('message') or '').replace('\n', ' ')
    if len(preview) > 90:
        preview = preview[:90] + '…'
    return {'title': '📬 פנייה חדשה מדף הנחיתה',
            'body': f"{m['name']} · {m['contact']}" + (f'\n{preview}' if preview else '')}
