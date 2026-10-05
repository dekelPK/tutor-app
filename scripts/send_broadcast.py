#!/usr/bin/env python3
"""
send_broadcast.py — sends a one-off push notification to every user who has
lesson reminders enabled. Triggered manually via the
"Broadcast notification" GitHub Actions workflow (Actions tab → Run workflow),
never on a schedule.

Required environment variables (same secrets as send_reminders.py):
  APP_URL, CRON_SECRET, VAPID_PRIVATE_KEY, VAPID_PUBLIC_KEY
Plus, for this script specifically:
  BROADCAST_TITLE, BROADCAST_BODY  (passed in from the workflow_dispatch inputs)
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

TITLE = os.environ['BROADCAST_TITLE']
BODY = os.environ['BROADCAST_BODY']


def main():
    resp = requests.get(f'{APP_URL}/api/push/all-subscriptions', params={'token': CRON_SECRET}, timeout=30)
    resp.raise_for_status()
    subs = resp.json()
    print(f'{len(subs)} subscriber(s) found')

    payload = json.dumps({'title': TITLE, 'body': BODY, 'url': '/'})
    sent, failed = 0, 0
    for sub in subs:
        subscription_info = {
            'endpoint': sub['endpoint'],
            'keys': {'p256dh': sub['p256dh'], 'auth': sub['auth']},
        }
        try:
            webpush(
                subscription_info=subscription_info,
                data=payload,
                vapid_private_key=VAPID_PRIVATE_KEY,
                vapid_claims=dict(VAPID_CLAIMS),
            )
            sent += 1
        except WebPushException as e:
            failed += 1
            status = e.response.status_code if e.response is not None else None
            print(f'  failed ({status}): {e}')
            if status in (404, 410):
                requests.delete(
                    f'{APP_URL}/api/push/due-today',
                    params={'token': CRON_SECRET},
                    json={'endpoint': sub['endpoint']},
                    timeout=15,
                )

    print(f'done: {sent} sent, {failed} failed')
    if failed and not sent:
        sys.exit(1)


if __name__ == '__main__':
    main()
