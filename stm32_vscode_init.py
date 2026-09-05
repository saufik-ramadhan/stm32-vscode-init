#!/usr/bin/env python3
"""
Generate a .vscode/ configuration for an STM32CubeMX project (CMake toolchain)
that builds, flashes and debugs using STM32CubeCLT - without needing the
toolchain on your global PATH/environment.

Usage:
    python3 stm32_vscode_init.py [project_dir] [options]

Run it again any time (new machine, new clone, CLT upgrade) to regenerate
the .vscode/ files with the paths that are correct for that machine.
"""
import argparse
import glob
import json
import platform
import re
import shutil
import sys
from pathlib import Path

IS_WINDOWS = platform.system() == "Windows"
IS_MAC = platform.system() == "Darwin"
IS_LINUX = platform.system() == "Linux"


def exe(name: str) -> str:
    return name + ".exe" if IS_WINDOWS else name


# --------------------------------------------------------------------------
# STM32CubeCLT discovery
# --------------------------------------------------------------------------

def candidate_clt_roots():
    patterns = []
    home = str(Path.home())
    if IS_WINDOWS:
        patterns += [
            r"C:\ST\STM32CubeCLT_*",
            r"C:\ST\STM32CubeCLT*",
        ]
    elif IS_MAC:
        patterns += [
            "/opt/ST/STM32CubeCLT_*",
            "/opt/st/stm32cubeclt_*",
            "/Applications/STM32CubeCLT*",
        ]
    else:  # Linux
        patterns += [
            "/opt/st/stm32cubeclt_*",
            "/opt/ST/STM32CubeCLT_*",
        ]
    patterns.append(str(Path(home) / "STM32CubeCLT*"))

    found = []
    for pattern in patterns:
        for path in glob.glob(pattern):
            p = Path(path)
            if p.is_dir():
                found.append(p)
    # de-dup, keep stable order, prefer newest-looking name last -> reverse sort by name
    uniq = sorted({p.resolve() for p in found}, key=lambda p: p.name)
    return uniq


def looks_like_clt_root(path: Path) -> bool:
    return (path / "GNU-tools-for-STM32" / "bin").is_dir()


def pick_clt_root(args) -> Path:
    if args.clt_path:
        p = Path(args.clt_path).expanduser().resolve()
        if not looks_like_clt_root(p):
            print(f"WARNING: {p} does not look like an STM32CubeCLT install "
                  f"(missing GNU-tools-for-STM32/bin) - continuing anyway.")
        return p

    candidates = [p for p in candidate_clt_roots() if looks_like_clt_root(p)]
    if not candidates:
        print("Could not auto-detect an STM32CubeCLT installation.")
        if args.yes:
            print("ERROR: pass --clt-path <dir> when using --yes.")
            sys.exit(1)
        entered = input("Enter the full path to your STM32CubeCLT_x.y.z folder: ").strip()
        p = Path(entered).expanduser().resolve()
        if not looks_like_clt_root(p):
            print(f"ERROR: {p} does not look like an STM32CubeCLT install.")
            sys.exit(1)
        return p

    if len(candidates) == 1 or args.yes:
        return candidates[-1]

    print("Multiple STM32CubeCLT installs found:")
    for i, p in enumerate(candidates):
        print(f"  [{i}] {p}")
    choice = input(f"Pick one [0-{len(candidates)-1}] (default: last/newest): ").strip()
    if not choice:
        return candidates[-1]
    return candidates[int(choice)]


class Toolchain:
    def __init__(self, clt_root: Path):
        self.clt_root = clt_root
        self.gcc = clt_root / "GNU-tools-for-STM32" / "bin" / exe("arm-none-eabi-gcc")
        self.gxx = clt_root / "GNU-tools-for-STM32" / "bin" / exe("arm-none-eabi-g++")
        self.gdb = clt_root / "GNU-tools-for-STM32" / "bin" / exe("arm-none-eabi-gdb")
        self.cmake = clt_root / "CMake" / "bin" / exe("cmake")
        self.ninja = clt_root / "Ninja" / "bin" / exe("ninja")
        self.programmer_cli = clt_root / "STM32CubeProgrammer" / "bin" / exe("STM32_Programmer_CLI")
        self.programmer_bin_dir = clt_root / "STM32CubeProgrammer" / "bin"
        self.gdbserver = clt_root / "STLink-gdb-server" / "bin" / exe("ST-LINK_gdbserver")
        self.svd_dir = clt_root / "STMicroelectronics_CMSIS_SVD"

    def check(self):
        missing = []
        for label, path in [
            ("arm-none-eabi-gcc", self.gcc),
            ("arm-none-eabi-gdb", self.gdb),
            ("cmake", self.cmake),
            ("ninja", self.ninja),
            ("STM32_Programmer_CLI", self.programmer_cli),
            ("ST-LINK_gdbserver", self.gdbserver),
        ]:
            if not path.is_file():
                missing.append(f"  - {label}: expected at {path}")
        return missing

    def path_dirs(self):
        return [
            self.clt_root / "GNU-tools-for-STM32" / "bin",
            self.clt_root / "CMake" / "bin",
            self.clt_root / "Ninja" / "bin",
            self.clt_root / "STM32CubeProgrammer" / "bin",
            self.clt_root / "STLink-gdb-server" / "bin",
        ]


