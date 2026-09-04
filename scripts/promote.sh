#!/usr/bin/env bash
# Promote the tailnet tier — advance the one Tailscale-served stack to a chosen
# commit. The canonical checkout sits on a local `tailnet` branch that moves ONLY
# through this script, so merges to `main` never reach the people on your tailnet
# until you run this. Every promotion is recorded in deploy/promote-log.md and
# tagged `tailnet-<date>`.
#
#   scripts/promote.sh [REF]        # REF defaults to origin/main
#   make promote            [REF=<ref>]
#   make promote REF=v0.3           # roll back / pin to any ref
#
# Run it in the canonical checkout (the original clone). It refuses elsewhere.
set -euo pipefail

canonical="$(git worktree list --porcelain | sed -n 's/^worktree //p' | head -1)"
if [ "$PWD" != "$canonical" ]; then
  echo "run promote in the canonical checkout:" >&2
  echo "  $canonical" >&2
  exit 1
fi

ref="${1:-origin/main}"
git fetch origin --tags --prune -q

old_sha="$(git rev-parse HEAD)"
old="$(git rev-parse --short HEAD)"
if ! target_sha="$(git rev-parse --verify -q "${ref}^{commit}")"; then
  echo "unknown ref: $ref" >&2
  exit 1
fi
target="$(git rev-parse --short "$target_sha")"
current_branch="$(git symbolic-ref --quiet --short HEAD || echo '(detached)')"

# Only the fully-idempotent case (same commit AND already on the tailnet
# branch) skips the checkout — if canonical drifted onto another branch at the
# same commit (a manual `git checkout main`, say), fall through and re-pin it.
if [ "$old_sha" = "$target_sha" ] && [ "$current_branch" = "tailnet" ]; then
  echo "tailnet is already at $target ($ref) — nothing to promote"
  echo "rebuilding anyway so the running stack matches..."
  docker compose up -d --build
  exit 0
fi

if [ "$current_branch" != "tailnet" ]; then
  echo "canonical wasn't pinned to 'tailnet' (was on '$current_branch') — pinning now"
fi

echo "promote tailnet:  $old  ->  $target   ($ref)"
if git merge-base --is-ancestor "$old_sha" "$target_sha"; then
  git --no-pager log --oneline "${old_sha}..${target_sha}" | sed 's/^/  + /'
else
  echo "  ! $ref is NOT a fast-forward from the current tailnet commit (rollback or divergent)"
fi
echo
printf 'proceed? [y/N] '
read -r reply
[ "$reply" = "y" ] || [ "$reply" = "Y" ] || { echo "aborted"; exit 1; }

git checkout -qB tailnet "$target_sha"

tag="tailnet-$(date +%Y%m%d-%H%M)"
git tag -f "$tag" -m "promote $old -> $target ($ref)" >/dev/null
git tag -f tailnet-current "$tag" >/dev/null 2>&1 || true

log="deploy/promote-log.md"
[ -f "$log" ] || cp deploy/promote-log.template.md "$log"
{
  printf '\n## %s\n\n' "$(date '+%Y-%m-%d %H:%M %Z')"
  printf '`%s` → `%s`  ·  ref `%s`  ·  tag `%s`\n\n' "$old" "$target" "$ref" "$tag"
  if git merge-base --is-ancestor "$old_sha" "$target_sha"; then
    git --no-pager shortlog --no-merges "${old_sha}..${target_sha}" | sed 's/^/    /'
  else
    printf '    (non-linear promote — no shortlog)\n'
  fi
} >> "$log"

echo
echo "rebuilding the tailnet stack..."
docker compose up -d --build

echo
echo "✓ tailnet now at $target  ·  tag $tag  ·  logged to $log"
echo "  push the tag for a shared record:  git push origin $tag"
