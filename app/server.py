#!/usr/bin/env python3
# Локальный сервер «Дизайнера презентаций». Только 127.0.0.1, только stdlib, Python 3.9+.
#   python3 app/server.py [--open]
import json, mimetypes, os, re, socket, subprocess, sys, threading, uuid, webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import unquote, urlparse

sys.path.insert(0, str(Path(__file__).resolve().parent))
from core import exporter, pptx_export, store, tasks  # noqa: E402

STATIC = Path(__file__).resolve().parent / "static"
SERVER_INFO = store.STATE / "server.json"
LOG = store.STATE / "server.log"
SLIDE_CSP = "script-src 'none'; object-src 'none'; frame-ancestors 'self'; base-uri 'none'; form-action 'none'"
_export_lock = threading.Lock()
_exporting = set()
_dialog_lock = threading.Lock()  # один системный диалог за раз
PRESETS = Path(os.environ.get("PD_CONFIG") or Path.home() / ".config" / "presentation-designer") / "presets.json"
GUIDE_HIDDEN = ("Исходники", "_extracted", "brief")


def log(msg):
    store.STATE.mkdir(exist_ok=True)
    with open(LOG, "a", encoding="utf-8") as f:
        f.write("%s %s\n" % (store.now(), msg))


def project_state(name):
    pdir = store.project_dir(name)
    lc = tasks.view("project", name) if (pdir / "lifecycle.json").exists() else None
    req = store.read_json(pdir / "request.json")
    meta = store.read_json(pdir / "project.json") if req else store.load_project(name)
    meta = meta or {"slides": []}
    slides = []
    for s in sorted(meta["slides"], key=lambda s: s["order"]):
        f = pdir / s["file"]
        slides.append(dict(s, mtime=f.stat().st_mtime if f.exists() else None))
    guide = store.read_json(store.GUIDES / meta.get("guideline_set_id", "") / "guide.json") or {}
    return {
        "name": name,
        "guide": {"id": guide.get("id"), "name": guide.get("name")},
        "slides": slides,
        "iterations": [store.iteration_summary(name, n) for n in reversed(store.edit_versions(name))],
        "export": store.read_json(pdir / "export-status.json"),
        "lifecycle": lc,
        "editable": editable(name),
        "request": {k: req.get(k) for k in ("mode", "topic", "guide")} if req else None,
    }


def editable(name):
    """Слайды можно комментировать, скрывать и переставлять только в готовом проекте."""
    pdir = store.project_dir(name)
    if not (pdir / "lifecycle.json").exists():
        return True
    return tasks.view("project", name)["state"] == "ready"


def require_editable(name):
    if not editable(name):
        raise RuntimeError("Проект ещё собирается")


def guide_info(g):
    data = store.read_json(g / "guide.json") or {}
    req = store.read_json(g / "request.json") or {}
    lc = tasks.view("guide", g.name) if (g / "lifecycle.json").exists() else None
    state = lc["state"] if lc else ("ready" if data else "broken")
    examples = [{"id": f.stem, "file": "examples/" + f.name, "title": store._slide_title(f), "order": i + 1,
                 "hidden": False, "mtime": f.stat().st_mtime}
                for i, f in enumerate(sorted((g / "examples").glob("*.html")))] if (g / "examples").is_dir() else []
    return {"id": g.name, "name": data.get("name") or req.get("name") or g.name, "state": state,
            "lifecycle": lc, "examples": examples}


def guides():
    if not store.GUIDES.is_dir():
        return []
    used = {}
    for p in (store.PROJECTS.iterdir() if store.PROJECTS.is_dir() else []):
        gid = (store.read_json(p / "project.json") or {}).get("guideline_set_id") if p.is_dir() else None
        if gid:
            used[gid] = used.get(gid, 0) + 1
    return [dict(guide_info(g), used_by=used.get(g.name, 0)) for g in sorted(store.GUIDES.iterdir())
            if g.is_dir() and not g.name.startswith(".")
            and ((g / "guide.json").exists() or (g / "lifecycle.json").exists())]


def project_lifecycles():
    return {n: tasks.view("project", n)["state"] for n in tasks.list_tasks("project")}


