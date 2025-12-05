@echo off
setlocal
cd /d "%~dp0"

REM One-click launcher: creates venv if absent and starts the app.
if not exist ".venv\Scripts\python.exe" (
    echo [*] Создаю виртуальное окружение...
    python -m venv .venv
    if errorlevel 1 (
        echo [!] Не удалось создать venv. Убедитесь, что установлен Python 3.10+.
        pause
        exit /b 1
    )
)

echo [*] Запуск CRM...
".venv\Scripts\python.exe" app.py
if errorlevel 1 (
    echo [!] Приложение завершилось с ошибкой. Смотрите логи в logs\app.log
)

pause

