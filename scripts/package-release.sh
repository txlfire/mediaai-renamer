#!/usr/bin/env bash
set -euo pipefail

VERSION=""
REPO="txlfire/mediaai-renamer"
TARGET="main"
NOTES=""
PUBLISH=0
SKIP_BUILD=0

while [[ $# -gt 0 ]]; do
  case "$1" in
    --version)
      VERSION="${2:-}"
      shift 2
      ;;
    --repo)
      REPO="${2:-}"
      shift 2
      ;;
    --target)
      TARGET="${2:-}"
      shift 2
      ;;
    --notes)
      NOTES="${2:-}"
      shift 2
      ;;
    --publish)
      PUBLISH=1
      shift
      ;;
    --skip-build)
      SKIP_BUILD=1
      shift
      ;;
    -h|--help)
      cat <<'EOF'
Usage: scripts/package-release.sh [options]

Options:
  --version <version>  Release version. Defaults to package.json version.
  --repo <repo>        GitHub repository. Defaults to txlfire/mediaai-renamer.
  --target <branch>    GitHub release target branch. Defaults to main.
  --notes <text>       GitHub release notes.
  --publish            Create or update GitHub Release with gh.
  --skip-build         Skip npm frontend build and reuse frontend/dist.
EOF
      exit 0
      ;;
    *)
      echo "Unknown option: $1" >&2
      exit 1
      ;;
  esac
done

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
cd "$ROOT"

if [[ -z "$VERSION" ]]; then
  VERSION="$(node -p "require('./package.json').version")"
fi

CLEAN_VERSION="${VERSION#v}"
if [[ -z "$CLEAN_VERSION" ]]; then
  echo "Version cannot be empty." >&2
  exit 1
fi

TAG="v$CLEAN_VERSION"
DIST_DIR="$ROOT/frontend/dist"
RELEASE_DIR="$ROOT/releases"
ARTIFACT="$RELEASE_DIR/mediaai-renamer-frontend-$TAG.zip"

if [[ "$SKIP_BUILD" -eq 0 ]]; then
  npm run frontend:build
fi

if [[ ! -d "$DIST_DIR" ]]; then
  echo "Frontend dist directory not found: $DIST_DIR" >&2
  exit 1
fi

mkdir -p "$RELEASE_DIR"
rm -f "$ARTIFACT"

if command -v zip >/dev/null 2>&1; then
  (cd "$DIST_DIR" && zip -qr "$ARTIFACT" .)
else
  python3 - "$DIST_DIR" "$ARTIFACT" <<'PY'
import pathlib
import sys
import zipfile

dist = pathlib.Path(sys.argv[1])
artifact = pathlib.Path(sys.argv[2])

with zipfile.ZipFile(artifact, "w", zipfile.ZIP_DEFLATED) as package:
    for path in dist.rglob("*"):
        if path.is_file():
            package.write(path, path.relative_to(dist).as_posix())
PY
fi

echo "Release package created: $ARTIFACT"

if [[ "$PUBLISH" -eq 1 ]]; then
  if ! command -v gh >/dev/null 2>&1; then
    echo "GitHub CLI is not installed or not available in PATH." >&2
    exit 1
  fi

  gh auth status >/dev/null

  if [[ -z "$NOTES" ]]; then
    NOTES="MediaAI Renamer $TAG release."
  fi

  if gh release view "$TAG" --repo "$REPO" >/dev/null 2>&1; then
    gh release upload "$TAG" "$ARTIFACT" --repo "$REPO" --clobber
    echo "Release asset uploaded to existing release: $TAG"
  else
    gh release create "$TAG" "$ARTIFACT" --repo "$REPO" --target "$TARGET" --title "$TAG" --notes "$NOTES" --latest
    echo "GitHub release created: $TAG"
  fi
fi
