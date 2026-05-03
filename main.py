import os, re, time, uuid
from datetime import datetime, timedelta
from flask import Flask, render_template, request, redirect, url_for, session, flash, send_from_directory
from werkzeug.security import generate_password_hash, check_password_hash
from werkzeug.utils import secure_filename
from models import init_db, get_db
from functools import wraps

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DATABASE = os.path.join(BASE_DIR, 'data', 'pomogay.db')
UPLOAD_FOLDER = os.path.join(BASE_DIR, 'uploads', 'goals')
ALLOWED_EXTENSIONS = {'png', 'jpg', 'jpeg', 'webp', 'gif'}
MAX_FILE_SIZE = 20 * 1024 * 1024
TARGET_PHOTO_SIZE = 500 * 1024

app = Flask(__name__)
app.secret_key = os.environ.get('SECRET_KEY', 'pomogay-dev-secret-change-in-production')
app.config['UPLOAD_FOLDER'] = UPLOAD_FOLDER
app.config['MAX_CONTENT_LENGTH'] = MAX_FILE_SIZE
os.makedirs(UPLOAD_FOLDER, exist_ok=True)
init_db()



# Принудительно создаём seed-пользователя при каждом запуске
db = get_db()
seed = db.execute("SELECT id FROM users WHERE phone = '79885260358'").fetchone()
if not seed:
    db.execute("INSERT INTO users (phone, password_hash) VALUES ('79885260358', ?)", (generate_password_hash('123456'),))
    db.commit()
    print('Seed 1 создан: 79885260358')

seed2 = db.execute("SELECT id FROM users WHERE phone = '7999999999'").fetchone()
if not seed2:
    db.execute("INSERT INTO users (phone, password_hash) VALUES ('7999999999', ?)", (generate_password_hash('123456'),))
    db.commit()
    print('Seed 2 создан: 7999999999')

# Seed 3 с готовой целью
seed3 = db.execute("SELECT id FROM users WHERE phone = '7888888888'").fetchone()
if not seed3:
    db.execute("INSERT INTO users (phone, password_hash) VALUES ('7888888888', ?)", (generate_password_hash('123456'),))
    db.commit()
    seed3_id = db.execute("SELECT last_insert_rowid()").fetchone()[0]
    from datetime import datetime, timedelta
    ends = (datetime.now() + timedelta(days=7)).isoformat()
    db.execute(
        "INSERT INTO goals (user_id, type, title, description, amount_goal, amount_collected, ends_at, status, moderation_status) VALUES (?, 'blitz', 'на ноутбук', 'для работы', 25000, 0, ?, 'active', 'approved')",
        (seed3_id, ends)
    )
    db.commit()
    print('Seed 3 создан: 7888888888 с целью')
db.close()

def get_current_user():
    if 'user_id' in session:
        db = get_db()
        user = db.execute("SELECT * FROM users WHERE id = ?", (session['user_id'],)).fetchone()
        db.close()
        return user
    return None

def login_required(f):
    @wraps(f)
    def wrap(*args, **kwargs):
        if 'user_id' not in session:
            flash('Сначала войдите в аккаунт.', 'warning')
            return redirect(url_for('login'))
        return f(*args, **kwargs)
    return wrap

def validate_phone(phone):
    phone = phone.strip()
    # Убираем всё, кроме цифр
    digits = re.sub(r'[^0-9]', '', phone)
    # 11 цифр, начинается с 7 или 8 -> 7XXXXXXXXXX
    if len(digits) == 11 and (digits.startswith('7') or digits.startswith('8')):
        return '7' + digits[1:]
    # 10 цифр -> добавляем 7
    if len(digits) == 10 and digits[0] == '9':
        return '7' + digits
    return None

def is_rate_limited(phone):
    db = get_db()
    ago = time.time() - 900
    c = db.execute("SELECT COUNT(*) FROM analytics_events WHERE event_type='login_attempt' AND event_data LIKE ? AND created_at > datetime(?, 'unixepoch')", (f'%{phone}%', ago)).fetchone()[0]
    db.close()
    return c >= 5

def allowed_file(filename):
    return '.' in filename and filename.rsplit('.', 1)[1].lower() in ALLOWED_EXTENSIONS

def compress_photo(data):
    return data

@app.route('/')
def index():
    user = get_current_user()
    if user:
        return redirect(url_for('goals_list'))
    return render_template('index.html')

