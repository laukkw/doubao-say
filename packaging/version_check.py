#!/usr/bin/env python3
"""Keep every published version source and an optional release tag aligned."""
import argparse
import json
import re
import tomllib
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def versions(root=ROOT):
    package = tomllib.loads((root / "pyproject.toml").read_text())["project"]["version"]
    plugin = json.loads((root / "manifest.json").read_text())["version"]
    product_source = (root / "src/doubao_input/product.py").read_text()
    match = re.search(r'^VERSION\s*=\s*["\']([^"\']+)["\']', product_source, re.MULTILINE)
    if not match:
        raise ValueError("src/doubao_input/product.py has no VERSION")
    return {"package": package, "plugin": plugin, "runtime": match.group(1)}


def validate(tag=None, root=ROOT):
    found = versions(root)
    unique = set(found.values())
    if len(unique) != 1:
        raise ValueError("Version mismatch: " + ", ".join(f"{key}={value}" for key, value in found.items()))
    version = unique.pop()
    expected_tag = f"v{version}"
    if tag and tag != expected_tag:
        raise ValueError(f"Release tag {tag!r} must be {expected_tag!r}")
    return version


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--tag", help="Require an exact v<version> release tag")
    args = parser.parse_args()
    print(validate(args.tag))


if __name__ == "__main__":
    main()
