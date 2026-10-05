"""Read-only release identity checks; never imports or installs the MCP package."""
from __future__ import annotations

import argparse
import ast
import json
from pathlib import Path
import re
import subprocess
import tomllib

PACKAGE = "korean-taxlaw-mcp"
RUNTIME_PATHS = ["src", "pyproject.toml", "uv.lock"]


class ReleaseError(Exception):
    pass


def require(condition: bool, message: str) -> None:
    if not condition:
        raise ReleaseError(message)


def git(root: Path, *args: str, allowed: tuple[int, ...] = (0,)) -> subprocess.CompletedProcess:
    result = subprocess.run(["git", "-C", str(root), *args], capture_output=True,
                            text=True, encoding="utf-8", timeout=30, check=False)
    require(result.returncode in allowed, "Git history is unavailable; fetch full history and tags.")
    return result


def module_version(text: str) -> str:
    values = [node.value.value for node in ast.parse(text).body
              if isinstance(node, ast.Assign)
              and any(isinstance(target, ast.Name) and target.id == "__version__" for target in node.targets)
              and isinstance(node.value, ast.Constant) and isinstance(node.value.value, str)]
    require(len(values) == 1, "Expected exactly one literal __version__ assignment.")
    return values[0]


def check_metadata(root: Path) -> dict:
    root = root.resolve()
    release = json.loads((root / ".github/taxlab-release.json").read_text(encoding="utf-8"))
    require(release.get("schema_version") == 1, "Unsupported release metadata schema.")
    upstream, fork = release["upstream"], release["fork"]
    require(upstream.get("repository") == "zisu17/korean-taxlaw-mcp", "Unexpected upstream repository.")
    require(fork.get("repository") == "hyunae52/korean-taxlaw-mcp", "Unexpected fork repository.")
    stable = r"(?:0|[1-9][0-9]*)\.(?:0|[1-9][0-9]*)\.(?:0|[1-9][0-9]*)"
    require(isinstance(upstream.get("version"), str)
            and re.fullmatch(stable, upstream["version"]) is not None, "Invalid upstream version.")
    version = fork.get("version")
    require(isinstance(version, str)
            and re.fullmatch(stable + r"(?:\.post[1-9][0-9]*)?(?:\+taxlab\.[1-9][0-9]*)?", version) is not None,
            "Invalid reviewed fork version.")
    require(fork.get("tag") == "taxlab-v" + version, "Release tag does not match the fork version.")
    commit = upstream.get("commit")
    require(isinstance(commit, str) and re.fullmatch(r"[a-f0-9]{40}", commit) is not None,
            "Upstream commit must be a full immutable commit ID.")

    project = tomllib.loads((root / "pyproject.toml").read_text(encoding="utf-8"))["project"]
    require(project.get("name") == PACKAGE and project.get("version") == version,
            "pyproject.toml does not match the reviewed fork version.")
    require(module_version((root / "src/korean_taxlaw_mcp/__init__.py").read_text(encoding="utf-8")) == version,
            "Runtime __version__ does not match the reviewed fork version.")
    lock = tomllib.loads((root / "uv.lock").read_text(encoding="utf-8"))
    own = [item for item in lock.get("package", []) if item.get("name") == PACKAGE]
    require(len(own) == 1 and own[0].get("version") == version and own[0].get("source") == {"editable": "."},
            "uv.lock project entry does not match the reviewed fork version.")

    return {"status": "pass", "verification_scope": "metadata_only",
            "upstream": upstream, "fork": fork, "production_activation": False}


def check(root: Path) -> dict:
    root = root.resolve()
    metadata = check_metadata(root)
    upstream, fork = metadata["upstream"], metadata["fork"]
    commit = upstream["commit"]
    require(git(root, "rev-parse", "--is-shallow-repository").stdout.strip() == "false",
            "Full release verification requires full history and tags; shallow checkout is insufficient.")

    require(git(root, "merge-base", "--is-ancestor", commit, "HEAD", allowed=(0, 1)).returncode == 0,
            "Reviewed upstream commit is not included in this checkout.")
    source_project = tomllib.loads(git(root, "show", commit + ":pyproject.toml").stdout)["project"]
    require(source_project.get("name") == PACKAGE and source_project.get("version") == upstream["version"],
            "Upstream version does not match the recorded upstream commit.")

    tag = git(root, "rev-parse", "--verify", "--quiet", "refs/tags/" + fork["tag"] + "^{commit}", allowed=(0, 1))
    tag_commit = tag.stdout.strip() if tag.returncode == 0 else None
    if tag_commit:
        # Documentation and CI may advance main without changing the released engine.
        changed = git(root, "diff", "--quiet", tag_commit, "--", *RUNTIME_PATHS, allowed=(0, 1))
        untracked = git(root, "ls-files", "--others", "--exclude-standard", "--", *RUNTIME_PATHS).stdout.strip()
        require(changed.returncode == 0 and not untracked,
                "Runtime or package content changed under an existing release tag; create a new reviewed version.")
    return {"status": "pass", "verification_scope": "full_history", "upstream": upstream, "fork": fork,
            "checkout_commit": git(root, "rev-parse", "HEAD").stdout.strip(),
            "release_commit": tag_commit, "tag_exists": tag_commit is not None,
            "production_activation": False}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parents[2])
    parser.add_argument("--metadata-only", action="store_true",
                        help="Check version declarations only; does not verify Git history or released content.")
    args = parser.parse_args()
    try:
        print(json.dumps(check_metadata(args.root) if args.metadata_only else check(args.root), indent=2))
    except (ReleaseError, ValueError, KeyError, OSError, SyntaxError, subprocess.TimeoutExpired) as error:
        message = str(error) if isinstance(error, ReleaseError) else "Release metadata or repository could not be validated."
        print(json.dumps({"status": "failed", "error": message}))
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