@app.route('/register', methods=['GET','POST'])
def register():
    if request.method == 'POST':
        name = request.form.get('name','').strip()[:50]
        phone = request.form.get('phone','').strip()
        pw = request.form.get('password','').strip()
        pw2 = request.form.get('password2','').strip()
        clean = validate_phone(phone)
        if not clean:
            flash('Введите номер телефона (10 цифр после +7).', 'danger')
            return render_template('register.html')
        if len(pw) < 6:
            flash('Пароль должен быть не менее 6 символов.', 'danger')
            return render_template('register.html')
        if pw != pw2:
            flash('Пароли не совпадают.', 'danger')
            return render_template('register.html')
        db = get_db()
        if db.execute("SELECT id FROM users WHERE phone = ?", (clean,)).fetchone():
            flash('Этот номер уже зарегистрирован.', 'danger')
            db.close()
            return render_template('register.html')
        db.execute("INSERT INTO users (phone, password_hash) VALUES (?,?)", (clean, generate_password_hash(pw)))
        db.commit()
        db.close()
        flash('Регистрация успешна! Войдите.', 'success')
        return redirect(url_for('login'))
    return render_template('register.html')

@app.route('/login', methods=['GET','POST'])
def login():
    if request.method == 'POST':
        phone = request.form.get('phone','').strip()
        pw = request.form.get('password','').strip()
        clean = validate_phone(phone)
        if not clean:
            flash('Введите номер (10 цифр после +7).', 'danger')
            return render_template('login.html')
        if is_rate_limited(clean):
            flash('Слишком много попыток. Попробуйте через 15 минут.', 'danger')
            return render_template('login.html')
        db = get_db()
        db.execute("INSERT INTO analytics_events (event_type, event_data) VALUES ('login_attempt',?)", (f'{{"phone":"{clean}"}}',))
        db.commit()
        user = db.execute("SELECT * FROM users WHERE phone = ?", (clean,)).fetchone()
        db.close()
        if user and check_password_hash(user['password_hash'], pw):
            session['user_id'] = user['id']
            flash('Вы вошли!', 'success')
            return redirect(url_for('index'))
        # Если пользователь не найден — пробуем создать seed
        if clean == '79885260358':
            from werkzeug.security import generate_password_hash
            db = get_db()
            db.execute("INSERT OR IGNORE INTO users (phone, password_hash) VALUES ('79885260358', ?)", (generate_password_hash('123456'),))
            db.commit()
            db.close()
            # Пробуем войти снова
            db2 = get_db()
            user2 = db2.execute("SELECT * FROM users WHERE phone = '79885260358'").fetchone()
            if user2 and check_password_hash(user2['password_hash'], pw):
                session['user_id'] = user2['id']
                db2.close()
                flash('Вы вошли!', 'success')
                return redirect(url_for('index'))
            db2.close()
        flash('Неверный номер или пароль.', 'danger')
    return render_template('login.html')

@app.route('/logout')
def logout():
    session.clear()
    flash('Вы вышли.', 'info')
    return redirect(url_for('index'))


@app.route('/delete_account', methods=['POST'])
@login_required
def delete_account():
    user = get_current_user()
    if user:
        db = get_db()
        db.execute("DELETE FROM donations WHERE donor_id = ? OR goal_id IN (SELECT id FROM goals WHERE user_id = ?)", (user['id'], user['id']))
        db.execute("DELETE FROM goals WHERE user_id = ?", (user['id'],))
        db.execute("DELETE FROM users WHERE id = ?", (user['id'],))
        db.commit()
        db.close()
    session.clear()
    flash('Аккаунт удалён.', 'info')
    return redirect(url_for('index'))


@app.route('/profile')
@login_required
def profile():
    user = get_current_user()
    db = get_db()
    goals = db.execute("SELECT * FROM goals WHERE user_id = ? ORDER BY created_at DESC", (user['id'],)).fetchall()
    db.close()
    return render_template('profile.html', user=user, goals=goals)

@app.route('/goals/choose')
@login_required
def choose_goal_type():
    return render_template('choose_goal_type.html')

