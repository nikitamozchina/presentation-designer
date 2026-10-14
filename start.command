#!/bin/bash
# Запуск «Дизайнера презентаций» двойным кликом.
cd "$(dirname "$0")" || exit 1

if ! /usr/bin/xcode-select -p >/dev/null 2>&1 && [ ! -x /Library/Frameworks/Python.framework/Versions/Current/bin/python3 ] && ! command -v brew >/dev/null 2>&1; then
  echo "Нужен Python 3. Сейчас откроется установка Command Line Tools от Apple."
  echo "После установки запустите start.command ещё раз."
  /usr/bin/xcode-select --install
  read -r -p "Нажмите Enter, чтобы закрыть окно…"
  exit 1
fi

if ! python3 -c 'import sys; sys.exit(sys.version_info < (3, 9))' 2>/dev/null; then
  echo "Нужен Python 3.9 или новее. Установите его (README.md, раздел «Установка») и повторите."
  read -r -p "Нажмите Enter, чтобы закрыть окно…"
  exit 1
fi

if [ ! -d "/Applications/Google Chrome.app" ] && [ ! -d "$HOME/Applications/Google Chrome.app" ]; then
  echo "Внимание: не найден Google Chrome — без него не будет работать экспорт PDF/JPEG."
fi

exec python3 app/server.py --open
