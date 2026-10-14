# Проверка PPTX по XSD ECMA-376 Transitional (только для разработки: нужен lxml и схемы).
#   Схемы: ECMA-376 5th edition, Part 4 → OfficeOpenXML-XMLSchema-Transitional.zip (распаковать в папку).
#   .venv-dev/bin/pip install lxml
#   .venv-dev/bin/python app/tests/ui/pptx_xsd.py --xsd <папка со схемами> file.pptx [...]
# PowerPoint просит «восстановить» из-за нарушений схемы (порядок элементов, диапазоны) — разбор XML их не ловит.
import argparse, re, sys, zipfile
from pathlib import Path
from lxml import etree

ap = argparse.ArgumentParser()
ap.add_argument("--xsd", required=True)
ap.add_argument("files", nargs="+")
a = ap.parse_args()
xsd = Path(a.xsd).expanduser()


def schema(name):
    return etree.XMLSchema(etree.parse(str(xsd / name)))


PML, DML = schema("pml.xsd"), schema("dml-main.xsd")
RULES = [(re.compile(r"ppt/(presentation|presProps|viewProps)\.xml$"), PML),
         (re.compile(r"ppt/(slides/slide|slideMasters/slideMaster|slideLayouts/slideLayout)\d+\.xml$"), PML),
         (re.compile(r"ppt/theme/theme\d+\.xml$"), DML),
         (re.compile(r"ppt/tableStyles\.xml$"), DML)]
bad = 0
for f in a.files:
    with zipfile.ZipFile(f) as z:
        checked = 0
        for name in z.namelist():
            sch = next((s for rx, s in RULES if rx.search(name)), None)
            if not sch:
                continue
            checked += 1
            doc = etree.fromstring(z.read(name))
            if not sch.validate(doc):
                bad += 1
                for e in list(sch.error_log)[:5]:
                    print("FAIL %s %s: строка %d: %s" % (Path(f).name, name, e.line, e.message))
        print("%s: проверено частей %d" % (Path(f).name, checked))
print("OK" if not bad else "ошибок схемы: %d" % bad)
sys.exit(1 if bad else 0)
