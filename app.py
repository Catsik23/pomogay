
import os
from flask import Flask, session, redirect, url_for, request
from models import init_db, get_db

# --- Конфигурация ---
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DATABASE = os.path.join(BASE_DIR, 'data', 'pomogay.db')

app = Flask(__name__)
app.secret_key = os.environ.get('SECRET_KEY', 'pomogay-dev-secret-change-in-production')

# === ИНИЦИАЛИЗАЦИЯ БД ===
init_db()

# === ВСПОМОГАТЕЛЬНЫЕ ФУНКЦИИ ===
def get_current_user():
    """Возвращает текущего пользователя или None"""
    if 'user_id' in session:
        db = get_db()
        user = db.execute("SELECT * FROM users WHERE id = ?", (session['user_id'],)).fetchone()
        db.close()
        return user
    return None

def login_required(route_func):
    """Декоратор: требует авторизацию"""
    def wrapper(*args, **kwargs):
        if 'user_id' not in session:
            return redirect(url_for('login'))
        return route_func(*args, **kwargs)
    wrapper.__name__ = route_func.__name__
    return wrapper

# === ГЛАВНАЯ СТРАНИЦА (заглушка) ===
@app.route('/')
def index():
    return """
    <!DOCTYPE html>
    <html lang="ru">
    <head>
        <meta charset="UTF-8">
        <meta name="viewport" content="width=device-width, initial-scale=1.0">
        <title>Помогай</title>
        <style>
            body { font-family: -apple-system, sans-serif; display: flex; justify-content: center; align-items: center; min-height: 100vh; margin: 0; background: #F8F9FC; color: #1A202C; }
            .container { text-align: center; }
            h1 { font-size: 48px; color: #FF8C69; }
            p { color: #4A5568; margin: 10px 0; }
            a { color: #FF8C69; text-decoration: none; }
        </style>
    </head>
    <body>
        <div class="container">
            <h1>Помогай</h1>
            <p>Платформа для прямой помощи людям</p>
            <p><a href="/register">Регистрация</a> | <a href="/login">Вход</a></p>
            <p><a href="/goals">Посмотреть цели</a></p>
        </div>
    </body>
    </html>
    """

# === ЗАГЛУШКИ МАРШРУТОВ ===

@app.route('/register')
def register():
    return '<h2 style="text-align:center;margin-top:50px">🚧 Регистрация — скоро</h2>'

@app.route('/login')
def login():
    return '<h2 style="text-align:center;margin-top:50px">🚧 Вход — скоро</h2>'

@app.route('/logout')
def logout():
    session.clear()
    return redirect(url_for('index'))

@app.route('/goals')
def goals_list():
    return '<h2 style="text-align:center;margin-top:50px">🚧 Лента целей — скоро</h2>'

@app.route('/goal/<int:goal_id>')
def goal_page(goal_id):
    return f'<h2 style="text-align:center;margin-top:50px">🚧 Страница цели #{goal_id} — скоро</h2>'

@app.route('/profile')
@login_required
def profile():
    return '<h2 style="text-align:center;margin-top:50px">🚧 Профиль — скоро</h2>'

@app.route('/admin')
def admin_panel():
    return '<h2 style="text-align:center;margin-top:50px">🚧 Админка — скоро</h2>'

# === ЗАПУСК ===
if __name__ == '__main__':
    port = int(os.environ.get('PORT', 5000))
    print(f"Сервер запущен на порту {port}")
    app.run(host='0.0.0.0', port=port, debug=False)
