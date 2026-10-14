#!/usr/bin/env python3
# Извлечение содержимого исходников без сторонних пакетов — чтобы агент не читал гигантские файлы целиком.
#   extract.py <файл|папка> <папка-результата> [--sheets]
# Для каждого файла пишет <результат>/<путь>.md, плюс index.md (что есть, сколько, где смотреть).
# --sheets: для pptx/ppt/key — картинки слайдов через Keynote и контактные листы по 16 слайдов.
# Форматы: pptx (текст по слайдам в правильном порядке, заметки, диаграммы, макеты, тема, частые цвета/шрифты),
# docx (заголовки, списки, таблицы, ссылки, исправления), xlsx (листы, даты), otf/ttf (семейство/начертание),
# md/txt/csv/json/html (как есть, до 200 КБ), pdf (число страниц — читать агентом), картинки (список).
import datetime, json, re, struct, subprocess, sys, zipfile
from collections import Counter
from pathlib import Path
from xml.etree import ElementTree as ET

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

NS = {
    "a": "http://schemas.openxmlformats.org/drawingml/2006/main",
    "p": "http://schemas.openxmlformats.org/presentationml/2006/main",
    "r": "http://schemas.openxmlformats.org/officeDocument/2006/relationships",
    "w": "http://schemas.openxmlformats.org/wordprocessingml/2006/main",
    "s": "http://schemas.openxmlformats.org/spreadsheetml/2006/main",
    "c": "http://schemas.openxmlformats.org/drawingml/2006/chart",
    "rel": "http://schemas.openxmlformats.org/package/2006/relationships",
}
R_ID = "{%s}id" % NS["r"]
TEXT_EXT = {".md", ".txt", ".csv", ".json", ".html", ".htm", ".xml", ".yaml", ".yml", ".tsv"}
IMG_EXT = {".png", ".jpg", ".jpeg", ".gif", ".svg", ".webp", ".heic", ".tif", ".tiff"}
FONT_EXT = {".otf", ".ttf", ".woff", ".woff2"}
MAX_TEXT = 200_000


def q(tag):
    p, t = tag.split(":")
    return "{%s}%s" % (NS[p], t)


def rels(z, part):
    """Связи части: {rId: абсолютный путь внутри zip}."""
    d, name = part.rsplit("/", 1) if "/" in part else ("", part)
    rp = (d + "/" if d else "") + "_rels/" + name + ".rels"
    if rp not in z.namelist():
        return {}
    out = {}
    for r in ET.fromstring(z.read(rp)).findall(q("rel:Relationship")):
        tgt = r.get("Target")
        if r.get("TargetMode") == "External":
            continue
        parts = (d.split("/") if d else []) + tgt.split("/")
        norm = []
        for x in parts:
            if x == "..":
                norm and norm.pop()
            elif x and x != ".":
                norm.append(x)
        out[r.get("Id")] = "/".join(norm) if not tgt.startswith("/") else tgt.lstrip("/")
    return out


def para_text(p):
    parts = []
    for el in p.iter():
        if el.tag == q("a:t"):
            parts.append(el.text or "")
        elif el.tag == q("a:br"):
            parts.append(" ")  # перенос строки внутри абзаца — не склеивать слова
    return re.sub(r"[ \t]+", " ", "".join(parts))


# ---------- pptx ----------

