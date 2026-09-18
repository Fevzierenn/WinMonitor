---
name: project-structure
description: Maps a codebase into a PROJECT_STRUCTURE.md at the repo root — annotated directory tree, module breakdown, key flows, and Mermaid diagrams showing how classes/components wire together with their fields — and refreshes that document when a newer version of the code arrives, archiving the old copy and appending a dated changelog of what was added, removed, moved, or changed. Use this whenever the user asks to document, map, explain, or diagram a project's structure or architecture, asks "what does this codebase look like", hands over a new version/drop/release of code and wants the structure file brought up to date, or mentions PROJECT_STRUCTURE.md, a structure doc, or comparing a new codebase version against an existing one — even if they don't name the file. Works on any language or stack.
---

# Project Structure Documentation

Produce and maintain `PROJECT_STRUCTURE.md`: the document a new developer reads first to
understand where things live, how the pieces are wired, and what changed between versions.

Two jobs live here, and the first thing to do is work out which one you're on:

- **Create** — no structure doc exists yet. Survey the codebase, read it, write the doc.
- **Update** — a doc already exists and the code has moved on. Archive the old copy,
  diff the code against what the doc describes, rewrite it, and record what changed.

Check with `ls PROJECT_STRUCTURE.md docs/PROJECT_STRUCTURE.md 2>/dev/null` and a quick search
for any similar file (`ARCHITECTURE.md`, `STRUCTURE.md`). If one turns up under a different
name, use that file rather than starting a second, competing document — two structure docs
that disagree are worse than one that's slightly stale.

## Step 1 — Survey before reading

Run the bundled survey first. It walks the tree honouring `.gitignore`, counts files and
lines, finds manifests and entry points, and prints the tree:

```bash
python <skill-path>/scripts/survey.py <repo-root> --max-depth 4 --json /tmp/survey-new.json
```

Do this before opening files. It tells you how big the job is, which files carry the weight,
and whether git history is available — all of which change how you spend the rest of the run.
If the script can't run (no Python), fall back to `git ls-files` or `find`, but you'll be
working with less information.

## Step 2 — Let size set the depth

The survey prints a tier. It's a budget, not a rule: a 40-file project with genuinely intricate
wiring deserves more than a 40-file CRUD app. Use judgement, but start here.

| Tier | Size | Tree depth | Diagrams | Module detail |
|---|---|---|---|---|
| small | ≤ 60 files | full tree, every file | 1 overview + 1 detailed class/flow diagram | every module gets a paragraph |
| medium | 61–400 files | depth 3–4, collapse leaf dirs with counts | 1 overview + one diagram per major module (3–6) | major modules get a paragraph, the rest a line |
| large | > 400 files | depth 2–3, top-level and subsystem dirs only | 1 overview + diagrams for the 3–5 modules carrying the real logic | subsystems get a paragraph, leaf folders a line in the tree |

The failure mode on a large codebase is a doc that lists everything and explains nothing.
When you have to choose, drop breadth and keep the explanation of how the core works.

## Step 3 — Read the code that carries the meaning

You cannot describe how classes wire together from filenames. Read, at minimum:

- every manifest the survey found (stack, dependencies, scripts, entry commands)
- the entry points (`main`, `index`, `app`, `Program.cs`, `manage.py`, route registration, DI container setup)
- the largest files in the survey's list — size usually tracks importance
- one representative file per layer (a controller, a service, a model/entity, a repository)
  so you can describe the layering honestly instead of assuming the usual shape

Follow imports outward from the entry points until you can answer: what happens when a request
(or job, or command) comes in, and which pieces does it touch on the way? That answer is the
spine of the document.

When something stays unclear after reading, say so in the doc with a short
`> **Unverified:** …` note. A flagged gap is useful; a confident guess that turns out wrong
poisons trust in the whole file.

## Step 4 — Diagrams

Diagrams are the part people actually look at, so they get real effort. Use Mermaid in fenced
` ```mermaid ` blocks — it renders on GitHub, in most editors, and stays diffable as text.

Every structure doc carries a **system overview** (`flowchart`) showing the major modules and
the direction data moves between them. Beyond that, pick by what the project is:

- backend / OO code → `classDiagram` with **fields and their types**, methods, and the
  relations between classes (inheritance, composition, "uses")
- data model → `erDiagram` with tables, columns, and cardinality
- a request or job path worth explaining → `sequenceDiagram`
- frontend → `flowchart` of the component tree with props/state noted on the nodes
- pipelines, state machines, build flows → `flowchart` or `stateDiagram-v2`

Readability rules that matter more than completeness: keep a diagram under ~15 nodes, and when
a module has more, split it into per-module diagrams rather than shipping a hairball. Label
every edge with what actually flows across it (`calls`, `publishes OrderCreated`, `reads`),
because an unlabelled arrow tells the reader nothing they couldn't have guessed.

`references/diagrams.md` has worked Mermaid examples for each of these, plus the syntax traps
that break rendering. Read it before writing your first diagram if you're not certain of the
syntax — a diagram that fails to render is worse than no diagram.

## Step 5 — Write the document

Use this structure. Sections that don't apply get dropped, not padded.

```markdown
# Project Structure — <project name>

