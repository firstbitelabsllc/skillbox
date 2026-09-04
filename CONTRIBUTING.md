# Contributing

Thanks for helping improve Skillbox. Keep changes small, local-first, and covered by the hermetic suite.

## Prerequisites

- Python 3.11 or 3.12 (the CI-tested interpreters); older Python may work with `tomli` but is not tested
- Bash
- macOS or Linux (the suite is not claimed for Windows)
- `git` for source-backed scenarios

## Setup

```bash
git clone https://github.com/firstbitelabsllc/skillbox.git
cd skillbox
mkdir -p ~/.local/bin ~/.skillbox \
  ~/.claude/skills ~/.agents/skills ~/.cursor/skills ~/.codex/skills
ln -s "$PWD/bin/skillbox.py" ~/.local/bin/skillbox  # fails safely if occupied
export PATH="$HOME/.local/bin:$PATH"
```

For day-to-day work you do **not** need a real `~/.skillbox/skills.toml`. Tests inject `$SKILLBOX_MANIFEST` pointing at a sandbox and never touch your live fleet.

## Run tests

Full suite (all hermetic):

```bash
bash tests/run_all.sh
```

One scenario:

```bash
bash tests/test_smoke.sh
bash tests/test_scrub.sh
python3 tests/test_unit.py
```

Expectations:

- Exit nonzero on any failure.
- No network required for the suite (org promote prints a draft; it does not open a PR).
- No writes under your real `~/.claude/skills`, `~/.agents/skills`, etc.

## What to change carefully

- **Name / path guards** — skill names become directory leaves and symlink names; keep the traversal wall intact.
- **Mount helpers** — configured symlink slots have no ownership metadata, so any symlink there may be replaced or unlinked; never clobber a real file/dir or delete a source folder.
- **Network verbs** — default `sync` and `update` may contact Git remotes; `update --dry-run` fetches, while `sync --no-pull` is the local-only path.
- **Private boundary** — `scrub` / promote guards for `KEEP-PRIVATE`, `.keep-private`, and `*-leo`.
- **`promote --to org`** — draft print only; never auto-send.

## Docs-only PRs

Public front-door docs live in `README.md`, `SECURITY.md`, `CONTRIBUTING.md`, and `CHANGELOG.md`. Preserve exact CLI verb behavior when editing the README.

## License

By contributing, you agree your changes are licensed under the MIT License in [LICENSE](LICENSE).
