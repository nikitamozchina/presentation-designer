# HTML-слайды → редактируемый PPTX. Порт раннего прототипа (python-pptx + Playwright) без pip:
#   1) /pptx-scan/<проект> — Chrome обходит видимые слайды (SCAN_JS) и отдаёт геометрию через --dump-dom;
#   2) /pptx-raster/<проект>/<слайд> — «растровый слой»: тот же обход помечает то, что не переносится объектами
#      (svg, баннеры .ad, фильтры, тени, трансформации, сложные фоны…), остальное прячется, фон прозрачный; скриншот 2×;
#   3) JSON → core.pptx: текстбоксы, фигуры, картинки; растровые элементы — PNG слоя с обрезкой srcRect.
import html as _html, json, re, struct
from pathlib import Path
from urllib.parse import quote, unquote, urlparse

from . import pptx, store

SCALE = 2  # растровый слой снимается в 2×
SLACK = tuple(float(v) for v in __import__("os").environ.get("PD_PPTX_SLACK", "0.025,4").split(","))  # подбирается тестом pptx_compare

# Общий обход: используется и сканером, и растровым слоем — набор «растровых» элементов совпадает по построению.
COMMON_JS = r"""
const cs = (w, el, p) => w.getComputedStyle(el, p || null);
const alpha = c => { const m = /rgba?\(([^)]+)\)/.exec(c || ''); if (!m) return 0; const p = m[1].split(/[ ,\/]+/).filter(Boolean); return p.length > 3 ? parseFloat(p[3]) : 1; };
const urlOf = s => { const m = /^url\("?(.*?)"?\)$/.exec((s || '').trim()); return m ? m[1] : null; };
const natural = url => new Promise(res => { const im = new Image(); im.onload = () => res([im.naturalWidth, im.naturalHeight]); im.onerror = () => res([0, 0]); im.src = url; });
const OK_IMG = /\.(png|jpe?g|gif)$/i;
const ownFile = u => { try { const x = new URL(u); return x.origin === location.origin && decodeURIComponent(x.pathname).startsWith('/p/' + PROJECT + '/') && OK_IMG.test(x.pathname); } catch (e) { return false; } };
const topLayers = bi => { let d = 0, n = 1; for (const ch of bi) { if (ch === '(') d++; else if (ch === ')') d--; else if (ch === ',' && d === 0) n++; } return n; };

function rasterReason(el, s) {
  const tag = el.tagName.toLowerCase();
  if (tag === 'svg') return 'график (svg)';
  if (tag === 'canvas' || tag === 'video' || tag === 'iframe' || tag === 'object') return tag;
  if (el.classList.contains('ad')) return 'баннер';
  if (tag === 'img' && !ownFile(el.currentSrc || el.src)) return 'картинка ' + ((el.currentSrc || el.src).split('/').pop() || '').slice(0, 40);
  if (s.filter !== 'none') return 'фильтр';
  if (s.transform !== 'none') return 'поворот/масштаб';
  if (s.boxShadow !== 'none') return 'тень';
  if (s.textShadow !== 'none') return 'тень текста';
  if (s.clipPath !== 'none') return 'обрезка clip-path';
  if ((s.webkitMaskImage || s.maskImage || 'none') !== 'none') return 'маска';
  if (s.mixBlendMode !== 'normal') return 'режим наложения';
  if ((s.backdropFilter || s.webkitBackdropFilter || 'none') !== 'none') return 'размытие фона';
  const bi = s.backgroundImage;
  if (bi !== 'none') {
    if (/(radial|conic|repeating)-gradient/.test(bi) || topLayers(bi) > 1) return 'сложный фон';
    const u = urlOf(bi); if (u && !ownFile(u)) return 'фон-картинка';
  }
  const rs = ['TopLeft', 'TopRight', 'BottomRight', 'BottomLeft'].map(k => s['border' + k + 'Radius']);
  const zero = v => parseFloat(v) === 0;
  const twoSame = (rs[0] === rs[1] && zero(rs[2]) && zero(rs[3])) || (rs[2] === rs[3] && zero(rs[0]) && zero(rs[1]));  // столбцы графиков
  if ((new Set(rs).size > 1 && !twoSame) || rs.some(v => v.trim().includes(' '))) return 'разные скругления углов';
  return null;
}

async function scan(doc) {
  const w = doc.defaultView, S = doc.querySelector('.slide') || doc.body, R0 = S.getBoundingClientRect();
  const rel = r => ({x: r.left - R0.left, y: r.top - R0.top, w: r.width, h: r.height});
  const items = [], pending = [];
  const isInline = n => n.nodeType === 1 && (n.tagName === 'BR' || cs(w, n).display === 'inline');
  function pseudoText(el, p) {
    const s = cs(w, el, p);
    if (!s.content || s.content === 'none' || s.content === 'normal' || s.position === 'absolute' || s.display === 'none') return null;
    const m = /^"(.*)"$/.exec(s.content);
    return m && m[1] ? {t: m[1].replace(/\\(.)/g, '$1'), s} : null;
  }
  const hasText = el => [...el.childNodes].some(n => (n.nodeType === 3 && n.textContent.trim()) || (isInline(n) && n.textContent.trim()))
    || ['::before', '::after'].some(p => pseudoText(el, p));
  function clipOf(el) {  // ближайший предок с overflow ≠ visible (включая сам слайд)
    for (let a = el.parentElement; a; a = a === S ? null : a.parentElement) {
      const s = cs(w, a);
      if (s.overflow !== 'visible' || a === S) {
        const r = a.getBoundingClientRect(), bl = parseFloat(s.borderLeftWidth) || 0, bt = parseFloat(s.borderTopWidth) || 0;
        return {x: r.left - R0.left + bl, y: r.top - R0.top + bt, w: a.clientWidth, h: a.clientHeight,
                radius: Math.max(0, (parseFloat(s.borderTopLeftRadius) || 0) - bl)};
      }
    }
    return null;
  }
  function box(el, s, r, op) {
    const bw = ['Top','Right','Bottom','Left'].map(k => parseFloat(s['border' + k + 'Width']) || 0);
    const bs = ['Top','Right','Bottom','Left'].map(k => s['border' + k + 'Style']);
    const bc = ['Top','Right','Bottom','Left'].map(k => s['border' + k + 'Color']);
    const img = s.backgroundImage, url = urlOf(img);
    const hasBorder = bw.some((v, i) => v > 0 && bs[i] !== 'none' && bs[i] !== 'hidden' && alpha(bc[i]) > 0);
    if (!(alpha(s.backgroundColor) > 0 || img !== 'none' || hasBorder)) return;
    const it = {k: 'box', ...rel(r), bg: s.backgroundColor, grad: /^linear-gradient/.test(img) ? img : null,
                url, bw, bs, bc, radius: parseFloat(s.borderTopLeftRadius) || 0, op, bgSize: s.backgroundSize, bgPos: s.backgroundPosition,
                rtop: parseFloat(s.borderTopLeftRadius) || 0, rbottom: parseFloat(s.borderBottomLeftRadius) || 0};
    if (url) { it.clip = clipOf(el); pending.push(natural(url).then(n => { it.nw = n[0]; it.nh = n[1]; })); }
    items.push(it);
  }
  function runs(el, out, op, deco) {
    const s0 = cs(w, el);
    deco = (deco || '') + ' ' + s0.textDecorationLine;  // оформление текста наследуется визуально, не через computed style
    const push = (t, s) => out.push({t, size: parseFloat(s.fontSize), weight: parseInt(s.fontWeight) || 400,
      italic: s.fontStyle === 'italic', family: s.fontFamily, color: s.color, op, ls: s.letterSpacing,
      upper: s.textTransform === 'uppercase', u: /underline/.test(deco), strike: /line-through/.test(deco)});
    const b = pseudoText(el, '::before'); if (b) push(b.t, b.s);
    for (const n of el.childNodes) {
      if (n.nodeType === 3) push(n.textContent, s0);
      else if (n.nodeType === 1 && n.tagName === 'BR') out.push({br: true, size: parseFloat(s0.fontSize)});
      else if (isInline(n)) { const sn = cs(w, n); if (sn.display !== 'none' && sn.visibility !== 'hidden') runs(n, out, op * (parseFloat(sn.opacity) || 1), deco); }
    }
    const a = pseudoText(el, '::after'); if (a) push(a.t, a.s);
  }
  function text(el, s, op) {
    const kids = [...el.childNodes].filter(n => (n.nodeType === 3 && n.textContent.trim()) || isInline(n));
    let rects = [], bx = null;
    if (kids.length) {
      const rg = doc.createRange(); rg.setStartBefore(kids[0]); rg.setEndAfter(kids[kids.length - 1]);
      rects = [...rg.getClientRects()].filter(r => r.width > 0.5); bx = rg.getBoundingClientRect();
    }
    const er = el.getBoundingClientRect();
    if (!bx || !bx.width) bx = er;  // текст только из ::before/::after
    else if (pseudoText(el, '::before')) {  // ▲ перед текстом — рамка от начала содержимого
      const l = er.left + parseFloat(s.paddingLeft) + parseFloat(s.borderLeftWidth);
      bx = {left: l, top: bx.top, width: bx.right - l, height: bx.height};
    }
    const out = []; runs(el, out, op);
    const lh = s.lineHeight === 'normal' ? null : parseFloat(s.lineHeight);
    const tops = new Set(rects.map(r => Math.round(r.top / 4)));
    items.push({k: 'text', x: bx.left - R0.left, y: bx.top - R0.top, w: bx.width, h: bx.height, runs: out,
                align: s.textAlign, lh, fs: parseFloat(s.fontSize), lines: Math.max(1, tops.size),
                tag: el.tagName.toLowerCase(), cls: el.className && el.className.baseVal === undefined ? el.className : ''});
  }
  function pseudoBox(el, p, op) {  // маркеры списков, затемнения поверх фото: абсолютные ::before/::after с фоном
    const s = cs(w, el, p);
    if (!s.content || s.content === 'none' || s.position !== 'absolute' || s.display === 'none') return;
    const er = el.getBoundingClientRect(), es = cs(w, el);
    const x = er.left + parseFloat(es.borderLeftWidth) + (parseFloat(s.left) || 0), y = er.top + parseFloat(es.borderTopWidth) + (parseFloat(s.top) || 0);
    const r = {left: x, top: y, width: parseFloat(s.width) || 0, height: parseFloat(s.height) || 0};
    if (r.width > 0 && r.height > 0) box(el, s, r, op * (parseFloat(s.opacity) || 1));
  }
  function extent(el) {  // растр целиком: подписи svg и дети с overflow:visible выходят за рамку элемента
    let r = el.getBoundingClientRect(), L = r.left, T = r.top, Rr = r.right, B = r.bottom;
    if (cs(w, el).overflow === 'visible' || el.tagName.toLowerCase() === 'svg')
      for (const d of el.querySelectorAll('*')) { const q = d.getBoundingClientRect(); if (q.width || q.height) { L = Math.min(L, q.left); T = Math.min(T, q.top); Rr = Math.max(Rr, q.right); B = Math.max(B, q.bottom); } }
    const pad = el.tagName.toLowerCase() === 'svg' ? 4 : 1;
    L = Math.max(L - pad, R0.left); T = Math.max(T - pad, R0.top); Rr = Math.min(Rr + pad, R0.right); B = Math.min(B + pad, R0.bottom);
    return {x: L - R0.left, y: T - R0.top, w: Math.max(0, Rr - L), h: Math.max(0, B - T)};
  }
  function img(el, s, r, op) {
    const bl = parseFloat(s.borderLeftWidth) + parseFloat(s.paddingLeft), bt = parseFloat(s.borderTopWidth) + parseFloat(s.paddingTop);
    const br = parseFloat(s.borderRightWidth) + parseFloat(s.paddingRight), bb = parseFloat(s.borderBottomWidth) + parseFloat(s.paddingBottom);
    if (parseFloat(s.borderTopWidth) > 0 || alpha(s.backgroundColor) > 0) box(el, s, r, op);  // кольцо/подложка иконки — фигурой
    const c = {left: r.left + bl, top: r.top + bt, width: r.width - bl - br, height: r.height - bt - bb};
    items.push({k: 'img', src: el.currentSrc || el.src, ...rel(c), nw: el.naturalWidth, nh: el.naturalHeight, fit: s.objectFit, pos: s.objectPosition,
                radius: Math.max(0, (parseFloat(s.borderTopLeftRadius) || 0) - Math.max(bl, bt)), op, clip: clipOf(el)});
  }
  function walk(el, op) {
    const s = cs(w, el);
    if (s.display === 'none') return;
    op *= parseFloat(s.opacity);
    if (op < 0.01) return;
    const r = el.getBoundingClientRect();
    const why = rasterReason(el, s);
    if (why) {
      if (s.visibility !== 'hidden' && r.width > 0 && r.height > 0) {
        const e = extent(el);
        if (e.w > 0 && e.h > 0) items.push({k: 'raster', ...e, why, text: (el.textContent || '').replace(/\s+/g, ' ').trim().slice(0, 40), _el: el});
      }
      return;
    }
    if (s.visibility !== 'hidden') {
      if (el.tagName === 'IMG') { if (r.width > 0 && r.height > 0) img(el, s, r, op); return; }
      if (el !== S && r.width > 0 && r.height > 0) box(el, s, r, op);
      pseudoBox(el, '::before', op);
      if (hasText(el)) text(el, s, op);
    }
    for (const c of el.children) if (!isInline(c)) walk(c, op);
    if (s.visibility !== 'hidden') pseudoBox(el, '::after', op);
  }
  function fontFaces() {  // @font-face, которые документ реально объявил: из них — файлы и начертания
    const out = [];
    const visit = (rules, base) => { for (const r of rules) {
      if (r.type === 5) {
        const m = /url\(\s*["']?([^"')]+)["']?\s*\)/.exec(r.style.getPropertyValue('src'));
        out.push({family: r.style.getPropertyValue('font-family').replace(/^["']|["']$/g, '').trim(),
                  weight: r.style.getPropertyValue('font-weight') || '400', style: r.style.getPropertyValue('font-style') || 'normal',
                  src: m ? new URL(m[1], base).href : null});
      } else if (r.cssRules) visit(r.cssRules, base);
    } };
    for (const sh of doc.styleSheets) { try { visit(sh.cssRules, sh.href || doc.baseURI); } catch (e) { /* чужой домен */ } }
    return out;
  }
  const ss = cs(w, S);
  const bg = {color: ss.backgroundColor, url: urlOf(ss.backgroundImage), grad: /^linear-gradient/.test(ss.backgroundImage) ? ss.backgroundImage : null,
              size: ss.backgroundSize, pos: ss.backgroundPosition};
  const sWhy = rasterReason(S, ss);
  if (sWhy && sWhy !== 'поворот/масштаб') { items.push({k: 'raster', x: 0, y: 0, w: R0.width, h: R0.height, why: 'фон слайда: ' + sWhy, text: '', _el: S}); bg.url = null; bg.grad = null; }
  else if (bg.url) pending.push(natural(bg.url).then(n => { bg.nw = n[0]; bg.nh = n[1]; }));
  if (!sWhy) for (const c of S.children) walk(c, 1);
  await Promise.all(pending);
  return {bg, items, w: R0.width, h: R0.height, fonts: fontFaces(), fonts_failed: [...doc.fonts].filter(f => f.status === 'error').map(f => f.family)};
}

async function load(f, src) {
  await new Promise((res, rej) => { f.onload = res; f.onerror = rej; f.src = src; });
  const d = f.contentDocument;
  await d.fonts.ready;
  await Promise.all([...d.images].map(i => i.complete ? null : new Promise(r => { i.onload = i.onerror = r; })));
  return d;
}
"""

