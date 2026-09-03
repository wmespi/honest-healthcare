#!/usr/bin/env bash
# What each tier is running, and the gap between them.
#
#   scripts/tiers.sh
#   make tiers
set -euo pipefail

canonical="$(git worktree list --porcelain | sed -n 's/^worktree //p' | head -1)"
git -C "$canonical" fetch origin --tags --prune -q 2>/dev/null || true

line() { printf '  %-10s %-9s %s\n' "$1" "$2" "$3"; }

echo
echo "  tier       commit    subject"
echo "  ────────────────────────────────────────────────────────────────"

# tailnet = whatever the canonical checkout is currently on (the tailnet branch)
t_sha="$(git -C "$canonical" rev-parse --short HEAD)"
t_sub="$(git -C "$canonical" log -1 --format=%s)"
t_branch="$(git -C "$canonical" symbolic-ref --quiet --short HEAD || echo 'detached')"
line "tailnet" "$t_sha" "$t_sub"
if [ "$t_branch" = "tailnet" ]; then
  printf '  %-10s %-9s %s\n' "" "" "on 'tailnet' — moves only via 'make promote'"
else
  printf '  %-10s %-9s %s\n' "" "" "⚠ on '$t_branch', not pinned yet — 'make promote' pins the tailnet tier"
fi

m_sha="$(git -C "$canonical" rev-parse --short origin/main)"
m_sub="$(git -C "$canonical" log -1 --format=%s origin/main)"
line "origin/main" "$m_sha" "$m_sub"

# preview stack, if one is up
preview_dir="$(dirname "$canonical")/hh-preview"
if [ -d "$preview_dir" ] && docker compose --project-directory "$preview_dir" ps -q 2>/dev/null | grep -q .; then
  p_sha="$(git -C "$preview_dir" rev-parse --short HEAD)"
  p_sub="$(git -C "$preview_dir" log -1 --format=%s)"
  line "preview" "$p_sha" "$p_sub (ephemeral — localhost:5183)"
fi

echo
if [ "$(git -C "$canonical" rev-parse HEAD)" = "$(git -C "$canonical" rev-parse origin/main)" ]; then
  echo "  tailnet is level with origin/main — nothing to promote."
else
  n="$(git -C "$canonical" rev-list --count HEAD..origin/main 2>/dev/null || echo '?')"
  echo "  $n commit(s) on origin/main not yet promoted to the tailnet:"
  git -C "$canonical" --no-pager log --oneline HEAD..origin/main | sed 's/^/    + /'
  echo
  echo "  review:   make preview REF=main       promote:  make promote"
fi
echo
