import os, re, time, uuid
from datetime import datetime, timedelta
from flask import Flask, render_template, request, redirect, url_for, session, flash, send_from_directory
from werkzeug.security import generate_password_hash, check_password_hash
from werkzeug.utils import secure_filename
from models import init_db, get_db
from trust_engine import add_trust_score, get_trust_perks, can_create_goal
from functools import wraps

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DATABASE = os.path.join(BASE_DIR, 'data', 'pomogay.db')
UPLOAD_FOLDER = os.path.join(BASE_DIR, 'uploads', 'goals')
ALLOWED_EXTENSIONS = {'png', 'jpg', 'jpeg', 'webp', 'gif'}
MAX_FILE_SIZE = 50 * 1024 * 1024
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
        try:
            user = db.execute("SELECT * FROM users WHERE id = ?", (session['user_id'],)).fetchone()
            return user
        except:
            return None
        finally:
            db.close()
    return None

def login_required(f):
    @wraps(f)
    def wrap(*args, **kwargs):
        if 'user_id' not in session:
            flash('Сначала войдите в аккаунт.', 'warning')
            return redirect(url_for('login'))
        return f(*args, **kwargs)
    return wrap

def admin_required(f):
    @wraps(f)
    def wrap(*args, **kwargs):
        user = get_current_user()
        if not user or user['phone'] != '79885260358':
            flash('Доступ запрещён.', 'danger')
            return redirect(url_for('index'))
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
    try:
        from PIL import Image
        import io
        img = Image.open(io.BytesIO(data))
        img = img.convert('RGB')
        max_size = (800, 800)
        img.thumbnail(max_size, Image.LANCZOS)
        output = io.BytesIO()
        img.save(output, format='JPEG', quality=75, optimize=True)
        compressed = output.getvalue()
        if len(compressed) < len(data):
            return compressed
    except ImportError:
        pass
    except Exception:
        pass
    return data

@app.route('/')
def index():
    user = get_current_user()
    db = get_db()
    goals = db.execute(
        "SELECT g.*, u.name as author_name, u.xp_level as author_level FROM goals g JOIN users u ON g.user_id = u.id WHERE g.status = 'active' ORDER BY g.created_at DESC LIMIT 10"
    ).fetchall()
    months = ['января','февраля','марта','апреля','мая','июня','июля','августа','сентября','октября','ноября','декабря']
    goals_list = []
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
        goals_list.append(g)
    db.close()
    return render_template('index.html', goals=goals_list)

@app.route('/register', methods=['GET','POST'])
def register():
    if request.method == 'POST':
        # Honeypot-проверка
        if request.form.get('honeypot', '').strip():
            flash('Регистрация отклонена.', 'danger')
            return render_template('register.html')
        # Тайминг-проверка (форма должна быть открыта >3 сек)
        try:
            form_time = float(request.form.get('form_time', '0'))
            if time.time() - form_time < 3:
                flash('Пожалуйста, не спешите.', 'danger')
                return render_template('register.html')
        except (ValueError, TypeError):
            pass
        
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
        db.execute("INSERT INTO users (phone, password_hash, name) VALUES (?,?,?)", (clean, generate_password_hash(pw), name if name else None))
        db.commit()
        flash('Регистрация успешна', 'success')
        # Автоматически входим
        user = db.execute("SELECT id FROM users WHERE phone = ?", (clean,)).fetchone()
        if user:
            session['user_id'] = user['id']
            # trust_score = 50 из DEFAULT, ставим правильный уровень
            db.execute("UPDATE users SET trust_level = 'member' WHERE id = ?", (user['id'],))
            db.commit()
        db.close()
        return redirect(url_for('goals_list'))
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
            return redirect(url_for('goals_list'))
        flash('Неверный номер или пароль.', 'danger')
    return render_template('login.html')

