#!/usr/bin/env python3
"""
Generate a .vscode/ configuration for an embedded CMake project - builds,
flashes and debugs using whichever toolchain profile matches the project
(see profiles/), without needing that toolchain on your global PATH or
installing a vendor's all-in-one VS Code extension.

Usage:
    python3 stm32_vscode_init.py [project_dir] [options]

Run it again any time (new machine, new clone, toolchain upgrade) to
regenerate the .vscode/ files with the paths that are correct for that
machine. See core.py --help for all options, and profiles/ for the list of
supported toolchains.
"""
from core import main

if __name__ == "__main__":
    main()
