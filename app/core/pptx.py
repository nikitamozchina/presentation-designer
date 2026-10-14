# Писатель PPTX на stdlib: zip + XML по ECMA-376 (структура как у python-pptx по умолчанию).
# Координаты на входе — px слайда 1920×1080; 1 px = 6350 EMU, кегль px → pt = px/2.
# Цвет — (r, g, b, a) с a в 0..1. Ничего не знает про HTML: на вход — готовые фигуры, тексты, картинки.
import hashlib, os, re, tempfile, zipfile
from datetime import datetime, timezone
from xml.sax.saxutils import escape as _esc

PX = 6350
NS = ('xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main" '
      'xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships" '
      'xmlns:p="http://schemas.openxmlformats.org/presentationml/2006/main"')
REL = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
CT_IMG = {"png": "image/png", "jpeg": "image/jpeg", "jpg": "image/jpeg", "gif": "image/gif"}
_BAD_XML = re.compile("[\x00-\x08\x0b\x0c\x0e-\x1f￾￿\ud800-\udfff]")  # + одиночные суррогаты
_CYR = re.compile("[Ѐ-ӿ]")


def emu(px):
    return int(round(px * PX))


def clamp(v, lo, hi):
    return max(lo, min(hi, v))


def esc(s):
    return _esc(_BAD_XML.sub("", s), {'"': "&quot;"})


def color_xml(c):
    """(r,g,b,a) → <a:srgbClr> с альфой."""
    r, g, b, a = (list(c) + [1.0])[:4]
    alpha = "" if a >= 0.999 else '<a:alpha val="%d"/>' % int(round(max(a, 0) * 100000))
    return '<a:srgbClr val="%02X%02X%02X">%s</a:srgbClr>' % (int(r), int(g), int(b), alpha)


def fill_xml(fill=None, gradient=None):
    if gradient and len(gradient[1]) == 1:
        fill, gradient = gradient[1][0][1], None
    if gradient and len(gradient[1]) >= 2:
        ang, stops = gradient
        gs = "".join('<a:gs pos="%d">%s</a:gs>' % (int(round(min(max(p, 0), 1) * 100000)), color_xml(c)) for p, c in stops)
        return '<a:gradFill rotWithShape="1"><a:gsLst>%s</a:gsLst><a:lin ang="%d" scaled="0"/></a:gradFill>' % (
            gs, int(round((ang % 360) * 60000)))
    if fill and fill[3] > 0.001:
        return "<a:solidFill>%s</a:solidFill>" % color_xml(fill)
    return "<a:noFill/>"


def line_xml(line=None):
    if not line:
        return "<a:ln><a:noFill/></a:ln>"
    c, w, dash = line
    d = '<a:prstDash val="%s"/>' % {"dashed": "dash", "dotted": "sysDot"}[dash] if dash in ("dashed", "dotted") else ""
    return '<a:ln w="%d"><a:solidFill>%s</a:solidFill>%s</a:ln>' % (emu(w), color_xml(c), d)


def geom_xml(kind, w, h, radius=0):
    if kind == "round2SameRect":  # скруглены два верхних угла (с flipV — два нижних)
        adj = int(round(min(0.5, radius / max(min(w, h), 1)) * 100000))
        return ('<a:prstGeom prst="round2SameRect"><a:avLst><a:gd name="adj1" fmla="val %d"/>'
                '<a:gd name="adj2" fmla="val 0"/></a:avLst></a:prstGeom>' % adj)
    if kind == "roundRect":
        adj = int(round(min(0.5, radius / max(min(w, h), 1)) * 100000))
        return '<a:prstGeom prst="roundRect"><a:avLst><a:gd name="adj" fmla="val %d"/></a:avLst></a:prstGeom>' % adj
    return '<a:prstGeom prst="%s"><a:avLst/></a:prstGeom>' % ("ellipse" if kind == "ellipse" else "rect")


