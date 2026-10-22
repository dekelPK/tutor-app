"""
invoicing.py — the business's "חשבוניות וקבלות" setting (העסק שלי), shared by both backends.

The app doesn't issue tax documents itself: it tracks which received payments still need a
receipt/invoice, lets the owner mark them as issued, and links to her own invoicing system
(חשבונית ירוקה, iCount, …). Off by default — businesses that don't issue documents never see it.
Per-payment state (receiptAmount / receiptAt / receiptNo) lives on the lesson/payment records.
"""
import json
import re

DOC_TYPES = ['קבלה', 'חשבונית מס קבלה', 'חשבונית מס']
DEFAULT = {'enabled': False, 'docType': DOC_TYPES[0], 'siteName': '', 'siteUrl': ''}


def normalize(raw):
    """The setting from untrusted input (or a stored JSON string): known keys only, safe values."""
    if isinstance(raw, str):
        try:
            raw = json.loads(raw)
        except ValueError:
            raw = None
    raw = raw if isinstance(raw, dict) else {}
    url = str(raw.get('siteUrl') or '').strip()[:300]
    scheme = re.match(r'^([a-z][a-z0-9+.-]*):(?!\d)', url, re.I)   # "host:8080" is a port, "javascript:x" a scheme
    if scheme and scheme.group(1).lower() not in ('http', 'https'):
        url = ''
    if url and not re.match(r'^https?://', url, re.I):
        url = 'https://' + url
    if url and not re.match(r'^https?://[^\s/$.?#][^\s]*$', url, re.I):
        url = ''
    return {
        'enabled': bool(raw.get('enabled')),
        'docType': raw.get('docType') if raw.get('docType') in DOC_TYPES else DEFAULT['docType'],
        'siteName': re.sub(r'\s+', ' ', str(raw.get('siteName') or '')).strip()[:40],
        'siteUrl': url,
    }