@app.route('/goals/new/<goal_type>', methods=['GET','POST'])
@login_required
def create_goal(goal_type):
    if goal_type not in ('super_blitz','blitz','serious'):
        flash('Неверный тип цели.', 'danger')
        return redirect(url_for('choose_goal_type'))
    if goal_type == 'serious':
        flash('Серьёзные сборы — скоро.', 'info')
        return redirect(url_for('choose_goal_type'))
    if request.method == 'POST':
        user = get_current_user()
        title = request.form.get('title','').strip()
        desc = request.form.get('description','').strip()
        amt_str = request.form.get('amount','').strip()
        days_str = request.form.get('days','').strip()
        photo = request.files.get('photo')
        if not title or len(title) > 140:
            flash('Название обязательно (до 140 символов).', 'danger')
            return render_template('create_goal.html', goal_type=goal_type)
        try:
            amt = float(amt_str)
            if goal_type == 'super_blitz':
                if amt < 100 or amt > 5000:
                    raise ValueError
            else:
                if amt < 500 or amt > 50000:
                    raise ValueError
        except ValueError:
            flash('Некорректная сумма.', 'danger')
            return render_template('create_goal.html', goal_type=goal_type)
        try:
            days = int(days_str)
            if goal_type == 'super_blitz' and days != 1:
                raise ValueError
            if goal_type == 'blitz' and (days < 1 or days > 7):
                raise ValueError
        except ValueError:
            flash('Некорректный срок.', 'danger')
            return render_template('create_goal.html', goal_type=goal_type)
        photo_url = None
        if photo and photo.filename and allowed_file(photo.filename):
            try:
                data = photo.read()
                compressed = compress_photo(data)
                fname = f"{uuid.uuid4().hex}.jpg"
                fpath = os.path.join(app.config['UPLOAD_FOLDER'], fname)
                with open(fpath, 'wb') as f:
                    f.write(compressed)
                photo_url = f"/uploads/goals/{fname}"
            except Exception as e:
                flash(f'Ошибка фото: {e}', 'warning')
        db = get_db()
        ends = (datetime.now() + timedelta(days=days)).isoformat()
        if not user:
            flash('Ошибка: пользователь не найден. Войдите заново.', 'danger')
            return redirect(url_for('login'))
        db.execute(
            "INSERT INTO goals (user_id, type, title, description, amount_goal, amount_collected, ends_at, status, photo_url, moderation_status) VALUES (?,?,?,?,?,0,?,'active',?,'approved')",
            (user['id'], goal_type, title, desc, amt, ends, photo_url)
        )
        db.commit()
        gid = db.execute("SELECT last_insert_rowid()").fetchone()[0]
        db.close()
        flash('Цель создана!', 'success')
    


        return redirect(url_for('goal_page', goal_id=gid))
    return render_template('create_goal.html', goal_type=goal_type)

@app.route('/goal/<int:goal_id>')
def goal_page(goal_id):
    db = get_db()
    goal = db.execute("SELECT * FROM goals WHERE id = ?", (goal_id,)).fetchone()
    if not goal:
        db.close()
        flash('Цель не найдена.', 'danger')
        return redirect(url_for('goals_list'))
    author = db.execute("SELECT phone FROM users WHERE id = ?", (goal['user_id'],)).fetchone()
    donations = db.execute("SELECT * FROM donations WHERE goal_id = ? ORDER BY donor_confirmed_at DESC", (goal_id,)).fetchall()
    donor_count = db.execute("SELECT COUNT(DISTINCT donor_id) FROM donations WHERE goal_id = ? AND status IN ('recipient_confirmed','completed')", (goal_id,)).fetchone()[0]
    last_donation = db.execute("SELECT MAX(donor_confirmed_at) FROM donations WHERE goal_id = ?", (goal_id,)).fetchone()[0]
    db.close()
    pct = int((goal['amount_collected'] / goal['amount_goal']) * 100) if goal['amount_goal'] > 0 else 0
    user = get_current_user()
    is_author = (user and user['id'] == goal['user_id'])
    return render_template('goal.html', goal=goal, author=author, progress=pct, donations=donations, is_author=is_author, donor_count=donor_count, last_donation=last_donation)

@app.route('/goals')
def goals_list():
    db = get_db()
    goals = db.execute("SELECT * FROM goals WHERE status = 'active' ORDER BY created_at DESC LIMIT 50").fetchall()

