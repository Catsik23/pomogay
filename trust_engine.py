"""Trust Score Engine — 12 уровней «Путь доверия» помогай24.рф"""

TRUST_LEVELS = [
    (0,   'guest',       '🔴 Гость',       {'max_donate': 500,   'max_goals': 0, 'priority_boost': 0,  'can_report': False, 'can_endorse': False, 'can_veto': False}),
    (30,  'newcomer',    '🟠 Новичок',     {'max_donate': 1000,  'max_goals': 1, 'priority_boost': 0,  'can_report': False, 'can_endorse': False, 'can_veto': False}),
    (50,  'member',      '🟡 Участник',    {'max_donate': 5000,  'max_goals': 1, 'priority_boost': 0,  'can_report': False, 'can_endorse': False, 'can_veto': False}),
    (70,  'verified',    '🟢 Проверенный', {'max_donate': 10000, 'max_goals': 2, 'priority_boost': 5,  'can_report': True,  'can_endorse': False, 'can_veto': False}),
    (90,  'reliable',    '🔵 Надёжный',    {'max_donate': 25000, 'max_goals': 2, 'priority_boost': 8,  'can_report': True,  'can_endorse': True,  'can_veto': False}),
    (110, 'trusted',     '🟣 Свой',        {'max_donate': 50000, 'max_goals': 3, 'priority_boost': 10, 'can_report': True,  'can_endorse': True,  'can_veto': False}),
    (130, 'veteran',     '⚪ Старожил',    {'max_donate': 75000, 'max_goals': 3, 'priority_boost': 12, 'can_report': True,  'can_endorse': True,  'can_veto': False}),
    (150, 'guardian',    '🟤 Хранитель',   {'max_donate': 100000,'max_goals': 4, 'priority_boost': 15, 'can_report': True,  'can_endorse': True,  'can_veto': False}),
    (170, 'beacon',      '🔶 Маяк',        {'max_donate': 150000,'max_goals': 4, 'priority_boost': 20, 'can_report': True,  'can_endorse': True,  'can_veto': True}),
    (190, 'guard',       '⭐ Страж',       {'max_donate': 200000,'max_goals': 5, 'priority_boost': 25, 'can_report': True,  'can_endorse': True,  'can_veto': True}),
    (210, 'sage',        '💫 Мудрец',      {'max_donate': 300000,'max_goals': 5, 'priority_boost': 30, 'can_report': True,  'can_endorse': True,  'can_veto': True}),
    (230, 'legend',      '👑 Легенда',     {'max_donate': 999999,'max_goals': 99,'priority_boost': 40, 'can_report': True,  'can_endorse': True,  'can_veto': True}),
]

SCORE_ACTIONS = {
    'registration': 50,
    'donation_confirmed': 2,
    'warm_word_sent': 1,
    'donation_recipient_confirmed': 3,
    'goal_closed_author': 10,
    'video_verified': 15,
    'endorsement_valid': 5,
    'month_clean': 5,
    'report_valid': 3,
    'report_false': -5,
    'goal_hidden_reports': -20,
    'donation_not_confirmed_24h': -3,
}


def get_trust_level(score):
    """Возвращает уровень по score."""
    info = TRUST_LEVELS[0]
    for threshold, key, name, perks in TRUST_LEVELS:
        if score >= threshold:
            info = (threshold, key, name, perks)
    return info


def add_trust_score(user_id, action, db):
    """Начисляет Trust Score и обновляет уровень."""
    points = SCORE_ACTIONS.get(action, 0)
    if points == 0:
        return 0
    
    db.execute('UPDATE users SET trust_score = trust_score + ? WHERE id = ?', (points, user_id))
    user = db.execute('SELECT trust_score FROM users WHERE id = ?', (user_id,)).fetchone()
    new_score = user['trust_score']
    
    _, level_key, _, _ = get_trust_level(new_score)
    db.execute("UPDATE users SET trust_level = ? WHERE id = ?", (level_key, user_id))
    db.commit()
    
    return points


def get_trust_perks(score):
    """Возвращает привилегии для текущего уровня."""
    _, _, _, perks = get_trust_level(score)
    return perks


def can_create_goal(user, db):
    """Проверяет, может ли пользователь создать ещё одну цель."""
    perks = get_trust_perks(user['trust_score'] or 0)
    active_count = db.execute(
        "SELECT COUNT(*) FROM goals WHERE user_id = ? AND status = 'active'",
        (user['id'],)
    ).fetchone()[0]
    return active_count < perks['max_goals']