SCAN_JS = COMMON_JS + r"""
(async () => {
  const out = [];
  for (const [id, src] of ITEMS) {
    const f = document.createElement('iframe');
    f.setAttribute('sandbox', 'allow-same-origin');
    document.body.appendChild(f);
    try {
      const res = await scan(await load(f, src));
      res.items.forEach(it => { delete it._el; });
      out.push(Object.assign({slide: id}, res));
    } catch (e) { out.push({slide: id, error: String(e)}); }
    f.remove();
  }
  document.getElementById('result').textContent = '__RESULT__' + JSON.stringify(out) + '__END__';
})().catch(e => { document.getElementById('result').textContent = '__RESULT__' + JSON.stringify([{slide: '*', error: String(e)}]) + '__END__'; });
"""

RASTER_JS = COMMON_JS + r"""
(async () => {
  const f = document.getElementById('slide');
  const d = await load(f, SRC);
  const res = await scan(d);
  res.items.filter(it => it.k === 'raster').forEach(it => it._el.setAttribute('data-pd-raster', ''));
  const st = d.createElement('style');
  st.textContent = 'html,body{background:transparent!important}*{visibility:hidden!important}' +
                   '[data-pd-raster],[data-pd-raster] *{visibility:visible!important}';
  d.head.appendChild(st);
  document.title = 'ready';
})();
"""