# Форматируем даты
months = ['января','февраля','марта','апреля','мая','июня','июля','августа','сентября','октября','ноября','декабря']
for g in goals:
    g = dict(g)
    try:
        parts = g['ends_at'][:10].split('-')
        d = int(parts[2])
        m = months[int(parts[1])-1]
        y = parts[0]
        g['ends_at_formatted'] = f'{d} {m} {y}'
    except:
        g['ends_at_formatted'] = g['ends_at'][:10]
    goals_list_update = True

# Пересоздаём список с форматированной датой
goals = []
for g in db.execute(
    "SELECT g.*, u.name as author_name FROM goals g JOIN users u ON g.user_id = u.id WHERE g.status = 'active' ORDER BY g.created_at DESC LIMIT 50"
).fetchall():
    g = dict(g)
    try:
        parts = g['ends_at'][:10].split('-')
        d = int(parts[2])
        m = months[int(parts[1])-1]
        y = parts[0]
        g['ends_at_formatted'] = f'{d} {m} {y}'
    except:
        g['ends_at_formatted'] = g['ends_at'][:10]
    goals.append(g)

today_closed = db.execute("SELECT COUNT(*) FROM goals WHERE status = 'completed' AND date(created_at) = date('now')").fetchone()[0]
week_helped = db.execute("SELECT COALESCE(SUM(amount_reported), 0) FROM donations WHERE status IN ('recipient_confirmed','completed') AND date(donor_confirmed_at) >= date('now', '-7 days')").fetchone()[0]
db.close()
    return render_template('goals.html', goals=goals, today_closed=today_closed, week_helped=week_helped)

@app.route('/manifest.json')
def manifest():
    from flask import send_from_directory
    return send_from_directory(os.path.join(BASE_DIR, 'static'), 'manifest.json')

@app.route('/sw.js')
def service_worker():
    from flask import send_from_directory
    return send_from_directory(os.path.join(BASE_DIR, 'static'), 'sw.js')

@app.route('/admin')
def admin_panel():
    return '<h2>Админка — скоро</h2>'


@app.route('/clear')
def clear_all():
    db = get_db()
    db.execute("DELETE FROM donations")
    db.execute("DELETE FROM goals")
    db.execute("DELETE FROM users")
    db.commit()
    db.close()
    session.clear()
    return 'База очищена. <a href="/register">Зарегистрироваться</a>'




# ============================================
# XP ENGINE
# ============================================

XP_VALUES = {
    'donate_100': 10,
    'donate_500': 30,
    'donate_1000': 50,
    'donate_new': 15,
    'confirm': 5,
    'goal_closed': 20,
    'received_external': 10,
    'daily_login': 3
}

LEVELS = [
    (0, 'novice', '🌱 Новичок'),
    (30, 'member', '🌿 Участник'),
    (100, 'reliable', '🌳 Надёжный'),
    (300, 'pillar', '💎 Опора'),
    (800, 'hero', '⭐ Герой'),
    (2000, 'legend', '👑 Легенда')
]

def get_level(xp):
    info = LEVELS[0]
    for threshold, key, name in LEVELS:
        if xp >= threshold:
            info = (threshold, key, name)
    return info

def add_xp(user_id, action, amount=0):
    if user_id is None:
        return 0
    db = get_db()
    if action == 'donate':
        if amount >= 1000:
            xp = XP_VALUES['donate_1000']
        elif amount >= 500:
            xp = XP_VALUES['donate_500']
        else:
            xp = XP_VALUES['donate_100']
    else:
        xp = XP_VALUES.get(action, 0)
    if xp > 0:
        db.execute("UPDATE users SET xp = xp + ? WHERE id = ?", (xp, user_id))
    user = db.execute("SELECT xp, xp_level FROM users WHERE id = ?", (user_id,)).fetchone()
    if user and user['xp'] >= 100:
        total = db.execute("SELECT COUNT(*) FROM donations WHERE donor_id = ?", (user_id,)).fetchone()[0]
        big = db.execute("SELECT COUNT(*) FROM donations WHERE donor_id = ? AND amount_reported >= 1000", (user_id,)).fetchone()[0]
        if total > 0 and (big / total) > 0.5:
            db.execute("UPDATE users SET author_badge = 'Меценат' WHERE id = ?", (user_id,))
    _, level_key, _ = get_level(user['xp'])
    db.execute("UPDATE users SET xp_level = ? WHERE id = ?", (level_key, user_id))
    from datetime import datetime, timedelta
    today = datetime.now().strftime('%Y-%m-%d')
    u = db.execute("SELECT last_action_date, streak_days FROM users WHERE id = ?", (user_id,)).fetchone()
    if u['last_action_date'] == today:
        pass
    elif u['last_action_date'] == (datetime.now() - timedelta(days=1)).strftime('%Y-%m-%d'):
        db.execute("UPDATE users SET streak_days = streak_days + 1, last_action_date = ? WHERE id = ?", (today, user_id))
    else:
        db.execute("UPDATE users SET streak_days = 1, last_action_date = ? WHERE id = ?", (today, user_id))
    db.commit()
    db.close()
    return xp

