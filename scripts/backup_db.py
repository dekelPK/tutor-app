#!/usr/bin/env python3
"""
backup_db.py — copies tutor.db into backups/ with a timestamp, and deletes
backups older than BACKUP_KEEP_DAYS.

Meant to run once a day as a PythonAnywhere scheduled Task (free-tier
accounts get one free daily task, under the Tasks tab) — this keeps the
data safe locally on the server without needing any outbound network call
or secret, since PythonAnywhere's free tier can't reach most external
services anyway. backups/ is gitignored: it holds real student data and
must never be committed.
"""
import os
import shutil
import glob
from datetime import datetime, timedelta

BASE_DIR = '/home/DekelPA/mysite'
DB_PATH = os.path.join(BASE_DIR, 'tutor.db')
BACKUP_DIR = os.path.join(BASE_DIR, 'backups')
BACKUP_KEEP_DAYS = 14


def main():
    if not os.path.exists(DB_PATH):
        print(f'No DB found at {DB_PATH} — nothing to back up')
        return

    os.makedirs(BACKUP_DIR, exist_ok=True)
    stamp = datetime.now().strftime('%Y%m%d_%H%M%S')
    dest = os.path.join(BACKUP_DIR, f'tutor_{stamp}.db')
    shutil.copy2(DB_PATH, dest)
    print(f'Backed up to {dest}')

    cutoff = datetime.now() - timedelta(days=BACKUP_KEEP_DAYS)
    removed = 0
    for path in glob.glob(os.path.join(BACKUP_DIR, 'tutor_*.db')):
        mtime = datetime.fromtimestamp(os.path.getmtime(path))
        if mtime < cutoff:
            os.remove(path)
            removed += 1
    if removed:
        print(f'Removed {removed} backup(s) older than {BACKUP_KEEP_DAYS} days')


if __name__ == '__main__':
    main()
