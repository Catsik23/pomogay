
import os
import re
import time
from flask import Flask, render_template, request, redirect, url_for, session, flash
from werkzeug.security import generate_password_hash, check_password_hash
from models import init_db, get_db
from functools import wraps

# --- Конфигурация ---
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DATABASE = os.path.join(BASE_DIR, 'data', 'pomogay.db')

app = Flask(__name__)
app.secret_key = os.environ.get('SECRET_KEY', 'pomogay-dev-secret-change-in-production')

# --- Инициализация БД ---
init_db()

# --- Вспомогательные функции ---
def get_current_user():
    if 'user_id' in session:
        db = get_db()
        user = db.execute("SELECT * FROM users WHERE id = ?", (session['user_id'],)).fetchone()
        db.close()
        return user
    return None

def login_required(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if 'user_id' not in session:
            flash('Сначала войдите в аккаунт.', 'warning')
            return redirect(url_for('login'))
        return f(*args, **kwargs)
    return decorated_function

def validate_phone(phone):
    phone = phone.strip()
    # Приводим к формату +7XXXXXXXXXX
    phone = re.sub(r'[^0-9+]', '', phone)
    if phone.startswith('8'):
        phone = '+7' + phone[1:]
    elif phone.startswith('7'):
        phone = '+' + phone
    if re.match(r'^\+7\d{10}$', phone):
        return phone[1:]  # сохраняем без +, только 7XXXXXXXXXX
    return None

def is_rate_limited(phone):
    db = get_db()
    fifteen_min_ago = time.time() - 900
    count = db.execute(
        "SELECT COUNT(*) FROM analytics_events WHERE event_type='login_attempt' AND event_data LIKE ? AND created_at > datetime(?, 'unixepoch')",
        (f'%{phone}%', fifteen_min_ago)
    ).fetchone()[0]
    db.close()
    return count >= 5

# --- Главная ---
@app.route('/')
def index():
    user = get_current_user()
    return render_template('index.html', user=user)

# --- Регистрация ---
@app.route('/register', methods=['GET', 'POST'])
def register():
    if request.method == 'POST':
        phone = request.form.get('phone', '').strip()
        password = request.form.get('password', '').strip()
        password2 = request.form.get('password2', '').strip()

        # Валидация телефона
        clean_phone = validate_phone(phone)
        if not clean_phone:
            flash('Введите корректный номер телефона (11 цифр, начиная с 7 или 8).', 'danger')
            return render_template('register.html')

        # Валидация пароля
        if len(password) < 6:
            flash('Пароль должен быть не менее 6 символов.', 'danger')
            return render_template('register.html')
        if password != password2:
            flash('Пароли не совпадают.', 'danger')
            return render_template('register.html')

        # Проверка, не занят ли номер
        db = get_db()
        existing = db.execute("SELECT id FROM users WHERE phone = ?", (clean_phone,)).fetchone()
        if existing:
            flash('Этот номер уже зарегистрирован.', 'danger')
            db.close()
            return render_template('register.html')

        # Создаём пользователя
        password_hash = generate_password_hash(password)
        db.execute(
            "INSERT INTO users (phone, password_hash) VALUES (?, ?)",
            (clean_phone, password_hash)
        )
        db.commit()
        db.close()

        flash('Регистрация успешна! Теперь войдите.', 'success')
        return redirect(url_for('login'))

    return render_template('register.html')

# --- Вход ---
@app.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        phone = request.form.get('phone', '').strip()
        password = request.form.get('password', '').strip()

        clean_phone = validate_phone(phone)
        if not clean_phone:
            flash('Введите корректный номер телефона.', 'danger')
            return render_template('login.html')

        # Rate limit
        if is_rate_limited(clean_phone):
            flash('Слишком много попыток. Попробуйте через 15 минут.', 'danger')
            return render_template('login.html')

        # Логируем попытку
        db = get_db()
        db.execute(
            "INSERT INTO analytics_events (event_type, event_data) VALUES ('login_attempt', ?)",
            (f'{{"phone":"{clean_phone}"}}',)
        )
        db.commit()

        # Проверяем пользователя
        user = db.execute("SELECT * FROM users WHERE phone = ?", (clean_phone,)).fetchone()
        db.close()

        if user and check_password_hash(user['password_hash'], password):
            session['user_id'] = user['id']
            flash('Вы вошли в аккаунт!', 'success')
            return redirect(url_for('index'))
        else:
            flash('Неверный номер или пароль.', 'danger')

    return render_template('login.html')

# --- Выход ---
@app.route('/logout')
def logout():
    session.clear()
    flash('Вы вышли из аккаунта.', 'info')
    return redirect(url_for('index'))

# --- Профиль (заглушка) ---
@app.route('/profile')
@login_required
def profile():
    user = get_current_user()
    return f'<h2>Профиль {user["phone"]} — скоро</h2>'

# --- Цели (заглушка) ---
@app.route('/goals')
def goals_list():
    return render_template('goals_stub.html')

# --- Страница цели (заглушка) ---
@app.route('/goal/<int:goal_id>')
def goal_page(goal_id):
    return f'<h2>Цель #{goal_id} — скоро</h2>'

# --- Админка (заглушка) ---
@app.route('/admin')
def admin_panel():
    return '<h2>Админка — скоро</h2>'

# --- Health check ---
@app.route('/health')
def health():
    return 'OK'

# --- Запуск ---
if __name__ == '__main__':
    port = int(os.environ.get('PORT', 5000))
    app.run(host='0.0.0.0', port=port, debug=False)