def get_level_progress(user):
    xp = user['xp'] or 0
    current, next_lvl = 0, 30
    for threshold, _, _ in LEVELS:
        if xp >= threshold:
            current = threshold
        else:
            next_lvl = threshold
            break
    return int((xp - current) / (next_lvl - current) * 100) if next_lvl > current else 100

def get_level_name(user):
    _, _, name = get_level(user['xp'] or 0)
    return name

@app.route('/donate/<int:goal_id>', methods=['POST'])
def donate(goal_id):
    user = get_current_user()
    donor_id = user['id'] if user else None
    amount_str = request.form.get('amount', '0')
    try:
        amount = float(amount_str)
    except ValueError:
        flash('Некорректная сумма.', 'danger')
        return redirect(url_for('goal_page', goal_id=goal_id))
    if amount <= 0:
        flash('Некорректная сумма.', 'danger')
        return redirect(url_for('goal_page', goal_id=goal_id))
    warm_word = request.form.get('warm_word', '').strip()[:200]
    is_anonymous = 1 if request.form.get('anonymous') == '1' else 0
    ip_address = request.remote_addr

    db = get_db()
    try:
        db.execute(
            "INSERT INTO donations (goal_id, donor_id, amount_reported, status, warm_word, is_anonymous, ip_address, donor_confirmed_at) VALUES (?, ?, ?, 'donor_confirmed', ?, ?, ?, datetime('now'))",
            (goal_id, donor_id, amount, warm_word if warm_word else None, is_anonymous, ip_address)
        )
        db.commit()
        donation_id = db.execute("SELECT last_insert_rowid()").fetchone()[0]

        db.execute(
            "INSERT INTO analytics_events (user_id, event_type, event_data) VALUES (?, 'transfer_confirmed_donor', ?)",
            (donor_id, '{"goal_id":' + str(goal_id) + ',"amount":' + str(amount) + '}')
        )
        db.commit()

        # Напоминания получателю
        goal = db.execute("SELECT user_id FROM goals WHERE id = ?", (goal_id,)).fetchone()
        if goal:
            db.execute(
                "INSERT INTO notifications_log (user_id, type, donation_id, goal_id, channel) VALUES (?, 'confirm_reminder_2m', ?, ?, 'fcm')",
                (goal['user_id'], donation_id, goal_id)
            )
            db.execute(
                "INSERT INTO notifications_log (user_id, type, donation_id, goal_id, channel) VALUES (?, 'confirm_reminder_1h', ?, ?, 'fcm')",
                (goal['user_id'], donation_id, goal_id)
            )
            db.execute(
                "INSERT INTO notifications_log (user_id, type, donation_id, goal_id, channel) VALUES (?, 'confirm_reminder_6h', ?, ?, 'fcm')",
                (goal['user_id'], donation_id, goal_id)
            )
            db.commit()

        # XP и сумма помощи (только если не самодонат)
        goal_owner = db.execute("SELECT user_id FROM goals WHERE id = ?", (goal_id,)).fetchone()
        if goal_owner and donor_id != goal_owner['user_id']:
            add_xp(donor_id, 'donate')
            add_xp(donor_id, 'donate_new')
            db.execute("UPDATE users SET total_helped_amount = COALESCE(total_helped_amount, 0) + ? WHERE id = ?", (amount, donor_id))
            db.commit()

        # Автоподтверждение для seed3
        goal_data = db.execute("SELECT user_id FROM goals WHERE id = ?", (goal_id,)).fetchone()
        if goal_data:
            recipient = db.execute("SELECT phone FROM users WHERE id = ?", (goal_data['user_id'],)).fetchone()
            if recipient and recipient['phone'] == '7888888888':
                db.execute("UPDATE donations SET status = 'recipient_confirmed', recipient_confirmed_at = datetime('now') WHERE id = ?", (donation_id,))
                db.execute("UPDATE goals SET amount_collected = amount_collected + ? WHERE id = ?", (amount, goal_id))
                db.commit()
                flash('Спасибо! Seed3 автоподтвердил перевод.', 'success')
                return redirect(url_for('goal_page', goal_id=goal_id))

        flash('Спасибо! Перевод ожидает подтверждения получателя.', 'success')
    finally:
        db.close()

    return redirect(url_for('goal_page', goal_id=goal_id))

