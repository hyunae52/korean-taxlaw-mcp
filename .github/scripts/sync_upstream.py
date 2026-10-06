"""Prepare a bounded source sync; never execute downloaded code in this step."""
from __future__ import annotations
import argparse
import ast
import json
import os
from pathlib import Path
import re
import subprocess
import tomllib

SHA = re.compile(r"[a-f0-9]{40}")
STABLE = re.compile(r"(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)")
PACKAGE = "korean-taxlaw-mcp"


def git(root, *args):
    return subprocess.check_output(["git", "-C", str(root), *args], timeout=60)


def files(root, ref, *paths):
    result = {}
    for row in git(root, "ls-tree", "-r", "-z", ref, "--", *paths).split(b"\0"):
        if not row:
            continue
        metadata, name = row.split(b"\t", 1)
        mode, kind, blob = metadata.decode().split()
        name = name.decode("utf-8")
        if mode not in ("100644", "100755") or kind != "blob" or ".." in Path(name).parts or "\\" in name:
            raise ValueError("UNSUPPORTED_SOURCE_ENTRY")
        content = git(root, "cat-file", "blob", blob)
        if len(content) > 10 * 1024 * 1024:
            raise ValueError("SOURCE_FILE_TOO_LARGE")
        result[name] = content
    if len(result) > 2000 or sum(map(len, result.values())) > 100 * 1024 * 1024:
        raise ValueError("SOURCE_TREE_TOO_LARGE")
    return result


def project_version(content):
    project = tomllib.loads(content.decode())["project"]
    if project.get("name") != PACKAGE or not STABLE.fullmatch(project.get("version", "")):
        raise ValueError("UPSTREAM_VERSION_NOT_STABLE")
    return project["version"]


def normalize(name, content):
    if name == "pyproject.toml":
        data = tomllib.loads(content.decode())
        data["project"]["version"] = "VERSION"
        data["project"].pop("urls", None)  # TaxLab adds its repository and upstream links.
        return data
    if name == "uv.lock":
        data = tomllib.loads(content.decode())
        own = [p for p in data["package"] if p["name"] == PACKAGE]
        if len(own) != 1:
            raise ValueError("LOCK_PROJECT_MISSING")
        own[0]["version"] = "VERSION"
        return data
    if name == "src/korean_taxlaw_mcp/__init__.py":
        return re.sub(rb'(?m)^__version__\s*=\s*[\x22\x27][^\x22\x27]+[\x22\x27]', b'__version__ = "VERSION"', content)
    return content


def same_runtime(first, second):
    return first.keys() == second.keys() and all(normalize(name, first[name]) == normalize(name, second[name]) for name in first)


def next_version(old, upstream_version):
    previous = old["upstream"]["version"]
    if tuple(map(int, upstream_version.split("."))) < tuple(map(int, previous.split("."))):
        raise ValueError("UPSTREAM_VERSION_DOWNGRADE")
    if upstream_version != previous:
        return upstream_version + ".post1"
    match = re.fullmatch(re.escape(previous) + r"(?:\.post([1-9][0-9]*))?", old["fork"]["version"])
    if not match:
        raise ValueError("FORK_VERSION_NEEDS_REVIEW")
    return previous + ".post" + str(int(match[1] or 0) + 1)


def rewrite_versions(root, version):
    project = root / "pyproject.toml"
    text, count = re.subn(r'(?m)^version = "[^"]+"$', 'version = "' + version + '"', project.read_text(encoding="utf-8"), count=1)
    if count != 1:
        raise ValueError("PROJECT_VERSION_NOT_LITERAL")
    project.write_text(text, encoding="utf-8")
    module = root / "src/korean_taxlaw_mcp/__init__.py"
    text, count = re.subn(r'(?m)^__version__\s*=\s*[\x22\x27][^\x22\x27]+[\x22\x27]', '__version__ = "' + version + '"', module.read_text(encoding="utf-8"))
    if count != 1:
        raise ValueError("MODULE_VERSION_NOT_LITERAL")
    ast.parse(text)
    module.write_text(text, encoding="utf-8")
    lock = root / "uv.lock"
    text, count = re.subn(r'(\[\[package\]\]\nname = "korean-taxlaw-mcp"\nversion = ")[^"]+("\n)', r'\g<1>' + version + r'\2', lock.read_text(encoding="utf-8"))
    if count != 1:
        raise ValueError("LOCK_VERSION_NOT_LITERAL")
    lock.write_text(text, encoding="utf-8")