def _js_const(name, value):
    return "const %s = %s;\n" % (name, json.dumps(value, ensure_ascii=True).replace("</", "<\\/"))


def scan_page(project, base):
    from .exporter import visible_slides
    items = [(s["id"], "%s/p/%s/%s" % (base, quote(project), quote(s["file"]))) for s in visible_slides(project)]
    return ("<!doctype html><meta charset='utf-8'><style>iframe{width:1920px;height:1080px;border:0;display:block}</style>"
            '<pre id="result"></pre><script>%s%s%s</script>' % (_js_const("ITEMS", items), _js_const("PROJECT", project), SCAN_JS))


def raster_page(project, slide_id, base):
    from .exporter import visible_slides
    s = next((s for s in visible_slides(project) if s["id"] == slide_id), None)
    if not s:
        raise FileNotFoundError("Слайд не найден: %s" % slide_id)
    src = "%s/p/%s/%s" % (base, quote(project), quote(s["file"]))
    return ("<!doctype html><meta charset='utf-8'><style>html,body{margin:0;background:transparent;overflow:hidden}"
            "iframe{position:absolute;left:0;top:0;width:1920px;height:1080px;border:0;background:transparent}</style>"
            '<iframe id="slide" sandbox="allow-same-origin"></iframe><script>%s%s%s</script>' % (
                _js_const("SRC", src), _js_const("PROJECT", project), RASTER_JS))