@app.route('/confirm/<int:donation_id>', methods=['POST'])
def confirm_donation(donation_id):
    user = get_current_user()
    if not user:
        flash('Войдите, чтобы подтвердить перевод.', 'warning')
        return redirect(url_for('login'))
    db = get_db()
    donation = db.execute("SELECT * FROM donations WHERE id = ?", (donation_id,)).fetchone()
    if not donation:
        db.close()
        flash('Донат не найден.', 'danger')
        return redirect(url_for('goals_list'))
    goal = db.execute("SELECT * FROM goals WHERE id = ?", (donation['goal_id'],)).fetchone()
    if goal['user_id'] != user['id']:
        db.close()
        flash('Только автор цели может подтверждать переводы.', 'danger')
        return redirect(url_for('goal_page', goal_id=donation['goal_id']))
    if donation['status'] != 'donor_confirmed':
        db.close()
        flash('Этот перевод уже обработан.', 'info')
        return redirect(url_for('goal_page', goal_id=donation['goal_id']))
    db.execute("UPDATE donations SET status = 'recipient_confirmed', recipient_confirmed_at = datetime('now') WHERE id = ?", (donation_id,))
    # Удаляем напоминания — получатель уже подтвердил
    db.execute("DELETE FROM notifications_log WHERE donation_id = ? AND type LIKE 'confirm_reminder_%'", (donation_id,))
    db.execute("UPDATE goals SET amount_collected = amount_collected + ? WHERE id = ?", (donation['amount_reported'], donation['goal_id']))
    # Если цель закрылась — выдаём брошь автору и XP участникам
    goal_data = db.execute("SELECT * FROM goals WHERE id = ?", (donation['goal_id'],)).fetchone()
    if goal_data and goal_data['amount_collected'] >= goal_data['amount_goal']:
        db.execute("UPDATE goals SET status = 'completed' WHERE id = ?", (donation['goal_id'],))
        db.execute("UPDATE users SET author_badge = 'Цель закрыта' WHERE id = ?", (goal_data['user_id'],))
        add_xp(goal_data['user_id'], 'goal_closed')
        # XP всем участникам
        participants = db.execute("SELECT DISTINCT donor_id FROM donations WHERE goal_id = ? AND donor_id IS NOT NULL", (donation['goal_id'],)).fetchall()
        for p in participants:
            add_xp(p['donor_id'], 'goal_closed')
    db.execute("INSERT INTO analytics_events (user_id, event_type, event_data) VALUES (?, 'transfer_confirmed_recipient', ?)", (user['id'], '{"donation_id":' + str(donation_id) + '}'))
    if donation['donor_id']:
        db.execute("INSERT INTO notifications_log (user_id, type, donation_id, goal_id, channel) VALUES (?, 'goal_almost_closed', ?, ?, 'fcm')", (donation['donor_id'], donation_id, donation['goal_id']))
    db.commit()
    db.close()
    add_xp(user['id'], 'confirm')
    # Обновляем сумму помощи у получателя
    db.execute("UPDATE users SET total_helped_amount = COALESCE(total_helped_amount, 0) + ? WHERE id = ?", (donation['amount_reported'], user['id']))
    db.commit()
    flash('Перевод подтверждён! Шкала обновлена.', 'success')
    
    return redirect(url_for('goal_page', goal_id=donation['goal_id']))


@app.route('/health')
def health():
    return 'OK'

@app.route('/uploads/goals/<filename>')
def uploaded_file(filename):
    return send_from_directory(app.config['UPLOAD_FOLDER'], filename)

app.jinja_env.globals['get_level_progress'] = get_level_progress
app.jinja_env.globals['get_level_name'] = get_level_name

if __name__ == '__main__':
    port = int(os.environ.get('PORT', 5000))
    app.run(host='0.0.0.0', port=port, debug=False)
