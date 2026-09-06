#!/usr/bin/env python3
"""skillbox — a toolbox for your AI coding-agent skills.

One SKILL.md folder, symlink-fanned into every runtime so it works everywhere.

Commands:
  skillbox list                      installed skills with their resolved source
  skillbox new <name> [--repo ID]    scaffold a new skill (default: your own repo) and link it everywhere
  skillbox add <name> [--source ID]  symlink an existing source skill into every runtime root
  skillbox rm <name>                 unlink named runtime-slot symlinks (source folder untouched)
  skillbox retire <name> --source ID safely park an excluded source leaf; refuses foreign or replacement mounts
  skillbox promote <name> --to ID    move the skill to another source repo (reversible) and relink
  skillbox promote <name> --to org   emit a plugin manifest + print the DRAFT marketplace PR (never sends)
  skillbox source add <id> <path>    add a local source repo (e.g. a teammate's clone); `source rm <id>` reverses
  skillbox diff <name>               the skill's uncommitted git diff (if its source repo has a .git)
  skillbox log <name>                the skill's commit history (recent versions of the folder)
  skillbox doctor [--json] [--strict] drift/parity + source health; strict refuses informational conflicts
  skillbox scrub [<name>] [--to ID] [--dry-run]  audit private boundaries (KEEP-PRIVATE / *-leo); block promote leaks
  skillbox sync [--no-pull]          pull Git sources unless --no-pull; relink winners + prune
  skillbox update [--dry-run]        pull Git sources, or fetch-only preview with --dry-run
  skillbox --help | <command> --help show this help without reading the manifest or touching the fleet

Manifest: ~/.skillbox/skills.toml  (override with $SKILLBOX_MANIFEST for tests).

Tier is never a stored flag — it is which source repo holds the folder. `new`
lands in your own repo by default; `promote` (later) is the one deliberate
sharing verb. Codex reads ~/.agents/skills; ~/.codex/skills is Cursor-compat only.
"""
import os, sys, json, re, hashlib, subprocess, ctypes, errno, stat, fcntl, secrets
from contextlib import contextmanager
from pathlib import Path
try:
    import tomllib  # Python 3.11+ stdlib
except ModuleNotFoundError:
    try:
        import tomli as tomllib  # older Python: `pip install tomli`
    except ModuleNotFoundError:
        tomllib = None

# Single source of truth for the public release identity (`skillbox --version`).
VERSION = "1.0.0"

# Config is overridable via $SKILLBOX_MANIFEST so the test harness can point at a
# hermetic sandbox manifest (with fake roots + fake source repos) and never touch
# the real fleet. Mutation state normally lives beside the manifest; callers
# reading a manifest from a source-controlled checkout can redirect that state
# with $SKILLBOX_STATE_DIR so the checkout remains clean.
MANIFEST = Path(os.environ.get("SKILLBOX_MANIFEST", str(Path.home() / ".skillbox" / "skills.toml")))
CONFIG_DIR = Path(os.environ.get("SKILLBOX_STATE_DIR", str(MANIFEST.parent))).expanduser()

# `new` puts a skill in your own repo by default (first-wins overlay means a
# shared same-name skill still wins unless you give yours a unique name, e.g. a
# `-mine` suffix). Override the default source id via $SKILLBOX_DEFAULT_SOURCE.
DEFAULT_NEW_SOURCE = os.environ.get("SKILLBOX_DEFAULT_SOURCE", "personal")


# Optional org plugin marketplace for `promote --to org` (the Claude Code plugin
# marketplace convention). Set $SKILLBOX_ORG_REPO to your "owner/repo"; unset
# disables the org off-ramp. DRAFT PR only — never auto-sent.
ORG_REPO = os.environ.get("SKILLBOX_ORG_REPO", "")

# A skill name becomes BOTH a directory under a source repo and a symlink leaf in
# each runtime root, so it must be one safe path segment — never something that
# escapes via "/", "..", or an absolute path (Path("/src") / "/etc/x" == "/etc/x").
# Shape mirrors the marketplace's kebab-case plugin names; the guard is the security wall.
_VALID_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")

# Private-boundary markers: skills carrying these must never promote to shared/org targets.
_KEEP_PRIVATE_RE = re.compile(
    r"KEEP[- ]PRIVATE|keep-private\s*:\s*true|visibility\s*:\s*private", re.I)