# ---------- шрифты: @font-face документа → файл → имя для PowerPoint ----------

def font_info(data):
    """OTF/TTF → {legacy (nameID 1), bold (fsSelection bit 5), italic (bit 0)}; Office на маке ищет шрифт так."""
    if data[:4] not in (b"\x00\x01\x00\x00", b"OTTO", b"true"):
        return None
    tables = {}
    for i in range(struct.unpack(">H", data[4:6])[0]):
        tag, _, off, ln = struct.unpack(">4sIII", data[12 + 16 * i: 28 + 16 * i])
        tables[tag] = off
    names = {}
    if b"name" in tables:
        off = tables[b"name"]
        _, count, soff = struct.unpack(">HHH", data[off:off + 6])
        for j in range(count):
            pid, eid, lid, nid, ln, o = struct.unpack(">HHHHHH", data[off + 6 + 12 * j: off + 18 + 12 * j])
            raw = data[off + soff + o: off + soff + o + ln]
            if nid in (1, 2):
                if (pid, eid) in ((3, 1), (0, 3), (0, 4)) and (nid not in names or lid == 0x409):
                    names[nid] = raw.decode("utf-16-be", "replace")
                elif pid == 1 and nid not in names:
                    names[nid] = raw.decode("mac_roman", "replace")
    if not names.get(1):
        return None
    sel = struct.unpack(">H", data[tables[b"OS/2"] + 62: tables[b"OS/2"] + 64])[0] if b"OS/2" in tables else 0
    sub = (names.get(2) or "").lower()
    return {"legacy": names[1], "bold": bool(sel & 0x20) or sub in ("bold", "bold italic"), "italic": bool(sel & 1) or "italic" in sub}


