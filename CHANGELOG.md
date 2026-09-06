# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project aims to follow [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Fixed

- `sync` no longer prunes a single-skill source's mounts while its repo is absent (unmounted or mid-move); a relative dangling link into a present source is now pruned.
- `source add` stores the source path absolute, so the source resolves from any working directory.
- `retire` refuses cleanly, and still prints the recovery path for slots it already parked, when a runtime root cannot hold a recovery journal.
- `list` reports an empty `[roots]` table instead of raising `StopIteration`.
- `source add --priority` rejects a non-integer instead of raising `ValueError`.

### Changed

- README opens with real `new`/`list`/`readlink`/`doctor` output and about 40% less prose.

### Removed

- Retired the localhost management GUI and `ui` command. The manifest-driven CLI remains the sole mount, source-precedence, repair, and health interface.

## [1.0.0] — 2026-07-29

### Added

- Local-first skill mount CLI: one `SKILL.md` source symlink-fanned into Claude Code, Codex, and Cursor runtime roots without copying.
- Core verbs: `list`, `new`, `add`, `rm`, `promote`, `scrub`, `source add` / `source rm`, `diff`, `log`, `doctor`, `sync`, `update`, `ui`.
- Manifest-driven local sources and roots (`~/.skillbox/skills.toml`).
- Private-boundary scrub (`KEEP-PRIVATE`, `.keep-private`, `*-leo`) that blocks unsafe `promote` targets, including `--to org`.
- `promote --to org` offline off-ramp: writes a local Claude Code plugin manifest and prints a draft marketplace PR command; never sends or publishes.
- Localhost management GUI on `127.0.0.1` with POST-only mutations, CSRF token, and DNS-rebinding Host checks.
- Hermetic test suite (`bash tests/run_all.sh`) for macOS/Linux Python/Bash environments.

### Security

- Skill name path-segment guard against traversal.
- Configured runtime slots may replace or unlink any occupying named symlink; real files/dirs and source folders are left untouched. Automatic prune is limited to dangling links whose targets are inside configured sources.
- Network boundaries are explicit: default `sync` and `update` pull configured Git sources, while `update --dry-run` fetches without changing the source working tree.
- UI bound to loopback with CSRF + same-origin / loopback-Host walls.
