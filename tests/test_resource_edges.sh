#!/usr/bin/env bash
# test_resource_edges.sh — adverse RESOURCE scenarios for skillbox.
#
# Goal: skillbox is robust against degenerate sources/roots — it never crashes
# (no Python traceback), it refuses to clobber non-skillbox files, and it
# reports/handles missing resources predictably. Every assertion below can
# FAIL if skillbox regresses (real targets, real exit codes, negative modes).
#
# Coverage map (scenario spec):
#   (a) empty source dir (no skills) is fine
#   (b) source path that does not exist on disk is skipped, no crash
#   (c) a dir without SKILL.md is NOT treated as a skill
#   (d) a real file in a skill-name slot is left untouched by add (refuse-to-clobber) + observed reporting
#   (e) MISSING-ROOT when a configured root is absent (doctor flags; sync skips gracefully — asserted, not assumed)
#   (f) a skill name with a hyphen AND a dot works end to end
#
# NOTE: only this file is authored; bin/skillbox.py and lib/sandbox.sh are untouched.

set -u
HERE="$(cd "$(dirname "$0")" && pwd -P)"
# shellcheck source=lib/sandbox.sh
source "$HERE/lib/sandbox.sh"
trap sb_teardown EXIT

# Helper: run skillbox, capture combined output, assert NO python traceback ever surfaces.
# Returns the captured exit code via $SB_RC and output via $SB_OUT.
SB_OUT=""; SB_RC=0
sb_run() { # desc args...
  local desc="$1"; shift
  SB_OUT="$(python3 "$SKILLBOX_BIN" "$@" 2>&1)"; SB_RC=$?
  # A traceback is always a regression. grep -vi style: assert it is ABSENT.
  if printf '%s\n' "$SB_OUT" | grep -qi 'Traceback'; then
    _sb_fail "$desc: python traceback (CRASH)"
    printf '       ---\n%s\n       ---\n' "$SB_OUT"
  else
    _sb_pass "$desc: no traceback"
  fi
}

sb_setup

# ── (a) empty source dir (no skills) is fine ─────────────────────────────────
mkdir -p "$SB_TMP/src/empty/skills"
_sb_initrepo "$SB_TMP/src/empty"   # has skills/ but no skill folders inside
cat >> "$SKILLBOX_MANIFEST" <<EOF

[sources.empty]
path = "$SB_TMP/src/empty/skills"
priority = 5
EOF

sb_run "(a) doctor with empty source" doctor --json
empty_json="$SB_OUT"
sb_eq "(a) doctor still exits 0 (empty source not blocking)" "$SB_RC" "0"
# Empty source contributes zero skills; baseline fleet still has the 4 winners (alpha/beta/gamma/shared/solo)
# but nothing is INSTALLED yet (no sync run), so skills_installed must be 0.
installed_a="$(printf '%s' "$empty_json" | python3 -c 'import sys,json;print(json.load(sys.stdin)["skills_installed"])')"
sb_eq "(a) empty source -> 0 skills installed (no crash, no phantom skill)" "$installed_a" "0"
# An empty source must NOT appear as a winner/skill. `list` reads installed links from the primary
# root which is empty pre-sync -> no output lines at all.
sb_run "(a) list with empty source" list
sb_eq "(a) list prints nothing pre-sync (empty source adds no skill)" "$(printf '%s' "$SB_OUT" | grep -c .)" "0"
# Negative guard: the literal source id 'empty' must never be emitted as a skill name.
sb_run "(a) sync tolerates empty source" sync
sb_run "(a) list after sync" list
case "$SB_OUT" in
  *"empty"*) _sb_fail "(a) 'empty' source id leaked as a skill name" ;;
  *) _sb_pass "(a) empty source id never becomes a skill" ;;
esac

# ── (b) a source path that does not exist on disk is skipped without crashing ─
cat >> "$SKILLBOX_MANIFEST" <<EOF

[sources.ghost]
path = "$SB_TMP/src/does_not_exist/here/skills"
priority = 6
EOF
sb_run "(b) doctor with nonexistent source path" doctor --json
sb_eq "(b) nonexistent source does not make doctor blocking" \
  "$(printf '%s' "$SB_OUT" | python3 -c 'import sys,json;print(json.load(sys.stdin)["blocking"])')" "0"
sb_run "(b) sync with nonexistent source path" sync
sb_eq "(b) sync survives nonexistent source (exit 0)" "$SB_RC" "0"
# sync's git-pull leg classifies a non-on-disk source as 'not a git repo, skipped'.
sb_contains "(b) sync reports ghost source as skipped, not fatal" "$SB_OUT" "ghost: not a git repo, skipped"

