#!/bin/sh
# Sync local repository with upstream (lemomo-ai/lemo-opuscar) without losing custom changes.
#
# Usage:
#   sh tools/sync_upstream.sh          # fetch & rebase current branch onto upstream/main
#   sh tools/sync_upstream.sh --push   # also push updated branch to origin (your fork)
set -e

REPO_ROOT=$(cd "$(dirname "$0")/.." && pwd)
cd "$REPO_ROOT"

UPSTREAM_URL="https://github.com/lemomo-ai/lemo-opuscar.git"
AUTO_PUSH=false

for arg in "$@"; do
  case "$arg" in
    --push) AUTO_PUSH=true ;;
    -h|--help)
      echo "Usage: sh tools/sync_upstream.sh [--push]"
      echo "  --push    Push updated branch to origin (with --force-with-lease)"
      exit 0
      ;;
    *)
      echo "Unknown option: $arg" >&2
      exit 1
      ;;
  esac
done

# 1. Ensure lemo.managed is false to prevent destructive automated git reset
if [ "$(git config --get lemo.managed 2>/dev/null)" != "false" ]; then
  git config lemo.managed false
  echo "✓ Ensured 'lemo.managed' is set to false (protects against auto-reset)."
fi

# 2. Check and configure remotes
if ! git remote get-url upstream >/dev/null 2>&1; then
  echo "→ Adding upstream remote: $UPSTREAM_URL"
  git remote add upstream "$UPSTREAM_URL"
fi

if ! git remote get-url origin >/dev/null 2>&1; then
  echo "! Warning: No 'origin' remote found. Run: git remote add origin <your-fork-url>" >&2
fi

# 3. Detect current branch
CURRENT_BRANCH=$(git rev-parse --abbrev-ref HEAD)
if [ -z "$CURRENT_BRANCH" ] || [ "$CURRENT_BRANCH" = "HEAD" ]; then
  echo "✗ Error: Detached HEAD state. Please checkout a branch first." >&2
  exit 1
fi

echo "→ Current branch: $CURRENT_BRANCH"

# 4. Handle uncommitted changes (auto-stash)
STASHED=false
if ! git diff-index --quiet HEAD -- 2>/dev/null || [ -n "$(git status --porcelain)" ]; then
  echo "→ Stashing uncommitted changes..."
  git stash push -u -m "auto-stash before upstream sync ($(date +%Y-%m-%d_%H:%M:%S))"
  STASHED=true
fi

# 5. Fetch upstream
echo "→ Fetching latest changes from upstream main..."
git fetch upstream main

# 6. Rebase or Fast-Forward
if [ "$CURRENT_BRANCH" = "main" ]; then
  echo "→ Fast-forwarding local 'main' with upstream/main..."
  git merge --ff-only upstream/main || {
    echo "! Cannot fast-forward 'main'. Rebasing onto upstream/main..."
    git rebase upstream/main
  }
else
  echo "→ Rebasing branch '$CURRENT_BRANCH' onto upstream/main..."
  if ! git rebase upstream/main; then
    echo ""
    echo "✗ Rebase encountered conflicts." >&2
    echo "  1. Fix conflicted files" >&2
    echo "  2. Run: git add <resolved-files>" >&2
    echo "  3. Run: git rebase --continue" >&2
    echo "  (or abort with: git rebase --abort)" >&2
    exit 1
  fi
fi

# 7. Restore stash if previously created
if [ "$STASHED" = true ]; then
  echo "→ Restoring stashed changes..."
  git stash pop
fi

# 8. Push to fork if requested
if [ "$AUTO_PUSH" = true ]; then
  echo "→ Pushing '$CURRENT_BRANCH' to origin with --force-with-lease..."
  git push origin "$CURRENT_BRANCH" --force-with-lease
  echo "✓ Successfully pushed to origin/$CURRENT_BRANCH."
fi

echo ""
echo "✓ Sync complete! '$CURRENT_BRANCH' is up to date with upstream/main."
