# Запуск: /usr/bin/python3 -m unittest discover -s app/tests
import importlib, json, os, shutil, sys, tempfile, threading, time, unittest
from http.server import ThreadingHTTPServer
from pathlib import Path
from urllib.parse import quote
from urllib.request import Request, urlopen
from urllib.error import HTTPError

APP = Path(__file__).resolve().parents[1]


class ServerProjects(unittest.TestCase):
    def setUp(self):
        self.root = Path(tempfile.mkdtemp())
        (self.root / "Проекты").mkdir()
        g = self.root / "Гайдлайны" / "brand"
        g.mkdir(parents=True)
        (g / "guide.json").write_text(json.dumps({"id": "brand", "name": "Brand", "version": 1, "base_css": "styles.css"}), encoding="utf-8")
        self.src = self.root / "src"
        self.src.mkdir()
        for n in ("a.md", "b.md", "c.md"):
            (self.src / n).write_text(n, encoding="utf-8")
        os.environ["PD_ROOT"] = str(self.root)
        os.environ["PD_CONFIG"] = str(self.root / "config")
        os.environ["PD_TRASH"] = str(self.root / "Корзина")  # не трогаем настоящую ~/.Trash
        sys.path.insert(0, str(APP))
        from core import store, tasks
        importlib.reload(store); importlib.reload(tasks)
        import server
        self.store, self.tasks, self.server = store, tasks, importlib.reload(server)
        self.httpd = ThreadingHTTPServer(("127.0.0.1", 0), server.Handler)
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()
        self.base = "http://127.0.0.1:%d" % self.httpd.server_address[1]

    def tearDown(self):
        self.httpd.shutdown(); self.httpd.server_close()
        shutil.rmtree(self.root)
        for k in ("PD_ROOT", "PD_CONFIG", "PD_TRASH"):
            os.environ.pop(k, None)

    def call(self, path, body=None):
        data = None if body is None else json.dumps(body).encode()
        req = Request(self.base + path, data=data, headers={"Content-Type": "application/json"})
        try:
            with urlopen(req, timeout=10) as r:
                return json.loads(r.read())
        except HTTPError as e:
            return json.loads(e.read())

    def test_create_project_with_reference_subset(self):
        tok = self.store.save_pick([self.src / "a.md", self.src / "b.md", self.src / "c.md"], "files")
        r = self.call("/api/projects", {"mode": "create", "topic": "Итоги  Q3", "guide": "brand", "duration": "12",
                                        "author": {"name": " Анна ", "position": ""},
                                        "references": [{"token": tok, "items": [0, 2, 7]}], "self_check": False})
        self.assertTrue(r["ok"], r)
        d = self.root / "Проекты" / r["name"]
        req = self.store.read_json(d / "request.json")
        self.assertEqual(req["topic"], "Итоги Q3")
        self.assertEqual(req["duration"], 12)
        self.assertEqual(req["author"], {"name": "Анна", "position": ""})
        self.assertEqual(req["references"], ["a.md", "c.md"])  # b.md убрали в форме, 7 — мусор
        self.assertFalse(req["options"]["self_check"])
        for _ in range(50):
            if self.tasks.lifecycle("project", r["name"]).get("sources_copied"):
                break
            time.sleep(0.1)
        self.assertEqual(sorted(p.name for p in (d / "Исходники").iterdir()), ["a.md", "c.md"])

    def test_validation(self):
        bad = [({"mode": "x", "topic": "T", "guide": "brand"}, "задачу"),
               ({"mode": "create", "topic": " ", "guide": "brand"}, "тему"),
               ({"mode": "create", "topic": "T", "guide": "нет"}, "гайдлайн"),
               ({"mode": "create", "topic": "T", "guide": "brand", "duration": 0}, "Длительность"),
               ({"mode": "extend", "topic": "T", "guide": "brand"}, "исходную"),
               ({"mode": "extend", "topic": "T", "guide": "brand", "source": {"type": "project", "project": "../x"}}, "")]
        for body, word in bad:
            r = self.call("/api/projects", body)
            self.assertFalse(r["ok"], body)
            self.assertIn(word, r["error"])
        self.assertEqual(list((self.root / "Проекты").iterdir()), [])

    def test_presets_save_and_delete(self):
        self.assertEqual(self.call("/api/presets")["presets"], [])
        self.call("/api/presets", {"action": "save", "name": "Анна", "position": "Лид"})
        r = self.call("/api/presets", {"action": "save", "name": "Анна", "position": "Лид"})
        self.assertEqual(r["presets"], [{"name": "Анна", "position": "Лид"}])  # без дублей
        self.assertFalse(self.call("/api/presets", {"action": "save", "name": "", "position": ""})["ok"])
        self.assertEqual(self.call("/api/presets", {"action": "delete", "name": "Анна", "position": "Лид"})["presets"], [])

    def test_delete_project_and_guide_to_trash(self):
        name = self.call("/api/projects", {"mode": "create", "topic": "Удалить", "guide": "brand"})["name"]
        for _ in range(50):
            if self.tasks.lifecycle("project", name).get("sources_copied"):
                break
            time.sleep(0.1)
        self.assertEqual([g["used_by"] for g in self.call("/api/state")["guides"]], [1])
        self.tasks.claim("project")
        self.tasks.set_state("project", name, "working", stage="generating")
        r = self.call("/api/projects/%s/delete" % quote(name), {})
        self.assertFalse(r["ok"]); self.assertIn("собирает", r["error"])  # агент работает — нельзя
        self.tasks.set_state("project", name, "waiting_user", stage="interview", question="?")
        self.assertTrue(self.call("/api/projects/%s/delete" % quote(name), {})["ok"])
        self.assertFalse((self.root / "Проекты" / name).exists())
        self.assertTrue((self.root / "Корзина" / name / "request.json").exists())
        self.assertNotIn(name, self.call("/api/state")["projects"])
        # второй проект с тем же именем в Корзине не затирает первый
        (self.root / "Проекты" / name / "slides").mkdir(parents=True)
        self.assertNotEqual(self.call("/api/projects/%s/delete" % quote(name), {})["trash_name"], name)
        self.assertTrue(self.call("/api/guides/brand/delete", {})["ok"])
        self.assertEqual(self.call("/api/state")["guides"], [])
        self.assertFalse(self.call("/api/guides/..%2F..%2Fsrc/delete", {})["ok"])
        self.assertTrue((self.root / "src" / "a.md").exists())


if __name__ == "__main__":
    unittest.main()
