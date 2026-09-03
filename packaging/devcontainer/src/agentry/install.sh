#!/bin/sh
# devcontainer Feature install script: install the agentry CLI system-wide.
# Runs at image build, so it only installs the binary; the frozen sync happens via the
# feature's postCreateCommand once the workspace is mounted.
set -e

# Devcontainer passes options as uppercased env vars; VERSION defaults to "latest".
export AGENTRY_VERSION="${VERSION:-latest}"
# Install onto a system PATH location so every user in the container gets `agentry`.
export AGENTRY_INSTALL_DIR="/usr/local/bin"

echo "Installing agentry version='${AGENTRY_VERSION}' to ${AGENTRY_INSTALL_DIR}…"
curl -fsSL https://raw.githubusercontent.com/OpenTechIL/agentry/main/install.sh | sh

agentry version
