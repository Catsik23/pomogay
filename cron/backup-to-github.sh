#!/bin/bash
cd /opt/pomogay
git add main.py models.py requirements.txt static/ templates/ cron/ gdrive.py README.md .gitignore
git commit -m "Бэкап с сервера $(date '+%Y-%m-%d %H:%M')" 2>/dev/null || true
git push origin main 2>/dev/null || echo "Push не удался"
echo "Бэкап кода загружен на GitHub (без data/ и uploads/)"