def _weight(v):
    v = str(v).strip().split()[0] if str(v).strip() else "400"
    return {"normal": 400, "bold": 700, "lighter": 300, "bolder": 700}.get(v, None) or int(float(v))


def match_face(faces, family, weight, italic):
    """Алгоритм CSS font matching (CSS Fonts 4, §5.2) по объявленным @font-face."""
    cand = [f for f in faces if f["family"].lower() == family.lower()]
    if not cand:
        return None
    st = [f for f in cand if (f["style"] != "normal") == italic] or cand
    ws = sorted({f["w"] for f in st})
    if weight in ws:
        pick = weight
    elif 400 <= weight <= 500:
        up = [x for x in ws if weight < x <= 500]
        down = [x for x in ws if x < weight]
        pick = min(up) if up else (max(down) if down else min(x for x in ws if x > 500))
    elif weight < 400:
        down = [x for x in ws if x < weight]
        pick = max(down) if down else min(ws)
    else:
        up = [x for x in ws if x > weight]
        pick = min(up) if up else max(ws)
    return next(f for f in st if f["w"] == pick)


class Fonts:
    """Сопоставление run → (имя, bold) и список использованных файлов шрифтов (для папки «Шрифты»)."""

    def __init__(self, project, declared):
        self.project, self.faces, self.used, self._info = project, [], {}, {}
        for d in declared or []:
            try:
                self.faces.append(dict(d, w=_weight(d.get("weight"))))
            except ValueError:
                continue

    def resolve(self, css_family, weight, italic):
        fams = [f.strip().strip("'\"") for f in (css_family or "").split(",") if f.strip()]
        for fam in fams:
            face = match_face(self.faces, fam, weight, italic)
            if not face:
                continue
            info = self._file_info(face.get("src"))
            if info:
                return info["legacy"], info["bold"], italic and not info["italic"]
            return fam, weight >= 600, italic  # woff2 и т.п. — имя из CSS
        first = next((f for f in fams if f.lower() not in ("sans-serif", "serif", "system-ui", "-apple-system", "inherit")), "Arial")
        return first, weight >= 600, italic

    def _file_info(self, src):
        if not src:
            return None
        if src not in self._info:
            info = None
            try:
                f = local_file(self.project, src)
                info = font_info(f.read_bytes()) if f.suffix.lower() in (".otf", ".ttf") and f.is_file() else None
                if info:
                    self.used[str(f)] = f
            except (ValueError, OSError, struct.error, IndexError):
                info = None
            self._info[src] = info
        return self._info[src]

    def files(self):
        return [p for p in self.used.values() if p]


# ---------- разбор CSS-значений ----------

