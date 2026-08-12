#!/usr/bin/env python3
"""Project structure checks.

Three checks, run over the files tracked by git:

  1. Large files      - fails the workflow
  2. File names       - warns
  3. README placeholders - warns

Warnings appear as annotations on the commit or pull request. Only the large
file check fails, because a large file committed to git is difficult to remove
afterwards, while a badly named file is not.

Adjust the settings below to suit the project. To turn a warning into a failure,
set the corresponding FAIL_ON_ constant to True.
"""

from __future__ import annotations

import os
import re
import subprocess
import sys
from pathlib import Path

# ---------------------------------------------------------------- settings --

MAX_FILE_MB = 5

FAIL_ON_LARGE_FILES = True
FAIL_ON_BAD_NAMES = False
FAIL_ON_README_PLACEHOLDERS = False

# Files and folders exempt from the naming check.
NAME_EXEMPT = {
    "README.md",
    "LICENSE",
    "CONTRIBUTING.md",
    "CODE_OF_CONDUCT.md",
    "CITATION.cff",
    "Makefile",
    "Dockerfile",
    "NAMESPACE",
    "DESCRIPTION",
}

# Extensions that are legitimately large and should not fail the size check.
SIZE_EXEMPT_SUFFIXES = {".lock"}

# Extensions that are conventionally capitalised.
CAPITALISED_SUFFIXES = {".R", ".Rmd", ".Rproj", ".Rnw", ".Rd"}

# Text that means a README field has not been filled in.
PLACEHOLDERS = ["TODO:", "PROJECT_NAME"]

GOOD_NAME = re.compile(r"^[a-z0-9._-]+$")

# ------------------------------------------------------------------- output --

problems = {"error": 0, "warning": 0}


def report(level: str, message: str, path: str | None = None, line: int | None = None) -> None:
    """Emit a GitHub Actions annotation, or plain text when run locally."""
    problems[level] += 1
    if os.environ.get("GITHUB_ACTIONS"):
        bits = []
        if path:
            bits.append(f"file={path}")
        if line:
            bits.append(f"line={line}")
        location = "," + ",".join(bits) if bits else ""
        print(f"::{level}{location}::{message}")
    else:
        where = f"{path}: " if path else ""
        print(f"[{level}] {where}{message}")


def tracked_files() -> list[Path]:
    out = subprocess.run(
        ["git", "ls-files", "-z"],
        capture_output=True,
        text=True,
        check=True,
    ).stdout
    return [Path(p) for p in out.split("\0") if p]


# ------------------------------------------------------------------ checks --


def check_sizes(files: list[Path]) -> None:
    limit = MAX_FILE_MB * 1024 * 1024
    for path in files:
        if path.suffix in SIZE_EXEMPT_SUFFIXES or not path.is_file():
            continue
        size = path.stat().st_size
        if size > limit:
            mb = size / 1024 / 1024
            report(
                "error" if FAIL_ON_LARGE_FILES else "warning",
                f"{mb:.1f} MB, over the {MAX_FILE_MB} MB limit. "
                f"Large files belong in shared storage, not in the repository. "
                f"If this was committed by mistake, remove it before merging — "
                f"deleting it in a later commit does not remove it from the history.",
                str(path),
            )


def check_names(files: list[Path]) -> None:
    level = "error" if FAIL_ON_BAD_NAMES else "warning"
    seen_dirs: set[Path] = set()

    for path in files:
        for part in path.parts[:-1]:
            parent = Path(part)
            if parent in seen_dirs:
                continue
            seen_dirs.add(parent)
            if not GOOD_NAME.match(part):
                report(level, f"folder name '{part}' — use lowercase, no spaces", str(path))

        name = path.name
        if name in NAME_EXEMPT or name.startswith("."):
            continue

        # Check the stem only where the extension is conventionally capitalised.
        if path.suffix in CAPITALISED_SUFFIXES:
            name = path.stem

        if not GOOD_NAME.match(name):
            reasons = []
            if " " in name:
                reasons.append("contains a space")
            if name != name.lower():
                reasons.append("contains capitals")
            if not reasons:
                reasons.append("contains characters other than letters, numbers, . _ -")
            report(
                level,
                f"file name '{name}' {', '.join(reasons)}. "
                f"Lowercase, hyphens or underscores, no spaces, dates as YYYY-MM-DD.",
                str(path),
            )


def check_readme() -> None:
    level = "error" if FAIL_ON_README_PLACEHOLDERS else "warning"
    readme = Path("README.md")
    if not readme.exists():
        report(level, "no README.md at the root of the project")
        return

    lines = readme.read_text(encoding="utf-8", errors="replace").splitlines()
    hits = [
        (n, marker)
        for n, line in enumerate(lines, start=1)
        for marker in PLACEHOLDERS
        if marker in line
    ]
    if hits:
        first = hits[0]
        report(
            level,
            f"README.md still has {len(hits)} unfilled placeholder"
            f"{'s' if len(hits) > 1 else ''}. "
            f"Anyone taking this project over reads the README first.",
            "README.md",
            first[0],
        )


# -------------------------------------------------------------------- main --


def main() -> int:
    files = tracked_files()
    check_sizes(files)
    check_names(files)
    check_readme()

    print()
    print(f"Checked {len(files)} tracked files.")
    print(f"{problems['error']} error(s), {problems['warning']} warning(s).")

    return 1 if problems["error"] else 0


if __name__ == "__main__":
    sys.exit(main())