def pptx(path):
    z = zipfile.ZipFile(path)
    pres = ET.fromstring(z.read("ppt/presentation.xml"))
    prel = rels(z, "ppt/presentation.xml")
    sz = pres.find(q("p:sldSz"))
    size = (int(sz.get("cx")), int(sz.get("cy"))) if sz is not None else None
    slide_parts = [prel[s.get(R_ID)] for s in pres.iter(q("p:sldId")) if s.get(R_ID) in prel]
    lines, colors, fonts, layouts_used, sizes = [], Counter(), Counter(), Counter(), Counter()
    media_use, slide_titles = {}, {}
    lines.append("# %s\n" % Path(path).name)
    if size:
        lines.append("Размер слайда: %d×%d EMU (%.0f×%.0f pt; при ширине 1920 px: 1 pt = %.2f px)\n" %
                     (size[0], size[1], size[0] / 12700, size[1] / 12700, 1920 / (size[0] / 12700)))
    # тема
    theme_part = next((n for n in z.namelist() if re.fullmatch(r"ppt/theme/theme\d+\.xml", n)), None)
    if theme_part:
        th = ET.fromstring(z.read(theme_part))
        cs = th.find(".//" + q("a:clrScheme"))
        if cs is not None:
            pal = []
            for el in cs:
                val = el.find(q("a:srgbClr"))
                sysc = el.find(q("a:sysClr"))
                v = val.get("val") if val is not None else (sysc.get("lastClr") if sysc is not None else "?")
                pal.append("%s=#%s" % (el.tag.split("}")[1], v))
            lines.append("Тема — цвета (семантика темы может не совпадать с брендом!): " + ", ".join(pal) + "\n")
        fs = th.find(".//" + q("a:fontScheme"))
        if fs is not None:
            mj = fs.find("%s/%s" % (q("a:majorFont"), q("a:latin")))
            mn = fs.find("%s/%s" % (q("a:minorFont"), q("a:latin")))
            lines.append("Тема — шрифты: заголовки «%s», текст «%s»\n" %
                         (mj.get("typeface") if mj is not None else "?", mn.get("typeface") if mn is not None else "?"))
    # частоты цветов/шрифтов по слайдам, макетам и мастерам
    for n in z.namelist():
        if re.fullmatch(r"ppt/(slides|slideLayouts|slideMasters)/[^/]+\.xml", n):
            x = ET.fromstring(z.read(n))
            colors.update(e.get("val").upper() for e in x.iter(q("a:srgbClr")))
            fonts.update(e.get("typeface") for e in x.iter(q("a:latin")) if e.get("typeface"))
            for tag in ("a:rPr", "a:defRPr", "a:endParaRPr"):
                sizes.update(int(e.get("sz")) / 100 for e in x.iter(q(tag)) if (e.get("sz") or "").isdigit())
    layout_names, media_layouts = {}, {}
    for n in z.namelist():
        if re.fullmatch(r"ppt/slide(Layout|Master)s/[^/]+\.xml", n):
            c = ET.fromstring(z.read(n)).find(q("p:cSld"))
            nm = (c.get("name") if c is not None else None) or Path(n).stem
            if "slideLayouts/" in n:
                layout_names[n] = nm
            else:
                nm = "мастер " + nm
            for t in rels(z, n).values():
                if "/media/" in t:
                    media_layouts.setdefault(Path(t).name, []).append(nm)
    lines.append("Макетов в шаблоне: %d (в PDF/картинках видны только слайды, не макеты)\n" % len(layout_names))
    lines.append("Частые цвета (hex: раз): " + ", ".join("#%s: %d" % kv for kv in colors.most_common(15)) + "\n")
    lines.append("Частые кегли, pt (×2 = px при ширине 1920): " + ", ".join("%g: %d" % kv for kv in sizes.most_common(14)) + "\n")
    lines.append("Шрифты (имя: раз; +mj/+mn — шрифты темы): " + ", ".join("%s: %d" % kv for kv in fonts.most_common(12)) + "\n")
    # слайды
    for i, part in enumerate(slide_parts, 1):
        srel = rels(z, part)
        x = ET.fromstring(z.read(part))
        layout = next((layout_names.get(t) for t in srel.values() if "slideLayouts/" in t), None)
        layouts_used[layout] += 1
        first = next((para_text(p_).strip() for p_ in x.iter(q("a:p")) if para_text(p_).strip()), "")
        slide_titles[i] = (first[:70], layout)
        lines.append("\n## Слайд %d%s\n" % (i, " · макет «%s»" % layout if layout else ""))
        for sp in x.iter():
            if sp.tag == q("a:tbl"):
                for tr in sp.iter(q("a:tr")):
                    cells = [" ".join(para_text(p) for p in tc.iter(q("a:p"))).strip() for tc in tr.iter(q("a:tc"))]
                    lines.append("| " + " | ".join(cells) + " |")
            elif sp.tag == q("p:txBody"):
                for p in sp.findall(q("a:p")):
                    t = para_text(p).strip()
                    if t:
                        lvl = p.find(q("a:pPr"))
                        ind = int(lvl.get("lvl", "0")) if lvl is not None else 0
                        lines.append("  " * ind + "- " + t)
        for t in srel.values():
            if "/charts/" in t and t.endswith(".xml") and t in z.namelist():
                cx = ET.fromstring(z.read(t))
                vals = [e.text for e in cx.iter(q("c:v")) if e.text] + [e.text for e in cx.iter(q("a:t")) if e.text]
                if vals:
                    lines.append("- [диаграмма] " + "; ".join(vals[:60]))
            if "notesSlide" in t and t in z.namelist():
                nx = ET.fromstring(z.read(t))
                note = " ".join(para_text(p) for p in nx.iter(q("a:p"))).strip()
                if note and not note.isdigit():
                    lines.append("> Заметки: " + note)
        media = [Path(t).name for t in srel.values() if "/media/" in t]
        for m_ in media:
            media_use.setdefault(m_, []).append(i)
        if media:
            lines.append("- [картинки] " + ", ".join(media))
    summary = "%d слайдов, %d макетов" % (len(slide_parts), len(layout_names))
    pptx.media_use = media_use
    pptx.media_layouts = media_layouts
    pptx.slide_titles = slide_titles
    return "\n".join(lines) + "\n", summary