_BLOCKING_DOCTOR_KINDS = {
    "BROKEN", "MISSING", "DRIFTED", "MISSING-ROOT", "PARITY", "OCCUPIED",
}
_STRICT_BLOCKING_DOCTOR_KINDS = _BLOCKING_DOCTOR_KINDS | {
    "SOURCE-MISSING", "SOURCE-DIRTY", "SOURCE-DETACHED", "SOURCE-WORKTREE",
    "SOURCE-AHEAD", "SOURCE-BEHIND", "SOURCE-DIVERGED",
    "UNMANAGED", "SHADOWED", "PATH-SHADOW", "SOURCE-NOT-GIT",
    "SOURCE-NO-UPSTREAM",
}


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
            exclude = s.get("exclude", [])
            if not isinstance(exclude, list) or not all(isinstance(name, str) for name in exclude):
                raise TypeError("source exclude must be an array of skill names")
            if len(exclude) != len(set(exclude)):
                raise TypeError("source exclude must not repeat a skill name")
            sources.append({
                "id": sid, "path": Path(os.path.expanduser(s["path"])),
                "priority": s.get("priority", 99), "single_skill": s.get("single_skill"),
                "exclude": frozenset(require_name(name) for name in exclude),
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
    excluded = src.get("exclude", frozenset())
    if src["single_skill"]:
        name = src["single_skill"]
        return ({name: src["path"]}
                if name not in excluded and (src["path"] / "SKILL.md").exists() else {})
    out = {}
    if src["path"].is_dir():
        for d in src["path"].iterdir():
            if d.name not in excluded and (d / "SKILL.md").exists():
                out[d.name] = d
    return out


def source_skill_path(src, name):
    """Physical leaf path for a source/name pair, including a single-skill root.

    This deliberately does not consult `exclude`: retirement needs to recognize
    an archived source folder even after resolver discovery has hidden it.
    """
    if src.get("single_skill"):
        return src["path"] if src["single_skill"] == name else None
    return src["path"] / name


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


# Every normal Skillbox mutation takes this cooperative lock before it reads
# the manifest.  It serializes `sync`, `add`, `rm`, and `retire` so a stale
# plan cannot re-create an alias just retired by another Skillbox command.  The
# file stays in place: flock is released automatically if the process dies,
# while unlinking the lock pathname would create a second-lock race.
_MUTATION_LOCK_NAME = ".skillbox-mutation.lock"


@contextmanager
def mutation_lock():
    lock = CONFIG_DIR / _MUTATION_LOCK_NAME
    flags = os.O_RDWR | os.O_CREAT | getattr(os, "O_CLOEXEC", 0)
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    try:
        fd = os.open(lock, flags, 0o600)
    except OSError as error:
        sys.exit(f"cannot acquire Skillbox mutation lock at {lock}: {error}")
    try:
        mode = os.fstat(fd).st_mode
        if not stat.S_ISREG(mode) or mode & 0o077:
            sys.exit(f"unsafe Skillbox mutation lock at {lock}: expected a private regular file")
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            sys.exit("another Skillbox mutation is active; wait for it to finish and retry")
        try:
            yield
        finally:
            fcntl.flock(fd, fcntl.LOCK_UN)
    finally:
        os.close(fd)


# Darwin's ordinary rename() replaces the destination.  `renameatx_np` with
# RENAME_EXCL atomically refuses a pre-existing destination instead.  We use
# directory FDs so the source runtime root and recovery journal stay anchored
# even if their pathnames are moved while a non-cooperating process races us.
_RENAME_EXCL = 0x00000004


def _rename_noreplace_at(src_fd, src_name, dst_fd, dst_name):
    """Atomically move one directory entry, refusing an occupied destination.

    The supported Skillbox platforms are macOS and Linux.  On a host without a
    kernel no-replace primitive, retirement fails closed rather than falling
    back to an overwriting rename.
    """
    if sys.platform == "darwin":
        libc = ctypes.CDLL("/usr/lib/libSystem.B.dylib", use_errno=True)
        renameatx_np = libc.renameatx_np
        renameatx_np.argtypes = [
            ctypes.c_int, ctypes.c_char_p,
            ctypes.c_int, ctypes.c_char_p,
            ctypes.c_uint,
        ]
        renameatx_np.restype = ctypes.c_int
        result = renameatx_np(
            src_fd, os.fsencode(src_name), dst_fd, os.fsencode(dst_name), _RENAME_EXCL
        )
    elif sys.platform.startswith("linux"):
        libc = ctypes.CDLL(None, use_errno=True)
        try:
            renameat2 = libc.renameat2
        except AttributeError:
            raise OSError(errno.ENOTSUP, "atomic no-replace rename is unavailable")
        renameat2.argtypes = [
            ctypes.c_int, ctypes.c_char_p,
            ctypes.c_int, ctypes.c_char_p,
            ctypes.c_uint,
        ]
        renameat2.restype = ctypes.c_int
        result = renameat2(
            src_fd, os.fsencode(src_name), dst_fd, os.fsencode(dst_name), 0x00000001
        )
    else:
        raise OSError(errno.ENOTSUP, "atomic no-replace rename is unavailable")
    if result:
        error = ctypes.get_errno()
        raise OSError(error, os.strerror(error), f"{src_name} -> {dst_name}")


def _open_runtime_dir(path):
    flags = getattr(os, "O_SEARCH", os.O_RDONLY)
    flags |= getattr(os, "O_DIRECTORY", 0) | getattr(os, "O_CLOEXEC", 0)
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    return os.open(path, flags)


def _open_runtime_dir_at(parent_fd, name):
    """Open a child directory through an already-anchored parent FD."""
    flags = getattr(os, "O_SEARCH", os.O_RDONLY)
    flags |= getattr(os, "O_DIRECTORY", 0) | getattr(os, "O_CLOEXEC", 0)
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    return os.open(name, flags, dir_fd=parent_fd)


def _new_recovery_journal(root_fd, skill):
    """Create a private journal beneath an already-open runtime root FD."""
    for _ in range(128):
        name = f".skillbox-retire-{skill}-{secrets.token_hex(16)}"
        try:
            os.mkdir(name, 0o700, dir_fd=root_fd)
            return name
        except FileExistsError:
            continue
    raise OSError(errno.EEXIST, "could not reserve a unique recovery journal")


def _journal_matches(root_fd, journal_name, journal_fd):
    """True only while `journal_name` still names the held journal directory.

    Holding a directory FD protects the eventual no-replace move from a
    pathname substitution.  It does not make the human-readable pathname
    immutable, though, so a caller may report that pathname only while its
    directory entry is still the exact directory held by `journal_fd`.
    """
    try:
        named = os.stat(journal_name, dir_fd=root_fd, follow_symlinks=False)
        held = os.fstat(journal_fd)
    except OSError:
        return False
    return (
        stat.S_ISDIR(named.st_mode)
        and named.st_dev == held.st_dev
        and named.st_ino == held.st_ino
    )


def _runtime_root_matches(root_path, root_fd):
    """True only while the configured root path still names the held root FD."""
    try:
        named = os.stat(root_path, follow_symlinks=False)
        held = os.fstat(root_fd)
    except OSError:
        return False
    return (
        stat.S_ISDIR(named.st_mode)
        and named.st_dev == held.st_dev
        and named.st_ino == held.st_ino
    )


def _entry_present(dir_fd, name):
    try:
        os.stat(name, dir_fd=dir_fd, follow_symlinks=False)
        return True
    except FileNotFoundError:
        return False
    except OSError:
        # A changed/unreadable entry is conservatively treated as occupied.
        return True


def _park_slot_noreplace(root_fd, skill, journal_fd):
    """Park `skill` in an anchored journal, returning (raw_target, occupied)."""
    _rename_noreplace_at(root_fd, skill, journal_fd, "mount")
    try:
        mode = os.stat("mount", dir_fd=journal_fd, follow_symlinks=False).st_mode
        raw_target = os.readlink("mount", dir_fd=journal_fd) if stat.S_ISLNK(mode) else None
    except OSError:
        raw_target = None
    return raw_target, _entry_present(root_fd, skill)


def git_root(path):
    d = path
    while d != d.parent and not (d / ".git").exists():
        d = d.parent
    return d if (d / ".git").exists() else None


# ── mount helpers ───────────────────────────────────────────────────────────

def link_one(roots, name, path, quiet=False):
    """Idempotently symlink name -> absolute source path into every root.
    Replaces any differing symlink in the configured slot; refuses real files/dirs."""
    linked = relinked = 0
    target = str(path)
    for rname, root in roots.items():
        if not root.is_dir():
            if not quiet:
                print(f"skip {rname}/{name}: runtime root missing ({root}) — mkdir it or remove it from [roots]")
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
                print(f"skip {rname}/{name}: real file/dir present (symlinks only are replaceable)")
        else:
            dst.symlink_to(path)
            linked += 1
            if not quiet:
                print(f"linked {rname}/{name} -> {path}")
    return linked, relinked


def _target_source(link, target, sources):
    """Return the configured source whose path contains a link target, else None."""
    candidate = Path(target)
    if not candidate.is_absolute():
        candidate = link.parent / candidate
    try:
        candidate = candidate.resolve(strict=False)
    except OSError:
        return None
    for src in sources:
        try:
            candidate.relative_to(src["path"].resolve(strict=False))
            return src
        except (OSError, ValueError):
            continue
    return None


def prune_dangling(roots, sources, quiet=False):
    cleaned = 0
    for rname, root in roots.items():
        if not root.is_dir():
            continue
        for link in sorted(root.iterdir()):
            if link.is_symlink() and not link.exists():
                target = os.readlink(link)
                # Runtime roots are shared with other tools. Never unlink an
                # unknown dangling symlink just because it happens to live
                # beside Skillbox mounts.
                src = _target_source(link, target, sources)
                if src is None:
                    if not quiet:
                        print(f"keep {rname}/{link.name}: target is outside configured sources")
                    continue
                # Only prune a genuinely-dead leaf (source dir present, skill folder
                # gone). If the source's configured path is absent the whole source
                # just blinked out (unmounted / mid-move) — pruning then would silently
                # unlink every skill of that source and report a false-clean fleet.
                # For a single-skill source the configured path IS the skill.
                if not src["path"].exists():
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
    if name in src.get("exclude", frozenset()):
        sys.exit(f"skill '{name}' is excluded by source '{repo}'; remove it from that "
                 "source's exclude list before creating it")
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


def skill_private_boundary(path, name):
    """Return human-readable reasons if this skill must stay private (empty = promotable)."""
    reasons = []
    if name.endswith("-leo"):
        reasons.append("leo-overlay suffix")
    if (path / ".keep-private").exists():
        reasons.append(".keep-private file")
    try:
        if _KEEP_PRIVATE_RE.search((path / "SKILL.md").read_text(errors="replace")):
            reasons.append("KEEP-PRIVATE marker")
    except OSError:
        pass
    return reasons


def skill_leak_paths(path):
    """All paths under a skill folder that would move on promote."""
    if not path.is_dir():
        return [str(path)]
    return sorted(str(p) for p in path.rglob("*") if p.is_file()) + [str(path)]


def scrub_findings(sources, name=None, to_id=None):
    """Return [{name, from_id, to_id, path, reasons, paths}] for would-leak promotes."""
    findings = []
    if name:
        name = require_name(name)
        src, path = resolve(name, sources)
        if not src:
            sys.exit(f"not found: {name}")
        reasons = skill_private_boundary(path, name)
        if not reasons:
            return []
        if to_id and to_id != "org" and to_id == src["id"]:
            return []  # no-op promote to same source
        findings.append({
            "name": name, "from_id": src["id"], "to_id": to_id or "(any shared)",
            "path": path, "reasons": reasons, "paths": skill_leak_paths(path),
        })
        return findings
    for src in sources:
        for skill_name, path in source_skills(src).items():
            reasons = skill_private_boundary(path, skill_name)
            if reasons:
                findings.append({
                    "name": skill_name, "from_id": src["id"],
                    "to_id": to_id or "(any shared)", "path": path,
                    "reasons": reasons, "paths": skill_leak_paths(path),
                })
    return findings


def scrub_would_leak(name, path, to_id, from_id):
    """True when promote <name> from from_id -> to_id would ship a private boundary."""
    reasons = skill_private_boundary(path, name)
    if not reasons:
        return False
    if to_id == from_id:
        return False
    return True


def cmd_scrub(sources, name=None, to_id=None, dry_run=False, as_json=False):
    """Audit KEEP-PRIVATE / *-leo overlays. Dry-run lists would-leak paths; else exit 1."""
    findings = scrub_findings(sources, name, to_id)
    if as_json:
        print(json.dumps([{
            "name": f["name"], "from": f["from_id"], "to": f["to_id"],
            "reasons": f["reasons"], "paths": f["paths"],
        } for f in findings], indent=2))
    else:
        if not findings:
            print("scrub: clean (no private-boundary skills would leak on promote)")
        for f in findings:
            why = ", ".join(f["reasons"])
            target = f" -> {f['to_id']}" if f["to_id"] else ""
            print(f"WOULD-LEAK {f['name']}  {f['from_id']}{target}  ({why})")
            for p in f["paths"]:
                print(f"  {p}")
        if findings:
            print(f"scrub: {len(findings)} private-boundary skill(s) — promote blocked")
    if dry_run or not findings:
        return 0
    return 1


def _scrub_guard_promote(name, path, from_id, to_id):
    if scrub_would_leak(name, path, to_id, from_id):
        reasons = ", ".join(skill_private_boundary(path, name))
        sys.exit(f"promote blocked: {name} is a private boundary ({reasons}). "
                 f"Run `skillbox scrub {name} --to {to_id} --dry-run` to list paths.")


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
    _scrub_guard_promote(name, path, src["id"], "org")
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
    if name in tgt.get("exclude", frozenset()):
        sys.exit(f"cannot promote '{name}' to {to_id}: that source excludes it; remove the "
                 "name from its exclude list first")
    new_path = tgt["path"] / name
    if new_path.exists():
        sys.exit(f"cannot promote: {new_path} already exists")
    _scrub_guard_promote(name, path, src["id"], to_id)
    import shutil
    new_path.parent.mkdir(parents=True, exist_ok=True)
    shutil.move(str(path), str(new_path))
    print(f"promoted {name}: {src['id']} -> {to_id}")
    _, npath = resolve(name, sources)  # re-resolve winner (precedence may re-pick)
    link_one(roots, name, npath or new_path, quiet=True)
    prune_dangling(roots, sources, quiet=True)
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
        print(f"{skill}: no symlinks found in configured runtime slots")


def cmd_retire(roots, sources, skill, source_id):
    """Safely park a retired source leaf outside active runtime slots.

    Unlike the deliberately broad `rm`, retirement proves that every parked
    link still points to the selected source's archived leaf. It also refuses
    if another active source would immediately take the name over on sync.
    """
    skill = require_name(skill)
    if not source_id:
        sys.exit("usage: skillbox retire <name> --source <source-id>")
    src = next((s for s in sources if s["id"] == source_id), None)
    if not src:
        sys.exit(f"unknown --source {source_id}; known: {', '.join(s['id'] for s in sources)}")
    if skill not in src.get("exclude", frozenset()):
        sys.exit(f"cannot retire '{skill}' from {source_id}: add it to that source's exclude list first")
    expected = source_skill_path(src, skill)
    if not expected or not (expected / "SKILL.md").is_file():
        sys.exit(f"cannot retire '{skill}' from {source_id}: its archived source leaf is missing")

    active_owners = [s["id"] for s in sources if skill in source_skills(s)]
    if active_owners:
        sys.exit(f"cannot retire '{skill}': it still resolves from active source(s) "
                 f"{', '.join(active_owners)}; exclude or rehome those copies first")

    expected_resolved = expected.resolve()
    removable, conflicts = [], []
    for rname, root in roots.items():
        if not root.is_dir():
            continue
        root_path = root.resolve(strict=False)
        try:
            root_fd = _open_runtime_dir(root_path)
        except OSError as error:
            conflicts.append(f"{rname} runtime root cannot be opened safely ({error})")
            continue
        try:
            try:
                mode = os.stat(skill, dir_fd=root_fd, follow_symlinks=False).st_mode
            except FileNotFoundError:
                os.close(root_fd)
                continue
            if not stat.S_ISLNK(mode):
                conflicts.append(f"{rname}/{skill} is a real file or directory")
                os.close(root_fd)
                continue
            raw_target = os.readlink(skill, dir_fd=root_fd)
            candidate = Path(raw_target)
            if not candidate.is_absolute():
                candidate = root_path / candidate
            actual = candidate.resolve(strict=False)
            if actual != expected_resolved:
                conflicts.append(f"{rname}/{skill} -> {raw_target}")
                os.close(root_fd)
                continue
            removable.append((rname, root, root_path, root_fd, raw_target))
        except OSError:
            conflicts.append(f"{rname}/{skill} cannot resolve its symlink")
            os.close(root_fd)
    if conflicts:
        for _, _, _, root_fd, _ in removable:
            os.close(root_fd)
        sys.exit(f"cannot retire '{skill}': refusing to park non-{source_id} slot(s): "
                 + "; ".join(conflicts))

    # A source replacement cannot be atomically compared with the preflight
    # target, so this is deliberately a recoverable partial operation: a
    # replaced source entry is captured, validated, retained, and reported.
    # The final move itself is an OS no-replace rename between anchored parent
    # FDs, so it never overwrites a recovery entry planted by another writer.
    retained, failure = [], None
    try:
        for rname, root, root_path, root_fd, raw_target in removable:
            if not _runtime_root_matches(root_path, root_fd):
                failure = (f"runtime root for {root / skill} changed before parking; "
                           "no runtime slot was moved")
                break
            try:
                journal_name = _new_recovery_journal(root_fd, skill)
            except OSError as error:
                failure = f"could not create a recovery journal for {root / skill}: {error}"
                break
            archive = root_path / journal_name / "mount"
            try:
                journal_fd = _open_runtime_dir_at(root_fd, journal_name)
            except OSError as error:
                failure = f"could not open recovery journal for {root / skill}: {error}"
                break
            try:
                # The no-replace move is FD-anchored, but this check is what
                # makes the path in our receipt truthful.  If another process
                # renames/replaces the journal, do not claim the old pathname
                # can recover the held mount.
                if not _runtime_root_matches(root_path, root_fd):
                    failure = (f"runtime root for {root / skill} changed before parking; "
                               "no runtime slot was moved")
                    break
                if not _journal_matches(root_fd, journal_name, journal_fd):
                    failure = (f"recovery journal for {root / skill} changed before parking; "
                               "no runtime slot was moved")
                    break
                try:
                    captured_raw_target, source_reoccupied = _park_slot_noreplace(
                        root_fd, skill, journal_fd
                    )
                except OSError as error:
                    if (_runtime_root_matches(root_path, root_fd)
                            and _journal_matches(root_fd, journal_name, journal_fd)):
                        retained.append(archive)
                        failure = f"could not park {root / skill}: {error}"
                    else:
                        failure = (f"runtime root or recovery journal for {root / skill} changed "
                                   "while parking; the nominal recovery path is untrusted")
                    break

                if not _runtime_root_matches(root_path, root_fd):
                    failure = (f"{root / skill} was parked, but its runtime root changed "
                               "during retirement; the nominal recovery path is untrusted")
                    break
                if not _journal_matches(root_fd, journal_name, journal_fd):
                    failure = (f"{root / skill} was parked, but its recovery journal changed "
                               "during retirement; the nominal recovery path is untrusted")
                    break

                retained.append(archive)
            finally:
                os.close(journal_fd)

            captured_expected = False
            if captured_raw_target is not None:
                captured_candidate = Path(captured_raw_target)
                if not captured_candidate.is_absolute():
                    captured_candidate = root_path / captured_candidate
                captured_expected = (
                    captured_raw_target == raw_target and
                    captured_candidate.resolve(strict=False) == expected_resolved
                )
            if not captured_expected:
                failure = (f"{root / skill} changed during retirement; its captured entry is "
                           f"preserved in {journal_name}")
                break
            # A new entry can arrive immediately after the move.  Do not touch
            # it or claim the host is retired; preserve the original archive.
            if source_reoccupied or _entry_present(root_fd, skill):
                failure = (f"{root / skill} changed during retirement; its source link is "
                           f"preserved in {journal_name}")
                break
    finally:
        for _, _, _, root_fd, _ in removable:
            try:
                os.close(root_fd)
            except OSError:
                pass

    if failure:
        held = "; ".join(str(archive) for archive in retained)
        suffix = f"; verified recovery retained: {held}" if held else ""
        sys.exit(f"cannot retire '{skill}' from {source_id}: {failure}{suffix}")

    archives = "; ".join(str(archive) for archive in retained)
    print(f"retired {skill} from {source_id}: parked {len(removable)} runtime slot(s) "
          f"in hidden recovery folders: {archives}")


def cmd_update(sources, dry):
    seen_roots = set()
    failures = 0
    for src in sources:
        root = git_root(src["path"])
        if not root:
            print(f"{src['id']}: not a git repo, skipped")
            continue
        if str(root) in seen_roots:
            continue
        seen_roots.add(str(root))
        upstream = subprocess.run(
            ["git", "-C", str(root), "rev-parse", "--abbrev-ref",
             "--symbolic-full-name", "@{u}"],
            capture_output=True,
            text=True,
        )
        if upstream.returncode != 0:
            print(f"{src['id']}: no upstream, skipped")
            continue
        if dry:
            fetched = subprocess.run(
                ["git", "-C", str(root), "fetch", "--quiet"],
                capture_output=True,
                text=True,
            )
            if fetched.returncode != 0:
                out = (fetched.stderr or fetched.stdout).strip()
                print(f"{src['id']}: update failed: {out.splitlines()[-1] if out else 'git fetch failed'}")
                failures += 1
                continue
            r = subprocess.run(["git", "-C", str(root), "diff", "--stat", "HEAD..@{u}", "--", "*SKILL.md"],
                               capture_output=True, text=True)
            if r.returncode != 0:
                out = (r.stderr or r.stdout).strip()
                print(f"{src['id']}: update failed: {out.splitlines()[-1] if out else 'upstream comparison failed'}")
                failures += 1
            else:
                print(f"{src['id']}: {r.stdout.strip() or 'up to date'}")
        else:
            r = subprocess.run(["git", "-C", str(root), "pull", "--ff-only"],
                               capture_output=True, text=True)
            out = (r.stdout or r.stderr).strip()
            if r.returncode != 0:
                print(f"{src['id']}: update failed: {out.splitlines()[-1] if out else 'git pull failed'}")
                failures += 1
            else:
                print(f"{src['id']}: {out.splitlines()[-1] if out else 'ok'}")
    return 1 if failures else 0


def cmd_sync(roots, sources, no_pull=False):
    if not no_pull and cmd_update(sources, dry=False):
        sys.exit("sync refused: one or more source updates failed")
    plan, _ = resolve_plan(sources)
    linked = relinked = 0
    for name, (src, path) in plan.items():
        l, r = link_one(roots, name, path, quiet=True)
        linked += l
        relinked += r
    cleaned = prune_dangling(roots, sources, quiet=True)
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
    if not roots:
        sys.exit(f"manifest {MANIFEST} has no runtime roots under [roots] — see skills.toml.example")
    root = next(iter(roots.values()))
    if not root.is_dir():
        sys.exit(f"primary root missing: {root}")
    for link in sorted(root.iterdir()):
        if not is_installed_skill_link(link):
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


def is_installed_skill_link(path):
    return path.is_symlink() and not path.name.startswith(".")


def other_skillbox_on_path(path_env=None, me=None):
    """Return PATH entries named `skillbox` that are not this script.

    Non-blocking doctor signal for the npm/Homebrew name collision: another
    CLI also called skillbox (no doctor/scrub) can win when PATH order is wrong.
    """
    me = (me or Path(__file__)).resolve()
    found = []
    seen = set()
    for d in (path_env if path_env is not None else os.environ.get("PATH", "")).split(os.pathsep):
        if not d:
            continue
        cand = Path(d) / "skillbox"
        try:
            if not (cand.is_file() or cand.is_symlink()):
                continue
            if not os.access(cand, os.X_OK):
                continue
            resolved = cand.resolve()
            if resolved == me:
                continue
            key = str(resolved)
            if key in seen:
                continue
            seen.add(key)
            found.append(str(cand))
        except OSError:
            continue
    return found


def source_git_problems(sources):
    """Return configured Git-source defects observable without network access."""
    problems = []
    seen = set()
    for src in sources:
        path = src["path"]
        if not path.exists():
            problems.append(("SOURCE-MISSING", src["id"], str(path)))
            continue
        root = git_root(path)
        if not root:
            problems.append((
                "SOURCE-NOT-GIT",
                src["id"],
                f"{path} is not inside a Git clone",
            ))
            continue
        key = str(root.resolve())
        if key in seen:
            continue
        seen.add(key)

        def git(*args):
            return subprocess.run(
                ["git", "-C", str(root), *args],
                capture_output=True,
                text=True,
            )

        branch = git("symbolic-ref", "--quiet", "--short", "HEAD")
        if branch.returncode != 0 or not branch.stdout.strip():
            head = git("rev-parse", "--short", "HEAD").stdout.strip() or "unknown"
            problems.append((
                "SOURCE-DETACHED",
                src["id"],
                f"{root} is detached at {head}; use a branch-backed canonical clone",
            ))

        git_dir = git("rev-parse", "--git-dir")
        git_common = git("rev-parse", "--git-common-dir")
        if git_dir.returncode == 0 and git_common.returncode == 0:
            def resolved_git_path(value):
                candidate = Path(value.strip())
                if not candidate.is_absolute():
                    candidate = root / candidate
                return candidate.resolve()

            if resolved_git_path(git_dir.stdout) != resolved_git_path(git_common.stdout):
                problems.append((
                    "SOURCE-WORKTREE",
                    src["id"],
                    f"{root} is a linked worktree; configure a normal canonical clone",
                ))

        status = subprocess.run(
            ["git", "-C", str(root), "status", "--porcelain"],
            capture_output=True,
            text=True,
        )
        if status.returncode == 0 and status.stdout.strip():
            problems.append((
                "SOURCE-DIRTY",
                src["id"],
                f"{root} has uncommitted source changes",
            ))

        upstream = git("rev-parse", "--abbrev-ref", "--symbolic-full-name", "@{u}")
        if upstream.returncode != 0:
            problems.append((
                "SOURCE-NO-UPSTREAM",
                src["id"],
                f"{root} has no configured upstream",
            ))
            continue
        counts = git("rev-list", "--left-right", "--count", "HEAD...@{u}")
        if counts.returncode != 0 or len(counts.stdout.split()) != 2:
            problems.append((
                "SOURCE-NO-UPSTREAM",
                src["id"],
                f"{root} upstream comparison failed",
            ))
            continue
        ahead, behind = (int(value) for value in counts.stdout.split())
        if ahead and behind:
            kind = "SOURCE-DIVERGED"
            detail = (f"{root} is {ahead} commit(s) ahead and {behind} behind "
                      f"{upstream.stdout.strip()}")
        elif ahead:
            kind = "SOURCE-AHEAD"
            detail = f"{root} is {ahead} commit(s) ahead of {upstream.stdout.strip()}"
        elif behind:
            kind = "SOURCE-BEHIND"
            detail = f"{root} is {behind} commit(s) behind {upstream.stdout.strip()}"
        else:
            continue
        problems.append((kind, src["id"], detail))
    return problems


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
            installed |= {l.name for l in root.iterdir() if is_installed_skill_link(l)}
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
                                         "real file/dir blocks the mount (symlinks only are replaceable)"))
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
            if is_installed_skill_link(link) and link.exists():
                hashes[rname] = skill_md_hash(link)
        if len({h for h in hashes.values() if h}) > 1:
            problems.append(("PARITY", name, f"SKILL.md differs across runtimes: {hashes}"))
        parity[name] = {"roots": hashes, "consistent": len({h for h in hashes.values() if h}) <= 1}
    return problems, installed, collisions, parity


