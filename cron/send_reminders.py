#!/usr/bin/env python3
"""Cron-скрипт отправки напоминаний."""
import sqlite3, os, time

DB = os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', 'data', 'pomogay.db')

def send_reminder(user_id, msg):
    """Заглушка — в будущем PWA Push / Email."""
    print(f"  -> Пользователь {user_id}: {msg}")

conn = sqlite3.connect(DB)
cur = conn.cursor()

# +2 минуты
cur.execute("""
    SELECT n.*, d.amount_reported FROM notifications_log n
    JOIN donations d ON n.donation_id = d.id
    WHERE n.type = 'confirm_reminder_2m'
    AND n.sent_at IS NULL
    AND datetime(d.donor_confirmed_at) <= datetime('now', '-2 minutes')
""")
for row in cur.fetchall():
    send_reminder(row['user_id'], f"Подтвердите перевод на {row['amount_reported']} ₽")
    cur.execute("UPDATE notifications_log SET sent_at = datetime('now') WHERE id = ?", (row['id'],))

# +1 час
cur.execute("""
    SELECT n.*, d.amount_reported FROM notifications_log n
    JOIN donations d ON n.donation_id = d.id
    WHERE n.type = 'confirm_reminder_1h'
    AND n.sent_at IS NULL
    AND datetime(d.donor_confirmed_at) <= datetime('now', '-1 hours')
""")
for row in cur.fetchall():
    send_reminder(row['user_id'], f"Неподтверждённые переводы снижают место в ленте. Подтвердите {row['amount_reported']} ₽")
    cur.execute("UPDATE notifications_log SET sent_at = datetime('now') WHERE id = ?", (row['id'],))

# +6 часов
cur.execute("""
    SELECT n.*, d.amount_reported FROM notifications_log n
    JOIN donations d ON n.donation_id = d.id
    WHERE n.type = 'confirm_reminder_6h'
    AND n.sent_at IS NULL
    AND datetime(d.donor_confirmed_at) <= datetime('now', '-6 hours')
""")
for row in cur.fetchall():
    send_reminder(row['user_id'], f"Вы рискуете получить предупреждение. Подтвердите перевод на {row['amount_reported']} ₽")
    cur.execute("UPDATE notifications_log SET sent_at = datetime('now') WHERE id = ?", (row['id'],))

conn.commit()
conn.close()
print("Напоминания обработаны.")
