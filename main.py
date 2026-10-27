import os, re, time, uuid, math
import sqlite3
from datetime import datetime, timedelta
from flask import Flask, render_template, request, redirect, url_for, session, flash, send_from_directory, abort, jsonify
from werkzeug.security import generate_password_hash, check_password_hash
from models import init_db, get_db, close_db
from trust_engine import add_trust_score, get_trust_perks, can_create_goal
from functools import wraps
import logging
import sys
import secrets as _secrets

# Логи идут в stderr → journalctl через Gunicorn.
# force=True перезаписывает root handler (если Gunicorn что-то настроил).
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(name)s: %(message)s',
    stream=sys.stderr,
    force=True
)
logger = logging.getLogger('pomogay')

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DATABASE = os.path.join(BASE_DIR, 'data', 'pomogay.db')
UPLOAD_FOLDER = os.path.join(BASE_DIR, 'uploads', 'goals')
ALLOWED_EXTENSIONS = {'png', 'jpg', 'jpeg', 'webp', 'gif'}
MAX_FILE_SIZE = 50 * 1024 * 1024

app = Flask(__name__)
app.teardown_appcontext(close_db)
app.secret_key = os.environ.get('SECRET_KEY')
app.config['PERMANENT_SESSION_LIFETIME'] = 60 * 60 * 24 * 31  # 31 день
if not app.secret_key:
    raise RuntimeError('SECRET_KEY environment variable is not set. Server cannot start without it.')
app.config['UPLOAD_FOLDER'] = UPLOAD_FOLDER
app.config['MAX_CONTENT_LENGTH'] = MAX_FILE_SIZE
os.makedirs(UPLOAD_FOLDER, exist_ok=True)
init_db()



# Seed-пользователи создаются ТОЛЬКО при SEED_USERS=1 (для разработки)
if os.environ.get('SEED_USERS') == '1':
    db = get_db()
    seed = db.execute("SELECT id FROM users WHERE phone = '79885260358'").fetchone()
    if not seed:
        db.execute("INSERT INTO users (phone, password_hash) VALUES ('79885260358', ?)", (generate_password_hash('123456'),))
        db.commit()
        logger.info('Seed 1 создан: 79885260358')

    seed2 = db.execute("SELECT id FROM users WHERE phone = '7999999999'").fetchone()
    if not seed2:
        db.execute("INSERT INTO users (phone, password_hash) VALUES ('7999999999', ?)", (generate_password_hash('123456'),))
        db.commit()
        logger.info('Seed 2 создан: 7999999999')

    # Seed 3 с готовой целью
    seed3 = db.execute("SELECT id FROM users WHERE phone = '79888888888'").fetchone()
    if not seed3:
        db.execute("INSERT INTO users (phone, password_hash) VALUES ('79888888888', ?)", (generate_password_hash('123456'),))
        db.commit()
        seed3_id = db.execute("SELECT last_insert_rowid()").fetchone()[0]
        ends = (datetime.now() + timedelta(days=7)).isoformat()
        db.execute(
            "INSERT INTO goals (user_id, type, title, description, amount_goal, amount_collected, ends_at, status, moderation_status) VALUES (?, 'blitz', 'на ноутбук', 'для работы', 25000, 0, ?, 'active', 'approved')",
            (seed3_id, ends)
        )
        db.commit()
        logger.info('Seed 3 создан: 79888888888 с целью')
    db.close()

def get_current_user():
    if 'user_id' in session:
        db = get_db()
        try:
            user = db.execute("SELECT * FROM users WHERE id = ?", (session['user_id'],)).fetchone()
            if user and user['is_banned']:
                return None
            return user
        except sqlite3.Error as e:
            logger.error(f'[get_current_user] Ошибка БД: {e}')
            return None
    return None

def log_guard(result, text, context, user_action):
    """Логирует срабатывание Content Guard в БД."""
    if not result or result.get('status') == 'passed':
        return
    try:
        db = get_db()
        user = get_current_user()
        db.execute("""
            INSERT INTO content_guard_log 
            (user_id, ip_address, context, original_text, found_word, category, status, user_action, position_start, position_end)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            user['id'] if user else None,
            request.remote_addr,
            context,
            text[:2000],
            result.get('found'),
            result.get('category'),
            result.get('status'),
            user_action,
            result.get('position', [None, None])[0] if result.get('position') else None,
            result.get('position', [None, None])[1] if result.get('position') else None,
        ))
        db.commit()
    except Exception as e:
        logger.error(f'[content_guard_log] Ошибка: {e}')


def login_required(f):
    @wraps(f)
    def wrap(*args, **kwargs):
        user = get_current_user()
        if not user:
            session.clear()
            flash('Сначала войдите в аккаунт.', 'warning')
            return redirect(url_for('login'))
        return f(*args, **kwargs)
    return wrap

def admin_required(f):
    """Доступ только для is_admin=1 + CSRF на POST. Для остальных — 404."""
    @wraps(f)
    def wrap(*args, **kwargs):
        user = get_current_user()
        if not user or not user['is_admin']:
            abort(404)
        if request.method == 'POST' and not check_csrf():
            abort(403)
        return f(*args, **kwargs)
    return wrap


def get_setting(db, key, default=None):
    """Получить значение настройки."""
    row = db.execute("SELECT value FROM settings WHERE key = ?", (key,)).fetchone()
    return row['value'] if row else default


def get_setting_int(db, key, default=0):
    """Получить числовое значение настройки."""
    val = get_setting(db, key, str(default))
    try:
        return int(val)
    except (ValueError, TypeError):
        return default


def get_setting_bool(db, key, default=False):
    """Получить булево значение настройки."""
    val = get_setting(db, key, '1' if default else '0')
    return val == '1'


def set_setting(db, key, value, admin_id=None):
    """Сохранить значение настройки."""
    db.execute(
        "UPDATE settings SET value = ?, updated_at = datetime('now') WHERE key = ?",
        (str(value), key)
    )
    if admin_id:
        import json as _json
        details_json = _json.dumps({'value': str(value)}, ensure_ascii=False)
        db.execute(
            "INSERT INTO admin_actions (admin_id, action_type, target_type, target_id, details) VALUES (?, 'update_setting', 'setting', ?, ?)",
            (admin_id, key, details_json)
        )


def get_csrf_token():
    """Возвращает CSRF-токен текущей сессии, генерирует если нет."""
    if 'csrf_token' not in session:
        session['csrf_token'] = _secrets.token_urlsafe(32)
    return session['csrf_token']


def check_csrf():
    """Проверяет CSRF-токен для POST-запросов. Возвращает True если ок."""
    if request.method != 'POST':
        return True
    token = request.form.get('csrf_token') or request.headers.get('X-CSRFToken')
    expected = session.get('csrf_token')
    return bool(token) and bool(expected) and _secrets.compare_digest(token, expected)


def record_event(event_type, db, *, actor_user_id=None, target_user_id=None,
                 goal_id=None, donation_id=None, report_id=None,
                 source='user', severity='info', payload=None, correlation_id=None):
    """
    Единая точка записи событий платформы.
    db ОБЯЗАТЕЛЕН — используется соединение вызывающего (flask.g).
    Commit — на вызывающем коде.
    payload — dict, сериализуется в JSON.
    """
    import json as _json
    
    try:
        payload_json = None
        if payload is not None:
            try:
                payload_json = _json.dumps(payload, ensure_ascii=False)
            except (TypeError, ValueError):
                payload_json = _json.dumps({'error': 'payload serialization failed'})
        
        db.execute("""
            INSERT INTO platform_events 
            (event_type, actor_user_id, target_user_id, goal_id, donation_id, report_id,
             source, severity, payload, correlation_id)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            event_type,
            actor_user_id,
            target_user_id,
            goal_id,
            donation_id,
            report_id,
            source,
            severity,
            payload_json,
            correlation_id,
        ))
    except Exception as e:
        logger.error(f'[record_event] Ошибка записи {event_type}: {e}')


def _safe_count(db, query, params=None):
    """Безопасный COUNT — возвращает 0 если таблица или колонка отсутствует."""
    try:
        if params:
            row = db.execute(query, params).fetchone()
        else:
            row = db.execute(query).fetchone()
        return row[0] if row else 0
    except Exception:
        return 0


def get_sidebar_counts(db):
    """
    Единые счётчики для sidebar админки.
    Один SQL-запрос с подзапросами. При ошибке — fallback на _safe_count.
    """
    try:
        row = db.execute("""
            SELECT
                (SELECT COUNT(*) FROM content_guard_log WHERE created_at > datetime('now', '-24 hours')) AS flags,
                (SELECT COUNT(*) FROM reports WHERE created_at > datetime('now', '-24 hours')) AS reports,
                (SELECT COUNT(*) FROM goals WHERE moderation_status = 'pending') AS moderation,
                (SELECT COUNT(*) FROM suspicious_activity WHERE status = 'new') AS suspicious,
                (SELECT COUNT(*) FROM admin_dialog_messages WHERE sender_type = 'user' AND is_read = 0) AS dialogs,
                (SELECT COUNT(*) FROM platform_events WHERE created_at > datetime('now', '-24 hours')) AS events
        """).fetchone()
        if row:
            return {
                'flags': row['flags'] or 0,
                'reports': row['reports'] or 0,
                'moderation': row['moderation'] or 0,
                'suspicious': row['suspicious'] or 0,
                'dialogs': row['dialogs'] or 0,
                'events': row['events'] or 0,
            }
    except Exception as e:
        logger.warning(f'[get_sidebar_counts] Fallback на _safe_count: {e}')
    
    # Fallback — если хоть одна таблица отсутствует
    return {
        'flags': _safe_count(db, "SELECT COUNT(*) FROM content_guard_log WHERE created_at > datetime('now', '-24 hours')"),
        'reports': _safe_count(db, "SELECT COUNT(*) FROM reports WHERE created_at > datetime('now', '-24 hours')"),
        'moderation': _safe_count(db, "SELECT COUNT(*) FROM goals WHERE moderation_status = 'pending'"),
        'suspicious': _safe_count(db, "SELECT COUNT(*) FROM suspicious_activity WHERE status = 'new'"),
        'dialogs': _safe_count(db, "SELECT COUNT(*) FROM admin_dialog_messages WHERE sender_type = 'user' AND is_read = 0"),
        'events': _safe_count(db, "SELECT COUNT(*) FROM platform_events WHERE created_at > datetime('now', '-24 hours')"),
    }


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
    ago = int(time.time() - 900)
    # Точное сравнение по JSON (защита от LIKE-совпадений)
    phone_json = f'{{"phone":"{phone}"}}'
    c = db.execute(
        "SELECT COUNT(*) FROM analytics_events WHERE event_type='login_attempt' AND event_data = ? AND created_at > datetime(?, 'unixepoch')",
        (phone_json, ago)
    ).fetchone()[0]
    return c >= 5

def allowed_file(filename):
    return '.' in filename and filename.rsplit('.', 1)[1].lower() in ALLOWED_EXTENSIONS

def compress_photo(data):
    """
    Сжимает фото до 800px JPEG quality 75.
    При ошибке Pillow/декодирования — возвращает оригинал (НЕ отклоняет,
    чтобы не ломать сценарий без Pillow).
    Защита от decompression bomb: >40M пикселей → отказ.
    """
    try:
        from PIL import Image
        import io
    except ImportError:
        logger.warning('[compress_photo] Pillow не установлен — фото не сжимается')
        return data
    
    try:
        img = Image.open(io.BytesIO(data))
        
        # Защита от decompression bomb
        if img.width * img.height > 40_000_000:
            logger.warning(f'[compress_photo] Отказ: {img.width}x{img.height} > 40M пикселей')
            return data  # оригинал, но с логом
        
        # Применяем EXIF-поворот (важно для селфи/паспортов с телефона)
        try:
            from PIL import ImageOps
            img = ImageOps.exif_transpose(img)
        except Exception:
            pass
        
        img = img.convert('RGB')
        max_size = (800, 800)
        img.thumbnail(max_size, Image.LANCZOS)
        output = io.BytesIO()
        img.save(output, format='JPEG', quality=75, optimize=True)
        compressed = output.getvalue()
        if len(compressed) < len(data):
            return compressed
    except Exception as e:
        logger.warning(f'[compress_photo] Ошибка сжатия: {e}')
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
    return render_template('index.html', goals=goals_list)

@app.route('/register', methods=['GET','POST'])
def register():
    if request.method == 'POST':
        # Honeypot-проверка
        if request.form.get('honeypot', '').strip():
            flash('Регистрация отклонена.', 'danger')
            return render_template('register.html')
        # Тайминг-проверка через сессию (>3 сек с открытия формы)
        form_time = session.pop('register_form_time', None)
        if form_time is None or (time.time() - form_time) < 3:
            flash('Пожалуйста, не спешите.', 'danger')
            return render_template('register.html')
        
        name = request.form.get('name','').strip()[:50]
        phone = request.form.get('phone','').strip()
        pw = request.form.get('password','').strip()
        pw2 = request.form.get('password2','').strip()
        clean = validate_phone(phone)
        if not clean:
            flash('Номер: 10 цифр после +7.', 'danger')
            return render_template('register.html')
        if len(pw) < 6:
            flash('Пароль: минимум 6 символов.', 'danger')
            return render_template('register.html')
        if pw != pw2:
            flash('Пароли не совпадают.', 'danger')
            return render_template('register.html')
        db = get_db()
        if db.execute("SELECT id FROM users WHERE phone = ?", (clean,)).fetchone():
            flash('Номер уже зарегистрирован.', 'danger')
            return render_template('register.html')
        db.execute("INSERT INTO users (phone, password_hash, name) VALUES (?,?,?)", (clean, generate_password_hash(pw), name if name else None))
        db.commit()
        flash('Регистрация успешна', 'success')
        # Автоматически входим
        user = db.execute("SELECT id FROM users WHERE phone = ?", (clean,)).fetchone()
        if user:
            session.clear()
            session.permanent = True
            session['user_id'] = user['id']
            # trust_score = 50 из DEFAULT, ставим правильный уровень
            db.execute("UPDATE users SET trust_level = 'member' WHERE id = ?", (user['id'],))
            db.commit()
            
            # Событие в platform_events
            record_event(
                'user_registered',
                db=db,
                actor_user_id=user['id'],
                source='user',
                payload={'phone': clean, 'name': name or None}
            )
            db.commit()
        return redirect(url_for('goals_list'))
    # GET — запоминаем время открытия формы
    session['register_form_time'] = time.time()
    return render_template('register.html')

@app.route('/login', methods=['GET','POST'])
def login():
    if request.method == 'POST':
        phone = request.form.get('phone','').strip()
        pw = request.form.get('password','').strip()
        clean = validate_phone(phone)
        if not clean:
            flash('Номер: 10 цифр после +7.', 'danger')
            return render_template('login.html')
        if is_rate_limited(clean):
            flash('Слишком много попыток. Подождите 15 мин.', 'danger')
            return render_template('login.html')
        db = get_db()
        db.execute("INSERT INTO analytics_events (event_type, event_data) VALUES ('login_attempt',?)", (f'{{"phone":"{clean}"}}',))
        db.commit()
        user = db.execute("SELECT * FROM users WHERE phone = ?", (clean,)).fetchone()
        
        # Фиктивный хеш для несуществующего — защита от timing attack
        if not user:
            check_password_hash(generate_password_hash('dummy'), pw)
            logger.warning(f'[login] Попытка входа: несуществующий номер (IP: {request.remote_addr})')
            flash('Неверный номер или пароль.', 'danger')
            return render_template('login.html')
        
        if check_password_hash(user['password_hash'], pw):
            if user['is_banned']:
                logger.warning(f'[login] Попытка входа забаненного: {clean}')
                flash('Аккаунт заблокирован. Обратитесь к администрации.', 'danger')
                return render_template('login.html')
            session.clear()
            session.permanent = True
            session['user_id'] = user['id']
            flash('Вы вошли!', 'success')
            return redirect(url_for('goals_list'))
        
        logger.warning(f'[login] Неудачный вход: {clean} (IP: {request.remote_addr})')
        flash('Неверный номер или пароль.', 'danger')
    return render_template('login.html')

