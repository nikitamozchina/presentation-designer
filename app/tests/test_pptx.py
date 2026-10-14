# Запуск: /usr/bin/python3 -m unittest discover -s app/tests
import posixpath, re, sys, tempfile, unittest, zipfile
import xml.etree.ElementTree as ET
from pathlib import Path

APP = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(APP))
from core import pptx  # noqa: E402

PNG = (b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01\x08\x06\x00\x00\x00\x1f\x15\xc4\x89"
       b"\x00\x00\x00\rIDATx\x9cc\xf8\xcf\xc0\xf0\x1f\x00\x05\x00\x01\xff\x89\x99=\x1d\x00\x00\x00\x00IEND\xaeB`\x82")
NS = {"a": "http://schemas.openxmlformats.org/drawingml/2006/main",
      "p": "http://schemas.openxmlformats.org/presentationml/2006/main",
      "ct": "http://schemas.openxmlformats.org/package/2006/content-types",
      "rel": "http://schemas.openxmlformats.org/package/2006/relationships"}


def sample_deck():
    d = pptx.Deck(title="Тест <&>", fonts=("Brand Sans DemiBold", "Brand Sans"))
    s = d.add_slide()
    s.picture(PNG, "png", 0, 0, 1920, 1080, crop=(0.1, 0, 0.1, 0), name="Фон")
    s.shape(100, 300, 500, 400, kind="roundRect", radius=24, fill=(222, 234, 254, 1))
    s.shape(700, 300, 500, 400, gradient=(45, [(0, (0, 119, 255, 1)), (1, (90, 165, 255, 0.5))]),
            line=((0, 119, 255, 1), 3, "dashed"))
    s.shape(1300, 300, 64, 64, kind="ellipse", fill=(0, 119, 255, 1))
    s.text(100, 80, 1500, 160, [{"align": "l", "line_px": 70, "runs": [
        {"text": "Заголовок с NBSP & <теги>", "size_px": 64, "font": "Brand Sans DemiBold", "color": (0, 0, 0, 1)},
        {"br": True, "size_px": 64},
        {"text": "вторая строка\x01", "size_px": 64, "bold": True, "color": (0, 119, 255, 0.6), "spacing_px": 2}]}])
    d.add_slide().picture(PNG, "png", 10, 10, 100, 100)  # та же картинка — один файл в media
    return d