def rgba(s):
    m = re.match(r"rgba?\(([^)]+)\)", (s or "").strip())
    if not m:
        return None
    p = [float(x) for x in re.split(r"[ ,/]+", m.group(1).strip()) if x]
    return (p[0], p[1], p[2], p[3] if len(p) > 3 else 1.0)


def mul_alpha(c, op):
    return (c[0], c[1], c[2], c[3] * op) if c else None


def gradient(css, op=1.0):
    """linear-gradient(...) из computed style → (угол OOXML, [(позиция 0..1, цвет)]). CSS 90° (слева направо) = OOXML 0°."""
    body = css[css.index("(") + 1: css.rindex(")")]
    parts = re.split(r",(?![^()]*\))", body)
    ang = 180.0
    first = parts[0].strip()
    if first.endswith("deg"):
        ang = float(first[:-3]); parts = parts[1:]
    elif first.startswith("to "):
        ang = {"to top": 0, "to right": 90, "to bottom": 180, "to left": 270, "to top right": 45, "to right top": 45,
               "to bottom right": 135, "to right bottom": 135, "to bottom left": 225, "to left bottom": 225,
               "to top left": 315, "to left top": 315}.get(first, 180); parts = parts[1:]
    stops = []
    for i, p in enumerate(parts):
        mc = re.search(r"rgba?\([^)]+\)", p)
        if not mc:
            continue
        m = re.search(r"([\d.]+)%\s*$", p.strip())
        pos = float(m.group(1)) / 100 if m else (i / max(len(parts) - 1, 1))
        stops.append((pos, mul_alpha(rgba(mc.group(0)), op)))
    return ((ang - 90) % 360, stops) if len(stops) >= 2 else None


def _pct(v, default=0.5):
    v = v.strip()
    if v.endswith("%"):
        return float(v[:-1]) / 100
    return {"left": 0, "top": 0, "center": 0.5, "right": 1, "bottom": 1}.get(v, default)


def fit_image(nw, nh, x, y, w, h, fit="fill", pos="50% 50%"):
    """→ (x, y, w, h, crop) как CSS object-fit / background-size (cover|contain|fill)."""
    if not (nw and nh and w and h) or fit in ("fill", None, ""):
        return x, y, w, h, None
    p = (pos or "50% 50%").split()
    px, py = _pct(p[0]), _pct(p[1] if len(p) > 1 else "50%")
    if fit in ("contain", "scale-down"):
        scale = min(w / nw, h / nh)
        if fit == "scale-down":
            scale = min(scale, 1)
        dw, dh = nw * scale, nh * scale
        return x + (w - dw) * px, y + (h - dh) * py, dw, dh, None
    scale = max(w / nw, h / nh)  # cover / auto
    sw, sh = nw * scale, nh * scale
    ox, oy = (sw - w) * px, (sh - h) * py
    return x, y, w, h, (ox / sw, oy / sh, (sw - w - ox) / sw, (sh - h - oy) / sh)


def apply_clip(x, y, w, h, crop, clip):
    """Обрезка картинки по предку с overflow:hidden: сужаем рамку и добавляем к srcRect. None — целиком снаружи."""
    if not clip:
        return x, y, w, h, crop
    l, t, r, b = crop or (0, 0, 0, 0)
    vw, vh = 1 - l - r, 1 - t - b  # видимая доля исходника на всю рамку
    cx0, cy0, cx1, cy1 = clip["x"], clip["y"], clip["x"] + clip["w"], clip["y"] + clip["h"]
    nx0, ny0, nx1, ny1 = max(x, cx0), max(y, cy0), min(x + w, cx1), min(y + h, cy1)
    if nx1 - nx0 < 0.5 or ny1 - ny0 < 0.5:
        return None
    l += (nx0 - x) / w * vw; r += (x + w - nx1) / w * vw
    t += (ny0 - y) / h * vh; b += (y + h - ny1) / h * vh
    crop = (l, t, r, b) if any(abs(v) > 1e-6 for v in (l, t, r, b)) else None
    return nx0, ny0, nx1 - nx0, ny1 - ny0, crop


# ---------- сборка ----------

def local_file(project, url):
    """URL файла слайда → файл проекта (только внутри проекта)."""
    path = unquote(urlparse(url).path)
    prefix = "/p/%s/" % project
    if not path.startswith(prefix):
        raise ValueError("Файл не из проекта: %s" % url)
    pdir = store.project_dir(project)
    return store.inside(pdir, pdir / path[len(prefix):])


IMG_EXT = {".png": "png", ".jpg": "jpeg", ".jpeg": "jpeg", ".gif": "gif"}