# ── (c) a dir without SKILL.md is NOT treated as a skill ─────────────────────
mkdir -p "$SB_TMP/src/team/skills/notaskill"
printf 'just a readme, no SKILL.md here\n' > "$SB_TMP/src/team/skills/notaskill/README.md"
sb_run "(c) sync ignores SKILL.md-less dir" sync
sb_run "(c) list after sync (notaskill must be absent)" list
case "$SB_OUT" in
  *"notaskill"*) _sb_fail "(c) dir without SKILL.md was treated as a skill" ;;
  *) _sb_pass "(c) dir without SKILL.md is not a skill" ;;
esac
# It also must not be linked into any root.
[ -e "$SB_TMP/roots/claude/notaskill" ] && _sb_fail "(c) notaskill got linked into claude root" \
  || _sb_pass "(c) notaskill never linked into a root"
# And `add notaskill` must fail (it does not resolve to a real skill).
sb_run "(c) add notaskill is refused" add notaskill
sb_eq "(c) add of a non-skill dir exits nonzero" "$SB_RC" "1"
sb_contains "(c) add reports not-found for the non-skill dir" "$SB_OUT" "not found: notaskill"

# ── (d) a real file in a skill-name slot is left untouched (refuse-to-clobber) ─
# Park a REAL regular file at claude/alpha. `alpha` is a genuine team skill, so add/sync
# will try to mount it everywhere — except this slot, which is a real file.
# Earlier sections ran sync, so claude/alpha is currently a configured-slot symlink; remove it
# first (otherwise a `>` redirect would follow the link into the source dir) and replace
# it with a genuine regular file that skillbox must treat as foreign.
REALFILE_BODY="i am a real user file, not a skillbox symlink $$"
rm -f "$SB_TMP/roots/claude/alpha"   # drop the configured-slot symlink left by prior sync
printf '%s\n' "$REALFILE_BODY" > "$SB_TMP/roots/claude/alpha"
# Sanity: the slot is now a real regular file, not a symlink, before we test refusal.
[ -f "$SB_TMP/roots/claude/alpha" ] && [ ! -L "$SB_TMP/roots/claude/alpha" ] \
  && _sb_pass "(d) precondition: claude/alpha is a real regular file" \
  || _sb_fail "(d) precondition setup failed: claude/alpha is not a plain file"
sb_run "(d) add alpha with a real file occupying claude/alpha" add alpha
sb_eq "(d) add still exits 0 (other roots mount fine)" "$SB_RC" "0"
sb_contains "(d) add prints refuse-to-clobber notice for the real-file slot" \
  "$SB_OUT" "skip claude/alpha: real file/dir present"
# The real file must survive as a REGULAR FILE with identical contents.
[ -L "$SB_TMP/roots/claude/alpha" ] && _sb_fail "(d) real file at claude/alpha was clobbered into a symlink" \
  || _sb_pass "(d) claude/alpha is still a regular file (not clobbered)"
sb_eq "(d) real file contents untouched by add" "$(cat "$SB_TMP/roots/claude/alpha")" "$REALFILE_BODY"
# Other roots DID get a real symlink to the exact source path (proves add still works around the obstacle).
sb_link "(d) agents/alpha links to the team source path" \
  "$SB_TMP/roots/agents/alpha" "$SB_TMP/src/team/skills/alpha"
# A FULL sync (relinks every winner) must STILL refuse to clobber the real file.
sb_run "(d) full sync does not clobber the real file" sync
[ -L "$SB_TMP/roots/claude/alpha" ] && _sb_fail "(d) sync clobbered the real file into a symlink" \
  || _sb_pass "(d) sync left the real file in place"
sb_eq "(d) real file contents untouched by sync" "$(cat "$SB_TMP/roots/claude/alpha")" "$REALFILE_BODY"
# doctor MUST surface the occupied slot: 'alpha' is installed via the other roots, but the
# claude slot is a real (non-symlink) file blocking the mount. skillbox reports this as an
# OCCUPIED (blocking) problem so a blocked runtime is never invisible (regression guard for
# the bug the harness found: doctor used to fall through and stay silent here).
sb_run "(d) doctor with the occupied slot" doctor --json
docd="$SB_OUT"
occupied_count="$(printf '%s' "$docd" | python3 -c 'import sys,json
d=json.load(sys.stdin)
print(sum(1 for p in d["problems"] if p["where"]=="claude/alpha" and p["kind"]=="OCCUPIED"))')"
sb_eq "(d) doctor reports OCCUPIED for the real-file slot at claude/alpha" "$occupied_count" "1"
# An occupied slot is a real mount failure -> blocking.
sb_eq "(d) occupied slot makes doctor blocking (>=1)" \
  "$(printf '%s' "$docd" | python3 -c 'import sys,json;print(1 if json.load(sys.stdin)["blocking"]>=1 else 0)')" "1"

