#!/usr/bin/env python3
"""skillbox — a toolbox for your AI coding-agent skills.

One SKILL.md folder, symlink-fanned into every runtime so it works everywhere.

Commands:
  skillbox list                      installed skills with their resolved source
  skillbox new <name> [--repo ID]    scaffold a new skill (default: your own repo) and link it everywhere
  skillbox add <name> [--source ID]  symlink an existing source skill into every runtime root
  skillbox rm <name>                 remove the skill's symlinks (source folder untouched)
  skillbox promote <name> --to ID    move the skill to another source repo (reversible) and relink
  skillbox promote <name> --to org   emit a plugin manifest + print the DRAFT marketplace PR (never sends)
  skillbox source add <id> <path>    add a local source repo (e.g. a teammate's clone); `source rm <id>` reverses
  skillbox diff <name>               the skill's uncommitted git diff (if its source repo has a .git)
  skillbox log <name>                the skill's commit history (recent versions of the folder)
  skillbox doctor [--json]           drift/parity check across runtimes: BROKEN/MISSING/DRIFTED/SHADOWED
  skillbox sync [--no-pull]          git pull each source + relink winners + prune dead links
  skillbox update [--dry-run]        git pull each source; show SKILL.md diffs first
  skillbox ui [--port N] [--render]  localhost management GUI (127.0.0.1); --render prints one page and exits

Manifest: ~/.skillbox/skills.toml  (override with $SKILLBOX_MANIFEST for tests).

Tier is never a stored flag — it is which source repo holds the folder. `new`
lands in your own repo by default; `promote` (later) is the one deliberate
sharing verb. Codex reads ~/.agents/skills; ~/.codex/skills is Cursor-compat only.
"""
import os, sys, json, re, hashlib, subprocess
from pathlib import Path
try:
    import tomllib  # Python 3.11+ stdlib
except ModuleNotFoundError:
    try:
        import tomli as tomllib  # older Python: `pip install tomli`
    except ModuleNotFoundError:
        tomllib = None

# Config is overridable via $SKILLBOX_MANIFEST so the test harness can point at a
# hermetic sandbox manifest (with fake roots + fake source repos) and never touch
# the real fleet. The sources clone dir derives from its parent.
MANIFEST = Path(os.environ.get("SKILLBOX_MANIFEST", str(Path.home() / ".skillbox" / "skills.toml")))
CONFIG_DIR = MANIFEST.parent

# `new` puts a skill in your own repo by default (first-wins overlay means a
# shared same-name skill still wins unless you give yours a unique name, e.g. a
# `-mine` suffix). Override the default source id via $SKILLBOX_DEFAULT_SOURCE.
DEFAULT_NEW_SOURCE = os.environ.get("SKILLBOX_DEFAULT_SOURCE", "personal")

# This project's home, shown on the GUI About page (override via $SKILLBOX_REPO_URL).
REPO_URL = os.environ.get("SKILLBOX_REPO_URL", "https://github.com/leojkwan/skillbox")

# Optional org plugin marketplace for `promote --to org` (the Claude Code plugin
# marketplace convention). Set $SKILLBOX_ORG_REPO to your "owner/repo"; unset
# disables the org off-ramp. DRAFT PR only — never auto-sent.
ORG_REPO = os.environ.get("SKILLBOX_ORG_REPO", "")

# A skill name becomes BOTH a directory under a source repo and a symlink leaf in
# each runtime root, so it must be one safe path segment — never something that
# escapes via "/", "..", or an absolute path (Path("/src") / "/etc/x" == "/etc/x").
# Shape mirrors the marketplace's kebab-case plugin names; the guard is the security wall.
_VALID_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")


def require_name(name):
    if not name or len(name) > 64 or ".." in name or not _VALID_NAME.match(name):
        shown = (name[:40] + "…") if name and len(name) > 40 else name
        sys.exit(f"invalid skill name: {shown!r} — letters/digits/._- only (≤64 chars), "
                 "no path separators, no '..'")
    return name


def _load_manifest():
    if tomllib is None:
        sys.exit("skillbox needs TOML support — use Python 3.11+ (stdlib tomllib) "
                 "or `pip install tomli` on older Python.")
    return tomllib.loads(MANIFEST.read_text(encoding="utf-8"))


def _toml_string(s):
    return json.dumps(str(s))


def load():
    try:
        m = _load_manifest()
        roots = {k: Path(os.path.expanduser(v)) for k, v in m["roots"].items()}
        sources = []
        for sid, s in m.get("sources", {}).items():
            # skillbox sources are LOCAL paths only — your repos + any repo already
            # on disk (incl. a teammate's clone).
            if "path" not in s:
                continue
            sources.append({
                "id": sid, "path": Path(os.path.expanduser(s["path"])),
                "priority": s.get("priority", 99), "single_skill": s.get("single_skill"),
            })
        sources.sort(key=lambda s: s["priority"])
    except ((tomllib.TOMLDecodeError if tomllib else ValueError), json.JSONDecodeError) as e:
        sys.exit(f"malformed manifest {MANIFEST}: {e}")
    except KeyError:
        sys.exit(f"manifest {MANIFEST} has no [roots] table — see skills.toml.example")
    except (AttributeError, TypeError):
        sys.exit(f"malformed manifest {MANIFEST}: [roots] and each [sources.*] must be "
                 "tables with string paths + numeric priorities — see skills.toml.example")
    return roots, sources


def source_skills(src):
    if src["single_skill"]:
        return {src["single_skill"]: src["path"]} if (src["path"] / "SKILL.md").exists() else {}
    out = {}
    if src["path"].is_dir():
        for d in src["path"].iterdir():
            if (d / "SKILL.md").exists():
                out[d.name] = d
    return out


def resolve(skill, sources, prefer=None):
    for src in sources:
        if prefer and src["id"] != prefer:
            continue
        skills = source_skills(src)
        if skill in skills:
            return src, skills[skill]
    return None, None


def resolve_plan(sources):
    """name -> (winning_src, path); collisions {name: [src ids in precedence order]}.

    First-wins: earlier source in precedence order wins a name collision; a lower
    source can still contribute a name no higher source defines."""
    plan, collisions = {}, {}
    for src in sources:  # already sorted by priority
        for name, path in source_skills(src).items():
            collisions.setdefault(name, []).append(src["id"])
            if name not in plan:
                plan[name] = (src, path)
    collisions = {n: owners for n, owners in collisions.items() if len(owners) > 1}
    return plan, collisions


def collision_map(sources):
    _, collisions = resolve_plan(sources)
    return collisions


def git_root(path):
    d = path
    while d != d.parent and not (d / ".git").exists():
        d = d.parent
    return d if (d / ".git").exists() else None


# ── mount helpers ───────────────────────────────────────────────────────────

