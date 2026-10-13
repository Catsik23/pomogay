
import sqlite3, os

DATABASE = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'data', 'pomogay.db')

def get_db():
    conn = sqlite3.connect(DATABASE, timeout=10)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=NORMAL")
    conn.execute("PRAGMA busy_timeout=5000")
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def migrate_db():
    """Добавляет новые столбцы, если их нет."""
    conn = get_db()
    cur = conn.cursor()
    cur.execute("PRAGMA table_info(users)")
    columns = [row[1] for row in cur.fetchall()]
    new_columns = {
        'xp': 'INTEGER DEFAULT 0',
        'xp_level': "TEXT DEFAULT 'novice'",
        'streak_days': 'INTEGER DEFAULT 0',
        'last_action_date': 'TEXT',
        'ip_registered': 'TEXT',
        'trust_score': 'INTEGER DEFAULT 0',
        'trust_level': "TEXT DEFAULT 'guest'",
        'is_admin': 'INTEGER DEFAULT 0',
        'profile_completed': 'INTEGER DEFAULT 0',
        'identity_verified': 'INTEGER DEFAULT 0',
        'identity_verified_at': 'TEXT',
        'identity_doc_path': 'TEXT',
        'identity_selfie_path': 'TEXT',
        'city': 'TEXT',
        'region_code': 'TEXT',
        'region_name': 'TEXT',
        'birth_date': 'TEXT',
        'onboarding_step': 'INTEGER DEFAULT 0'
    }
    for col_name, col_def in new_columns.items():
        if col_name not in columns:
            try:
                cur.execute(f"ALTER TABLE users ADD COLUMN {col_name} {col_def}")
            except sqlite3.OperationalError:
                pass
    # Устанавливаем trust_score существующим пользователям, у которых он NULL
    cur.execute('UPDATE users SET trust_score = 30 WHERE trust_score IS NULL')
    cur.execute("UPDATE users SET trust_level = 'newcomer' WHERE trust_level IS NULL")
    # ip_address в donations
    cur.execute("PRAGMA table_info(donations)")
    donation_cols = [row[1] for row in cur.fetchall()]
    if 'ip_address' not in donation_cols:
        try:
            cur.execute("ALTER TABLE donations ADD COLUMN ip_address TEXT")
        except sqlite3.OperationalError:
            pass
    # goals: moderation_comment, moderated_at, moderated_by
    cur.execute("PRAGMA table_info(goals)")
    goal_cols = [row[1] for row in cur.fetchall()]
    for col_name, col_def in {
        'moderation_comment': 'TEXT',
        'moderated_at': 'TEXT',
        'moderated_by': 'INTEGER',
        'resubmission_count': 'INTEGER DEFAULT 0',
        'is_resubmission': 'INTEGER DEFAULT 0',
        'rejection_categories': 'TEXT',
    }.items():
        if col_name not in goal_cols:
            try:
                cur.execute(f"ALTER TABLE goals ADD COLUMN {col_name} {col_def}")
            except sqlite3.OperationalError:
                pass
    # photos в goals — JSON-массив путей к фото
    cur.execute("PRAGMA table_info(goals)")
    goal_cols = [row[1] for row in cur.fetchall()]
    if 'photos' not in goal_cols:
        try:
            cur.execute("ALTER TABLE goals ADD COLUMN photos TEXT")
        except sqlite3.OperationalError:
            pass
    conn.commit()
    conn.close()