def cmd_doctor(roots, sources, as_json=False, strict=False):
    problems, installed, collisions, parity = doctor_problems(roots, sources)
    problems.extend(source_git_problems(sources))
    for peer in other_skillbox_on_path():
        problems.append((
            "PATH-SHADOW", "skillbox",
            f"another skillbox on PATH: {peer} — put this tool's dir first "
            f"(npm/Homebrew name collision)",
        ))
    blocking_kinds = _STRICT_BLOCKING_DOCTOR_KINDS if strict else _BLOCKING_DOCTOR_KINDS
    blocking = [p for p in problems if p[0] in blocking_kinds]
    path_shadows = [p for p in problems if p[0] == "PATH-SHADOW"]
    if as_json:
        print(json.dumps({
            "roots": {k: str(v) for k, v in roots.items()},
            "skills_installed": len(installed),
            "problems": [{"kind": k, "where": w, "detail": d} for k, w, d in problems],
            "blocking": len(blocking),
            "strict": strict,
            "parity": parity,
        }, indent=2))
    else:
        for kind, where, detail in problems:
            print(f"{kind:12} {where}  {detail}")
        extra = ""
        if collisions:
            extra += f"; {len(collisions)} shadow(s)"
        if path_shadows:
            extra += f"; {len(path_shadows)} PATH-SHADOW(s)"
        print(f"doctor: {len(blocking)} blocking problem(s)" if blocking
              else f"doctor: clean ({len(installed)} skills across "
                   f"{sum(1 for r in roots.values() if r.is_dir())} runtimes)"
                   + extra)
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
    # Absolute, not resolved: the manifest is read from any working directory,
    # but a user's symlinked source dir should stay spelled the way they gave it.
    p = Path(os.path.expanduser(path)).absolute()
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