def link_one(roots, name, path, quiet=False):
    """Idempotently symlink name -> absolute source path into every root.
    Relinks a drifted skillbox-owned link; refuses to clobber a real file/dir."""
    linked = relinked = 0
    target = str(path)
    for rname, root in roots.items():
        if not root.is_dir():
            continue
        dst = root / name
        if dst.is_symlink():
            # normalize trailing slash on both sides (old captain sync.sh wrote
            # targets with a trailing slash; a raw compare would relink needlessly)
            cur = os.readlink(dst)
            if cur.rstrip("/") == target.rstrip("/"):
                continue
            dst.unlink()
            dst.symlink_to(path)
            relinked += 1
            if not quiet:
                # show the replaced target — surfaces an adopted foreign/drifted link
                print(f"relinked {rname}/{name}: replaced {cur} -> {path}")
        elif dst.exists():
            if not quiet:
                print(f"skip {rname}/{name}: real file/dir present (not skillbox-owned)")
        else:
            dst.symlink_to(path)
            linked += 1
            if not quiet:
                print(f"linked {rname}/{name} -> {path}")
    return linked, relinked


def prune_dangling(roots, quiet=False):
    cleaned = 0
    for rname, root in roots.items():
        if not root.is_dir():
            continue
        for link in sorted(root.iterdir()):
            if link.is_symlink() and not link.exists():
                target = os.readlink(link)
                # Only prune a genuinely-dead leaf (source dir present, skill folder
                # gone). If the target's parent dir is absent the whole source just
                # blinked out (unmounted / mid-move) — pruning then would silently
                # unlink every skill of that source and report a false-clean fleet.
                if not Path(target).parent.exists():
                    if not quiet:
                        print(f"keep {rname}/{link.name}: source absent, not pruned (transient)")
                    continue
                if not quiet:
                    print(f"pruned {rname}/{link.name} (dangling -> {target})")
                link.unlink()
                cleaned += 1
    return cleaned


# ── commands ──────────────────────────────────────────────────────────────────

SKILL_TEMPLATE = """---
name: {name}
description: TODO — one-line description of what this skill does and when to use it
---

# {name}

## Purpose

TODO — describe what this skill does in 1-3 sentences.

## When to use

TODO — explicit trigger phrases and contexts.

## How it works

TODO — section-by-section explanation.
"""


def cmd_new(roots, sources, name, repo=None):
    name = require_name(name)
    repo = repo or DEFAULT_NEW_SOURCE
    src = next((s for s in sources if s["id"] == repo), None)
    if not src:
        sys.exit(f"unknown --repo {repo}; known: {', '.join(s['id'] for s in sources)}")
    if src.get("single_skill"):
        sys.exit(f"--repo {repo} is a single-skill source, not scaffoldable")
    existing_src, _ = resolve(name, sources)
    if existing_src:
        sys.exit(f"skill '{name}' already exists in source '{existing_src['id']}'. "
                 f"Use a different name (or '{name}-mine' to privately override a shared skill).")
    skill_dir = src["path"] / name
    if skill_dir.exists():  # clean refusal — never let mkdir raise a traceback
        sys.exit(f"path already exists: {skill_dir}")
    skill_dir.mkdir(parents=True, exist_ok=False)
    (skill_dir / "SKILL.md").write_text(SKILL_TEMPLATE.format(name=name))
    print(f"created {skill_dir}/SKILL.md  (source: {repo})")
    linked, relinked = link_one(roots, name, skill_dir)
    print(f"mounted into {linked} runtime root(s)" + (f", relinked {relinked}" if relinked else ""))


def _git_author(path):
    """Best-effort (name, email) from the source repo's git config, for the
    plugin author field — derived, never fabricated; '' when unset."""
    root = git_root(path)
    def cfg(key):
        if not root:
            return ""
        r = subprocess.run(["git", "-C", str(root), "config", key],
                           capture_output=True, text=True)
        return r.stdout.strip()
    return cfg("user.name"), cfg("user.email")


def cmd_promote_org(sources, name):
    """Org-tier publish OFF-RAMP (not a folder move): emit a Claude-Code plugin
    manifest into the skill's own folder and print the marketplace registration
    entry + DRAFT-PR command for your org marketplace ($SKILLBOX_ORG_REPO).
    skillbox NEVER opens the PR — you review and run it yourself."""
    if not ORG_REPO:
        sys.exit("no org marketplace configured — set $SKILLBOX_ORG_REPO to your "
                 "plugin marketplace repo (e.g. your-org/skill-marketplace)")
    src, path = resolve(name, sources)
    if not src:
        sys.exit(f"not found: {name}")
    desc = skill_description(path) or f"TODO — one-line description of {name}"
    cp = path / ".claude-plugin"
    cp.mkdir(exist_ok=True)
    manifest = cp / "plugin.json"
    pj = {"name": name, "version": "0.1.0", "description": desc}
    if manifest.exists():  # idempotent: keep an already-chosen version
        try:
            prev = json.loads(manifest.read_text())
            if isinstance(prev, dict):
                pj["version"] = prev.get("version", pj["version"])
        except (OSError, ValueError):
            pass
    aname, aemail = _git_author(path)
    if aname or aemail:
        pj["author"] = {"name": aname or "TODO", "email": aemail or "you@example.com"}
    manifest.write_text(json.dumps(pj, indent=2) + "\n")
    print(f"wrote {manifest}  (Claude-Code plugin manifest — folder NOT moved)")
    entry = {"name": name, "description": desc, "version": pj["version"],
             "source": f"./plugins/{name}", "category": "productivity"}
    if "author" in pj:
        entry["author"] = pj["author"]
    print(f"\nmarketplace.json entry — add to plugins[] in {ORG_REPO}:")
    print(json.dumps(entry, indent=2))
    print(f"\nDRAFT PR into {ORG_REPO} (review, then run yourself — skillbox never sends):")
    print(f"  cp -R {path} <marketplace-checkout>/plugins/{name}    # then add the entry above")
    print(f"  gh pr create --draft --repo {ORG_REPO} --title \"add plugin: {name}\"")
    print("gated: opening the PR needs an explicit per-PR go — no reviewers, no @-mentions.")