CHECK_JS = r"""
const MIN = %MIN%, TOL = 8;
const R = (r, S) => ({x: Math.round(r.left - S.left), y: Math.round(r.top - S.top), w: Math.round(r.width), h: Math.round(r.height)});
const hasText = el => [...el.childNodes].some(n => n.nodeType === 3 && n.textContent.trim());
const short = t => t.replace(/\s+/g, ' ').trim().slice(0, 80);
// реальные строки текста (а не рамка элемента): ширина блока часто больше самого текста
function lines(el, doc) {
  const out = [];
  for (const n of el.childNodes) if (n.nodeType === 3 && n.textContent.trim()) {
    const r = doc.createRange(); r.selectNodeContents(n);
    for (const b of r.getClientRects()) if (b.width > 1 && b.height > 1) out.push(b);
  }
  return out;
}
const union = rs => rs.reduce((u, b) => ({left: Math.min(u.left, b.left), top: Math.min(u.top, b.top), right: Math.max(u.right, b.right), bottom: Math.max(u.bottom, b.bottom), width: 0, height: 0}),
  {left: 1e9, top: 1e9, right: -1e9, bottom: -1e9});
// контраст по WCAG; фон — первый сплошной цвет у предков, под картинкой/градиентом не меряем
function rgba(c) { const m = c.match(/[\d.]+/g) || []; return m.length < 3 ? null : {r: +m[0], g: +m[1], b: +m[2], a: m.length > 3 ? +m[3] : 1}; }
function lum(c) { return [c.r, c.g, c.b].map(v => { v /= 255; return v <= 0.03928 ? v / 12.92 : Math.pow((v + 0.055) / 1.055, 2.4); })
  .reduce((s, v, i) => s + v * [0.2126, 0.7152, 0.0722][i], 0); }
function bgOf(el, slide, doc) {
  for (let a = el; a; a = a === slide ? null : a.parentElement) {
    const s = doc.defaultView.getComputedStyle(a);
    if (s.backgroundImage !== 'none') return null;
    const c = rgba(s.backgroundColor);
    if (c && c.a > 0.9) return c;
    if (c && c.a > 0.05) return null;  // полупрозрачная подложка — честно не посчитать
  }
  return {r: 255, g: 255, b: 255, a: 1};
}
function contrast(el, slide, doc) {
  const fg = rgba(doc.defaultView.getComputedStyle(el).color), bg = bgOf(el, slide, doc);
  if (!fg || !bg || fg.a < 0.9) return null;
  const [x, y] = [lum(fg), lum(bg)].sort((p, q) => q - p);
  return (x + 0.05) / (y + 0.05);
}
// нижняя граница видимого содержания: текст, картинки, плашки с фоном или рамкой (без логотипа и источника)
function contentBottom(slide, doc, S) {
  let b = 0;
  for (const el of slide.querySelectorAll('*')) {
    if (el.closest('.logo, .note-src')) continue;
    const s = doc.defaultView.getComputedStyle(el);
    if (s.display === 'none' || s.visibility === 'hidden') continue;
    const boxy = el.matches('img, svg') || hasText(el) || s.backgroundImage !== 'none'
      || (rgba(s.backgroundColor) || {a: 0}).a > 0.05 || parseFloat(s.borderTopWidth) > 0 || parseFloat(s.borderBottomWidth) > 0;
    if (!boxy) continue;
    const r = el.getBoundingClientRect();
    if (r.width < 2 || r.height < 2 || (r.width >= S.width - 2 && r.height >= S.height - 2)) continue;  // фоновые подложки на весь слайд не в счёт
    b = Math.max(b, r.bottom - S.top);
  }
  return b;
}
function check(id, doc) {
  const issues = [], slide = doc.querySelector('.slide') || doc.body, S = slide.getBoundingClientRect();
  const visible = el => { const s = doc.defaultView.getComputedStyle(el); return s.display !== 'none' && s.visibility !== 'hidden' && +s.opacity > 0; };
  const textEls = [...slide.querySelectorAll('*')].filter(el => hasText(el) && visible(el));
  for (const el of textEls) {
    const ls = lines(el, doc); if (!ls.length) continue;
    const r = union(ls); r.width = r.right - r.left; r.height = r.bottom - r.top;
    const st = doc.defaultView.getComputedStyle(el);
    if (r.left < S.left - TOL || r.top < S.top - TOL || r.right > S.right + TOL || r.bottom > S.bottom + TOL)
      issues.push({type: 'out_of_canvas', text: short(el.textContent), rect: R(r, S)});
    if (el.scrollHeight > el.clientHeight + TOL && el.clientHeight > 0 && st.overflowY !== 'visible')
      issues.push({type: 'text_cut', text: short(el.textContent), rect: R(r, S), need_px: el.scrollHeight - el.clientHeight});
    for (let a = el.parentElement; a && a !== slide; a = a.parentElement) {
      const as = doc.defaultView.getComputedStyle(a);
      if (as.overflow !== 'visible') {
        const ar = a.getBoundingClientRect();
        if (r.bottom > ar.bottom + TOL || r.right > ar.right + TOL)
          issues.push({type: 'clipped_by_box', text: short(el.textContent), rect: R(r, S), box: R(ar, S)});
        break;
      }
    }
    const fs = parseFloat(st.fontSize);
    if (fs < MIN * 0.85) issues.push({type: 'small_text', text: short(el.textContent), font_px: fs, min_px: MIN});
    const cr = contrast(el, slide, doc);
    if (cr !== null && cr < 3 && /[\p{L}\d]/u.test(el.textContent)) issues.push({type: 'low_contrast', text: short(el.textContent), ratio: Math.round(cr * 10) / 10, color: st.color});
  }
  for (let i = 0; i < textEls.length; i++) for (let j = i + 1; j < textEls.length; j++) {
    const A = textEls[i], B = textEls[j];
    if (A.contains(B) || B.contains(A)) continue;
    let hit = null;
    for (const a of lines(A, doc)) for (const b of lines(B, doc)) {
      const w = Math.min(a.right, b.right) - Math.max(a.left, b.left), h = Math.min(a.bottom, b.bottom) - Math.max(a.top, b.top);
      if (w > 4 && h > 4 && w * h > 150 && w * h > 0.15 * Math.min(a.width * a.height, b.width * b.height)) { hit = [a, b]; break; }
    }
    if (hit) issues.push({type: 'overlap', text: short(A.textContent) + ' ⟷ ' + short(B.textContent), rect: R(hit[0], S), other: R(hit[1], S)});
  }
  for (const img of slide.querySelectorAll('img')) if (img.complete && !img.naturalWidth)
    issues.push({type: 'broken_image', src: img.getAttribute('src')});
  for (const f of doc.fonts) if (f.status === 'error') issues.push({type: 'font_failed', family: f.family});
  if (!textEls.length && !slide.querySelector('img,svg')) issues.push({type: 'empty'});
  else {
    const bottom = contentBottom(slide, doc, S);
    if (bottom < S.height * 0.7) issues.push({type: 'empty_bottom', content_ends_px: Math.round(bottom), slide_px: Math.round(S.height)});
  }
  return {slide: id, issues};
}
(async () => {
  const out = [];
  for (const [id, src] of ITEMS) {
    const f = document.createElement('iframe');
    f.setAttribute('sandbox', 'allow-same-origin');
    document.body.appendChild(f);
    try {
      await new Promise((res, rej) => { f.onload = res; f.onerror = rej; f.src = src; });
      await f.contentDocument.fonts.ready;
      out.push(check(id, f.contentDocument));
    } catch (e) { out.push({slide: id, issues: [{type: 'error', message: String(e)}]}); }
    f.remove();
  }
  document.getElementById('result').textContent = '__RESULT__' + JSON.stringify(out) + '__END__';
})().catch(e => { document.getElementById('result').textContent = '__RESULT__' + JSON.stringify([{slide: '*', issues: [{type: 'error', message: String(e)}]}]) + '__END__'; });
"""


