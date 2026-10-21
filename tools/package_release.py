"""Build and package public source from this Git checkout, never the owner workspace."""

import argparse
import hashlib
import json
import subprocess
import sys
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def git(*args):
    return subprocess.check_output(["git", "-C", str(ROOT), *args])


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    out = args.out.resolve()
    out.mkdir(parents=True, exist_ok=True)
    version_text = (ROOT / "src/pybynix/__init__.py").read_text(encoding="utf-8")
    import re

    version = re.search(r'__version__ = "([^"]+)"', version_text).group(1)
    names = git("ls-files", "-z").decode().split("\0")
    source = out / f"PyBynix-{version}-source.zip"
    with zipfile.ZipFile(source, "w", zipfile.ZIP_DEFLATED) as archive:
        for name in filter(None, names):
            path = (ROOT / name).resolve()
            if not path.is_relative_to(ROOT) or (ROOT / name).is_symlink():
                raise ValueError("Source input escapes the public checkout")
            if any(
                part in {"private", ".git", "browser-profile", ".venv"}
                for part in path.relative_to(ROOT).parts
            ):
                raise ValueError("Private path in source selection")
            if path.name.startswith(".env") and path.name != ".env.example":
                raise ValueError("Private environment file in source selection")
            archive.writestr("PyBynix/" + name, path.read_bytes())
    subprocess.run([sys.executable, "-m", "build", str(ROOT)], check=True)
    artifacts = [source]
    for path in (ROOT / "dist").glob(f"pybynix-{version}*"):
        target = out / path.name
        if target.resolve() != path.resolve():
            target.write_bytes(path.read_bytes())
        artifacts.append(target)
    sums = "\n".join(f"{hashlib.sha256(p.read_bytes()).hexdigest()}  {p.name}" for p in artifacts)
    (out / f"PyBynix-{version}-SHA256SUMS.txt").write_text(sums + "\n")
    (out / f"PyBynix-{version}-manifest.json").write_text(
        json.dumps(
            {
                "version": version,
                "commit": git("rev-parse", "HEAD").decode().strip(),
                "artifacts": [p.name for p in artifacts],
                "published": False,
            },
            indent=2,
        )
    )
    print("Release artifacts:", ", ".join(p.name for p in artifacts))


if __name__ == "__main__":
    main()