def cmd_promote(roots, sources, name, to_id):
    """The one deliberate sharing verb: move a skill's folder to another source
    repo (e.g. personal -> team) and relink. Reversible (promote back). Does not
    auto-commit the source repos — sharing stays a deliberate, reviewable step.
    `--to org` is the org-tier publish off-ramp (see cmd_promote_org)."""
    name = require_name(name)
    if to_id == "org":
        return cmd_promote_org(sources, name)
    if not to_id:
        sys.exit("usage: skillbox promote <name> --to <source-id>|org")
    if name in collision_map(sources):
        owners = collision_map(sources)[name]
        sys.exit(f"'{name}' is shadowed across sources {owners}; resolve the collision "
                 f"first (e.g. rename one '{name}-mine') — promoting a colliding name "
                 "would orphan a copy and misdirect the reverse.")
    src, path = resolve(name, sources)
    if not src:
        sys.exit(f"not found: {name}")
    if src.get("single_skill"):
        sys.exit(f"'{name}' lives in single-skill source '{src['id']}' whose path IS the "
                 "repo root — promote moves a skill folder, not a whole repo. Copy it manually.")
    tgt = next((s for s in sources if s["id"] == to_id), None)
    if not tgt:
        sys.exit(f"unknown --to {to_id}; known: {', '.join(s['id'] for s in sources)}")
    if tgt.get("single_skill"):
        sys.exit(f"--to {to_id} is a single-skill source, not a movable target")
    if tgt["id"] == src["id"]:
        sys.exit(f"{name} already lives in {to_id}")
    new_path = tgt["path"] / name
    if new_path.exists():
        sys.exit(f"cannot promote: {new_path} already exists")
    import shutil
    new_path.parent.mkdir(parents=True, exist_ok=True)
    shutil.move(str(path), str(new_path))
    print(f"promoted {name}: {src['id']} -> {to_id}")
    _, npath = resolve(name, sources)  # re-resolve winner (precedence may re-pick)
    link_one(roots, name, npath or new_path, quiet=True)
    prune_dangling(roots, quiet=True)
    print(f"relinked {name} -> {npath or new_path}")
    print(f"reverse with: skillbox promote {name} --to {src['id']}")
    print("commit the move in each affected source repo when ready (skillbox does not auto-commit).")


def cmd_add(roots, sources, skill, prefer=None):
    skill = require_name(skill)
    src, path = resolve(skill, sources, prefer)
    if not src:
        sys.exit(f"not found: {skill}" + (f" in source {prefer}" if prefer else " in any source"))
    linked, relinked = link_one(roots, skill, path)
    if not linked and not relinked:
        print(f"{skill}: already mounted everywhere")


def cmd_rm(roots, skill):
    skill = require_name(skill)
    n = 0
    for rname, root in roots.items():
        dst = root / skill
        if dst.is_symlink():
            dst.unlink()
            print(f"unlinked {rname}/{skill}")
            n += 1
    if not n:
        print(f"{skill}: no skillbox-owned symlinks found")


def cmd_update(sources, dry):
    seen_roots = set()
    for src in sources:
        root = git_root(src["path"])
        if not root:
            print(f"{src['id']}: not a git repo, skipped")
            continue
        if str(root) in seen_roots:
            continue
        seen_roots.add(str(root))
        if dry:
            subprocess.run(["git", "-C", str(root), "fetch", "--quiet"], check=False)
            r = subprocess.run(["git", "-C", str(root), "diff", "--stat", "HEAD..@{u}", "--", "*SKILL.md"],
                               capture_output=True, text=True)
            print(f"{src['id']}: {r.stdout.strip() or 'up to date'}")
        else:
            r = subprocess.run(["git", "-C", str(root), "pull", "--ff-only"],
                               capture_output=True, text=True)
            out = (r.stdout or r.stderr).strip()
            print(f"{src['id']}: {out.splitlines()[-1] if out else 'ok'}")


def cmd_sync(roots, sources, no_pull=False):
    if not no_pull:
        cmd_update(sources, dry=False)
    plan, _ = resolve_plan(sources)
    linked = relinked = 0
    for name, (src, path) in plan.items():
        l, r = link_one(roots, name, path, quiet=True)
        linked += l
        relinked += r
    cleaned = prune_dangling(roots, quiet=True)
    print(f"sync: {len(plan)} skills resolved · linked={linked} relinked={relinked} pruned={cleaned}")


def owner_of(target, sources, plan, name):
    """Attribute an installed symlink to the source that actually holds its
    target (readlink), not merely the plan winner — a link can point at a
    shadowed copy after `add --source <shadow>` or drift."""
    if target:
        for src in sources:
            for _n, p in source_skills(src).items():
                try:
                    if p.resolve() == target:
                        return src["id"]
                except OSError:
                    continue
    w = plan.get(name)
    return w[0]["id"] if w else "?(unmanaged)"


def cmd_list(roots, sources):
    plan, _ = resolve_plan(sources)
    root = next(iter(roots.values()))
    if not root.is_dir():
        sys.exit(f"primary root missing: {root}")
    for link in sorted(root.iterdir()):
        if not link.is_symlink():
            continue
        try:
            target = link.resolve()
        except OSError:
            target = None
        print(f"{link.name:32} {owner_of(target, sources, plan, link.name)}")


def skill_md_hash(path):
    try:
        return hashlib.sha256((path / "SKILL.md").read_bytes()).hexdigest()[:12]
    except OSError:
        return None


def doctor_problems(roots, sources):
    """Return (problems, installed, collisions, parity). Pure — no printing."""
    plan, collisions = resolve_plan(sources)
    problems = []
    for rname, root in roots.items():
        if not root.is_dir():
            problems.append(("MISSING-ROOT", rname, str(root)))
    installed = set()
    for root in roots.values():
        if root.is_dir():
            installed |= {l.name for l in root.iterdir() if l.is_symlink()}
    # Iterate installed ∪ plan winners: a winner blocked by a real file/dir in
    # EVERY root is symlinked nowhere (absent from `installed`) and would slip by
    # unreported. MISSING stays guarded on is_installed so an available-but-
    # uninstalled skill is not falsely flagged as missing.
    for name in sorted(set(installed) | set(plan)):
        winner = plan.get(name)
        is_installed = name in installed
        if is_installed and not winner:
            # installed (symlinked somewhere) but no source repo owns it — informational,
            # not a blocked mount. A real local dir here is the skill's home, not an obstacle.
            problems.append(("UNMANAGED", name, "installed but no source repo owns it"))
        for rname, root in roots.items():
            if not root.is_dir():
                continue
            link = root / name
            if not link.is_symlink():
                if link.exists():
                    if winner:  # a real file blocks a skill that SHOULD mount → blocking
                        problems.append(("OCCUPIED", f"{rname}/{name}",
                                         "real file/dir blocks the mount (not skillbox-owned)"))
                elif winner and is_installed:
                    problems.append(("MISSING", f"{rname}/{name}",
                                     f"-> {winner[0]['id']} (present in other runtimes)"))
                continue
            if not link.exists():
                problems.append(("BROKEN", f"{rname}/{name}", f"-> {os.readlink(link)}"))
            elif winner and link.resolve() != winner[1].resolve():
                problems.append(("DRIFTED", f"{rname}/{name}",
                                 f"-> {os.readlink(link)} (winner: {winner[1]})"))
    for name, owners in sorted(collisions.items()):
        problems.append(("SHADOWED", name, f"{owners[0]} wins; shadowed: {owners[1:]}"))
    parity = {}
    for name in sorted(installed):
        hashes = {}
        for rname, root in roots.items():
            link = root / name
            if link.is_symlink() and link.exists():
                hashes[rname] = skill_md_hash(link)
        if len({h for h in hashes.values() if h}) > 1:
            problems.append(("PARITY", name, f"SKILL.md differs across runtimes: {hashes}"))
        parity[name] = {"roots": hashes, "consistent": len({h for h in hashes.values() if h}) <= 1}
    return problems, installed, collisions, parity