def add_image(slide, project, url, x, y, w, h, nw=0, nh=0, fit="fill", pos=None, clip=None, op=1.0, radius=0,
              name="Картинка", warn=print):
    f = local_file(project, url)
    ext = IMG_EXT.get(f.suffix.lower())
    if not ext or not f.is_file():
        warn("пропущена картинка %s" % f.name)
        return
    x, y, w, h, crop = fit_image(nw, nh, x, y, w, h, fit, pos)
    full = (x, y, w, h)
    c = apply_clip(x, y, w, h, crop, clip)
    if c is None:
        return
    x, y, w, h, crop = c
    kind = "roundRect" if radius > 0.5 else "rect"
    if clip and clip.get("radius", 0) > 0.5 and abs(x - clip["x"]) < 2 and abs(y - clip["y"]) < 2 \
            and abs(w - clip["w"]) < 2 and abs(h - clip["h"]) < 2:  # фото на всю карточку со скруглением
        kind, radius = "roundRect", clip["radius"]
    slide.picture(f.read_bytes(), ext, x, y, w, h, crop=crop, alpha=op, kind=kind, radius=radius, name=name)
    return full


def add_box(slide, project, it, warn):
    x, y, w, h, op = it["x"], it["y"], it["w"], it["h"], it["op"]
    if it.get("url"):
        size = it.get("bgSize") or "auto"
        fit = "cover" if size in ("cover", "auto") else ("contain" if size == "contain" else "fill")
        add_image(slide, project, it["url"], x, y, w, h, it.get("nw"), it.get("nh"), fit, it.get("bgPos"), it.get("clip"),
                  op, it["radius"], "Фото", warn)
        return
    bw, bs, bc = it["bw"], it["bs"], it["bc"]
    on = [bw[i] > 0 and bs[i] not in ("none", "hidden") and (rgba(bc[i]) or (0, 0, 0, 0))[3] > 0 for i in range(4)]
    uniform = all(on) and len({round(v, 1) for v in bw}) == 1 and len(set(bc)) == 1
    if any(on) and not uniform:  # частичные рамки (таблицы, разделители) — тонкими прямоугольниками
        sides = [(x, y, w, bw[0]), (x + w - bw[1], y, bw[1], h), (x, y + h - bw[2], w, bw[2]), (x, y, bw[3], h)]
        for i, (sx, sy, sw, sh) in enumerate(sides):
            if on[i]:
                slide.shape(sx, sy, sw, sh, fill=mul_alpha(rgba(bc[i]), op), name="Линия")
    fill = mul_alpha(rgba(it["bg"]), op)
    grad = gradient(it["grad"], op) if it.get("grad") else None
    line = None
    r = it["radius"]
    if uniform:
        line = (mul_alpha(rgba(bc[0]), op), bw[0], bs[0])
        x, y, w, h = x + bw[0] / 2, y + bw[0] / 2, w - bw[0], h - bw[0]  # CSS-рамка внутри блока, линия pptx — по центру края
        r -= bw[0] / 2
    if not grad and not (fill and fill[3] > 0.001) and not line:
        return
    rt, rb = it.get("rtop", it["radius"]), it.get("rbottom", it["radius"])
    flip = False
    if abs(rt - rb) > 0.5:  # скруглены только верхние (столбцы) или только нижние углы
        kind, r, flip = "round2SameRect", max(rt, rb) - (bw[0] / 2 if uniform else 0), rb > rt
    else:
        kind = "ellipse" if r >= min(w, h) / 2 - 0.5 and abs(w - h) < 1.5 else ("roundRect" if r > 0.5 else "rect")
    slide.shape(x, y, w, h, kind=kind, radius=r, fill=fill, gradient=grad, line=line, flip_v=flip,
                name="Плашка" if (fill or grad) else "Рамка")