def pptx_media(path, dst, media_use, want_sheets, media_layouts=None, slide_titles=None):
    """Картинки из ppt/media → папка + media.md (файл, размер, на каких слайдах) + сетка."""
    z = zipfile.ZipFile(path)
    dst.mkdir(parents=True, exist_ok=True)
    rows, imgs = [], []
    for n in sorted(z.namelist()):
        if not n.startswith("ppt/media/"):
            continue
        name = Path(n).name
        ext = Path(name).suffix.lower()
        if ext not in IMG_EXT:
            continue
        data = z.read(n)
        (dst / name).write_bytes(data)
        used = media_use.get(name, [])
        lays = sorted(set((media_layouts or {}).get(name, [])))
        rows.append("| %s | %d | %s | %s |" % (name, len(data) // 1024, ", ".join(map(str, used[:20])) + (" …" if len(used) > 20 else ""),
                                               "; ".join(lays[:6]) + (" …" if len(lays) > 6 else "")))
        if ext not in (".svg",):
            imgs.append(dst / name)
    by_slide = []  # каталог «по слайдам»: где в шаблоне что лежит — искать по смыслу заголовка
    for sn in sorted({s for v in media_use.values() for s in v}):
        files = sorted(n for n, v in media_use.items() if sn in v)
        title, lay = (slide_titles or {}).get(sn, ("", None))
        by_slide.append("- Слайд %d «%s»%s: %s" % (sn, title, " (макет «%s»)" % lay if lay else "", ", ".join(files[:40]) + (" …" if len(files) > 40 else "")))
    (dst / "по-слайдам.md").write_text("# Картинки по слайдам %s\n\nПревью слайдов — контактные листы в _extracted. Файлы — в этой же папке.\n\n%s\n"
                                      % (Path(path).name, "\n".join(by_slide)), encoding="utf-8")
    (dst / "media.md").write_text("# Картинки из %s\n\nКопировать в гайд: `agent.py copy guide <имя> \"_extracted/…/<файл>\" \"assets/…\"`\n\n"
                                  "| Файл | КБ | Слайды | Макеты |\n|---|---|---|---|\n%s\n" % (Path(path).name, "\n".join(rows)), encoding="utf-8")
    sheets_made = []
    if want_sheets and imgs:
        from core import sheets
        sheets_made = sheets.make_sheets(imgs, dst, prefix="медиа", labels=[p.name for p in imgs], log=lambda m: None)
    return len(rows), sheets_made


# ---------- docx ----------

