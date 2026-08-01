#!/bin/bash
# Build a single-file installer executable for the current platform.
# Run separately on each target OS (or via .github/workflows/build-installer.yml,
# which does this across a Windows/Mac/Linux matrix).
set -euo pipefail

# --distpath/--workpath keep PyInstaller's build/dist output under
# installer/ instead of the repo root, where it'd otherwise sit alongside
# source code (CI's own pyinstaller invocation is left at its defaults --
# ephemeral runners don't have a repo-clutter problem, and changing that
# path would mean also updating the upload-artifact step in
# .github/workflows/build-installer.yml to match).
pip install pyinstaller
pyinstaller --onefile --name docking-platform-installer \
    --distpath installer/dist --workpath installer/build \
    installer/install.py

echo "Built: installer/dist/docking-platform-installer"