class Writer(unittest.TestCase):
    def setUp(self):
        self.parts = sample_deck().parts()

    def test_every_xml_part_parses_and_is_declared(self):
        ct = ET.fromstring(self.parts["[Content_Types].xml"])
        overrides = {o.get("PartName") for o in ct.findall("ct:Override", NS)}
        defaults = {d.get("Extension") for d in ct.findall("ct:Default", NS)}
        for name, data in self.parts.items():
            ext = name.rsplit(".", 1)[-1]
            if ext in ("xml", "rels"):
                ET.fromstring(data)  # падает на битом XML
            if name != "[Content_Types].xml" and ext not in ("rels",):
                self.assertTrue("/" + name in overrides or ext in defaults, name)
        for o in overrides:
            self.assertIn(o.lstrip("/"), self.parts)

    def test_all_relationships_resolve(self):
        for name, data in self.parts.items():
            if not name.endswith(".rels"):
                continue
            base = posixpath.dirname(posixpath.dirname(name))  # ppt/slides/_rels/x.rels → ppt/slides
            for r in ET.fromstring(data).findall("rel:Relationship", NS):
                target = posixpath.normpath(posixpath.join(base, r.get("Target")))
                self.assertIn(target, self.parts, "%s → %s" % (name, target))
        # каждый r:id в слайде есть в его rels
        for n in (1, 2):
            ids = set(re.findall(r'r:embed="(rId\d+)"', self.parts["ppt/slides/slide%d.xml" % n].decode()))
            rels = {r.get("Id") for r in ET.fromstring(self.parts["ppt/slides/_rels/slide%d.xml.rels" % n]).findall("rel:Relationship", NS)}
            self.assertTrue(ids <= rels)

    def test_shapes_text_and_units(self):
        x = ET.fromstring(self.parts["ppt/slides/slide1.xml"])
        ids = [c.get("id") for c in x.iter("{%s}cNvPr" % NS["p"])]
        self.assertEqual(len(ids), len(set(ids)))  # id фигур уникальны
        off = x.find(".//p:sp/p:spPr/a:xfrm/a:off", NS)
        self.assertEqual((off.get("x"), off.get("y")), ("635000", "1905000"))  # 100 px, 300 px
        pres = ET.fromstring(self.parts["ppt/presentation.xml"]).find("p:sldSz", NS)
        self.assertEqual((pres.get("cx"), pres.get("cy")), ("12192000", "6858000"))
        runs = x.findall(".//a:r", NS)
        self.assertEqual(runs[0].find("a:t", NS).text, "Заголовок с NBSP & <теги>")
        self.assertEqual(runs[1].find("a:t", NS).text, "вторая строка")  # управляющие символы вырезаны
        rpr = runs[0].find("a:rPr", NS)
        self.assertEqual(rpr.get("sz"), "3200")  # 64 px → 32 pt
        self.assertEqual(rpr.find("a:latin", NS).get("typeface"), "Brand Sans DemiBold")
        self.assertEqual(runs[1].find("a:rPr", NS).get("b"), "1")
        self.assertEqual(runs[1].find(".//a:alpha", NS).get("val"), "60000")
        self.assertEqual(x.find(".//a:lnSpc/a:spcPts", NS).get("val"), "3500")  # 70 px → 35 pt
        self.assertEqual(x.find(".//a:lin", NS).get("ang"), str(45 * 60000))
        self.assertEqual(x.find(".//a:prstDash", NS).get("val"), "dash")
        self.assertEqual(x.find(".//a:srcRect", NS).get("l"), "10000")

    def test_media_deduplicated_and_save_is_atomic(self):
        self.assertEqual([k for k in self.parts if k.startswith("ppt/media/")], ["ppt/media/image1.png"])
        with tempfile.TemporaryDirectory() as t:
            out = Path(t) / "a.pptx"
            sample_deck().save(out)
            with zipfile.ZipFile(out) as z:
                self.assertIsNone(z.testzip())
                self.assertEqual(z.namelist()[0], "[Content_Types].xml")
            self.assertEqual([p.name for p in Path(t).iterdir()], ["a.pptx"])  # без временных файлов
        with self.assertRaises(ValueError):
            pptx.Deck().media(b"x", "webp")

    def test_failed_save_leaves_nothing_and_invariants(self):
        d = sample_deck()
        d.slides[0].items.append(object())  # сломанный объект — упадёт внутри записи
        with tempfile.TemporaryDirectory() as t:
            with self.assertRaises(TypeError):
                d.save(Path(t) / "b.pptx")
            self.assertEqual(list(Path(t).iterdir()), [])
        d = pptx.Deck(); s = d.add_slide()
        s.shape(0, 0, 10, 10, gradient=(0, [(0, (1, 2, 3, 1))]))  # один стоп → сплошная заливка
        s.text(0, 0, 10, 10, [{"runs": [{"text": "Привет \ud800 мир", "size_px": 99999}, {"text": "Only English", "size_px": 20, "underline": True}]}])
        parts = d.parts()
        x = ET.fromstring(parts["ppt/slides/slide1.xml"])
        self.assertIsNone(x.find(".//a:gradFill", NS)); self.assertIsNotNone(x.find(".//p:sp/p:spPr/a:solidFill", NS))
        r1, r2 = x.findall(".//a:r/a:rPr", NS)
        self.assertEqual((r1.get("sz"), r1.get("lang")), ("400000", "ru-RU"))
        self.assertEqual((r2.get("lang"), r2.get("u")), ("en-US", "sng"))
        with tempfile.TemporaryDirectory() as t:
            d.save(Path(t) / "c.pptx")
            self.assertEqual(oct((Path(t) / "c.pptx").stat().st_mode & 0o777), "0o644")


