"""Restore pinned framework checkouts and local code patches without overwriting existing work."""
import argparse
import hashlib
import json
from pathlib import Path
import subprocess


def restore(root, *, dry_run=False):
    root = Path(root)
    lock = json.loads((root / "lock.json").read_text())
    for repo in lock["repositories"]:
        destination = root / repo["name"]
        patch = root / repo["patch"]
        if hashlib.sha256(patch.read_bytes()).hexdigest() != repo["patch_sha256"]:
            raise ValueError(f"Patch checksum mismatch: {patch}")
        if destination.exists():
            print(f"Preserving existing checkout: {destination}")
            continue
        print(f"Restore {repo['name']} @ {repo['commit']}")
        if dry_run:
            continue
        subprocess.run(["git", "clone", "--no-checkout", repo["url"], str(destination)], check=True)
        subprocess.run(["git", "-C", str(destination), "checkout", "--detach", repo["commit"]], check=True)
        if patch.stat().st_size:
            subprocess.run(["git", "-C", str(destination), "apply", "--check", str(patch.resolve())], check=True)
            subprocess.run(["git", "-C", str(destination), "apply", str(patch.resolve())], check=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parents[1] / "third_party")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    restore(args.root, dry_run=args.dry_run)


if __name__ == "__main__":
    main()