@app.route('/logout', methods=['GET', 'POST'])
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
    
    # Статистика: донаты МНЕ (в мои цели)
    received_total = db.execute(
        "SELECT COUNT(*) FROM donations WHERE goal_id IN (SELECT id FROM goals WHERE user_id = ?) AND donor_id IS NOT NULL",
        (user['id'],)
    ).fetchone()[0]
    received_approved = db.execute(
        "SELECT COUNT(*) FROM donations WHERE goal_id IN (SELECT id FROM goals WHERE user_id = ?) AND donor_id IS NOT NULL AND status IN ('recipient_confirmed','completed')",
        (user['id'],)
    ).fetchone()[0]

    # Статистика: МОИ донаты (я — донатор)
    sent_total = db.execute(
        "SELECT COUNT(*) FROM donations WHERE donor_id = ?",
        (user['id'],)
    ).fetchone()[0]
    sent_approved = db.execute(
        "SELECT COUNT(*) FROM donations WHERE donor_id = ? AND status IN ('recipient_confirmed','completed')",
        (user['id'],)
    ).fetchone()[0]
    
    # Список игноров: донаты в статусе donor_confirmed старше 24 часов для целей пользователя
    ignored = db.execute(
        "SELECT d.amount_reported, d.donor_confirmed_at, u.phone as donor_phone FROM donations d LEFT JOIN users u ON d.donor_id = u.id WHERE d.goal_id IN (SELECT id FROM goals WHERE user_id = ?) AND d.status = 'donor_confirmed' AND datetime(d.donor_confirmed_at) <= datetime('now', '-24 hours') ORDER BY d.donor_confirmed_at DESC LIMIT 10",
        (user['id'],)
    ).fetchall()
    
    # Форматируем даты для игноров
    months = ['января','февраля','марта','апреля','мая','июня','июля','августа','сентября','октября','ноября','декабря']
    ignored_list = []
    for ig in ignored:
        ig = dict(ig)
        try:
            parts = ig['donor_confirmed_at'][:10].split('-')
            ig['date_formatted'] = f"{int(parts[2])} {months[int(parts[1])-1]} {parts[0]}"
        except:
            ig['date_formatted'] = ig['donor_confirmed_at'][:10]
        ignored_list.append(ig)
    
    # Отложенные — лайкнутые цели
    liked_goals = db.execute("""
        SELECT g.*, u.name as author_name, u.xp_level as author_level
        FROM likes l
        JOIN goals g ON l.goal_id = g.id
        JOIN users u ON g.user_id = u.id
        WHERE l.user_id = ?
        ORDER BY l.created_at DESC
    """, (user['id'],)).fetchall()
    
    db.close()
    return render_template('profile.html',
        user=user,
        goals=goals,
        received_total=received_total,
        received_approved=received_approved,
        sent_total=sent_total,
        sent_approved=sent_approved,
        ignored_donations=ignored_list,
        liked_goals=liked_goals)

@app.route('/profile/received')
@login_required
def profile_received():
    """Донаты в мои цели."""
    user = get_current_user()
    db = get_db()
    donations = db.execute("""
        SELECT d.*, 
               CASE WHEN d.is_anonymous = 1 OR d.donor_id IS NULL THEN NULL 
                    ELSE COALESCE(u.name, '+' || u.phone) 
               END as donor_name,
               g.title as goal_title,
               g.id as goal_id
        FROM donations d
        JOIN goals g ON d.goal_id = g.id
        LEFT JOIN users u ON d.donor_id = u.id
        WHERE g.user_id = ?
        ORDER BY d.donor_confirmed_at DESC
    """, (user['id'],)).fetchall()
    total = len(donations)
    approved = sum(1 for d in donations if d['status'] in ('recipient_confirmed', 'completed'))
    db.close()
    return render_template('profile_donations.html',
        mode='received',
        donations=donations,
        total=total,
        approved=approved,
        user=user)


@app.route('/profile/sent')
@login_required
def profile_sent():
    """Мои донаты — кому я помог."""
    user = get_current_user()
    db = get_db()
    donations = db.execute("""
        SELECT d.*, 
               g.title as goal_title,
               g.id as goal_id,
               COALESCE(u.name, '+' || u.phone) as recipient_name
        FROM donations d
        JOIN goals g ON d.goal_id = g.id
        JOIN users u ON g.user_id = u.id
        WHERE d.donor_id = ?
        ORDER BY d.donor_confirmed_at DESC
    """, (user['id'],)).fetchall()
    total = len(donations)
    approved = sum(1 for d in donations if d['status'] in ('recipient_confirmed', 'completed'))
    db.close()
    return render_template('profile_donations.html',
        mode='sent',
        donations=donations,
        total=total,
        approved=approved,
        user=user)