def cmd_doctor(roots, sources, as_json=False):
    problems, installed, collisions, parity = doctor_problems(roots, sources)
    blocking = [p for p in problems if p[0] in ("BROKEN", "MISSING", "DRIFTED", "MISSING-ROOT", "PARITY", "OCCUPIED")]
    if as_json:
        print(json.dumps({
            "roots": {k: str(v) for k, v in roots.items()},
            "skills_installed": len(installed),
            "problems": [{"kind": k, "where": w, "detail": d} for k, w, d in problems],
            "blocking": len(blocking),
            "parity": parity,
        }, indent=2))
    else:
        for kind, where, detail in problems:
            print(f"{kind:12} {where}  {detail}")
        print(f"doctor: {len(blocking)} blocking problem(s)" if blocking
              else f"doctor: clean ({len(installed)} skills across "
                   f"{sum(1 for r in roots.values() if r.is_dir())} runtimes)"
                   + (f"; {len(collisions)} shadow(s)" if collisions else ""))
    return 1 if blocking else 0


def skill_description(path):
    try:
        for line in (path / "SKILL.md").read_text(errors="replace").split("\n")[:12]:
            if line.startswith("description:"):
                d = line[len("description:"):].strip().strip('"')
                return d[:220] + ("…" if len(d) > 220 else "")
    except OSError:
        pass
    return ""


# ── source management (manifest edits) ──────────────────────────────────────

def _resolve_source_path(path):
    """A source path points at a skills dir (subfolders each holding SKILL.md).
    Accept either that dir or a repo root containing a `skills/` subdir; return
    the dir that actually yields ≥1 skill, or None."""
    p = Path(os.path.expanduser(path))
    if not p.is_dir():
        return None
    for cand in (p, p / "skills"):
        if source_skills({"path": cand, "single_skill": None}):
            return cand
    return None


def cmd_source_add(name, path, priority=None):
    """Add a local source repo to the manifest (e.g. a teammate's clone). Born
    LOWEST precedence so it can never shadow your own skills; its skills are NOT
    auto-mounted — they show as available to install. Reversible via source rm."""
    require_name(name)
    _, sources = load()
    if any(s["id"] == name for s in sources):
        sys.exit(f"source '{name}' already exists — pick another id (or `skillbox source rm {name}` first)")
    resolved = _resolve_source_path(path)
    if resolved is None:
        sys.exit(f"no skills found at {path} — point at a dir of SKILL.md folders "
                 "(or a repo root containing skills/)")
    if priority is None:  # lowest precedence: a teammate source never shadows your own
        priority = max([s["priority"] for s in sources], default=0) + 10
    with open(MANIFEST, "a", encoding="utf-8") as f:
        f.write(f'\n[sources.{name}]\npath = {_toml_string(resolved)}\npriority = {priority}\n')
    n = len(source_skills({"path": resolved, "single_skill": None}))
    print(f"added source '{name}' (priority {priority}, {n} skills) → {resolved}")
    print(f"its skills are available to install, not auto-mounted. reverse: skillbox source rm {name}")


def cmd_source_rm(name):
    """Remove a source's block from the manifest (reverse of source add).
    Preserves the rest of the file; leaves any already-mounted symlinks for
    `doctor`/`rm` to reconcile."""
    require_name(name)
    lines = MANIFEST.read_text(encoding="utf-8").splitlines(keepends=True)
    start = next((i for i, l in enumerate(lines) if l.strip() == f"[sources.{name}]"), None)
    if start is None:
        sys.exit(f"no source '{name}' in {MANIFEST}")
    end = start + 1
    while end < len(lines) and not lines[end].lstrip().startswith("["):
        end += 1
    # Don't swallow blank/comment lines that head the NEXT table — they belong to it,
    # not to the block being removed (else `source rm` silently eats a user's comments).
    while end - 1 > start and (lines[end - 1].strip() == "" or lines[end - 1].lstrip().startswith("#")):
        end -= 1
    if start > 0 and lines[start - 1].strip() == "":  # drop the separating blank line we added
        start -= 1
    del lines[start:end]
    MANIFEST.write_text("".join(lines), encoding="utf-8")
    print(f"removed source '{name}' from {MANIFEST}")
    print("skills it provided stay symlinked until you run `skillbox doctor`/`rm`.")


# ── git view (read-only: per-skill uncommitted diff + version history) ───────

def _skill_git(path):
    """(gitroot, relpath-str) if the skill folder sits under a git repo, else (None, None)."""
    root = git_root(path)
    if not root:
        return None, None
    try:
        return root, str(path.resolve().relative_to(root.resolve()))
    except ValueError:
        return None, None


def gitinfo(path, n=25):
    """Read-only git view of a skill folder: uncommitted diff vs HEAD + recent
    history touching the folder. Returns {git, diff, clean, log:[{hash,date,subj}],
    repo}. NEVER writes (only `git diff` / `git log`)."""
    root, rel = _skill_git(path)
    if not root:
        return {"git": False, "diff": "", "clean": True, "log": [], "repo": ""}

    def g(*a):
        return subprocess.run(["git", "-C", str(root), *a], capture_output=True, text=True).stdout

    diff = g("diff", "HEAD", "--", rel).rstrip()
    log = []
    for line in g("log", f"-{n}", "--format=%h%x09%ad%x09%s", "--date=short", "--", rel).splitlines():
        parts = line.split("\t", 2)
        if len(parts) == 3:
            log.append({"hash": parts[0], "date": parts[1], "subj": parts[2]})
    return {"git": True, "diff": diff, "clean": not diff, "log": log, "repo": root.name}


def cmd_diff(sources, name):
    src, path = resolve(name, sources)
    if not src:
        sys.exit(f"not found: {name}")
    info = gitinfo(path)
    if not info["git"]:
        print(f"{name}: not under a git repo ({path})")
    elif info["clean"]:
        print(f"{name}: no uncommitted changes (clean in {info['repo']})")
    else:
        print(info["diff"])


def cmd_log(sources, name):
    src, path = resolve(name, sources)
    if not src:
        sys.exit(f"not found: {name}")
    info = gitinfo(path)
    if not info["git"]:
        print(f"{name}: not under a git repo ({path})")
    elif not info["log"]:
        print(f"{name}: no commit history for this folder")
    else:
        for c in info["log"]:
            print(f"{c['hash']} {c['date']} {c['subj']}")


# ── GUI (localhost) ─────────────────────────────────────────────────────────

