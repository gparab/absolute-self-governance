#!/usr/bin/env bash
# Absolute Self-Governance Installer
# Install globally via: curl -fsSL https://raw.githubusercontent.com/gparab/absolute-self-governance/master/install.sh | bash

set -euo pipefail

echo "========================================="
echo "Installing Absolute Self-Governance..."
echo "========================================="

# 1. Check for Python
if ! command -v python3 &>/dev/null; then
    echo "Error: Python 3 is required but not found. Please install Python." >&2
    exit 1
fi

# 2. Setup installation path
INSTALL_DIR="${HOME}/.self-governance"
mkdir -p "${INSTALL_DIR}"

# 3. Clone or pull repository
if [ -d "${INSTALL_DIR}/.git" ]; then
    echo "Updating existing installation in ${INSTALL_DIR}..."
    git -C "${INSTALL_DIR}" pull origin master
else
    echo "Cloning repository to ${INSTALL_DIR}..."
    git clone https://github.com/gparab/absolute-self-governance.git "${INSTALL_DIR}"
fi

# 4. Create virtual environment and install
echo "Creating isolated virtual environment..."
python3 -m venv "${INSTALL_DIR}/venv"
"${INSTALL_DIR}/venv/bin/pip" install --upgrade pip
"${INSTALL_DIR}/venv/bin/pip" install "${INSTALL_DIR}"

# 5. Create symlink to user bin if writeable, otherwise suggest path
BIN_DIR="${HOME}/.local/bin"
mkdir -p "${BIN_DIR}"

ln -sf "${INSTALL_DIR}/venv/bin/self-governance" "${BIN_DIR}/self-governance"

echo "========================================="
echo "Installation Successful!"
echo "========================================="
echo "The command 'self-governance' has been installed to ${BIN_DIR}/self-governance."
echo ""
echo "To ensure it is available in your terminal, make sure ${BIN_DIR} is in your PATH:"
echo "  export PATH=\"\${PATH}:${BIN_DIR}\""
echo ""
echo "Run 'self-governance --help' to verify the installation."
echo "========================================="
