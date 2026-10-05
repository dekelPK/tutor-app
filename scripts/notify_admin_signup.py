#!/usr/bin/env python3
"""
notify_admin_signup.py — pushes a notification to the ADMIN'S OWN device(s)
only, the moment a new registration request comes in. Triggered automatically
by flask_app.py's auth_register() via workflow_dispatch (never by a human,
never on a schedule) — see notify-admin-signup.yml.

Required environment variables (same secrets as the other push scripts):
  APP_URL, CRON_SECRET, VAPID_PRIVATE_KEY
Plus, for this script specifically:
  SIGNUP_NAME, SIGNUP_EMAIL  (passed in from the workflow_dispatch inputs)
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

SIGNUP_NAME = os.environ['SIGNUP_NAME']
SIGNUP_EMAIL = os.environ['SIGNUP_EMAIL']


def main():
    resp = requests.get(f'{APP_URL}/api/push/admin-subscriptions', params={'token': CRON_SECRET}, timeout=30)
    resp.raise_for_status()
    subs = resp.json()
    print(f'{len(subs)} admin device(s) found')
    if not subs:
        print('admin has no push subscription — nothing to send')
        return

    # Explicitly labeled as an admin-only notification so it reads differently
    # from the regular "you have a lesson today" pushes on the same device.
    payload = json.dumps({
        'title': '🔔 התראת מנהל — בקשת הרשמה חדשה',
        'body': f'{SIGNUP_NAME} ({SIGNUP_EMAIL}) ביקש/ה להירשם. לחצי לאישור בעמוד הניהול.',
        'url': '/',
    })

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
