#!/usr/bin/env python3
# Запуск/остановка сервера в фоне — для глобальной команды /presentations.
#   launch.py start    запустить (если не запущен) и открыть браузер
#   launch.py stop     остановить
#   launch.py status   запущен ли и по какому адресу
import json, os, subprocess, sys, time, urllib.request, webbrowser
from pathlib import Path

APP = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(APP / "app"))
from core import store  # noqa: E402

SERVER = APP / "app" / "server.py"


def running():
    info = store.read_json(store.STATE / "server.json")
    if not info:
        return None
    try:
        os.kill(info["pid"], 0)
        urllib.request.urlopen(info["url"] + "api/state", timeout=2)
        return info
    except (OSError, KeyError, ValueError):
        return None


def out(data, code=0):
    print(json.dumps(data, ensure_ascii=False))
    sys.exit(code)


def start():
    info = running()
    if info:
        webbrowser.open(info["url"])
        out({"ok": True, "url": info["url"], "already_running": True})
    store.STATE.mkdir(exist_ok=True)
    log = open(store.STATE / "server.out", "a")
    subprocess.Popen([sys.executable, str(SERVER)], cwd=str(APP), stdout=log, stderr=log,
                     stdin=subprocess.DEVNULL, start_new_session=True)
    for _ in range(50):
        time.sleep(0.2)
        info = running()
        if info:
            webbrowser.open(info["url"])
            out({"ok": True, "url": info["url"]})
    out({"ok": False, "error": "Сервер не запустился, см. %s" % (store.STATE / "server.out")}, 1)


def stop():
    info = running()
    if not info:
        out({"ok": True, "message": "Приложение не запущено"})
    req = urllib.request.Request(info["url"] + "api/shutdown", data=b"{}",
                                 headers={"Content-Type": "application/json"})
    try:
        urllib.request.urlopen(req, timeout=5)
    except OSError:
        pass
    for _ in range(25):
        time.sleep(0.2)
        if not running():
            out({"ok": True, "message": "Приложение остановлено"})
    out({"ok": False, "error": "Сервер не остановился, pid %s" % info["pid"]}, 1)


if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else "start"
    if cmd == "start":
        start()
    elif cmd == "stop":
        stop()
    elif cmd == "status":
        info = running()
        out({"ok": True, "running": bool(info), "url": info and info["url"]})
    else:
        out({"ok": False, "error": "Команды: start | stop | status"}, 1)