if __name__ == "__main__":
    unittest.main()


class Mapping(unittest.TestCase):
    """Перевод CSS → PPTX: шрифты по @font-face, градиенты, object-fit и обрезка."""
    FONTS = sorted((APP.parent / "Гайдлайны").glob("*/fonts/*.otf"))

    @unittest.skipUnless(sorted((APP.parent / "Гайдлайны").glob("*/fonts/*.otf")), "нет установленных шрифтов гайдлайнов (пакет)")
    def test_font_names_from_real_otf(self):
        from core import pptx_export as px
        for f in self.FONTS:
            info = px.font_info(f.read_bytes())
            self.assertTrue(info["legacy"], f.name)
            if f.stem.endswith(" Bold"):  # Bold — то же семейство, что Regular, с флагом жирности
                self.assertTrue(info["bold"], f.name)
                reg = f.with_name(f.stem[:-5] + " Regular.otf")
                if reg.exists():
                    self.assertEqual(px.font_info(reg.read_bytes())["legacy"], info["legacy"])

    def test_css_font_matching(self):
        from core import pptx_export as px
        faces = [dict(family="Brand Sans", style="normal", w=w, src=str(w)) for w in (400, 500, 600, 700)]
        pick = lambda w, it=False: px.match_face(faces, "brand sans", w, it)["w"]
        self.assertEqual([pick(400), pick(500), pick(600), pick(700)], [400, 500, 600, 700])
        self.assertEqual(pick(300), 400)   # Light не объявлен — браузер берёт Regular, не «ближайший файл»
        self.assertEqual(pick(450), 500)   # 400–500: сначала вверх до 500
        self.assertEqual(pick(650), 700)   # >500: вверх
        self.assertEqual(pick(900), 700)
        self.assertIsNone(px.match_face(faces, "Inter", 400, False))
        self.assertEqual(px._weight("bold"), 700)
        self.assertEqual(px._weight("100 900"), 100)

    def test_gradient_angles_and_alpha(self):
        from core import pptx_export as px
        ang, stops = px.gradient("linear-gradient(90deg, rgb(0, 119, 255) 0%, rgba(90, 165, 255, 0) 100%)")
        self.assertEqual(ang, 0)  # CSS 90° (слева направо) = OOXML 0°
        self.assertEqual(stops[1], (1.0, (90, 165, 255, 0.0)))
        self.assertEqual(px.gradient("linear-gradient(135deg, rgb(0, 0, 0) 0%, rgb(1, 1, 1) 100%)")[0], 45)
        self.assertEqual(px.gradient("linear-gradient(rgb(0, 0, 0), rgb(1, 1, 1))")[0], 90)  # по умолчанию сверху вниз
        self.assertIsNone(px.gradient("linear-gradient(90deg, rgb(0, 0, 0) 0%)"))

    def test_object_fit_and_clip(self):
        from core import pptx_export as px
        # contain: 200×100 в рамке 100×100 → 100×50 по центру
        self.assertEqual(px.fit_image(200, 100, 0, 0, 100, 100, "contain", "50% 50%"), (0, 25.0, 100.0, 50.0, None))
        # cover: 200×100 в 100×100 → обрезка по 25% слева и справа
        x, y, w, h, crop = px.fit_image(200, 100, 0, 0, 100, 100, "cover", "50% 50%")
        self.assertEqual((w, h), (100, 100)); self.assertAlmostEqual(crop[0], 0.25); self.assertAlmostEqual(crop[2], 0.25)
        # картинка 100×100 наполовину вылезает за карточку справа — отрезаем половину исходника
        x, y, w, h, crop = px.apply_clip(50, 0, 100, 100, None, {"x": 0, "y": 0, "w": 100, "h": 100})
        self.assertEqual((x, w), (50, 50)); self.assertAlmostEqual(crop[2], 0.5)
        self.assertIsNone(px.apply_clip(200, 0, 50, 50, None, {"x": 0, "y": 0, "w": 100, "h": 100}))