def _dispatch_command(args, roots, sources):
    """Run one already-parsed command against one manifest snapshot."""
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
    elif cmd == "retire" and len(args) >= 2:
        cmd_retire(roots, sources, args[1], opt("--source"))
    elif cmd == "promote" and len(args) >= 2:
        cmd_promote(roots, sources, args[1], opt("--to"))
    elif cmd == "source" and args[1:2] == ["add"] and len(args) >= 4:
        p = opt("--priority")
        try:
            priority = int(p) if p else None
        except ValueError:
            sys.exit(f"--priority needs an integer, got {p!r}")
        cmd_source_add(args[2], args[3], priority)
    elif cmd == "source" and args[1:2] == ["rm"] and len(args) >= 3:
        cmd_source_rm(args[2])
    elif cmd == "diff" and len(args) >= 2:
        cmd_diff(sources, args[1])
    elif cmd == "log" and len(args) >= 2:
        cmd_log(sources, args[1])
    elif cmd == "scrub":
        scrub_name = args[1] if len(args) >= 2 and not args[1].startswith("--") else None
        sys.exit(cmd_scrub(sources, scrub_name, opt("--to"), dry_run="--dry-run" in args,
                           as_json="--json" in args))
    elif cmd == "doctor" and len(args) >= 2 and args[1] == "scrub":
        scrub_name = args[2] if len(args) >= 3 and not args[2].startswith("--") else None
        sys.exit(cmd_scrub(sources, scrub_name, opt("--to"), dry_run="--dry-run" in args,
                           as_json="--json" in args))
    elif cmd in ("doctor", "audit"):
        sys.exit(cmd_doctor(
            roots,
            sources,
            as_json="--json" in args,
            strict="--strict" in args,
        ))
    elif cmd == "sync":
        cmd_sync(roots, sources, no_pull="--no-pull" in args)
    elif cmd == "update":
        sys.exit(cmd_update(sources, "--dry-run" in args))
    else:
        sys.exit(f"unknown or incomplete command: {' '.join(args)!r}\n{__doc__}")


