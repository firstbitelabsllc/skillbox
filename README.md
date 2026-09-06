# Skillbox

**Write a skill once. Every coding tool you use reads the same folder.**

Real session, trimmed:

```console
$ skillbox new hello --repo demo
created …/source/hello/SKILL.md  (source: demo)
linked claude/hello -> …/source/hello
linked codex/hello -> …/source/hello
mounted into 2 runtime root(s)

$ skillbox list
hello                            demo

$ readlink …/claude/hello …/codex/hello
…/source/hello
…/source/hello

$ rm -r …/source/hello && skillbox doctor
UNMANAGED    hello  installed but no source repo owns it
BROKEN       claude/hello  -> …/source/hello
BROKEN       codex/hello  -> …/source/hello
doctor: 2 blocking problem(s)
```

Claude Code, Codex, and Cursor each read their own skills directory, so I
had three copies and every fix missed one. Skillbox symlinks one source
folder into each tool's directory; `doctor` says when a link is broken or
stale. That's the product.

[Install](#install) · [Try it](#try-it) ·
[Commands](#verbs) ·
[Security](SECURITY.md) · [Issues](https://github.com/firstbitelabsllc/skillbox/issues)

<p align="center">
  <a href="https://github.com/firstbitelabsllc/skillbox/actions/workflows/ci.yml"><img src="https://github.com/firstbitelabsllc/skillbox/actions/workflows/ci.yml/badge.svg" alt="CI" /></a>
  <a href="LICENSE"><img src="https://img.shields.io/badge/license-MIT-blue.svg" alt="MIT License" /></a>
  <img src="https://img.shields.io/badge/python-3.11%2B-blue" alt="Python 3.11+" />
  <img src="https://img.shields.io/badge/daemon-none-success" alt="No daemon" />
</p>

## Four words

- **source**: a folder of `SKILL.md` folders, usually a Git clone.
- **root**: a tool's skills directory, like `~/.claude/skills`.
- **manifest**: `~/.skillbox/skills.toml`: sources, roots, and which source
  wins a name.
- **doctor**: read-only link check; nonzero exit on a broken, drifted, or
  blocked mount.

## Install

Python 3.11+, Bash, macOS or Linux. Git for `update`.

```bash
git clone https://github.com/firstbitelabsllc/skillbox.git
cd skillbox
mkdir -p ~/.local/bin ~/.skillbox
ln -s "$PWD/bin/skillbox.py" ~/.local/bin/skillbox
export PATH="$HOME/.local/bin:$PATH"
cp skills.toml.example ~/.skillbox/skills.toml   # then edit the paths
skillbox sync --no-pull
skillbox doctor
```

Unknown command? An npm package shares the name; put `~/.local/bin` first
on `PATH`.

## Try it

One skill, two scratch roots, nothing under `~` changes:

```bash
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
export SKILLBOX_MANIFEST="$skillbox_demo/skills.toml"
skillbox new hello --repo demo
skillbox list
readlink "$skillbox_demo/claude/hello" "$skillbox_demo/codex/hello"
rm -r "$skillbox_demo/source/hello" && skillbox doctor
```

The demo source has no Git, so `doctor` adds a `SOURCE-NOT-GIT` line.

## Why not…

| | |
|---|---|
| Copy into each tool | copies drift; the edited one isn't the one read |
| `ln -s` by hand | nothing tells you when a folder moves |
| A marketplace | someone else's registry; this links what's on disk |

## Not built, on purpose

No registry, no fetcher, no model calls, no Windows. It doesn't vet a
skill's contents; read a source before you mount it. `scrub` blocks
`promote` on folders marked private: a tripwire, not a scanner.
[SECURITY.md](SECURITY.md) has the boundaries.

## Feedback

A mount that behaves differently in one tool than another is the bug I most
want. Open an [issue](https://github.com/firstbitelabsllc/skillbox/issues)
with the smallest manifest that shows it. Tests: `bash tests/run_all.sh`.

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

Skillbox does not keep a provenance registry for runtime-root symlinks. `add` and `sync` may replace any symlink occupying a configured `<root>/<name>` slot when its target differs, and `rm` may unlink any symlink in the named slot. A real file or directory is refused and left untouched. `sync` prunes a dangling link only when its target is inside a configured source and that source's configured path still exists; unrelated dangling links in a runtime root are preserved.

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
