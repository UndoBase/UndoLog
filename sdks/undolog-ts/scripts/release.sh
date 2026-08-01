#!/usr/bin/env bash
# ------------------------------------------------------------------
# Manual release script for @undolog/sdk.
#
# Usage:  ./scripts/release.sh <version>
# Example: ./scripts/release.sh 0.2.0
#
# Steps performed:
#   1. Update version in package.json
#   2. Create a version-bump commit
#   3. Create an annotated tag
#   (the script prints the push command; pushing is left to the caller)
#
# The CI workflow (.github/workflows/release-sdk.yml) handles
# the actual build, test, and npm publish steps.
# ------------------------------------------------------------------

set -euo pipefail

if [ $# -ne 1 ]; then
  echo "Usage: $0 <version>"
  echo "Example: $0 0.2.0"
  exit 1
fi

VERSION="$1"
TAG="@undolog/sdk@v${VERSION}"
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
SDK_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"

echo "Releasing $TAG from $SDK_DIR"

# Bump version in package.json
cd "$SDK_DIR"
npm version "$VERSION" --no-git-tag-version

# Commit
git add package.json package-lock.json
git commit -m "chore(sdk): release v${VERSION}"

# Tag
git tag -a "$TAG" -m "Release $TAG"

echo "Created tag $TAG"
echo "Push with: git push origin main && git push origin $TAG"
