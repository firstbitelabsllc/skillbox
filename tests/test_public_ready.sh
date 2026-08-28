#!/usr/bin/env bash
# Public-ready content gate: no employer traces in tracked files.
# skillbox is a public personal-fleet repo; a leak should fail this suite
# before it merges, not surface in a later audit. This gate file is the only
# exclusion (it must name the patterns to search for them).
set -u
cd "$(dirname "$0")/.."

offenders=0
while IFS= read -r -d '' file; do
  case "$file" in
    tests/test_public_ready.sh) continue ;;
  esac
  # text-ish files only, mirroring the takeoff gate
  case "$file" in
    *.py|*.md|*.sh|*.txt|*.toml|*.json|*.yaml|*.yml|*.zsh|*.bash|*.js|*.ts|*.html|*.css) ;;
    *) continue ;;
  esac
  hits=$(grep -nEi 'sc-corp\.net|snapchat\.com|lkwan@snap|(^|@)[^@[:space:]]*\.snap([[:space:]]|$)' "$file" | head -3 || true)
  if [ -n "$hits" ]; then
    printf '%s\n' "$hits"
    offenders=$((offenders + 1))
  fi
done < <(git ls-files -z)

if [ "$offenders" -gt 0 ]; then
  echo "public-ready gate: $offenders file(s) carry employer traces" >&2
  exit 1
fi
echo "public-ready gate: clean"
