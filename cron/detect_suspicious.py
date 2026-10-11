"""Детектор подозрительной активности. Запуск раз в час."""
import sqlite3
import os
import json
from datetime import datetime, timedelta

DB = os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', 'data', 'pomogay.db')

conn = sqlite3.connect(DB, timeout=10)
conn.row_factory = sqlite3.Row
conn.execute("PRAGMA busy_timeout=5000")
cur = conn.cursor()

def log_activity(user_id, ip_address, activity_type, details):
    """Записывает подозрительную активность, если её ещё нет."""
    # Проверяем дубликат за последние 24 часа
    existing = cur.execute("""
        SELECT id FROM suspicious_activity 
        WHERE user_id IS ? AND activity_type = ? 
        AND created_at > datetime('now', '-24 hours')
    """, (user_id, activity_type)).fetchone()
    if existing:
        return False
    cur.execute("""
        INSERT INTO suspicious_activity (user_id, ip_address, activity_type, details, status)
        VALUES (?, ?, ?, ?, 'new')
    """, (user_id, ip_address, activity_type, json.dumps(details, ensure_ascii=False)))
    return True

# ============================================
# 1. Много целей за 24 часа
# ============================================
rows = cur.execute("""
    SELECT user_id, COUNT(*) as cnt 
    FROM goals 
    WHERE created_at > datetime('now', '-24 hours')
    GROUP BY user_id HAVING cnt >= 3
""").fetchall()
for r in rows:
    u = cur.execute("SELECT phone FROM users WHERE id = ?", (r['user_id'],)).fetchone()
    log_activity(r['user_id'], None, 'mass_goals', 
                 {'count': r['cnt'], 'phone': u['phone'] if u else None})

# ============================================
# 2. Массовые регистрации с одного IP
# ============================================
rows = cur.execute("""
    SELECT ip_registered, COUNT(*) as cnt 
    FROM users 
    WHERE ip_registered IS NOT NULL
    AND created_at > datetime('now', '-7 days')
    GROUP BY ip_registered HAVING cnt >= 5
""").fetchall()
for r in rows:
    log_activity(None, r['ip_registered'], 'mass_registration',
                 {'count': r['cnt']})

# ============================================
# 3. Много флагов Content Guard за сутки
# ============================================
rows = cur.execute("""
    SELECT user_id, COUNT(*) as cnt 
    FROM content_guard_log 
    WHERE user_id IS NOT NULL
    AND created_at > datetime('now', '-24 hours')
    GROUP BY user_id HAVING cnt >= 3
""").fetchall()
for r in rows:
    log_activity(r['user_id'], None, 'many_flags', {'count': r['cnt']})

# ============================================
# 4. Не подтверждает донаты 2+ суток
# ============================================
rows = cur.execute("""
    SELECT g.user_id, COUNT(*) as cnt, MIN(d.donor_confirmed_at) as oldest
    FROM donations d
    JOIN goals g ON d.goal_id = g.id
    WHERE d.status = 'donor_confirmed'
    AND d.donor_confirmed_at < datetime('now', '-48 hours')
    GROUP BY g.user_id HAVING cnt >= 1
""").fetchall()
for r in rows:
    u = cur.execute("SELECT phone FROM users WHERE id = ?", (r['user_id'],)).fetchone()
    log_activity(r['user_id'], None, 'no_confirm_48h',
                 {'count': r['cnt'], 'oldest': r['oldest'], 'phone': u['phone'] if u else None})

# ============================================
# 5. Много жалоб на юзера (цели которого жалуют)
# ============================================
rows = cur.execute("""
    SELECT g.user_id, COUNT(*) as cnt 
    FROM reports r
    JOIN goals g ON r.goal_id = g.id
    WHERE r.created_at > datetime('now', '-7 days')
    GROUP BY g.user_id HAVING cnt >= 3
""").fetchall()
for r in rows:
    u = cur.execute("SELECT phone FROM users WHERE id = ?", (r['user_id'],)).fetchone()
    log_activity(r['user_id'], None, 'many_reports',
                 {'count': r['cnt'], 'phone': u['phone'] if u else None})

conn.commit()
conn.close()
print(f"Детектор завершён: {datetime.now().isoformat()}")