def xfrm(x, y, w, h, flip_v=False):
    return '<a:xfrm%s><a:off x="%d" y="%d"/><a:ext cx="%d" cy="%d"/></a:xfrm>' % (' flipV="1"' if flip_v else "", emu(x), emu(y), max(emu(w), 1), max(emu(h), 1))


class Slide:
    def __init__(self, deck, n):
        self.deck, self.n, self.items, self.rels, self._id = deck, n, [], {}, 1

    def _nid(self):
        self._id += 1
        return self._id

    def shape(self, x, y, w, h, kind="rect", radius=0, fill=None, gradient=None, line=None, name="Фигура", flip_v=False):
        """kind: rect | roundRect | round2SameRect | ellipse. line = (цвет, ширина px, solid|dashed|dotted) — рисуется по центру края."""
        self.items.append(
            '<p:sp><p:nvSpPr><p:cNvPr id="%d" name="%s"/><p:cNvSpPr/><p:nvPr/></p:nvSpPr>'
            "<p:spPr>%s%s%s%s</p:spPr></p:sp>" % (
                self._nid(), esc(name), xfrm(x, y, w, h, flip_v), geom_xml(kind, w, h, radius), fill_xml(fill, gradient), line_xml(line)))

    def text(self, x, y, w, h, paragraphs, wrap=True, anchor="t", name="Текст"):
        """paragraphs: [{align: l|ctr|r|just, line_px: float|None, runs: [{text, size_px, font, bold, italic,
        color, spacing_px} | {br: True, size_px}]}]."""
        ps = []
        for p in paragraphs:
            ppr = '<a:pPr algn="%s">' % p.get("align", "l")
            if p.get("line_px"):
                ppr += '<a:lnSpc><a:spcPts val="%d"/></a:lnSpc>' % clamp(int(round(p["line_px"] / 2 * 100)), 0, 158400)
            ppr += '<a:spcBef><a:spcPts val="0"/></a:spcBef><a:spcAft><a:spcPts val="0"/></a:spcAft><a:buNone/></a:pPr>'
            body, last = [], None
            for r in p["runs"]:
                rpr = self._rpr(r)
                if r.get("br"):
                    body.append("<a:br>%s</a:br>" % rpr)
                elif r.get("text"):
                    body.append("<a:r>%s<a:t>%s</a:t></a:r>" % (rpr, esc(r["text"])))
                last = r
            end = self._rpr(last or {}, "endParaRPr")
            ps.append("<a:p>%s%s%s</a:p>" % (ppr, "".join(body), end))
        self.items.append(
            '<p:sp><p:nvSpPr><p:cNvPr id="%d" name="%s"/><p:cNvSpPr txBox="1"/><p:nvPr/></p:nvSpPr>'
            '<p:spPr>%s<a:prstGeom prst="rect"><a:avLst/></a:prstGeom><a:noFill/></p:spPr>'
            '<p:txBody><a:bodyPr wrap="%s" lIns="0" tIns="0" rIns="0" bIns="0" anchor="%s" rtlCol="0"><a:noAutofit/></a:bodyPr>'
            "<a:lstStyle/>%s</p:txBody></p:sp>" % (
                self._nid(), esc(name), xfrm(x, y, w, h), "square" if wrap else "none", anchor, "".join(ps)))

    @staticmethod
    def _rpr(r, tag="rPr"):
        t = r.get("text") or ""
        attrs = 'lang="%s"' % ("en-US" if t and not _CYR.search(t) and re.search("[A-Za-z]", t) else "ru-RU")
        if r.get("size_px"):
            attrs += ' sz="%d"' % clamp(int(round(r["size_px"] * 50)), 100, 400000)
        attrs += ' b="%d" i="%d"' % (1 if r.get("bold") else 0, 1 if r.get("italic") else 0)
        if r.get("underline"):
            attrs += ' u="sng"'
        if r.get("strike"):
            attrs += ' strike="sngStrike"'
        if r.get("spacing_px"):
            attrs += ' spc="%d"' % clamp(int(round(r["spacing_px"] / 2 * 100)), -400000, 400000)
        inner = ""
        if r.get("color"):
            inner += "<a:solidFill>%s</a:solidFill>" % color_xml(r["color"])
        if r.get("font"):
            f = esc(r["font"])
            inner += '<a:latin typeface="%s"/><a:ea typeface="%s"/><a:cs typeface="%s"/>' % (f, f, f)
        return "<a:%s %s dirty=\"0\">%s</a:%s>" % (tag, attrs, inner, tag)

    def picture(self, data, ext, x, y, w, h, crop=None, alpha=1.0, kind="rect", radius=0, name="Картинка"):
        """crop = (left, top, right, bottom) — доли исходной картинки, которые отрезать (как a:srcRect)."""
        rid = self._image_rel(data, ext)
        src = ""
        if crop and any(abs(v) > 1e-6 for v in crop):
            src = '<a:srcRect l="%d" t="%d" r="%d" b="%d"/>' % tuple(int(round(clamp(v, -1, 1) * 100000)) for v in crop)
        amt = "" if alpha >= 0.999 else '<a:alphaModFix amt="%d"/>' % int(round(max(alpha, 0) * 100000))
        self.items.append(
            '<p:pic><p:nvPicPr><p:cNvPr id="%d" name="%s"/><p:cNvPicPr><a:picLocks noChangeAspect="1"/></p:cNvPicPr><p:nvPr/></p:nvPicPr>'
            '<p:blipFill><a:blip r:embed="%s">%s</a:blip>%s<a:stretch><a:fillRect/></a:stretch></p:blipFill>'
            "<p:spPr>%s%s</p:spPr></p:pic>" % (
                self._nid(), esc(name), rid, amt, src, xfrm(x, y, w, h), geom_xml(kind, w, h, radius)))

    def _image_rel(self, data, ext):
        media = self.deck.media(data, ext)
        for rid, target in self.rels.items():
            if target == media:
                return rid
        rid = "rId%d" % (len(self.rels) + 2)  # rId1 — макет
        self.rels[rid] = media
        return rid

    def xml(self):
        return ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n<p:sld %s><p:cSld><p:spTree>'
                '<p:nvGrpSpPr><p:cNvPr id="1" name=""/><p:cNvGrpSpPr/><p:nvPr/></p:nvGrpSpPr>'
                '<p:grpSpPr><a:xfrm><a:off x="0" y="0"/><a:ext cx="0" cy="0"/><a:chOff x="0" y="0"/><a:chExt cx="0" cy="0"/></a:xfrm></p:grpSpPr>'
                "%s</p:spTree></p:cSld><p:clrMapOvr><a:masterClrMapping/></p:clrMapOvr></p:sld>" % (NS, "".join(self.items)))

    def rels_xml(self):
        rs = ['<Relationship Id="rId1" Type="%s/slideLayout" Target="../slideLayouts/slideLayout1.xml"/>' % REL]
        rs += ['<Relationship Id="%s" Type="%s/image" Target="../media/%s"/>' % (rid, REL, m) for rid, m in self.rels.items()]
        return _rels(rs)


