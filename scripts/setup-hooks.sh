#!/bin/sh
# Run once per clone:
#   bash scripts/setup-hooks.sh
#
# Points git at the in-repo hooks dir, makes them executable, and prints
# the current author identity so you can spot a wrong / bot email before
# the first commit.

set -e
cd "$(git rev-parse --show-toplevel)"

git config core.hooksPath .githooks
chmod +x .githooks/* 2>/dev/null || true

echo "✓ core.hooksPath set to .githooks"
echo
echo "Current git identity for this repo:"
printf "  user.name  = %s\n" "$(git config user.name)"
printf "  user.email = %s\n" "$(git config user.email)"

EMAIL=$(git config user.email)
case "$EMAIL" in
    *noreply@anthropic.com|*noreply@openai.com|*claude*|*codex*|*copilot*)
        echo
        echo "⚠ user.email looks like a bot account — set a personal one with:"
        echo "    git config user.email \"you@example.com\""
        ;;
esac
