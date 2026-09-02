#!/usr/bin/env bash
# New sibling git worktree + branch off origin/main, fully set up (GH #59).
#
#   scripts/worktree-new.sh <topic> [type]      # type defaults to "feat"
#   make worktree TOPIC=<topic> [TYPE=<type>]
#
# Runs from any worktree; always branches from origin/main and creates
# ../hh-<topic> next to the canonical checkout.
set -euo pipefail

topic="${1:?usage: worktree-new.sh <topic> [type]}"
type="${2:-feat}"

# canonical checkout = the first worktree git knows about
canonical="$(git worktree list --porcelain | awk '/^worktree /{print $2; exit}')"
dir="$(dirname "$canonical")/hh-$topic"
branch="espinoza/$type/$topic"

[ -e "$dir" ] && { echo "already exists: $dir" >&2; exit 1; }

git -C "$canonical" fetch origin -q
git -C "$canonical" worktree add "$dir" -b "$branch" origin/main

( cd "$dir" && HH_CANONICAL="$canonical" bash scripts/dev-setup.sh )

cat <<EOF

$(printf '\033[32m✓\033[0m') worktree ready

  cd "$dir"          # ($branch)
  make check-local              # host gate, no Docker
  make stack-up                 # only when you need the app running

  when merged:  git worktree remove "$dir"   (or: make worktree-rm TOPIC=$topic)
EOF
