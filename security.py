"""Content Filter — локальный word-модератор помогай24.рф"""
import json
import re
import os

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
STOPWORDS_PATH = os.path.join(BASE_DIR, 'data', 'stopwords.json')

# Загружаем базу один раз при импорте
with open(STOPWORDS_PATH, 'r', encoding='utf-8') as f:
    STOPWORDS = json.load(f)


def _normalize(text):
    """Приводим текст к нижнему регистру, убираем повторы букв (лоооох → лох)."""
    text = text.lower().strip()
    text = re.sub(r'(.)\1{2,}', r'\1\1', text)  # схлопываем >2 повторов
    return text


def _check_words(text, words):
    """Прямое совпадение слов."""
    for word in words:
        if word in text:
            return word
    return None


def _check_patterns(text, patterns):
    """Проверка регулярных выражений."""
    for pattern in patterns:
        if re.search(pattern, text):
            return pattern
    return None


def check_content(title, description=""):
    """
    Проверяет название и описание цели.
    Возвращает: ('passed', None) или ('blocked', reason) или ('flagged', reason)
    """
    text = _normalize(f"{title} {description}")

    # BLOCK — мгновенный запрет
    for category, data in STOPWORDS.get('block', {}).items():
        word_match = _check_words(text, data.get('words', []))
        if word_match:
            return ('blocked', f"{data['reason']} (найдено: '{word_match}')")
        pattern_match = _check_patterns(text, data.get('patterns', []))
        if pattern_match:
            return ('blocked', f"{data['reason']} (паттерн: '{pattern_match}')")

    # FLAG — ручная модерация
    for category, data in STOPWORDS.get('flag', {}).items():
        word_match = _check_words(text, data.get('words', []))
        if word_match:
            return ('flagged', f"{data['reason']} (найдено: '{word_match}')")
        pattern_match = _check_patterns(text, data.get('patterns', []))
        if pattern_match:
            return ('flagged', f"{data['reason']} (паттерн: '{pattern_match}')")

    return ('passed', None)


def is_filter_enabled(db):
    """Проверяет, включён ли Content Filter."""
    flag = db.execute(
        "SELECT enabled FROM feature_flags WHERE flag_key = 'ENABLE_CONTENT_FILTER'"
    ).fetchone()
    return flag['enabled'] == 1 if flag else True  # По умолчанию включён
