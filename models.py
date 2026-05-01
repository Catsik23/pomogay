
import sqlite3, os

DATABASE = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'data', 'pomogay.db')

def get_db():
    conn = sqlite3.connect(DATABASE)
    conn.row_factory = sqlite3.Row
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
        'ip_registered': 'TEXT'
    }
    for col_name, col_def in new_columns.items():
        if col_name not in columns:
            try:
                cur.execute(f"ALTER TABLE users ADD COLUMN {col_name} {col_def}")
            except:
                pass
    # ip_address в donations
    cur.execute("PRAGMA table_info(donations)")
    donation_cols = [row[1] for row in cur.fetchall()]
    if 'ip_address' not in donation_cols:
        try:
            cur.execute("ALTER TABLE donations ADD COLUMN ip_address TEXT")
        except:
            pass
    conn.commit()
    conn.close()


def init_db():
    os.makedirs(os.path.dirname(DATABASE), exist_ok=True)
    conn = get_db()
    migrate_db()
    c = conn.cursor()
    c.execute("CREATE TABLE IF NOT EXISTS users (id INTEGER PRIMARY KEY AUTOINCREMENT, phone TEXT UNIQUE NOT NULL, password_hash TEXT NOT NULL, email TEXT NULL, name TEXT NULL, avatar_url TEXT NULL, max_id TEXT NULL, vk_id TEXT NULL, fcm_token TEXT NULL, onboarding_passed INTEGER DEFAULT 0, rating REAL DEFAULT 0.0, rating_level TEXT DEFAULT 'novice', confirmations_total INTEGER DEFAULT 0, confirmations_approved INTEGER DEFAULT 0, confirmations_ignored INTEGER DEFAULT 0, donation_streak INTEGER DEFAULT 0, goals_helped_close INTEGER DEFAULT 0, total_helped_amount REAL DEFAULT 0.00, priority_days INTEGER DEFAULT 0, circles_of_care INTEGER DEFAULT 0, interest_weights TEXT DEFAULT NULL, eternal_donation_config TEXT DEFAULT NULL, yellow_card_until TEXT NULL, is_banned INTEGER DEFAULT 0, created_at TEXT DEFAULT (datetime('now')))")
    c.execute("CREATE TABLE IF NOT EXISTS goals (id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER NOT NULL, type TEXT NOT NULL CHECK(type IN ('blitz','serious')), title TEXT NOT NULL, description TEXT NULL, photo_url TEXT NULL, amount_goal REAL NOT NULL, amount_collected REAL DEFAULT 0.00, ends_at TEXT NULL, status TEXT DEFAULT 'draft' CHECK(status IN ('draft','active','frozen','completed','failed','lightning_failed','hidden')), trial_ends_at TEXT NULL, subscription_active_until TEXT NULL, last_boosted_at TEXT NULL, is_lightning INTEGER DEFAULT 0, lightning_started_at TEXT NULL, moderation_status TEXT DEFAULT 'pending', completion_type TEXT DEFAULT 'organic', is_seed INTEGER DEFAULT 0, views_count INTEGER DEFAULT 0, views_24h INTEGER DEFAULT 0, donations_count INTEGER DEFAULT 0, donations_24h INTEGER DEFAULT 0, created_at TEXT DEFAULT (datetime('now')), FOREIGN KEY (user_id) REFERENCES users(id))")
    c.execute("CREATE TABLE IF NOT EXISTS donations (id INTEGER PRIMARY KEY AUTOINCREMENT, goal_id INTEGER NOT NULL, donor_id INTEGER NULL, amount_reported REAL NOT NULL, status TEXT NOT NULL CHECK(status IN ('donor_confirmed','recipient_confirmed','completed','disputed')), is_anonymous INTEGER DEFAULT 0, is_seed INTEGER DEFAULT 0, warm_word TEXT NULL, donor_confirmed_at TEXT DEFAULT (datetime('now')), recipient_confirmed_at TEXT NULL, completed_at TEXT NULL, dispute_reason TEXT NULL, donor_to_recipient_count INTEGER DEFAULT 1, referred_by INTEGER NULL, FOREIGN KEY (goal_id) REFERENCES goals(id), FOREIGN KEY (donor_id) REFERENCES users(id))")
    c.execute("CREATE TABLE IF NOT EXISTS subscriptions (id INTEGER PRIMARY KEY AUTOINCREMENT, goal_id INTEGER NOT NULL, user_id INTEGER NOT NULL, amount REAL NOT NULL, weeks INTEGER DEFAULT 1, payment_id TEXT NULL, status TEXT DEFAULT 'pending', paid_at TEXT NULL, expires_at TEXT NOT NULL, created_at TEXT DEFAULT (datetime('now')), FOREIGN KEY (goal_id) REFERENCES goals(id), FOREIGN KEY (user_id) REFERENCES users(id))")
    c.execute("CREATE TABLE IF NOT EXISTS feature_flags (id INTEGER PRIMARY KEY AUTOINCREMENT, flag_key TEXT UNIQUE NOT NULL, enabled INTEGER DEFAULT 0, value_json TEXT NULL, description TEXT NULL, updated_at TEXT DEFAULT (datetime('now')))")
    c.execute("CREATE TABLE IF NOT EXISTS notifications_log (id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER NOT NULL, type TEXT NOT NULL, donation_id INTEGER NULL, goal_id INTEGER NULL, channel TEXT DEFAULT 'fcm', sent_at TEXT DEFAULT (datetime('now')), FOREIGN KEY (user_id) REFERENCES users(id))")
    c.execute("CREATE TABLE IF NOT EXISTS analytics_events (id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER NULL, event_type TEXT NOT NULL, event_data TEXT NULL, created_at TEXT DEFAULT (datetime('now')))")
    c.execute("CREATE TABLE IF NOT EXISTS reports (id INTEGER PRIMARY KEY AUTOINCREMENT, goal_id INTEGER NOT NULL, reporter_id INTEGER NOT NULL, reason TEXT NULL, created_at TEXT DEFAULT (datetime('now')), FOREIGN KEY (goal_id) REFERENCES goals(id), FOREIGN KEY (reporter_id) REFERENCES users(id))")
    c.execute("CREATE TABLE IF NOT EXISTS deferred_donations (id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER NOT NULL, goal_id INTEGER NOT NULL, remind_at TEXT NULL, status TEXT DEFAULT 'pending', created_at TEXT DEFAULT (datetime('now')), FOREIGN KEY (user_id) REFERENCES users(id), FOREIGN KEY (goal_id) REFERENCES goals(id))")
    c.execute("CREATE TABLE IF NOT EXISTS community_pots (id INTEGER PRIMARY KEY AUTOINCREMENT, title TEXT NOT NULL, description TEXT NULL, created_by INTEGER NOT NULL, status TEXT DEFAULT 'active', created_at TEXT DEFAULT (datetime('now')), FOREIGN KEY (created_by) REFERENCES users(id))")
    c.execute("CREATE TABLE IF NOT EXISTS community_pot_goals (id INTEGER PRIMARY KEY AUTOINCREMENT, pot_id INTEGER NOT NULL, goal_id INTEGER NOT NULL, FOREIGN KEY (pot_id) REFERENCES community_pots(id), FOREIGN KEY (goal_id) REFERENCES goals(id))")
    conn.commit()
    conn.close()
    print("База данных готова. Все 11 таблиц созданы.")
