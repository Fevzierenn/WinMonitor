#!/usr/bin/env python3
"""Survey a codebase: size, tree, manifests, entry points.

Language-agnostic. Run this before writing or updating PROJECT_STRUCTURE.md so the
size tier, the tree, and the list of things worth reading are facts rather than guesses.

Usage:
    python survey.py <root> [--max-depth 4] [--json survey.json] [--tree-only]

Prints a human-readable report to stdout and optionally writes the raw data as JSON.
"""

import argparse
import json
import os
import subprocess
import sys
from collections import defaultdict

# Directories that are almost never part of the story a structure doc tells.
SKIP_DIRS = {
    ".git", ".hg", ".svn", ".idea", ".vscode", ".vs", "__pycache__", ".pytest_cache",
    ".mypy_cache", ".ruff_cache", "node_modules", "bower_components", "vendor",
    "venv", ".venv", "env", ".env.d", "dist", "build", "out", "target", "bin", "obj",
    ".next", ".nuxt", ".svelte-kit", ".parcel-cache", ".turbo", ".gradle", ".terraform",
    "coverage", ".nyc_output", "Pods", "DerivedData", ".dart_tool", ".expo",
}

SKIP_FILE_SUFFIXES = (
    ".lock", ".log", ".map", ".min.js", ".min.css", ".pyc", ".pyo", ".class", ".o",
    ".so", ".dll", ".dylib", ".exe", ".pdb", ".zip", ".tar", ".gz", ".7z", ".rar",
    ".png", ".jpg", ".jpeg", ".gif", ".ico", ".svg", ".webp", ".mp4", ".mp3", ".pdf",
    ".woff", ".woff2", ".ttf", ".eot", ".DS_Store",
)

# Files that identify the stack. Finding these is usually the fastest way to know
# what kind of project this is.
MANIFESTS = {
    "package.json", "pnpm-workspace.yaml", "tsconfig.json", "requirements.txt",
    "pyproject.toml", "setup.py", "Pipfile", "poetry.lock", "go.mod", "Cargo.toml",
    "pom.xml", "build.gradle", "build.gradle.kts", "Gemfile", "composer.json",
    "Dockerfile", "docker-compose.yml", "docker-compose.yaml", "Makefile",
    "CMakeLists.txt", "pubspec.yaml", "mix.exs", "deno.json", "angular.json",
    "next.config.js", "vite.config.ts", "vite.config.js", "nx.json", "turbo.json",
    "Descriptor", "AxModel", ".csproj", ".sln", ".fsproj", "Chart.yaml",
    "serverless.yml", "terraform.tf", "main.tf", "alembic.ini", "manage.py",
}

ENTRY_HINTS = (
    "main", "index", "app", "server", "program", "startup", "bootstrap", "cli",
    "__main__", "manage", "run", "wsgi", "asgi",
)

CODE_EXTS = {
    ".py", ".js", ".jsx", ".ts", ".tsx", ".mjs", ".cjs", ".java", ".kt", ".kts",
    ".cs", ".fs", ".vb", ".go", ".rs", ".rb", ".php", ".swift", ".m", ".mm",
    ".c", ".h", ".cpp", ".cc", ".hpp", ".scala", ".clj", ".ex", ".exs", ".dart",
    ".lua", ".pl", ".r", ".jl", ".sh", ".bash", ".ps1", ".sql", ".vue", ".svelte",
    ".xpp", ".xml", ".html", ".css", ".scss", ".less", ".graphql", ".proto",
    ".md", ".json", ".yml", ".yaml", ".toml", ".ini", ".cfg",
}


def git_tracked(root):
    """Return the set of git-tracked paths, or None when this isn't a git repo.

    Using git's own view of the tree is the cheapest correct way to honour
    .gitignore without reimplementing its matching rules.
    """
    try:
        out = subprocess.run(
            ["git", "-C", root, "ls-files"],
            capture_output=True, text=True, timeout=30,
        )
        if out.returncode != 0:
            return None
        files = {line.strip() for line in out.stdout.splitlines() if line.strip()}
        return files or None
    except (OSError, subprocess.SubprocessError):
        return None


def git_meta(root):
    def run(args):
        try:
            out = subprocess.run(["git", "-C", root] + args,
                                 capture_output=True, text=True, timeout=15)
            return out.stdout.strip() if out.returncode == 0 else None
        except (OSError, subprocess.SubprocessError):
            return None

    head = run(["rev-parse", "HEAD"])
    if not head:
        return None
    return {
        "head": head,
        "short": head[:10],
        "branch": run(["rev-parse", "--abbrev-ref", "HEAD"]),
        "date": run(["log", "-1", "--format=%cI"]),
        "subject": run(["log", "-1", "--format=%s"]),
    }


def count_lines(path):
    try:
        with open(path, "rb") as fh:
            return sum(1 for _ in fh)
    except OSError:
        return 0


def skip_file(name):
    return name.startswith(".") and name not in {".env.example", ".gitignore"} \
        or name.endswith(SKIP_FILE_SUFFIXES)


