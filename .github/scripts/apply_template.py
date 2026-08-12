#!/usr/bin/env python3
"""Apply the group project template to repositories that do not have it.

Run from the organisation's .github repository by the apply-template workflow.

For each repository in the organisation that is missing the marker file:

  - empty repository  -> the template is pushed straight to the default branch
  - existing content  -> a pull request is opened adding the missing files only

Nothing already in the repository is overwritten, and no file is deleted.

Only repositories created on or after CREATED_AFTER are considered, so
installing this does not open pull requests across everything that already
exists. Naming a single repository on a manual run overrides the cutoff.

Environment:
  GH_TOKEN       token with repo write access across the organisation
  ORG            organisation login
  TEMPLATE_REPO  owner/name of the template repository
  CREATED_AFTER  date, YYYY-MM-DD; repositories created before it are ignored
  ONLY_REPO      optional, a single repository name to act on
  MAX_REPOS      optional, safety limit per run (default 10)
  DRY_RUN        optional, 'true' to report without changing anything
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
from datetime import date, datetime
from pathlib import Path

MARKER = ".project-structure"

ORG = os.environ["ORG"]
TEMPLATE_REPO = os.environ["TEMPLATE_REPO"]
ONLY_REPO = os.environ.get("ONLY_REPO", "").strip()
CREATED_AFTER = os.environ.get("CREATED_AFTER", "").strip()
MAX_REPOS = int(os.environ.get("MAX_REPOS", "10"))
DRY_RUN = os.environ.get("DRY_RUN", "").lower() == "true"
TOKEN = os.environ["GH_TOKEN"]

BRANCH = "add-project-structure"

PR_BODY = """This adds the group's standard project structure.

Only files that were missing have been added. Nothing has been overwritten or
deleted, so this is safe to merge, and equally safe to close if the structure
does not suit this project.

The guide is at https://github.com/{org}/{template}
""".format(org=ORG, template=TEMPLATE_REPO.split("/")[-1])


def gh(*args: str, check: bool = True, cwd: str | None = None) -> str:
    result = subprocess.run(
        ["gh", *args], capture_output=True, text=True, cwd=cwd
    )
    if check and result.returncode != 0:
        raise RuntimeError(f"gh {' '.join(args)} failed:\n{result.stderr}")
    return result.stdout


def git(*args: str, cwd: str, check: bool = True) -> str:
    result = subprocess.run(
        ["git", *args], capture_output=True, text=True, cwd=cwd
    )
    if check and result.returncode != 0:
        raise RuntimeError(f"git {' '.join(args)} failed:\n{result.stderr}")
    return result.stdout


def cutoff() -> date:
    """The date before which repositories are ignored.

    Defaults to today, so installing this without setting CREATED_AFTER affects
    only repositories created from now on.
    """
    if not CREATED_AFTER:
        return date.today()
    try:
        return date.fromisoformat(CREATED_AFTER)
    except ValueError:
        raise SystemExit(
            f"CREATED_AFTER is '{CREATED_AFTER}'. It must be a date, as YYYY-MM-DD."
        )


def list_repos() -> list[dict]:
    """Repositories in the organisation that this workflow should consider."""
    raw = gh(
        "repo",
        "list",
        ORG,
        "--limit",
        "1000",
        "--no-archived",
        "--json",
        "name,isEmpty,defaultBranchRef,isFork,createdAt",
    )
    repos = json.loads(raw)

    skip = {TEMPLATE_REPO.split("/")[-1], ".github"}
    repos = [r for r in repos if r["name"] not in skip and not r["isFork"]]

    # Naming a repository explicitly is a deliberate act, so the cutoff does
    # not apply to it.
    if ONLY_REPO:
        return [r for r in repos if r["name"] == ONLY_REPO]

    since = cutoff()
    recent = []
    for repo in repos:
        created = datetime.fromisoformat(
            repo["createdAt"].replace("Z", "+00:00")
        ).date()
        if created >= since:
            recent.append(repo)

    print(f"{len(recent)} of {len(repos)} repositories created on or after {since}.")
    return recent


def has_marker(repo: str) -> bool:
    result = subprocess.run(
        ["gh", "api", f"repos/{ORG}/{repo}/contents/{MARKER}"],
        capture_output=True,
        text=True,
    )
    return result.returncode == 0


def copy_missing(src: Path, dst: Path) -> list[str]:
    """Copy files from src to dst where dst does not already have them."""
    added: list[str] = []
    for path in sorted(src.rglob("*")):
        if not path.is_file():
            continue
        rel = path.relative_to(src)
        if rel.parts[0] == ".git":
            continue
        target = dst / rel
        if target.exists():
            continue
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(path, target)
        added.append(str(rel))
    return added


def process(repo: dict, template: Path) -> str:
    name = repo["name"]

    if has_marker(name):
        return f"{name}: already has the structure, skipped"

    if DRY_RUN:
        return f"{name}: would add the structure (dry run)"

    url = f"https://x-access-token:{TOKEN}@github.com/{ORG}/{name}.git"

    with tempfile.TemporaryDirectory() as tmp:
        work = Path(tmp) / name

        if repo["isEmpty"]:
            work.mkdir(parents=True)
            git("init", "-q", cwd=str(work))
            git("checkout", "-q", "-b", "main", cwd=str(work))
            added = copy_missing(template, work)
            (work / MARKER).write_text("Applied by the apply-template workflow.\n")
            git("add", "-A", cwd=str(work))
            git("commit", "-qm", "Add standard project structure", cwd=str(work))
            git("remote", "add", "origin", url, cwd=str(work))
            git("push", "-q", "-u", "origin", "main", cwd=str(work))
            return f"{name}: structure pushed to main ({len(added)} files)"

        git("clone", "-q", "--depth", "1", url, str(work), cwd=tmp)
        git("checkout", "-q", "-b", BRANCH, cwd=str(work))
        added = copy_missing(template, work)
        (work / MARKER).write_text("Applied by the apply-template workflow.\n")

        if not added:
            return f"{name}: nothing missing, skipped"

        git("add", "-A", cwd=str(work))
        git("commit", "-qm", "Add standard project structure", cwd=str(work))
        git("push", "-q", "-u", "origin", BRANCH, cwd=str(work))

        gh(
            "pr",
            "create",
            "--repo",
            f"{ORG}/{name}",
            "--head",
            BRANCH,
            "--title",
            "Add standard project structure",
            "--body",
            PR_BODY,
            cwd=str(work),
        )
        return f"{name}: pull request opened ({len(added)} files)"


def main() -> int:
    repos = list_repos()
    if not repos:
        print("No repositories to consider.")
        return 0

    with tempfile.TemporaryDirectory() as tmp:
        template = Path(tmp) / "template"
        subprocess.run(
            [
                "git",
                "clone",
                "-q",
                "--depth",
                "1",
                f"https://x-access-token:{TOKEN}@github.com/{TEMPLATE_REPO}.git",
                str(template),
            ],
            check=True,
        )
        shutil.rmtree(template / ".git")

        done = 0
        for repo in repos:
            if done >= MAX_REPOS:
                print(f"Reached the limit of {MAX_REPOS} repositories for this run.")
                break
            try:
                message = process(repo, template)
            except Exception as exc:  # keep going through the rest
                message = f"{repo['name']}: failed — {exc}"
            print(message)
            if "skipped" not in message:
                done += 1

    return 0


if __name__ == "__main__":
    sys.exit(main())