def check_page(kind, name, min_px):
    from html import escape
    from urllib.parse import quote as uq
    if kind == "project":
        pdir = store.project_dir(name)
        meta = (store.read_json(pdir / "project.json") if (pdir / "request.json").exists()
                else store.load_project(name)) or {"slides": []}
        items = [(s["id"], "/p/%s/%s" % (uq(name), uq(s["file"]))) for s in sorted(meta["slides"], key=lambda s: s["order"])]
    else:
        gdir = tasks.task_dir("guide", name)
        items = [(f.stem, "/g/%s/examples/%s" % (uq(name), uq(f.name))) for f in sorted((gdir / "examples").glob("*.html"))]
    data = json.dumps(items, ensure_ascii=True).replace("</", "<\\/")
    return ("<!doctype html><meta charset='utf-8'><style>iframe{width:1920px;height:1080px;border:0;display:block}</style>"
            "<pre id=\"result\"></pre><script>const ITEMS = %s;\n%s</script>" % (data, CHECK_JS.replace("%MIN%", str(int(min_px)))))


def load_presets():
    return store.read_json(PRESETS) or []


def pick_label(path):
    home = str(Path.home())
    s = str(path)
    return "~" + s[len(home):] if s.startswith(home) else s


class Handler(BaseHTTPRequestHandler):
    server_version = "PresentationDesigner"

    def log_message(self, fmt, *args):
        if not self.path.startswith(("/api/state", "/api/projects")):
            log("%s %s" % (self.address_string(), fmt % args))

    # ---------- защита ----------
    def _host_ok(self):
        # защита от DNS rebinding: принимаем только localhost-хосты нашего порта
        host = (self.headers.get("Host") or "").lower()
        port = self.server.server_address[1]
        return host in ("127.0.0.1:%d" % port, "localhost:%d" % port)

    def _origin_ok(self):
        origin = self.headers.get("Origin")
        port = self.server.server_address[1]
        return origin in (None, "http://127.0.0.1:%d" % port, "http://localhost:%d" % port)

    # ---------- ответы ----------
    def _send(self, code, body, ctype="application/json; charset=utf-8", headers=None):
        data = body if isinstance(body, bytes) else body.encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("X-Content-Type-Options", "nosniff")
        for k, v in (headers or {}).items():
            self.send_header(k, v)
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(data)

    def _json(self, data, code=200):
        self._send(code, json.dumps(data, ensure_ascii=False), headers={"Cache-Control": "no-store"})

    def _error(self, msg, code=400):
        self._json({"ok": False, "error": msg}, code)

    def _file(self, path, extra=None):
        if not path.is_file():
            return self._error("Файл не найден", 404)
        ctype = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
        if ctype.startswith("text/") or ctype in ("application/javascript",):
            ctype += "; charset=utf-8"
        self._send(200, path.read_bytes(), ctype, extra)

    # ---------- GET ----------
    def do_HEAD(self):
        self.do_GET()

    def do_GET(self):
        if not self._host_ok():
            return self._error("Запрещённый Host", 403)
        path = unquote(urlparse(self.path).path)
        try:
            if path == "/":
                return self._file(STATIC / "index.html", {"Cache-Control": "no-store"})
            if path.startswith("/static/"):
                return self._file(store.inside(STATIC, STATIC / path[len("/static/"):]))
            if path == "/api/state":
                return self._json({"ok": True, "projects": store.list_projects(), "project_states": project_lifecycles(),
                                   "guides": guides(), "agent": store.agent_status(),
                                   "chrome": bool(exporter.find_chrome()),
                                   "edit_commands": store.agent_commands("правки")})
            if path == "/api/presets":
                return self._json({"ok": True, "presets": load_presets()})
            m = re.fullmatch(r"/api/guides/([^/]+)", path)
            if m:
                return self._json(dict(guide_info(tasks.task_dir("guide", m.group(1))), ok=True))
            m = re.fullmatch(r"/g/([^/]+)/(.+)", path)
            if m:  # файлы гайда для просмотра эталонов; служебное и исходники закрыты
                gdir = tasks.task_dir("guide", m.group(1))
                f = store.inside(gdir, gdir / m.group(2))
                if f.parts[len(gdir.parts)] in GUIDE_HIDDEN or f.name.endswith(".json"):
                    return self._error("Нет доступа", 403)
                headers = {"Cache-Control": "no-cache"}
                if f.suffix.lower() in (".html", ".htm", ".svg"):
                    headers["Content-Security-Policy"] = SLIDE_CSP
                return self._file(f, headers)
            m = re.fullmatch(r"/api/projects/([^/]+)", path)
            if m:
                return self._json(dict(project_state(m.group(1)), ok=True))
            m = re.fullmatch(r"/p/([^/]+)/(.+)", path)
            if m:  # файлы проекта: слайды, styles.css, assets
                pdir = store.project_dir(m.group(1))
                f = store.inside(pdir, pdir / m.group(2))
                if f.parts[len(pdir.parts)] in ("edits", "history", "Исходники", "_extracted", "brief") or f.name.endswith(".json"):
                    return self._error("Нет доступа", 403)
                headers = {"Cache-Control": "no-cache"}
                if f.suffix.lower() in (".html", ".htm", ".svg"):
                    headers["Content-Security-Policy"] = SLIDE_CSP
                return self._file(f, headers)
            m = re.fullmatch(r"/pptx-raster/([^/]+)/([^/]+)", path)
            if m:  # растровый слой слайда для PPTX (свой скрипт; слайд — в sandbox без скриптов)
                base = "http://127.0.0.1:%d" % self.server.server_address[1]
                return self._send(200, pptx_export.raster_page(m.group(1), m.group(2), base), "text/html; charset=utf-8",
                                  {"Cache-Control": "no-store"})
            m = re.fullmatch(r"/pptx-scan/([^/]+)", path)
            if m:  # служебная страница экспорта PPTX: геометрия видимых слайдов (свой скрипт; слайды — без скриптов)
                base = "http://127.0.0.1:%d" % self.server.server_address[1]
                return self._send(200, pptx_export.scan_page(m.group(1), base), "text/html; charset=utf-8",
                                  {"Cache-Control": "no-store"})
            m = re.fullmatch(r"/check/(project|guide)/([^/]+)", path)
            if m:  # служебная страница самопроверки (свой скрипт; слайды внутри — без скриптов)
                q = dict(p.split("=", 1) for p in urlparse(self.path).query.split("&") if "=" in p)
                return self._send(200, check_page(m.group(1), m.group(2), int(q.get("min", "20"))),
                                  "text/html; charset=utf-8", {"Cache-Control": "no-store"})
            m = re.fullmatch(r"/print/([^/]+)", path)
            if m:
                base = "http://127.0.0.1:%d" % self.server.server_address[1]
                return self._send(200, exporter.print_page(m.group(1), base), "text/html; charset=utf-8",
                                  {"Content-Security-Policy": SLIDE_CSP, "Cache-Control": "no-store"})
            return self._error("Не найдено", 404)
        except (ValueError, FileNotFoundError, KeyError) as e:
            return self._error(str(e.args[0]) if e.args else str(e), 404)

    # ---------- POST ----------
    def do_POST(self):
        if not self._host_ok() or not self._origin_ok():
            return self._error("Запрещённый источник запроса", 403)
        if not (self.headers.get("Content-Type") or "").startswith("application/json"):
            return self._error("Ожидается JSON", 415)
        try:
            length = int(self.headers.get("Content-Length") or 0)
            if length > 2_000_000:
                return self._error("Слишком большой запрос", 413)
            body = json.loads(self.rfile.read(length) or b"{}")
        except ValueError:
            return self._error("Повреждённый JSON")
        path = unquote(urlparse(self.path).path)
        try:
            return self._route_post(path, body)
        except (ValueError, KeyError, FileNotFoundError) as e:
            return self._error(str(e.args[0]) if e.args else str(e))
        except (RuntimeError, TimeoutError) as e:
            return self._error(str(e), 409)
        except Exception as e:  # не роняем сервер, пишем диагностику
            log("POST %s: %r" % (path, e))
            return self._error("Внутренняя ошибка, подробности в .state/server.log", 500)

    def _route_post(self, path, body):
        m = re.fullmatch(r"/api/projects/([^/]+)/slides/([^/]+)/hidden", path)
        if m:
            name, sid, hidden = m.group(1), m.group(2), bool(body.get("hidden"))
            require_editable(name)

            def mutate(meta):
                slide = next((s for s in meta["slides"] if s["id"] == sid), None)
                if slide is None:
                    raise KeyError("Слайд не найден: %s" % sid)
                slide["hidden"] = hidden
            store.update_project(name, mutate)
            return self._json({"ok": True})
        m = re.fullmatch(r"/api/projects/([^/]+)/order", path)
        if m:
            ids = body.get("ids") or []
            require_editable(m.group(1))

            def mutate(meta):
                if sorted(ids) != sorted(s["id"] for s in meta["slides"]):
                    raise ValueError("Список слайдов не совпадает с проектом")
                pos = {sid: i + 1 for i, sid in enumerate(ids)}
                for s in meta["slides"]:
                    s["order"] = pos[s["id"]]
            store.update_project(m.group(1), mutate)
            return self._json({"ok": True})
        m = re.fullmatch(r"/api/projects/([^/]+)/edits", path)
        if m:
            require_editable(m.group(1))
            data = store.create_edits(m.group(1), body.get("items") or [])
            return self._json({"ok": True, "version": data["version"]})
        m = re.fullmatch(r"/api/projects/([^/]+)/edits/(\d+)/slides/([^/]+)/revert", path)
        if m:
            store.set_slide_reverted(m.group(1), int(m.group(2)), m.group(3), bool(body.get("revert")))
            return self._json({"ok": True})
        m = re.fullmatch(r"/api/projects/([^/]+)/export", path)
        if m:
            return self._start_export(m.group(1), body.get("format"), body.get("target"))
        if path == "/api/pick":
            return self._pick(body.get("kind"), body.get("prompt") or "")
        if path == "/api/guides":
            return self._create_guide(body)
        if path == "/api/projects":
            return self._create_project(body)
        m = re.fullmatch(r"/api/tasks/(guide|project)/([^/]+)/cancel", path)
        if m:
            tasks.cancel(m.group(1), m.group(2))
            return self._json({"ok": True})
        if path == "/api/presets":
            return self._presets(body)
        m = re.fullmatch(r"/api/(projects|guides)/([^/]+)/delete", path)
        if m:
            return self._delete(m.group(1), m.group(2))
        m = re.fullmatch(r"/api/projects/([^/]+)/reveal", path)
        if m:  # путь берётся только из статуса экспорта, браузер его не передаёт
            st = store.read_json(store.project_dir(m.group(1)) / "export-status.json") or {}
            last = (st.get("last") or {}).get(body.get("format"))
            if not last or not Path(last["path"]).exists():
                raise FileNotFoundError("Файл не найден — перемещён или удалён")
            subprocess.run(["open", "-R", last["path"]])
            return self._json({"ok": True})
        if path == "/api/shutdown":
            self._json({"ok": True})
            threading.Thread(target=self.server.shutdown, daemon=True).start()
            return None
        return self._error("Не найдено", 404)

    def _pick(self, kind, prompt):
        """Родной диалог macOS (folder | files | file). Пути остаются на сервере, браузеру — токен."""
        if kind not in ("folder", "files", "file"):
            raise ValueError("kind: folder | files | file")
        prompt = re.sub(r'["\\]', "", prompt)[:120] or "Выбери"
        if kind == "folder":
            body = 'set r to choose folder with prompt "%s" default location (path to home folder)\nreturn POSIX path of r' % prompt
        else:
            multi = " with multiple selections allowed" if kind == "files" else ""
            body = ('set fs to choose file with prompt "%s"%s default location (path to home folder)\n'
                    'if class of fs is not list then set fs to {fs}\n'
                    'set out to ""\nrepeat with f in fs\nset out to out & POSIX path of f & linefeed\nend repeat\nreturn out') % (prompt, multi)
        if not _dialog_lock.acquire(blocking=False):
            raise RuntimeError("Окно выбора уже открыто")
        try:
            r = subprocess.run(["osascript", "-"], input="activate\n" + body, capture_output=True, text=True, timeout=600)
        except subprocess.TimeoutExpired:
            return self._json({"ok": True, "cancelled": True})
        finally:
            _dialog_lock.release()
        if r.returncode != 0:
            return self._json({"ok": True, "cancelled": True})
        paths = [Path(s).resolve() for s in r.stdout.splitlines() if s.strip()]
        if not paths:
            return self._json({"ok": True, "cancelled": True})
        token = store.save_pick(paths, kind)
        return self._json({"ok": True, "token": token,
                           "items": [{"label": pick_label(p), "name": p.name, "is_dir": p.is_dir()} for p in paths]})

    def _create_guide(self, body):
        name = tasks.clean_name(body.get("name"))
        if not name:
            raise ValueError("Укажи название")
        pick = store.get_pick(body.get("token"))
        if pick["kind"] != "folder":
            raise ValueError("Выбери папку")
        src = Path(pick["paths"][0])
        sources = [{"path": str(src), "as": tasks.clean_name(src.name) or "Исходники", "is_dir": True}]
        req = {"title": name, "sources": sources, "source_label": pick_label(src),
               "options": {"examples": bool(body.get("examples", True))}}
        gname = tasks.create("guide", name, req, with_date=False)
        threading.Thread(target=tasks.copy_sources, args=("guide", gname, log), daemon=True).start()
        return self._json({"ok": True, "id": gname, "commands": store.agent_commands("гайд")})

    def _create_project(self, body):
        mode = body.get("mode")
        if mode not in ("create", "extend", "rebuild"):
            raise ValueError("Выбери задачу")
        topic = re.sub(r"\s+", " ", str(body.get("topic") or "")).strip()
        if not topic:
            raise ValueError("Укажи тему")
        guide = body.get("guide")
        g = next((g for g in guides() if g["id"] == guide), None)
        if not g or g["state"] != "ready":
            raise ValueError("Выбери гайдлайн")
        dur = body.get("duration")
        if dur not in (None, ""):
            dur = int(dur)
            if not 1 <= dur <= 600:
                raise ValueError("Длительность — от 1 до 600 минут")
        else:
            dur = None
        author = body.get("author") or {}
        author = {"name": str(author.get("name") or "").strip()[:120],
                  "position": str(author.get("position") or "").strip()[:200]}
        if not author["name"] and not author["position"]:
            author = None
        sources, base = [], None
        if mode in ("extend", "rebuild"):
            src = body.get("source") or {}
            if src.get("type") == "project":
                spdir = store.project_dir(src.get("project"))
                if not editable(spdir.name):
                    raise ValueError("Исходный проект ещё собирается")
                base = {"type": "project", "project": spdir.name, "as": "Основа"}
                sources.append({"path": str(spdir), "as": "Основа", "is_dir": True,
                                "only": ["slides", "styles.css", "assets", "project.json"]})
            elif src.get("type") == "file":
                pk = store.get_pick(src.get("token"))
                fp = Path(pk["paths"][0])
                base = {"type": "file", "as": "Основа — " + tasks.clean_name(fp.name)}
                sources.append({"path": str(fp), "as": base["as"], "is_dir": fp.is_dir()})
            else:
                raise ValueError("Выбери исходную презентацию")
        refs = []
        for ref in body.get("references") or []:  # токен целиком или {token, items: [индексы]}
            paths = store.get_pick(ref["token"] if isinstance(ref, dict) else ref)["paths"]
            if isinstance(ref, dict):
                paths = [paths[i] for i in ref.get("items", []) if isinstance(i, int) and 0 <= i < len(paths)]
            refs += paths
        ref_entries = tasks.source_entries(list(dict.fromkeys(refs)))
        taken = {s["as"].lower() for s in sources}
        for e in ref_entries:
            if e["as"].lower() in taken:
                e["as"] = "Фактура — " + e["as"]
        sources += ref_entries
        req = {"mode": mode, "topic": topic, "guide": guide, "duration": dur, "author": author,
               "base": base, "references": [e["as"] for e in ref_entries], "sources": sources,
               "options": {"self_check": bool(body.get("self_check", True))}}

        def extra(tmp, name):
            store.write_json(tmp / "project.json", {
                "id": re.sub(r"\W+", "-", name.lower()).strip("-"), "name": name,
                "created_at": store.now(), "updated_at": store.now(),
                "guideline_set_id": guide, "slides": []})
        pname = tasks.create("project", topic, req, with_date=True, make_extra=extra)
        threading.Thread(target=tasks.copy_sources, args=("project", pname, log), daemon=True).start()
        return self._json({"ok": True, "name": pname, "commands": store.agent_commands("создать")})

    def _delete(self, what, name):
        """Проект или гайд — в Корзину macOS. Пока над ним работают агент или экспорт — нельзя."""
        if what == "projects":
            d = store.project_dir(name)
            if (d / "lifecycle.json").exists() and tasks.view("project", name)["state"] == "working":
                raise RuntimeError("Агент собирает проект. Дождись конца или отмени")
            if any(store.iteration_summary(name, n)["status"] == "in_progress" for n in store.edit_versions(name)):
                raise RuntimeError("Агент вносит правки — удалить можно после")
            if name in _exporting:
                raise RuntimeError("Идёт экспорт — дождись окончания")
        else:
            d = tasks.task_dir("guide", name)
            if not d.is_dir():
                raise FileNotFoundError("Гайдлайн не найден: %s" % name)
            if (d / "lifecycle.json").exists() and tasks.view("guide", name)["state"] == "working":
                raise RuntimeError("Агент собирает гайдлайн. Дождись конца или отмени")
        target = store.move_to_trash(d)
        log("delete %s %s -> %s" % (what, name, target))
        return self._json({"ok": True, "trash_name": target.name})

    def _presets(self, body):
        action = body.get("action")
        name = str(body.get("name") or "").strip()[:120]
        position = str(body.get("position") or "").strip()[:200]
        presets = [p for p in load_presets() if not (p["name"] == name and p["position"] == position)]
        if action == "save":
            if not name and not position:
                raise ValueError("Заполни имя или должность")
            presets.insert(0, {"name": name, "position": position})
        elif action != "delete":
            raise ValueError("action: save | delete")
        store.write_json(PRESETS, presets[:20])
        return self._json({"ok": True, "presets": presets[:20]})

    def _start_export(self, name, fmt, target=None):
        if fmt not in ("pdf", "jpeg", "pptx"):
            raise ValueError("Формат должен быть pdf, jpeg или pptx")
        dest = None
        if target:
            pk = store.get_pick(target)
            if pk["kind"] != "folder":
                raise ValueError("Для экспорта нужна папка")
            dest = Path(pk["paths"][0])
        store.project_dir(name)
        with _export_lock:
            if name in _exporting:
                raise RuntimeError("Экспорт этого проекта уже идёт")
            _exporting.add(name)
        base = "http://127.0.0.1:%d" % self.server.server_address[1]

        def run():
            try:
                exporter.export(name, fmt, base, log, dest)
            except Exception:
                pass  # статус и ошибка уже записаны в export-status.json
            finally:
                with _export_lock:
                    _exporting.discard(name)
        threading.Thread(target=run, daemon=True).start()
        return self._json({"ok": True})