# --------------------------------------------------------------------------
# Project introspection
# --------------------------------------------------------------------------

def read_text(path: Path) -> str:
    return path.read_text(encoding="utf-8", errors="ignore")


def find_project_name(project_dir: Path) -> str:
    cmakelists = project_dir / "CMakeLists.txt"
    if not cmakelists.is_file():
        print(f"ERROR: no CMakeLists.txt in {project_dir}. Point this script at "
              f"the folder STM32CubeMX generated (the one with CMakeLists.txt).")
        sys.exit(1)
    text = read_text(cmakelists)
    m = re.search(r'set\(\s*CMAKE_PROJECT_NAME\s+([A-Za-z0-9_\-]+)', text)
    if m:
        return m.group(1)
    m = re.search(r'project\(\s*\$?\{?([A-Za-z0-9_\-]+)', text)
    if m:
        return m.group(1)
    print("ERROR: could not determine project name from CMakeLists.txt.")
    sys.exit(1)


def find_device(project_dir: Path):
    search_files = [
        project_dir / "cmake" / "stm32cubemx" / "CMakeLists.txt",
        project_dir / "CMakeLists.txt",
    ]
    search_files += [Path(p) for p in glob.glob(str(project_dir / "cmake" / "*.cmake"))]
    for f in search_files:
        if not f.is_file():
            continue
        text = read_text(f)
        m = re.search(r'STM32[A-Z0-9]*?xx', text)
        if m:
            return m.group(0)
    return None


def find_svd(toolchain: Toolchain, device: str):
    if not device or not toolchain.svd_dir.is_dir():
        return None
    base = device[:-2] if device.endswith("xx") else device
    matches = list(toolchain.svd_dir.glob(f"{base}*.svd"))
    if not matches:
        matches = list(toolchain.svd_dir.glob(f"{base}*.[sS][vV][dD]"))
    return matches[0] if matches else None


def has_cmake_presets(project_dir: Path) -> bool:
    return (project_dir / "CMakePresets.json").is_file()


# --------------------------------------------------------------------------
# File writers
# --------------------------------------------------------------------------

def backup_if_exists(path: Path):
    if path.is_file():
        bak = path.with_suffix(path.suffix + ".bak")
        shutil.copy2(path, bak)


def write_json(path: Path, data, dry_run: bool):
    print(f"  write {path}" + (" (dry-run)" if dry_run else ""))
    if dry_run:
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    backup_if_exists(path)
    path.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")


def merge_json(path: Path, updates: dict, dry_run: bool):
    existing = {}
    if path.is_file():
        try:
            existing = json.loads(read_text(path))
        except json.JSONDecodeError:
            print(f"  WARNING: {path} was not valid JSON, backing up and replacing it.")
            existing = {}
    existing.update(updates)
    write_json(path, existing, dry_run)


# --------------------------------------------------------------------------
# Generators
# --------------------------------------------------------------------------

def os_platform_key():
    if IS_WINDOWS:
        return "windows"
    if IS_MAC:
        return "osx"
    return "linux"


def path_separator():
    return ";" if IS_WINDOWS else ":"


def build_settings_update(tc: Toolchain):
    sep = path_separator()
    path_value = sep.join(str(d) for d in tc.path_dirs()) + sep + "${env:PATH}"
    key = f"terminal.integrated.env.{os_platform_key()}"
    return {
        key: {"PATH": path_value},
        "cmake.additionalKits": ["${workspaceFolder}/.vscode/cmake-kits.json"],
        "cmake.configureArgs": [f"-DCMAKE_MAKE_PROGRAM={tc.ninja}"],
        "C_Cpp.default.compilerPath": str(tc.gcc),
    }


