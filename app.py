
import os
import re
import time
from flask import Flask, render_template, request, redirect, url_for, session, flash
from werkzeug.security import generate_password_hash, check_password_hash
from werkzeug.utils import secure_filename
from models import init_db, get_db
from functools import wraps


# --- Конфигурация ---
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DATABASE = os.path.join(BASE_DIR, 'data', 'pomogay.db')
UPLOAD_FOLDER = os.path.join(BASE_DIR, 'uploads', 'goals')
ALLOWED_EXTENSIONS = {'png', 'jpg', 'jpeg', 'webp', 'gif'}
MAX_FILE_SIZE = 20 * 1024 * 1024  # 20 MB
TARGET_PHOTO_SIZE = 500 * 1024  # 500 KB после сжатия

app = Flask(__name__)
app.secret_key = os.environ.get('SECRET_KEY', 'pomogay-dev-secret-change-in-production')
app.config['UPLOAD_FOLDER'] = UPLOAD_FOLDER
app.config['MAX_CONTENT_LENGTH'] = MAX_FILE_SIZE

os.makedirs(UPLOAD_FOLDER, exist_ok=True)

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
    # Убираем всё, кроме цифр
    digits = re.sub(r'[^0-9]', '', phone)
    
    # Если 11 цифр и начинается с 7 или 8 — приводим к 7XXXXXXXXXX
    if len(digits) == 11 and (digits.startswith('7') or digits.startswith('8')):
        return '7' + digits[1:]
    # Если 10 цифр — добавляем 7 в начало
    if len(digits) == 10:
        return '7' + digits
    
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

def allowed_file(filename):
    return '.' in filename and filename.rsplit('.', 1)[1].lower() in ALLOWED_EXTENSIONS

def compress_photo(input_data):
    """Без сжатия — возвращает как есть."""
    return input_data

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

        clean_phone = validate_phone(phone)
        if not clean_phone:
            flash('Введите номер телефона (10 цифр после +7).', 'danger')
            return render_template('register.html')

        if len(password) < 6:
            flash('Пароль должен быть не менее 6 символов.', 'danger')
            return render_template('register.html')
        if password != password2:
            flash('Пароли не совпадают.', 'danger')
            return render_template('register.html')

        db = get_db()
        existing = db.execute("SELECT id FROM users WHERE phone = ?", (clean_phone,)).fetchone()
        if existing:
            flash('Этот номер уже зарегистрирован.', 'danger')
            db.close()
            return render_template('register.html')

        password_hash = generate_password_hash(password)
        db.execute("INSERT INTO users (phone, password_hash) VALUES (?, ?)", (clean_phone, password_hash))
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

        if is_rate_limited(clean_phone):
            flash('Слишком много попыток. Попробуйте через 15 минут.', 'danger')
            return render_template('login.html')

        db = get_db()
        db.execute(
            "INSERT INTO analytics_events (event_type, event_data) VALUES ('login_attempt', ?)",
            (f'{{"phone":"{clean_phone}"}}',)
        )
        db.commit()

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

# --- Профиль ---
@app.route('/profile')
@login_required
def profile():
    user = get_current_user()
    db = get_db()
    goals = db.execute("SELECT * FROM goals WHERE user_id = ? ORDER BY created_at DESC", (user['id'],)).fetchall()
    db.close()
    return render_template('profile.html', user=user, goals=goals)



        try:
            amount = float(amount_str)
            if amount < 500 or amount > 50000:
                raise ValueError
        except ValueError:
            flash('Сумма должна быть от 500 до 50 000 ₽.', 'danger')
            return render_template('create_goal.html')

        try:
            days = int(days_str)
            if days < 1 or days > 7:
                raise ValueError
        except ValueError:
            flash('Срок должен быть от 1 до 7 дней.', 'danger')
            return render_template('create_goal.html')

        # Обработка фото
        photo_url = None
        if photo and photo.filename and allowed_file(photo.filename):
            try:
                photo_data = photo.read()
                compressed = compress_photo(photo_data)
                
                from datetime import datetime
                import uuid
                filename = f"{uuid.uuid4().hex}.jpg"
                filepath = os.path.join(app.config['UPLOAD_FOLDER'], filename)
                with open(filepath, 'wb') as f:
                    f.write(compressed)
                photo_url = f"/uploads/goals/{filename}"
            except Exception as e:
                flash(f'Ошибка при обработке фото: {str(e)}', 'warning')

        # Определяем тип
        if amount <= 5000 and days == 1:
            goal_type = 'super_blitz'
        else:
            goal_type = 'blitz'

        # Сохраняем цель
        db = get_db()
        from datetime import datetime, timedelta
        ends_at = datetime.now() + timedelta(days=days)
        
        db.execute(
            """INSERT INTO goals (user_id, type, title, description, amount_goal, amount_collected, ends_at, status, photo_url, moderation_status)
               VALUES (?, ?, ?, ?, ?, 0, ?, 'active', ?, 'approved')""",
            (user['id'], goal_type, title, description, amount, ends_at.isoformat(), photo_url)
        )
        db.commit()
        goal_id = db.execute("SELECT last_insert_rowid()").fetchone()[0]
        db.close()

        flash('Цель создана! Теперь ей можно поделиться.', 'success')
        return redirect(url_for('goal_page', goal_id=goal_id))

    return render_template('create_goal.html')