@app.route('/goals/choose')
@login_required
def choose_goal_type():
    user = get_current_user()
    return render_template('choose_goal_type.html', user=user)

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
        photos_files = request.files.getlist('photos')
        
        # Content Guard
        from content_guard import check as cg_check, is_filter_enabled
        db_check = get_db()
        if is_filter_enabled(db_check):
            db_check.close()
            r_title = cg_check(title, 'goal_title')
            if r_title['status'] == 'blocked':
                return render_template('create_goal.html', goal_type=goal_type,
                    error_message=r_title['message'],
                    error_position=r_title['position'],
                    error_field='title',
                    title=title, description=desc,
                    amount=amt_str, days=days_str)
            r_desc = cg_check(desc, 'goal_description')
            if r_desc['status'] == 'blocked':
                return render_template('create_goal.html', goal_type=goal_type,
                    error_message=r_desc['message'],
                    error_position=r_desc['position'],
                    error_field='description',
                    error_text=desc,
                    title=title, description=desc,
                    amount=amt_str, days=days_str)
            if r_title['status'] == 'flagged' or r_desc['status'] == 'flagged':
                flash('Цель отправлена на модерацию. Мы проверим её вручную.', 'warning')
        else:
            db_check.close()
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
        import json as _json
        photos_urls = []
        MAX_PHOTOS = 20
        for pf in photos_files[:MAX_PHOTOS]:
            if pf and pf.filename and allowed_file(pf.filename):
                try:
                    data = pf.read()
                    compressed = compress_photo(data)
                    fname = f"{uuid.uuid4().hex}.jpg"
                    fpath = os.path.join(app.config['UPLOAD_FOLDER'], fname)
                    with open(fpath, 'wb') as f:
                        f.write(compressed)
                    photos_urls.append(f"/uploads/goals/{fname}")
                except Exception as e:
                    flash(f'Ошибка фото: {e}', 'warning')
        photo_url = photos_urls[0] if photos_urls else None
        photos_json = _json.dumps(photos_urls) if photos_urls else None
        db = get_db()
        # Проверка слотов: тип цели → слот → открыт ли
        if not can_create_goal(user, db, goal_type=goal_type):
            from trust_engine import can_create_goal_reason
            reason = can_create_goal_reason(user)
            db.close()
            if reason == 'need_profile':
                flash('Блицы закрыты. Заполните анкету.', 'warning')
                return redirect(url_for('profile'))
            elif reason == 'need_video':
                flash('Серьёзные сборы закрыты. Нужна видео-верификация.', 'warning')
                return redirect(url_for('profile'))
            else:
                flash('У вас уже есть активная цель в этой категории.', 'warning')
                return redirect(url_for('goals_list'))
        ends = (datetime.now() + timedelta(days=days)).isoformat()
        if not user:
            flash('Ошибка: пользователь не найден. Войдите заново.', 'danger')
            return redirect(url_for('login'))
        db.execute(
            "INSERT INTO goals (user_id, type, title, description, amount_goal, amount_collected, ends_at, status, photo_url, photos, moderation_status) VALUES (?,?,?,?,?,0,?,'active',?,?,'approved')",
            (user['id'], goal_type, title, desc, amt, ends, photo_url, photos_json)
        )
        db.commit()
        gid = db.execute("SELECT last_insert_rowid()").fetchone()[0]
        db.close()
        flash('Цель создана', 'success')
    


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
    # Парсим photos — JSON-массив путей
    import json as _json
    photos_list = []
    if goal['photos']:
        try:
            photos_list = _json.loads(goal['photos'])
        except Exception:
            photos_list = []
    if not photos_list and goal['photo_url']:
        photos_list = [goal['photo_url']]
    author = db.execute("SELECT phone FROM users WHERE id = ?", (goal['user_id'],)).fetchone()
    donations = db.execute(
        """SELECT d.*, 
           CASE WHEN d.is_anonymous = 1 OR d.donor_id IS NULL THEN NULL 
                ELSE COALESCE(u.name, '+' || u.phone) 
           END as donor_name
        FROM donations d 
        LEFT JOIN users u ON d.donor_id = u.id 
        WHERE d.goal_id = ? 
        ORDER BY d.donor_confirmed_at DESC""", 
        (goal_id,)
    ).fetchall()
    donor_count = db.execute("SELECT COUNT(DISTINCT donor_id) FROM donations WHERE goal_id = ? AND status IN ('recipient_confirmed','completed')", (goal_id,)).fetchone()[0]
    last_donation = db.execute("SELECT MAX(donor_confirmed_at) FROM donations WHERE goal_id = ?", (goal_id,)).fetchone()[0]
    db.close()
    pct = int((goal['amount_collected'] / goal['amount_goal']) * 100) if goal['amount_goal'] > 0 else 0
    # Форматируем дату
    months = ['января','февраля','марта','апреля','мая','июня','июля','августа','сентября','октября','ноября','декабря']
    try:
        parts = goal['ends_at'][:10].split('-')
        ends_at_formatted = f"{int(parts[2])} {months[int(parts[1])-1]} {parts[0]}"
    except:
        ends_at_formatted = goal['ends_at'][:10]
    user = get_current_user()
    is_author = (user and user['id'] == goal['user_id'])
    
    # Лайкнут ли цель текущим пользователем
    is_liked = False
    if user:
        ldb = get_db()
        is_liked = ldb.execute(
            "SELECT id FROM likes WHERE user_id = ? AND goal_id = ?",
            (user['id'], goal_id)
        ).fetchone() is not None
        ldb.close()
    
    # Проверяем, жаловался ли уже пользователь
    already_reported = False
    if user:
        rdb = get_db()
        already_reported = rdb.execute(
            'SELECT id FROM reports WHERE goal_id = ? AND reporter_id = ?',
            (goal_id, user['id'])
        ).fetchone() is not None
        rdb.close()
    
    return render_template('goal.html', goal=goal, author=author, progress=pct, donations=donations, is_author=is_author, donor_count=donor_count, last_donation=last_donation, ends_at_formatted=ends_at_formatted, already_reported=already_reported, user=user, photos_list=photos_list, is_liked=is_liked)