_MUTATING_COMMANDS = frozenset({
    "new", "add", "rm", "retire", "promote", "source", "sync", "update",
})

_HELP_OPTIONS = frozenset({"--help", "-h"})

# Manual dispatch must still have an explicit option contract.  Validate it
# before manifest access or mutation-lock acquisition so a typo can never turn
# a preview/help request into a real pull, relink, or manifest edit.
_COMMAND_OPTIONS = {
    ("list",): {},
    ("new",): {"--repo": True},
    ("add",): {"--source": True},
    ("rm",): {},
    ("retire",): {"--source": True},
    ("promote",): {"--to": True},
    ("source", "add"): {"--priority": True},
    ("source", "rm"): {},
    ("diff",): {},
    ("log",): {},
    ("scrub",): {"--to": True, "--dry-run": False, "--json": False},
    ("doctor", "scrub"): {
        "--to": True, "--dry-run": False, "--json": False,
    },
    ("doctor",): {"--json": False, "--strict": False},
    ("audit",): {"--json": False, "--strict": False},
    ("sync",): {"--no-pull": False},
    ("update",): {"--dry-run": False},
}


def _command_key(args):
    if args[0] == "source" and len(args) >= 2:
        return ("source", args[1])
    if args[0] == "doctor" and args[1:2] == ["scrub"]:
        return ("doctor", "scrub")
    return (args[0],)