def build_kits(tc: Toolchain):
    return [
        {
            "name": "STM32CubeCLT Toolchain",
            "compilers": {"C": str(tc.gcc), "CXX": str(tc.gxx)},
        }
    ]


def build_tasks(tc: Toolchain, project_name: str, use_presets: bool):
    # The CubeMX toolchain file resolves the compiler by bare name
    # (arm-none-eabi-gcc), so it must be resolvable on PATH at configure/build
    # time. Rather than relying on terminal.integrated.env being applied to
    # task shells, set PATH explicitly for every task here - guaranteed to
    # work regardless of shell/profile.
    sep = path_separator()
    task_path = sep.join(str(d) for d in tc.path_dirs()) + sep + "${env:PATH}"
    global_options = {"cwd": "${workspaceFolder}", "env": {"PATH": task_path}}

    def configure(config):
        args = ["--preset", config] if use_presets else [
            "-S", ".", "-B", f"build/{config}",
            "-G", "Ninja",
            f"-DCMAKE_BUILD_TYPE={config}",
            "-DCMAKE_TOOLCHAIN_FILE=cmake/gcc-arm-none-eabi.cmake",
            f"-DCMAKE_MAKE_PROGRAM={tc.ninja}",
        ]
        return {
            "label": f"CMake: Configure ({config})",
            "type": "shell",
            "command": str(tc.cmake),
            "args": args,
            "problemMatcher": ["$gcc"],
        }

    def build(config):
        return {
            "label": f"Build ({config})",
            "type": "shell",
            "command": str(tc.cmake),
            "args": ["--build", f"build/{config}"],
            "dependsOn": [f"CMake: Configure ({config})"],
            "dependsOrder": "sequence",
            "problemMatcher": ["$gcc"],
            "group": {"kind": "build", "isDefault": config == "Debug"},
        }

    def flash(config):
        return {
            "label": f"Flash ({config})",
            "type": "shell",
            "command": str(tc.programmer_cli),
            "args": [
                "--connect", "port=SWD", "mode=NORMAL", "reset=HWrst",
                "--download", f"${{workspaceFolder}}/build/{config}/{project_name}.elf",
                "--start",
            ],
            "problemMatcher": [],
            "group": "build",
        }

    def build_flash(config):
        return {
            "label": f"Build + Flash ({config})",
            "dependsOn": [f"Build ({config})", f"Flash ({config})"],
            "dependsOrder": "sequence",
            "problemMatcher": [],
            "group": "build",
        }

    def clean(config):
        return {
            "label": f"Clean ({config})",
            "type": "shell",
            "command": str(tc.cmake),
            "args": ["--build", f"build/{config}", "--target", "clean"],
            "problemMatcher": [],
        }

    tasks = []
    for config in ("Debug", "Release"):
        tasks += [configure(config), build(config), flash(config), build_flash(config), clean(config)]

    return {"version": "2.0.0", "options": global_options, "tasks": tasks}


def build_launch(tc: Toolchain, project_name: str, device: str, svd_path):
    def config(name, build_first: bool):
        cfg = {
            "name": name,
            "type": "cortex-debug",
            "request": "launch",
            "servertype": "stlink",
            "cwd": "${workspaceFolder}",
            "executable": "${workspaceFolder}/build/Debug/" + project_name + ".elf",
            "device": device or "REPLACE_WITH_YOUR_MCU_DEVICE",
            "interface": "swd",
            "runToEntryPoint": "main",
            "serverpath": str(tc.gdbserver),
            "armToolchainPath": str(tc.clt_root / "GNU-tools-for-STM32" / "bin"),
            "gdbPath": str(tc.gdb),
            "stm32cubeprogrammer": str(tc.programmer_bin_dir),
        }
        if svd_path:
            cfg["svdFile"] = str(svd_path)
        if build_first:
            cfg["preLaunchTask"] = "Build (Debug)"
        return cfg

    return {
        "version": "0.2.0",
        "configurations": [
            config("Debug (Build + Flash)", build_first=True),
            config("Debug (No Rebuild)", build_first=False),
        ],
    }


