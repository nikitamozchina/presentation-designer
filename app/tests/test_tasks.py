# Запуск: /usr/bin/python3 -m unittest discover -s app/tests
import importlib, json, os, shutil, subprocess, sys, tempfile, unittest
from datetime import date
from pathlib import Path

APP = Path(__file__).resolve().parents[1]
AGENT = APP / "tools" / "agent.py"


class Tasks(unittest.TestCase):
    def setUp(self):
        self.root = Path(tempfile.mkdtemp())
        (self.root / "Проекты").mkdir()
        g = self.root / "Гайдлайны" / "brand"
        (g / "fonts").mkdir(parents=True)
        (g / "fonts" / "A.otf").write_bytes(b"font")
        (g / "styles.css").write_text('@font-face{src:url("fonts/A.otf")} .x{background:url(assets/bg.jpg)}', encoding="utf-8")
        store_json = {"id": "brand", "name": "Brand", "version": 1, "base_css": "styles.css"}
        (g / "guide.json").write_text(json.dumps(store_json), encoding="utf-8")
        self.src = self.root / "src"
        (self.src / "папка").mkdir(parents=True)
        (self.src / "папка" / "a.txt").write_text("a", encoding="utf-8")
        (self.src / "doc.md").write_text("# doc", encoding="utf-8")
        os.environ["PD_ROOT"] = str(self.root)
        sys.path.insert(0, str(APP))
        from core import store
        importlib.reload(store)
        from core import tasks
        self.store, self.tasks = store, importlib.reload(tasks)

    def tearDown(self):
        shutil.rmtree(self.root)
        os.environ.pop("PD_ROOT", None)

    def agent(self, *args):
        r = subprocess.run([sys.executable, str(AGENT)] + list(args), capture_output=True, text=True,
                           env=dict(os.environ, PD_ROOT=str(self.root)))
        return json.loads(r.stdout)

    def project(self, topic="Тема", sources=None):
        req = {"mode": "create", "topic": topic, "guide": "brand", "sources": sources or [], "options": {"self_check": True}}

        def extra(tmp, name):
            self.store.write_json(tmp / "project.json", {"id": "x", "name": name, "slides": [], "guideline_set_id": "brand"})
        return self.tasks.create("project", topic, req, with_date=True, make_extra=extra)

    def test_names_with_date_and_duplicates(self):
        today = date.today().isoformat()
        a, b, c = self.project("Отчёт: итоги/Q3"), self.project("Отчёт: итоги/Q3"), self.project("Отчёт: итоги/Q3")
        self.assertEqual(a, "Отчёт итоги Q3 — %s" % today)
        self.assertEqual((b, c), (a + " 2", a + " 3"))
        self.assertEqual(self.tasks.clean_name("..скрытый"), "скрытый")
        self.assertLessEqual(len(self.tasks.clean_name("я" * 300)), 80)
        with self.assertRaises(ValueError):
            self.tasks.unique_name("project", " / : ", True)

    def test_create_is_atomic_and_loading_does_not_autocreate(self):
        def boom(tmp, name):
            raise RuntimeError("сбой")
        with self.assertRaises(RuntimeError):
            self.tasks.create("project", "Сбой", {"sources": []}, True, make_extra=boom)
        self.assertEqual([p.name for p in (self.root / "Проекты").iterdir() if not p.name.startswith(".create")], [])
        name = self.project()
        (self.root / "Проекты" / name / "project.json").unlink()
        with self.assertRaises(FileNotFoundError):  # не превращается в «пустой проект без гайда»
            self.store.load_project(name)

    def test_claim_order_lock_and_resume(self):
        a = self.project("А")
        b = self.project("Б")
        n1, resumed = self.tasks.claim("project")
        self.assertEqual((n1, resumed), (a, False))
        self.tasks.set_state("project", a, "working", stage="analysis")
        n2, _ = self.tasks.claim("project")
        self.assertEqual(n2, b)  # занятую другой сессией не берём
        self.tasks.set_state("project", b, "working", stage="interview")
        self.tasks.set_state("project", a, "waiting_user", stage="interview", question="Кто аудитория?")
        lc = self.store.read_json(self.root / "Проекты" / a / "lifecycle.json")
        lc["updated_at"] = "2000-01-01T00:00:00+00:00"  # ответ ждут давно — это не «завис»
        self.store.write_json(self.root / "Проекты" / a / "lifecycle.json", lc)
        self.assertEqual(self.tasks.view("project", a)["state"], "waiting_user")
        self.assertEqual(self.tasks.claim("project"), (a, True))  # незавершённая — первой
        self.assertEqual(self.store.agent_status()["status"], "in_progress")

    def test_stalled_and_cancel(self):
        a = self.project()
        self.tasks.claim("project")
        self.tasks.set_state("project", a, "working", stage="generating", progress={"done": 1, "total": 5})
        lc = self.store.read_json(self.root / "Проекты" / a / "lifecycle.json")
        lc["updated_at"] = "2000-01-01T00:00:00+00:00"
        self.store.write_json(self.root / "Проекты" / a / "lifecycle.json", lc)
        self.assertEqual(self.tasks.view("project", a)["state"], "stalled")
        self.assertEqual(self.store.agent_status()["status"], "stalled")
        self.tasks.cancel("project", a)
        with self.assertRaises(RuntimeError):
            self.tasks.set_state("project", a, "working")
        self.assertFalse(self.agent("heartbeat", "project", a, "x")["ok"])

    def test_copy_sources_idempotent_and_claim_waits(self):
        entries = self.tasks.source_entries([self.src / "папка", self.src / "doc.md"])
        a = self.project(sources=entries)
        self.assertEqual(self.tasks.claim("project"), (None, False))  # пока не скопировано — не берём
        self.tasks.copy_sources("project", a, log=lambda m: None)
        d = self.root / "Проекты" / a / "Исходники"
        self.assertTrue((d / "папка" / "a.txt").exists() and (d / "doc.md").exists())
        self.assertTrue(self.tasks.lifecycle("project", a)["sources_copied"])
        self.tasks.copy_sources("project", a, log=lambda m: None)  # повтор после рестарта безопасен
        self.assertEqual(self.tasks.claim("project")[0], a)

    def test_agent_cli_flow(self):
        a = self.project()
        t = self.agent("task-claim", "project")["task"]
        self.assertEqual((t["name"], t["lifecycle"]["state"], t["lifecycle"]["stage"]), (a, "working", "analysis"))
        self.assertTrue(self.agent("task-log", "project", a, "agent", "Какова цель?")["ok"])
        self.agent("task-log", "project", a, "user", "Обучить партнёров")
        self.assertIn("Обучить партнёров", self.agent("task-claim", "project", "--resume", a)["task"]["interview_so_far"])
        self.assertTrue(self.agent("task-state", "project", a, "waiting_user", "--stage", "structure",
                                   "--question", "Ок структура?", "Покажите структуру")["ok"])
        self.assertFalse(self.agent("task-state", "project", a, "ready", "готово")["ok"])  # нет слайдов
        self.assertTrue(self.agent("init-project", a)["ok"])
        pdir = self.root / "Проекты" / a
        css = (pdir / "styles.css").read_text(encoding="utf-8")
        self.assertIn('url("assets/fonts/A.otf")', css)
        self.assertIn("url(assets/bg.jpg)", css)
        self.assertTrue((pdir / "assets" / "fonts" / "A.otf").exists())
        (pdir / "slides" / "01-title.html").write_text('<title>01 Титул</title><div class="slide">Титул</div>', encoding="utf-8")
        self.assertEqual(self.agent("sync-slides", a)["slides"], 1)
        self.assertTrue(self.agent("task-state", "project", a, "working", "--stage", "generating",
                                   "--progress", "1/1", "Слайды")["ok"])
        self.assertTrue(self.agent("task-state", "project", a, "ready", "Готово")["ok"])
        self.assertFalse((pdir / ".claim.lock").exists())
        self.assertEqual(self.store.load_project(a)["slides"][0]["title"], "Титул")

    def test_assets_pulled_from_guide_on_sync(self):
        g = self.root / "Гайдлайны" / "brand" / "assets"
        (g / "library" / "икон").mkdir(parents=True)
        (g / "library" / "икон" / "a b.png").write_bytes(b"png")
        (g / "logo").mkdir()
        (g / "logo" / "l.png").write_bytes(b"l")
        (g / "big").mkdir()
        (g / "big" / "unused.png").write_bytes(b"x" * 10)
        (g / "bg.jpg").write_bytes(b"j")  # на него ссылается styles.css тестового гайда
        a = self.project()
        self.agent("task-claim", "project")
        self.agent("init-project", a)
        pdir = self.root / "Проекты" / a
        self.assertTrue((pdir / "assets" / "logo" / "l.png").exists())
        self.assertFalse((pdir / "assets" / "big").exists())  # библиотека не копируется целиком
        (pdir / "slides" / "01-a.html").write_text('<title>01 A</title><div class="slide"><img src="../assets/library/%D0%B8%D0%BA%D0%BE%D0%BD/a%20b.png">x</div>', encoding="utf-8")
        r = self.agent("sync-slides", a)
        self.assertTrue(r["ok"], r)
        self.assertTrue((pdir / "assets" / "library" / "икон" / "a b.png").exists())
        (pdir / "slides" / "02-b.html").write_text('<title>02 B</title><div class="slide"><img src="../assets/нет.png">y</div>', encoding="utf-8")
        r = self.agent("sync-slides", a)
        self.assertFalse(r["ok"])
        self.assertIn("нет.png", r["error"])

    def test_copy_inside_task_only(self):
        a = self.project()
        self.assertFalse(self.agent("copy", "project", a, "../../src/doc.md", "x.md")["ok"])

    def test_guide_ready_check(self):
        gname = self.tasks.create("guide", "Новый гайд", {"sources": [], "options": {"examples": True}}, with_date=False)
        self.agent("task-claim", "guide")
        r = self.agent("task-state", "guide", gname, "ready", "готово")
        self.assertFalse(r["ok"])
        self.assertIn("guide.json", r["error"])


if __name__ == "__main__":
    unittest.main()
