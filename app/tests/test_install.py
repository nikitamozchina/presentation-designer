# Запуск: /usr/bin/python3 -m unittest discover -s app/tests
# Установщик гоняется на копии приложения во временной папке с временным HOME (PD_HOME): настоящие ~/.claude и ~/.codex не трогаются.
import json, os, shutil, subprocess, sys, tempfile, unittest
from pathlib import Path

APP = Path(__file__).resolve().parents[2]
CODEX_BIN = Path("/Applications/ChatGPT.app/Contents/Resources/codex-cli/bin/codex")
CONFIG = 'model = "x"\n\n[mcp_servers.demo]\ncommand = "demo"\n'


class Install(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="pd-install-")).resolve()
        self.app = self.tmp / "Мои проекты" / "presentation designer"
        shutil.copytree(APP / "app", self.app / "app", ignore=shutil.ignore_patterns("__pycache__", "tests", "legacy"))
        self.home = self.tmp / "home"
        (self.home / ".claude").mkdir(parents=True)
        (self.home / ".codex").mkdir(parents=True)

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def run_install(self, *args):
        env = dict(os.environ, PD_HOME=str(self.home), PD_INSTALL_NO_APPS="1")
        env = {k: v for k, v in env.items() if not k.startswith("CODEX_")}
        r = subprocess.run([sys.executable, str(self.app / "app/tools/install.py")] + list(args),
                           capture_output=True, text=True, env=env)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        return r.stdout

    def record(self):
        return json.loads((self.app / ".state/install.json").read_text(encoding="utf-8"))

    def test_claude(self):
        self.run_install("--agent", "claude")
        skill = (self.home / ".claude/skills/presentations/SKILL.md").read_text(encoding="utf-8")
        self.assertIn("name: presentations", skill)
        self.assertIn("# /presentations", skill)
        self.assertIn(str(self.app), skill)
        self.assertNotIn("{{", skill)
        allow = json.loads((self.home / ".claude/settings.json").read_text(encoding="utf-8"))["permissions"]["allow"]
        self.assertIn("Bash(python3 %s/app/tools/agent.py *)" % self.app, allow)
        self.assertEqual(list(self.record()["agents"]), ["claude"])
        self.assertFalse((self.home / ".codex/rules").exists())
        self.run_install("--uninstall")
        self.assertFalse((self.home / ".claude/skills/presentations").exists())
        allow = json.loads((self.home / ".claude/settings.json").read_text(encoding="utf-8"))["permissions"]["allow"]
        self.assertEqual(allow, [])

    def test_codex_new_table_and_clean_uninstall(self):
        cfg = self.home / ".codex/config.toml"
        cfg.write_text(CONFIG, encoding="utf-8")
        out = self.run_install("--agent", "codex")
        skill = (self.home / ".agents/skills/slides/SKILL.md").read_text(encoding="utf-8")
        self.assertIn("name: slides", skill)
        self.assertIn("# $slides", skill)
        self.assertFalse((self.home / ".claude/skills/presentations").exists())
        rules = (self.home / ".codex/rules/presentation-designer.rules").read_text(encoding="utf-8")
        self.assertEqual(rules.count("prefix_rule("), 5)
        text = cfg.read_text(encoding="utf-8")
        self.assertTrue(text.startswith(CONFIG))  # чужие настройки не тронуты
        self.assertIn('[sandbox_workspace_write]\nwritable_roots = ["%s"]' % self.app, text)
        self.assertIn("$slides правки", out)
        self.run_install("--agent", "codex")  # повтор не дублирует
        self.assertEqual(cfg.read_text(encoding="utf-8").count(str(self.app)), 1)
        self.run_install("--uninstall")
        self.assertEqual(cfg.read_text(encoding="utf-8"), CONFIG)
        self.assertFalse((self.home / ".codex/rules/presentation-designer.rules").exists())
        self.assertFalse((self.home / ".agents/skills/slides").exists())

    def test_codex_existing_table_and_roots(self):
        cfg = self.home / ".codex/config.toml"
        cfg.write_text(CONFIG + '\n[sandbox_workspace_write]\nnetwork_access = true\nwritable_roots = ["/data"]\n\n[other]\nk = 1\n', encoding="utf-8")
        self.run_install("--agent", "codex")
        text = cfg.read_text(encoding="utf-8")
        self.assertIn('writable_roots = ["/data", "%s"]' % self.app, text)
        self.assertIn("network_access = true", text)
        self.assertIn("[other]\nk = 1", text)
        self.run_install("--uninstall")
        self.assertIn('writable_roots = ["/data"]', cfg.read_text(encoding="utf-8"))

    def test_codex_table_without_roots(self):
        cfg = self.home / ".codex/config.toml"
        cfg.write_text("[sandbox_workspace_write]\nnetwork_access = false\n", encoding="utf-8")
        self.run_install("--agent", "codex")
        self.assertEqual(cfg.read_text(encoding="utf-8"),
                         '[sandbox_workspace_write]\nwritable_roots = ["%s"]\nnetwork_access = false\n' % self.app)

    def test_codex_unclear_config_left_alone(self):
        cfg = self.home / ".codex/config.toml"
        original = "sandbox_workspace_write.network_access = true\n"
        cfg.write_text(original, encoding="utf-8")
        out = self.run_install("--agent", "codex")
        self.assertEqual(cfg.read_text(encoding="utf-8"), original)
        self.assertIn("вручную", out)
        self.assertTrue((self.home / ".codex/rules/presentation-designer.rules").exists())

    def test_agent_commands_follow_install(self):
        env = dict(os.environ, PD_ROOT=str(self.app))
        code = "import sys; sys.path.insert(0, %r); from core import store; print(store.agent_commands('правки'))" % str(self.app / "app")
        get = lambda: subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, env=env).stdout.strip()
        self.assertEqual(get(), "['/presentations правки']")  # не установлено — по умолчанию Claude Code
        self.run_install("--agent", "codex")
        self.assertEqual(get(), "['$slides правки']")
        self.run_install("--agent", "claude")
        self.assertEqual(get(), "['/presentations правки', '$slides правки']")

    def test_legacy_record_migrated(self):
        legacy = self.home / ".config/presentation-designer/config.json"
        legacy.parent.mkdir(parents=True)
        rule = "Bash(python3 %s/app/tools/old.py *)" % self.app
        legacy.write_text(json.dumps({"app_dir": str(self.app), "added_rules": [rule]}), encoding="utf-8")
        (self.home / ".claude/settings.json").write_text(json.dumps({"permissions": {"allow": [rule]}}), encoding="utf-8")
        self.run_install("--agent", "claude")
        self.assertFalse(legacy.exists())
        allow = json.loads((self.home / ".claude/settings.json").read_text(encoding="utf-8"))["permissions"]["allow"]
        self.assertNotIn(rule, allow)  # устаревшее правило прошлой установки убрано

    @unittest.skipUnless(CODEX_BIN.exists(), "нет Codex CLI")
    def test_rules_accepted_by_codex(self):
        self.run_install("--agent", "codex")
        rules = str(self.home / ".codex/rules/presentation-designer.rules")
        check = lambda *cmd: json.loads(subprocess.run([str(CODEX_BIN), "execpolicy", "check", "--rules", rules] + list(cmd),
                                                       capture_output=True, text=True).stdout)
        self.assertEqual(check("python3", "%s/app/tools/agent.py" % self.app, "status")["decision"], "allow")
        self.assertNotIn("decision", check("python3", "%s/app/tools/other.py" % self.app))


if __name__ == "__main__":
    unittest.main()