def docx(path):
    z = zipfile.ZipFile(path)
    body = ET.fromstring(z.read("word/document.xml")).find(q("w:body"))
    out = ["# %s\n" % Path(path).name]

    def ptext(p):
        parts = []
        for el in p.iter():
            if el.tag == q("w:t"):
                parts.append(el.text or "")
            elif el.tag == q("w:tab"):
                parts.append("\t")
            elif el.tag in (q("w:br"), q("w:cr")):
                parts.append("\n")
        return "".join(parts)  # w:delText (удалённое в исправлениях) не попадает — это другой тег

    def para(p):
        t = ptext(p).strip()
        if not t:
            return None
        ppr = p.find(q("w:pPr"))
        style = ""
        if ppr is not None:
            ps = ppr.find(q("w:pStyle"))
            style = (ps.get(q("w:val")) or "") if ps is not None else ""
            ol = ppr.find(q("w:outlineLvl"))
            if ol is not None:
                return "#" * min(int(ol.get(q("w:val"), "0")) + 2, 6) + " " + t
            if ppr.find(q("w:numPr")) is not None:
                ilvl = ppr.find("%s/%s" % (q("w:numPr"), q("w:ilvl")))
                return "  " * int(ilvl.get(q("w:val"), "0") if ilvl is not None else 0) + "- " + t
        m = re.search(r"(?:Heading|Заголовок)\s*(\d)", style, re.I)
        if m:
            return "#" * min(int(m.group(1)) + 1, 6) + " " + t
        if style.lower() in ("title", "название"):
            return "# " + t
        return t

    def walk(el):
        for ch in el:
            if ch.tag == q("w:p"):
                t = para(ch)
                if t:
                    out.append(t)
            elif ch.tag == q("w:tbl"):
                for tr in ch.iter(q("w:tr")):
                    cells = [" ".join(ptext(p).strip() for p in tc.iter(q("w:p"))).strip() for tc in tr.findall(q("w:tc"))]
                    out.append("| " + " | ".join(cells) + " |")
                out.append("")
            elif ch.tag in (q("w:sdt"), q("w:sdtContent"), q("w:ins"), q("w:customXml")):
                walk(ch)
    walk(body)
    words = len(" ".join(out).split())
    return "\n".join(out) + "\n", "%d слов" % words


# ---------- xlsx ----------

DATE_FMT_IDS = set(range(14, 23)) | {45, 46, 47}


def xlsx(path, max_rows=200, max_cols=30):
    z = zipfile.ZipFile(path)
    wb = ET.fromstring(z.read("xl/workbook.xml"))
    wrel = rels(z, "xl/workbook.xml")
    pr = wb.find(q("s:workbookPr"))
    date1904 = pr is not None and pr.get("date1904") in ("1", "true")
    shared = []
    if "xl/sharedStrings.xml" in z.namelist():
        for si in ET.fromstring(z.read("xl/sharedStrings.xml")).findall(q("s:si")):
            shared.append("".join(t.text or "" for t in si.iter(q("s:t"))))
    date_styles = set()
    if "xl/styles.xml" in z.namelist():
        st = ET.fromstring(z.read("xl/styles.xml"))
        custom = {int(n.get("numFmtId")): n.get("formatCode", "") for n in st.iter(q("s:numFmt"))}
        xfs = st.find(q("s:cellXfs"))
        for i, xf in enumerate(xfs.findall(q("s:xf")) if xfs is not None else []):
            fid = int(xf.get("numFmtId", "0"))
            code = re.sub(r'"[^"]*"|\[[^\]]*\]', "", custom.get(fid, ""))
            if fid in DATE_FMT_IDS or (fid in custom and re.search(r"[dmy]", code, re.I) and not re.search(r"[#0]", code)):
                date_styles.add(i)
    base = datetime.datetime(1904, 1, 1) if date1904 else datetime.datetime(1899, 12, 30)
    out, total = ["# %s\n" % Path(path).name], 0
    for sh in wb.iter(q("s:sheet")):
        part = wrel.get(sh.get(R_ID))
        if not part or part not in z.namelist():
            continue
        rows = {}
        for c in ET.fromstring(z.read(part)).iter(q("s:c")):
            ref = c.get("r") or ""
            m = re.fullmatch(r"([A-Z]+)(\d+)", ref)
            if not m:
                continue
            col = 0
            for ch in m.group(1):
                col = col * 26 + ord(ch) - 64
            row = int(m.group(2))
            t, v = c.get("t"), c.find(q("s:v"))
            if t == "s" and v is not None:
                val = shared[int(v.text)]
            elif t == "inlineStr":
                val = "".join(x.text or "" for x in c.iter(q("s:t")))
            elif t == "b" and v is not None:
                val = "ИСТИНА" if v.text == "1" else "ЛОЖЬ"
            elif v is not None and v.text is not None:
                val = v.text
                if int(c.get("s", "0")) in date_styles:
                    try:
                        val = (base + datetime.timedelta(days=float(v.text))).strftime("%Y-%m-%d")
                    except (ValueError, OverflowError):
                        pass
            else:
                continue
            rows.setdefault(row, {})[col] = str(val).replace("|", "/").replace("\n", " ")
            total += 1
        out.append("\n## Лист «%s» (%d строк)\n" % (sh.get("name"), len(rows)))
        if not rows:
            continue
        ncols = min(max(max(r) for r in rows.values()), max_cols)
        for i, rn in enumerate(sorted(rows)):
            if i >= max_rows:
                out.append("… ещё %d строк не показано" % (len(rows) - max_rows))
                break
            out.append("| " + " | ".join(rows[rn].get(c, "") for c in range(1, ncols + 1)) + " |")
    return "\n".join(out) + "\n", "%d листов, %d ячеек" % (len(list(wb.iter(q("s:sheet")))), total)


