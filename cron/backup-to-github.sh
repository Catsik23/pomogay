#!/bin/bash
cd /opt/pomogay
git add -A
git commit -m "Бэкап с сервера $(date '+%Y-%m-%d %H:%M')"
git push origin main
echo "Бэкап загружен на GitHub"
