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
    return render_template('index.html', user=user)

@app.route('/register', methods=['GET','POST'])
def register():
    if request.method == 'POST':
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
    today_closed = db.execute("SELECT COUNT(*) FROM goals WHERE status = 'completed' AND date(created_at) = date('now')").fetchone()[0]
    week_helped = db.execute("SELECT COALESCE(SUM(amount_reported), 0) FROM donations WHERE status IN ('recipient_confirmed','completed') AND date(donor_confirmed_at) >= date('now', '-7 days')").fetchone()[0]
    db.close()
    return render_template('goals.html', goals=goals, today_closed=today_closed, week_helped=week_helped)

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



@app.route('/donate/<int:goal_id>', methods=['POST'])
def donate(goal_id):
    user = get_current_user()
    # Разрешаем донат без регистрации
    amount_str = request.form.get('amount', '0')
    try:
        amount = float(amount_str)
    except ValueError:
        flash('Некорректная сумма.', 'danger')
        return redirect(url_for('goal_page', goal_id=goal_id))
    if amount <= 0:
        flash('Некорректная сумма.', 'danger')
        return redirect(url_for('goal_page', goal_id=goal_id))
    db = get_db()
    db.execute(
        "INSERT INTO donations (goal_id, donor_id, amount_reported, status, donor_confirmed_at) VALUES (?, ?, ?, 'donor_confirmed', datetime('now'))",
        (goal_id, user['id'], amount)
    )
    db.commit()
    db.execute(
        "INSERT INTO analytics_events (user_id, event_type, event_data) VALUES (?, 'transfer_confirmed_donor', ?)",
        (user['id'], '{"goal_id":' + str(goal_id) + ',"amount":' + str(amount) + '}')
    )
    db.commit()
    db.close()
    # Создаём напоминания получателю
    goal = db.execute("SELECT user_id FROM goals WHERE id = ?", (goal_id,)).fetchone()
    if goal:
        # +2 минуты
        db.execute(
            "INSERT INTO notifications_log (user_id, type, donation_id, goal_id, channel) VALUES (?, 'confirm_reminder_2m', ?, ?, 'fcm')",
            (goal['user_id'], donation_id, goal_id)
        )
        # +1 час
        db.execute(
            "INSERT INTO notifications_log (user_id, type, donation_id, goal_id, channel) VALUES (?, 'confirm_reminder_1h', ?, ?, 'fcm')",
            (goal['user_id'], donation_id, goal_id)
        )
        # +6 часов
        db.execute(
            "INSERT INTO notifications_log (user_id, type, donation_id, goal_id, channel) VALUES (?, 'confirm_reminder_6h', ?, ?, 'fcm')",
            (goal['user_id'], donation_id, goal_id)
        )
        db.commit()
    
    flash('Спасибо! Перевод ожидает подтверждения получателя.', 'success')
    


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
    db.execute("INSERT INTO analytics_events (user_id, event_type, event_data) VALUES (?, 'transfer_confirmed_recipient', ?)", (user['id'], '{"donation_id":' + str(donation_id) + '}'))
    if donation['donor_id']:
        db.execute("INSERT INTO notifications_log (user_id, type, donation_id, goal_id, channel) VALUES (?, 'goal_almost_closed', ?, ?, 'fcm')", (donation['donor_id'], donation_id, donation['goal_id']))
    db.commit()
    db.close()
    flash('Перевод подтверждён! Шкала обновлена.', 'success')
    
    return redirect(url_for('goal_page', goal_id=donation['goal_id']))


@app.route('/health')
def health():
    return 'OK'

@app.route('/uploads/goals/<filename>')
def uploaded_file(filename):
    return send_from_directory(app.config['UPLOAD_FOLDER'], filename)

if __name__ == '__main__':
    port = int(os.environ.get('PORT', 5000))
    app.run(host='0.0.0.0', port=port, debug=False)
