# Запуск: /usr/bin/python3 -m unittest discover -s app/tests
import shutil, sys, tempfile, unittest, zipfile
from pathlib import Path

APP = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(APP / "tools"))
import extract  # noqa: E402

A = 'xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main"'
P = 'xmlns:p="http://schemas.openxmlformats.org/presentationml/2006/main"'
R = 'xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships"'
REL = '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">%s</Relationships>'
W = 'xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"'
S = 'xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"'


def slide(text):
    return ('<p:sld %s %s><p:cSld><p:spTree><p:sp><p:txBody><a:p><a:r><a:t>%s</a:t></a:r><a:br/>'
            '<a:r><a:t>вторая строка</a:t></a:r></a:p></p:txBody></p:sp></p:spTree></p:cSld></p:sld>' % (P, A, text))


class Extract(unittest.TestCase):
    def setUp(self):
        self.d = Path(tempfile.mkdtemp())

    def tearDown(self):
        shutil.rmtree(self.d)

    def test_pptx_order_from_presentation_xml_and_line_breaks(self):
        f = self.d / "t.pptx"
        with zipfile.ZipFile(f, "w") as z:
            # порядок в презентации: slide2, потом slide1 — по именам файлов было бы наоборот
            z.writestr("ppt/presentation.xml", '<p:presentation %s %s><p:sldIdLst><p:sldId id="1" r:id="rB"/>'
                       '<p:sldId id="2" r:id="rA"/></p:sldIdLst><p:sldSz cx="12192000" cy="6858000"/></p:presentation>' % (P, R))
            z.writestr("ppt/_rels/presentation.xml.rels", REL % (
                '<Relationship Id="rA" Target="slides/slide1.xml"/><Relationship Id="rB" Target="slides/slide2.xml"/>'))
            z.writestr("ppt/slides/slide1.xml", slide("Второй по порядку"))
            z.writestr("ppt/slides/slide2.xml", slide("Первый по порядку"))
        md, summary = extract.pptx(f)
        self.assertLess(md.index("Первый по порядку"), md.index("Второй по порядку"))
        self.assertIn("Первый по порядку вторая строка", md)  # a:br не склеивает слова
        self.assertIn("1 pt = 2.00 px", md)
        self.assertTrue(summary.startswith("2 слайдов"))

    def test_docx_headings_lists_tables_and_deleted_text(self):
        f = self.d / "t.docx"
        body = ('<w:p><w:pPr><w:pStyle w:val="Heading1"/></w:pPr><w:r><w:t>Раздел</w:t></w:r></w:p>'
                '<w:p><w:pPr><w:numPr><w:ilvl w:val="1"/></w:numPr></w:pPr><w:r><w:t>пункт</w:t></w:r></w:p>'
                '<w:p><w:hyperlink><w:r><w:t>ссылка</w:t></w:r></w:hyperlink><w:del><w:r><w:delText>удалено</w:delText></w:r></w:del>'
                '<w:ins><w:r><w:t> вставлено</w:t></w:r></w:ins></w:p>'
                '<w:tbl><w:tr><w:tc><w:p><w:r><w:t>A</w:t></w:r></w:p></w:tc><w:tc><w:p><w:r><w:t>B</w:t></w:r></w:p></w:tc></w:tr></w:tbl>')
        with zipfile.ZipFile(f, "w") as z:
            z.writestr("word/document.xml", "<w:document %s><w:body>%s</w:body></w:document>" % (W, body))
        md, _ = extract.docx(f)
        self.assertIn("## Раздел", md)
        self.assertIn("  - пункт", md)
        self.assertIn("ссылка вставлено", md)
        self.assertNotIn("удалено", md)
        self.assertIn("| A | B |", md)

    def test_xlsx_dates_and_shared_strings(self):
        f = self.d / "t.xlsx"
        with zipfile.ZipFile(f, "w") as z:
            z.writestr("xl/workbook.xml", '<workbook %s %s><sheets><sheet name="Итоги" r:id="r1"/></sheets></workbook>' % (S, R))
            z.writestr("xl/_rels/workbook.xml.rels", REL % '<Relationship Id="r1" Target="worksheets/sheet1.xml"/>')
            z.writestr("xl/sharedStrings.xml", '<sst %s><si><t>Дата</t></si></sst>' % S)
            z.writestr("xl/styles.xml", '<styleSheet %s><cellXfs><xf numFmtId="0"/><xf numFmtId="14"/></cellXfs></styleSheet>' % S)
            z.writestr("xl/worksheets/sheet1.xml", '<worksheet %s><sheetData><row r="1"><c r="A1" t="s"><v>0</v></c>'
                       '<c r="B1"><v>42</v></c></row><row r="2"><c r="A2" s="1"><v>46023</v></c></row></sheetData></worksheet>' % S)
        md, summary = extract.xlsx(f)
        self.assertIn("Лист «Итоги»", md)
        self.assertIn("| Дата | 42 |", md)
        self.assertIn("2026-01-01", md)
        self.assertIn("3 ячеек", summary)

    def test_font_names_from_real_otf(self):
        fonts = list((APP.parent / "Гайдлайны").rglob("*.otf"))
        if not fonts:
            self.skipTest("нет шрифтов в Гайдлайны/ (пакет не установлен)")
        nm = extract.font_names(fonts[0])
        self.assertTrue(nm and nm["family"])


if __name__ == "__main__":
    unittest.main()