def _rels(items):
    return ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n'
            '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">%s</Relationships>' % "".join(items))


class Deck:
    def __init__(self, width_px=1920, height_px=1080, title="", fonts=("Arial", "Arial")):
        self.w, self.h, self.title, self.fonts = width_px, height_px, title, fonts
        self.slides, self._media = [], {}

    def add_slide(self):
        s = Slide(self, len(self.slides) + 1)
        self.slides.append(s)
        return s

    def media(self, data, ext):
        ext = ext.lower().lstrip(".")
        if ext not in CT_IMG:
            raise ValueError("Формат картинки не поддерживается в pptx: %s" % ext)
        key = hashlib.sha1(data).hexdigest()
        if key not in self._media:
            self._media[key] = ("image%d.%s" % (len(self._media) + 1, "jpeg" if ext == "jpg" else ext), data)
        return self._media[key][0]

    def parts(self):
        """Имя части → bytes. Отдельно от save, чтобы тесты проверяли структуру без диска."""
        out = {}
        n = len(self.slides)
        exts = sorted({m.rsplit(".", 1)[1] for m, _ in self._media.values()})
        ov = [("/ppt/presentation.xml", "application/vnd.openxmlformats-officedocument.presentationml.presentation.main+xml"),
              ("/ppt/slideMasters/slideMaster1.xml", "application/vnd.openxmlformats-officedocument.presentationml.slideMaster+xml"),
              ("/ppt/slideLayouts/slideLayout1.xml", "application/vnd.openxmlformats-officedocument.presentationml.slideLayout+xml"),
              ("/ppt/theme/theme1.xml", "application/vnd.openxmlformats-officedocument.theme+xml"),
              ("/ppt/presProps.xml", "application/vnd.openxmlformats-officedocument.presentationml.presProps+xml"),
              ("/ppt/viewProps.xml", "application/vnd.openxmlformats-officedocument.presentationml.viewProps+xml"),
              ("/ppt/tableStyles.xml", "application/vnd.openxmlformats-officedocument.presentationml.tableStyles+xml"),
              ("/docProps/core.xml", "application/vnd.openxmlformats-package.core-properties+xml"),
              ("/docProps/app.xml", "application/vnd.openxmlformats-officedocument.extended-properties+xml")]
        ov += [("/ppt/slides/slide%d.xml" % i, "application/vnd.openxmlformats-officedocument.presentationml.slide+xml") for i in range(1, n + 1)]
        out["[Content_Types].xml"] = (
            '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
            '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
            '<Default Extension="xml" ContentType="application/xml"/>'
            + "".join('<Default Extension="%s" ContentType="%s"/>' % (e, CT_IMG[e]) for e in exts)
            + "".join('<Override PartName="%s" ContentType="%s"/>' % o for o in ov) + "</Types>")
        out["_rels/.rels"] = _rels([
            '<Relationship Id="rId1" Type="%s/officeDocument" Target="ppt/presentation.xml"/>' % REL,
            '<Relationship Id="rId2" Type="http://schemas.openxmlformats.org/package/2006/relationships/metadata/core-properties" Target="docProps/core.xml"/>',
            '<Relationship Id="rId3" Type="%s/extended-properties" Target="docProps/app.xml"/>' % REL])
        now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        out["docProps/core.xml"] = (
            '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n<cp:coreProperties xmlns:cp="http://schemas.openxmlformats.org/package/2006/metadata/core-properties" '
            'xmlns:dc="http://purl.org/dc/elements/1.1/" xmlns:dcterms="http://purl.org/dc/terms/" xmlns:dcmitype="http://purl.org/dc/dcmitype/" '
            'xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance"><dc:title>%s</dc:title><dc:creator>Дизайнер презентаций</dc:creator>'
            '<dcterms:created xsi:type="dcterms:W3CDTF">%s</dcterms:created><dcterms:modified xsi:type="dcterms:W3CDTF">%s</dcterms:modified>'
            "</cp:coreProperties>" % (esc(self.title), now, now))
        out["docProps/app.xml"] = (
            '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n<Properties xmlns="http://schemas.openxmlformats.org/officeDocument/2006/extended-properties" '
            'xmlns:vt="http://schemas.openxmlformats.org/officeDocument/2006/docPropsVTypes"><Application>Дизайнер презентаций</Application>'
            "<Slides>%d</Slides></Properties>" % n)
        out["ppt/presentation.xml"] = (
            '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n<p:presentation %s saveSubsetFonts="1">'
            '<p:sldMasterIdLst><p:sldMasterId id="2147483648" r:id="rId1"/></p:sldMasterIdLst>'
            "<p:sldIdLst>%s</p:sldIdLst>"
            '<p:sldSz cx="%d" cy="%d"/><p:notesSz cx="6858000" cy="9144000"/></p:presentation>' % (
                NS, "".join('<p:sldId id="%d" r:id="rId%d"/>' % (255 + i, 9 + i) for i in range(1, n + 1)), emu(self.w), emu(self.h)))
        out["ppt/_rels/presentation.xml.rels"] = _rels(
            ['<Relationship Id="rId1" Type="%s/slideMaster" Target="slideMasters/slideMaster1.xml"/>' % REL,
             '<Relationship Id="rId2" Type="%s/presProps" Target="presProps.xml"/>' % REL,
             '<Relationship Id="rId3" Type="%s/viewProps" Target="viewProps.xml"/>' % REL,
             '<Relationship Id="rId4" Type="%s/theme" Target="theme/theme1.xml"/>' % REL,
             '<Relationship Id="rId5" Type="%s/tableStyles" Target="tableStyles.xml"/>' % REL]
            + ['<Relationship Id="rId%d" Type="%s/slide" Target="slides/slide%d.xml"/>' % (9 + i, REL, i) for i in range(1, n + 1)])
        empty_tree = ('<p:cSld%s><p:spTree><p:nvGrpSpPr><p:cNvPr id="1" name=""/><p:cNvGrpSpPr/><p:nvPr/></p:nvGrpSpPr>'
                      '<p:grpSpPr><a:xfrm><a:off x="0" y="0"/><a:ext cx="0" cy="0"/><a:chOff x="0" y="0"/><a:chExt cx="0" cy="0"/></a:xfrm></p:grpSpPr>'
                      "</p:spTree></p:cSld>")
        out["ppt/slideMasters/slideMaster1.xml"] = (
            '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n<p:sldMaster %s>%s'
            '<p:clrMap bg1="lt1" tx1="dk1" bg2="lt2" tx2="dk2" accent1="accent1" accent2="accent2" accent3="accent3" '
            'accent4="accent4" accent5="accent5" accent6="accent6" hlink="hlink" folHlink="folHlink"/>'
            '<p:sldLayoutIdLst><p:sldLayoutId id="2147483649" r:id="rId1"/></p:sldLayoutIdLst>'
            "<p:txStyles><p:titleStyle/><p:bodyStyle/><p:otherStyle/></p:txStyles></p:sldMaster>" % (NS, empty_tree % ""))
        out["ppt/slideMasters/_rels/slideMaster1.xml.rels"] = _rels(
            ['<Relationship Id="rId1" Type="%s/slideLayout" Target="../slideLayouts/slideLayout1.xml"/>' % REL,
             '<Relationship Id="rId2" Type="%s/theme" Target="../theme/theme1.xml"/>' % REL])
        out["ppt/slideLayouts/slideLayout1.xml"] = (
            '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n<p:sldLayout %s type="blank" preserve="1">%s'
            "<p:clrMapOvr><a:masterClrMapping/></p:clrMapOvr></p:sldLayout>" % (NS, empty_tree % ' name="Пустой"'))
        out["ppt/slideLayouts/_rels/slideLayout1.xml.rels"] = _rels(
            ['<Relationship Id="rId1" Type="%s/slideMaster" Target="../slideMasters/slideMaster1.xml"/>' % REL])
        out["ppt/theme/theme1.xml"] = theme_xml(*self.fonts)
        out["ppt/presProps.xml"] = '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n<p:presentationPr %s/>' % NS
        out["ppt/viewProps.xml"] = (
            '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n<p:viewPr %s><p:normalViewPr><p:restoredLeft sz="15620"/>'
            '<p:restoredTop sz="94660"/></p:normalViewPr><p:gridSpacing cx="76200" cy="76200"/></p:viewPr>' % NS)
        out["ppt/tableStyles.xml"] = ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n<a:tblStyleLst '
                                      'xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main" def="{5C22544A-7EE6-4342-B048-85BDC9FD1C3A}"/>')
        for s in self.slides:
            out["ppt/slides/slide%d.xml" % s.n] = s.xml()
            out["ppt/slides/_rels/slide%d.xml.rels" % s.n] = s.rels_xml()
        for name, data in self._media.values():
            out["ppt/media/" + name] = data
        return {k: (v.encode("utf-8") if isinstance(v, str) else v) for k, v in out.items()}

    def save(self, path):
        """Атомарно: временный файл рядом → os.replace."""
        path = str(path)
        fd, tmp = tempfile.mkstemp(dir=os.path.dirname(path) or ".", suffix=".pptx.tmp")
        os.close(fd)
        try:
            parts = self.parts()
            with zipfile.ZipFile(tmp, "w", zipfile.ZIP_DEFLATED) as z:
                z.writestr("[Content_Types].xml", parts.pop("[Content_Types].xml"))  # первым — так принято
                for name, data in parts.items():
                    comp = zipfile.ZIP_STORED if name.startswith("ppt/media/") else zipfile.ZIP_DEFLATED
                    z.writestr(name, data, compress_type=comp)
            os.chmod(tmp, 0o644)  # mkstemp даёт 0600 — файл должен открываться у других
            os.replace(tmp, path)
        except BaseException:
            if os.path.exists(tmp):
                os.unlink(tmp)
            raise


