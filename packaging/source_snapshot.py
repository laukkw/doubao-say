"""Export current Git-visible files without committing or modifying the index."""
from pathlib import Path
import shutil
import subprocess


def export_source(root: Path, destination: Path) -> list[Path]:
    """Include unstaged/new source, exclude ignored files, reject unsafe content."""
    root = root.resolve()
    if destination.exists():
        raise ValueError("Snapshot destination must not exist")
    names = subprocess.check_output(
        ["git", "ls-files", "-z", "--cached", "--others", "--exclude-standard", "--deduplicate"],
        cwd=root).decode().split("\0")
    paths = []
    for name in filter(None, names):
        relative = Path(name)
        if relative.is_absolute() or ".." in relative.parts:
            raise ValueError("Unsafe repository path")
        path = root / relative
        if path.is_symlink() or any(p.is_symlink() for p in path.parents if p != root):
            raise ValueError(f"Symlink cannot be published: {relative}")
        if not path.exists():  # Unstaged deletion.
            continue
        if not path.is_file():
            raise ValueError(f"Not a regular file: {relative}")
        ignored = subprocess.run(["git", "check-ignore", "--no-index", "-q", "--", name], cwd=root)
        if ignored.returncode == 0:
            raise ValueError(f"Tracked ignored file: {relative}; remove it from the index first")
        if ignored.returncode != 1:
            raise ValueError("Cannot inspect ignore rules")
        paths.append(relative)
    destination.mkdir(parents=True)
    for relative in paths:
        target = destination / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(root / relative, target)
    return paths