def survey(root, max_depth):
    root = os.path.abspath(root)
    tracked = git_tracked(root)
    files = []
    by_ext = defaultdict(lambda: {"files": 0, "lines": 0})
    by_topdir = defaultdict(lambda: {"files": 0, "lines": 0})
    manifests, entry_points = [], []

    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = sorted(d for d in dirnames if d not in SKIP_DIRS)
        rel_dir = os.path.relpath(dirpath, root)
        rel_dir = "" if rel_dir == "." else rel_dir
        for name in sorted(filenames):
            rel = os.path.join(rel_dir, name) if rel_dir else name
            rel = rel.replace(os.sep, "/")
            if tracked is not None and rel not in tracked:
                continue
            if tracked is None and skip_file(name):
                continue
            full = os.path.join(dirpath, name)
            ext = os.path.splitext(name)[1].lower()
            lines = count_lines(full) if ext in CODE_EXTS else 0
            size = os.path.getsize(full) if os.path.exists(full) else 0
            files.append({"path": rel, "ext": ext, "lines": lines, "bytes": size})
            by_ext[ext or "(none)"]["files"] += 1
            by_ext[ext or "(none)"]["lines"] += lines
            top = rel.split("/")[0] if "/" in rel else "(root)"
            by_topdir[top]["files"] += 1
            by_topdir[top]["lines"] += lines
            if name in MANIFESTS or ext in {".csproj", ".sln", ".fsproj"}:
                manifests.append(rel)
            stem = os.path.splitext(name)[0].lower()
            if stem in ENTRY_HINTS and ext in CODE_EXTS:
                entry_points.append(rel)

    total_lines = sum(f["lines"] for f in files)
    tier = "small" if len(files) <= 60 else "medium" if len(files) <= 400 else "large"

    return {
        "root": root,
        "git": git_meta(root),
        "totals": {"files": len(files), "code_lines": total_lines, "tier": tier},
        "by_extension": dict(sorted(by_ext.items(),
                                    key=lambda kv: -kv[1]["files"])),
        "by_top_dir": dict(sorted(by_topdir.items(),
                                  key=lambda kv: -kv[1]["files"])),
        "manifests": manifests,
        "entry_points": entry_points,
        "largest_files": sorted(files, key=lambda f: -f["lines"])[:25],
        "tree": build_tree(files, max_depth),
        "files": [f["path"] for f in files],
    }


def build_tree(files, max_depth):
    """ASCII tree pruned at max_depth; deeper levels collapse into a file count."""
    root = {}
    for f in files:
        node = root
        for part in f["path"].split("/"):
            node = node.setdefault(part, {})

    lines = []

    def count_descendants(node):
        n = 0
        for child in node.values():
            n += 1 if not child else count_descendants(child)
        return n

    def walk(node, prefix, depth):
        items = sorted(node.items(), key=lambda kv: (not kv[1], kv[0].lower()))
        for i, (name, child) in enumerate(items):
            last = i == len(items) - 1
            branch = "└── " if last else "├── "
            is_dir = bool(child)
            if is_dir and depth >= max_depth:
                lines.append(f"{prefix}{branch}{name}/  ({count_descendants(child)} files)")
                continue
            lines.append(f"{prefix}{branch}{name}{'/' if is_dir else ''}")
            if is_dir:
                walk(child, prefix + ("    " if last else "│   "), depth + 1)

    walk(root, "", 1)
    return "\n".join(lines)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("root")
    ap.add_argument("--max-depth", type=int, default=4)
    ap.add_argument("--json", help="write raw survey data here")
    ap.add_argument("--tree-only", action="store_true")
    args = ap.parse_args()

    data = survey(args.root, args.max_depth)

    if args.json:
        with open(args.json, "w") as fh:
            json.dump(data, fh, indent=2)

    if args.tree_only:
        print(data["tree"])
        return

    t = data["totals"]
    print(f"ROOT: {data['root']}")
    if data["git"]:
        g = data["git"]
        print(f"GIT: {g['short']} on {g['branch']} ({g['date']}) — {g['subject']}")
    else:
        print("GIT: not a git repository (compare against the existing doc instead)")
    print(f"SIZE: {t['files']} files, {t['code_lines']} code lines → tier: {t['tier']}")

    print("\nTOP-LEVEL DIRECTORIES")
    for name, s in list(data["by_top_dir"].items())[:20]:
        print(f"  {name:<28} {s['files']:>5} files  {s['lines']:>7} lines")

    print("\nEXTENSIONS")
    for ext, s in list(data["by_extension"].items())[:15]:
        print(f"  {ext:<12} {s['files']:>5} files  {s['lines']:>7} lines")

    print("\nMANIFESTS / STACK MARKERS")
    for m in data["manifests"][:30] or ["  (none found)"]:
        print(f"  {m}")

    print("\nLIKELY ENTRY POINTS")
    for e in data["entry_points"][:20] or ["  (none matched by name)"]:
        print(f"  {e}")

    print("\nLARGEST FILES (read these first)")
    for f in data["largest_files"][:15]:
        print(f"  {f['lines']:>6} lines  {f['path']}")

    print("\nTREE")
    print(data["tree"])


if __name__ == "__main__":
    sys.exit(main())
