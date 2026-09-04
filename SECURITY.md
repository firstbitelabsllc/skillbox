# Security

Skillbox is a local package manager for AI-agent skill folders. Sources are paths already on your machine. It has no background fetcher or remote-catalog install path, but the explicit `update` and default `sync` commands do contact configured Git remotes.

## Symlinks and path traversal

- Skill names are a single safe path segment: letters, digits, `._-`, max 64 chars. Names with `/`, `..`, or absolute paths are refused (`require_name`).
- Mounts are absolute symlinks from each runtime root leaf to the resolved source directory. Skillbox has no symlink-provenance registry: `add` and `sync` may replace any symlink occupying a configured `<root>/<name>` slot when its target differs.
- `skillbox rm <name>` unlinks any symlink at that named slot, even if another tool created it. A real file or directory in the slot is refused and left untouched.
- `sync` may prune a dangling symlink only when its target is inside a configured source and the target’s parent still exists. Dangling links outside configured sources are preserved. Source skill folders are never deleted by mount/unmount verbs.

## Local manifests

- The fleet map is `~/.skillbox/skills.toml` (override with `$SKILLBOX_MANIFEST` for tests).
- `[sources.*]` entries are **local filesystem paths** only — your repos and any clone already on disk. There is no remote “install from URL” path in the CLI.
- A malformed or missing manifest fails closed before fleet commands run. The identity-only `--version` path deliberately does not read a manifest.

## Network and source trust

- `update` and the default `sync` run `git pull --ff-only` for each distinct configured Git checkout. They therefore contact the checkout’s configured remote and may change its working tree through a fast-forward.
- `update --dry-run` still runs `git fetch`. It can update remote-tracking refs, but it does not update the source working tree.
- `sync --no-pull` skips the Git update and performs only local resolution, relinking, and pruning.
- A mounted skill becomes immediately visible to the configured agent runtimes. Review the local source and its Git remote before mounting or updating it.

## Private-boundary scrub

Before a skill leaves a private source, Skillbox blocks promote leaks:

| Marker | Meaning |
|---|---|
| `KEEP-PRIVATE` (or related markers) in `SKILL.md` | must not promote to shared/org |
| `.keep-private` file in the skill folder | same |
| name ending in `-leo` | private overlay suffix |

- `skillbox scrub` lists would-leak skills and paths; without `--dry-run` it exits `1` when findings exist.
- `skillbox promote … --to <shared-id>` and `promote … --to org` refuse private-boundary skills.
- `promote --to org` only writes a local plugin manifest and **prints** a draft PR command; it never sends, pushes, or publishes.

## Reporting issues

If you find a mount, path, network, or private-boundary bug, open an issue on the canonical repository once it is public: <https://github.com/firstbitelabsllc/skillbox>. Do not attach private skill contents in the report.
