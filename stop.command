#!/bin/bash
# Остановка «Дизайнера презентаций». Проекты и экспорт не удаляются.
cd "$(dirname "$0")" || exit 1
python3 - <<'EOF'
import json, urllib.request
try:
    info = json.load(open(".state/server.json"))
except FileNotFoundError:
    print("Приложение не запущено."); raise SystemExit
req = urllib.request.Request("http://127.0.0.1:%d/api/shutdown" % info["port"], data=b"{}",
                             headers={"Content-Type": "application/json"})
try:
    urllib.request.urlopen(req, timeout=5)
    print("Приложение остановлено.")
except OSError:
    print("Сервер не отвечает — возможно, уже остановлен.")
EOF
