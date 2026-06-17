#!/usr/bin/env bash
# test_promote_org.sh — T8: `promote <name> --to org` is the ORG-tier publish
# off-ramp. It emits a Claude-Code plugin manifest INTO the skill's own folder
# (folder NOT moved) and PRINTS the marketplace registration entry +
# DRAFT-PR command for $SKILLBOX_ORG_REPO. It NEVER opens the PR — that is an
# external action gated on an explicit per-PR go. Hermetic: operates only inside
# $SB_TMP. Never touches the real fleet and never makes a network call.
set -uo pipefail
cd "$(dirname "$0")"
source lib/sandbox.sh
sb_setup
trap sb_teardown EXIT
echo "sandbox: $SB_TMP"

# The org off-ramp targets whatever marketplace repo you configure.
export SKILLBOX_ORG_REPO="acme/skill-marketplace"

# gamma lives only in private (no collision) — a clean publish candidate.
out="$(sb_skillbox promote gamma --to org 2>&1)"; rc=$?
sb_eq "promote --to org exits 0" "$rc" "0"

# 1. The folder is NOT moved (org publish is a copy/off-ramp, not a relocation).
sb_eq "gamma folder stays in its source (not moved)" \
  "$([ -f "$SB_TMP/src/private/skills/gamma/SKILL.md" ] && echo here || echo gone)" "here"

# 2. A valid plugin manifest is emitted into the skill's own .claude-plugin/.
pj="$SB_TMP/src/private/skills/gamma/.claude-plugin/plugin.json"
sb_ok "plugin.json emitted into the skill folder" test -f "$pj"
sb_ok "plugin.json is valid JSON with name/version/description" \
  python3 -c "import json,sys; d=json.load(open('$pj')); sys.exit(0 if d.get('name')=='gamma' and d.get('version') and d.get('description') else 1)"

# 3. The printed marketplace registration entry matches the marketplace schema.
sb_contains "prints marketplace entry with ./plugins/gamma source" "$out" '"source": "./plugins/gamma"'
sb_contains "names the configured org marketplace repo" "$out" "acme/skill-marketplace"

# 4. The DRAFT-PR command is PRINTED, gated, and NEVER auto-sent.
sb_contains "prints a DRAFT PR command" "$out" "gh pr create --draft"
sb_contains "states skillbox never sends the PR" "$out" "never sends"
sb_contains "states the per-PR go gate (no reviewers/@-mentions)" "$out" "per-PR go"
case "$out" in
  *"PR opened"*|*"PR #"*|*"created pull request"*|*"pushed branch"*)
    _sb_fail "must NOT auto-open or push a PR — only print the command" ;;
  *) _sb_pass "no PR auto-opened/pushed (command printed, not executed)" ;;
esac

# 5. The skill still mounts normally after the manifest emit (folder intact).
sb_ok   "gamma still mountable after manifest emit" sb_skillbox add gamma
sb_link "gamma links to its (unmoved) private folder" \
  "$SB_TMP/roots/claude/gamma" "$SB_TMP/src/private/skills/gamma"

# 6. Idempotent: a second run succeeds and leaves a single valid manifest.
sb_ok "promote --to org is idempotent" sb_skillbox promote gamma --to org
sb_ok "plugin.json still valid after a second run" \
  python3 -c "import json; json.load(open('$pj'))"

# A pre-existing NON-DICT plugin.json must not crash the idempotent path.
printf '[1, 2, 3]\n' > "$pj"
sb_ok "promote --to org survives a non-dict existing plugin.json (no traceback)" \
  sb_skillbox promote gamma --to org
sb_ok "non-dict plugin.json is rewritten to a valid object" \
  python3 -c "import json; d=json.load(open('$pj')); assert isinstance(d, dict) and d.get('name')=='gamma'"

# 7. Refusals: unknown skill, and traversal name.
sb_fails "promote unknown skill --to org refused"      sb_skillbox promote nope --to org
sb_fails "promote traversal name --to org refused"     sb_skillbox promote "../escape" --to org

sb_report
