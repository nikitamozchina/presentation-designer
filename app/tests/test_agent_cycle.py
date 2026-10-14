# Запуск: /usr/bin/python3 -m unittest discover -s app/tests
import json, os, shutil, subprocess, sys, tempfile, unittest
from pathlib import Path

APP = Path(__file__).resolve().parents[1]
AGENT = APP / "tools" / "agent.py"
SLIDE = """<!doctype html><html><head><title>01 Тест</title><style>.x{}</style></head><body>
<div class="slide"><div>О&nbsp;чем&nbsp;поговорим</div><div>и&nbsp;еще и&nbsp;еще</div></div></body></html>"""


MULTI = """<!doctype html><html><head><title>01 Тест</title></head><body>
<div class="slide">
  <div class="h">Заголовок</div>
  <div>Пункт один</div>
  <div>Пункт два</div>
  <div>Пункт три</div>
  <div>Пункт четыре</div>
  <div>Подвал</div>
</div></body></html>"""


class AgentCycle(unittest.TestCase):
    def setUp(self):
        self.root = Path(tempfile.mkdtemp())
        p = self.root / "Проекты" / "Тест" / "slides"
        p.mkdir(parents=True)
        (p / "01-test.html").write_text(SLIDE, encoding="utf-8")
        (p / "02-other.html").write_text(SLIDE, encoding="utf-8")
        os.environ["PD_ROOT"] = str(self.root)
        sys.path.insert(0, str(APP))
        import importlib
        from core import store, textmatch
        self.store = importlib.reload(store)
        self.textmatch = textmatch

    def tearDown(self):
        shutil.rmtree(self.root)
        os.environ.pop("PD_ROOT", None)

    def agent(self, *args):
        r = subprocess.run([sys.executable, str(AGENT)] + list(args), capture_output=True, text=True,
                           env=dict(os.environ, PD_ROOT=str(self.root)))
        return json.loads(r.stdout)

    def edits(self, items):
        return self.store.create_edits("Тест", items)["version"]

    def test_versions_unique_and_immutable(self):
        a = self.edits([{"slide_id": "01-test", "comment": "a"}])
        b = self.edits([{"slide_id": "01-test", "comment": "b"}])
        self.assertEqual((a, b), (1, 2))
        with self.assertRaises(ValueError):
            self.edits([{"slide_id": "99-none", "comment": "x"}])
        with self.assertRaises(ValueError):
            self.edits([{"slide_id": "01-test", "comment": "  "}])

    def test_full_cycle_and_no_double_claim(self):
        n = self.edits([{"slide_id": "01-test", "comment": "c", "selection": {"text": "О чем поговорим"}}])
        job = self.agent("claim")["job"]
        self.assertEqual(job["version"], n)
        self.assertFalse(self.agent("claim")["ok"])  # второй агент не забирает то же задание
        loc = self.agent("locate", "Тест", str(n), "edit-001")
        self.assertEqual(loc["verdict"], "unique")
        self.assertEqual(loc["matches"][0]["raw"], "О&nbsp;чем&nbsp;поговорим")
        self.assertFalse(self.agent("finish", "Тест", str(n))["ok"])  # правка не отмечена
        self.assertFalse(self.agent("item", "Тест", str(n), "edit-001", "done", "ok")["ok"])  # без begin нельзя
        self.agent("begin", "Тест", str(n), "edit-001")
        self.agent("item", "Тест", str(n), "edit-001", "done", "ok")
        self.assertEqual(self.agent("finish", "Тест", str(n))["status"], "completed")
        self.assertIsNone(self.agent("claim")["job"])  # повторно не обрабатывается

    def test_ambiguous_and_missing_selection(self):
        n = self.edits([{"slide_id": "01-test", "comment": "c", "selection": {"text": "и еще"}},
                        {"slide_id": "01-test", "comment": "c", "selection": {"text": "нет такого"}}])
        self.agent("claim")
        self.assertEqual(self.agent("locate", "Тест", str(n), "edit-001")["verdict"], "ambiguous")
        self.assertEqual(self.agent("locate", "Тест", str(n), "edit-002")["verdict"], "not_found")

    def test_prefix_disambiguates(self):
        raw = (self.root / "Проекты/Тест/slides/01-test.html").read_text(encoding="utf-8")
        hits = self.textmatch.find(raw, "и еще", prefix="и еще ")
        self.assertEqual(len(hits), 1)

    def test_stalled_agent_and_takeover(self):
        n = self.edits([{"slide_id": "01-test", "comment": "a"}, {"slide_id": "02-other", "comment": "b"}])
        self.agent("claim")
        self.agent("begin", "Тест", str(n), "edit-001")
        self.agent("item", "Тест", str(n), "edit-001", "done", "ok")
        f = self.store.agent_status_file("Тест")
        st = self.store.read_json(f)
        st["updated_at"] = "2000-01-01T00:00:00+00:00"  # агент пропал
        self.store.write_json(f, st)
        self.assertEqual(self.store.iteration_state("Тест", n), "stalled")
        self.assertEqual(self.store.agent_status()["status"], "stalled")
        self.assertFalse(self.agent("claim")["ok"])  # без --takeover не трогаем
        job = self.agent("claim", "--takeover")["job"]
        self.assertEqual(job["already_done"], ["edit-001"])  # результат первой правки сохранён

    def test_crash_mid_edit_no_false_done(self):
        n = self.edits([{"slide_id": "01-test", "comment": "a"}])
        self.agent("claim")
        self.agent("begin", "Тест", str(n), "edit-001")
        f = self.root / "Проекты/Тест/slides/01-test.html"
        f.write_text("", encoding="utf-8")  # агент испортил слайд
        self.agent("item", "Тест", str(n), "edit-001", "done", "ok")
        self.assertEqual(self.agent("finish", "Тест", str(n))["status"], "failed")  # ложного «Готово» нет
        self.store.set_slide_reverted("Тест", n, "01-test", True)  # откат слайда чинит его
        self.assertEqual(f.read_text(encoding="utf-8"), SLIDE)

    def _apply(self, n, edit_id, old, new):
        self.agent("begin", "Тест", str(n), edit_id)
        self.f.write_text(self.f.read_text(encoding="utf-8").replace(old, new), encoding="utf-8")
        self.agent("item", "Тест", str(n), edit_id, "done", "ok")

    def test_revert_slide_keeps_later_iterations(self):
        self.f = self.root / "Проекты/Тест/slides/01-test.html"
        self.f.write_text(MULTI, encoding="utf-8")
        n1 = self.edits([{"slide_id": "01-test", "comment": "a"}, {"slide_id": "01-test", "comment": "b"}])
        self.agent("claim")
        self._apply(n1, "edit-001", "Заголовок", "Новый заголовок")
        self._apply(n1, "edit-002", "Пункт три", "Пункт 3")
        self.agent("finish", "Тест", str(n1))
        n2 = self.edits([{"slide_id": "01-test", "comment": "c"}])
        self.agent("claim")
        self._apply(n2, "edit-001", "Подвал", "Новый подвал")
        self.agent("finish", "Тест", str(n2))
        # откат слайда в v1 убирает обе правки v1, но не трогает правку v2
        self.store.set_slide_reverted("Тест", n1, "01-test", True)
        t = self.f.read_text(encoding="utf-8")
        self.assertIn(">Заголовок<", t); self.assertIn("Пункт три", t); self.assertIn("Новый подвал", t)
        items = self.store.iteration_summary("Тест", n1)["items"]
        self.assertTrue(all(i["reverted"] for i in items))
        # возврат
        self.store.set_slide_reverted("Тест", n1, "01-test", False)
        t = self.f.read_text(encoding="utf-8")
        self.assertIn("Новый заголовок", t); self.assertIn("Пункт 3", t); self.assertIn("Новый подвал", t)
        self.assertFalse(any(i["reverted"] for i in self.store.iteration_summary("Тест", n1)["items"]))

    def test_revert_conflict_leaves_files_untouched(self):
        self.f = self.root / "Проекты/Тест/slides/01-test.html"
        self.f.write_text(MULTI, encoding="utf-8")
        n1 = self.edits([{"slide_id": "01-test", "comment": "a"}])
        self.agent("claim"); self._apply(n1, "edit-001", "Заголовок", "Новый заголовок"); self.agent("finish", "Тест", str(n1))
        n2 = self.edits([{"slide_id": "01-test", "comment": "b"}])
        self.agent("claim"); self._apply(n2, "edit-001", "Новый заголовок", "Третий заголовок"); self.agent("finish", "Тест", str(n2))
        before = self.f.read_text(encoding="utf-8")
        with self.assertRaises(RuntimeError):
            self.store.set_slide_reverted("Тест", n1, "01-test", True)
        self.assertEqual(self.f.read_text(encoding="utf-8"), before)

    def test_revert_refused_while_running(self):
        n = self.edits([{"slide_id": "01-test", "comment": "a"}])
        self.agent("claim")
        with self.assertRaises(RuntimeError):
            self.store.set_slide_reverted("Тест", n, "01-test", True)

    def test_path_traversal(self):
        for bad in ("../x", ".state", "a/b", ""):
            with self.assertRaises((ValueError, FileNotFoundError)):
                self.store.project_dir(bad)

    def test_atomic_write_leaves_no_tmp(self):
        p = self.root / "x.json"
        self.store.write_json(p, {"a": 1})
        self.store.write_json(p, {"a": 2})
        self.assertEqual(json.loads(p.read_text()), {"a": 2})
        self.assertEqual([f.name for f in self.root.iterdir() if f.name.endswith(".tmp")], [])


if __name__ == "__main__":
    unittest.main()