def prepare(root, upstream):
    root = Path(root).resolve()
    if not SHA.fullmatch(upstream):
        raise ValueError("INVALID_UPSTREAM_COMMIT")
    if git(root, "status", "--porcelain").strip():
        raise ValueError("DIRTY_CHECKOUT")
    base = git(root, "rev-parse", "HEAD").decode().strip()
    old = json.loads((root / ".github/taxlab-release.json").read_text(encoding="utf-8"))
    if old["upstream"]["repository"] != "zisu17/korean-taxlaw-mcp" or old["fork"]["repository"] != "hyunae52/korean-taxlaw-mcp":
        raise ValueError("UNEXPECTED_REPOSITORY")
    if old["upstream"]["commit"] == upstream:
        return {"changed": False, "base": base, "head": base, "runtime_changed": False,
                "version": old["fork"]["version"], "tag": old["fork"]["tag"]}
    subprocess.run(["git", "-C", str(root), "merge-base", "--is-ancestor", old["upstream"]["commit"], upstream], check=True, timeout=30)
    paths = ("src", "pyproject.toml", "uv.lock")
    previous = files(root, old["upstream"]["commit"], *paths)
    current = files(root, base, *paths)
    incoming = files(root, upstream, *paths)
    if not same_runtime(previous, current):
        raise ValueError("FORK_LOCAL_RUNTIME_CHANGES_REQUIRE_REVIEW")
    if files(root, base, "LICENSE") != files(root, upstream, "LICENSE"):
        raise ValueError("LICENSE_CHANGED")
    version = project_version(incoming["pyproject.toml"])
    # Even packaging-only byte changes need a new immutable release identity.
    changed = previous != incoming
    selected = next_version(old, version) if changed else old["fork"]["version"]
    # Keep fork workflows/policy/docs. Record upstream ancestry, then import only
    # runtime and upstream test files. No downloaded script runs with write access.
    git(root, "merge", "--no-commit", "--no-ff", "--strategy=ours", upstream)
    prior_tests = files(root, old["upstream"]["commit"], "tests")
    new_tests = files(root, upstream, "tests")
    current_tests = files(root, base, "tests")
    for name, content in current_tests.items():
        if name in prior_tests and content != prior_tests[name]:
            raise ValueError("FORK_LOCAL_TEST_CHANGES_REQUIRE_REVIEW")
        if name not in prior_tests and name in new_tests and content != new_tests[name]:
            raise ValueError("FORK_TEST_COLLISION")
    for name in set(previous) | set(prior_tests):
        target = root / name
        if name not in incoming and name not in new_tests:
            target.unlink()
    for name, content in {**incoming, **new_tests}.items():
        target = root / name
        if not target.resolve().is_relative_to(root):
            raise ValueError("SOURCE_PATH_ESCAPE")
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(content)
    # Repository links are fork metadata, not executable source.
    project = root / "pyproject.toml"
    text = project.read_text(encoding="utf-8")
    text = re.sub(r'(?m)^Repository = "https://github.com/zisu17/korean-taxlaw-mcp"$', 'Repository = "https://github.com/hyunae52/korean-taxlaw-mcp"\nUpstream = "https://github.com/zisu17/korean-taxlaw-mcp"', text)
    project.write_text(text, encoding="utf-8")
    rewrite_versions(root, selected)
    metadata = {"schema_version": 1,
                "upstream": {"repository": "zisu17/korean-taxlaw-mcp", "version": version, "commit": upstream},
                "fork": {"repository": "hyunae52/korean-taxlaw-mcp", "version": selected, "tag": "taxlab-v" + selected}}
    (root / ".github/taxlab-release.json").write_text(json.dumps(metadata, indent=2) + "\n", encoding="utf-8")
    git(root, "add", "src", "tests", "pyproject.toml", "uv.lock", ".github/taxlab-release.json")
    git(root, "commit", "-m", "chore: sync zisu17 " + upstream[:12] + " as " + selected)
    return {"changed": True, "runtime_changed": changed, "base": base,
            "head": git(root, "rev-parse", "HEAD").decode().strip(), "upstream": upstream,
            "version": selected, "tag": "taxlab-v" + selected}


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--upstream", required=True)
    args = parser.parse_args()
    result = prepare(args.root, args.upstream)
    if os.environ.get("GITHUB_OUTPUT"):
        with open(os.environ["GITHUB_OUTPUT"], "a", encoding="utf-8") as output:
            for key, value in result.items():
                output.write(f"{key}={str(value).lower() if isinstance(value, bool) else value}\n")
    print(json.dumps(result))
