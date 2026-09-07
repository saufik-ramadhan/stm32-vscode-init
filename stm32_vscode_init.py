#!/usr/bin/env python3
"""
Generate editor configuration for an embedded CMake project - builds, flashes
and debugs using whichever toolchain profile matches the project (see
profiles/), without needing that toolchain on your global PATH or installing a
vendor's all-in-one editor extension.

Usage:
    python3 stm32_vscode_init.py [project_dir] [--editor vscode|zed|all] [options]

Run it again any time (new machine, new clone, toolchain upgrade) to regenerate
the config with the paths that are correct for that machine. See --help for all
options, profiles/ for the supported toolchains and editors/ for the supported
editors.
"""
from core import main

if __name__ == "__main__":
    main()