UI_CSS = """
:root{--bg:#fdfaf6;--fg:#1a1a1a;--accent:#1d4ed8;--line:#e4ddd0;--muted:#6b6b60}
*{box-sizing:border-box}
body{font:14px/1.55 ui-monospace,"SF Mono",Menlo,monospace;background:var(--bg);color:var(--fg);margin:0}
header{padding:22px 28px 14px;border-bottom:1px solid var(--line)}
header h1{font-size:20px;margin:0 0 2px;letter-spacing:-.01em}
header .sub{color:var(--muted);font-size:12px}
a{color:var(--accent)}
button,.btn{font:inherit;font-size:12px;background:#fff;border:1px solid var(--line);border-radius:6px;padding:5px 12px;color:var(--accent);cursor:pointer;text-decoration:none;display:inline-block}
button:hover,.btn:hover{border-color:var(--accent)}
.btn.danger{color:#b42318}
input[type=text]{font:inherit;background:#fff;border:1px solid var(--line);border-radius:6px;padding:7px 10px;color:var(--fg)}
input[type=text]:focus{outline:none;border-color:var(--accent)}
.toolbar{display:flex;gap:10px;align-items:center;padding:12px 28px;border-bottom:1px solid var(--line);flex-wrap:wrap}
.toolbar #flt{flex:1;min-width:200px;max-width:420px}
.docstrip{background:#fbe4e1;border-bottom:1px solid #ecc8c3;padding:9px 28px;font-size:12px;color:#b42318}
.note{background:#fbf0d9;border-bottom:1px solid #e8d9b0;padding:8px 28px;font-size:12px}
.err{background:#fbe4e1;border:1px solid #ecc8c3;border-radius:6px;padding:6px 10px;font-size:12px;margin:6px 0}
.list{padding:2px 0}
details.row{border-bottom:1px solid var(--line)}
details.row>summary{list-style:none;cursor:pointer;display:flex;align-items:center;gap:9px;padding:9px 28px}
details.row>summary::-webkit-details-marker{display:none}
details.row[open]>summary{background:#fffdf9}
.glyph{font-size:9px}.glyph.ok{color:#1f7a4d}.glyph.bad{color:#b42318}.glyph.avail{color:#b9b3a6}
.nm{font-weight:600}
.tag{font-size:10px;border:1px solid var(--line);border-radius:999px;padding:1px 8px;color:var(--muted)}
.tag.win{color:#1f7a4d;border-color:#1f7a4d}
.tag.avail{color:var(--accent);border-color:var(--accent)}
.detail{padding:2px 28px 14px 46px;font-size:12px;color:#3a3a34}
.detail .desc{color:var(--muted);margin-bottom:6px;max-width:70ch}
.rts{display:flex;gap:12px;flex-wrap:wrap;margin-bottom:6px}
.rt.on{color:#1f7a4d}.rt.off{color:#b42318}
.pnote{color:#b42318;margin-bottom:6px}
.acts{display:flex;gap:6px;flex-wrap:wrap;margin-bottom:6px}
.actf{display:inline;margin:0}
.srcpath,.hint{color:var(--muted);font-size:11px;margin-top:4px}
.ovr{color:var(--muted);font-size:11px;margin-bottom:6px;max-width:70ch}
.legend{color:var(--muted);font-size:11px;white-space:nowrap;margin-left:4px}
.git{margin-top:8px}
.gitcols{display:flex;gap:18px;flex-wrap:wrap}
.gitdiff{flex:2;min-width:240px}.githist{flex:1;min-width:170px}
.gitcols h4{font-size:10px;text-transform:uppercase;letter-spacing:.06em;color:var(--muted);margin:0 0 4px}
.gitlog{list-style:none;margin:0;padding:0;font-size:11px}
.gitlog li{padding:2px 0;color:#3a3a34;line-height:1.4}.gitlog .gd{color:var(--muted)}
.gitnone{color:var(--muted);font-size:11px;font-style:italic}
pre.diff{background:#f6f1e7;border:1px solid var(--line);border-radius:6px;padding:8px 10px;font-size:11px;overflow-x:auto;margin:6px 0 0}
.empty{color:var(--muted);font-size:12px;padding:18px 28px}
.shell{display:flex;align-items:flex-start}
.srcbar{width:182px;flex:none;border-right:1px solid var(--line);padding:10px 0}
.srcbar h2{font-size:11px;text-transform:uppercase;letter-spacing:.06em;color:var(--muted);margin:0;padding:6px 16px}
.srcitem{display:flex;justify-content:space-between;gap:8px;padding:6px 16px;cursor:pointer;color:var(--fg);text-decoration:none;border-left:2px solid transparent}
.srcitem:hover{background:#fffdf9}
.srcitem.active{background:#fffdf9;border-left-color:var(--accent);color:var(--accent)}
.srcitem .cnt{color:var(--muted)}
.seg{display:flex;gap:4px;padding:10px 16px 4px;flex-wrap:wrap;border-top:1px solid var(--line);margin-top:8px}
.seg .opt{font-size:10px;border:1px solid var(--line);border-radius:999px;padding:2px 8px;cursor:pointer;color:var(--muted)}
.seg .opt.active{color:var(--accent);border-color:var(--accent)}
.addsrc{display:flex;flex-direction:column;gap:6px;padding:10px 16px 4px}
.addsrc input{font:inherit;font-size:11px;background:#fff;border:1px solid var(--line);border-radius:6px;padding:5px 8px;color:var(--fg);width:100%}
.addsrc input:focus{outline:none;border-color:var(--accent)}
.addsrc .btn{font-size:11px;padding:5px 8px;text-align:center}
.addnote{color:var(--muted);font-size:10px;padding:6px 16px 10px;line-height:1.5}
.main{flex:1;min-width:0}
"""