> Generated <date> · <N> files · <stack summary> · basis: <commit sha | folder snapshot>

## Overview
Two or three sentences: what this project does and what kind of system it is.

## Tech stack
Languages, frameworks, database, build and run commands, external services.

## Directory tree
<tree at the depth the tier allows, each significant entry annotated with its purpose>

## Modules
### <module name>  `path/to/module`
What it's responsible for, what it depends on, what depends on it, and the one or two
files inside it worth opening first.

## Architecture diagrams
### System overview
<mermaid flowchart>
### <module> — class structure
<mermaid classDiagram with fields>
### Data model
<mermaid erDiagram>

## Key flows
Numbered walkthrough of the 1–3 paths that matter, naming the real files and functions
at each hop, so a reader can follow along in the editor.

## Conventions
Naming, where new code of each kind belongs, testing and config patterns.

## Version history
<newest first; see the update section>

<!-- project-structure-meta
generated: <ISO timestamp>
basis: <git <sha> (<branch>) | snapshot of <path>>
files: <N>
tier: <small|medium|large>
-->
```

The HTML comment at the bottom is what makes the next update accurate rather than guesswork —
it records exactly which version of the code the document describes. Keep it there, and keep
it truthful.

Also save the survey JSON as the machine-readable companion:

```bash
mkdir -p docs/structure-history
cp /tmp/survey-new.json docs/structure-history/survey-<YYYY-MM-DD>.json
```

That file list is what lets a later run detect a renamed or deleted file exactly, even when
there's no git history to lean on.

## Updating when a new version arrives

The user's existing document may contain hand-written notes that took real thought, and an
earlier version of the doc is sometimes the only record of how the system used to look. So the
old copy is never destroyed and never silently overwritten.

**1. Archive first, before touching anything.**

```bash
mkdir -p docs/structure-history
cp PROJECT_STRUCTURE.md "docs/structure-history/PROJECT_STRUCTURE_$(date +%Y-%m-%d).md"
```

If that filename already exists, add a `_2`, `_3` suffix rather than overwriting the archive.

**2. Establish what you're comparing against.** Read the meta block at the bottom of the
existing doc, then pick the strongest basis available:

- *Same repo with history* — `git diff --stat <recorded-sha> HEAD` and
  `git diff --name-status <recorded-sha> HEAD` give you added/deleted/renamed exactly.
  `git log --oneline <sha>..HEAD` tells you the intent behind the changes.
- *A separate drop, zip, or folder with no shared history* — run the survey on the new tree and
  diff its file list against the most recent `docs/structure-history/survey-*.json`.
  `python -c "import json,sys; a=set(json.load(open(sys.argv[1]))['files']); b=set(json.load(open(sys.argv[2]))['files']); print('ADDED:'); [print(' +',p) for p in sorted(b-a)]; print('REMOVED:'); [print(' -',p) for p in sorted(a-b)]" old.json new.json`
- *Neither available* — compare the new survey's tree against the tree written in the existing
  doc. Less exact, so say in the changelog that the comparison was made against the document
  rather than a recorded snapshot.

A path that disappears and a similar one that appears is usually a move, not a delete plus an
add. Check the content before reporting it as removal — a changelog that claims a module was
deleted when it was renamed sends people looking for a problem that doesn't exist.

**3. Rewrite the document** against the new code, carrying forward everything from the old
version that's still true — especially prose the user wrote themselves. Anything inside
`<!-- keep -->` … `<!-- /keep -->` markers is transferred verbatim, no exceptions. If a
hand-written note now contradicts the code, keep the note and flag it rather than deleting it:

```markdown
> **Note (was true as of <old version>):** <their text>
> This no longer matches the code — <what changed>.
```

Re-generate the diagrams from the new code rather than patching the old Mermaid by hand; stale
edges are the most common way these documents start lying.

**4. Add the changelog entry** at the top of `## Version history`:

```markdown
### <YYYY-MM-DD> — <version or commit range>
*Compared against: <git sha | survey-<date>.json | previous document>*

**Added**
- `path/to/new_module/` — what it does and why it appeared

**Removed**
- `path/old_thing.py` — what replaced it, if anything

**Moved / renamed**
- `a/old.py` → `b/new.py`

**Changed**
- `OrderService` — <what changed about its responsibilities, fields, or wiring>

**Diagram impact**
- System overview: <what moved>; Order module class diagram: regenerated.
```

Keep the ten most recent entries in the live document and let the archived copies hold the rest,
so the file stays readable.

## Closing the loop with the user

End by telling them, in a few lines: where the doc is, where the old copy was archived, the
headline of what changed (or what the project turned out to be, on a first run), and anything
you flagged as unverified. That last part matters — they're the one who can resolve it.