# --- Страница цели ---
@app.route('/goal/<int:goal_id>')
def goal_page(goal_id):
    db = get_db()
    goal = db.execute("SELECT * FROM goals WHERE id = ?", (goal_id,)).fetchone()
    if not goal:
        db.close()
        flash('Цель не найдена.', 'danger')
        return redirect(url_for('goals_list'))
    
    author = db.execute("SELECT phone FROM users WHERE id = ?", (goal['user_id'],)).fetchone()
    db.close()
    
    # Прогресс
    progress = int((goal['amount_collected'] / goal['amount_goal']) * 100) if goal['amount_goal'] > 0 else 0
    
    return render_template('goal.html', goal=goal, author=author, progress=progress)

# --- Лента целей ---
@app.route('/goals')
def goals_list():
    db = get_db()
    goals = db.execute(
        "SELECT * FROM goals WHERE status = 'active' ORDER BY created_at DESC LIMIT 50"
    ).fetchall()
    db.close()
    return render_template('goals.html', goals=goals)


# --- Выбор типа цели ---
@app.route('/goals/choose')
@login_required
def choose_goal_type():
    return render_template('choose_goal_type.html')

# --- Создание цели ---
@app.route('/goals/new/<goal_type>', methods=['GET', 'POST'])
@login_required
def create_goal(goal_type):
    if goal_type not in ('super_blitz', 'blitz', 'serious'):
        flash('Неверный тип цели.', 'danger')
        return redirect(url_for('choose_goal_type'))
    
    if goal_type == 'serious':
        flash('Серьёзные сборы — скоро.', 'info')
        return redirect(url_for('choose_goal_type'))

    if request.method == 'POST':
        user = get_current_user()
        title = request.form.get('title', '').strip()
        description = request.form.get('description', '').strip()
        amount_str = request.form.get('amount', '').strip()
        days_str = request.form.get('days', '').strip()
        photo = request.files.get('photo')

        if not title or len(title) > 140:
            flash('Название обязательно и не должно превышать 140 символов.', 'danger')
            return render_template('create_goal.html', goal_type=goal_type)

        try:
            amount = float(amount_str)
            if goal_type == 'super_blitz':
                if amount < 100 or amount > 5000:
                    raise ValueError
            else:
                if amount < 500 or amount > 50000:
                    raise ValueError
        except ValueError:
            if goal_type == 'super_blitz':
                flash('Сумма должна быть от 100 до 5 000 ₽.', 'danger')
            else:
                flash('Сумма должна быть от 500 до 50 000 ₽.', 'danger')
            return render_template('create_goal.html', goal_type=goal_type)

        try:
            days = int(days_str)
            if goal_type == 'super_blitz':
                if days != 1:
                    raise ValueError
            else:
                if days < 1 or days > 7:
                    raise ValueError
        except ValueError:
            if goal_type == 'super_blitz':
                flash('Супер-блиц — только на 1 день.', 'danger')
            else:
                flash('Срок должен быть от 1 до 7 дней.', 'danger')
            return render_template('create_goal.html', goal_type=goal_type)

        photo_url = None
        if photo and photo.filename and allowed_file(photo.filename):
            try:
                photo_data = photo.read()
                compressed = compress_photo(photo_data)
                import uuid
                filename = f"{uuid.uuid4().hex}.jpg"
                filepath = os.path.join(app.config['UPLOAD_FOLDER'], filename)
                with open(filepath, 'wb') as f:
                    f.write(compressed)
                photo_url = f"/uploads/goals/{filename}"
            except Exception as e:
                flash(f'Ошибка при обработке фото: {str(e)}', 'warning')

        db = get_db()
        from datetime import datetime, timedelta
        ends_at = datetime.now() + timedelta(days=days)

        db.execute(
            "INSERT INTO goals (user_id, type, title, description, amount_goal, amount_collected, ends_at, status, photo_url, moderation_status) VALUES (?, ?, ?, ?, ?, 0, ?, 'active', ?, 'approved')",
            (user['id'], goal_type, title, description, amount, ends_at.isoformat(), photo_url)
        )
        db.commit()
        goal_id = db.execute("SELECT last_insert_rowid()").fetchone()[0]
        db.close()

        flash('Цель создана!', 'success')
        return redirect(url_for('goal_page', goal_id=goal_id))

    return render_template('create_goal.html', goal_type=goal_type)

# --- Админка ---
@app.route('/admin')
def admin_panel():
    return '<h2>Админка — скоро</h2>'

# --- Health check ---
@app.route('/health')
def health():
    return 'OK'

# --- Статические файлы (фото) ---
@app.route('/uploads/goals/<filename>')
def uploaded_file(filename):
    from flask import send_from_directory
    return send_from_directory(app.config['UPLOAD_FOLDER'], filename)

# --- Запуск ---
if __name__ == '__main__':
    port = int(os.environ.get('PORT', 5000))
    app.run(host='0.0.0.0', port=port, debug=False)