def render_page(roots, sources, state, token=""):
    """Single-window view → bytes. Pure; testable without a live server.

    A left source rail (filter by repo, with owned counts) beside a filterable
    skill list; each skill is an expandable row with runtime parity + POST
    actions (CSRF token). A show-state toggle (installed/available/all, default
    installed) and a doctor strip appear when relevant. The server renders every
    row; the rail/toggle/text filters are client-side."""
    from html import escape

    home = os.path.expanduser("~")
    def short(p):
        s = str(p)
        return "~" + s[len(home):] if s.startswith(home) else s

    primary = next(iter(roots.values()))
    installed = {l.name for l in primary.iterdir() if l.is_symlink()} if primary.is_dir() else set()
    plan, collisions = resolve_plan(sources)
    problems, _, _, parity = doctor_problems(roots, sources)
    blocking = [(k, w, d) for (k, w, d) in problems
                if k in ("BROKEN", "MISSING", "DRIFTED", "MISSING-ROOT", "PARITY", "OCCUPIED")]
    prob_by_name = {}
    for k, w, d in problems:
        prob_by_name.setdefault(w.split("/")[-1], set()).add(k)

    def has_block(n):
        return bool(prob_by_name.get(n, set()) & {"BROKEN", "MISSING", "DRIFTED", "PARITY", "OCCUPIED"})

    def src_of(name):
        win = plan.get(name)
        return win[0]["id"] if win else "unmanaged"

    names = sorted(set(plan) | installed, key=lambda n: (0 if has_block(n) else 1, n))
    counts = {}  # rows owned by each source (the tag you see), + unmanaged
    for n in names:
        counts[src_of(n)] = counts.get(src_of(n), 0) + 1

    def row(name):
        win = plan.get(name)
        src_id = win[0]["id"] if win else None
        path = win[1] if win else None
        inst = name in installed
        kinds = prob_by_name.get(name, set())
        g, gc = ("●", "bad") if has_block(name) else (("●", "ok") if inst else ("○", "avail"))
        tag = escape(src_id) if src_id else "unmanaged"
        sm = [f'<summary><span class="glyph {gc}">{g}</span><span class="nm">{escape(name)}</span>'
              f'<span class="tag">{tag}</span>']
        if name in collisions:  # this row is the winner (highest-precedence source)
            others = " · ".join(escape(o) for o in collisions[name][1:])
            sm.append(f'<span class="tag win">overrides {others}</span>')
        if not inst:
            sm.append('<span class="tag avail">available</span>')
        sm.append('</summary>')
        dt = ['<div class="detail">']
        desc = skill_description(path) if path else ""
        if desc:
            dt.append(f'<div class="desc">{escape(desc)}</div>')
        rts = []
        for rn, rt in roots.items():
            on = (rt / name).is_symlink() and (rt / name).exists()
            rts.append(f'<span class="rt {"on" if on else "off"}">{"✓" if on else "✗"} {escape(rn)}</span>')
        dt.append('<div class="rts">' + "".join(rts) + '</div>')
        shown = kinds - {"SHADOWED"}  # the "overrides" pill + explainer convey this — it's not a warning
        if shown:
            dt.append(f'<div class="pnote">⚠ {escape(", ".join(sorted(shown)))}</div>')
        if name in collisions:
            dt.append(f'<div class="ovr">same name in '
                      f'{" · ".join(escape(o) for o in collisions[name][1:])} — this '
                      f'<b>{escape(src_id or "?")}</b> version loads; the others are ignored.</div>')
        acts = []
        if inst:
            acts.append(f'<form class="actf" method="post" action="/rm">'
                        f'<input type="hidden" name="s" value="{escape(name)}">'
                        f'<input type="hidden" name="t" value="{escape(token)}">'
                        f'<button class="btn danger">remove</button></form>')
        elif src_id:
            acts.append(f'<form class="actf" method="post" action="/add">'
                        f'<input type="hidden" name="s" value="{escape(name)}">'
                        f'<input type="hidden" name="src" value="{escape(src_id)}">'
                        f'<input type="hidden" name="t" value="{escape(token)}">'
                        f'<button class="btn">install</button></form>')
        dt.append('<div class="acts">' + "".join(acts) + '</div>')
        if path:
            dt.append(f'<div class="srcpath">{escape(src_id or "?")}: {escape(short(path))}</div>')
            dt.append(f'<div class="hint">share: <code>skillbox promote {escape(name)} --to &lt;source&gt;</code></div>')
            dt.append(f'<div class="git" data-skill="{escape(name)}"></div>')  # lazy: filled via /gitinfo on expand — git never runs on full render (keeps the 118-skill page instant)
        dt.append('</div>')
        return (f'<details class="row" data-name="{escape(name)}" '
                f'data-source="{escape(src_of(name))}" data-inst="{1 if inst else 0}">'
                + "".join(sm) + "".join(dt) + '</details>')

    h = [f"<!doctype html><html><head><meta charset='utf-8'><title>skillbox</title><style>{UI_CSS}</style></head><body>"]
    h.append('<header><h1>skillbox</h1><div class="sub">a toolbox for your AI skills · '
             '<a href="/about">how it works</a></div></header>')
    if blocking:
        items = "; ".join(f"{escape(k)} {escape(w)}" for k, w, d in blocking[:4])
        more = f" +{len(blocking) - 4} more" if len(blocking) > 4 else ""
        h.append(f'<div class="docstrip">&#9888; {len(blocking)} issue(s): {items}{more} · run '
                 '<code>skillbox doctor</code></div>')
    if state["flash"]:
        h.append(f'<div class="note">{escape(state["flash"])}</div>')
    if state["error"]:
        h.append(f'<div class="err">{escape(state["error"])}</div>')
    h.append('<div class="shell">')
    # ── source rail (filter by repo, in precedence order) + show-state toggle ──
    h.append('<nav class="srcbar"><h2>sources</h2>')
    h.append(f'<a class="srcitem active" data-src="all" href="#">all<span class="cnt">{len(names)}</span></a>')
    for s in sources:
        sid = s["id"]
        h.append(f'<a class="srcitem" data-src="{escape(sid)}" href="#" '
                 f'title="{escape(short(s["path"]))} · priority {s["priority"]}">'
                 f'{escape(sid)}<span class="cnt">{counts.get(sid, 0)}</span></a>')
    if counts.get("unmanaged"):
        h.append(f'<a class="srcitem" data-src="unmanaged" href="#">unmanaged'
                 f'<span class="cnt">{counts["unmanaged"]}</span></a>')
    # Default to "available" on a fresh fleet so the shelf isn't an empty "nothing
    # here" wall when nothing is installed yet (the whole point is to install).
    default_show = "installed" if installed else "available"
    h.append('<div class="seg">' + "".join(
        f'<span class="opt{" active" if s == default_show else ""}" data-show="{s}">{s}</span>'
        for s in ("installed", "available", "all")) + '</div>')
    h.append(f'<form class="addsrc" method="post" action="/source-add">'
             f'<input type="text" name="id" placeholder="id (e.g. coworker)" autocomplete="off">'
             f'<input type="text" name="path" placeholder="~/path/to/their/skills" autocomplete="off">'
             f'<input type="hidden" name="t" value="{escape(token)}">'
             f'<button class="btn">+ add source</button></form>')
    h.append('<div class="addnote">a teammate\'s repo — added at lowest precedence, '
             'never overrides yours; its skills appear ○ available to install.</div>')
    h.append('</nav>')
    # ── main column: filter + skill list ──
    h.append('<div class="main">')
    h.append('<div class="toolbar"><input id="flt" type="text" placeholder="filter skills…">'
             '<span class="legend"><span class="glyph ok">●</span> installed &middot; '
             '<span class="glyph avail">○</span> available</span></div>')
    h.append('<div class="list">')
    if names:
        h.extend(row(n) for n in names)
    else:
        h.append('<div class="empty">no skills resolved — check ~/.skillbox/skills.toml</div>')
    h.append('</div>')  # close .list
    h.append('<div id="nomatch" class="empty" style="display:none">nothing here — '
             'pick another source, or switch the filter to "available" / "all".</div>')
    h.append('</div></div>')  # close .main, .shell
    h.append(f"<script>window.__sb_show={json.dumps(default_show)};</script>")
    h.append("""<script>
(function(){var src='all',show=window.__sb_show||'installed',flt='';
function apply(){var vis=0;document.querySelectorAll('details.row').forEach(function(d){
var okName=d.dataset.name.toLowerCase().indexOf(flt)>-1;
var okSrc=src==='all'||d.dataset.source===src;
var okShow=show==='all'||(show==='installed')===(d.dataset.inst==='1');
var ok=okName&&okSrc&&okShow;d.style.display=ok?'':'none';if(ok)vis++;});
var nm=document.getElementById('nomatch');if(nm)nm.style.display=vis?'none':'';}
document.getElementById('flt').addEventListener('input',function(){flt=this.value.toLowerCase();apply();});
document.querySelectorAll('.srcitem').forEach(function(a){a.addEventListener('click',function(e){e.preventDefault();
document.querySelectorAll('.srcitem').forEach(function(x){x.classList.remove('active');});
a.classList.add('active');src=a.dataset.src;apply();});});
document.querySelectorAll('.seg .opt').forEach(function(o){o.addEventListener('click',function(){
document.querySelectorAll('.seg .opt').forEach(function(x){x.classList.remove('active');});
o.classList.add('active');show=o.dataset.show;apply();});});
function esc(s){return String(s).replace(/[&<>"]/g,function(c){return {'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[c];});}
document.querySelectorAll('details.row').forEach(function(d){d.addEventListener('toggle',function(){
if(!d.open)return;var g=d.querySelector('.git');if(!g||g.dataset.loaded)return;
g.dataset.loaded='1';g.innerHTML='<div class="gitnone">loading git…</div>';
fetch('/gitinfo?s='+encodeURIComponent(g.dataset.skill)).then(function(r){return r.json();}).then(function(j){
if(!j.git){g.innerHTML='<div class="gitnone">not under a git repo</div>';return;}
var diff=j.clean?'<div class="gitnone">no uncommitted changes</div>':'<pre class="diff">'+esc(j.diff)+'</pre>';
var hist=j.log.length?'<ul class="gitlog">'+j.log.map(function(c){return '<li><code>'+esc(c.hash)+'</code> <span class="gd">'+esc(c.date)+'</span> '+esc(c.subj)+'</li>';}).join('')+'</ul>':'<div class="gitnone">no history</div>';
g.innerHTML='<div class="gitcols"><div class="gitdiff"><h4>uncommitted diff</h4>'+diff+'</div><div class="githist"><h4>version history</h4>'+hist+'</div></div>';
}).catch(function(){g.innerHTML='<div class="gitnone">git view unavailable</div>';});});});
apply();})();
</script>""")
    h.append('</body></html>')
    return "".join(h).encode()


