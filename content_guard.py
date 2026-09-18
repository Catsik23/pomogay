"""Content Guard — сквозной валидатор контента помогай24.рф"""
import json
import re
import os

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
GUARD_PATH = os.path.join(BASE_DIR, 'data', 'content_guard.json')

with open(GUARD_PATH, 'r', encoding='utf-8') as f:
    GUARD = json.load(f)

SHORT_MESSAGES = {
    'links': 'Уберите ссылку — так безопаснее',
    'messengers_meta': 'В тексте упоминается экстремистская организация',
    'tiktok': 'В тексте упоминается нежелательная организация',
    'contacts': 'Уберите контакт — свяжетесь после доната',
    'extremism': 'Уберите этот текст',
    'drugs': 'Уберите этот текст',
    'adult': 'Уберите этот текст',
    'insults': 'Без оскорблений',
    'scam': 'Уберите этот текст',
    'military_context': 'Текст отправлен на проверку',
}


def _normalize(text):
    text = text.lower().strip()
    text = re.sub(r'(.)\1{2,}', r'\1\1', text)
    return text


def _find_word(text, words):
    for word in words:
        idx = text.find(word)
        if idx != -1:
            return word, (idx, idx + len(word))
    return None, None


def _find_pattern(text, patterns):
    for pattern in patterns:
        match = re.search(pattern, text)
        if match:
            return pattern, (match.start(), match.end())
    return None, None


def _check_categories(text, category_names, level):
    for cat in category_names:
        data = GUARD.get(level, {}).get(cat)
        if not data:
            continue
        word, pos = _find_word(text, data.get('words', []))
        if word:
            return {
                'status': 'blocked' if level == 'block' else 'flagged',
                'category': cat,
                'found': word,
                'position': pos,
                'message': SHORT_MESSAGES.get(cat, data.get('reason', '')),
            }
        pattern, pos = _find_pattern(text, data.get('patterns', []))
        if pattern:
            return {
                'status': 'blocked' if level == 'block' else 'flagged',
                'category': cat,
                'found': pattern,
                'position': pos,
                'message': SHORT_MESSAGES.get(cat, data.get('reason', '')),
            }
    return None


def check(text, context='goal_description'):
    if not text:
        return {'status': 'passed'}

    ctx = GUARD.get('contexts', {}).get(context, {})
    max_length = ctx.get('max_length', 1000)
    if len(text) > max_length:
        return {
            'status': 'blocked',
            'category': 'length',
            'position': (max_length, len(text)),
            'message': f'Максимум {max_length} символов',
        }

    normalized = _normalize(text)

    block_cats = ctx.get('blocks', [])
    result = _check_categories(normalized, block_cats, 'block')
    if result:
        return result

    flag_cats = ctx.get('flags', [])
    result = _check_categories(normalized, flag_cats, 'flag')
    if result:
        return result

    return {'status': 'passed'}


def is_filter_enabled(db):
    flag = db.execute(
        "SELECT enabled FROM feature_flags WHERE flag_key = 'ENABLE_CONTENT_FILTER'"
    ).fetchone()
    return flag['enabled'] == 1 if flag else True
