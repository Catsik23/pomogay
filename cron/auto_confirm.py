#!/usr/bin/env python3
"""Cron-скрипт автоподтверждения. Запускать раз в 10 минут."""
import requests
try:
    r = requests.get('https://pomogay.onrender.com/auto_confirm')
    print(f"Автоподтверждение: {r.text}")
except Exception as e:
    print(f"Ошибка: {e}")
