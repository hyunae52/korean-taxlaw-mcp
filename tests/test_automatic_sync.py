import importlib.util
import json
from pathlib import Path
import subprocess
import tempfile
import unittest

path = Path(__file__).resolve().parents[1] / ".github/scripts/sync_upstream.py"
spec = importlib.util.spec_from_file_location("sync_upstream", path)
sync = importlib.util.module_from_spec(spec)
spec.loader.exec_module(sync)


class AutomaticSyncTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.git("init", "-q", "-b", "main")
        self.git("config", "user.name", "Sync test")
        self.git("config", "user.email", "sync@example.invalid")
        self.write("src/korean_taxlaw_mcp/__init__.py", '__version__ = "2.1.0"\n')
        self.write("src/korean_taxlaw_mcp/server.py", "ORIGINAL = True\n")
        self.write("tests/test_original.py", "assert True\n")
        self.write("pyproject.toml", '[project]\nname = "korean-taxlaw-mcp"\nversion = "2.1.0"\n[project.urls]\nRepository = "https://github.com/zisu17/korean-taxlaw-mcp"\n')
        self.write("uv.lock", 'version = 1\n[[package]]\nname = "korean-taxlaw-mcp"\nversion = "2.1.0"\nsource = { editable = "." }\n')
        self.write("LICENSE", "MIT\n")
        self.commit("original")
        self.upstream = self.git("rev-parse", "HEAD")
        self.git("branch", "upstream")
        sync.rewrite_versions(self.root, "2.1.0.post1")
        self.write(".github/workflows/owned.yml", "trusted fork policy\n")
        self.write("tests/test_fork.py", "assert True # own test\n")
        self.write(".github/taxlab-release.json", json.dumps({"schema_version": 1,
            "upstream": {"repository": "zisu17/korean-taxlaw-mcp", "commit": self.upstream, "version": "2.1.0"},
            "fork": {"repository": "hyunae52/korean-taxlaw-mcp", "version": "2.1.0.post1", "tag": "taxlab-v2.1.0.post1"}}))
        self.commit("fork metadata")

    def git(self, *args):
        return subprocess.check_output(["git", "-C", str(self.root), *args], stderr=subprocess.DEVNULL).decode().strip()

    def write(self, name, content):
        path = self.root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")

    def commit(self, message):
        self.git("add", ".")
        self.git("commit", "-qm", message)

    def incoming(self, version="2.1.0", license_change=False):
        self.git("switch", "upstream")
        sync.rewrite_versions(self.root, version)
        self.write("src/korean_taxlaw_mcp/server.py", "IMPROVED = True\n")
        self.write(".github/workflows/owned.yml", "untrusted incoming workflow\n")
        if license_change:
            self.write("LICENSE", "different license\n")
        self.commit("upstream change")
        head = self.git("rev-parse", "HEAD")
        self.git("switch", "main")
        return head

    def test_current_is_noop_and_never_reversions(self):
        head = self.git("rev-parse", "HEAD")
        result = sync.prepare(self.root, self.upstream)
        self.assertFalse(result["changed"])
        self.assertEqual(self.git("rev-parse", "HEAD"), head)

    def test_same_version_patch_gets_new_post_release_without_importing_workflows(self):
        incoming = self.incoming()
        result = sync.prepare(self.root, incoming)
        self.assertEqual(result["version"], "2.1.0.post2")
        self.assertTrue(result["runtime_changed"])
        self.assertEqual((self.root / ".github/workflows/owned.yml").read_text(), "trusted fork policy\n")
        self.assertTrue((self.root / "tests/test_fork.py").exists())
        self.assertIn("IMPROVED", (self.root / "src/korean_taxlaw_mcp/server.py").read_text())
        self.git("merge-base", "--is-ancestor", incoming, "HEAD")
        self.assertEqual(self.git("status", "--porcelain"), "")

    def test_new_upstream_release_starts_its_own_post_sequence(self):
        result = sync.prepare(self.root, self.incoming("2.2.0"))
        self.assertEqual(result["version"], "2.2.0.post1")
        self.assertIn('version = "2.2.0.post1"', (self.root / "uv.lock").read_text())

    def test_fork_runtime_fix_is_never_silently_overwritten(self):
        incoming = self.incoming()
        self.write("src/korean_taxlaw_mcp/server.py", "LOCAL_FIX = True\n")
        self.commit("local fix")
        head = self.git("rev-parse", "HEAD")
        with self.assertRaisesRegex(ValueError, "LOCAL_RUNTIME"):
            sync.prepare(self.root, incoming)
        self.assertEqual(self.git("rev-parse", "HEAD"), head)
        self.assertIn("LOCAL_FIX", (self.root / "src/korean_taxlaw_mcp/server.py").read_text())

    def test_license_downgrade_and_dirty_worktree_are_blocked(self):
        incoming = self.incoming(license_change=True)
        with self.assertRaisesRegex(ValueError, "LICENSE_CHANGED"):
            sync.prepare(self.root, incoming)
        self.write("note", "keep")
        with self.assertRaisesRegex(ValueError, "DIRTY_CHECKOUT"):
            sync.prepare(self.root, incoming)
        with self.assertRaisesRegex(ValueError, "DOWNGRADE"):
            sync.next_version({"upstream": {"version": "2.1.0"}}, "2.0.0")


if __name__ == "__main__":
    unittest.main()