@app.route('/report/<int:goal_id>', methods=['POST'])
@login_required
def report_goal(goal_id):
    user = get_current_user()
    reason = request.form.get('reason', '').strip()
    if not reason:
        flash('Укажите причину жалобы.', 'warning')
        return redirect(url_for('goal_page', goal_id=goal_id))
    
    db = get_db()
    goal = db.execute('SELECT * FROM goals WHERE id = ?', (goal_id,)).fetchone()
    if not goal:
        db.close()
        flash('Цель не найдена.', 'danger')
        return redirect(url_for('goals_list'))
    if goal['user_id'] == user['id']:
        db.close()
        flash('Нельзя жаловаться на свою цель.', 'warning')
        return redirect(url_for('goal_page', goal_id=goal_id))
    
    existing = db.execute(
        'SELECT id FROM reports WHERE goal_id = ? AND reporter_id = ?',
        (goal_id, user['id'])
    ).fetchone()
    if existing:
        db.close()
        flash('Вы уже жаловались на эту цель.', 'info')
        return redirect(url_for('goal_page', goal_id=goal_id))
    
    db.execute(
        'INSERT INTO reports (goal_id, reporter_id, reason) VALUES (?, ?, ?)',
        (goal_id, user['id'], reason)
    )
    db.commit()
    
    # Считаем жалобы
    report_count = db.execute(
        'SELECT COUNT(*) FROM reports WHERE goal_id = ?',
        (goal_id,)
    ).fetchone()[0]
    
    if report_count >= 3:
        db.execute(
            "UPDATE goals SET status = 'hidden', moderation_status = 'reported' WHERE id = ?",
            (goal_id,)
        )
        db.execute(
            "INSERT INTO notifications_log (user_id, type, goal_id, channel) VALUES (?, 'goal_reported_hidden', ?, 'admin')",
            (goal['user_id'], goal_id)
        )
        db.commit()
        # Trust Score: -20 автору цели, +3 каждому жалобщику
        add_trust_score(goal['user_id'], 'goal_hidden_reports', db)
        reporters = db.execute('SELECT DISTINCT reporter_id FROM reports WHERE goal_id = ?', (goal_id,)).fetchall()
        for r in reporters:
            add_trust_score(r['reporter_id'], 'report_valid', db)
        db.close()
        flash('Цель скрыта после нескольких жалоб. Администратор проверит её.', 'info')
        return redirect(url_for('goals_list'))
    
    db.close()
    flash('Жалоба отправлена. Спасибо за бдительность.', 'success')
    return redirect(url_for('goal_page', goal_id=goal_id))

