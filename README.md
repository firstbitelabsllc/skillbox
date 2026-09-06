# Skillbox

**Write a skill once. Every coding tool you use gets the same file.**

I keep my agent skills (the `SKILL.md` folders Claude Code, Codex, and Cursor
all read) in a Git repo. For a while each tool had its own copy, and every
fix meant three edits and a forgotten one. Skillbox is the small Python
script I wrote to stop that: it symlinks one source folder into each tool's
skills directory, and `doctor` tells me when a link is broken or pointing
somewhere stale.

```text
your-skills/review/SKILL.md
        ├── ~/.claude/skills/review  → same folder
        ├── ~/.agents/skills/review  → same folder   (Codex)
        └── ~/.cursor/skills/review  → same folder
```

A short TOML file names your source repos, the host directories, and which
source wins when two define the same name. That's the whole product. No
registry, no background fetcher, no model calls.

Tested on Python 3.11 and 3.12 with Bash on macOS and Linux. Git is needed
for `update`. I haven't tried Windows.

## Try it without touching your real skills

This makes one skill and mounts it into two scratch folders. Nothing under
your home directory changes.

```bash
git clone https://github.com/firstbitelabsllc/skillbox.git
cd skillbox
skillbox_demo=$(mktemp -d)
mkdir -p "$skillbox_demo/claude" "$skillbox_demo/codex"
cat > "$skillbox_demo/skills.toml" <<EOF
[roots]
claude = "$skillbox_demo/claude"
codex = "$skillbox_demo/codex"
[sources.demo]
path = "$skillbox_demo/source"
priority = 1
EOF
SKILLBOX_MANIFEST="$skillbox_demo/skills.toml" python3 bin/skillbox.py new hello --repo demo
SKILLBOX_MANIFEST="$skillbox_demo/skills.toml" python3 bin/skillbox.py list
readlink "$skillbox_demo/claude/hello"
readlink "$skillbox_demo/codex/hello"
```

Both `readlink` lines print the same folder. Edit that `SKILL.md` and both
mounts see it, because they are the same file. The demo source isn't a Git
clone on purpose; for real use, point the manifest at your own repos.

## Install it for real

From the clone:

```bash
mkdir -p ~/.local/bin ~/.skillbox
ln -s "$PWD/bin/skillbox.py" ~/.local/bin/skillbox
export PATH="$HOME/.local/bin:$PATH"
cp skills.toml.example ~/.skillbox/skills.toml   # then edit the paths
skillbox sync --no-pull
skillbox doctor
```

Edit the example paths to your actual skill repos before `sync`. Create any
host directory that doesn't exist yet; Skillbox skips missing roots rather
than making them. `sync --no-pull` relinks without touching Git. It will
replace a symlink already sitting in a skill slot, but it refuses to touch a
real file or folder, so look at your roots first.

If `skillbox doctor` says unknown command, run `command -v skillbox`. There
is an unrelated npm package with the same name; put `~/.local/bin` first on
your `PATH`.

## Day to day

| I want to… | I run |
| --- | --- |
| See every skill and where it comes from | `skillbox list` |
| Mount a skill that already exists in a source | `skillbox add review --source personal` |
| Start a new skill in my source repo | `skillbox new review --repo personal` |
| Check every link | `skillbox doctor` |
| See what a `git pull` on my sources would change | `skillbox update --dry-run` |
| Relink without fetching | `skillbox sync --no-pull` |

Plain `sync` pulls your source repos first. `doctor --strict` also refuses
dirty, detached, or diverged sources and any skill sitting in a root that
Skillbox doesn't manage.

## What it doesn't do

Skillbox manages symlinks. It does not vet what's inside a skill. Read a
source before you mount it. There's a `scrub` command that catches folders
you've marked private so `promote` can't leak them, but that is a tripwire,
not a secret scanner. [SECURITY.md](SECURITY.md) has the exact boundaries.

