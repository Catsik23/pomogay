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


def get_streak_multiplier(streak_days):
    """Множитель очков рейтинга в зависимости от серии дней."""
    if streak_days >= 10:
        return 2.0
    if streak_days <= 1:
        return 1.0
    return 1.0 + (streak_days * 0.1)


def add_trust_score(user_id, action, db):
    """Начисляет Trust Score и обновляет уровень."""
    points = SCORE_ACTIONS.get(action, 0)
    if points == 0:
        return 0

    # Бонус за серию — только для донатов
    if action in ('donation_recipient_confirmed', 'donation_confirmed'):
        user_streak = db.execute('SELECT streak_days FROM users WHERE id = ?', (user_id,)).fetchone()
        if user_streak:
            multiplier = get_streak_multiplier(user_streak['streak_days'] or 0)
            points = round(points * multiplier)

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


# Слоты целей: какие типы в каком слоте
GOAL_SLOTS = {
    'group':       1,   # Общий сбор — открыт всем
    'super_blitz': 2,   # Супер-блиц — после основной анкеты
    'blitz':       3,   # Блиц, Срочный, Жизненный — после верификации личности
    'urgent':      3,
    'serious':     3,
}


def get_open_slots(user):
    """Возвращает список открытых слотов для пользователя."""
    slots = [1]  # Слот 1 открыт всегда
    try:
        if user['profile_completed'] == 1:
            slots.append(2)
    except (KeyError, IndexError):
        pass
    try:
        if user['identity_verified'] == 1:
            slots.append(3)
    except (KeyError, IndexError):
        pass
    return slots


def can_create_goal(user, db, goal_type=None):
    """Проверяет, может ли пользователь создать цель этого типа."""
    # Админ — без ограничений
    try:
        if user['is_admin'] == 1:
            return True
    except (KeyError, IndexError):
        pass

    if goal_type is None:
        # Без типа — просто проверяем, есть ли хоть один открытый слот
        return len(get_open_slots(user)) > 0

    slot_needed = GOAL_SLOTS.get(goal_type)
    if slot_needed is None:
        return False

    open_slots = get_open_slots(user)
    if slot_needed not in open_slots:
        return False

    # В этом слоте — не больше 1 активной цели соответствующего типа
    types_in_slot = [t for t, s in GOAL_SLOTS.items() if s == slot_needed]
    placeholders = ','.join('?' * len(types_in_slot))
    active_in_slot = db.execute(
        f"SELECT COUNT(*) FROM goals WHERE user_id = ? AND status = 'active' AND type IN ({placeholders})",
        (user['id'], *types_in_slot)
    ).fetchone()[0]

    return active_in_slot < 1


def can_create_goal_reason(user):
    """Возвращает причину, почему нельзя создать цель — для UX."""
    slots = get_open_slots(user)
    if 1 not in slots:
        return 'no_slot'
    if 2 not in slots and 3 not in slots:
        return 'need_profile'
    if 3 not in slots:
        return 'need_video'
    return None


# Палитра 12 уровней — от песка через закат к золоту
TRUST_COLORS = [
    '#C4B5A0',  # Гость — песок
    '#FFB88C',  # Новичок — персик
    '#FF8C5A',  # Участник — закатный оранж
    '#FF6B4A',  # Проверенный — коралл
    '#F55F6E',  # Надёжный — тёплый розовый
    '#E8457A',  # Свой — малина
    '#B54A8C',  # Старожил — пурпур
    '#6E5BA6',  # Хранитель — индиго
    '#4A6BB8',  # Маяк — глубокий синий
    '#3D8B9E',  # Страж — морской
    '#4A9E7F',  # Мудрец — изумруд
    '#D4A853',  # Легенда — золото
]


def get_trust_segments(score):
    """Возвращает список сегментов для развёрнутой шкалы рейтинга."""
    score = score or 0
    segments = []
    for i, (min_score, key, emoji_name, perks) in enumerate(TRUST_LEVELS):
        # Верхняя граница сегмента — начало следующего уровня (или +∞ для последнего)
        if i < len(TRUST_LEVELS) - 1:
            max_score = TRUST_LEVELS[i+1][0]
        else:
            max_score = 999999

        # Текущий уровень: score попадает в этот диапазон
        is_current = min_score <= score < max_score

        # Пройденный: score уже перешёл верхнюю границу
        is_passed = score >= max_score

        segments.append({
            'level': i + 1,
            'key': key,
            'name': get_trust_level_name(key),
            'min_score': min_score,
            'max_score': max_score,
            'color': TRUST_COLORS[i],
            'is_current': is_current,
            'is_passed': is_passed,
        })
    return segments


def get_trust_level_name(level_key):
    """Возвращает русское имя уровня по ключу."""
    NAMES = {
        'guest': 'Гость',
        'newcomer': 'Новичок',
        'member': 'Участник',
        'verified': 'Проверенный',
        'reliable': 'Надёжный',
        'trusted': 'Свой',
        'veteran': 'Старожил',
        'guardian': 'Хранитель',
        'beacon': 'Маяк',
        'guard': 'Страж',
        'sage': 'Мудрец',
        'legend': 'Легенда',
    }
    return NAMES.get(level_key, 'Гость')


def get_trust_progress(score):
    """
    Возвращает прогресс до следующего уровня.
    { current_name, current_score, next_name, next_score, points_to_next, progress_pct }
    """
    score = score or 0
    current_threshold, current_key, _, _ = TRUST_LEVELS[0]
    next_threshold = None
    next_key = None

    for threshold, key, name, perks in TRUST_LEVELS:
        if score >= threshold:
            current_threshold = threshold
            current_key = key
        else:
            next_threshold = threshold
            next_key = key
            break

    current_name = get_trust_level_name(current_key)
    if next_threshold is None:
        return {
            'current_name': current_name,
            'current_score': score,
            'next_name': None,
            'next_score': None,
            'points_to_next': 0,
            'progress_pct': 100,
        }

    next_name = get_trust_level_name(next_key)
    range_size = next_threshold - current_threshold
    progress_in_range = score - current_threshold
    progress_pct = int((progress_in_range / range_size) * 100) if range_size > 0 else 0

    return {
        'current_name': current_name,
        'current_score': score,
        'next_name': next_name,
        'next_score': next_threshold,
        'points_to_next': next_threshold - score,
        'progress_pct': progress_pct,
    }
