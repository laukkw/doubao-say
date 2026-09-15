#!/usr/bin/env python3
"""Local pre-publication checks. Never creates commits, pushes, or submits issues."""
import argparse
import hashlib
import json
from pathlib import Path
import re
import shutil
import subprocess
import tempfile
import tomllib

from source_snapshot import export_source

ROOT = Path(__file__).resolve().parents[1]


def check_documents(snapshot):
    for name in ("README.md", "README.zh-CN.md", "LICENSE", "NOTICE", "packaging/INSTALL.md"):
        text = (snapshot / name).read_text()
        for link in re.findall(r"\]\(([^)]+)\)", text):
            if "://" in link or link.startswith("#"):
                continue
            if not (snapshot / name).parent.joinpath(link.split("#")[0]).is_file():
                raise ValueError(f"Broken local link in {name}: {link}")
    manifest = json.loads((snapshot / "manifest.json").read_text())
    for field in ("author", "description", "name", "version", "id"):
        if not isinstance(manifest.get(field), str) or not manifest[field].strip():
            raise ValueError(f"Missing manifest field: {field}")
    version = tomllib.loads((snapshot / "pyproject.toml").read_text())["project"]["version"]
    if version != manifest["version"]:
        raise ValueError("Package and plugin versions differ")
    for name in ("start.sh", "setup-omarchy.sh", "install.sh"):
        if not (snapshot / name).stat().st_mode & 0o111:
            raise ValueError(f"Launcher is not executable: {name}")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--secrets", action="store_true", help="Require Gitleaks; scan all Git refs and source")
    args = parser.parse_args()
    if not shutil.which("omarchy"):
        raise SystemExit("Run on Omarchy: its official validator is required")
    reports = ROOT / "artifacts/marketplace"
    reports.mkdir(parents=True, exist_ok=True)
    (reports / "preflight.json").write_text(json.dumps({"status": "incomplete"}) + "\n")
    with tempfile.TemporaryDirectory(prefix="doubao-marketplace-") as directory:
        snapshot = Path(directory) / "source"
        files = export_source(ROOT, snapshot)
        check_documents(snapshot)
        subprocess.run(["omarchy", "plugin", "validate", str(snapshot)], check=True)
        if args.secrets:
            if not shutil.which("gitleaks"):
                raise SystemExit("Install Gitleaks or add its verified binary to PATH")
            shallow = subprocess.check_output(["git", "rev-parse", "--is-shallow-repository"], cwd=ROOT).strip()
            if shallow != b"false":
                raise SystemExit("History scan requires a full clone")
            refs = subprocess.check_output(["git", "for-each-ref", "--format=%(refname)"], cwd=ROOT).strip()
            scans = [("dir", snapshot, [])]
            if refs:
                scans.insert(0, ("git", ROOT, ["--log-opts=--all"]))
            else:
                print("No Git refs yet: scanning the complete publication snapshot; history scan is not applicable.")
            for mode, target, extra in scans:
                subprocess.run(["gitleaks", mode, str(target), *extra, "--redact", "--no-banner",
                                "--report-format", "json", "--report-path", str(reports / f"{mode}-secrets.json")],
                               check=True)
        report = {"status": "passed", "scope": "working-tree snapshot, not a committed marketplace approval",
                  "manifest_validation": "passed", "document_links": "passed",
                  "secret_scan": "passed" if args.secrets else "not run",
                  "files": {str(p): hashlib.sha256((snapshot / p).read_bytes()).hexdigest() for p in files}}
        (reports / "preflight.json").write_text(json.dumps(report, indent=2) + "\n")
        print(f"PASS: {len(files)} source files; manifest, links, versions, executable launchers")
        print("Not checked: public availability, marketplace ID reservation, clean OS, physical input, license clearance")


if __name__ == "__main__":
    main()
