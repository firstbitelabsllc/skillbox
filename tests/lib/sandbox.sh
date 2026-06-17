#!/usr/bin/env bash
# Hermetic sandbox for skillbox tests. NEVER touches the real fleet (~/.claude etc.).
# Each test: source this, call sb_setup, run sb_skillbox, assert, sb_report.
# sb_setup builds a temp world: 4 fake runtime roots + 3 fake source repos
# (team[git], private[git], solo[single-skill git]) + a manifest, and exports
# SKILLBOX_MANIFEST so skillbox operates entirely inside the sandbox.

SKILLBOX_BIN="${SKILLBOX_BIN:-$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)/bin/skillbox.py}"

sb_skillbox() { python3 "$SKILLBOX_BIN" "$@"; }

_sb_commit() { git -C "$1" -c user.email=t@t -c user.name=t -c commit.gpgsign=false commit -q "${@:2}"; }
_sb_initrepo() { git -C "$1" init -q -b main && git -C "$1" add -A && _sb_commit "$1" -m init; }

_sb_mkskill() { # skills_dir name [description]
  local dir="$1/$2"
  mkdir -p "$dir"
  printf -- '---\nname: %s\ndescription: %s\n---\n# %s\ntest body\n' "$2" "${3:-test skill $2}" "$2" > "$dir/SKILL.md"
}

sb_setup() {
  SB_TMP="$(mktemp -d "${TMPDIR:-/tmp}/skillbox-sb.XXXXXX")"
  SB_TMP="$(cd "$SB_TMP" && pwd -P)"  # canonicalize: strip TMPDIR's trailing slash + resolve /var->/private/var
  export SB_TMP
  mkdir -p "$SB_TMP/roots/claude" "$SB_TMP/roots/agents" "$SB_TMP/roots/cursor" "$SB_TMP/roots/codex"

  # team source (priority 1): alpha, beta, shared
  mkdir -p "$SB_TMP/src/team/skills"
  _sb_mkskill "$SB_TMP/src/team/skills" alpha
  _sb_mkskill "$SB_TMP/src/team/skills" beta
  _sb_mkskill "$SB_TMP/src/team/skills" shared "shared — team version (wins)"
  _sb_initrepo "$SB_TMP/src/team"

  # private source (priority 2): gamma, shared(collides → shadowed)
  mkdir -p "$SB_TMP/src/private/skills"
  _sb_mkskill "$SB_TMP/src/private/skills" gamma
  _sb_mkskill "$SB_TMP/src/private/skills" shared "shared — private version (shadowed)"
  _sb_initrepo "$SB_TMP/src/private"

  # solo source (priority 3): single-skill repo (root IS the skill)
  mkdir -p "$SB_TMP/src/solo"
  printf -- '---\nname: solo\ndescription: single-skill source\n---\n# solo\n' > "$SB_TMP/src/solo/SKILL.md"
  _sb_initrepo "$SB_TMP/src/solo"

  export SKILLBOX_MANIFEST="$SB_TMP/skills.toml"
  cat > "$SKILLBOX_MANIFEST" <<EOF
[roots]
claude = "$SB_TMP/roots/claude"
agents = "$SB_TMP/roots/agents"
cursor = "$SB_TMP/roots/cursor"
codex  = "$SB_TMP/roots/codex"

[sources.team]
path = "$SB_TMP/src/team/skills"
priority = 1

[sources.private]
path = "$SB_TMP/src/private/skills"
priority = 2

[sources.solo]
path = "$SB_TMP/src/solo"
priority = 3
single_skill = "solo"
EOF
}

# Make a throwaway "remote" git repo with skills, usable as a clone source.
sb_make_remote() { # name skill1 skill2...
  local name="$1"; shift
  local repo="$SB_TMP/remotes/$name"
  mkdir -p "$repo/skills"
  local s
  for s in "$@"; do _sb_mkskill "$repo/skills" "$s"; done
  _sb_initrepo "$repo"
  echo "$repo"
}

sb_teardown() { [ -n "${SB_TMP:-}" ] && rm -rf "$SB_TMP"; }

# ── assertions ──────────────────────────────────────────────────────────────
SB_FAILS=0
_sb_pass() { printf '  \033[32mPASS\033[0m %s\n' "$1"; }
_sb_fail() { printf '  \033[31mFAIL\033[0m %s\n' "$1"; SB_FAILS=$((SB_FAILS + 1)); }
sb_eq()       { [ "$2" = "$3" ] && _sb_pass "$1" || { _sb_fail "$1"; printf '       got [%s] want [%s]\n' "$2" "$3"; }; }
sb_contains() { case "$2" in *"$3"*) _sb_pass "$1";; *) _sb_fail "$1"; printf '       output missing [%s]\n' "$3";; esac; }
sb_link()     { local got; got="$(readlink "$2" 2>/dev/null)"; sb_eq "$1" "${got%/}" "${3%/}"; }
sb_ok()       { local d="$1"; shift; if "$@" >/dev/null 2>&1; then _sb_pass "$d"; else _sb_fail "$d (expected exit 0)"; fi; }
sb_fails()    { local d="$1"; shift; if "$@" >/dev/null 2>&1; then _sb_fail "$d (expected nonzero exit)"; else _sb_pass "$d"; fi; }
sb_report()   { echo; if [ "$SB_FAILS" -eq 0 ]; then printf '\033[32mALL PASS\033[0m\n'; else printf '\033[31m%s FAILURE(S)\033[0m\n' "$SB_FAILS"; fi; return "$SB_FAILS"; }