# ── (e) MISSING-ROOT: a configured root dir is absent ────────────────────────
# Fresh sandbox slice: start clean, sync, then yank a root out from under skillbox.
rm -rf "$SB_TMP/roots/cursor"
sb_run "(e) doctor with a missing root dir" doctor --json
mroot_json="$SB_OUT"
# doctor MUST flag MISSING-ROOT for cursor and count it as blocking.
missing_root_count="$(printf '%s' "$mroot_json" | python3 -c 'import sys,json
d=json.load(sys.stdin)
print(sum(1 for p in d["problems"] if p["kind"]=="MISSING-ROOT" and p["where"]=="cursor"))')"
sb_eq "(e) doctor flags MISSING-ROOT for the absent cursor root" "$missing_root_count" "1"
sb_eq "(e) MISSING-ROOT is blocking (>=1)" \
  "$(printf '%s' "$mroot_json" | python3 -c 'import sys,json;print(1 if json.load(sys.stdin)["blocking"]>=1 else 0)')" "1"
# Text doctor must exit nonzero when a root is missing (blocking).
sb_run "(e) doctor text exits nonzero on missing root" doctor
sb_eq "(e) doctor returns 1 when a root is missing" "$SB_RC" "1"
# sync MUST NOT crash. ASSERT ACTUAL BEHAVIOR: link_one skips non-dir roots, so sync
# does NOT recreate the missing root — it silently leaves it absent.
sb_run "(e) sync with a missing root" sync
sb_eq "(e) sync survives a missing root (exit 0)" "$SB_RC" "0"
if [ -d "$SB_TMP/roots/cursor" ]; then
  _sb_fail "(e) UNEXPECTED: sync recreated the missing cursor root (behavior changed)"
else
  _sb_pass "(e) sync leaves the missing root absent (current skip-gracefully behavior)"
fi
# doctor STILL flags it after sync (sync did not heal it).
sb_run "(e) doctor still flags missing root after sync" doctor --json
sb_eq "(e) MISSING-ROOT persists after sync (sync does not heal it)" \
  "$(printf '%s' "$SB_OUT" | python3 -c 'import sys,json
d=json.load(sys.stdin)
print(sum(1 for p in d["problems"] if p["kind"]=="MISSING-ROOT" and p["where"]=="cursor"))')" "1"
# Restore the root for the next section.
mkdir -p "$SB_TMP/roots/cursor"

# ── (f) a skill name containing a hyphen AND a dot works ─────────────────────
HD="foo-bar.baz"
_sb_mkskill "$SB_TMP/src/team/skills" "$HD"
sb_run "(f) add hyphen+dot skill" add "$HD"
sb_eq "(f) add of hyphen+dot name exits 0" "$SB_RC" "0"
# Real link target check (not a tautology): each root links to the exact source path.
sb_link "(f) claude link target for $HD" "$SB_TMP/roots/claude/$HD" "$SB_TMP/src/team/skills/$HD"
sb_link "(f) codex link target for $HD"  "$SB_TMP/roots/codex/$HD"  "$SB_TMP/src/team/skills/$HD"
sb_run "(f) list includes hyphen+dot skill" list
sb_contains "(f) list shows the hyphen+dot skill owned by team" "$SB_OUT" "$HD"
# doctor must stay clean for the new skill (parity consistent across roots) and never traceback.
sb_run "(f) doctor with hyphen+dot skill" doctor --json
hd_consistent="$(printf '%s' "$SB_OUT" | python3 -c "import sys,json
d=json.load(sys.stdin)
print('1' if d['parity'].get('$HD',{}).get('consistent') else '0')")"
sb_eq "(f) hyphen+dot skill is parity-consistent across roots" "$hd_consistent" "1"
# Negative guard: removing it should unlink from every root (proves the name round-trips, not just adds).
sb_run "(f) rm hyphen+dot skill" rm "$HD"
[ -e "$SB_TMP/roots/claude/$HD" ] && _sb_fail "(f) rm left claude/$HD behind" \
  || _sb_pass "(f) rm unlinked claude/$HD"
[ -e "$SB_TMP/roots/agents/$HD" ] && _sb_fail "(f) rm left agents/$HD behind" \
  || _sb_pass "(f) rm unlinked agents/$HD"

sb_report
