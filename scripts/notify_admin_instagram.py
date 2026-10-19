#!/usr/bin/env python3
"""
notify_admin_instagram.py — the Sunday-morning push that reminds the admin to make
the week's Instagram content. Runs from instagram-weekly-reminder.yml (twice, to
cover DST); /api/push/instagram-reminder only answers with devices when a reminder
is actually due, so exactly one run sends.

Required environment variables: APP_URL, CRON_SECRET, VAPID_PRIVATE_KEY
"""
import os
import sys
import json
import requests
from pywebpush import webpush, WebPushException

APP_URL = os.environ['APP_URL'].rstrip('/')
CRON_SECRET = os.environ['CRON_SECRET']
VAPID_PRIVATE_KEY = os.environ['VAPID_PRIVATE_KEY']
VAPID_CLAIMS = {'sub': os.environ.get('VAPID_CONTACT_EMAIL', 'mailto:admin@example.com')}


def main():
    resp = requests.get(f'{APP_URL}/api/push/instagram-reminder', params={'token': CRON_SECRET}, timeout=30)
    resp.raise_for_status()
    notice = resp.json()
    subs = notice.get('subscriptions', [])
    if not subs:
        print('no reminder due (or no admin device) — nothing to send')
        return
    payload = json.dumps({k: notice[k] for k in ('title', 'body', 'url', 'tag')})
    sent, failed = 0, 0
    for sub in subs:
        try:
            webpush(
                subscription_info={'endpoint': sub['endpoint'], 'keys': {'p256dh': sub['p256dh'], 'auth': sub['auth']}},
                data=payload,
                vapid_private_key=VAPID_PRIVATE_KEY,
                vapid_claims=dict(VAPID_CLAIMS),
                ttl=24 * 3600,
                headers={'Urgency': 'high'},
            )
            sent += 1
        except WebPushException as e:
            failed += 1
            status = e.response.status_code if e.response is not None else None
            print(f'  failed ({status}): {e}')
            if status in (404, 410):
                requests.delete(f'{APP_URL}/api/push/due-today', params={'token': CRON_SECRET},
                                json={'endpoint': sub['endpoint']}, timeout=15)
    print(f'done: {sent} sent, {failed} failed')
    if failed and not sent:
        sys.exit(1)


if __name__ == '__main__':
    main()