@app.route('/logout', methods=['POST'])
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
        uid = user['id']
        
        try:
            db.execute("BEGIN IMMEDIATE")
        except sqlite3.OperationalError as e:
            logger.error(f'[delete_account] Не удалось начать транзакцию: {e}')
            flash('Не удалось удалить аккаунт. Попробуйте позже.', 'danger')
            return redirect(url_for('profile'))
        
        # ID целей пользователя (для удаления связанных)
        goal_ids = [r['id'] for r in db.execute("SELECT id FROM goals WHERE user_id = ?", (uid,)).fetchall()]
        
        # Собираем ВСЕ файлы фото ДО удаления из БД
        import json as _json_del
        files_to_delete = []
        if goal_ids:
            placeholders_photos = ','.join('?' * len(goal_ids))
            photo_rows = db.execute(
                f"SELECT photo_url, photos FROM goals WHERE id IN ({placeholders_photos})",
                goal_ids
            ).fetchall()
            for row in photo_rows:
                if row['photo_url']:
                    files_to_delete.append(row['photo_url'])
                if row['photos']:
                    try:
                        extra = _json_del.loads(row['photos'])
                        if isinstance(extra, list):
                            files_to_delete.extend(extra)
                    except Exception:
                        pass
        
        if goal_ids:
            placeholders = ','.join('?' * len(goal_ids))
            
            # Донаты (мои + в мои цели)
            db.execute(f"DELETE FROM donations WHERE donor_id = ? OR goal_id IN ({placeholders})", (uid, *goal_ids))
            # Жалобы на мои цели
            db.execute(f"DELETE FROM reports WHERE goal_id IN ({placeholders})", goal_ids)
            # Уведомления по моим целям
            db.execute(f"DELETE FROM notifications_log WHERE goal_id IN ({placeholders})", goal_ids)
            # Отложенные донаты по моим целям
            db.execute(f"DELETE FROM deferred_donations WHERE goal_id IN ({placeholders})", goal_ids)
            # События по моим целям
            db.execute(f"DELETE FROM platform_events WHERE goal_id IN ({placeholders})", goal_ids)
            # Мои цели
            db.execute("DELETE FROM goals WHERE user_id = ?", (uid,))
        
        # Донаты, где я — донатор (если ещё не удалены)
        db.execute("DELETE FROM donations WHERE donor_id = ?", (uid,))
        # Мои жалобы (где я жалобщик)
        db.execute("DELETE FROM reports WHERE reporter_id = ?", (uid,))
        # Мои лайки
        db.execute("DELETE FROM likes WHERE user_id = ?", (uid,))
        # Мои напоминания
        db.execute("DELETE FROM deferred_donations WHERE user_id = ?", (uid,))
        # Мои уведомления
        db.execute("DELETE FROM notifications WHERE user_id = ?", (uid,))
        db.execute("DELETE FROM notifications_log WHERE user_id = ?", (uid,))
        # Мои события
        db.execute("DELETE FROM platform_events WHERE actor_user_id = ?", (uid,))
        # Мои аналитические события
        db.execute("DELETE FROM analytics_events WHERE user_id = ?", (uid,))
        # Мои действия в админке
        db.execute("DELETE FROM admin_actions WHERE admin_id = ?", (uid,))
        # Мои диалоги
        dialog_ids = [r['id'] for r in db.execute("SELECT id FROM admin_dialogs WHERE user_id = ?", (uid,)).fetchall()]
        if dialog_ids:
            dplaceholders = ','.join('?' * len(dialog_ids))
            db.execute(f"DELETE FROM admin_dialog_messages WHERE dialog_id IN ({dplaceholders})", dialog_ids)
            db.execute("DELETE FROM admin_dialogs WHERE user_id = ?", (uid,))
        # Мои сигналы CG
        db.execute("DELETE FROM content_guard_log WHERE user_id = ?", (uid,))
        # Мои suspicious activity
        db.execute("DELETE FROM suspicious_activity WHERE user_id = ?", (uid,))
        
        # Сам пользователь
        db.execute("DELETE FROM users WHERE id = ?", (uid,))
        
        try:
            db.commit()
        except Exception as e:
            db.rollback()
            logger.error(f'[delete_account] Ошибка commit: {e}')
            flash('Не удалось удалить аккаунт. Попробуйте позже.', 'danger')
            return redirect(url_for('profile'))
        
        # Удаляем файлы фото с диска (после успешного commit)
        upload_folder = app.config['UPLOAD_FOLDER']
        deleted_files = 0
        for url_path in files_to_delete:
            try:
                filename = os.path.basename(url_path)
                if not filename:
                    continue
                fpath = os.path.join(upload_folder, filename)
                if os.path.exists(fpath):
                    os.remove(fpath)
                    deleted_files += 1
            except Exception as e:
                logger.warning(f'[delete_account] Не удалось удалить {url_path}: {e}')
        logger.info(f'[delete_account] Удалено файлов: {deleted_files} из {len(files_to_delete)}')
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
                    ELSE COALESCE(NULLIF(u.name, ''), '+' || u.phone) 
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
               COALESCE(NULLIF(u.name, ''), '+' || u.phone) as recipient_name
        FROM donations d
        JOIN goals g ON d.goal_id = g.id
        JOIN users u ON g.user_id = u.id
        WHERE d.donor_id = ?
        ORDER BY d.donor_confirmed_at DESC
    """, (user['id'],)).fetchall()
    total = len(donations)
    approved = sum(1 for d in donations if d['status'] in ('recipient_confirmed', 'completed'))
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
        db = get_db()
        if is_filter_enabled(db):
            r_title = cg_check(title, 'goal_title')
            log_guard(r_title, title, 'goal_title', 'create_goal')
            if r_title['status'] == 'blocked':
                return render_template('create_goal.html', goal_type=goal_type,
                    error_message=r_title['message'],
                    error_position=r_title['position'],
                    error_field='title',
                    title=title, description=desc,
                    amount=amt_str, days=days_str)
            r_desc = cg_check(desc, 'goal_description')
            log_guard(r_desc, desc, 'goal_description', 'create_goal')
            if r_desc['status'] == 'blocked':
                return render_template('create_goal.html', goal_type=goal_type,
                    error_message=r_desc['message'],
                    error_position=r_desc['position'],
                    error_field='description',
                    error_text=desc,
                    title=title, description=desc,
                    amount=amt_str, days=days_str)
            if r_title['status'] == 'flagged' or r_desc['status'] == 'flagged':
                flash('Цель на модерации.', 'warning')
        if not title or len(title) > 140:
            flash('Название: до 140 символов.', 'danger')
            return render_template('create_goal.html', goal_type=goal_type)
        try:
            amt = float(amt_str)
            if not math.isfinite(amt):
                raise ValueError
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
        # Проверка слотов: тип цели → слот → открыт ли (ДО обработки фото)
        if not can_create_goal(user, db, goal_type=goal_type):
            from trust_engine import can_create_goal_reason
            reason = can_create_goal_reason(user, goal_type=goal_type)
            if reason == 'need_profile':
                flash('Блицы закрыты. Заполните анкету.', 'warning')
                return redirect(url_for('profile'))
            elif reason == 'need_video':
                flash('Нужна видео-верификация.', 'warning')
                return redirect(url_for('profile'))
            else:
                flash('Цель в этой категории уже есть.', 'warning')
                return redirect(url_for('goals_list'))
        
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
        ends = (datetime.now() + timedelta(days=days)).isoformat()
        if not user:
            flash('Войдите заново.', 'danger')
            return redirect(url_for('login'))
        # Автоодобрение для админа, модерация для всех остальных
        moderation_status = 'approved' if user['is_admin'] else 'pending'
        goal_status = 'active' if user['is_admin'] else 'draft'
        
        db.execute(
            "INSERT INTO goals (user_id, type, title, description, amount_goal, amount_collected, ends_at, status, photo_url, photos, moderation_status) VALUES (?,?,?,?,?,0,?,?,?,?,?)",
            (user['id'], goal_type, title, desc, amt, ends, goal_status, photo_url, photos_json, moderation_status)
        )
        db.commit()
        gid = db.execute("SELECT last_insert_rowid()").fetchone()[0]
        
        # Событие в platform_events
        record_event(
            'goal_created',
            db=db,
            actor_user_id=user['id'],
            goal_id=gid,
            source='user',
            payload={
                'type': goal_type,
                'title': title,
                'amount': amt,
                'is_admin_auto': bool(user['is_admin']),
            }
        )
        db.commit()
        
        if user['is_admin']:
            flash('Цель создана', 'success')
        else:
            flash('Цель на модерации.', 'info')
        
        return redirect(url_for('goal_page', goal_id=gid))
    return render_template('create_goal.html', goal_type=goal_type)

@app.route('/goal/<int:goal_id>')
def goal_page(goal_id):
    db = get_db()
    goal = db.execute("SELECT * FROM goals WHERE id = ?", (goal_id,)).fetchone()
    if not goal:
        flash('Цель не найдена.', 'danger')
        return redirect(url_for('goals_list'))
    
    # Проверка видимости: черновики и отклонённые видят только автор и админ
    if goal['status'] in ('draft', 'failed'):
        current_user = get_current_user()
        is_author = current_user and current_user['id'] == goal['user_id']
        is_admin = current_user and current_user['is_admin']
        if not (is_author or is_admin):
            abort(404)
    
    # Скрытые цели не показываем никому, кроме админа
    if goal['status'] == 'hidden':
        current_user = get_current_user()
        is_admin = current_user and current_user['is_admin']
        if not is_admin:
            abort(404)
    
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
    
    # Парсим rejection_categories для баннера
    rejection_cats = []
    if goal['rejection_categories']:
        try:
            rejection_cats = _json.loads(goal['rejection_categories'])
        except Exception:
            rejection_cats = []
    
    author = db.execute("SELECT phone FROM users WHERE id = ?", (goal['user_id'],)).fetchone()
    donations = db.execute(
        """SELECT d.*, 
           CASE WHEN d.is_anonymous = 1 OR d.donor_id IS NULL THEN NULL 
                ELSE COALESCE(NULLIF(u.name, ''), '+' || u.phone) 
           END as donor_name
        FROM donations d 
        LEFT JOIN users u ON d.donor_id = u.id 
        WHERE d.goal_id = ? 
        ORDER BY d.donor_confirmed_at DESC""", 
        (goal_id,)
    ).fetchall()
    donor_count = db.execute("SELECT COUNT(DISTINCT donor_id) FROM donations WHERE goal_id = ? AND status IN ('recipient_confirmed','completed')", (goal_id,)).fetchone()[0]
    last_donation = db.execute("SELECT MAX(donor_confirmed_at) FROM donations WHERE goal_id = ?", (goal_id,)).fetchone()[0]
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
    
    # Проверяем, жаловался ли уже пользователь
    already_reported = False
    if user:
        rdb = get_db()
        already_reported = rdb.execute(
            'SELECT id FROM reports WHERE goal_id = ? AND reporter_id = ?',
            (goal_id, user['id'])
        ).fetchone() is not None
    
    return render_template('goal.html', goal=goal, author=author, progress=pct, donations=donations, is_author=is_author, donor_count=donor_count, last_donation=last_donation, ends_at_formatted=ends_at_formatted, already_reported=already_reported, user=user, photos_list=photos_list, is_liked=is_liked, rejection_cats=rejection_cats)

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
        flash('Цель не найдена.', 'danger')
        return redirect(url_for('goals_list'))
    if goal['user_id'] == user['id']:
        flash('Нельзя жаловаться на свою цель.', 'warning')
        return redirect(url_for('goal_page', goal_id=goal_id))
    
    existing = db.execute(
        'SELECT id FROM reports WHERE goal_id = ? AND reporter_id = ?',
        (goal_id, user['id'])
    ).fetchone()
    if existing:
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
        # Атомарное скрытие: только если цель ещё active и не была скрыта по жалобам
        cur = db.execute(
            "UPDATE goals SET status = 'hidden', moderation_status = 'reported' "
            "WHERE id = ? AND status = 'active' AND (moderation_status IS NULL OR moderation_status != 'reported')",
            (goal_id,)
        )
        if cur.rowcount > 0:
            # Скрыли впервые — уведомление автору и trust -20
            db.execute(
                "INSERT INTO notifications_log (user_id, type, goal_id, channel) VALUES (?, 'goal_reported_hidden', ?, 'admin')",
                (goal['user_id'], goal_id)
            )
            add_trust_score(goal['user_id'], 'goal_hidden_reports', db)
            # +3 каждому жалобщику за подтверждённую жалобу
            reporters = db.execute('SELECT DISTINCT reporter_id FROM reports WHERE goal_id = ?', (goal_id,)).fetchall()
            for r in reporters:
                add_trust_score(r['reporter_id'], 'report_valid', db)
            db.commit()
        flash('Цель скрыта после нескольких жалоб. Администратор проверит её.', 'info')
        return redirect(url_for('goals_list'))
    
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


@app.route('/clear', methods=['POST'])
@admin_required
def clear_all():
    """Очистка БД. Только POST + CSRF + явное подтверждение."""
    if request.form.get('confirm') != 'YES_DELETE_ALL':
        flash('Подтверждение не получено. База не очищена.', 'danger')
        return redirect(url_for('metrics_dashboard'))
    
    admin = get_current_user()
    db = get_db()
    db.execute("DELETE FROM donations")
    db.execute("DELETE FROM goals")
    db.execute("DELETE FROM users")
    db.commit()
    
    # Логируем критическое действие
    record_event(
        'db_cleared',
        db=db,
        actor_user_id=admin['id'] if admin else None,
        source='admin',
        severity='critical',
        payload={'action': 'full_db_clear'}
    )
    db.commit()
    
    session.clear()
    flash('База очищена.', 'info')
    return redirect(url_for('index'))




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

def add_xp(user_id, action, amount=0, db=None):
    if user_id is None:
        return 0
    if db is None:
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
    if not user:
        # Пользователь удалён или не существует — XP не начисляем
        return 0
    if user['xp'] >= 100:
        total = db.execute("SELECT COUNT(*) FROM donations WHERE donor_id = ?", (user_id,)).fetchone()[0]
        big = db.execute("SELECT COUNT(*) FROM donations WHERE donor_id = ? AND amount_reported >= 1000", (user_id,)).fetchone()[0]
        if total > 0 and (big / total) > 0.5:
            db.execute("UPDATE users SET author_badge = 'Меценат' WHERE id = ?", (user_id,))
    _, level_key, _ = get_level(user['xp'])
    db.execute("UPDATE users SET xp_level = ? WHERE id = ?", (level_key, user_id))
    today = datetime.now().strftime('%Y-%m-%d')
    u = db.execute("SELECT last_action_date, streak_days FROM users WHERE id = ?", (user_id,)).fetchone()
    if u:
        if u['last_action_date'] == today:
            pass
        elif u['last_action_date'] == (datetime.now() - timedelta(days=1)).strftime('%Y-%m-%d'):
            db.execute("UPDATE users SET streak_days = streak_days + 1, last_action_date = ? WHERE id = ?", (today, user_id))
        else:
            db.execute("UPDATE users SET streak_days = 1, last_action_date = ? WHERE id = ?", (today, user_id))
    # commit делается вызывающим кодом
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
    if not math.isfinite(amount) or amount <= 0:
        flash('Некорректная сумма.', 'danger')
        return redirect(url_for('goal_page', goal_id=goal_id))
    warm_word = request.form.get('warm_word', '').strip()[:200]
    is_anonymous = 1 if request.form.get('anonymous') == '1' else 0
    ip_address = request.remote_addr

    # Лимит на сумму доната
    if amount > 1000000:
        flash('Сумма доната слишком велика (максимум 1 000 000 ₽).', 'danger')
        return redirect(url_for('goal_page', goal_id=goal_id))
    
    db = get_db()
    
    # Проверка цели: существует, активна, есть остаток
    goal_check = db.execute("SELECT id, user_id, status, amount_goal, amount_collected FROM goals WHERE id = ?", (goal_id,)).fetchone()
    if not goal_check:
        flash('Цель не найдена.', 'danger')
        return redirect(url_for('goals_list'))
    if goal_check['status'] != 'active':
        flash('Эта цель не принимает новые переводы.', 'warning')
        return redirect(url_for('goal_page', goal_id=goal_id))
    
    # Проверка: автор цели не забанен
    goal_owner = db.execute("SELECT is_banned FROM users WHERE id = ?", (goal_check['user_id'],)).fetchone()
    if goal_owner and goal_owner['is_banned']:
        flash('Эта цель временно недоступна для переводов.', 'warning')
        return redirect(url_for('goal_page', goal_id=goal_id))
    
    remaining = (goal_check['amount_goal'] or 0) - (goal_check['amount_collected'] or 0)
    if remaining > 0 and amount > remaining:
        flash(f'Сумма превышает остаток по цели ({remaining:.0f} ₽).', 'warning')
        return redirect(url_for('goal_page', goal_id=goal_id))
    
    try:
        db.execute(
            "INSERT INTO donations (goal_id, donor_id, amount_reported, status, warm_word, is_anonymous, ip_address, donor_confirmed_at) VALUES (?, ?, ?, 'donor_confirmed', ?, ?, ?, datetime('now'))",
            (goal_id, donor_id, amount, warm_word if warm_word else None, is_anonymous, ip_address)
        )
        db.commit()
        donation_id = db.execute("SELECT last_insert_rowid()").fetchone()[0]
        
        # Событие в platform_events
        record_event(
            'donation_declared',
            db=db,
            actor_user_id=donor_id,
            goal_id=goal_id,
            donation_id=donation_id,
            source='user',
            payload={
                'amount': amount,
                'is_anonymous': bool(is_anonymous),
                'has_warm_word': bool(warm_word),
            }
        )

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
            add_xp(donor_id, 'donate', db=db)
            add_xp(donor_id, 'donate_new', db=db)
            db.execute("UPDATE users SET total_helped_amount = COALESCE(total_helped_amount, 0) + ? WHERE id = ?", (amount, donor_id))
            db.commit()

        flash('Перевод ожидает подтверждения', 'success')
    except Exception as e:
        db.rollback()
        logger.error(f"[donate] Ошибка: {e}")
        flash('Ошибка при создании перевода. Попробуйте ещё раз.', 'danger')

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
        flash('Донат не найден.', 'danger')
        return redirect(url_for('goals_list'))
    goal = db.execute("SELECT * FROM goals WHERE id = ?", (donation['goal_id'],)).fetchone()
    if goal['user_id'] != user['id']:
        flash('Только автор цели.', 'danger')
        return redirect(url_for('goal_page', goal_id=donation['goal_id']))
    # Атомарное подтверждение: обновляем только если статус ещё donor_confirmed
    cur = db.execute(
        "UPDATE donations SET status = 'recipient_confirmed', recipient_confirmed_at = datetime('now') "
        "WHERE id = ? AND status = 'donor_confirmed'",
        (donation_id,)
    )
    if cur.rowcount == 0:
        flash('Уже обработан', 'info')
        return redirect(url_for('goal_page', goal_id=donation['goal_id']))
    # Удаляем напоминания — получатель уже подтвердил
    db.execute("DELETE FROM notifications_log WHERE donation_id = ? AND type LIKE 'confirm_reminder_%'", (donation_id,))
    db.execute("UPDATE goals SET amount_collected = amount_collected + ? WHERE id = ?", (donation['amount_reported'], donation['goal_id']))
    # Если цель закрылась — выдаём брошь автору и XP участникам
    goal_data = db.execute("SELECT * FROM goals WHERE id = ?", (donation['goal_id'],)).fetchone()
    if goal_data and goal_data['amount_collected'] >= goal_data['amount_goal']:
        db.execute("UPDATE goals SET status = 'completed' WHERE id = ?", (donation['goal_id'],))
        db.execute("UPDATE users SET author_badge = 'Цель закрыта' WHERE id = ?", (goal_data['user_id'],))
        add_xp(goal_data['user_id'], 'goal_closed', db=db)
        # XP всем участникам
        participants = db.execute("SELECT DISTINCT donor_id FROM donations WHERE goal_id = ? AND donor_id IS NOT NULL", (donation['goal_id'],)).fetchall()
        for p in participants:
            add_xp(p['donor_id'], 'goal_closed', db=db)
    db.execute("INSERT INTO analytics_events (user_id, event_type, event_data) VALUES (?, 'transfer_confirmed_recipient', ?)", (user['id'], '{"donation_id":' + str(donation_id) + '}'))
    if donation['donor_id']:
        db.execute("INSERT INTO notifications_log (user_id, type, donation_id, goal_id, channel) VALUES (?, 'goal_almost_closed', ?, ?, 'fcm')", (donation['donor_id'], donation_id, donation['goal_id']))
    db.commit()
    # XP за confirm — только если это не самоподтверждение
    is_self_confirmation = donation['donor_id'] == user['id']
    if not is_self_confirmation:
        add_xp(user['id'], 'confirm', db=db)
        db.commit()
    # Trust Score: получатель +3 за подтверждение, донатор +3 за подтверждённый донат
    add_trust_score(user['id'], 'donation_recipient_confirmed', db)
    if donation['donor_id']:
        add_trust_score(donation['donor_id'], 'donation_recipient_confirmed', db)
    db.commit()
    flash('Подтверждено', 'success')
    
    return redirect(url_for('goal_page', goal_id=donation['goal_id']))


@app.route('/api/confirm/<int:donation_id>', methods=['POST'])
def api_confirm_donation(donation_id):
    """AJAX-подтверждение доната. Возвращает JSON."""
    user = get_current_user()
    if not user:
        return jsonify({'ok': False, 'error': 'Войдите в аккаунт'}), 401
    
    db = get_db()
    donation = db.execute("SELECT * FROM donations WHERE id = ?", (donation_id,)).fetchone()
    if not donation:
        return jsonify({'ok': False, 'error': 'Донат не найден'}), 404
    
    goal = db.execute("SELECT * FROM goals WHERE id = ?", (donation['goal_id'],)).fetchone()
    if goal['user_id'] != user['id']:
        return jsonify({'ok': False, 'error': 'Только автор цели может подтверждать'}), 403
    
    # Атомарное подтверждение: обновляем только если статус ещё donor_confirmed
    cur = db.execute(
        "UPDATE donations SET status = 'recipient_confirmed', recipient_confirmed_at = datetime('now') "
        "WHERE id = ? AND status = 'donor_confirmed'",
        (donation_id,)
    )
    if cur.rowcount == 0:
        return jsonify({'ok': False, 'error': 'Уже обработан'}), 400
    
    db.execute("DELETE FROM notifications_log WHERE donation_id = ? AND type LIKE 'confirm_reminder_%'", (donation_id,))
    db.execute("UPDATE goals SET amount_collected = amount_collected + ? WHERE id = ?", (donation['amount_reported'], donation['goal_id']))
    
    goal_data = db.execute("SELECT * FROM goals WHERE id = ?", (donation['goal_id'],)).fetchone()
    goal_closed = False
    if goal_data and goal_data['amount_collected'] >= goal_data['amount_goal']:
        goal_closed = True
        db.execute("UPDATE goals SET status = 'completed' WHERE id = ?", (donation['goal_id'],))
        db.execute("UPDATE users SET author_badge = 'Цель закрыта' WHERE id = ?", (goal_data['user_id'],))
        add_xp(goal_data['user_id'], 'goal_closed', db=db)
        participants = db.execute("SELECT DISTINCT donor_id FROM donations WHERE goal_id = ? AND donor_id IS NOT NULL", (donation['goal_id'],)).fetchall()
        for p in participants:
            add_xp(p['donor_id'], 'goal_closed', db=db)
    
    db.execute("INSERT INTO analytics_events (user_id, event_type, event_data) VALUES (?, 'transfer_confirmed_recipient', ?)", (user['id'], '{"donation_id":' + str(donation_id) + '}'))
    if donation['donor_id']:
        db.execute("INSERT INTO notifications_log (user_id, type, donation_id, goal_id, channel) VALUES (?, 'goal_almost_closed', ?, ?, 'fcm')", (donation['donor_id'], donation_id, donation['goal_id']))
    db.commit()
    is_self_confirmation = donation['donor_id'] == user['id']
    if not is_self_confirmation:
        add_xp(user['id'], 'confirm', db=db)
        db.commit()
    add_trust_score(user['id'], 'donation_recipient_confirmed', db)
    if donation['donor_id']:
        add_trust_score(donation['donor_id'], 'donation_recipient_confirmed', db)
    db.commit()
    
    # Обновлённые данные для UI
    new_amount = db.execute("SELECT amount_collected FROM goals WHERE id = ?", (donation['goal_id'],)).fetchone()['amount_collected']
    
    return jsonify({
        'ok': True,
        'donation_id': donation_id,
        'amount': donation['amount_reported'],
        'goal_id': donation['goal_id'],
        'goal_amount_collected': new_amount,
        'goal_closed': goal_closed,
        'message': 'Подтверждено' + (' — цель закрыта!' if goal_closed else '')
    })


@app.route('/api/guard-check', methods=['POST'])
def api_guard_check():
    """AJAX-проверка текста через Content Guard с rate limit."""
    from content_guard import check as cg_check
    
    # Rate limit: не более 30 запросов в минуту с одного IP
    ip = request.remote_addr or 'unknown'
    rl_db = get_db()
    recent = rl_db.execute(
        "SELECT COUNT(*) FROM analytics_events WHERE event_type = 'guard_check' AND event_data LIKE ? AND created_at > datetime('now', '-1 minute')",
        (f'%\"ip\":\"{ip}\"%',)
    ).fetchone()[0]
    
    if recent >= 30:
        return jsonify({'status': 'rate_limited', 'error': 'Слишком много запросов. Подождите минуту.'}), 429
    
    rl_db.execute(
        "INSERT INTO analytics_events (event_type, event_data) VALUES ('guard_check', ?)",
        ('{"ip":"' + ip + '"}',)
    )
    rl_db.commit()
    
    data = request.get_json(silent=True) or {}
    text = (data.get('text') or '').strip()
    context = data.get('context', 'goal_description')
    if not text:
        return jsonify({'status': 'passed'})
    result = cg_check(text, context)
    log_guard(result, text, context, 'live_check')
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
    import json as _json
    from PIL import Image
    import io

    user = get_current_user()
    db = get_db()
    goal = db.execute("SELECT * FROM goals WHERE id = ?", (goal_id,)).fetchone()
    if not goal:
        return jsonify({'error': 'Цель не найдена'}), 404
    if goal['user_id'] != user['id']:
        return jsonify({'error': 'Только автор может редактировать'}), 403

    data = request.get_json(silent=True) or {}
    new_order = data.get('order', [])
    angles = data.get('angles', {})
    deleted = data.get('deleted', [])
    
    # Валидация типов
    if not isinstance(new_order, list):
        return jsonify({'error': 'order должен быть списком'}), 400
    if not isinstance(deleted, list):
        return jsonify({'error': 'deleted должен быть списком'}), 400
    if not isinstance(angles, dict):
        return jsonify({'error': 'angles должен быть объектом'}), 400
    
    # Валидация элементов списков — должны быть строками
    if not all(isinstance(p, str) for p in new_order):
        return jsonify({'error': 'order содержит не строки'}), 400
    if not all(isinstance(p, str) for p in deleted):
        return jsonify({'error': 'deleted содержит не строки'}), 400

    # Валидация: все пути должны быть из текущей цели
    current_photos = _json.loads(goal['photos']) if goal['photos'] else []
    if not current_photos and goal['photo_url']:
        current_photos = [goal['photo_url']]

    current_photos_set = set(current_photos)
    def is_own(path):
        return path in current_photos_set

    new_order = [p for p in new_order if is_own(p)]
    deleted = [p for p in deleted if is_own(p)]
    angles = {k: v for k, v in angles.items() if is_own(k)}

    # Собираем пути для удаления (удалим после commit)
    upload_folder = app.config['UPLOAD_FOLDER']
    files_to_delete = []
    for path in deleted:
        filename = os.path.basename(path)
        fpath = os.path.join(upload_folder, filename)
        if os.path.exists(fpath):
            files_to_delete.append(fpath)

    # Валидация angles: только int, только 90/180/270, только свои пути
    validated_angles = {}
    for path, angle in angles.items():
        if not isinstance(path, str) or not is_own(path):
            continue
        if not isinstance(angle, int) or isinstance(angle, bool):
            continue
        if angle not in (90, 180, 270):
            continue
        validated_angles[path] = angle
    
    # Поворачиваем файлы
    for path, angle in validated_angles.items():
        filename = os.path.basename(path)
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
            logger.error(f'Rotate error: {e}')

    # Финальный список — исключаем удалённые, сохраняем порядок
    # Если order пуст — сохраняем текущий порядок (защита от обнуления галереи)
    base_order = new_order if new_order else current_photos
    final = [p for p in base_order if p not in deleted]
    # Дедупликация с сохранением порядка
    final = list(dict.fromkeys(final))

    photo_url = final[0] if final else None
    photos_json = _json.dumps(final) if final else None

    db.execute(
        "UPDATE goals SET photos = ?, photo_url = ? WHERE id = ?",
        (photos_json, photo_url, goal_id)
    )
    db.commit()
    
    # Удаляем файлы только после успешного commit
    deleted_files = 0
    for fpath in files_to_delete:
        try:
            os.remove(fpath)
            deleted_files += 1
        except Exception as e:
            logger.warning(f'[photos_update] Не удалось удалить {fpath}: {e}')
    
    return jsonify({'ok': True, 'photos': final, 'deleted_files': deleted_files})


@app.route('/api/goal/<int:goal_id>/photos/upload', methods=['POST'])
@login_required
def api_photos_upload(goal_id):
    """Добавление новых фото в цель."""
    import json as _json

    user = get_current_user()
    db = get_db()
    goal = db.execute("SELECT * FROM goals WHERE id = ?", (goal_id,)).fetchone()
    if not goal:
        return jsonify({'error': 'Цель не найдена'}), 404
    if goal['user_id'] != user['id']:
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
                logger.error(f'Upload error: {e}')

    final = current_photos + added
    photo_url = final[0] if final else None
    photos_json = _json.dumps(final) if final else None

    db.execute(
        "UPDATE goals SET photos = ?, photo_url = ? WHERE id = ?",
        (photos_json, photo_url, goal_id)
    )
    db.commit()
    return jsonify({'ok': True, 'added': added, 'photos': final})


@app.route('/api/goal/<int:goal_id>/update-description', methods=['POST'])
@login_required
def api_update_description(goal_id):
    """Инлайн-редактирование описания цели."""
    from content_guard import check as cg_check

    user = get_current_user()
    db = get_db()
    goal = db.execute("SELECT * FROM goals WHERE id = ?", (goal_id,)).fetchone()
    if not goal:
        return jsonify({'error': 'Цель не найдена'}), 404
    if goal['user_id'] != user['id']:
        return jsonify({'error': 'Только автор может редактировать'}), 403

    data = request.get_json(silent=True) or {}
    new_desc = (data.get('description') or '').strip()

    if len(new_desc) > 1000:
        return jsonify({'error': 'Максимум 1000 символов'}), 400

    # Content Guard
    r = cg_check(new_desc, 'goal_description')
    log_guard(r, new_desc, 'goal_description', 'edit_description')
    if r['status'] == 'blocked':
        return jsonify({
            'error': r.get('message', 'Текст не прошёл проверку'),
            'position': r.get('position'),
        }), 400

    db.execute("UPDATE goals SET description = ? WHERE id = ?", (new_desc if new_desc else None, goal_id))
    db.commit()
    return jsonify({'ok': True, 'description': new_desc})


@app.route('/api/goal/<int:goal_id>/like', methods=['POST'])
@login_required
def api_goal_like(goal_id):
    user = get_current_user()
    db = get_db()
    goal = db.execute("SELECT id FROM goals WHERE id = ?", (goal_id,)).fetchone()
    if not goal:
        return jsonify({'error': 'Цель не найдена'}), 404
    try:
        cur = db.execute("INSERT OR IGNORE INTO likes (user_id, goal_id) VALUES (?, ?)", (user['id'], goal_id))
        db.commit()
        created = cur.rowcount == 1
    except sqlite3.Error as e:
        logger.error(f'[api_goal_like] Ошибка: {e}')
        return jsonify({'ok': False, 'error': 'Не удалось сохранить лайк'}), 500
    return jsonify({'ok': True, 'liked': True, 'created': created})


@app.route('/api/goal/<int:goal_id>/unlike', methods=['POST'])
@login_required
def api_goal_unlike(goal_id):
    user = get_current_user()
    db = get_db()
    db.execute("DELETE FROM likes WHERE user_id = ? AND goal_id = ?", (user['id'], goal_id))
    db.commit()
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
    db = get_db()
    regions = db.execute(
        "SELECT DISTINCT region_code, region_name FROM cities ORDER BY region_name"
    ).fetchall()
    return jsonify([{'code': r['region_code'], 'name': r['region_name']} for r in regions])


@app.route('/api/cities')
def api_cities():
    """Города по региону."""
    region_code = request.args.get('region', '').strip()
    if not region_code:
        return jsonify([])
    db = get_db()
    cities = db.execute(
        "SELECT city_name FROM cities WHERE region_code = ? ORDER BY city_name",
        (region_code,)
    ).fetchall()
    return jsonify([c['city_name'] for c in cities])


@app.route('/api/onboarding/step', methods=['POST'])
@login_required
def api_onboarding_step():
    """Сохранение одного шага онбординга."""
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
    return jsonify({'ok': True, 'step': step})


@app.route('/api/onboarding/state')
@login_required
def api_onboarding_state():
    """Текущее состояние онбординга пользователя."""
    user = get_current_user()
    if not user:
        return jsonify({'error': 'Не авторизован'}), 401
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
    import json as _json
    user = get_current_user()
    data = request.get_json(silent=True) or {}
    
    # Валидация goal_id
    try:
        goal_id = int(data.get('goal_id'))
    except (ValueError, TypeError):
        return jsonify({'error': 'Неверный ID цели'}), 400
    
    # Валидация photo_index
    photo_index = data.get('photo_index')
    if not isinstance(photo_index, int) or isinstance(photo_index, bool) or photo_index < 0:
        return jsonify({'error': 'Неверный индекс фото'}), 400
    
    reason = (data.get('reason') or '').strip()[:500]
    if not reason:
        return jsonify({'error': 'Укажите причину'}), 400

    db = get_db()
    goal = db.execute("SELECT id, user_id, photos, photo_url FROM goals WHERE id = ?", (goal_id,)).fetchone()
    if not goal:
        return jsonify({'error': 'Цель не найдена'}), 404
    if goal['user_id'] == user['id']:
        return jsonify({'error': 'Нельзя жаловаться на свою цель'}), 403

    # Проверяем, что фото с таким индексом существует
    photos = []
    if goal['photos']:
        try:
            photos = _json.loads(goal['photos'])
        except Exception:
            photos = []
    if not photos and goal['photo_url']:
        photos = [goal['photo_url']]
    
    if photo_index >= len(photos):
        return jsonify({'error': 'Фото не найдено'}), 404

    # Проверяем, не жаловался ли уже
    existing = db.execute(
        "SELECT id FROM reports WHERE goal_id = ? AND reporter_id = ? AND photo_index = ?",
        (goal_id, user['id'], photo_index)
    ).fetchone()
    if existing:
        return jsonify({'error': 'Вы уже жаловались на это фото'}), 400

    db.execute(
        "INSERT INTO reports (goal_id, reporter_id, reason, photo_index) VALUES (?, ?, ?, ?)",
        (goal_id, user['id'], reason, photo_index)
    )
    db.commit()
    return jsonify({'ok': True})


@app.route('/api/deferred/set', methods=['POST'])
@login_required
def api_deferred_set():
    """Установить напоминание о цели."""
    user = get_current_user()
    data = request.get_json(silent=True) or {}
    
    # Валидация goal_id
    try:
        goal_id = int(data.get('goal_id'))
    except (ValueError, TypeError):
        return jsonify({'error': 'Неверный ID цели'}), 400
    
    frequency = data.get('frequency', 'once')  # once / daily / custom
    days = data.get('days')  # для custom
    
    if frequency not in ('once', 'daily', 'custom'):
        return jsonify({'error': 'Неверный тип напоминания'}), 400

    db = get_db()
    goal = db.execute("SELECT id FROM goals WHERE id = ?", (goal_id,)).fetchone()
    if not goal:
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
            return jsonify({'error': 'Неверное количество дней'}), 400
    else:
        return jsonify({'error': 'Неверный тип напоминания'}), 400

    # Удаляем старое напоминание для этой цели
    db.execute("DELETE FROM deferred_donations WHERE user_id = ? AND goal_id = ?", (user['id'], goal_id))

    # Создаём новое
    db.execute(
        "INSERT INTO deferred_donations (user_id, goal_id, remind_at, frequency, status) VALUES (?, ?, ?, ?, 'pending')",
        (user['id'], goal_id, remind_at, frequency)
    )
    db.commit()
    return jsonify({'ok': True, 'remind_at': remind_at, 'frequency': frequency})


@app.route('/api/deferred/cancel', methods=['POST'])
@login_required
def api_deferred_cancel():
    """Отменить напоминание."""
    user = get_current_user()
    data = request.get_json(silent=True) or {}
    goal_id = data.get('goal_id')

    if not goal_id:
        return jsonify({'error': 'Не указана цель'}), 400

    db = get_db()
    db.execute("DELETE FROM deferred_donations WHERE user_id = ? AND goal_id = ?", (user['id'], goal_id))
    db.commit()
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
    return jsonify({'ok': True})


@app.route('/api/identity/upload', methods=['POST'])
@login_required
def api_identity_upload():
    """Загрузка документа или селфи для верификации."""
    user = get_current_user()
    doc_type = request.form.get('type', '').strip()
    if doc_type not in ('passport', 'selfie'):
        return jsonify({'error': 'Неверный тип'}), 400

    file = request.files.get('file')
    if not file or not file.filename:
        return jsonify({'error': 'Файл не загружен'}), 400

    if not allowed_file(file.filename):
        return jsonify({'error': 'Неверный формат файла'}), 400

    # Проверка через Pillow (реальный тип, не только расширение)
    try:
        from PIL import Image
        import io as _io
        data = file.read()
        try:
            img_check = Image.open(_io.BytesIO(data))
            if img_check.format not in ('JPEG', 'PNG', 'WEBP'):
                return jsonify({'error': 'Неверный формат (JPEG/PNG/WEBP)'}), 400
            if img_check.width * img_check.height > 40_000_000:
                return jsonify({'error': 'Фото слишком большое'}), 400
            # Переоткрыть после verify
            img_check = Image.open(_io.BytesIO(data))
        except Exception:
            return jsonify({'error': 'Файл не является изображением'}), 400
    except ImportError:
        data = file.read()
    
    # Папка uploads/identity
    identity_folder = os.path.join(BASE_DIR, 'uploads', 'identity')
    os.makedirs(identity_folder, exist_ok=True)
    
    # UUID-имя вместо предсказуемого {user_id}_{doc_type}
    fname = f"{uuid.uuid4().hex}.jpg"
    fpath = os.path.join(identity_folder, fname)

    try:
        compressed = compress_photo(data)
        with open(fpath, 'wb') as f:
            f.write(compressed)
    except Exception as e:
        logger.error(f'[api_identity_upload] Ошибка сохранения: {e}')
        return jsonify({'error': 'Не удалось сохранить файл'}), 500

    rel_path = f"uploads/identity/{fname}"
    db = get_db()
    
    # Запоминаем старый путь для удаления
    old_path = db.execute(
        f"SELECT identity_{'doc' if doc_type == 'passport' else 'selfie'}_path as p FROM users WHERE id = ?",
        (user['id'],)
    ).fetchone()
    
    if doc_type == 'passport':
        db.execute("UPDATE users SET identity_doc_path = ? WHERE id = ?", (rel_path, user['id']))
    else:
        db.execute("UPDATE users SET identity_selfie_path = ? WHERE id = ?", (rel_path, user['id']))
    
    # Аудит
    import json as _json
    record_event(
        'identity_uploaded',
        db=db,
        actor_user_id=user['id'],
        source='user',
        payload={'doc_type': doc_type, 'path': rel_path}
    )
    db.commit()
    
    # Удаляем старый файл (если был)
    if old_path and old_path['p']:
        try:
            old_filename = os.path.basename(old_path['p'])
            old_fpath = os.path.join(identity_folder, old_filename)
            if os.path.exists(old_fpath):
                os.remove(old_fpath)
        except Exception as e:
            logger.warning(f'[api_identity_upload] Не удалён старый файл: {e}')

    return jsonify({'ok': True, 'path': '/' + rel_path})


@app.route('/uploads/identity/<filename>')
@login_required
def identity_file(filename):
    """Отдача файлов верификации — владельцу или админу (для модерации)."""
    import re as _re
    user = get_current_user()
    if not user:
        abort(401)
    
    # Защита от traversal и проверка формата имени (uuid.jpg)
    if not _re.fullmatch(r'[0-9a-f]{32}\.(jpg|jpeg|png|webp)', filename):
        abort(404)
    
    # Админ — может открыть любой
    if user['is_admin']:
        return send_from_directory(os.path.join(BASE_DIR, 'uploads', 'identity'), filename)
    
    # Обычный юзер — только свой файл
    # Проверяем, что этот файл принадлежит юзеру (по БД, а не по префиксу имени)
    db = get_db()
    u = db.execute(
        "SELECT identity_doc_path, identity_selfie_path FROM users WHERE id = ?",
        (user['id'],)
    ).fetchone()
    if not u:
        abort(403)
    
    own_paths = set()
    if u['identity_doc_path']:
        own_paths.add(os.path.basename(u['identity_doc_path']))
    if u['identity_selfie_path']:
        own_paths.add(os.path.basename(u['identity_selfie_path']))
    
    if filename not in own_paths:
        abort(403)
    
    return send_from_directory(os.path.join(BASE_DIR, 'uploads', 'identity'), filename)


@app.route('/api/state')
@login_required
def api_state():
    """
    Текущее состояние пользователя — действия на лету.
    """
    user = get_current_user()
    db = get_db()
    actions = []

    # 1. Профиль не заполнен
    if not user['profile_completed']:
        missing = []
        if not user['name']:
            missing.append('имя')
        if not user['region_code'] or not user['city']:
            missing.append('город')
        if not user['birth_date']:
            missing.append('дата рождения')
        if missing:
            actions.append({
                'type': 'onboarding',
                'priority': 1,
                'title': 'Завершите профиль',
                'message': 'Осталось: ' + ', '.join(missing) + '.',
                'action_url': '/profile/edit',
                'action_label': 'Заполнить'
            })

    # 2. Неподтверждённые донаты
    unconfirmed = db.execute("""
        SELECT d.id, d.amount_reported, g.title as goal_title,
               COALESCE(NULLIF(u.name, ''), 'Донатор') as donor_name
        FROM donations d
        JOIN goals g ON d.goal_id = g.id
        LEFT JOIN users u ON d.donor_id = u.id
        WHERE g.user_id = ? AND d.status = 'donor_confirmed'
    """, (user['id'],)).fetchall()

    if unconfirmed:
        actions.append({
            'type': 'donation',
            'priority': 2,
            'title': 'Переводы ожидают подтверждения',
            'message': str(len(unconfirmed)) + ' перевод(а) ждут вашего подтверждения.',
            'action_url': '/profile/received',
            'action_label': 'Проверить'
        })

    # 3. Отложенные напоминания (созревшие)
    now_iso = datetime.now().isoformat()
    deferred = db.execute("""
        SELECT dd.id, dd.goal_id, g.title as goal_title
        FROM deferred_donations dd
        JOIN goals g ON dd.goal_id = g.id
        WHERE dd.user_id = ? AND dd.status = 'pending' AND dd.remind_at <= ?
    """, (user['id'], now_iso)).fetchall()

    if deferred:
        actions.append({
            'type': 'reminder',
            'priority': 3,
            'title': 'Напоминание',
            'message': str(len(deferred)) + ' цель(и) ждёт вашей поддержки.',
            'action_url': '/goal/' + str(deferred[0]['goal_id']) if len(deferred) == 1 else '/profile',
            'action_label': 'Перейти'
        })

    # 4. Открытые диалоги с админом
    open_dialogs = db.execute("""
        SELECT d.id, d.subject,
               (SELECT COUNT(*) FROM admin_dialog_messages 
                WHERE dialog_id = d.id AND sender_type = 'admin' AND is_read = 0) as unread,
               (SELECT COUNT(*) FROM admin_dialog_messages 
                WHERE dialog_id = d.id) as total
        FROM admin_dialogs d
        WHERE d.user_id = ? AND d.status = 'open'
        ORDER BY d.id DESC LIMIT 3
    """, (user['id'],)).fetchall()

    if open_dialogs:
        total_unread = sum(d['unread'] for d in open_dialogs)
        first_dialog = open_dialogs[0]
        if total_unread > 0:
            message = str(total_unread) + ' новых сообщений.'
        else:
            message = 'Диалог с администрацией открыт.'
        actions.append({
            'type': 'dialog',
            'priority': 1,
            'title': 'Сообщение от администрации',
            'message': message,
            'action_url': '/dialogs/' + str(first_dialog['id']),
            'action_label': 'Открыть'
        })

    # 5. Непрочитанные события
    unread_events = db.execute(
        "SELECT COUNT(*) FROM notifications WHERE user_id = ? AND is_read = 0",
        (user['id'],)
    ).fetchone()[0]

    if unread_events:
        actions.append({
            'type': 'events',
            'priority': 5,
            'title': 'Новые события',
            'message': str(unread_events) + ' новых уведомлений.',
            'action_url': '/notifications',
            'action_label': 'Открыть'
        })

    return jsonify({
        'actions': actions,
        'count': len(actions)
    })


@app.route('/api/notifications')
@login_required
def api_notifications():
    """Только события (что-то произошло)."""
    user = get_current_user()
    db = get_db()
    rows = db.execute(
        "SELECT * FROM notifications WHERE user_id = ? ORDER BY created_at DESC LIMIT 20",
        (user['id'],)
    ).fetchall()
    return jsonify({'items': [dict(r) for r in rows]})


@app.route('/api/notifications/read', methods=['POST'])
@login_required
def api_notifications_read():
    """Отметить уведомление прочитанным."""
    user = get_current_user()
    data = request.get_json(silent=True) or {}
    nid = data.get('id')
    db = get_db()
    if nid:
        db.execute("UPDATE notifications SET is_read = 1 WHERE id = ? AND user_id = ?", (nid, user['id']))
    else:
        db.execute("UPDATE notifications SET is_read = 1 WHERE user_id = ?", (user['id'],))
    db.commit()
    return jsonify({'ok': True})


# ============================================
# МОСТИК — админ-панель /_metrics
# ============================================

@app.route('/_metrics')
@app.route('/_metrics/')
@admin_required
def metrics_dashboard():
    """Дашборд — сводка за сегодня."""
    user = get_current_user()
    db = get_db()
    
    today = datetime.now().strftime('%Y-%m-%d')
    
    # Метрики за сегодня
    flags_today = db.execute(
        "SELECT COUNT(*) FROM content_guard_log WHERE date(created_at) = ?",
        (today,)
    ).fetchone()[0]
    
    reports_new = db.execute(
        "SELECT COUNT(*) FROM reports WHERE date(created_at) = ?",
        (today,)
    ).fetchone()[0]
    
    moderation_pending = db.execute(
        "SELECT COUNT(*) FROM goals WHERE moderation_status = 'pending'"
    ).fetchone()[0]
    
    new_users_today = db.execute(
        "SELECT COUNT(*) FROM users WHERE date(created_at) = ?",
        (today,)
    ).fetchone()[0]
    
    donations_sum_today = db.execute(
        "SELECT COALESCE(SUM(amount_reported), 0) FROM donations WHERE date(donor_confirmed_at) = ? AND status IN ('recipient_confirmed','completed')",
        (today,)
    ).fetchone()[0]
    
    donations_count_today = db.execute(
        "SELECT COUNT(*) FROM donations WHERE date(donor_confirmed_at) = ?",
        (today,)
    ).fetchone()[0]
    
    active_goals = db.execute(
        "SELECT COUNT(*) FROM goals WHERE status = 'active'"
    ).fetchone()[0]
    
    suspicious_new = db.execute(
        "SELECT COUNT(*) FROM suspicious_activity WHERE status = 'new'"
    ).fetchone()[0]
    
    dialogs_open = db.execute(
        "SELECT COUNT(*) FROM admin_dialogs WHERE status = 'open'"
    ).fetchone()[0]
    
    # Последние флаги
    recent_flags = db.execute(
        "SELECT * FROM content_guard_log ORDER BY id DESC LIMIT 10"
    ).fetchall()
    
    # Для sidebar — счётчики
    counts = get_sidebar_counts(db)
    
    
    return render_template('_metrics/dashboard.html',
        user=user,
        active='dashboard',
        counts=counts,
        flags_today=flags_today,
        reports_new=reports_new,
        moderation_pending=moderation_pending,
        new_users_today=new_users_today,
        donations_sum_today=donations_sum_today,
        donations_count_today=donations_count_today,
        active_goals=active_goals,
        suspicious_new=suspicious_new,
        dialogs_open=dialogs_open,
        recent_flags=recent_flags,
    )


@app.route('/_metrics/flags')
@admin_required
def metrics_flags():
    """Список срабатываний Content Guard."""
    user = get_current_user()
    db = get_db()
    
    # Фильтры
    status_filter = request.args.get('status', 'all')
    category_filter = request.args.get('category', 'all')
    user_filter = request.args.get('user', '').strip()
    
    query = "SELECT * FROM content_guard_log WHERE 1=1"
    params = []
    
    if status_filter != 'all':
        query += " AND status = ?"
        params.append(status_filter)
    
    if category_filter != 'all':
        query += " AND category = ?"
        params.append(category_filter)
    
    if user_filter:
        query += " AND (user_id = ? OR ip_address LIKE ?)"
        params.append(user_filter if user_filter.isdigit() else -1)
        params.append(f'%{user_filter}%')
    
    query += " ORDER BY id DESC LIMIT 200"
    flags = db.execute(query, params).fetchall()
    
    # Все категории для фильтра
    categories = db.execute(
        "SELECT DISTINCT category FROM content_guard_log WHERE category IS NOT NULL ORDER BY category"
    ).fetchall()
    
    # Счётчики по статусам
    counts_status = {
        'all': db.execute("SELECT COUNT(*) FROM content_guard_log").fetchone()[0],
        'blocked': db.execute("SELECT COUNT(*) FROM content_guard_log WHERE status = 'blocked'").fetchone()[0],
        'flagged': db.execute("SELECT COUNT(*) FROM content_guard_log WHERE status = 'flagged'").fetchone()[0],
    }
    
    counts = get_sidebar_counts(db)
    
    
    return render_template('_metrics/flags.html',
        user=user,
        active='flags',
        counts=counts,
        flags=flags,
        categories=[c['category'] for c in categories],
        status_filter=status_filter,
        category_filter=category_filter,
        user_filter=user_filter,
        counts_status=counts_status,
    )


@app.route('/_metrics/users')
@admin_required
def metrics_users():
    """Список всех пользователей."""
    user = get_current_user()
    db = get_db()
    
    search = request.args.get('search', '').strip()
    filter_type = request.args.get('filter', 'all')  # all, banned, flagged, active
    
    query = """
        SELECT u.*,
            (SELECT COUNT(*) FROM goals WHERE user_id = u.id) as goals_count,
            (SELECT COUNT(*) FROM donations WHERE donor_id = u.id AND status IN ('recipient_confirmed','completed')) as donations_sent,
            (SELECT COALESCE(SUM(amount_reported), 0) FROM donations WHERE donor_id = u.id AND status IN ('recipient_confirmed','completed')) as total_sent,
            (SELECT COUNT(*) FROM content_guard_log WHERE user_id = u.id) as flags_count,
            (SELECT COUNT(*) FROM reports WHERE reporter_id = u.id) as reports_made
        FROM users u
        WHERE 1=1
    """
    params = []
    
    if search:
        query += " AND (u.phone LIKE ? OR u.name LIKE ? OR CAST(u.id AS TEXT) = ?)"
        params.extend([f'%{search}%', f'%{search}%', search])
    
    if filter_type == 'banned':
        query += " AND u.is_banned = 1"
    elif filter_type == 'flagged':
        query += " AND (SELECT COUNT(*) FROM content_guard_log WHERE user_id = u.id) > 0"
    elif filter_type == 'active':
        query += " AND (SELECT COUNT(*) FROM donations WHERE donor_id = u.id) > 0"
    
    query += " ORDER BY u.id DESC LIMIT 200"
    users = db.execute(query, params).fetchall()
    
    # Счётчики
    counts_total = db.execute("SELECT COUNT(*) FROM users").fetchone()[0]
    counts_banned = db.execute("SELECT COUNT(*) FROM users WHERE is_banned = 1").fetchone()[0]
    counts_flagged = db.execute("SELECT COUNT(DISTINCT user_id) FROM content_guard_log WHERE user_id IS NOT NULL").fetchone()[0]
    counts_active = db.execute("SELECT COUNT(DISTINCT donor_id) FROM donations WHERE donor_id IS NOT NULL").fetchone()[0]
    
    counts = get_sidebar_counts(db)
    
    
    return render_template('_metrics/users.html',
        user=user,
        active='users',
        counts=counts,
        users=users,
        search=search,
        filter_type=filter_type,
        counts_total=counts_total,
        counts_banned=counts_banned,
        counts_flagged=counts_flagged,
        counts_active=counts_active,
    )


@app.route('/_metrics/users/<int:user_id>')
@admin_required
def metrics_user_detail(user_id):
    """Карточка пользователя — всё о нём."""
    admin = get_current_user()
    db = get_db()
    
    u = db.execute("SELECT * FROM users WHERE id = ?", (user_id,)).fetchone()
    if not u:
        abort(404)
    
    # Статистика
    goals = db.execute(
        "SELECT * FROM goals WHERE user_id = ? ORDER BY id DESC",
        (user_id,)
    ).fetchall()
    
    donations_sent = db.execute(
        """SELECT d.*, g.title as goal_title, g.id as goal_id,
                  COALESCE(NULLIF(gu.name, ''), gu.phone) as recipient_name
           FROM donations d
           JOIN goals g ON d.goal_id = g.id
           JOIN users gu ON g.user_id = gu.id
           WHERE d.donor_id = ?
           ORDER BY d.id DESC LIMIT 50""",
        (user_id,)
    ).fetchall()
    
    donations_received = db.execute(
        """SELECT d.*, g.title as goal_title, g.id as goal_id,
                  COALESCE(NULLIF(du.name, ''), du.phone) as donor_name
           FROM donations d
           JOIN goals g ON d.goal_id = g.id
           LEFT JOIN users du ON d.donor_id = du.id
           WHERE g.user_id = ?
           ORDER BY d.id DESC LIMIT 50""",
        (user_id,)
    ).fetchall()
    
    flags = db.execute(
        "SELECT * FROM content_guard_log WHERE user_id = ? ORDER BY id DESC LIMIT 30",
        (user_id,)
    ).fetchall()
    
    reports_made = db.execute(
        "SELECT r.*, g.title as goal_title FROM reports r JOIN goals g ON r.goal_id = g.id WHERE r.reporter_id = ? ORDER BY r.id DESC LIMIT 20",
        (user_id,)
    ).fetchall()
    
    reports_received = db.execute(
        """SELECT r.*, g.title as goal_title FROM reports r 
           JOIN goals g ON r.goal_id = g.id 
           WHERE g.user_id = ? ORDER BY r.id DESC LIMIT 20""",
        (user_id,)
    ).fetchall()
    
    dialogs = db.execute(
        "SELECT * FROM admin_dialogs WHERE user_id = ? ORDER BY id DESC LIMIT 10",
        (user_id,)
    ).fetchall()
    
    # Итоги
    total_sent = db.execute(
        "SELECT COALESCE(SUM(amount_reported), 0) FROM donations WHERE donor_id = ? AND status IN ('recipient_confirmed','completed')",
        (user_id,)
    ).fetchone()[0]
    
    total_received = db.execute(
        "SELECT COALESCE(SUM(d.amount_reported), 0) FROM donations d JOIN goals g ON d.goal_id = g.id WHERE g.user_id = ? AND d.status IN ('recipient_confirmed','completed')",
        (user_id,)
    ).fetchone()[0]
    
    counts = get_sidebar_counts(db)
    
    
    return render_template('_metrics/user_detail.html',
        user=admin,
        active='users',
        counts=counts,
        u=u,
        goals=goals,
        donations_sent=donations_sent,
        donations_received=donations_received,
        flags=flags,
        reports_made=reports_made,
        reports_received=reports_received,
        dialogs=dialogs,
        total_sent=total_sent,
        total_received=total_received,
    )


@app.route('/_metrics/users/<int:user_id>/ban', methods=['POST'])
@admin_required
def metrics_user_ban(user_id):
    """Забанить пользователя с категориями и комментарием."""
    import json as _json
    admin = get_current_user()
    categories_raw = (request.form.get('categories') or '').strip()
    comment = (request.form.get('comment') or '').strip()
    
    if not categories_raw:
        flash('Выберите минимум одну категорию.', 'danger')
        return redirect(url_for('metrics_user_detail', user_id=user_id))
    
    categories = [c.strip() for c in categories_raw.split(',') if c.strip()]
    
    db = get_db()
    u = db.execute("SELECT * FROM users WHERE id = ?", (user_id,)).fetchone()
    if not u:
        abort(404)
    if u['is_admin']:
        flash('Нельзя забанить админа.', 'danger')
        return redirect(url_for('metrics_user_detail', user_id=user_id))
    
    db.execute("UPDATE users SET is_banned = 1 WHERE id = ?", (user_id,))
    
    # Скрываем все активные цели забаненного (с флагом)
    hidden_count = db.execute(
        "UPDATE goals SET status = 'hidden', hidden_by_ban = 1 WHERE user_id = ? AND status IN ('active', 'draft')",
        (user_id,)
    ).rowcount
    
    details = _json.dumps({
        'categories': categories,
        'comment': comment,
        'old_value': 'active',
        'new_value': 'banned',
        'hidden_goals': hidden_count
    }, ensure_ascii=False)
    db.execute(
        "INSERT INTO admin_actions (admin_id, action_type, target_type, target_id, details) VALUES (?, 'ban_user', 'user', ?, ?)",
        (admin['id'], str(user_id), details)
    )
    db.commit()
    
    if hidden_count > 0:
        flash(f'Пользователь #{user_id} забанен. Скрыто целей: {hidden_count}.', 'success')
    else:
        flash(f'Пользователь #{user_id} забанен.', 'success')
    return redirect(url_for('metrics_user_detail', user_id=user_id))


@app.route('/_metrics/users/<int:user_id>/unban', methods=['POST'])
@admin_required
def metrics_user_unban(user_id):
    """Разбанить пользователя с причиной."""
    import json as _json
    admin = get_current_user()
    categories_raw = (request.form.get('categories') or '').strip()
    comment = (request.form.get('comment') or '').strip()
    
    if not categories_raw:
        flash('Выберите минимум одну причину разбана.', 'danger')
        return redirect(url_for('metrics_user_detail', user_id=user_id))
    
    categories = [c.strip() for c in categories_raw.split(',') if c.strip()]
    
    db = get_db()
    u = db.execute("SELECT * FROM users WHERE id = ?", (user_id,)).fetchone()
    if not u:
        abort(404)
    
    db.execute("UPDATE users SET is_banned = 0 WHERE id = ?", (user_id,))
    
    # Восстанавливаем цели, скрытые при бане
    restored_count = db.execute(
        "UPDATE goals SET status = 'active', hidden_by_ban = 0 WHERE user_id = ? AND hidden_by_ban = 1 AND status = 'hidden'",
        (user_id,)
    ).rowcount
    
    details = _json.dumps({
        'categories': categories,
        'comment': comment,
        'old_value': 'banned',
        'new_value': 'active',
        'restored_goals': restored_count
    }, ensure_ascii=False)
    db.execute(
        "INSERT INTO admin_actions (admin_id, action_type, target_type, target_id, details) VALUES (?, 'unban_user', 'user', ?, ?)",
        (admin['id'], str(user_id), details)
    )
    db.commit()
    
    if restored_count > 0:
        flash(f'Пользователь #{user_id} разбанен. Восстановлено целей: {restored_count}.', 'success')
    else:
        flash(f'Пользователь #{user_id} разбанен.', 'success')
    return redirect(url_for('metrics_user_detail', user_id=user_id))


@app.route('/_metrics/goals')
@admin_required
def metrics_goals():
    """Список всех целей."""
    user = get_current_user()
    db = get_db()
    
    search = request.args.get('search', '').strip()
    status_filter = request.args.get('status', 'all')
    type_filter = request.args.get('type', 'all')
    
    query = """
        SELECT g.*, 
            COALESCE(NULLIF(u.name, ''), u.phone) as author_name,
            u.phone as author_phone,
            (SELECT COUNT(*) FROM donations WHERE goal_id = g.id) as donations_count,
            (SELECT COUNT(*) FROM reports WHERE goal_id = g.id) as reports_count
        FROM goals g
        JOIN users u ON g.user_id = u.id
        WHERE 1=1
    """
    params = []
    
    if search:
        query += " AND (g.title LIKE ? OR CAST(g.id AS TEXT) = ?)"
        params.extend([f'%{search}%', search])
    
    if status_filter != 'all':
        query += " AND g.status = ?"
        params.append(status_filter)
    
    if type_filter != 'all':
        query += " AND g.type = ?"
        params.append(type_filter)
    
    query += " ORDER BY g.id DESC LIMIT 200"
    goals = db.execute(query, params).fetchall()
    
    # Счётчики
    counts_all = db.execute("SELECT COUNT(*) FROM goals").fetchone()[0]
    counts_active = db.execute("SELECT COUNT(*) FROM goals WHERE status = 'active'").fetchone()[0]
    counts_completed = db.execute("SELECT COUNT(*) FROM goals WHERE status = 'completed'").fetchone()[0]
    counts_hidden = db.execute("SELECT COUNT(*) FROM goals WHERE status = 'hidden'").fetchone()[0]
    
    counts = get_sidebar_counts(db)
    
    
    return render_template('_metrics/goals.html',
        user=user,
        active='goals',
        counts=counts,
        goals=goals,
        search=search,
        status_filter=status_filter,
        type_filter=type_filter,
        counts_all=counts_all,
        counts_active=counts_active,
        counts_completed=counts_completed,
        counts_hidden=counts_hidden,
    )


@app.route('/_metrics/goals/<int:goal_id>')
@admin_required
def metrics_goal_detail(goal_id):
    """Карточка цели — всё о ней."""
    admin = get_current_user()
    db = get_db()
    
    g = db.execute("""
        SELECT g.*, 
            COALESCE(NULLIF(u.name, ''), u.phone) as author_name,
            u.phone as author_phone,
            u.id as author_id,
            u.trust_score as author_trust,
            u.identity_verified as author_verified,
            u.is_banned as author_banned
        FROM goals g
        JOIN users u ON g.user_id = u.id
        WHERE g.id = ?
    """, (goal_id,)).fetchone()
    
    if not g:
        abort(404)
    
    donations = db.execute("""
        SELECT d.*, 
            COALESCE(NULLIF(u.name, ''), u.phone) as donor_name,
            u.id as donor_user_id
        FROM donations d
        LEFT JOIN users u ON d.donor_id = u.id
        WHERE d.goal_id = ?
        ORDER BY d.id DESC
    """, (goal_id,)).fetchall()
    
    reports = db.execute("""
        SELECT r.*, 
            COALESCE(NULLIF(u.name, ''), u.phone) as reporter_name,
            u.id as reporter_user_id
        FROM reports r
        LEFT JOIN users u ON r.reporter_id = u.id
        WHERE r.goal_id = ?
        ORDER BY r.id DESC
    """, (goal_id,)).fetchall()
    
    flags = db.execute("""
        SELECT * FROM content_guard_log 
        WHERE user_id = ? AND (context LIKE '%goal%' OR context = 'goal_title' OR context = 'goal_description')
        ORDER BY id DESC LIMIT 20
    """, (g['user_id'],)).fetchall()
    
    # История модерации — из admin_actions (решения админов)
    moderation_history_rows = db.execute("""
        SELECT aa.*, COALESCE(NULLIF(u.name, ''), u.phone) as admin_name
        FROM admin_actions aa
        LEFT JOIN users u ON aa.admin_id = u.id
        WHERE aa.target_type = 'goal' AND aa.target_id = ?
        ORDER BY aa.id DESC
    """, (str(goal_id),)).fetchall()
    
    # История событий — из platform_events (действия автора и системы)
    platform_events_rows = db.execute("""
        SELECT pe.*, COALESCE(NULLIF(u.name, ''), u.phone) as actor_name
        FROM platform_events pe
        LEFT JOIN users u ON pe.actor_user_id = u.id
        WHERE pe.goal_id = ? AND pe.event_type IN ('goal_resubmitted', 'goal_created', 'goal_published')
        ORDER BY pe.id DESC
    """, (goal_id,)).fetchall()
    
    import json as _json
    moderation_history = []
    
    for h in moderation_history_rows:
        hd = dict(h)
        det = {}
        if hd.get('details'):
            try:
                det = _json.loads(hd['details'])
            except:
                det = {}
        hd['parsed_categories'] = det.get('categories', [])
        hd['parsed_comment'] = det.get('comment', '')
        hd['event_kind'] = 'admin_action'
        moderation_history.append(hd)
    
    for pe in platform_events_rows:
        pd = dict(pe)
        det = {}
        if pd.get('payload'):
            try:
                det = _json.loads(pd['payload'])
            except:
                det = {}
        pd['parsed_categories'] = []
        pd['parsed_comment'] = ''
        pd['event_kind'] = 'platform_event'
        moderation_history.append(pd)
    
    # Сортируем по дате — новые сверху
    def _norm_dt(s):
        return (s or '').replace('T', ' ')[:19]
    moderation_history.sort(key=lambda x: _norm_dt(x.get('created_at')), reverse=True)
    
    # Парсим фото
    photos = []
    if g['photos']:
        try:
            import json as _json
            photos = _json.loads(g['photos'])
        except:
            pass
    elif g['photo_url']:
        photos = [g['photo_url']]
    
    counts = get_sidebar_counts(db)
    
    
    return render_template('_metrics/goal_detail.html',
        user=admin,
        active='goals',
        counts=counts,
        g=g,
        donations=donations,
        reports=reports,
        flags=flags,
        photos=photos,
        moderation_history=moderation_history,
    )


@app.route('/_metrics/goals/<int:goal_id>/hide', methods=['POST'])
@admin_required
def metrics_goal_hide(goal_id):
    """Скрыть цель с категориями и комментарием."""
    import json as _json
    admin = get_current_user()
    categories_raw = (request.form.get('categories') or '').strip()
    comment = (request.form.get('comment') or '').strip()
    
    if not categories_raw:
        flash('Выберите минимум одну категорию.', 'danger')
        return redirect(url_for('metrics_goal_detail', goal_id=goal_id))
    
    categories = [c.strip() for c in categories_raw.split(',') if c.strip()]
    
    db = get_db()
    old_status = db.execute("SELECT status FROM goals WHERE id = ?", (goal_id,)).fetchone()
    if not old_status:
        abort(404)
    
    db.execute("UPDATE goals SET status = 'hidden' WHERE id = ?", (goal_id,))
    details = _json.dumps({
        'categories': categories,
        'comment': comment,
        'old_value': old_status['status'],
        'new_value': 'hidden'
    }, ensure_ascii=False)
    db.execute(
        "INSERT INTO admin_actions (admin_id, action_type, target_type, target_id, details) VALUES (?, 'hide_goal', 'goal', ?, ?)",
        (admin['id'], str(goal_id), details)
    )
    db.commit()
    flash(f'Цель #{goal_id} скрыта.', 'success')
    return redirect(url_for('metrics_goal_detail', goal_id=goal_id))


@app.route('/_metrics/goals/<int:goal_id>/restore', methods=['POST'])
@admin_required
def metrics_goal_restore(goal_id):
    """Восстановить цель."""
    import json as _json
    admin = get_current_user()
    db = get_db()
    old_status = db.execute("SELECT status FROM goals WHERE id = ?", (goal_id,)).fetchone()
    if not old_status:
        abort(404)
    
    db.execute("UPDATE goals SET status = 'active' WHERE id = ?", (goal_id,))
    details = _json.dumps({
        'old_value': old_status['status'],
        'new_value': 'active'
    }, ensure_ascii=False)
    db.execute(
        "INSERT INTO admin_actions (admin_id, action_type, target_type, target_id, details) VALUES (?, 'restore_goal', 'goal', ?, ?)",
        (admin['id'], str(goal_id), details)
    )
    db.commit()
    flash(f'Цель #{goal_id} восстановлена.', 'success')
    return redirect(url_for('metrics_goal_detail', goal_id=goal_id))


@app.route('/_metrics/reports')
@admin_required
def metrics_reports():
    """Список всех жалоб."""
    user = get_current_user()
    db = get_db()
    
    search = request.args.get('search', '').strip()
    
    query = """
        SELECT r.*, 
            g.title as goal_title,
            g.status as goal_status,
            g.user_id as goal_author_id,
            COALESCE(NULLIF(au.name, ''), au.phone) as goal_author_name,
            COALESCE(NULLIF(ru.name, ''), ru.phone) as reporter_name,
            ru.id as reporter_user_id
        FROM reports r
        JOIN goals g ON r.goal_id = g.id
        JOIN users au ON g.user_id = au.id
        LEFT JOIN users ru ON r.reporter_id = ru.id
        WHERE 1=1
    """
    params = []
    
    if search:
        query += " AND (g.title LIKE ? OR CAST(r.goal_id AS TEXT) = ? OR CAST(r.reporter_id AS TEXT) = ?)"
        params.extend([f'%{search}%', search, search])
    
    query += " ORDER BY r.id DESC LIMIT 200"
    reports = db.execute(query, params).fetchall()
    
    # Группируем по цели — сколько жалоб на каждую
    goal_reports = {}
    for r in reports:
        gid = r['goal_id']
        if gid not in goal_reports:
            goal_reports[gid] = {
                'goal_id': gid,
                'goal_title': r['goal_title'],
                'goal_status': r['goal_status'],
                'goal_author_name': r['goal_author_name'],
                'goal_author_id': r['goal_author_id'],
                'count': 0
            }
        goal_reports[gid]['count'] += 1
    
    # Счётчики
    counts_total = len(reports)
    counts_today = db.execute("SELECT COUNT(*) FROM reports WHERE date(created_at) = date('now')").fetchone()[0]
    counts_goals_with_3 = db.execute("""
        SELECT COUNT(*) FROM (
            SELECT goal_id FROM reports GROUP BY goal_id HAVING COUNT(*) >= 3
        )
    """).fetchone()[0]
    
    counts = get_sidebar_counts(db)
    
    
    return render_template('_metrics/reports.html',
        user=user,
        active='reports',
        counts=counts,
        reports=reports,
        goal_reports=list(goal_reports.values()),
        search=search,
        counts_total=counts_total,
        counts_today=counts_today,
        counts_goals_with_3=counts_goals_with_3,
    )


@app.route('/_metrics/dialogs')
@admin_required
def metrics_dialogs():
    """Список диалогов с пользователями."""
    user = get_current_user()
    db = get_db()
    
    status_filter = request.args.get('status', 'open')
    
    query = """
        SELECT d.*,
            COALESCE(NULLIF(u.name, ''), u.phone) as user_name,
            u.phone as user_phone,
            (SELECT COUNT(*) FROM admin_dialog_messages WHERE dialog_id = d.id) as messages_count,
            (SELECT COUNT(*) FROM admin_dialog_messages WHERE dialog_id = d.id AND sender_type = 'user' AND is_read = 0) as unread_count,
            (SELECT message FROM admin_dialog_messages WHERE dialog_id = d.id ORDER BY id DESC LIMIT 1) as last_message,
            (SELECT created_at FROM admin_dialog_messages WHERE dialog_id = d.id ORDER BY id DESC LIMIT 1) as last_message_at
        FROM admin_dialogs d
        JOIN users u ON d.user_id = u.id
        WHERE 1=1
    """
    params = []
    
    if status_filter != 'all':
        query += " AND d.status = ?"
        params.append(status_filter)
    
    query += " ORDER BY d.id DESC LIMIT 100"
    dialogs = db.execute(query, params).fetchall()
    
    # Счётчики
    counts_open = db.execute("SELECT COUNT(*) FROM admin_dialogs WHERE status = 'open'").fetchone()[0]
    counts_closed = db.execute("SELECT COUNT(*) FROM admin_dialogs WHERE status = 'closed'").fetchone()[0]
    counts_unread = db.execute("SELECT COUNT(*) FROM admin_dialog_messages WHERE sender_type = 'user' AND is_read = 0").fetchone()[0]
    
    counts = get_sidebar_counts(db)
    
    
    return render_template('_metrics/dialogs.html',
        user=user,
        active='dialogs',
        counts=counts,
        dialogs=dialogs,
        status_filter=status_filter,
        counts_open=counts_open,
        counts_closed=counts_closed,
        counts_unread=counts_unread,
    )


@app.route('/_metrics/dialogs/<int:dialog_id>')
@admin_required
def metrics_dialog_detail(dialog_id):
    """Карточка диалога с чатом."""
    admin = get_current_user()
    db = get_db()
    
    d = db.execute("""
        SELECT d.*, 
            COALESCE(NULLIF(u.name, ''), u.phone) as user_name,
            u.phone as user_phone,
            u.id as user_id
        FROM admin_dialogs d
        JOIN users u ON d.user_id = u.id
        WHERE d.id = ?
    """, (dialog_id,)).fetchone()
    
    if not d:
        abort(404)
    
    messages = db.execute("""
        SELECT * FROM admin_dialog_messages 
        WHERE dialog_id = ? 
        ORDER BY id ASC
    """, (dialog_id,)).fetchall()
    
    # Помечаем сообщения пользователя как прочитанные
    db.execute(
        "UPDATE admin_dialog_messages SET is_read = 1 WHERE dialog_id = ? AND sender_type = 'user' AND is_read = 0",
        (dialog_id,)
    )
    db.commit()
    
    counts = get_sidebar_counts(db)
    
    
    return render_template('_metrics/dialog_detail.html',
        user=admin,
        active='dialogs',
        counts=counts,
        d=d,
        messages=messages,
    )


@app.route('/_metrics/dialogs/<int:dialog_id>/send', methods=['POST'])
@admin_required
def metrics_dialog_send(dialog_id):
    """Отправить сообщение в диалог."""
    admin = get_current_user()
    data = request.get_json(silent=True) or {}
    text = (data.get('message') or '').strip()
    if not text:
        return jsonify({'ok': False, 'error': 'Пустое сообщение'}), 400
    
    db = get_db()
    d = db.execute("SELECT * FROM admin_dialogs WHERE id = ?", (dialog_id,)).fetchone()
    if not d:
        return jsonify({'ok': False, 'error': 'Диалог не найден'}), 404
    if d['status'] == 'closed':
        return jsonify({'ok': False, 'error': 'Диалог закрыт'}), 400
    
    db.execute(
        "INSERT INTO admin_dialog_messages (dialog_id, sender_type, sender_id, message) VALUES (?, 'admin', ?, ?)",
        (dialog_id, admin['id'], text)
    )
    db.commit()
    
    return jsonify({'ok': True})


@app.route('/_metrics/dialogs/<int:dialog_id>/messages')
@admin_required
def metrics_dialog_messages(dialog_id):
    """JSON со всеми сообщениями диалога для polling."""
    admin = get_current_user()
    db = get_db()
    
    d = db.execute("""
        SELECT d.*, COALESCE(NULLIF(u.name, ''), u.phone) as user_name
        FROM admin_dialogs d
        JOIN users u ON d.user_id = u.id
        WHERE d.id = ?
    """, (dialog_id,)).fetchone()
    
    if not d:
        return jsonify({'ok': False, 'error': 'Диалог не найден'}), 404
    
    messages = db.execute("""
        SELECT id, sender_type, sender_id, message, created_at
        FROM admin_dialog_messages
        WHERE dialog_id = ?
        ORDER BY id ASC
    """, (dialog_id,)).fetchall()
    
    # Помечаем сообщения пользователя прочитанными
    db.execute(
        "UPDATE admin_dialog_messages SET is_read = 1 WHERE dialog_id = ? AND sender_type = 'user' AND is_read = 0",
        (dialog_id,)
    )
    db.commit()
    
    # Форматируем даты
    def fmt_date(s):
        if not s: return ''
        try:
            parts = s[:16].split(' ')
            date_parts = parts[0].split('-')
            time_part = parts[1][:5] if len(parts) > 1 else ''
            return f"{date_parts[2]}.{date_parts[1]}.{date_parts[0]} {time_part}"
        except:
            return s[:16]
    
    return jsonify({
        'ok': True,
        'user_name': d['user_name'],
        'messages': [{
            'id': m['id'],
            'sender_type': m['sender_type'],
            'sender_id': m['sender_id'],
            'message': m['message'],
            'created_at': m['created_at'],
            'created_at_formatted': fmt_date(m['created_at'])
        } for m in messages]
    })


@app.route('/_metrics/dialogs/<int:dialog_id>/close', methods=['POST'])
@admin_required
def metrics_dialog_close(dialog_id):
    """Закрыть диалог."""
    import json as _json
    admin = get_current_user()
    db = get_db()
    
    # Атомарно: только если ещё open
    cur = db.execute(
        "UPDATE admin_dialogs SET status = 'closed', closed_at = datetime('now') WHERE id = ? AND status = 'open'",
        (dialog_id,)
    )
    if cur.rowcount == 0:
        flash('Диалог уже закрыт или не найден.', 'warning')
        return redirect(url_for('metrics_dialogs'))
    
    details = _json.dumps({'old_value': 'open', 'new_value': 'closed'}, ensure_ascii=False)
    db.execute(
        "INSERT INTO admin_actions (admin_id, action_type, target_type, target_id, details) VALUES (?, 'close_dialog', 'dialog', ?, ?)",
        (admin['id'], str(dialog_id), details)
    )
    db.commit()
    flash('Диалог закрыт.', 'success')
    return redirect(url_for('metrics_dialogs'))


@app.route('/_metrics/dialogs/new', methods=['GET', 'POST'])
@admin_required
def metrics_dialog_new():
    """Создать новый диалог с пользователем."""
    admin = get_current_user()
    
    if request.method == 'POST':
        try:
            user_id = int(request.form.get('user_id', '0'))
        except (ValueError, TypeError):
            flash('Неверный ID пользователя.', 'danger')
            return redirect(url_for('metrics_dialog_new'))
        
        subject = request.form.get('subject', '').strip()[:200]
        message = request.form.get('message', '').strip()[:2000]
        
        if not user_id or not message:
            flash('Заполните все поля.', 'danger')
            return redirect(url_for('metrics_dialog_new'))
        
        db = get_db()
        u = db.execute("SELECT id FROM users WHERE id = ?", (user_id,)).fetchone()
        if not u:
            flash('Пользователь не найден.', 'danger')
            return redirect(url_for('metrics_dialog_new'))
        
        db.execute(
            "INSERT INTO admin_dialogs (user_id, admin_id, subject, status) VALUES (?, ?, ?, 'open')",
            (user_id, admin['id'], subject or None)
        )
        dialog_id = db.execute("SELECT last_insert_rowid()").fetchone()[0]
        
        db.execute(
            "INSERT INTO admin_dialog_messages (dialog_id, sender_type, sender_id, message) VALUES (?, 'admin', ?, ?)",
            (dialog_id, admin['id'], message)
        )
        
        db.execute(
            "INSERT INTO admin_actions (admin_id, action_type, target_type, target_id, details) VALUES (?, 'create_dialog', 'user', ?, ?)",
            (admin['id'], str(user_id), '{"dialog_id": ' + str(dialog_id) + '}')
        )
        
        db.commit()
        flash('Диалог создан.', 'success')
        return redirect(url_for('metrics_dialog_detail', dialog_id=dialog_id))
    
    # GET — форма создания
    user_id = request.args.get('user_id', '').strip()
    db = get_db()
    u = None
    if user_id:
        u = db.execute("SELECT * FROM users WHERE id = ?", (user_id,)).fetchone()
    
    counts = get_sidebar_counts(db)
    
    return render_template('_metrics/dialog_new.html',
        user=admin,
        active='dialogs',
        counts=counts,
        u=u,
    )


# ============================================
# API диалогов для пользователя
# ============================================

@app.route('/api/dialogs/<int:dialog_id>')
@login_required
def api_dialog_get(dialog_id):
    """Получить сообщения диалога."""
    user = get_current_user()
    db = get_db()
    
    d = db.execute(
        "SELECT * FROM admin_dialogs WHERE id = ? AND user_id = ?",
        (dialog_id, user['id'])
    ).fetchone()
    
    if not d:
        return jsonify({'ok': False, 'error': 'Диалог не найден'}), 404
    
    messages = db.execute(
        "SELECT id, sender_type, message, created_at FROM admin_dialog_messages WHERE dialog_id = ? ORDER BY id ASC",
        (dialog_id,)
    ).fetchall()
    
    db.execute(
        "UPDATE admin_dialog_messages SET is_read = 1 WHERE dialog_id = ? AND sender_type = 'admin' AND is_read = 0",
        (dialog_id,)
    )
    db.commit()
    
    return jsonify({
        'ok': True,
        'dialog': {
            'id': d['id'],
            'subject': d['subject'],
            'status': d['status'],
        },
        'messages': [dict(m) for m in messages]
    })


@app.route('/api/dialogs/<int:dialog_id>/send', methods=['POST'])
@login_required
def api_dialog_send(dialog_id):
    """Пользователь отправляет сообщение в диалог."""
    user = get_current_user()
    data = request.get_json(silent=True) or {}
    text = (data.get('message') or '').strip()
    
    if not text:
        return jsonify({'ok': False, 'error': 'Пустое сообщение'}), 400
    if len(text) > 2000:
        return jsonify({'ok': False, 'error': 'Слишком длинное сообщение'}), 400
    
    from content_guard import check as cg_check
    r = cg_check(text, 'dialog_message')
    log_guard(r, text, 'dialog_message', 'dialog_reply')
    if r.get('status') == 'blocked':
        return jsonify({'ok': False, 'error': r.get('message', 'Сообщение не прошло проверку')}), 400
    
    db = get_db()
    d = db.execute(
        "SELECT * FROM admin_dialogs WHERE id = ? AND user_id = ?",
        (dialog_id, user['id'])
    ).fetchone()
    
    if not d:
        return jsonify({'ok': False, 'error': 'Диалог не найден'}), 404
    if d['status'] == 'closed':
        return jsonify({'ok': False, 'error': 'Диалог закрыт'}), 400
    
    db.execute(
        "INSERT INTO admin_dialog_messages (dialog_id, sender_type, sender_id, message) VALUES (?, 'user', ?, ?)",
        (dialog_id, user['id'], text)
    )
    db.commit()
    
    return jsonify({'ok': True})


@app.route('/api/dialogs/check')
@login_required
def api_dialogs_check():
    """Проверка новых сообщений во всех открытых диалогах (для polling)."""
    user = get_current_user()
    db = get_db()
    
    dialogs = db.execute("""
        SELECT d.id, d.subject,
               (SELECT COUNT(*) FROM admin_dialog_messages 
                WHERE dialog_id = d.id AND sender_type = 'admin' AND is_read = 0) as unread
        FROM admin_dialogs d
        WHERE d.user_id = ? AND d.status = 'open'
    """, (user['id'],)).fetchall()
    
    
    total_unread = sum(d['unread'] for d in dialogs)
    
    return jsonify({
        'ok': True,
        'unread': total_unread,
        'dialogs': [{'id': d['id'], 'subject': d['subject'], 'unread': d['unread']} for d in dialogs]
    })


@app.route('/_metrics/moderation')
@admin_required
def metrics_moderation():
    """Очередь модерации — pending цели."""
    user = get_current_user()
    db = get_db()
    
    pending_rows = db.execute("""
        SELECT g.*, 
            COALESCE(NULLIF(u.name, ''), u.phone) as author_name,
            u.phone as author_phone,
            u.id as author_id,
            u.trust_score as author_trust,
            u.identity_verified as author_verified,
            (SELECT COUNT(*) FROM content_guard_log WHERE user_id = g.user_id) as author_flags
        FROM goals g
        JOIN users u ON g.user_id = u.id
        WHERE g.moderation_status = 'pending'
        ORDER BY g.id DESC
    """).fetchall()
    
    # Парсим photos в Python
    import json as _json
    pending = []
    for row in pending_rows:
        d = dict(row)
        photos = []
        if d.get('photos'):
            try:
                photos = _json.loads(d['photos'])
            except:
                pass
        if not photos and d.get('photo_url'):
            photos = [d['photo_url']]
        d['photos_list'] = photos
        
        # История предыдущих отказов (из admin_actions)
        prev_rejections = db.execute("""
            SELECT details, created_at FROM admin_actions
            WHERE action_type = 'reject_goal' AND target_type = 'goal' AND target_id = ?
            ORDER BY id DESC
        """, (str(d['id']),)).fetchall()
        
        d['prev_rejections'] = []
        for pr in prev_rejections:
            try:
                det = _json.loads(pr['details'])
                d['prev_rejections'].append({
                    'categories': det.get('categories', []),
                    'comment': det.get('comment', ''),
                    'created_at': pr['created_at']
                })
            except:
                pass
        
        pending.append(d)
    
    # История модерации (последние 20)
    history = db.execute("""
        SELECT g.id, g.title, g.moderation_status, g.moderation_comment, g.moderated_at,
               g.is_resubmission, g.resubmission_count,
               COALESCE(NULLIF(u.name, ''), u.phone) as author_name
        FROM goals g
        JOIN users u ON g.user_id = u.id
        WHERE g.moderation_status IN ('approved', 'rejected')
        ORDER BY g.moderated_at DESC LIMIT 20
    """).fetchall()
    
    counts = get_sidebar_counts(db)
    
    
    return render_template('_metrics/moderation.html',
        user=user,
        active='moderation',
        counts=counts,
        pending=pending,
        history=history,
    )


@app.route('/_metrics/moderation/<int:goal_id>/approve', methods=['POST'])
@admin_required
def metrics_moderation_approve(goal_id):
    """Одобрить цель."""
    admin = get_current_user()
    db = get_db()
    
    # Проверка: автор не забанен
    goal = db.execute("SELECT user_id, title FROM goals WHERE id = ?", (goal_id,)).fetchone()
    if not goal:
        abort(404)
    author = db.execute("SELECT is_banned FROM users WHERE id = ?", (goal['user_id'],)).fetchone()
    if author and author['is_banned']:
        flash('Автор цели забанен. Разбаньте его перед одобрением.', 'danger')
        return redirect(url_for('metrics_moderation'))
    
    # Атомарное одобрение: только если ещё pending
    cur = db.execute(
        "UPDATE goals SET moderation_status = 'approved', status = 'active', moderation_comment = NULL, rejection_categories = NULL, moderated_at = datetime('now'), moderated_by = ? WHERE id = ? AND moderation_status = 'pending'",
        (admin['id'], goal_id)
    )
    if cur.rowcount == 0:
        flash('Цель уже рассмотрена (не в статусе pending).', 'warning')
        return redirect(url_for('metrics_moderation'))
    
    db.execute(
        "INSERT INTO admin_actions (admin_id, action_type, target_type, target_id, details) VALUES (?, 'approve_goal', 'goal', ?, NULL)",
        (admin['id'], str(goal_id))
    )
    
    # Уведомление автору
    db.execute(
        "INSERT INTO notifications (user_id, type, title, message, action_url) VALUES (?, 'goal_approved', 'Цель одобрена', 'Цель одобрена и появится в ленте.', ?)",
        (goal['user_id'], '/goal/' + str(goal_id))
    )
    db.commit()
    flash('Цель одобрена.', 'success')
    return redirect(url_for('metrics_moderation'))


@app.route('/_metrics/moderation/<int:goal_id>/reject', methods=['POST'])
@admin_required
def metrics_moderation_reject(goal_id):
    """Отклонить цель с категориями и комментарием."""
    import json as _json
    admin = get_current_user()
    categories_raw = (request.form.get('categories') or '').strip()
    comment = (request.form.get('comment') or '').strip()
    
    if not categories_raw:
        flash('Выберите минимум одну категорию.', 'danger')
        return redirect(url_for('metrics_moderation'))
    if not comment:
        flash('Укажите комментарий для автора.', 'danger')
        return redirect(url_for('metrics_moderation'))
    
    categories = [c.strip() for c in categories_raw.split(',') if c.strip()]
    comment = comment[:1000]
    
    db = get_db()
    old_goal = db.execute("SELECT user_id, moderation_status FROM goals WHERE id = ?", (goal_id,)).fetchone()
    if not old_goal:
        abort(404)
    
    # Атомарное отклонение: только если ещё pending
    cur = db.execute(
        "UPDATE goals SET moderation_status = 'rejected', status = 'failed', moderation_comment = ?, rejection_categories = ?, moderated_at = datetime('now'), moderated_by = ? WHERE id = ? AND moderation_status = 'pending'",
        (comment, _json.dumps(categories, ensure_ascii=False), admin['id'], goal_id)
    )
    if cur.rowcount == 0:
        flash('Цель уже рассмотрена (не в статусе pending).', 'warning')
        return redirect(url_for('metrics_moderation'))
    
    details = _json.dumps({
        'categories': categories,
        'comment': comment,
        'old_value': old_goal['moderation_status'],
        'new_value': 'rejected'
    }, ensure_ascii=False)
    db.execute(
        "INSERT INTO admin_actions (admin_id, action_type, target_type, target_id, details) VALUES (?, 'reject_goal', 'goal', ?, ?)",
        (admin['id'], str(goal_id), details)
    )
    # Уведомление автору
    db.execute(
        "INSERT INTO notifications (user_id, type, title, message, action_url) VALUES (?, 'goal_rejected', 'Цель отклонена', ?, ?)",
        (old_goal['user_id'], 'Причина: ' + comment, '/goal/' + str(goal_id))
    )
    db.commit()
    flash('Цель отклонена.', 'success')
    return redirect(url_for('metrics_moderation'))


@app.route('/_metrics/suspicious')
@admin_required
def metrics_suspicious():
    """Список подозрительной активности."""
    user = get_current_user()
    db = get_db()
    
    status_filter = request.args.get('status', 'new')
    type_filter = request.args.get('type', 'all')
    
    query = """
        SELECT s.*, 
            COALESCE(NULLIF(u.name, ''), u.phone) as user_name,
            u.phone as user_phone,
            u.is_banned as user_banned
        FROM suspicious_activity s
        LEFT JOIN users u ON s.user_id = u.id
        WHERE 1=1
    """
    params = []
    
    if status_filter != 'all':
        query += " AND s.status = ?"
        params.append(status_filter)
    
    if type_filter != 'all':
        query += " AND s.activity_type = ?"
        params.append(type_filter)
    
    query += " ORDER BY s.id DESC LIMIT 200"
    activities = db.execute(query, params).fetchall()
    
    # Счётчики
    counts_new = db.execute("SELECT COUNT(*) FROM suspicious_activity WHERE status = 'new'").fetchone()[0]
    counts_reviewed = db.execute("SELECT COUNT(*) FROM suspicious_activity WHERE status = 'reviewed'").fetchone()[0]
    counts_resolved = db.execute("SELECT COUNT(*) FROM suspicious_activity WHERE status = 'resolved'").fetchone()[0]
    
    # Типы
    types = db.execute("SELECT DISTINCT activity_type FROM suspicious_activity ORDER BY activity_type").fetchall()
    
    counts = get_sidebar_counts(db)
    
    
    # Человекопонятные названия
    type_names = {
        'mass_goals': 'Много целей за 24ч',
        'mass_registration': 'Массовые регистрации с IP',
        'many_flags': 'Много флагов Content Guard',
        'no_confirm_48h': 'Не подтверждает донаты 48ч+',
        'many_reports': 'Много жалоб на юзера',
    }
    
    return render_template('_metrics/suspicious.html',
        user=user,
        active='suspicious',
        counts=counts,
        activities=activities,
        status_filter=status_filter,
        type_filter=type_filter,
        types=[t['activity_type'] for t in types],
        type_names=type_names,
        counts_new=counts_new,
        counts_reviewed=counts_reviewed,
        counts_resolved=counts_resolved,
    )


@app.route('/_metrics/suspicious/<int:activity_id>/resolve', methods=['POST'])
@admin_required
def metrics_suspicious_resolve(activity_id):
    """Закрыть подозрительную активность с категорией."""
    import json as _json
    admin = get_current_user()
    categories_raw = (request.form.get('categories') or '').strip()
    comment = (request.form.get('comment') or '').strip()
    
    if not categories_raw:
        flash('Выберите категорию закрытия.', 'danger')
        return redirect(url_for('metrics_suspicious'))
    
    categories = [c.strip() for c in categories_raw.split(',') if c.strip()]
    
    db = get_db()
    old = db.execute("SELECT status FROM suspicious_activity WHERE id = ?", (activity_id,)).fetchone()
    if not old:
        abort(404)
    
    # Атомарно: только если ещё new или reviewed
    cur = db.execute(
        "UPDATE suspicious_activity SET status = 'resolved' WHERE id = ? AND status IN ('new', 'reviewed')",
        (activity_id,)
    )
    if cur.rowcount == 0:
        flash('Уже обработано.', 'warning')
        return redirect(url_for('metrics_suspicious'))
    
    details = _json.dumps({
        'categories': categories,
        'comment': comment,
        'old_value': old['status'],
        'new_value': 'resolved'
    }, ensure_ascii=False)
    db.execute(
        "INSERT INTO admin_actions (admin_id, action_type, target_type, target_id, details) VALUES (?, 'resolve_suspicious', 'suspicious', ?, ?)",
        (admin['id'], str(activity_id), details)
    )
    db.commit()
    flash('Помечено как обработанное.', 'success')
    return redirect(url_for('metrics_suspicious'))


@app.route('/_metrics/suspicious/<int:activity_id>/review', methods=['POST'])
@admin_required
def metrics_suspicious_review(activity_id):
    """Пометить как reviewed (взято в работу)."""
    import json as _json
    admin = get_current_user()
    db = get_db()
    
    # Атомарно: только если ещё 'new'
    cur = db.execute(
        "UPDATE suspicious_activity SET status = 'reviewed' WHERE id = ? AND status = 'new'",
        (activity_id,)
    )
    if cur.rowcount == 0:
        flash('Уже взято в работу или обработано.', 'warning')
        return redirect(url_for('metrics_suspicious'))
    
    # Аудит
    details = _json.dumps({'old_value': 'new', 'new_value': 'reviewed'}, ensure_ascii=False)
    db.execute(
        "INSERT INTO admin_actions (admin_id, action_type, target_type, target_id, details) VALUES (?, 'review_suspicious', 'suspicious', ?, ?)",
        (admin['id'], str(activity_id), details)
    )
    db.commit()
    flash('Взято в работу.', 'info')
    return redirect(url_for('metrics_suspicious'))


@app.route('/_metrics/logs')
@admin_required
def metrics_logs():
    """Объединённая лента событий."""
    user = get_current_user()
    db = get_db()
    
    # Фильтры
    period = request.args.get('period', '24h')  # 1h, 24h, 7d, 30d, all
    source = request.args.get('source', 'all')  # all, actions, flags, donations, goals, reports
    search = request.args.get('search', '').strip()
    flagged = request.args.get('flagged', '0') == '1'
    
    period_map = {
        '1h': '-1 hour',
        '24h': '-24 hours',
        '7d': '-7 days',
        '30d': '-30 days',
    }
    
    time_filter = period_map.get(period)
    
    events = []
    
    # Словарь человеческих названий событий
    event_labels = {
        'login_attempt': ('🔐', 'Вход в аккаунт'),
        'registration': ('🎉', 'Регистрация'),
        'transfer_confirmed_donor': ('💸', 'Донатор сообщил о переводе'),
        'transfer_confirmed_recipient': ('✅', 'Получатель подтвердил перевод'),
        'goal_created': ('🎯', 'Создание цели'),
        'goal_approved': ('✓', 'Цель одобрена'),
        'goal_rejected': ('✗', 'Цель отклонена'),
        'report_created': ('⚠️', 'Жалоба на цель'),
        'dialog_message': ('💬', 'Сообщение в диалоге'),
    }
    
    # 1. Analytics events (входы, действия)
    if source in ('all', 'actions'):
        q = "SELECT id, user_id, event_type as type, event_data as data, created_at FROM analytics_events WHERE 1=1"
        params = []
        if time_filter:
            q += " AND created_at > datetime('now', ?)"
            params.append(time_filter)
        q += " ORDER BY id DESC LIMIT 100"
        for r in db.execute(q, params).fetchall():
            icon, label = event_labels.get(r['type'], ('🔵', r['type']))
            events.append({
                'time': r['created_at'],
                'source': 'analytics',
                'icon': icon,
                'label': label,
                'user_id': r['user_id'],
                'data': r['data'],
            })
    
    # 2. Content Guard flags
    if source in ('all', 'flags'):
        if flagged:
            q = "SELECT id, user_id, found_word, category, status, original_text, created_at FROM content_guard_log WHERE status = 'flagged'"
        else:
            q = "SELECT id, user_id, found_word, category, status, original_text, created_at FROM content_guard_log WHERE status IN ('blocked', 'flagged')"
        params = []
        if time_filter:
            q += " AND created_at > datetime('now', ?)"
            params.append(time_filter)
        q += " ORDER BY id DESC LIMIT 100"
        for r in db.execute(q, params).fetchall():
            events.append({
                'time': r['created_at'],
                'source': 'flag',
                'icon': '🚩',
                'label': f"FLAG: {r['found_word']} ({r['category']})",
                'user_id': r['user_id'],
                'data': r['original_text'][:100] if r['original_text'] else '',
            })
    
    # 3. Донаты
    if source in ('all', 'donations'):
        q = "SELECT id, donor_id as user_id, amount_reported, status, donor_confirmed_at as created_at FROM donations WHERE 1=1"
        params = []
        if time_filter:
            q += " AND donor_confirmed_at > datetime('now', ?)"
            params.append(time_filter)
        q += " ORDER BY id DESC LIMIT 100"
        for r in db.execute(q, params).fetchall():
            events.append({
                'time': r['created_at'],
                'source': 'donation',
                'icon': '💰',
                'label': f"Донат {(r['amount_reported'] or 0):.0f} ₽",
                'user_id': r['user_id'],
                'data': f"status: {r['status']}",
            })
    
    # 4. Создание целей
    if source in ('all', 'goals'):
        q = "SELECT id, user_id, title, type, status, created_at FROM goals WHERE 1=1"
        params = []
        if time_filter:
            q += " AND created_at > datetime('now', ?)"
            params.append(time_filter)
        q += " ORDER BY id DESC LIMIT 100"
        for r in db.execute(q, params).fetchall():
            events.append({
                'time': r['created_at'],
                'source': 'goal',
                'icon': '🎯',
                'label': f"Цель: {(r['title'] or '')[:60]}",
                'user_id': r['user_id'],
                'data': f"type: {r['type']}, status: {r['status']}",
            })
    
    # 5. Жалобы
    if source in ('all', 'reports'):
        q = "SELECT id, reporter_id as user_id, goal_id, reason, created_at FROM reports WHERE 1=1"
        params = []
        if time_filter:
            q += " AND created_at > datetime('now', ?)"
            params.append(time_filter)
        q += " ORDER BY id DESC LIMIT 100"
        for r in db.execute(q, params).fetchall():
            events.append({
                'time': r['created_at'],
                'source': 'report',
                'icon': '⚠️',
                'label': f"Жалоба на цель #{r['goal_id']}",
                'user_id': r['user_id'],
                'data': r['reason'],
            })
    
    # Сортируем по времени
    # Если flagged=1 — оставляем только события-флаги
    if flagged:
        events = [e for e in events if e['source'] == 'flag']
    
    # Нормализация дат — разные источники могут давать разный формат
    def _norm_dt(s):
        return (s or '').replace('T', ' ')[:19]
    
    events.sort(key=lambda x: _norm_dt(x['time']), reverse=True)
    events = events[:300]
    
    # Обогащаем данными юзера
    user_ids = set(e['user_id'] for e in events if e['user_id'])
    users_map = {}
    if user_ids:
        placeholders = ','.join('?' * len(user_ids))
        rows = db.execute(f"SELECT id, phone, name FROM users WHERE id IN ({placeholders})", list(user_ids)).fetchall()
        for r in rows:
            users_map[r['id']] = {'phone': r['phone'], 'name': r['name']}
    
    for e in events:
        u = users_map.get(e['user_id'])
        e['user_label'] = (u['name'] or u['phone']) if u else '—'
    
    counts = get_sidebar_counts(db)
    
    
    return render_template('_metrics/logs.html',
        user=user,
        active='logs',
        counts=counts,
        events=events,
        period=period,
        source=source,
        search=search,
    )


@app.route('/_metrics/settings')
@admin_required
def metrics_settings():
    """Настройки системы."""
    user = get_current_user()
    db = get_db()
    
    # Feature flags
    flags = db.execute("SELECT * FROM feature_flags ORDER BY flag_key").fetchall()
    
    # Content Guard info
    import os
    import json
    cg_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'data', 'content_guard.json')
    cg_stats = {'block_categories': 0, 'flag_categories': 0, 'contexts': 0, 'total_words': 0}
    cg_loaded = False
    if os.path.exists(cg_path):
        try:
            with open(cg_path, 'r', encoding='utf-8') as f:
                cg = json.load(f)
            cg_stats['block_categories'] = len(cg.get('block', {}))
            cg_stats['flag_categories'] = len(cg.get('flag', {}))
            cg_stats['contexts'] = len(cg.get('contexts', {}))
            for cat in cg.get('block', {}).values():
                cg_stats['total_words'] += len(cat.get('words', [])) + len(cat.get('patterns', []))
            for cat in cg.get('flag', {}).values():
                cg_stats['total_words'] += len(cat.get('words', [])) + len(cat.get('patterns', []))
            cg_loaded = True
        except Exception as e:
            cg_loaded = False
    
    # Системная информация
    db_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'data', 'pomogay.db')
    db_size = os.path.getsize(db_path) if os.path.exists(db_path) else 0
    
    # Счётчики записей в основных таблицах
    stats = {
        'users': db.execute("SELECT COUNT(*) FROM users").fetchone()[0],
        'goals': db.execute("SELECT COUNT(*) FROM goals").fetchone()[0],
        'donations': db.execute("SELECT COUNT(*) FROM donations").fetchone()[0],
        'flags': db.execute("SELECT COUNT(*) FROM content_guard_log").fetchone()[0],
        'suspicious': db.execute("SELECT COUNT(*) FROM suspicious_activity").fetchone()[0],
        'dialogs': db.execute("SELECT COUNT(*) FROM admin_dialogs").fetchone()[0],
    }
    
    # Настройки из БД
    settings_rows = db.execute("SELECT * FROM settings ORDER BY key").fetchall()
    settings = {}
    for r in settings_rows:
        settings[r['key']] = {'value': r['value'], 'description': r['description'], 'updated_at': r['updated_at']}
    
    counts = get_sidebar_counts(db)
    
    return render_template('_metrics/settings.html',
        user=user,
        active='settings',
        counts=counts,
        flags=flags,
        cg_stats=cg_stats,
        cg_loaded=cg_loaded,
        db_size=db_size,
        stats=stats,
        settings=settings,
    )


@app.route('/_metrics/settings/save', methods=['POST'])
@admin_required
def metrics_settings_save():
    """Сохранить настройки из формы."""
    admin = get_current_user()
    db = get_db()
    
    # Все ключи из settings
    rows = db.execute("SELECT key FROM settings").fetchall()
    updated = 0
    for r in rows:
        key = r['key']
        # Boolean через чекбоксы — если не пришёл, значит выключено
        if key in ('notifications_enabled', 'reminders_enabled', 'registration_open', 'goals_open'):
            new_value = '1' if request.form.get(key) == '1' else '0'
        else:
            new_value = request.form.get(key, '').strip()
            if not new_value:
                continue
        
        old_val = db.execute("SELECT value FROM settings WHERE key = ?", (key,)).fetchone()['value']
        if old_val != new_value:
            set_setting(db, key, new_value, admin['id'])
            updated += 1
    
    db.commit()
    flash(f'Сохранено изменений: {updated}', 'success')
    return redirect(url_for('metrics_settings'))


@app.route('/_metrics/settings/flag/<int:flag_id>/toggle', methods=['POST'])
@admin_required
def metrics_settings_toggle_flag(flag_id):
    """Переключить feature flag."""
    admin = get_current_user()
    db = get_db()
    flag = db.execute("SELECT * FROM feature_flags WHERE id = ?", (flag_id,)).fetchone()
    if not flag:
        flash('Флаг не найден.', 'danger')
        return redirect(url_for('metrics_settings'))
    
    new_value = 0 if flag['enabled'] else 1
    db.execute("UPDATE feature_flags SET enabled = ?, updated_at = datetime('now') WHERE id = ?", (new_value, flag_id))
    db.execute(
        "INSERT INTO admin_actions (admin_id, action_type, target_type, target_id, details) VALUES (?, 'toggle_flag', 'feature_flag', ?, ?)",
        (admin['id'], str(flag_id), '{"key": "' + flag['flag_key'] + '", "new": ' + str(new_value) + '}')
    )
    db.commit()
    flash(f'Флаг {flag["flag_key"]} переключён.', 'success')
    return redirect(url_for('metrics_settings'))


@app.route('/goal/<int:goal_id>/edit', methods=['GET', 'POST'])
@login_required
def goal_edit(goal_id):
    """Редактирование отклонённой цели."""
    user = get_current_user()
    db = get_db()
    goal = db.execute("SELECT * FROM goals WHERE id = ?", (goal_id,)).fetchone()
    
    if not goal:
        flash('Цель не найдена.', 'danger')
        return redirect(url_for('goals_list'))
    
    if goal['user_id'] != user['id']:
        abort(404)
    
    if goal['status'] not in ('failed', 'draft'):
        flash('Эту цель нельзя редактировать.', 'warning')
        return redirect(url_for('goal_page', goal_id=goal_id))
    
    import json as _json
    photos_list = []
    if goal['photos']:
        try:
            photos_list = _json.loads(goal['photos'])
        except Exception:
            photos_list = []
    if not photos_list and goal['photo_url']:
        photos_list = [goal['photo_url']]
    
    rejection_cats = []
    if goal['rejection_categories']:
        try:
            rejection_cats = _json.loads(goal['rejection_categories'])
        except Exception:
            rejection_cats = []
    
    if request.method == 'POST':
        title = request.form.get('title', '').strip()
        desc = request.form.get('description', '').strip()
        amt_str = request.form.get('amount', '').strip()
        days_str = request.form.get('days', '').strip()
        
        if not title or len(title) > 140:
            flash('Название: до 140 символов.', 'danger')
            return render_template('create_goal.html', 
                goal_type=goal['type'], mode='edit', goal=goal, 
                photos_list=photos_list, rejection_cats=rejection_cats)
        
        try:
            amt = float(amt_str)
        except (ValueError, TypeError):
            flash('Некорректная сумма.', 'danger')
            return render_template('create_goal.html', 
                goal_type=goal['type'], mode='edit', goal=goal,
                photos_list=photos_list, rejection_cats=rejection_cats)
        
        try:
            days = int(days_str) if days_str else 7
        except (ValueError, TypeError):
            days = 7
        
        from content_guard import check as cg_check, is_filter_enabled
        if is_filter_enabled(db):
            r_title = cg_check(title, 'goal_title')
            log_guard(r_title, title, 'goal_title', 'edit_goal')
            if r_title['status'] == 'blocked':
                flash('Цель не прошла проверку: ' + r_title.get('message', ''), 'danger')
                return render_template('create_goal.html', 
                    goal_type=goal['type'], mode='edit', goal=goal,
                    photos_list=photos_list, rejection_cats=rejection_cats)
            
            r_desc = cg_check(desc, 'goal_description')
            log_guard(r_desc, desc, 'goal_description', 'edit_goal')
            if r_desc['status'] == 'blocked':
                flash('Цель не прошла проверку: ' + r_desc.get('message', ''), 'danger')
                return render_template('create_goal.html', 
                    goal_type=goal['type'], mode='edit', goal=goal,
                    photos_list=photos_list, rejection_cats=rejection_cats)
        
        ends = (datetime.now() + timedelta(days=days)).isoformat()
        new_count = (goal['resubmission_count'] or 0) + 1
        
        db.execute("""
            UPDATE goals 
            SET title = ?, description = ?, amount_goal = ?, ends_at = ?,
                status = 'draft', moderation_status = 'pending',
                resubmission_count = ?, is_resubmission = 1,
                moderated_at = NULL, moderated_by = NULL,
                moderation_comment = NULL, rejection_categories = NULL
            WHERE id = ?
        """, (title, desc, amt, ends, new_count, goal_id))
        
        # Событие в platform_events (действие автора)
        record_event(
            'goal_resubmitted',
            db=db,
            actor_user_id=user['id'],
            goal_id=goal_id,
            source='user',
            payload={
                'resubmission_count': new_count,
                'title': title,
            }
        )
        
        db.execute(
            "INSERT INTO notifications (user_id, type, title, message, action_url) VALUES (?, 'goal_resubmitted', 'Цель отправлена на повторную проверку', ?, '/goal/" + str(goal_id) + "')",
            (user['id'], 'Мы проверим её в ближайшее время.')
        )
        
        db.commit()
        flash('Цель отправлена на повторную проверку.', 'success')
        return redirect(url_for('goal_page', goal_id=goal_id))
    
    return render_template('create_goal.html',
        goal_type=goal['type'],
        mode='edit',
        goal=goal,
        photos_list=photos_list,
        rejection_cats=rejection_cats,
        title=goal['title'],
        description=goal['description'] or '',
        amount=goal['amount_goal'],
        days=None,
    )


@app.route('/_metrics/events')
@admin_required
def metrics_events():
    """Журнал событий платформы (platform_events)."""
    user = get_current_user()
    db = get_db()
    
    # Фильтры
    period = request.args.get('period', '24h')
    event_type_filter = request.args.get('type', 'all')
    source_filter = request.args.get('source', 'all')
    search = request.args.get('search', '').strip()
    
    period_map = {
        '1h': '-1 hour',
        '24h': '-24 hours',
        '7d': '-7 days',
        '30d': '-30 days',
    }
    time_filter = period_map.get(period)
    
    query = """
        SELECT pe.*, 
            COALESCE(NULLIF(au.name, ''), au.phone) as actor_name,
            COALESCE(tu.name, tu.phone) as target_name,
            g.title as goal_title,
            d.amount_reported as donation_amount
        FROM platform_events pe
        LEFT JOIN users au ON pe.actor_user_id = au.id
        LEFT JOIN users tu ON pe.target_user_id = tu.id
        LEFT JOIN goals g ON pe.goal_id = g.id
        LEFT JOIN donations d ON pe.donation_id = d.id
        WHERE 1=1
    """
    params = []
    
    if time_filter:
        query += " AND pe.created_at > datetime('now', ?)"
        params.append(time_filter)
    
    if event_type_filter != 'all':
        query += " AND pe.event_type = ?"
        params.append(event_type_filter)
    
    if source_filter != 'all':
        query += " AND pe.source = ?"
        params.append(source_filter)
    
    if search:
        query += " AND (CAST(pe.id AS TEXT) = ? OR CAST(pe.actor_user_id AS TEXT) = ? OR CAST(pe.goal_id AS TEXT) = ?)"
        params.extend([search, search, search])
    
    query += " ORDER BY pe.id DESC LIMIT 200"
    events = db.execute(query, params).fetchall()
    
    # Счётчики для фильтра по типам
    event_types = db.execute("""
        SELECT event_type, COUNT(*) as cnt
        FROM platform_events
        WHERE created_at > datetime('now', '-7 days')
        GROUP BY event_type
        ORDER BY cnt DESC
    """).fetchall()
    
    # Счётчики источников
    sources = db.execute("""
        SELECT source, COUNT(*) as cnt
        FROM platform_events
        WHERE created_at > datetime('now', '-24 hours')
        GROUP BY source
    """).fetchall()
    
    # Общее количество
    total_24h = db.execute(
        "SELECT COUNT(*) FROM platform_events WHERE created_at > datetime('now', '-24 hours')"
    ).fetchone()[0]
    
    counts = get_sidebar_counts(db)
    
    return render_template('_metrics/events.html',
        user=user,
        active='events',
        counts=counts,
        events=events,
        period=period,
        event_type_filter=event_type_filter,
        source_filter=source_filter,
        search=search,
        event_types=event_types,
        sources=sources,
        total_24h=total_24h,
    )


@app.route('/health')
def health():
    return 'OK'

@app.route('/uploads/goals/<filename>')
def uploaded_file(filename):
    return send_from_directory(app.config['UPLOAD_FOLDER'], filename)

app.jinja_env.globals['get_csrf_token'] = get_csrf_token
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


def format_date_ru(date_str, with_time=True):
    """Формат даты: 13.10.2026 19:07 или 13.10.2026"""
    if not date_str:
        return '—'
    try:
        s = str(date_str)
        # Есть ли время
        if ' ' in s:
            date_part, time_part = s.split(' ', 1)
            y, m, d = date_part.split('-')
            time_part = time_part[:5]  # HH:MM
            if with_time:
                return f'{d}.{m}.{y} {time_part}'
            return f'{d}.{m}.{y}'
        else:
            # Только дата
            parts = s[:10].split('-')
            if len(parts) == 3:
                y, m, d = parts
                return f'{d}.{m}.{y}'
    except Exception:
        pass
    return str(date_str)[:16]


app.jinja_env.filters['date_ru'] = format_date_ru

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
