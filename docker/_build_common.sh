#!/bin/bash
# 内部脚本：由 build_linux_*.sh / build_native.sh 调用，请勿直接执行
set -e

SCRIPT_DIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )" && pwd )"
PROJECT_ROOT="$(dirname "$SCRIPT_DIR")"
RELEASE_DIR="$SCRIPT_DIR/release"
if [ -z "$VERSION" ]; then
  echo "错误: 未指定版本号。请使用相应的入口构建脚本并传入版本号 (例如: ./build_linux_x86.sh 1.2.0)"
  exit 1
fi
IMAGE_NAME="yunshu-ai-agent:$VERSION"

# PLATFORM: linux/amd64 | linux/arm64；留空则本机原生架构
# EXPORT_TAR: 1 导出 tar 到 docker/release/（默认）；0 不导出
EXPORT_TAR="${EXPORT_TAR:-1}"

platform_label() {
  case "${1:-}" in
    linux/amd64) echo "linux-amd64" ;;
    linux/arm64) echo "linux-arm64" ;;
    *) echo "native" ;;
  esac
}

# 判断是否需要跨架构构建（Mac M 系打 x86 包等）
needs_cross_build() {
  [[ -n "${PLATFORM:-}" ]] || return 1
  local machine
  machine="$(uname -m)"
  case "$PLATFORM" in
    linux/amd64)
      [[ "$machine" == "x86_64" ]] && return 1
      return 0
      ;;
    linux/arm64)
      [[ "$machine" == "arm64" || "$machine" == "aarch64" ]] && return 1
      return 0
      ;;
    *)
      return 0
      ;;
  esac
}

# 解析可用的 buildx 命令（兼容 Homebrew / Docker Desktop）
resolve_buildx() {
  if docker buildx version >/dev/null 2>&1; then
    BUILDX=(docker buildx)
    return 0
  fi
  if command -v docker-buildx >/dev/null 2>&1; then
    BUILDX=(docker-buildx)
    return 0
  fi
  local candidates=(
    "$(brew --prefix docker-buildx 2>/dev/null)/bin/docker-buildx"
    "/opt/homebrew/lib/docker/cli-plugins/docker-buildx"
    "/usr/local/lib/docker/cli-plugins/docker-buildx"
  )
  local p
  for p in "${candidates[@]}"; do
    if [[ -n "$p" && -x "$p" ]]; then
      BUILDX=("$p")
      return 0
    fi
  done
  return 1
}

print_buildx_help() {
  cat <<EOF
错误: 跨平台构建需要 docker buildx，但当前环境不可用。

常见原因: 使用 Homebrew 安装的 docker，但 ~/.docker/cli-plugins/docker-buildx
         仍指向已卸载的 Docker Desktop（失效软链）。

修复（任选其一）:

  1) 一键修复（推荐，在 docker 目录执行）:
     ./install-buildx.sh

  2) 手动安装:
     brew install docker-buildx
     mkdir -p ~/.docker/cli-plugins
     ln -sf "\$(brew --prefix docker-buildx)/bin/docker-buildx" ~/.docker/cli-plugins/docker-buildx
     docker buildx version

  3) 安装并启动 Docker Desktop，使用其自带的 docker 命令。

修复后重新执行: ./build_linux_x86.sh
EOF
}

ensure_buildx() {
  if ! resolve_buildx; then
    print_buildx_help
    exit 1
  fi
  if ! "${BUILDX[@]}" inspect yunshu-builder >/dev/null 2>&1; then
    "${BUILDX[@]}" create --name yunshu-builder --use
  else
    "${BUILDX[@]}" use yunshu-builder >/dev/null
  fi
}

# Mac 跨平台构建时，在宿主机（原生架构）预构建前端，避免 qemu 模拟 amd64 时 vite OOM
build_frontend_on_host() {
  echo "=== 宿主机预构建前端（跨平台 Docker 构建易 OOM，跳过容器内 vite build）==="
  if ! command -v node >/dev/null 2>&1 || ! command -v npm >/dev/null 2>&1; then
    echo "错误: 未找到 node/npm，请先安装 Node.js 或在 Linux 本机构建"
    exit 1
  fi
  (
    cd "$PROJECT_ROOT/frontend"
    npm ci || npm install
    NODE_OPTIONS="${NODE_OPTIONS:---max-old-space-size=4096}" npx vite build
  )
  test -f "$PROJECT_ROOT/frontend/dist/index.html"
  echo "=== 前端预构建完成: frontend/dist ==="
}

docker_build_args() {
  DOCKER_BUILD_ARGS=(-f "$SCRIPT_DIR/Dockerfile" -t "$IMAGE_NAME")
  if [[ "${PREBUILD_FRONTEND:-0}" == "1" ]]; then
    DOCKER_BUILD_ARGS+=(--build-arg "PREBUILD_FRONTEND=1")
  fi
}

run_build() {
  docker_build_args

  if [[ -z "${PLATFORM:-}" ]]; then
    docker build "${DOCKER_BUILD_ARGS[@]}" .
    return
  fi

  if needs_cross_build; then
    build_frontend_on_host
    PREBUILD_FRONTEND=1
    docker_build_args
    ensure_buildx
    "${BUILDX[@]}" build \
      --platform "$PLATFORM" \
      --progress=plain \
      "${DOCKER_BUILD_ARGS[@]}" \
      --load \
      .
    return
  fi

  # 本机已是目标架构（如 Intel Mac 打 amd64），可直接 docker build
  echo "本机架构与目标平台一致，使用 docker build（无需 buildx）"
  DOCKER_BUILDKIT=1 docker build \
    --platform "$PLATFORM" \
    --progress=plain \
    "${DOCKER_BUILD_ARGS[@]}" \
    .
}

LABEL="$(platform_label "${PLATFORM:-}")"
DATE_TAG="$(date +%Y%m%d)"
OUTPUT_FILE="$RELEASE_DIR/yunshu-ai-agent_${VERSION}_${LABEL}_${DATE_TAG}.tar"

echo "=== 开始构建 Docker 镜像 ==="
echo "项目根目录: $PROJECT_ROOT"
echo "Dockerfile:   $SCRIPT_DIR/Dockerfile"
echo "镜像标签:     $IMAGE_NAME"
echo "tar 输出目录: $RELEASE_DIR"
if [[ -n "${PLATFORM:-}" ]]; then
  if needs_cross_build; then
    echo "目标平台:     $PLATFORM (docker buildx 跨平台)"
  else
    echo "目标平台:     $PLATFORM (docker build)"
  fi
else
  echo "目标平台:     本机原生架构 (docker build)"
fi

mkdir -p "$RELEASE_DIR"
cd "$PROJECT_ROOT"

run_build

echo "=== 镜像构建成功: $IMAGE_NAME ==="

if docker image inspect "$IMAGE_NAME" --format '{{.Os}}/{{.Architecture}}' 2>/dev/null; then
  echo "镜像架构:     $(docker image inspect "$IMAGE_NAME" --format '{{.Os}}/{{.Architecture}}')"
fi

if [[ "$EXPORT_TAR" == "1" ]]; then
  echo "=== 正在导出镜像到 $OUTPUT_FILE ==="
  docker save -o "$OUTPUT_FILE" "$IMAGE_NAME"
  echo "=== 导出完成 ==="
  ls -lh "$OUTPUT_FILE"
else
  echo "=== 跳过导出 tar (EXPORT_TAR=0) ==="
fi
