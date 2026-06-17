#!/usr/bin/env bash
# test_source_add.sh — `skillbox source add/rm`: add a teammate's local repo (the
# a teammate's clone) as a source. Born LOWEST precedence (never shadows your own),
# manifest-preserving, never auto-mounted; reversible. Hermetic: only $SB_TMP.
set -uo pipefail
cd "$(dirname "$0")"
source lib/sandbox.sh
sb_setup
trap sb_teardown EXIT
echo "sandbox: $SB_TMP"

# a teammate's clone: a skills dir holding two skills
COWORKER="$SB_TMP/coworker/skills"
_sb_mkskill "$COWORKER" coworker-one
_sb_mkskill "$COWORKER" coworker-two

out="$(sb_skillbox source add coworker "$COWORKER" 2>&1)"; rc=$?
sb_eq       "source add exits 0" "$rc" "0"
sb_contains "reports it added the source" "$out" "added source 'coworker'"
sb_contains "manifest gained [sources.coworker]"              "$(cat "$SKILLBOX_MANIFEST")" "[sources.coworker]"
sb_contains "manifest PRESERVED existing [sources.team]"   "$(cat "$SKILLBOX_MANIFEST")" "[sources.team]"
sb_contains "manifest PRESERVED existing [sources.private]" "$(cat "$SKILLBOX_MANIFEST")" "[sources.private]"

# resolve picks up coworker's skills, shown AVAILABLE in the rail — not auto-mounted
page="$(sb_skillbox ui --render)"
sb_contains "coworker-one resolves (available in the rail)" "$page" "coworker-one"
sb_contains "rail lists the coworker source" "$page" 'data-src="coworker"'
sb_eq "coworker-one NOT auto-mounted into claude" \
  "$([ -e "$SB_TMP/roots/claude/coworker-one" ] && echo mounted || echo no)" "no"

# lowest precedence: coworker priority > team's (1) so it can never shadow your own
prio="$(awk '/\[sources.coworker\]/{f=1} f&&/priority/{print $3; exit}' "$SKILLBOX_MANIFEST")"
sb_eq "coworker born lowest precedence (>10)" "$([ "${prio:-0}" -gt 10 ] && echo hi || echo lo)" "hi"

# selectively installable — the actual point of the flow
sb_ok   "install just one coworker skill" sb_skillbox add coworker-one --source coworker
sb_link "coworker-one mounts to the coworker source" "$SB_TMP/roots/claude/coworker-one" "$COWORKER/coworker-one"
sb_eq   "coworker-two stays available (no bulk mount)" \
  "$([ -e "$SB_TMP/roots/claude/coworker-two" ] && echo mounted || echo no)" "no"

# refusals
sb_fails "duplicate id refused"        sb_skillbox source add coworker "$COWORKER"
sb_fails "nonexistent path refused"    sb_skillbox source add nope /no/such/dir
sb_fails "traversal id refused"        sb_skillbox source add "../evil" "$COWORKER"
mkdir -p "$SB_TMP/empty-repo"
sb_fails "path with no skills refused" sb_skillbox source add empty "$SB_TMP/empty-repo"

# repo-root acceptance: pass a repo root that contains a skills/ subdir
MATE="$SB_TMP/mate"; _sb_mkskill "$MATE/skills" mate-one
out2="$(sb_skillbox source add mate "$MATE" 2>&1)"
sb_contains "accepts a repo root with a skills/ subdir" "$out2" "added source 'mate'"
sb_contains "mate-one resolves after repo-root add" "$(sb_skillbox ui --render)" "mate-one"

# TOML-safe path writing: a valid local folder name with a quote must not corrupt the manifest
QUOTED="$SB_TMP/quote\"repo"; _sb_mkskill "$QUOTED/skills" quoted-one
outq="$(sb_skillbox source add quoted "$QUOTED" 2>&1)"; rcq=$?
sb_eq "source add accepts a quoted local path" "$rcq" "0"
sb_contains "quoted path is escaped in TOML" "$(cat "$SKILLBOX_MANIFEST")" 'quote\"repo'
sb_contains "quoted-one resolves after quoted-path add" "$(sb_skillbox ui --render)" "quoted-one"

# source rm reverses, preserving everything else (incl. a later-added source)
sb_ok "source rm coworker" sb_skillbox source rm coworker
case "$(cat "$SKILLBOX_MANIFEST")" in
  *'[sources.coworker]'*) _sb_fail "coworker block still present after rm" ;;
  *) _sb_pass "coworker block removed by source rm" ;;
esac
sb_contains "rm preserved [sources.team]"        "$(cat "$SKILLBOX_MANIFEST")" "[sources.team]"
sb_contains "rm preserved the later [sources.mate]" "$(cat "$SKILLBOX_MANIFEST")" "[sources.mate]"
sb_contains "rm preserved the later quoted source" "$(cat "$SKILLBOX_MANIFEST")" "[sources.quoted]"
sb_fails "rm of unknown source refused" sb_skillbox source rm ghostsrc
# manifest still parses cleanly after the edits (no doctor traceback)
sb_ok "manifest still valid after add/rm churn" sb_skillbox doctor --json

sb_report
