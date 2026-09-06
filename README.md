# Skillbox

**Edit a skill once. Use it in Claude Code, Codex, and Cursor.**

If each coding tool has its own copy of your skills, one fix becomes several
edits. Skillbox mounts the same local `SKILL.md` folder into each configured
tool with symlinks. Your Git repository owns the source; every mount points
back to it.

```text
your-skills/review/SKILL.md
        ├── Claude Code → same folder
        ├── Codex       → same folder
        └── Cursor      → same folder
```

Use it for skills you already keep locally. A small TOML file names the sources,
host directories, and which source wins when names overlap. `doctor` shows
broken links, missing mounts, and drift before you rely on them.

Python 3.11 or 3.12 and Bash on macOS/Linux are the CI-tested combinations.
Git is required for source updates. Windows is not currently tested.

## Try it in a temporary folder

This example creates one skill and mounts it into two scratch directories.
It does not touch your installed agent skills or call a model.

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

Both links should print the same source folder. Edit its `SKILL.md`; both
mounts see the edit immediately. The temporary source is deliberately not a
Git clone. For everyday use, point the manifest at your own durable clones.

## Install for your coding tools

From the Skillbox clone:

```bash
mkdir -p ~/.local/bin ~/.skillbox
ln -s "$PWD/bin/skillbox.py" ~/.local/bin/skillbox
export PATH="$HOME/.local/bin:$PATH"
```

The symlink command refuses an occupied destination. If you already have a
manifest, edit it in place. Otherwise copy
[skills.toml.example](skills.toml.example) to `~/.skillbox/skills.toml`.
Replace its example source paths with your actual skill repositories before
running these commands. Create any configured host directories that do not
exist yet; Skillbox skips missing roots.

```bash
skillbox sync --no-pull
skillbox doctor
```

`sync --no-pull` mounts local sources without updating Git. It may replace an
existing symlink in a configured skill slot; it refuses a real file or folder.
Review the configured roots first. `doctor --strict` also checks source Git
health and rejects unmanaged or shadowed sources.

If `doctor` is an unknown command, check `command -v skillbox`: an unrelated
npm package has the same name. Put `~/.local/bin` first on `PATH`.

## Everyday commands

| Want to… | Run |
| --- | --- |
| See each skill and its source | `skillbox list` |
| Mount one existing skill | `skillbox add review --source personal` |
| Create a skill in your source | `skillbox new review --repo personal` |
| Check every mount | `skillbox doctor` |
| Preview source updates | `skillbox update --dry-run` |
| Relink without fetching | `skillbox sync --no-pull` |

`update --dry-run` fetches Git refs. Default `sync` pulls source repositories;
use `--no-pull` when you only want local mount repair.

## Safety and contribution

Skillbox manages symlinks, not skill trust. Review a source before mounting it.
Its private-content markers help catch accidental promotion, but they are not
a complete secret scanner. See [Security](SECURITY.md) for exact boundaries.

Found a mount that behaves differently across tools? Open an
[issue](https://github.com/firstbitelabsllc/skillbox/issues) with the smallest
manifest that reproduces it, replacing personal paths with examples.
[Contributing](CONTRIBUTING.md) covers the hermetic tests and focused changes.

```bash
bash tests/run_all.sh
```

[MIT License](LICENSE).

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
