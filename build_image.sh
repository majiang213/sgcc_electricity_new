#!/bin/bash
set -e

# Extract version from config.yaml
# Assumes format: version: "v1.0.0"
VERSION=$(grep '^version:' config.yaml | awk '{print $2}' | tr -d '"')
echo "Detected version from config.yaml: $VERSION"

# Load .env file
if [ -f .env ]; then
  export $(grep -v '^#' .env | xargs)
fi

# Define Repository
# Ensure this matches the 'image' field in config.yaml without the -{arch} suffix
REPO="crpi-nsw3mx3pnbkqn9j5.cn-beijing.personal.cr.aliyuncs.com/majiang213/sgcc_electricity"

# Login to Aliyun if password is available
if [ ! -z "$ALIYUN_PASSWORD" ]; then
    echo "Found ALIYUN_PASSWORD in .env, logging in..."
    echo "$ALIYUN_PASSWORD" | podman login crpi-nsw3mx3pnbkqn9j5.cn-beijing.personal.cr.aliyuncs.com -u "麻绛213" --password-stdin
else
    echo "Warning: ALIYUN_PASSWORD not found in .env. If you haven't logged in manually, the push may fail."
    echo "You can login manually with: docker login --username=麻绛213 crpi-nsw3mx3pnbkqn9j5.cn-beijing.personal.cr.aliyuncs.com"
fi

# Define Dockerfile
DOCKERFILE="Dockerfile-local"

echo "Starting build and push for $REPO using Podman..."

# 1. Build & Push for AMD64
echo "----------------------------------------"
echo "Building for AMD64..."
podman build \
  --platform linux/amd64 \
  --build-arg VERSION="$VERSION" \
  -f "$DOCKERFILE" \
  -t "${REPO}:${VERSION}-amd64" \
  .
echo "Pushing AMD64..."
podman push "${REPO}:${VERSION}-amd64"

# 2. Build & Push for ARM64
echo "----------------------------------------"
echo "Building for ARM64 (aarch64)..."
podman build \
  --platform linux/arm64 \
  --build-arg VERSION="$VERSION" \
  -f "$DOCKERFILE" \
  -t "${REPO}:${VERSION}-aarch64" \
  .
echo "Pushing ARM64..."
podman push "${REPO}:${VERSION}-aarch64"

# 3. Create and Push Manifest List
echo "----------------------------------------"
echo "Creating Manifest List for ${VERSION} and latest..."

MANIFEST_NAME="${REPO}:${VERSION}"
MANIFEST_LATEST="${REPO}:latest"

# Remove local manifest if exists to start fresh
podman manifest rm "$MANIFEST_NAME" 2>/dev/null || true
podman manifest rm "$MANIFEST_LATEST" 2>/dev/null || true

# Create manifest and add REMOTE images (using docker:// prefix ensures we reference the pushed images)
podman manifest create "$MANIFEST_NAME"
podman manifest add "$MANIFEST_NAME" "docker://${REPO}:${VERSION}-amd64"
podman manifest add "$MANIFEST_NAME" "docker://${REPO}:${VERSION}-aarch64"

# Push the manifest list
echo "Pushing Manifest List (Version)..."
podman manifest push "$MANIFEST_NAME" "docker://${MANIFEST_NAME}"

echo "Pushing Manifest List (Latest)..."
podman manifest push "$MANIFEST_NAME" "docker://${MANIFEST_LATEST}"

echo "----------------------------------------"
echo "Build and push completed successfully!"
echo "Images pushed:"
echo "  - ${REPO}:${VERSION}-amd64"
echo "  - ${REPO}:${VERSION}-aarch64"
echo "Manifest List (Multi-Arch):"
echo "  - ${REPO}:${VERSION}"
echo "  - ${REPO}:latest"