def running_instance():
    info = store.read_json(SERVER_INFO)
    if not info:
        return None
    try:
        os.kill(info["pid"], 0)
        with socket.create_connection(("127.0.0.1", info["port"]), timeout=1):
            return info
    except (OSError, KeyError):
        return None


def free_port(start=8765):
    for port in range(start, start + 50):
        with socket.socket() as s:
            try:
                s.bind(("127.0.0.1", port))
                return port
            except OSError:
                continue
    raise RuntimeError("Нет свободного порта")


def recover():
    """После аварийного завершения: незавершённый экспорт помечается как прерванный."""
    for name in store.list_projects():
        p = store.project_dir(name) / "export-status.json"
        st = store.read_json(p)
        if st and st.get("status") == "in_progress":
            st.update(status="failed", error="Экспорт прерван перезапуском приложения", finished_at=store.now())
            store.write_json(p, st)
    for kind, name in tasks.pending_copies():  # копирование исходников прервалось — продолжаем
        threading.Thread(target=tasks.copy_sources, args=(kind, name, log), daemon=True).start()


def main():
    other = running_instance()
    if other:
        print("Уже запущено: %s" % other["url"])
        if "--open" in sys.argv:
            webbrowser.open(other["url"])
        return
    store.STATE.mkdir(exist_ok=True)
    recover()
    port = int(os.environ.get("PD_PORT") or free_port())
    httpd = ThreadingHTTPServer(("127.0.0.1", port), Handler)
    httpd.daemon_threads = True
    url = "http://127.0.0.1:%d/" % port
    store.write_json(SERVER_INFO, {"pid": os.getpid(), "port": port, "url": url, "started_at": store.now()})
    log("start %s" % url)
    print("Дизайнер презентаций: %s  (остановить: Ctrl+C или stop.command)" % url, flush=True)
    if "--open" in sys.argv:
        webbrowser.open(url)
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        httpd.server_close()
        if (store.read_json(SERVER_INFO) or {}).get("pid") == os.getpid():
            SERVER_INFO.unlink()
        log("stop")
        print("Остановлено")


if __name__ == "__main__":
    main()
