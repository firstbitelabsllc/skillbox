# Skillbox reference

[Back to the README](../README.md)

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
in one private recovery root under `$SKILLBOX_STATE_DIR` (normally
`~/.skillbox/recovery`). Skillbox refuses to use it unless it sits outside every
configured runtime root, including broader nested roots. The old route is no
longer active, while its exact link remains recoverable without a recursive
compatibility loader rediscovering it as a skill; if anything changes mid-
operation, Skillbox stops and prints the retained recovery path.
When a retired runtime link used a relative target, the journal's `mount` is
rebased to that verified absolute destination and the original spelling is
preserved alongside it as `raw-mount`; moving the journal therefore cannot
silently change what recovery means. Each new retirement journal also records
the original slot and raw link target in `origin.json`.
`sync --no-pull` also relocates any journal made by the prior in-root layout,
or refuses rather than claiming a clean runtime if it cannot verify that
relocation. `doctor` reports an in-root legacy journal as blocking
`LEGACY-RECOVERY` until that sync completes.
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
| `~/.agents/skills` | Codex shared skill directory |
| `~/.cursor/skills` | Cursor |
| `~/.codex/skills` | Host-specific Codex/Cursor installations; check your configured roots |

## Configuration

| Env var | Purpose |
|---|---|
| `SKILLBOX_MANIFEST` | path to the manifest (default `~/.skillbox/skills.toml`) |
| `SKILLBOX_STATE_DIR` | runtime lock and recovery directory (default: the manifest directory); set this when the manifest is read from a source-controlled checkout |
| `SKILLBOX_ORG_REPO` | `owner/repo` of your plugin marketplace for `promote --to org` |
| `SKILLBOX_DEFAULT_SOURCE` | default source id for `skillbox new` (default `personal`) |

See [skills.toml.example](../skills.toml.example) for the manifest shape. Sources are **local paths only**.

Set `hosts = ["cursor"]` in a source table to mount that source only in the
`cursor` root. Host names must be distinct existing `[roots]` keys; an empty
list is invalid. Omit `hosts` to keep mounting in every root. Source precedence
still elects one winner for each skill across the manifest. `add` and `sync`
refuse existing same-name links outside that winner's target hosts, and `doctor`
reports them as drift. Inspect and remove those links explicitly; `retire`
continues to check every root for an excluded source's old mounts.




## Related tools

- [Shadow](https://github.com/firstbitelabsllc/shadow) — one durable plan, atomic claims, and proof-gated completion for the AI coding agents whose skills Skillbox mounts.
- [Claudux](https://github.com/firstbitelabsllc/claudux) — keeps a VitePress docs site current as the code changes, using your own Claude or Codex CLI.
