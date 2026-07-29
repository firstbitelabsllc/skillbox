#!/usr/bin/env bash
# Hermetic test: selective pull from an EXTERNAL LOCAL source — a teammate's
# repo. skillbox does NOT clone remotes; a teammate's repo already on disk is
# just another LOCAL path source, and you mount only the one skill you want.
#
# Covers:
#   1. register a teammate repo as a LOCAL source; `add <skill> --source coworker`
#      mounts ONLY that skill into all 4 roots (selective pull).
#   2. the teammate's OTHER skills are NOT mounted.
#   3. doctor stays clean; update tolerates the extra source.
#   4. rm unlinks from all roots; the teammate's source repo is untouched.
set -uo pipefail
cd "$(dirname "$0")"
source lib/sandbox.sh
sb_setup
trap sb_teardown EXIT
echo "sandbox: $SB_TMP"

# A teammate's repo, already on disk (sb_make_remote builds a real local git repo).
COWORKER="$(sb_make_remote coworker toolx tooly toolz)"
sb_eq "teammate repo on disk" "$( [ -f "$COWORKER/skills/toolx/SKILL.md" ] && echo yes )" "yes"

# Register it as a LOCAL path source, lowest precedence.
cat >> "$SKILLBOX_MANIFEST" <<EOF

[sources.coworker]
path = "$COWORKER/skills"
priority = 10
EOF

# (1) selective pull: mount ONLY toolx into every runtime, pointing at coworker's source.
sb_ok "add toolx --source coworker" sb_skillbox add toolx --source coworker
for r in claude agents cursor codex; do
  sb_link "toolx mounted in $r -> coworker source" "$SB_TMP/roots/$r/toolx" "$COWORKER/skills/toolx"
done

# (2) the teammate's OTHER skills are NOT mounted (selective, not wholesale).
for r in claude agents cursor codex; do
  sb_eq "tooly NOT mounted in $r" "$( [ -L "$SB_TMP/roots/$r/tooly" ] && echo linked || echo no )" "no"
  sb_eq "toolz NOT mounted in $r" "$( [ -L "$SB_TMP/roots/$r/toolz" ] && echo linked || echo no )" "no"
done

# (3) doctor clean (only toolx installed); update tolerates the extra source.
DOC="$(sb_skillbox doctor --json)"
sb_contains "doctor reports 1 skill installed"   "$DOC" '"skills_installed": 1'
sb_contains "doctor reports 0 blocking problems" "$DOC" '"blocking": 0'
sb_ok "update tolerates the teammate source" sb_skillbox update

# (4) rm unlinks from every root; the teammate's source repo is untouched.
sb_ok "rm toolx" sb_skillbox rm toolx
for r in claude agents cursor codex; do
  sb_eq "toolx unlinked from $r" "$( [ -L "$SB_TMP/roots/$r/toolx" ] && echo linked || echo gone )" "gone"
done
sb_eq "teammate skill source untouched" "$( [ -f "$COWORKER/skills/toolx/SKILL.md" ] && echo yes )" "yes"

# NEGATIVE: rm of a never-installed name is a no-op (exits 0).
sb_contains "rm of unknown skill is a no-op" \
  "$(sb_skillbox rm neverwas)" "no symlinks found in configured runtime slots"

sb_report
