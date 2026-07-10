#!/bin/bash
# Build a single-file installer executable for the current platform.
# Run separately on each target OS (or via .github/workflows/build-installer.yml,
# which does this across a Windows/Mac/Linux matrix).
set -euo pipefail

pip install pyinstaller
pyinstaller --onefile --name docking-platform-installer installer/install.py

echo "Built: dist/docking-platform-installer"