If a mount behaves differently in one tool than another, that's the bug I
most want to hear about. Open an
[issue](https://github.com/firstbitelabsllc/skillbox/issues) with the
smallest manifest that shows it. Tests are hermetic and run with:

```bash
bash tests/run_all.sh
```

[Contributing](CONTRIBUTING.md) · [MIT](LICENSE)

<details>
<summary>Command details, source precedence, retirement, and uninstall</summary>

## Uninstall

These steps unmount named runtime slots, then remove the CLI and optional config. They never delete your source skill folders.

```bash
# 1) Unmount skills from runtime roots (source folders stay on disk)
skillbox list                    # see what’s mounted
skillbox rm <name>               # repeat per skill; or leave mounts if you still want them

# 2) Remove the CLI symlink and optional config
readlink ~/.local/bin/skillbox   # inspect before removing; it should name this clone
test -L ~/.local/bin/skillbox && rm ~/.local/bin/skillbox
rm -f ~/.skillbox/skills.toml    # optional; delete only if you want config gone
rmdir ~/.skillbox 2>/dev/null || true

# 3) Optionally remove the clone itself (your skill repos are separate)
# rm -rf /path/to/skillbox
```

`skillbox rm <name>` unlinks any symlink occupying `<configured-root>/<name>`, regardless of which tool created it. It ignores a real file or directory at that slot and never deletes the `SKILL.md` source directory.

## Verbs

```
skillbox list                       installed skills + the source repo each resolves from
skillbox new <name> [--repo ID]     scaffold a new skill and link it into every runtime
skillbox add <name> [--source ID]   link an existing source skill into every runtime
skillbox rm <name>                  unlink any symlink in the named runtime slots; leave the source untouched
skillbox retire <name> --source ID  safely unmount an excluded source leaf; refuses foreign/replacement links
skillbox promote <name> --to ID     move a skill to another source repo and relink (reversible)
skillbox promote <name> --to org    publish to your org's plugin marketplace (prints a DRAFT PR; never sends)
skillbox scrub [--dry-run] [--json]   list KEEP-PRIVATE / *-leo skills that would leak on promote
skillbox scrub <name> --to ID         check one promote target; non-dry-run exits 1 if blocked
skillbox source add <id> <path>     register a local source repo (e.g. a teammate's clone)
skillbox diff <name> | log <name>   the skill folder's uncommitted diff / commit history
skillbox doctor [--json] [--strict] check mounts and Git source health; strict also refuses unmanaged/shadowed/non-Git sources
skillbox sync [--no-pull]           pull Git sources by default, then relink/prune only if every update succeeds
skillbox update [--dry-run]         pull Git sources; --dry-run fetches and previews SKILL.md diffs; failures exit nonzero
```

Skillbox does not keep a provenance registry for runtime-root symlinks. `add` and `sync` may replace any symlink occupying a configured `<root>/<name>` slot when its target differs, and `rm` may unlink any symlink in the named slot. A real file or directory is refused and left untouched. `sync` prunes a dangling link only when its target is inside a configured source and the source parent still exists; unrelated dangling links in a runtime root are preserved.

`update` and the default `sync` explicitly contact each configured Git source’s remote (`git pull --ff-only`). `update --dry-run` still runs `git fetch`, which can update remote-tracking refs, but does not change the source working tree. Use `sync --no-pull` for a local-only relink/prune pass. Skillbox has no background fetcher and no remote-catalog install path.

`doctor` always refuses unsafe mount drift and reports source provenance as
diagnostics. `doctor --strict` also refuses source states that cannot be
fast-forwarded without judgment (missing, dirty, detached, linked-worktree,
ahead, behind, or diverged clones), unmanaged runtime skills, same-name source
shadows, non-Git sources, sources without an upstream, and another `skillbox`
executable shadowing this one on `PATH`. Source checks are read-only and compare
the current local upstream ref; run `update --dry-run` first when you need a
fresh network observation.

`promote --to org` emits a Claude Code plugin manifest into the skill folder and **prints** a draft marketplace registration plus a `gh pr create --draft` command for `$SKILLBOX_ORG_REPO`. It never opens, pushes, or publishes that PR — you review and run it yourself.

## Where a skill lives = how it's shared

A skill’s reach is simply **which source repo holds the folder**, shown as the tag in each `list`/`doctor` row (e.g. `deploy  team`). `new` creates a skill in your own repo by default; `promote` is the one explicit command that moves a skill to a shared source and relinks it — reversible. `scrub` audits private-boundary skills (`KEEP-PRIVATE`, `*-leo`, `.keep-private`) and blocks `promote` when a move would leak them.

Sources resolve in priority order; the first to define a name wins (a suffix like `-mine` lets you keep your own version of a shared skill). Run `skillbox doctor` any time to confirm every runtime is mounted consistently.

To retire a compatibility alias without deleting its source folder, add an
`exclude = ["old-alias"]` list to that source in `skills.toml`, run
`skillbox retire old-alias --source <id>`, then run `skillbox sync --no-pull`.
Excluded leaves are absent from `list`, `add`, and future sync plans, so sync
will not recreate them. Retirement only parks slots that still point at that
specific source leaf; it refuses real files, a different tool's symlink, or an
active lower-priority copy that would otherwise take over the same name.
Rather than deleting a mutable runtime link, `retire` parks each accepted link
in a fresh hidden recovery folder within that same runtime root. The old route
is no longer active, while its exact link remains recoverable; if anything
changes mid-operation, Skillbox stops and prints the retained recovery path.
It verifies that the named journal is still the exact directory it holds
and that the runtime-root path still names its held directory before reporting
that path; if another same-user process renames either one, it fails without
claiming the stale location as a receipt. The cooperative lock serializes
normal Skillbox writers, but no local tool can preserve a recovery link after a
separate same-user process deletes it after retirement completes.
Each normal mutating command takes one short cooperative lock before it reads
the manifest, so a waiting `sync` cannot revive an alias retired by another
Skillbox command. Retirement additionally uses the operating system's
no-replace move primitive: a non-cooperating filesystem change is captured or
refused and reported, never overwritten. Skillbox deliberately leaves hidden
recovery journals behind on a failed retirement rather than racing a cleanup.
Skillbox also refuses to create or promote a skill into a source that excludes
its name. `rm` remains the deliberately broad manual unlink command. Exclusion
is per source, so use `retire` to preflight every configured source before
calling a route fully retired.
## Runtime roots

| Root | Runtime |
|---|---|
| `~/.claude/skills` | Claude Code |
| `~/.agents/skills` | Codex (the dir Codex actually scans) |
| `~/.cursor/skills` | Cursor |
| `~/.codex/skills` | Cursor-compat / legacy (Codex does **not** scan this) |

## Configuration

| Env var | Purpose |
|---|---|
| `SKILLBOX_MANIFEST` | path to the manifest (default `~/.skillbox/skills.toml`) |
| `SKILLBOX_STATE_DIR` | runtime lock directory (default: the manifest directory); set this when the manifest is read from a source-controlled checkout |
| `SKILLBOX_ORG_REPO` | `owner/repo` of your plugin marketplace for `promote --to org` |
| `SKILLBOX_DEFAULT_SOURCE` | default source id for `skillbox new` (default `personal`) |

See [skills.toml.example](skills.toml.example) for the manifest shape. Sources are **local paths only**.


</details>

## Related tools

- [Shadow](https://github.com/firstbitelabsllc/shadow) — one durable plan, atomic claims, and proof-gated completion for the AI coding agents whose skills Skillbox mounts.
- [Claudux](https://github.com/firstbitelabsllc/claudux) — keeps a VitePress docs site current as the code changes, using your own Claude or Codex CLI.