def theme_xml(major="Arial", minor="Arial"):
    """Полная тема (без неё PowerPoint просит «восстановить»): цвета Office, шрифты гайда, стандартные стили."""
    clr = ("<a:dk1><a:sysClr val=\"windowText\" lastClr=\"000000\"/></a:dk1><a:lt1><a:sysClr val=\"window\" lastClr=\"FFFFFF\"/></a:lt1>"
           "<a:dk2><a:srgbClr val=\"44546A\"/></a:dk2><a:lt2><a:srgbClr val=\"E7E6E6\"/></a:lt2>"
           "<a:accent1><a:srgbClr val=\"0077FF\"/></a:accent1><a:accent2><a:srgbClr val=\"ED7D31\"/></a:accent2>"
           "<a:accent3><a:srgbClr val=\"A5A5A5\"/></a:accent3><a:accent4><a:srgbClr val=\"FFC000\"/></a:accent4>"
           "<a:accent5><a:srgbClr val=\"5B9BD5\"/></a:accent5><a:accent6><a:srgbClr val=\"70AD47\"/></a:accent6>"
           "<a:hlink><a:srgbClr val=\"0563C1\"/></a:hlink><a:folHlink><a:srgbClr val=\"954F72\"/></a:folHlink>")
    font = lambda f: '<a:latin typeface="%s"/><a:ea typeface=""/><a:cs typeface=""/>' % esc(f)
    solid = '<a:solidFill><a:schemeClr val="phClr"/></a:solidFill>'
    grad = ('<a:gradFill rotWithShape="1"><a:gsLst><a:gs pos="0"><a:schemeClr val="phClr"/></a:gs>'
            '<a:gs pos="100000"><a:schemeClr val="phClr"/></a:gs></a:gsLst><a:lin ang="5400000" scaled="0"/></a:gradFill>')
    ln = lambda w: '<a:ln w="%d" cap="flat" cmpd="sng" algn="ctr">%s<a:prstDash val="solid"/><a:miter lim="800000"/></a:ln>' % (w, solid)
    eff = "<a:effectStyle><a:effectLst/></a:effectStyle>"
    return ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n<a:theme xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main" name="Тема">'
            '<a:themeElements><a:clrScheme name="Цвета">%s</a:clrScheme>'
            '<a:fontScheme name="Шрифты"><a:majorFont>%s</a:majorFont><a:minorFont>%s</a:minorFont></a:fontScheme>'
            '<a:fmtScheme name="Стили"><a:fillStyleLst>%s%s%s</a:fillStyleLst><a:lnStyleLst>%s%s%s</a:lnStyleLst>'
            "<a:effectStyleLst>%s%s%s</a:effectStyleLst><a:bgFillStyleLst>%s%s%s</a:bgFillStyleLst></a:fmtScheme>"
            "</a:themeElements><a:objectDefaults/><a:extraClrSchemeLst/></a:theme>" % (
                clr, font(major), font(minor), solid, grad, grad, ln(6350), ln(12700), ln(19050), eff, eff, eff, solid, solid, grad))