def render_about(repo_url=REPO_URL):
    """In-GUI 'how it works' page: TL;DR + a one-boundary architecture diagram
    (monochrome, top-down — /architect discipline) + a link to the source repo.
    Pure → bytes; testable via `skillbox ui --render-about`."""
    from html import escape
    diagram = (
        '<svg viewBox="0 0 360 362" width="360" height="362" role="img" '
        'aria-label="source repos resolve through skillbox into every runtime">'
        '<defs><marker id="ar" markerWidth="9" markerHeight="9" refX="6" refY="3" orient="auto">'
        '<path d="M0,0 L6,3 L0,6 Z" fill="#1a1a1a"/></marker></defs>'
        '<rect x="40" y="14" width="280" height="58" rx="10" fill="#fffdf9" stroke="#1a1a1a" stroke-width="1.5"/>'
        '<text x="180" y="39" text-anchor="middle" font-size="13" font-weight="600" fill="#1a1a1a" font-family="monospace">source repos</text>'
        '<text x="180" y="57" text-anchor="middle" font-size="10" fill="#6b6b60" font-family="monospace">shared &#8250; team &#8250; personal · first-wins</text>'
        '<line x1="180" y1="72" x2="180" y2="110" stroke="#1a1a1a" stroke-width="1.5" marker-end="url(#ar)"/>'
        '<text x="188" y="95" font-size="9.5" fill="#6b6b60" font-family="monospace">resolve winner</text>'
        '<rect x="92" y="110" width="176" height="56" rx="10" fill="#1a1a1a"/>'
        '<text x="180" y="134" text-anchor="middle" font-size="14" font-weight="600" fill="#fdfaf6" font-family="monospace">skillbox</text>'
        '<text x="180" y="152" text-anchor="middle" font-size="9.5" fill="#d8d2c4" font-family="monospace">new · promote · doctor · sync</text>'
        '<line x1="180" y1="166" x2="180" y2="204" stroke="#1a1a1a" stroke-width="1.5" marker-end="url(#ar)"/>'
        '<text x="188" y="189" font-size="9.5" fill="#6b6b60" font-family="monospace">per-skill symlink</text>'
        '<rect x="20" y="204" width="320" height="144" rx="10" fill="#fffdf9" stroke="#1a1a1a" stroke-width="1.5"/>'
        '<text x="180" y="228" text-anchor="middle" font-size="13" font-weight="600" fill="#1a1a1a" font-family="monospace">runtimes · one folder, everywhere</text>'
        '<text x="180" y="254" text-anchor="middle" font-size="11" fill="#1a1a1a" font-family="monospace">~/.claude/skills   (Claude Code)</text>'
        '<text x="180" y="276" text-anchor="middle" font-size="11" fill="#1a1a1a" font-family="monospace">~/.agents/skills   (Codex)</text>'
        '<text x="180" y="298" text-anchor="middle" font-size="11" fill="#1a1a1a" font-family="monospace">~/.cursor/skills   (Cursor)</text>'
        '<text x="180" y="320" text-anchor="middle" font-size="11" fill="#6b6b60" font-family="monospace">~/.codex/skills   (compat)</text>'
        '</svg>'
    )
    h = [
        f"<!doctype html><html><head><meta charset='utf-8'><title>skillbox — how it works</title><style>{UI_CSS}</style></head><body>",
        '<header><h1>skillbox</h1><div class="sub">how it works · <a href="/">back to skills</a></div></header>',
        '<div style="max-width:680px;padding:22px 28px">',
        '<p style="font-size:14px;line-height:1.6">One <b>SKILL.md</b> folder, working in every AI coding runtime at '
        'once. Your skills live in source repos and skillbox symlinks each one into Claude Code, Codex, and Cursor by '
        'first-wins precedence. <code>new</code> creates a skill in your own repo; <code>promote</code> shares it to a '
        'team or org source (reversible); <code>doctor</code> keeps every runtime mounted and in sync.</p>',
        '<p style="font-size:13px;line-height:1.6;color:#6b6b60">When two repos define a skill with the <b>same '
        'name</b>, the higher-precedence copy <b>overrides</b> the others — they are not deleted, just dormant '
        '(e.g. a private <code>-mine</code> version overriding a shared one).</p>',
        f'<div style="margin:18px 0">{diagram}</div>',
        f'<p style="font-size:13px">Source: <a href="{escape(repo_url)}">{escape(repo_url)}</a></p>',
        '</div></body></html>',
    ]
    return "".join(h).encode()


