#!/usr/bin/env python3
"""
send_approval_email.py — emails a user once the admin approves their signup.

Same reason as send_password_reset.py: PythonAnywhere's free tier can't send
outbound email, so this runs on a GitHub Actions runner instead. Triggered
by flask_app.py's admin_approve_user() via workflow_dispatch.

Required environment variables:
  APP_URL, EMAIL_ADDRESS, EMAIL_APP_PASSWORD
Plus, for this script specifically:
  APPROVED_EMAIL, APPROVED_NAME  (passed in from workflow_dispatch inputs)
"""
import os
import smtplib
from email.mime.text import MIMEText

APP_URL = os.environ['APP_URL'].rstrip('/')
EMAIL_ADDRESS = os.environ['EMAIL_ADDRESS']
EMAIL_APP_PASSWORD = os.environ['EMAIL_APP_PASSWORD']

APPROVED_EMAIL = os.environ['APPROVED_EMAIL']
APPROVED_NAME = os.environ.get('APPROVED_NAME', '')


def main():
    greeting = f'היי {APPROVED_NAME},' if APPROVED_NAME else 'היי,'
    body = f'''{greeting}

החשבון שלך באפליקציית ניהול השיעורים הפרטיים אושר! 🎉

אפשר להתחבר עכשיו:
{APP_URL}/

בהצלחה!
'''
    msg = MIMEText(body, 'plain', 'utf-8')
    msg['Subject'] = 'החשבון שלך אושר - ניהול שיעורים פרטיים'
    msg['From'] = EMAIL_ADDRESS
    msg['To'] = APPROVED_EMAIL

    with smtplib.SMTP_SSL('smtp.gmail.com', 465) as smtp:
        smtp.login(EMAIL_ADDRESS, EMAIL_APP_PASSWORD)
        smtp.send_message(msg)

    print(f'Approval email sent to {APPROVED_EMAIL}')


if __name__ == '__main__':
    main()
