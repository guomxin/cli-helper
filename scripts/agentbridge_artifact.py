"""Fixed-commit wheel and locally trusted validation receipts (no remote actions)."""
from __future__ import annotations

import argparse
import hashlib
import importlib.metadata
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import zipfile

REQUIRED = ("bscli/adapters/seeyon_page_scripts/continue_submit.js",
            "bscli/adapters/seeyon_page_scripts/launch_save_draft.js")
CHECKS = ["python-full", "compileall", "pip-check", "workspace-node", "openclaw", "openclaw-pack", "installed-wheel"]


def run(args, cwd):
    result = subprocess.run([str(x) for x in args], cwd=cwd, check=True,
                            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, encoding="utf-8")
    return result.stdout.strip()


def git(root, *args):
    metadata = root / (".gitrepo" if (root / ".gitrepo").exists() else ".git")
    return run(["git", f"--git-dir={metadata}", f"--work-tree={root}", *args], root)


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def save(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    os.replace(temporary, path)


def inputs(root):
    # Hash actual tracked bytes as well as HEAD: changes during tests must fail closed.
    files = git(root, "ls-files", "-z").split("\0")
    tracked = {name: digest(root / name) for name in files if name}
    packages = sorted((d.metadata["Name"].lower(), d.version) for d in importlib.metadata.distributions())
    installed_locks = {name: digest(root / name) if (root / name).is_file() else None for name in (
        "integrations/openclaw-agentbridge/node_modules/.package-lock.json",
        "integrations/mcp-app/node_modules/.package-lock.json")}
    return {"commit": git(root, "rev-parse", "HEAD"), "tracked": tracked, "installedNodeLocks": installed_locks,
            "python": sys.version, "packages": packages,
            "node": run(["node", "--version"], root), "npm": run(["npm.cmd" if os.name == "nt" else "npm", "--version"], root)}


def clean(root):
    if git(root, "status", "--porcelain", "--untracked-files=no"):
        raise ValueError("Formal candidate requires clean tracked files; use an isolated committed checkout")


def extract(archive, destination):
    with zipfile.ZipFile(archive) as source:
        for name in source.namelist():
            if not (destination / name).resolve().is_relative_to(destination.resolve()):
                raise ValueError("Unsafe archive member")
        source.extractall(destination)


def inspect_wheel(wheel, source):
    with zipfile.ZipFile(wheel) as archive:
        names = archive.namelist()
        for name in REQUIRED:
            if name not in names or not archive.read(name):
                raise ValueError(f"Missing required wheel resource: {name}")
        # All tracked package resources and Python modules must survive packaging.
        expected = [p.relative_to(source).as_posix() for p in (source / "bscli").rglob("*")
                    if p.is_file() and p.suffix in {".py", ".js", ".mjs", ".html", ".css", ".svg"}]
        for name in expected:
            if name not in names or archive.read(name) != (source / name).read_bytes():
                raise ValueError(f"Wheel/source mismatch: {name}")
        return [{"path": name, "sha256": hashlib.sha256(archive.read(name)).hexdigest()}
                for name in sorted(names)]


def installed_probe(wheel, directory):
    env = directory / "install-env"
    run([sys.executable, "-m", "venv", env], directory)
    python = env / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
    run([python, "-m", "pip", "install", "--no-index", "--no-deps", wheel], directory)
    code = """import json, pathlib, sys, importlib.metadata
from bscli.adapters import page_scripts
p = pathlib.Path(page_scripts.__file__).resolve()
assert p.is_relative_to(pathlib.Path(sys.prefix).resolve()), str(p)
loads = {a: len(page_scripts.load_seeyon_action_page_script(a)['script_source']) for a in ('ContinueSubmit','SaveDraft')}
assert all(loads.values())
print(json.dumps({'module': str(p), 'version': importlib.metadata.version('cli-helper'), 'loads': loads}))
"""
    return json.loads(run([python, "-I", "-c", code], directory))


def build(root, output):
    clean(root)
    commit = git(root, "rev-parse", "HEAD")
    output.mkdir(parents=True, exist_ok=False)
    archive = output / "source.zip"
    git(root, "archive", "--format=zip", f"--output={archive}", commit)
    source = output / "source"
    source.mkdir()
    extract(archive, source)
    # Source is exclusively a Git archive. Stale build/egg-info cannot leak from the workspace.
    wheel_dir = output / "wheel"
    run([sys.executable, "-m", "pip", "wheel", "--disable-pip-version-check", "--no-deps",
         "--no-build-isolation", "--wheel-dir", wheel_dir, source], output)
    wheels = list(wheel_dir.glob("*.whl"))
    if len(wheels) != 1:
        raise ValueError("Expected exactly one wheel")
    wheel = wheels[0]
    entries = inspect_wheel(wheel, source)
    probe = installed_probe(wheel, output)
    manifest = {"schema": "agentbridge.artifact.v1", "commit": commit, "wheel": str(wheel),
                "sha256": digest(wheel), "archiveSha256": digest(archive), "files": entries,
                "installedProbe": probe, "environment": inputs(root)}
    save(output / "artifact.json", manifest)
    return output / "artifact.json"


def begin(root, receipt):
    clean(root)
    save(receipt, {"schema": "agentbridge.validation.v1", "status": "running", "inputs": inputs(root)})


def finish(root, receipt):
    original = json.loads(receipt.read_text(encoding="utf-8"))
    current = inputs(root)
    if original["status"] != "running" or original["inputs"] != json.loads(json.dumps(current)):
        raise ValueError("Candidate inputs changed during validation")
    output = Path(tempfile.mkdtemp(prefix="candidate-", dir=receipt.parent)) / "artifact"
    manifest = build(root, output)
    clean(root)
    if json.loads(json.dumps(inputs(root))) != original["inputs"]:
        raise ValueError("Candidate inputs changed during artifact checks")
    save(receipt, {**original, "status": "succeeded", "checks": CHECKS, "skipped": [],
                   "manifest": str(manifest), "manifestSha256": digest(manifest)})


def verify(root, receipt):
    clean(root)
    data = json.loads(receipt.read_text(encoding="utf-8"))
    if (data.get("schema") != "agentbridge.validation.v1" or data.get("status") != "succeeded"
            or data.get("checks") != CHECKS or data.get("skipped") != []
            or data.get("inputs") != json.loads(json.dumps(inputs(root)))):
        raise ValueError("No complete validation for this candidate and environment; run Full validation")
    manifest_path = Path(data["manifest"])
    if digest(manifest_path) != data["manifestSha256"]:
        raise ValueError("Artifact manifest changed")
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    if manifest["commit"] != data["inputs"]["commit"] or digest(Path(manifest["wheel"])) != manifest["sha256"]:
        raise ValueError("Artifact commit or wheel hash mismatch")
    return {"manifest": str(manifest_path), "wheel": manifest["wheel"], "sha256": manifest["sha256"]}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=["begin", "finish", "verify", "build"])
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    root = args.root.resolve()
    receipt = root / "output/release-validation/full.json"
    if args.action == "begin":
        begin(root, receipt)
    elif args.action == "finish":
        finish(root, receipt)
    elif args.action == "build":
        if not args.output:
            parser.error("build requires --output")
        print(build(root, args.output.resolve()))
    else:
        print(json.dumps(verify(root, receipt)))


if __name__ == "__main__":
    main()
