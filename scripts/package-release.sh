#!/usr/bin/env bash
# 用途：在 Linux/macOS 环境构建前端发布包，可选上传到 GitHub Release。
# 关键步骤：解析参数 -> 确定版本 -> 构建前端 -> 复制 dist 和示例配置 -> 压缩 artifact -> 可选发布。
set -euo pipefail

VERSION=""
REPO="txlfire/mediaai-renamer"
TARGET="main"
NOTES=""
PUBLISH=0
SKIP_BUILD=0

# 解析发布参数；未传版本时使用 package.json 版本。
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
  # 通过 Node 读取根 package.json，保持和 Windows 脚本行为一致。
  VERSION="$(node -p "require('./package.json').version")"
fi

# 发布标签统一为 vX.Y.Z，传入 v 前缀时会自动去重。
CLEAN_VERSION="${VERSION#v}"
if [[ -z "$CLEAN_VERSION" ]]; then
  echo "Version cannot be empty." >&2
  exit 1
fi

TAG="v$CLEAN_VERSION"
DIST_DIR="$ROOT/frontend/dist"
RELEASE_DIR="$ROOT/releases"
ARTIFACT="$RELEASE_DIR/mediaai-renamer-frontend-$TAG.zip"
PACKAGE_ROOT="$RELEASE_DIR/package-$TAG"

if [[ "$SKIP_BUILD" -eq 0 ]]; then
  # 默认重新构建前端，确保 artifact 来自当前代码。
  npm run frontend:build
fi

if [[ ! -d "$DIST_DIR" ]]; then
  echo "Frontend dist directory not found: $DIST_DIR" >&2
  exit 1
fi

mkdir -p "$RELEASE_DIR"
rm -f "$ARTIFACT"
rm -rf "$PACKAGE_ROOT"
mkdir -p "$PACKAGE_ROOT/config"
# 正式包只带示例配置，避免把本地正式配置 config.toml 打进包。
cp -R "$DIST_DIR"/. "$PACKAGE_ROOT"/
cp "$ROOT/config/config.example.toml" "$PACKAGE_ROOT/config/config.example.toml"

# 优先使用系统 zip；没有 zip 时用 Python 标准库兜底。
if command -v zip >/dev/null 2>&1; then
  (cd "$PACKAGE_ROOT" && zip -qr "$ARTIFACT" .)
else
  python3 - "$PACKAGE_ROOT" "$ARTIFACT" <<'PY'
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
rm -rf "$PACKAGE_ROOT"

echo "Release package created: $ARTIFACT"

if [[ "$PUBLISH" -eq 1 ]]; then
  # 发布模式依赖 GitHub CLI，并会创建或更新对应 Release 资源。
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