def init_db():
    os.makedirs(os.path.dirname(DATABASE), exist_ok=True)
    conn = get_db()
    c = conn.cursor()
    c.execute("CREATE TABLE IF NOT EXISTS users (id INTEGER PRIMARY KEY AUTOINCREMENT, phone TEXT UNIQUE NOT NULL, password_hash TEXT NOT NULL, email TEXT NULL, name TEXT NULL, avatar_url TEXT NULL, max_id TEXT NULL, vk_id TEXT NULL, fcm_token TEXT NULL, onboarding_passed INTEGER DEFAULT 0, rating REAL DEFAULT 0.0, rating_level TEXT DEFAULT 'novice', confirmations_total INTEGER DEFAULT 0, confirmations_approved INTEGER DEFAULT 0, confirmations_ignored INTEGER DEFAULT 0, donation_streak INTEGER DEFAULT 0, goals_helped_close INTEGER DEFAULT 0, total_helped_amount REAL DEFAULT 0.00, author_badge TEXT, priority_days INTEGER DEFAULT 0, circles_of_care INTEGER DEFAULT 0, interest_weights TEXT DEFAULT NULL, eternal_donation_config TEXT DEFAULT NULL, yellow_card_until TEXT NULL, is_banned INTEGER DEFAULT 0, created_at TEXT DEFAULT (datetime('now')))")
    c.execute("CREATE TABLE IF NOT EXISTS goals (id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER NOT NULL, type TEXT NOT NULL CHECK(type IN ('super_blitz','blitz','serious','urgent','group')), title TEXT NOT NULL, description TEXT NULL, photo_url TEXT NULL, amount_goal REAL NOT NULL, amount_collected REAL DEFAULT 0.00, ends_at TEXT NULL, status TEXT DEFAULT 'draft' CHECK(status IN ('draft','active','frozen','completed','failed','lightning_failed','hidden')), trial_ends_at TEXT NULL, subscription_active_until TEXT NULL, last_boosted_at TEXT NULL, is_lightning INTEGER DEFAULT 0, lightning_started_at TEXT NULL, moderation_status TEXT DEFAULT 'pending', moderation_comment TEXT, moderated_at TEXT, moderated_by INTEGER, resubmission_count INTEGER DEFAULT 0, is_resubmission INTEGER DEFAULT 0, rejection_categories TEXT, completion_type TEXT DEFAULT 'organic', is_seed INTEGER DEFAULT 0, views_count INTEGER DEFAULT 0, views_24h INTEGER DEFAULT 0, donations_count INTEGER DEFAULT 0, donations_24h INTEGER DEFAULT 0, created_at TEXT DEFAULT (datetime('now')), FOREIGN KEY (user_id) REFERENCES users(id))")
    c.execute("CREATE TABLE IF NOT EXISTS donations (id INTEGER PRIMARY KEY AUTOINCREMENT, goal_id INTEGER NOT NULL, donor_id INTEGER NULL, amount_reported REAL NOT NULL, status TEXT NOT NULL CHECK(status IN ('donor_confirmed','recipient_confirmed','completed','disputed')), is_anonymous INTEGER DEFAULT 0, is_seed INTEGER DEFAULT 0, warm_word TEXT NULL, donor_confirmed_at TEXT DEFAULT (datetime('now')), recipient_confirmed_at TEXT NULL, completed_at TEXT NULL, dispute_reason TEXT NULL, donor_to_recipient_count INTEGER DEFAULT 1, referred_by INTEGER NULL, FOREIGN KEY (goal_id) REFERENCES goals(id), FOREIGN KEY (donor_id) REFERENCES users(id))")
    c.execute("CREATE TABLE IF NOT EXISTS subscriptions (id INTEGER PRIMARY KEY AUTOINCREMENT, goal_id INTEGER NOT NULL, user_id INTEGER NOT NULL, amount REAL NOT NULL, weeks INTEGER DEFAULT 1, payment_id TEXT NULL, status TEXT DEFAULT 'pending', paid_at TEXT NULL, expires_at TEXT NOT NULL, created_at TEXT DEFAULT (datetime('now')), FOREIGN KEY (goal_id) REFERENCES goals(id), FOREIGN KEY (user_id) REFERENCES users(id))")
    c.execute("CREATE TABLE IF NOT EXISTS feature_flags (id INTEGER PRIMARY KEY AUTOINCREMENT, flag_key TEXT UNIQUE NOT NULL, enabled INTEGER DEFAULT 0, value_json TEXT NULL, description TEXT NULL, updated_at TEXT DEFAULT (datetime('now')))")
    c.execute("CREATE TABLE IF NOT EXISTS notifications_log (id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER NOT NULL, type TEXT NOT NULL, donation_id INTEGER NULL, goal_id INTEGER NULL, channel TEXT DEFAULT 'fcm', sent_at TEXT DEFAULT (datetime('now')), FOREIGN KEY (user_id) REFERENCES users(id))")
    c.execute("CREATE TABLE IF NOT EXISTS analytics_events (id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER NULL, event_type TEXT NOT NULL, event_data TEXT NULL, created_at TEXT DEFAULT (datetime('now')))")
    c.execute("CREATE TABLE IF NOT EXISTS reports (id INTEGER PRIMARY KEY AUTOINCREMENT, goal_id INTEGER NOT NULL, reporter_id INTEGER NOT NULL, reason TEXT NULL, photo_index INTEGER NULL, created_at TEXT DEFAULT (datetime('now')), FOREIGN KEY (goal_id) REFERENCES goals(id), FOREIGN KEY (reporter_id) REFERENCES users(id))")
    c.execute("CREATE TABLE IF NOT EXISTS deferred_donations (id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER NOT NULL, goal_id INTEGER NOT NULL, remind_at TEXT NULL, status TEXT DEFAULT 'pending', frequency TEXT DEFAULT 'once', created_at TEXT DEFAULT (datetime('now')), FOREIGN KEY (user_id) REFERENCES users(id), FOREIGN KEY (goal_id) REFERENCES goals(id))")
    c.execute("CREATE TABLE IF NOT EXISTS community_pots (id INTEGER PRIMARY KEY AUTOINCREMENT, title TEXT NOT NULL, description TEXT NULL, created_by INTEGER NOT NULL, status TEXT DEFAULT 'active', created_at TEXT DEFAULT (datetime('now')), FOREIGN KEY (created_by) REFERENCES users(id))")
    c.execute("CREATE TABLE IF NOT EXISTS community_pot_goals (id INTEGER PRIMARY KEY AUTOINCREMENT, pot_id INTEGER NOT NULL, goal_id INTEGER NOT NULL, FOREIGN KEY (pot_id) REFERENCES community_pots(id), FOREIGN KEY (goal_id) REFERENCES goals(id))")
    
    # ============================================
    # Таблицы для админ-панели /_metrics
    # ============================================
    
    # Лог срабатываний Content Guard
    c.execute("""CREATE TABLE IF NOT EXISTS content_guard_log (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id INTEGER NULL,
        ip_address TEXT NULL,
        context TEXT NULL,
        original_text TEXT NULL,
        found_word TEXT NULL,
        category TEXT NULL,
        status TEXT NULL,
        user_action TEXT NULL,
        position_start INTEGER NULL,
        position_end INTEGER NULL,
        created_at TEXT DEFAULT (datetime('now'))
    )""")
    
    # Действия администратора
    c.execute("""CREATE TABLE IF NOT EXISTS admin_actions (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        admin_id INTEGER NOT NULL,
        action_type TEXT NOT NULL,
        target_type TEXT NULL,
        target_id TEXT NULL,
        details TEXT NULL,
        created_at TEXT DEFAULT (datetime('now')),
        FOREIGN KEY (admin_id) REFERENCES users(id)
    )""")
    
    # Подозрительная активность
    c.execute("""CREATE TABLE IF NOT EXISTS suspicious_activity (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id INTEGER NULL,
        ip_address TEXT NULL,
        activity_type TEXT NOT NULL,
        details TEXT NULL,
        status TEXT DEFAULT 'new',
        created_at TEXT DEFAULT (datetime('now')),
        FOREIGN KEY (user_id) REFERENCES users(id)
    )""")
    
    # Диалоги администрации с пользователями
    c.execute("""CREATE TABLE IF NOT EXISTS admin_dialogs (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id INTEGER NOT NULL,
        admin_id INTEGER NOT NULL,
        subject TEXT NULL,
        status TEXT DEFAULT 'open',
        created_at TEXT DEFAULT (datetime('now')),
        closed_at TEXT NULL,
        FOREIGN KEY (user_id) REFERENCES users(id),
        FOREIGN KEY (admin_id) REFERENCES users(id)
    )""")
    
    c.execute("""CREATE TABLE IF NOT EXISTS admin_dialog_messages (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        dialog_id INTEGER NOT NULL,
        sender_type TEXT NOT NULL,
        sender_id INTEGER NOT NULL,
        message TEXT NOT NULL,
        is_read INTEGER DEFAULT 0,
        created_at TEXT DEFAULT (datetime('now')),
        FOREIGN KEY (dialog_id) REFERENCES admin_dialogs(id)
    )""")
    
    # ============================================
    # Таблица настроек (key-value)
    # ============================================
    c.execute("""CREATE TABLE IF NOT EXISTS settings (
        key TEXT PRIMARY KEY,
        value TEXT,
        description TEXT,
        updated_at TEXT DEFAULT (datetime('now'))
    )""")
    
    # Дефолтные значения
    defaults = [
        ('max_goals_per_user', '3', 'Максимум активных целей на пользователя'),
        ('max_goals_per_hour', '3', 'Максимум новых целей в час (антифрод)'),
        ('max_reports_per_day', '5', 'Максимум жалоб в сутки от одного пользователя'),
        ('max_phone_reveals_per_hour', '3', 'Максимум показов номера в час с одного IP'),
        ('auto_approve_amount', '5000', 'Автоодобрение целей до этой суммы (₽)'),
        ('auto_hide_reports', '3', 'Автоскрытие цели после N жалоб'),
        ('notifications_enabled', '1', 'Отправлять уведомления'),
        ('reminders_enabled', '1', 'Отправлять напоминания'),
        ('registration_open', '1', 'Открыта регистрация'),
        ('goals_open', '1', 'Открыто создание целей'),
    ]
    for key, value, desc in defaults:
        c.execute(
            "INSERT OR IGNORE INTO settings (key, value, description) VALUES (?, ?, ?)",
            (key, value, desc)
        )
    
    conn.commit()
    conn.close()
    # Миграция после создания таблиц
    migrate_db()
    print("База данных готова. Все 17 таблиц созданы.")