def build_cpp_properties(tc: Toolchain):
    return {
        "configurations": [
            {
                "name": "STM32",
                "compileCommands": "${workspaceFolder}/build/Debug/compile_commands.json",
                "compilerPath": str(tc.gcc),
                "cStandard": "gnu11",
                "cppStandard": "gnu++14",
            }
        ],
        "version": 4,
    }


def build_extensions():
    return {
        "recommendations": [
            "marus25.cortex-debug",
            "ms-vscode.cpptools",
            "ms-vscode.cmake-tools",
        ]
    }


def update_gitignore(project_dir: Path, dry_run: bool):
    gitignore = project_dir / ".gitignore"
    wanted = ["build/", ".vscode/"]
    lines = []
    if gitignore.is_file():
        lines = read_text(gitignore).splitlines()
    to_add = [w for w in wanted if w not in lines]
    if not to_add:
        return
    print(f"  update {gitignore} (+{', '.join(to_add)})" + (" (dry-run)" if dry_run else ""))
    if dry_run:
        return
    with gitignore.open("a", encoding="utf-8") as f:
        if lines and lines[-1] != "":
            f.write("\n")
        for w in to_add:
            f.write(w + "\n")


# --------------------------------------------------------------------------
# Main
# --------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("project_dir", nargs="?", default=".", help="Path to the CubeMX-generated project (default: current dir)")
    parser.add_argument("--clt-path", help="Path to your STM32CubeCLT_x.y.z install (auto-detected if omitted)")
    parser.add_argument("--device", help="Override auto-detected MCU device string for cortex-debug (e.g. STM32H7A3xx)")
    parser.add_argument("-y", "--yes", action="store_true", help="Non-interactive: pick defaults instead of prompting")
    parser.add_argument("--dry-run", action="store_true", help="Print what would be written, without writing files")
    args = parser.parse_args()

    project_dir = Path(args.project_dir).expanduser().resolve()
    if not project_dir.is_dir():
        print(f"ERROR: {project_dir} is not a directory.")
        sys.exit(1)

    print(f"Project dir : {project_dir}")
    project_name = find_project_name(project_dir)
    print(f"Project name: {project_name}")

    clt_root = pick_clt_root(args)
    print(f"CubeCLT root: {clt_root}")
    tc = Toolchain(clt_root)
    missing = tc.check()
    if missing:
        print("WARNING: some expected tools were not found:")
        print("\n".join(missing))
        print("Generated files will still point at these paths - fix your CLT install/path if builds fail.")

    device = args.device or find_device(project_dir)
    if device:
        print(f"MCU device  : {device}")
    else:
        print("WARNING: could not auto-detect MCU device string; pass --device STM32XXxx and re-run, "
              "or edit .vscode/launch.json afterwards.")

    svd_path = find_svd(tc, device) if device else None
    if svd_path:
        print(f"SVD file    : {svd_path}")

    use_presets = has_cmake_presets(project_dir)
    print(f"CMakePresets.json found: {use_presets}")

    vscode_dir = project_dir / ".vscode"
    print(f"\nWriting VS Code config into {vscode_dir}")

    merge_json(vscode_dir / "settings.json", build_settings_update(tc), args.dry_run)
    write_json(vscode_dir / "cmake-kits.json", build_kits(tc), args.dry_run)
    write_json(vscode_dir / "tasks.json", build_tasks(tc, project_name, use_presets), args.dry_run)
    write_json(vscode_dir / "launch.json", build_launch(tc, project_name, device, svd_path), args.dry_run)
    write_json(vscode_dir / "c_cpp_properties.json", build_cpp_properties(tc), args.dry_run)
    write_json(vscode_dir / "extensions.json", build_extensions(), args.dry_run)
    update_gitignore(project_dir, args.dry_run)

    print("\nDone. Next steps:")
    print("  1. Open the project folder in VS Code.")
    print("  2. Install the recommended extensions if prompted (Cortex-Debug, C/C++, CMake Tools).")
    print("  3. Ctrl+Shift+B (Cmd+Shift+B on macOS) -> 'Build (Debug)' is the default build task.")
    print("  4. Terminal > Run Task... for 'Build + Flash (Debug)' / '(Release)' variants.")
    print("  5. F5 -> 'Debug (Build + Flash)' to build, flash and start debugging in one go,")
    print("     or pick 'Debug (No Rebuild)' to just re-flash+debug the existing binary.")
    if not device:
        print("\n  NOTE: set the correct 'device' field in .vscode/launch.json before debugging.")


if __name__ == "__main__":
    main()