@app.route('/goals')
def goals_list():
    db = get_db()
    months = ['января','февраля','марта','апреля','мая','июня','июля','августа','сентября','октября','ноября','декабря']
    goals = []
    for g in db.execute(
        "SELECT g.*, u.name as author_name, u.xp_level as author_level FROM goals g JOIN users u ON g.user_id = u.id WHERE g.status = 'active' ORDER BY g.created_at DESC LIMIT 50"
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

    db.close()
    user = get_current_user()
    return render_template('goals.html', goals=goals, user=user)


@app.route('/manifest.json')
def manifest():
    from flask import send_from_directory
    return send_from_directory(os.path.join(BASE_DIR, 'static'), 'manifest.json')

@app.route('/sw.js')
def service_worker():
    from flask import send_from_directory
    return send_from_directory(os.path.join(BASE_DIR, 'static'), 'sw.js')

@app.route('/admin')
@admin_required
def admin_panel():
    return '<h2>Админка — скоро</h2>'


@app.route('/clear')
@admin_required
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

        # XP и сумма помощи + авто-подтверждение seed3
        goal_data = db.execute("SELECT user_id FROM goals WHERE id = ?", (goal_id,)).fetchone()
        
        # XP (только если не самодонат)
        if goal_data and donor_id != goal_data['user_id']:
            add_xp(donor_id, 'donate')
            add_xp(donor_id, 'donate_new')
            db.execute("UPDATE users SET total_helped_amount = COALESCE(total_helped_amount, 0) + ? WHERE id = ?", (amount, donor_id))
            db.commit()

        # Автоподтверждение для seed3
        if goal_data:
            recipient = db.execute("SELECT phone FROM users WHERE id = ?", (goal_data['user_id'],)).fetchone()
            if recipient and recipient['phone'] == '7888888888':
                db.execute("UPDATE donations SET status = 'recipient_confirmed', recipient_confirmed_at = datetime('now') WHERE id = ?", (donation_id,))
                db.execute("UPDATE goals SET amount_collected = amount_collected + ? WHERE id = ?", (amount, goal_id))
                db.commit()
                flash('Подтверждено', 'success')
                return redirect(url_for('goal_page', goal_id=goal_id))

        flash('Перевод ожидает подтверждения', 'success')
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
        flash('Уже обработан', 'info')
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
    add_xp(user['id'], 'confirm')
    db.commit()
    # Trust Score: получатель +3 за подтверждение, донатор +3 за подтверждённый донат
    add_trust_score(user['id'], 'donation_recipient_confirmed', db)
    if donation['donor_id']:
        add_trust_score(donation['donor_id'], 'donation_recipient_confirmed', db)
    db.close()
    flash('Подтверждено', 'success')
    
    return redirect(url_for('goal_page', goal_id=donation['goal_id']))


@app.route('/api/guard-check', methods=['POST'])
def api_guard_check():
    """AJAX-проверка текста через Content Guard."""
    from flask import jsonify
    from content_guard import check as cg_check
    data = request.get_json(silent=True) or {}
    text = (data.get('text') or '').strip()
    context = data.get('context', 'goal_description')
    if not text:
        return jsonify({'status': 'passed'})
    result = cg_check(text, context)
    return jsonify({
        'status': result.get('status', 'passed'),
        'category': result.get('category'),
        'found': result.get('found'),
        'position': result.get('position'),
        'message': result.get('message'),
    })


@app.route('/api/goal/<int:goal_id>/photos/update', methods=['POST'])
@login_required
def api_photos_update(goal_id):
    """Сохранение изменений в галерее: порядок, повороты, удаления."""
    from flask import jsonify
    import json as _json
    from PIL import Image
    import io

    user = get_current_user()
    db = get_db()
    goal = db.execute("SELECT * FROM goals WHERE id = ?", (goal_id,)).fetchone()
    if not goal:
        db.close()
        return jsonify({'error': 'Цель не найдена'}), 404
    if goal['user_id'] != user['id']:
        db.close()
        return jsonify({'error': 'Только автор может редактировать'}), 403

    data = request.get_json(silent=True) or {}
    new_order = data.get('order', [])       # [path1, path2, ...]
    angles = data.get('angles', {})         # { path: angle }
    deleted = data.get('deleted', [])       # [path1, path2]

    # Валидация: все пути должны быть из текущей цели
    current_photos = _json.loads(goal['photos']) if goal['photos'] else []
    if not current_photos and goal['photo_url']:
        current_photos = [goal['photo_url']]

    def is_own(path):
        return path in current_photos

    new_order = [p for p in new_order if is_own(p)]
    deleted = [p for p in deleted if is_own(p)]
    angles = {k: v for k, v in angles.items() if is_own(k)}

    # Удаляем файлы
    upload_folder = app.config['UPLOAD_FOLDER']
    for path in deleted:
        filename = path.split('/')[-1]
        fpath = os.path.join(upload_folder, filename)
        if os.path.exists(fpath):
            try:
                os.remove(fpath)
            except Exception:
                pass

    # Поворачиваем файлы
    for path, angle in angles.items():
        if angle % 360 == 0:
            continue
        filename = path.split('/')[-1]
        fpath = os.path.join(upload_folder, filename)
        if not os.path.exists(fpath):
            continue
        try:
            with open(fpath, 'rb') as f:
                img = Image.open(io.BytesIO(f.read()))
            img = img.convert('RGB')
            rotated = img.rotate(-angle, expand=True)  # минус, т.к. Pillow поворачивает против часовой
            output = io.BytesIO()
            rotated.save(output, format='JPEG', quality=75, optimize=True)
            with open(fpath, 'wb') as f:
                f.write(output.getvalue())
        except Exception as e:
            print(f'Rotate error: {e}')

    # Финальный список — исключаем удалённые, сохраняем порядок
    final = [p for p in new_order if p not in deleted]

    photo_url = final[0] if final else None
    photos_json = _json.dumps(final) if final else None

    db.execute(
        "UPDATE goals SET photos = ?, photo_url = ? WHERE id = ?",
        (photos_json, photo_url, goal_id)
    )
    db.commit()
    db.close()
    return jsonify({'ok': True, 'photos': final})


@app.route('/api/goal/<int:goal_id>/photos/upload', methods=['POST'])
@login_required
def api_photos_upload(goal_id):
    """Добавление новых фото в цель."""
    from flask import jsonify
    import json as _json

    user = get_current_user()
    db = get_db()
    goal = db.execute("SELECT * FROM goals WHERE id = ?", (goal_id,)).fetchone()
    if not goal:
        db.close()
        return jsonify({'error': 'Цель не найдена'}), 404
    if goal['user_id'] != user['id']:
        db.close()
        return jsonify({'error': 'Только автор может редактировать'}), 403

    current_photos = _json.loads(goal['photos']) if goal['photos'] else []
    if not current_photos and goal['photo_url']:
        current_photos = [goal['photo_url']]

    MAX_PHOTOS = 20
    files = request.files.getlist('photos')
    added = []
    for pf in files:
        if len(current_photos) + len(added) >= MAX_PHOTOS:
            break
        if pf and pf.filename and allowed_file(pf.filename):
            try:
                data = pf.read()
                compressed = compress_photo(data)
                fname = f"{uuid.uuid4().hex}.jpg"
                fpath = os.path.join(app.config['UPLOAD_FOLDER'], fname)
                with open(fpath, 'wb') as f:
                    f.write(compressed)
                added.append(f"/uploads/goals/{fname}")
            except Exception as e:
                print(f'Upload error: {e}')

    final = current_photos + added
    photo_url = final[0] if final else None
    photos_json = _json.dumps(final) if final else None

    db.execute(
        "UPDATE goals SET photos = ?, photo_url = ? WHERE id = ?",
        (photos_json, photo_url, goal_id)
    )
    db.commit()
    db.close()
    return jsonify({'ok': True, 'added': added, 'photos': final})


@app.route('/api/goal/<int:goal_id>/update-description', methods=['POST'])
@login_required
def api_update_description(goal_id):
    """Инлайн-редактирование описания цели."""
    from flask import jsonify
    from content_guard import check as cg_check

    user = get_current_user()
    db = get_db()
    goal = db.execute("SELECT * FROM goals WHERE id = ?", (goal_id,)).fetchone()
    if not goal:
        db.close()
        return jsonify({'error': 'Цель не найдена'}), 404
    if goal['user_id'] != user['id']:
        db.close()
        return jsonify({'error': 'Только автор может редактировать'}), 403

    data = request.get_json(silent=True) or {}
    new_desc = (data.get('description') or '').strip()

    if len(new_desc) > 1000:
        db.close()
        return jsonify({'error': 'Максимум 1000 символов'}), 400

    # Content Guard
    r = cg_check(new_desc, 'goal_description')
    if r['status'] == 'blocked':
        db.close()
        return jsonify({
            'error': r.get('message', 'Текст не прошёл проверку'),
            'position': r.get('position'),
        }), 400

    db.execute("UPDATE goals SET description = ? WHERE id = ?", (new_desc if new_desc else None, goal_id))
    db.commit()
    db.close()
    return jsonify({'ok': True, 'description': new_desc})


@app.route('/demo-crane')
def demo_crane():
    return render_template('demo_crane.html')


@app.route('/api/goal/<int:goal_id>/like', methods=['POST'])
@login_required
def api_goal_like(goal_id):
    from flask import jsonify
    user = get_current_user()
    db = get_db()
    goal = db.execute("SELECT id FROM goals WHERE id = ?", (goal_id,)).fetchone()
    if not goal:
        db.close()
        return jsonify({'error': 'Цель не найдена'}), 404
    try:
        db.execute("INSERT INTO likes (user_id, goal_id) VALUES (?, ?)", (user['id'], goal_id))
        db.commit()
    except Exception:
        pass
    db.close()
    return jsonify({'ok': True, 'liked': True})


@app.route('/api/goal/<int:goal_id>/unlike', methods=['POST'])
@login_required
def api_goal_unlike(goal_id):
    from flask import jsonify
    user = get_current_user()
    db = get_db()
    db.execute("DELETE FROM likes WHERE user_id = ? AND goal_id = ?", (user['id'], goal_id))
    db.commit()
    db.close()
    return jsonify({'ok': True, 'liked': False})


@app.route('/privacy')
def privacy():
    return render_template('privacy.html')


@app.route('/terms')
def terms():
    return render_template('terms.html')


@app.route('/consent')
def consent():
    return render_template('consent.html')


@app.route('/rules')
def rules():
    return render_template('rules.html')


@app.route('/verification')
def verification():
    return render_template('verification.html')


@app.route('/abuse')
def abuse():
    return render_template('abuse.html')


@app.route('/api/regions')
def api_regions():
    """Список всех регионов."""
    from flask import jsonify
    db = get_db()
    regions = db.execute(
        "SELECT DISTINCT region_code, region_name FROM cities ORDER BY region_name"
    ).fetchall()
    db.close()
    return jsonify([{'code': r['region_code'], 'name': r['region_name']} for r in regions])


@app.route('/api/cities')
def api_cities():
    """Города по региону."""
    from flask import jsonify
    region_code = request.args.get('region', '').strip()
    if not region_code:
        return jsonify([])
    db = get_db()
    cities = db.execute(
        "SELECT city_name FROM cities WHERE region_code = ? ORDER BY city_name",
        (region_code,)
    ).fetchall()
    db.close()
    return jsonify([c['city_name'] for c in cities])


@app.route('/api/onboarding/step', methods=['POST'])
@login_required
def api_onboarding_step():
    """Сохранение одного шага онбординга."""
    from flask import jsonify
    user = get_current_user()
    data = request.get_json(silent=True) or {}
    step = data.get('step')

    db = get_db()

    if step == 1:
        name = (data.get('name') or '').strip()[:50]
        if name:
            db.execute("UPDATE users SET name = ?, onboarding_step = 1 WHERE id = ?", (name, user['id']))
    elif step == 2:
        region_code = (data.get('region_code') or '').strip()
        region_name = (data.get('region_name') or '').strip()
        city = (data.get('city') or '').strip()
        if region_code and city:
            db.execute(
                "UPDATE users SET region_code = ?, region_name = ?, city = ?, onboarding_step = 2 WHERE id = ?",
                (region_code, region_name, city, user['id'])
            )
    elif step == 3:
        birth_date = (data.get('birth_date') or '').strip()
        if birth_date:
            db.execute("UPDATE users SET birth_date = ?, onboarding_step = 3 WHERE id = ?", (birth_date, user['id']))
    elif step == 4:
        # Проверяем, всё ли заполнено
        u = db.execute("SELECT name, region_code, city, birth_date FROM users WHERE id = ?", (user['id'],)).fetchone()
        all_filled = u and u['name'] and u['region_code'] and u['city'] and u['birth_date']
        if all_filled:
            db.execute(
                "UPDATE users SET onboarding_passed = 1, onboarding_step = 4, profile_completed = 1 WHERE id = ?",
                (user['id'],)
            )
        else:
            # Онбординг пройден (модалки больше не показываем), но анкета не заполнена
            db.execute(
                "UPDATE users SET onboarding_passed = 1, onboarding_step = 4 WHERE id = ?",
                (user['id'],)
            )

    db.commit()
    db.close()
    return jsonify({'ok': True, 'step': step})


@app.route('/api/onboarding/state')
@login_required
def api_onboarding_state():
    """Текущее состояние онбординга пользователя."""
    from flask import jsonify
    user = get_current_user()
    return jsonify({
        'passed': user['onboarding_passed'] or 0,
        'step': user['onboarding_step'] or 0,
        'name': user['name'] or '',
        'city': user['city'] or '',
        'region_code': user['region_code'] or '',
        'region_name': user['region_name'] or '',
        'birth_date': user['birth_date'] or '',
    })


@app.route('/api/report-photo', methods=['POST'])
@login_required
def api_report_photo():
    """Жалоба на конкретное фото цели."""
    from flask import jsonify
    user = get_current_user()
    data = request.get_json(silent=True) or {}
    goal_id = data.get('goal_id')
    photo_index = data.get('photo_index')
    reason = (data.get('reason') or '').strip()

    if not goal_id or photo_index is None:
        return jsonify({'error': 'Неверные данные'}), 400
    if not reason:
        return jsonify({'error': 'Укажите причину'}), 400

    db = get_db()
    goal = db.execute("SELECT id, user_id FROM goals WHERE id = ?", (goal_id,)).fetchone()
    if not goal:
        db.close()
        return jsonify({'error': 'Цель не найдена'}), 404
    if goal['user_id'] == user['id']:
        db.close()
        return jsonify({'error': 'Нельзя жаловаться на свою цель'}), 403

    # Проверяем, не жаловался ли уже
    existing = db.execute(
        "SELECT id FROM reports WHERE goal_id = ? AND reporter_id = ? AND photo_index = ?",
        (goal_id, user['id'], photo_index)
    ).fetchone()
    if existing:
        db.close()
        return jsonify({'error': 'Вы уже жаловались на это фото'}), 400

    db.execute(
        "INSERT INTO reports (goal_id, reporter_id, reason, photo_index) VALUES (?, ?, ?, ?)",
        (goal_id, user['id'], reason, photo_index)
    )
    db.commit()
    db.close()
    return jsonify({'ok': True})


@app.route('/api/deferred/set', methods=['POST'])
@login_required
def api_deferred_set():
    """Установить напоминание о цели."""
    from flask import jsonify
    from datetime import datetime, timedelta
    user = get_current_user()
    data = request.get_json(silent=True) or {}
    goal_id = data.get('goal_id')
    frequency = data.get('frequency', 'once')  # once / daily / custom
    days = data.get('days')  # для custom

    if not goal_id:
        return jsonify({'error': 'Не указана цель'}), 400

    db = get_db()
    goal = db.execute("SELECT id FROM goals WHERE id = ?", (goal_id,)).fetchone()
    if not goal:
        db.close()
        return jsonify({'error': 'Цель не найдена'}), 404

    now = datetime.now()

    if frequency == 'once':
        remind_at = (now + timedelta(days=1)).isoformat()
    elif frequency == 'daily':
        remind_at = (now + timedelta(days=1)).isoformat()
    elif frequency == 'custom' and days:
        try:
            days = int(days)
            if days < 1:
                days = 1
            if days > 365:
                days = 365
            remind_at = (now + timedelta(days=days)).isoformat()
        except (ValueError, TypeError):
            db.close()
            return jsonify({'error': 'Неверное количество дней'}), 400
    else:
        db.close()
        return jsonify({'error': 'Неверный тип напоминания'}), 400

    # Удаляем старое напоминание для этой цели
    db.execute("DELETE FROM deferred_donations WHERE user_id = ? AND goal_id = ?", (user['id'], goal_id))

    # Создаём новое
    db.execute(
        "INSERT INTO deferred_donations (user_id, goal_id, remind_at, frequency, status) VALUES (?, ?, ?, ?, 'pending')",
        (user['id'], goal_id, remind_at, frequency)
    )
    db.commit()
    db.close()
    return jsonify({'ok': True, 'remind_at': remind_at, 'frequency': frequency})


@app.route('/api/deferred/cancel', methods=['POST'])
@login_required
def api_deferred_cancel():
    """Отменить напоминание."""
    from flask import jsonify
    user = get_current_user()
    data = request.get_json(silent=True) or {}
    goal_id = data.get('goal_id')

    if not goal_id:
        return jsonify({'error': 'Не указана цель'}), 400

    db = get_db()
    db.execute("DELETE FROM deferred_donations WHERE user_id = ? AND goal_id = ?", (user['id'], goal_id))
    db.commit()
    db.close()
    return jsonify({'ok': True})


@app.route('/profile/edit')
@login_required
def profile_edit():
    """Страница редактирования анкеты."""
    user = get_current_user()
    return render_template('profile_edit.html', user=user)


@app.route('/api/profile/save', methods=['POST'])
@login_required
def api_profile_save():
    """Сохранение анкеты."""
    from flask import jsonify
    user = get_current_user()
    data = request.get_json(silent=True) or {}

    name = (data.get('name') or '').strip()[:50]
    region_code = (data.get('region_code') or '').strip()
    region_name = (data.get('region_name') or '').strip()
    city = (data.get('city') or '').strip()
    birth_date = (data.get('birth_date') or '').strip()

    db = get_db()
    db.execute(
        "UPDATE users SET name = ?, region_code = ?, region_name = ?, city = ?, birth_date = ? WHERE id = ?",
        (name or None, region_code or None, region_name or None, city or None, birth_date or None, user['id'])
    )

    # Любое сохранение через /profile/edit — пользователь "прошёл" онбординг
    # (модалки больше не показываем)
    if name and region_code and city and birth_date:
        db.execute(
            "UPDATE users SET profile_completed = 1, onboarding_passed = 1, onboarding_step = 4 WHERE id = ?",
            (user['id'],)
        )
    else:
        db.execute(
            "UPDATE users SET onboarding_passed = 1, onboarding_step = 4 WHERE id = ?",
            (user['id'],)
        )

    db.commit()
    db.close()
    return jsonify({'ok': True})


@app.route('/api/identity/upload', methods=['POST'])
@login_required
def api_identity_upload():
    """Загрузка документа или селфи для верификации."""
    from flask import jsonify
    user = get_current_user()
    doc_type = request.form.get('type', '').strip()
    if doc_type not in ('passport', 'selfie'):
        return jsonify({'error': 'Неверный тип'}), 400

    file = request.files.get('file')
    if not file or not file.filename:
        return jsonify({'error': 'Файл не загружен'}), 400

    if not allowed_file(file.filename):
        return jsonify({'error': 'Неверный формат файла'}), 400

    # Папка uploads/identity
    identity_folder = os.path.join(BASE_DIR, 'uploads', 'identity')
    os.makedirs(identity_folder, exist_ok=True)

    try:
        data = file.read()
        compressed = compress_photo(data)
        fname = f"{user['id']}_{doc_type}.jpg"
        fpath = os.path.join(identity_folder, fname)
        with open(fpath, 'wb') as f:
            f.write(compressed)
    except Exception as e:
        return jsonify({'error': f'Ошибка сохранения: {e}'}), 500

    rel_path = f"uploads/identity/{fname}"
    db = get_db()
    if doc_type == 'passport':
        db.execute("UPDATE users SET identity_doc_path = ? WHERE id = ?", (rel_path, user['id']))
    else:
        db.execute("UPDATE users SET identity_selfie_path = ? WHERE id = ?", (rel_path, user['id']))

    # Проверяем, оба ли загружены
    u = db.execute("SELECT identity_doc_path, identity_selfie_path FROM users WHERE id = ?", (user['id'],)).fetchone()
    db.commit()
    db.close()

    return jsonify({'ok': True, 'path': '/' + rel_path})


@app.route('/uploads/identity/<filename>')
@login_required
def identity_file(filename):
    """Отдача файлов верификации — только владельцу."""
    user = get_current_user()
    if not filename.startswith(f"{user['id']}_"):
        from flask import abort
        abort(403)
    return send_from_directory(os.path.join(BASE_DIR, 'uploads', 'identity'), filename)


@app.route('/health')
def health():
    return 'OK'

@app.route('/uploads/goals/<filename>')
def uploaded_file(filename):
    return send_from_directory(app.config['UPLOAD_FOLDER'], filename)

app.jinja_env.globals['get_level_progress'] = get_level_progress
app.jinja_env.globals['get_level_name'] = get_level_name

# Фильтры для шаблонов
LEVEL_EMOJI = {
    'novice': '🌱',
    'member': '🌿',
    'reliable': '🌳',
    'pillar': '💎',
    'hero': '⭐',
    'legend': '👑'
}
LEVEL_NAMES = {
    'novice': 'Новичок',
    'member': 'Участник',
    'reliable': 'Надёжный',
    'pillar': 'Опора',
    'hero': 'Герой',
    'legend': 'Легенда'
}
MONTHS = ['января','февраля','марта','апреля','мая','июня','июля','августа','сентября','октября','ноября','декабря']

def level_emoji(level_key):
    return LEVEL_EMOJI.get(level_key, '🌱')

def level_name(level_key):
    return LEVEL_NAMES.get(level_key, 'Новичок')

def format_date(date_str):
    if not date_str:
        return ''
    try:
        parts = date_str[:10].split('-')
        return f"{int(parts[2])} {MONTHS[int(parts[1])-1]} {parts[0]}"
    except:
        return date_str[:10]

app.jinja_env.filters['level_emoji'] = level_emoji
app.jinja_env.filters['level_name'] = level_name
app.jinja_env.filters['format_date'] = format_date

# Trust Score helpers
from trust_engine import get_trust_level_name as _tln, get_trust_progress as _ttp, get_trust_segments as _tsg
app.jinja_env.filters['trust_name'] = _tln
app.jinja_env.globals['trust_progress'] = _ttp
app.jinja_env.globals['trust_segments'] = _tsg

# Lucide иконки
from lucide.jinja import lucide as lucide_icon
app.jinja_env.globals['lucide'] = lucide_icon

if __name__ == '__main__':
    port = int(os.environ.get('PORT', 5000))
    app.run(host='0.0.0.0', port=port, debug=False)