# ---------- шрифты ----------

def font_names(path):
    """family / subfamily / full из таблицы name (otf/ttf). woff/woff2 — без разбора."""
    data = Path(path).read_bytes()
    if data[:4] not in (b"\x00\x01\x00\x00", b"OTTO", b"true"):
        return None
    num = struct.unpack(">H", data[4:6])[0]
    for i in range(num):
        tag, _, off, _ = struct.unpack(">4sIII", data[12 + 16 * i: 28 + 16 * i])
        if tag != b"name":
            continue
        _, count, soff = struct.unpack(">HHH", data[off:off + 6])
        names = {}
        for j in range(count):
            pid, eid, lid, nid, ln, o = struct.unpack(">HHHHHH", data[off + 6 + 12 * j: off + 18 + 12 * j])
            raw = data[off + soff + o: off + soff + o + ln]
            if nid in (1, 2, 4, 16, 17) and (pid, eid) in ((3, 1), (0, 3), (0, 4)):
                names.setdefault(nid, raw.decode("utf-16-be", "replace"))
            elif nid in (1, 2, 4, 16, 17) and pid == 1:
                names.setdefault(nid, raw.decode("mac_roman", "replace"))
        return {"family": names.get(16) or names.get(1), "style": names.get(17) or names.get(2), "full": names.get(4)}
    return None


def pdf_pages(path):
    data = Path(path).read_bytes()
    return len(re.findall(rb"/Type\s*/Page[^s]", data))


# ---------- обход ----------

def handle(f, rel, out_root, want_sheets, index, fonts_found):
    ext = f.suffix.lower()
    media_sheets = []
    md_path = out_root / (rel + ".md")
    md, summary, kind = None, "", ext.lstrip(".")
    try:
        if ext == ".pptx":
            md, summary = pptx(f)
            n_media, msheets = pptx_media(f, out_root / (rel + "-медиа"), pptx.media_use, False, pptx.media_layouts, pptx.slide_titles)
            summary += "; картинок внутри: %d (%s-медиа/media.md)" % (n_media, rel)
            media_sheets.extend(msheets)
        elif ext == ".docx":
            md, summary = docx(f)
        elif ext == ".xlsx":
            md, summary = xlsx(f)
        elif ext in TEXT_EXT:
            text = f.read_text(encoding="utf-8", errors="replace")
            md = text[:MAX_TEXT] + ("\n\n… обрезано, всего %d символов" % len(text) if len(text) > MAX_TEXT else "")
            summary = "%d символов" % len(text)
        elif ext == ".pdf":
            n = pdf_pages(f)
            summary = "%d стр. — читать агентом частями (не больше 20 стр. за раз)" % n
        elif ext in FONT_EXT:
            nm = font_names(f) if ext in (".otf", ".ttf") else None
            if nm:
                fonts_found.append((rel, nm))
                summary = "шрифт: семейство «%s», начертание «%s»" % (nm["family"], nm["style"])
            else:
                summary = "шрифт (%s)" % ext
            kind = "шрифт"
        elif ext in IMG_EXT:
            summary, kind = "картинка", "картинка"
        elif ext == ".key":
            summary = "Keynote: для текста — convert.py --pptx, затем extract.py по pptx"
        elif ext in (".ppt", ".doc", ".xls"):
            summary = "старый двоичный формат — откройте в Keynote/Pages/Numbers и сохраните в новом, или ppt → convert.py"
        else:
            summary = "не разбирается"
    except (zipfile.BadZipFile, ET.ParseError, KeyError, struct.error) as e:
        summary = "ошибка разбора: %s" % e
    if md is not None:
        md_path.parent.mkdir(parents=True, exist_ok=True)
        md_path.write_text(md, encoding="utf-8")
    sheets = []
    if want_sheets and ext in (".pptx", ".ppt", ".key"):
        sheets = make_slide_sheets(f, out_root / (rel + "-листы"))
        if sheets is None:
            summary += "; контактные листы: не получилось (см. convert.py)"
        else:
            summary += "; контактных листов: %d" % len(sheets)
    index.append({"file": rel, "type": kind, "size_kb": round(f.stat().st_size / 1024), "summary": summary,
                  "md": str(md_path.relative_to(out_root)) if md is not None else None,
                  "sheets": [str(s.relative_to(out_root)) for s in (sheets or []) + media_sheets]})