def cmd_ui(roots, sources, port=8765, render_once=False):
    state = {"flash": "", "error": ""}
    if render_once:  # smoke/test hook: build one page, print, exit (no server)
        sys.stdout.buffer.write(render_page(roots, sources, state))
        return
    import secrets
    from http.server import BaseHTTPRequestHandler, HTTPServer
    from urllib.parse import parse_qs, urlparse

    # Per-process CSRF token: mutating actions are POST-only and must carry it.
    # A cross-origin page can neither read the token (same-origin policy) nor
    # forge a same-origin POST, so it cannot silently mutate the local fleet.
    token = secrets.token_urlsafe(16)

    def fresh():  # re-read the manifest per request so source-add / external edits show live
        try:
            return load()
        except SystemExit:
            return roots, sources  # keep last-good on a transiently-bad manifest

    def run_action(fn, *a, **kw):
        import io, contextlib
        buf = io.StringIO()
        try:
            with contextlib.redirect_stdout(buf):
                fn(*a, **kw)
            state["flash"], state["error"] = buf.getvalue().strip(), ""
        except SystemExit as e:
            state["error"] = str(e)
        except Exception as e:  # render, don't crash the server
            state["error"] = f"{type(e).__name__}: {e}"

    def send_html(h, body):
        h.send_response(200)
        h.send_header("Content-Type", "text/html; charset=utf-8")
        h.send_header("Content-Length", str(len(body)))
        h.end_headers()
        h.wfile.write(body)

    def redirect(h, where="/"):
        h.send_response(303)
        h.send_header("Location", where)
        h.end_headers()

    class H(BaseHTTPRequestHandler):
        def log_message(self, *a):
            pass

        def do_GET(self):
            u = urlparse(self.path)
            if u.path == "/about":
                send_html(self, render_about())
                return
            if u.path == "/gitinfo":  # read-only JSON for per-skill diff + history
                q = {k: v[0] for k, v in parse_qs(u.query).items()}
                _, s = fresh()
                _, p = resolve(q.get("s", ""), s)
                info = gitinfo(p) if p else {"git": False, "diff": "", "clean": True, "log": [], "repo": ""}
                body = json.dumps(info).encode()
                self.send_response(200)
                self.send_header("Content-Type", "application/json; charset=utf-8")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
                return
            if u.path != "/":  # GET never mutates — stale /add,/rm just bounce home
                redirect(self)
                return
            r, s = fresh()
            send_html(self, render_page(r, s, state, token))
            state["flash"] = state["error"] = ""  # one-shot: a result shows once, not on every refresh

        def do_POST(self):
            u = urlparse(self.path)
            length = int(self.headers.get("Content-Length", 0) or 0)
            body = self.rfile.read(length).decode("utf-8", "replace") if length else ""
            q = {k: v[0] for k, v in parse_qs(body).items()}
            origin = self.headers.get("Origin")
            host = self.headers.get("Host", "")
            same_origin = (not origin) or urlparse(origin).netloc == host
            # Pin Host to loopback so a DNS-rebound page (its Host = attacker domain
            # resolving to 127.0.0.1) cannot pass same-origin and mutate the fleet.
            host_ok = host.split(":")[0] in ("127.0.0.1", "localhost")
            if q.get("t") != token or not same_origin or not host_ok:  # CSRF + rebind wall
                self.send_response(403)
                self.end_headers()
                self.wfile.write(b"forbidden (csrf)")
                return
            r, s = fresh()
            if u.path == "/add" and "s" in q:
                run_action(cmd_add, r, s, q["s"], q.get("src"))
            elif u.path == "/rm" and "s" in q:
                run_action(cmd_rm, r, q["s"])
            elif u.path == "/source-add" and q.get("id") and q.get("path"):
                run_action(cmd_source_add, q["id"], q["path"])
            redirect(self)

    print(f"skillbox ui → http://127.0.0.1:{port} (Ctrl-C to stop)")
    HTTPServer(("127.0.0.1", port), H).serve_forever()


def main():
    args = sys.argv[1:]
    if not args:
        print(__doc__)
        return
    if not MANIFEST.exists():
        sys.exit(f"no manifest at {MANIFEST}\n"
                 f"create it:  mkdir -p {MANIFEST.parent} && cp skills.toml.example {MANIFEST}\n"
                 "then edit the [sources.*] paths to point at your skill repos.")
    roots, sources = load()
    cmd = args[0]

    def opt(flag):
        if flag not in args:
            return None
        i = args.index(flag) + 1
        if i >= len(args) or args[i].startswith("--"):
            sys.exit(f"{flag} needs a value")
        return args[i]

    if cmd == "list":
        cmd_list(roots, sources)
    elif cmd == "new" and len(args) >= 2:
        cmd_new(roots, sources, args[1], opt("--repo"))
    elif cmd == "add" and len(args) >= 2:
        cmd_add(roots, sources, args[1], opt("--source"))
    elif cmd == "rm" and len(args) >= 2:
        cmd_rm(roots, args[1])
    elif cmd == "promote" and len(args) >= 2:
        cmd_promote(roots, sources, args[1], opt("--to"))
    elif cmd == "source" and args[1:2] == ["add"] and len(args) >= 4:
        p = opt("--priority")
        cmd_source_add(args[2], args[3], int(p) if p else None)
    elif cmd == "source" and args[1:2] == ["rm"] and len(args) >= 3:
        cmd_source_rm(args[2])
    elif cmd == "diff" and len(args) >= 2:
        cmd_diff(sources, args[1])
    elif cmd == "log" and len(args) >= 2:
        cmd_log(sources, args[1])
    elif cmd in ("doctor", "audit"):
        sys.exit(cmd_doctor(roots, sources, as_json="--json" in args))
    elif cmd == "sync":
        cmd_sync(roots, sources, no_pull="--no-pull" in args)
    elif cmd == "update":
        cmd_update(sources, "--dry-run" in args)
    elif cmd == "ui":
        if "--render-about" in args:
            sys.stdout.buffer.write(render_about())
            return
        try:
            port = int(opt("--port") or 8765)
        except ValueError:
            sys.exit("--port needs a number")
        cmd_ui(roots, sources, port, render_once="--render" in args)
    else:
        sys.exit(f"unknown or incomplete command: {' '.join(args)!r}\n{__doc__}")


if __name__ == "__main__":
    main()
