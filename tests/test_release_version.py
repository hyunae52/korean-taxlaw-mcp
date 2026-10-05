from __future__ import annotations

import importlib.util
import json
from pathlib import Path
import subprocess
import tempfile
import unittest

REPOSITORY = Path(__file__).resolve().parents[1]
SCRIPT = REPOSITORY / ".github/scripts/check_release_version.py"
spec = importlib.util.spec_from_file_location("taxlab_release_check", SCRIPT)
checker = importlib.util.module_from_spec(spec)
spec.loader.exec_module(checker)


class CheckoutVersionTests(unittest.TestCase):
    def test_actual_checkout_version_declarations_match(self):
        # Existing offline CI uses a shallow checkout. Never represent this as
        # verification of ancestor commits or immutable released contents.
        result = checker.check_metadata(REPOSITORY)
        self.assertEqual(result["verification_scope"], "metadata_only")
        self.assertNotIn("release_commit", result)


class ReleaseVersionTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory(prefix="taxlab-release-test-")
        self.root = Path(self.directory.name).resolve()
        self.addCleanup(self.cleanup)
        self.git("init", "--initial-branch=main")
        self.set_versions("2.1.0")
        self.write("src/korean_taxlaw_mcp/service.py", "VALUE = 1\n")
        self.commit("upstream")
        self.upstream = self.git("rev-parse", "HEAD")
        self.set_versions("2.1.0.post1")
        self.metadata = {"schema_version": 1,
                         "upstream": {"repository": "zisu17/korean-taxlaw-mcp", "version": "2.1.0", "commit": self.upstream},
                         "fork": {"repository": "hyunae52/korean-taxlaw-mcp", "version": "2.1.0.post1", "tag": "taxlab-v2.1.0.post1"}}
        self.save_metadata()
        self.commit("reviewed fork")
        self.released = self.git("rev-parse", "HEAD")
        self.git("tag", "taxlab-v2.1.0.post1")

    def cleanup(self):
        # Verify the generated fixture path before TemporaryDirectory removes it.
        self.assertEqual(self.root.parent, Path(tempfile.gettempdir()).resolve())
        self.assertTrue(self.root.name.startswith("taxlab-release-test-"))
        self.assertFalse(self.root.is_symlink())
        self.directory.cleanup()

    def git(self, *args):
        return subprocess.check_output(["git", "-C", str(self.root), "-c", "user.name=Release Test",
                                        "-c", "user.email=release-test@example.test", *args],
                                       stderr=subprocess.PIPE, text=True, encoding="utf-8").strip()

    def write(self, path, content):
        target = self.root / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")

    def commit(self, message):
        self.git("add", ".")
        self.git("commit", "-m", message)

    def set_versions(self, version):
        self.write("pyproject.toml", f'[project]\nname = "korean-taxlaw-mcp"\nversion = "{version}"\n')
        self.write("src/korean_taxlaw_mcp/__init__.py", f'__version__ = "{version}"\n')
        self.write("uv.lock", f'[[package]]\nname = "korean-taxlaw-mcp"\nversion = "{version}"\nsource = {{ editable = "." }}\n')

    def save_metadata(self):
        self.write(".github/taxlab-release.json", json.dumps(self.metadata))

    def test_released_version_keeps_upstream_and_fork_identities_separate(self):
        result = checker.check(self.root)
        self.assertEqual(result["verification_scope"], "full_history")
        self.assertEqual(result["upstream"]["version"], "2.1.0")
        self.assertEqual(result["fork"]["version"], "2.1.0.post1")
        self.assertEqual(result["release_commit"], self.released)
        self.assertTrue(result["tag_exists"])
        self.assertFalse(result["production_activation"])

    def test_metadata_check_works_without_history_and_does_not_claim_release_verification(self):
        self.git("update-ref", "-d", "HEAD")
        result = checker.check_metadata(self.root)
        self.assertEqual(result["verification_scope"], "metadata_only")
        self.assertNotIn("tag_exists", result)
        self.assertNotIn("release_commit", result)

    def test_full_check_refuses_shallow_history(self):
        self.write(".git/shallow", self.released + "\n")
        with self.assertRaisesRegex(checker.ReleaseError, "shallow checkout is insufficient"):
            checker.check(self.root)

    def test_package_version_mismatch_is_rejected(self):
        self.write("pyproject.toml", '[project]\nname = "korean-taxlaw-mcp"\nversion = "2.2.0"\n')
        with self.assertRaisesRegex(checker.ReleaseError, "pyproject.toml"):
            checker.check(self.root)

    def test_runtime_version_mismatch_is_rejected(self):
        self.write("src/korean_taxlaw_mcp/__init__.py", '__version__ = "2.2.0"\n')
        with self.assertRaisesRegex(checker.ReleaseError, "Runtime __version__"):
            checker.check(self.root)

    def test_lock_version_mismatch_is_rejected(self):
        self.write("uv.lock", '[[package]]\nname = "korean-taxlaw-mcp"\nversion = "2.2.0"\nsource = { editable = "." }\n')
        with self.assertRaisesRegex(checker.ReleaseError, "uv.lock"):
            checker.check(self.root)

    def test_upstream_version_must_match_its_actual_commit(self):
        self.metadata["upstream"]["version"] = "2.0.0"
        self.save_metadata()
        with self.assertRaisesRegex(checker.ReleaseError, "Upstream version does not match"):
            checker.check(self.root)

    def test_unrelated_upstream_commit_is_rejected(self):
        tree = self.git("rev-parse", "HEAD^{tree}")
        self.metadata["upstream"]["commit"] = self.git("commit-tree", tree, "-m", "unrelated root")
        self.save_metadata()
        with self.assertRaisesRegex(checker.ReleaseError, "not included"):
            checker.check(self.root)

    def test_runtime_patch_cannot_reuse_an_existing_release(self):
        self.write("src/korean_taxlaw_mcp/service.py", "VALUE = 2\n")
        with self.assertRaisesRegex(checker.ReleaseError, "existing release tag"):
            checker.check(self.root)

    def test_untracked_runtime_file_also_requires_a_new_release(self):
        self.write("src/korean_taxlaw_mcp/new_service.py", "VALUE = 2\n")
        with self.assertRaisesRegex(checker.ReleaseError, "existing release tag"):
            checker.check(self.root)

    def test_documentation_and_ci_updates_do_not_bump_the_engine_version(self):
        self.write("docs/maintenance.md", "Updated release procedure.\n")
        self.write(".github/workflows/review.yml", "name: review\n")
        self.commit("documentation and CI")
        result = checker.check(self.root)
        self.assertEqual(result["release_commit"], self.released)
        self.assertNotEqual(result["checkout_commit"], self.released)

    def test_tag_and_repository_mismatches_are_rejected(self):
        self.metadata["fork"]["tag"] = "taxlab-v2.2.0"
        self.save_metadata()
        with self.assertRaisesRegex(checker.ReleaseError, "Release tag"):
            checker.check(self.root)
        self.metadata["fork"]["tag"] = "taxlab-v2.1.0.post1"
        self.metadata["upstream"]["repository"] = "other/korean-taxlaw-mcp"
        self.save_metadata()
        with self.assertRaisesRegex(checker.ReleaseError, "Unexpected upstream"):
            checker.check(self.root)

    def test_new_reviewed_version_is_a_candidate_until_its_tag_exists(self):
        version = "2.1.0.post1+taxlab.1"
        self.set_versions(version)
        self.metadata["fork"].update(version=version, tag="taxlab-v" + version)
        self.save_metadata()
        self.write("src/korean_taxlaw_mcp/service.py", "VALUE = 2\n")
        result = checker.check(self.root)
        self.assertFalse(result["tag_exists"])
        self.assertIsNone(result["release_commit"])
        self.assertFalse(result["production_activation"])

    def test_ambiguous_runtime_version_is_rejected_without_importing_package(self):
        with self.assertRaisesRegex(checker.ReleaseError, "exactly one"):
            checker.module_version('__version__ = "2.1.0"\n__version__ = "2.2.0"\n')
        self.assertEqual(checker.module_version('__version__ = "2.1.0"\nraise RuntimeError("must not run")\n'), "2.1.0")


if __name__ == "__main__":
    unittest.main()