def _validate_cli_options(args):
    """Reject unknown flags before any config, lock, source, or mount access."""
    command = _command_key(args)
    allowed = _COMMAND_OPTIONS.get(command)
    if allowed is None:
        sys.exit(f"unknown or incomplete command: {' '.join(args)!r}\n{__doc__}")

    i = 1
    while i < len(args):
        token = args[i]
        if token in _HELP_OPTIONS:
            i += 1
            continue
        if token in allowed:
            if not allowed[token]:
                i += 1
                continue
            value_at = i + 1
            if (value_at >= len(args) or args[value_at].startswith("--")
                    or args[value_at] in _HELP_OPTIONS):
                sys.exit(f"{token} needs a value")
            i += 2
            continue
        if token.startswith("-"):
            sys.exit(f"unknown option for {' '.join(command)}: {token}")
        i += 1


def main():
    args = sys.argv[1:]
    if not args:
        print(__doc__)
        return
    # Version is identity-only: no manifest, no home config, no fleet touch.
    if args[0] == "--version":
        if len(args) != 1:
            sys.exit(f"unknown option for --version: {args[1]}")
        print(f"skillbox {VERSION}")
        return
    if args[0] in _HELP_OPTIONS:
        if len(args) != 1:
            sys.exit(f"unknown option for {args[0]}: {args[1]}")
        print(__doc__)
        return
    _validate_cli_options(args)
    if any(arg in _HELP_OPTIONS for arg in args[1:]):
        print(__doc__)
        return
    if not MANIFEST.exists():
        sys.exit(f"no manifest at {MANIFEST}\n"
                 f"create it:  mkdir -p {MANIFEST.parent} && cp skills.toml.example {MANIFEST}\n"
                 "then edit the [sources.*] paths to point at your skill repos.")
    if args[0] in _MUTATING_COMMANDS:
        # Acquire first, then take a fresh snapshot. Otherwise a command that
        # waited behind `retire` could act on the manifest it read beforehand.
        with mutation_lock():
            roots, sources = load()
            return _dispatch_command(args, roots, sources)
    roots, sources = load()
    return _dispatch_command(args, roots, sources)


if __name__ == "__main__":
    main()
