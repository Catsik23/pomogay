#!/usr/bin/env python3
"""Cron-скрипт автоподтверждения. Временно отключён — требуется переработка."""
# TODO: переписать на прямую работу с БД или добавить маршрут в main.py
import sys
sys.exit(0)

import requests
try:
    r = requests.get('https://pomogay.onrender.com/auto_confirm')
    print(f"Автоподтверждение: {r.text}")
except Exception as e:
    print(f"Ошибка: {e}")