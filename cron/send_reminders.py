"""Cron-скрипт отправки напоминаний о неподтверждённых донатах.
Запуск: каждые 5 минут.
Логика:
- confirm_reminder_5m/1h/6h/12h/23h — по времени от donor_confirmed_at
- confirm_reminder_daily — раз в 23+ часа для донатов >24ч без подтверждения
"""
import sqlite3
import os
import logging

logging.basicConfig(level=logging.INFO, format='%(asctime)s [%(levelname)s] %(message)s')
logger = logging.getLogger('reminders')

DB = os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', 'data', 'pomogay.db')

# Интервалы: (тип, минуты)
INTERVALS = [
    ('confirm_reminder_5m', 5),
    ('confirm_reminder_1h', 60),
    ('confirm_reminder_6h', 360),
    ('confirm_reminder_12h', 720),
    ('confirm_reminder_23h', 1380),
]

conn = sqlite3.connect(DB, timeout=30)
conn.row_factory = sqlite3.Row
conn.execute("PRAGMA busy_timeout=30000")

# ============================================
# 1. Обычные напоминания по интервалам
# ============================================
for reminder_type, minutes in INTERVALS:
    rows = conn.execute("""
        SELECT nl.id, nl.user_id, nl.donation_id, nl.goal_id, d.amount_reported
        FROM notifications_log nl
        JOIN donations d ON nl.donation_id = d.id
        WHERE nl.type = ?
          AND nl.sent_at IS NULL
          AND d.status = 'donor_confirmed'
          AND datetime(d.donor_confirmed_at) <= datetime('now', ?)
    """, (reminder_type, f'-{minutes} minutes')).fetchall()

    for row in rows:
        amount = row['amount_reported'] or 0
        title = 'Подтвердите перевод'
        if reminder_type == 'confirm_reminder_5m':
            message = f'Перевод на {amount:.0f} ₽ ждёт вашего подтверждения'
        elif reminder_type == 'confirm_reminder_1h':
            message = f'Прошёл час. Подтвердите перевод на {amount:.0f} ₽'
        elif reminder_type == 'confirm_reminder_6h':
            message = f'Не забудьте подтвердить перевод на {amount:.0f} ₽'
        elif reminder_type == 'confirm_reminder_12h':
            message = f'Важно: подтверждение перевода на {amount:.0f} ₽ влияет на доверие к вашей цели'
        else:  # 23h
            message = f'Без подтверждения перевода на {amount:.0f} ₽ ваша цель может быть снята с публикации'

        action_url = f'/goal/{row["goal_id"]}'

        # UI-уведомление в колокольчик
        conn.execute(
            "INSERT INTO notifications (user_id, type, title, message, action_url) VALUES (?, ?, ?, ?, ?)",
            (row['user_id'], reminder_type, title, message, action_url)
        )
        # Помечаем отправленным
        conn.execute(
            "UPDATE notifications_log SET sent_at = datetime('now') WHERE id = ?",
            (row['id'],)
        )
        logger.info(f'Напоминание {reminder_type} → user {row["user_id"]}, донат {row["donation_id"]}')

# ============================================
# 2. Daily-напоминания для донатов >24ч
# ============================================
daily_rows = conn.execute("""
    SELECT d.id AS donation_id, d.amount_reported, d.donor_confirmed_at,
           g.user_id AS recipient_id, g.id AS goal_id,
           CAST(julianday('now') - julianday(d.donor_confirmed_at) AS INTEGER) AS days_waiting,
           (SELECT MAX(sent_at) FROM notifications_log
            WHERE donation_id = d.id AND type = 'confirm_reminder_daily') AS last_daily
    FROM donations d
    JOIN goals g ON d.goal_id = g.id
    WHERE d.status = 'donor_confirmed'
      AND datetime(d.donor_confirmed_at) <= datetime('now', '-24 hours')
""").fetchall()

for row in daily_rows:
    last_daily = row['last_daily']
    # Отправляем, если daily ещё не было или последнее >23 часов назад
    if last_daily:
        hours_since = conn.execute(
            "SELECT (julianday('now') - julianday(?)) * 24",
            (last_daily,)
        ).fetchone()[0]
        if hours_since < 23:
            continue

    amount = row['amount_reported'] or 0
    days = row['days_waiting']
    title = 'Неподтверждённый перевод'
    message = f'Перевод на {amount:.0f} ₽ висит {days} дн. Подтвердите — иначе цель может быть снята с публикации'
    action_url = f'/goal/{row["goal_id"]}'

    conn.execute(
        "INSERT INTO notifications (user_id, type, title, message, action_url) VALUES (?, ?, ?, ?, ?)",
        (row['recipient_id'], 'confirm_reminder_daily', title, message, action_url)
    )
    conn.execute(
        "INSERT INTO notifications_log (user_id, type, donation_id, goal_id, channel, sent_at) VALUES (?, 'confirm_reminder_daily', ?, ?, 'fcm', datetime('now'))",
        (row['recipient_id'], row['donation_id'], row['goal_id'])
    )
    logger.info(f'Daily-напоминание → user {row["recipient_id"]}, донат {row["donation_id"]}, {days} дн.')

conn.commit()
conn.close()
logger.info('Напоминания обработаны.')
