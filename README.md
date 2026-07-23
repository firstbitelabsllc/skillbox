# skillbox

A toolbox for your AI coding-agent skills. One `SKILL.md` folder works in **Claude Code, Codex, and Cursor** at once — skillbox symlink-fans a single source folder into every runtime, so an edit is live everywhere with no reinstall.

## Install

```bash
git clone https://github.com/leojkwan/skillbox.git
mkdir -p ~/.local/bin ~/.skillbox                            # create target dirs first
ln -s "$PWD/skillbox/bin/skillbox.py" ~/.local/bin/skillbox  # ~/.local/bin must be on your $PATH
cp skillbox/skills.toml.example ~/.skillbox/skills.toml      # then edit the [sources.*] paths
skillbox doctor                                              # run from anywhere
```

Python 3.11+ (uses the stdlib `tomllib`); on older Python, `pip install tomli`. No other dependencies.

## Verbs

```
skillbox list                       installed skills + the source repo each resolves from
skillbox new <name> [--repo ID]     scaffold a new skill and link it into every runtime
skillbox add <name> [--source ID]   link an existing source skill into every runtime
skillbox rm <name>                  remove a skill's symlinks (the source folder is untouched)
skillbox promote <name> --to ID     move a skill to another source repo and relink (reversible)
skillbox promote <name> --to org    publish to your org's plugin marketplace (prints a DRAFT PR; never sends)
skillbox scrub [--dry-run] [--json]   list KEEP-PRIVATE / *-leo skills that would leak on promote
skillbox scrub <name> --to ID         check one promote target; non-dry-run exits 1 if blocked
skillbox source add <id> <path>     register a local source repo (e.g. a teammate's clone)
skillbox diff <name> | log <name>   the skill folder's uncommitted diff / commit history
skillbox doctor [--json]            check every mount across runtimes: BROKEN / MISSING / DRIFTED / SHADOWED
skillbox sync [--no-pull]           git pull each source, relink, and prune dead links
skillbox update [--dry-run]         git pull each source; show SKILL.md diffs first
skillbox ui [--port N]              localhost management GUI at 127.0.0.1
```

## Where a skill lives = how it's shared

skillbox never stores a "tier" flag and never copies a skill. A skill's reach is simply **which source repo holds the folder**, shown as the tag in each `list`/`doctor` row (e.g. `deploy  team`). `new` creates a skill in your own repo by default; `promote` is the one explicit command that moves a skill to a shared source and relinks it — reversible. `scrub` audits private-boundary skills (`KEEP-PRIVATE`, `*-leo`, `.keep-private`) and blocks `promote` when a move would leak them. `promote --to org` emits a Claude Code plugin manifest and prints the DRAFT marketplace PR for the repo in `$SKILLBOX_ORG_REPO`, which you review and fire yourself.

Sources resolve in priority order; the first to define a name wins (a suffix like `-mine` lets you keep your own version of a shared skill). Run `skillbox doctor` any time to confirm every runtime is mounted consistently.

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
| `SKILLBOX_ORG_REPO` | `owner/repo` of your plugin marketplace for `promote --to org` |
| `SKILLBOX_REPO_URL` | source URL shown on the GUI About page |

## Tests

```bash
bash tests/run_all.sh   # fully hermetic — runs against a sandbox, never your real fleet
```

## License

MIT — see [LICENSE](LICENSE).