def make_slide_sheets(f, dst):
    from core import sheets
    r = subprocess.run([sys.executable, str(Path(__file__).with_name("convert.py")), str(f), str(dst), "--images"],
                       capture_output=True, text=True)
    try:
        res = json.loads(r.stdout)
    except ValueError:
        return None
    if not res.get("ok"):
        (dst / "ошибка.txt").parent.mkdir(parents=True, exist_ok=True)
        (dst / "ошибка.txt").write_text(res.get("error", ""), encoding="utf-8")
        return None
    imgs = sorted(Path(res["images_dir"]).glob("*.png"))
    return sheets.make_sheets(imgs, dst, prefix="лист", labels=["Слайд %d" % i for i in range(1, len(imgs) + 1)],
                              log=lambda m: None)


def main(src, out_root, want_sheets):
    src, out_root = Path(src).resolve(), Path(out_root).resolve()
    out_root.mkdir(parents=True, exist_ok=True)
    files = [src] if src.is_file() else sorted(
        p for p in src.rglob("*") if p.is_file()
        and not any(part.startswith(".") or part == "_extracted" for part in p.relative_to(src).parts))
    index, fonts_found = [], []
    for f in files:
        rel = f.name if src.is_file() else str(f.relative_to(src))
        handle(f, rel, out_root, want_sheets, index, fonts_found)
    if want_sheets:  # сетки картинок по папкам (иконки, 3D и т.п.) — одним взглядом вместо сотни файлов
        from core import sheets
        groups = {}
        for f in files:
            if f.suffix.lower() in IMG_EXT - {".svg", ".heic"}:
                groups.setdefault(f.parent, []).append(f)
        for folder, imgs in sorted(groups.items()):
            if len(imgs) < 4:
                continue
            rel = "." if folder == src else str(folder.relative_to(src))
            dst = out_root / ("картинки-" + rel.replace("/", "—"))
            made = sheets.make_sheets(imgs, dst, prefix="сетка", labels=[i.name for i in imgs], log=lambda m: None)
            index.append({"file": rel + "/", "type": "сетка картинок", "size_kb": 0,
                          "summary": "%d картинок" % len(imgs), "md": None,
                          "sheets": [str(s.relative_to(out_root)) for s in made]})
    lines = ["# Индекс исходников\n", "Источник: %s\n" % src,
             "Читай этот индекс, затем только нужные .md и контактные листы. Полные PDF — частями.\n",
             "| Файл | Тип | КБ | Кратко | Извлечено | Листы |", "|---|---|---|---|---|---|"]
    by_type = Counter(i["type"] for i in index)
    for i in index:
        if i["type"] in ("картинка",) and by_type["картинка"] > 30:
            continue
        lines.append("| %s | %s | %d | %s | %s | %s |" % (i["file"], i["type"], i["size_kb"], i["summary"],
                                                       i["md"] or "", ", ".join(i["sheets"])))
    if by_type["картинка"] > 30:
        lines.append("\nКартинок: %d — список в images.txt (не перечислены выше)" % by_type["картинка"])
        (out_root / "images.txt").write_text("\n".join(i["file"] for i in index if i["type"] == "картинка"), encoding="utf-8")
    if fonts_found:
        lines.append("\n## Шрифты\n")
        for rel, nm in fonts_found:
            lines.append("- %s — семейство «%s», начертание «%s»" % (rel, nm["family"], nm["style"]))
    (out_root / "index.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(json.dumps({"ok": True, "index": str(out_root / "index.md"), "files": len(index),
                      "types": dict(by_type)}, ensure_ascii=False))


if __name__ == "__main__":
    a = sys.argv[1:]
    if len(a) < 2:
        print(json.dumps({"ok": False, "error": "extract.py <файл|папка> <папка-результата> [--sheets]"}, ensure_ascii=False))
        sys.exit(1)
    main(a[0], a[1], "--sheets" in a)
