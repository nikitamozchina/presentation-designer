#!/usr/bin/env python3
# Конвертация презентаций через Keynote (обязателен; PowerPoint неинтерактивно не работает).
#   convert.py <файл.pptx|.ppt|.key> <папка-результата> [--pdf] [--images] [--pptx]
#   convert.py --check   Keynote установлен и разрешено им управлять
#     --pdf     → <имя>.pdf (по умолчанию, если ничего не указано)
#     --images  → <имя>-slides/NNN.png — картинки слайдов (для контактных листов)
#     --pptx    → <имя>.pptx (для .key: чтобы extract.py мог достать текст и тему)
# Вывод — JSON. Keynote закрывается, только если его запустил этот скрипт; закрывается только свой документ.
import json, re, shutil, subprocess, sys, time
from pathlib import Path

KEYNOTE = Path("/Applications/Keynote.app")
ERRORS = {
    "-600": "Keynote не запустился. Откройте Keynote вручную один раз (закройте окно приветствия) и повторите.",
    "-1743": "Нет разрешения управлять Keynote. Системные настройки → Конфиденциальность и безопасность → "
             "Автоматизация → включите Keynote для приложения, в котором работает Claude Code "
             "(если запрос был отклонён: в Терминале `tccutil reset AppleEvents`, затем повторите).",
    "-1712": "Keynote не ответил вовремя — возможно, открыто окно с вопросом. Посмотрите на Keynote, закройте окно и повторите.",
    "-128": "Действие отменено в Keynote.",
}


def out(data, code=0):
    print(json.dumps(data, ensure_ascii=False))
    sys.exit(code)


def osa(script, timeout):
    r = subprocess.run(["osascript", "-"], input=script, capture_output=True, text=True, timeout=timeout)
    return r.returncode, r.stdout.strip(), r.stderr.strip()


def explain(err):
    m = re.search(r"\((-\d+)\)", err)
    return ERRORS.get(m.group(1), "Ошибка Keynote: %s" % err) if m else "Ошибка Keynote: %s" % err


def keynote_running():
    return subprocess.run(["pgrep", "-x", "Keynote"], capture_output=True).returncode == 0


def ensure_keynote():
    """Запустить без восстановления окон и дождаться, пока начнёт отвечать на скрипты."""
    if not KEYNOTE.exists():
        out({"ok": False, "error": "Keynote не установлен — он обязателен. Установите его из App Store (бесплатно)."}, 1)
    started = False
    if not keynote_running():
        subprocess.run(["open", "-g", "-a", "Keynote", "--args", "-ApplePersistenceIgnoreState", "YES"])
        started = True
    deadline = time.time() + 60
    last = ""
    while time.time() < deadline:
        code, _, err = osa('tell application "Keynote" to get name', 10)
        if code == 0:
            return started
        last = err
        if "-1743" in err:
            break
        time.sleep(1)
    out({"ok": False, "error": explain(last or "(-600)")}, 1)


def q(path):
    return str(path).replace("\\", "\\\\").replace('"', '\\"')


def convert(src, dst_dir, want_pdf, want_images, want_pptx):
    src, dst_dir = Path(src).resolve(), Path(dst_dir).resolve()
    if not src.exists():
        out({"ok": False, "error": "Нет файла %s" % src}, 1)
    dst_dir.mkdir(parents=True, exist_ok=True)
    stem = src.stem
    started = ensure_keynote()
    steps, result = [], {"ok": True, "source": str(src)}
    if want_pdf:
        pdf = dst_dir / (stem + ".pdf")
        steps.append('export d to POSIX file "%s" as PDF with properties {PDF image quality:Good}' % q(pdf))
        result["pdf"] = str(pdf)
    if want_pptx and src.suffix.lower() == ".key":
        pptx = dst_dir / (stem + ".pptx")
        steps.append('export d to POSIX file "%s" as Microsoft PowerPoint' % q(pptx))
        result["pptx"] = str(pptx)
    img_dir = None
    if want_images:
        img_dir = dst_dir / (stem + "-slides")
        shutil.rmtree(img_dir, ignore_errors=True)
        img_dir.mkdir()
        steps.append('export d to POSIX file "%s" as slide images with properties {image format:PNG, skipped slides:false}' % q(img_dir))
    script = '''with timeout of 600 seconds
tell application "Keynote"
  set d to open POSIX file "%s"
  try
    %s
    set n to count of slides of d
  on error e number k
    close d saving no
    error e number k
  end try
  close d saving no
  return n
end tell
end timeout''' % (q(src), "\n    ".join(steps))
    t0 = time.time()
    try:
        code, stdout, err = osa(script, 660)
    except subprocess.TimeoutExpired:
        code, stdout, err = 1, "", "(-1712)"
    if started:  # закрываем Keynote, только если сами его открыли и в нём ничего не осталось
        osa('tell application "Keynote" to if (count of documents) is 0 then quit', 30)
    if code != 0:
        out({"ok": False, "error": explain(err)}, 1)
    result["slides"] = int(stdout) if stdout.isdigit() else None
    result["seconds"] = round(time.time() - t0, 1)
    if img_dir:
        imgs = sorted(p for p in img_dir.rglob("*.png"))
        # Keynote называет файлы «Имя.001.png» — приводим к NNN.png
        for i, p in enumerate(imgs, 1):
            p.rename(img_dir / ("%03d.png" % i))
        for d in sorted((p for p in img_dir.iterdir() if p.is_dir()), reverse=True):
            shutil.rmtree(d, ignore_errors=True)
        result["images_dir"] = str(img_dir)
        result["images"] = len(imgs)
    for key in ("pdf", "pptx"):
        if key in result and not Path(result[key]).exists():
            out({"ok": False, "error": "Keynote не создал %s" % Path(result[key]).name}, 1)
    out(result)


if __name__ == "__main__":
    a = sys.argv[1:]
    if a[:1] == ["--check"]:  # для install.py: Keynote есть и отвечает на скрипты (заодно запрос разрешения)
        started = ensure_keynote()
        if started:
            osa('tell application "Keynote" to if (count of documents) is 0 then quit', 30)
        out({"ok": True})
    if len(a) < 2:
        out({"ok": False, "error": "convert.py <файл> <папка> [--pdf] [--images] [--pptx]"}, 1)
    flags = set(a[2:])
    pdf = "--pdf" in flags or not ({"--images", "--pptx"} & flags)
    convert(a[0], a[1], pdf, "--images" in flags, "--pptx" in flags)
