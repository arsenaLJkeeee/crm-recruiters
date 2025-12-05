#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")"

if [ ! -f ".venv/bin/python" ]; then
  echo "[*] Создаю виртуальное окружение..."
  python3 -m venv .venv
fi

echo "[*] Запуск CRM..."
exec .venv/bin/python app.py