def add_text(slide, it, fonts):
    x, y, w, h = it["x"], it["y"], it["w"], it["h"]
    one = it["lines"] <= 1
    slack = SLACK[0] * w + SLACK[1]  # запас ширины, чтобы округления PowerPoint не давали лишних переносов
    align = it.get("align") or "left"
    if align == "center":
        x -= slack / 2
    elif align in ("right", "end"):
        x -= slack
    w += slack
    fs = max([r["size"] for r in it["runs"] if not r.get("br")] or [it["fs"]])
    if it.get("lh") and it["lh"] < 1.2 * fs:  # PowerPoint поднимает строки при плотном межстрочном
        y += (1.2 * fs - it["lh"]) * 0.95
    runs, prev_space = [], True
    for r in it["runs"]:
        if r.get("br"):
            runs.append({"br": True, "size_px": r.get("size")}); prev_space = True
            continue
        t = re.sub(r"[ \t\r\n]+", " ", r["t"])
        if prev_space:
            t = t.lstrip(" ")
        if not t:
            continue
        prev_space = t.endswith(" ")
        font, bold, italic = fonts.resolve(r.get("family"), r.get("weight", 400), bool(r.get("italic")))
        ls = r.get("ls") or "normal"
        runs.append({"text": t.upper() if r.get("upper") else t, "size_px": r["size"], "font": font, "bold": bold,
                     "italic": italic, "underline": r.get("u"), "strike": r.get("strike"),
                     "color": mul_alpha(rgba(r["color"]), r.get("op", 1)),
                     "spacing_px": float(ls[:-2]) if ls.endswith("px") else 0})
    while runs and runs[-1].get("text", "").endswith(" "):
        runs[-1]["text"] = runs[-1]["text"].rstrip(" ")
        if not runs[-1]["text"]:
            runs.pop()
    if not any(r.get("text") for r in runs):
        return False
    a = {"center": "ctr", "right": "r", "end": "r", "justify": "just"}.get(align, "l")
    slide.text(x, y, w, h, [{"align": a, "line_px": it.get("lh"), "runs": runs}], wrap=not one,
               name="Текст: " + "".join(r.get("text", "") for r in runs)[:40])
    return True


def build(project, scan, raster_pngs, out_path, warn=print, title=None):
    """scan — результат SCAN_JS; raster_pngs — {slide_id: PNG слоя}. → {slides, raster: [...], fonts: [файлы]}."""
    declared = [f for sd in scan for f in sd.get("fonts", [])]
    fonts = Fonts(project, declared)
    regular = fonts.resolve(declared[0]["family"], 400, False)[0] if declared else "Arial"
    bold = fonts.resolve(declared[0]["family"], 600, False)[0] if declared else "Arial"
    deck = pptx.Deck(title=title or project, fonts=(bold, regular))
    raster_report = []
    for n, sd in enumerate(scan, 1):
        if sd.get("error"):
            raise ValueError("Слайд %s не прочитан: %s" % (sd["slide"], sd["error"]))
        for fam in sd.get("fonts_failed") or []:
            warn("слайд %d: шрифт «%s» не загрузился — переносы могут отличаться" % (n, fam))
        s = deck.add_slide()
        bg = sd["bg"]
        c = rgba(bg.get("color"))
        if c and c[3] > 0:
            s.shape(0, 0, 1920, 1080, fill=c, name="Фон")
        if bg.get("grad"):
            g = gradient(bg["grad"])
            if g:
                s.shape(0, 0, 1920, 1080, gradient=g, name="Фон")
        if bg.get("url"):
            size = bg.get("size") or "auto"
            add_image(s, project, bg["url"], 0, 0, 1920, 1080, bg.get("nw"), bg.get("nh"),
                      "contain" if size == "contain" else "cover", bg.get("pos"), name="Фон", warn=warn)
        layer = raster_pngs.get(sd["slide"])
        for it in sd["items"]:
            k = it["k"]
            if k == "box":
                add_box(s, project, it, warn)
            elif k == "img":
                add_image(s, project, it["src"], it["x"], it["y"], it["w"], it["h"], it.get("nw"), it.get("nh"), it.get("fit"),
                          it.get("pos"), it.get("clip"), it.get("op", 1), it.get("radius", 0),
                          "Картинка " + Path(unquote(urlparse(it["src"]).path)).name, warn)
            elif k == "text":
                add_text(s, it, fonts)
            elif k == "raster":
                if it.get("text") or (it.get("why") or "").startswith("график"):  # логотипы и иконки — картинки и так
                    raster_report.append({"slide": n, "why": it.get("why"), "text": it.get("text", "")})
                if layer is None:
                    warn("нет растрового слоя для слайда %d" % n)
                    continue
                W, H = sd.get("w", 1920), sd.get("h", 1080)
                x0, y0, x1, y1 = max(it["x"], 0), max(it["y"], 0), min(it["x"] + it["w"], W), min(it["y"] + it["h"], H)
                if x1 - x0 < 1 or y1 - y0 < 1:
                    continue
                s.picture(layer, "png", x0, y0, x1 - x0, y1 - y0, crop=(x0 / W, y0 / H, (W - x1) / W, (H - y1) / H),
                          name="Картинка: " + (it.get("why") or ""))
    deck.save(out_path)
    return {"slides": len(deck.slides), "raster": raster_report, "fonts": fonts.files()}


def parse_dump(text):
    m = re.search(r'id="result">__RESULT__(.*?)__END__', text, re.S)
    if not m:
        return None
    return json.loads(_html.unescape(m.group(1)))
