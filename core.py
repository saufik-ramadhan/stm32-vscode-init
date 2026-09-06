"""
Generate a .vscode/ configuration for an embedded CMake project - builds,
flashes and debugs using whichever toolchain profile matches the project
(see profiles/), without needing that toolchain on your global PATH or
installing a vendor's all-in-one VS Code extension.

Run it again any time (new machine, new clone, toolchain upgrade) to
regenerate the .vscode/ files with the paths that are correct for that
machine.
"""
import argparse
import json
import shutil
import sys
from pathlib import Path

from osutil import os_platform_key, path_separator
from profiles import PROFILES, detect_profile
from profiles.base import ToolchainProfile


# --------------------------------------------------------------------------
# Project introspection (vendor-neutral: just reads CMakeLists.txt)
# --------------------------------------------------------------------------

def read_text(path: Path) -> str:
    return path.read_text(encoding="utf-8", errors="ignore")


def find_project_name(project_dir: Path) -> str:
    cmakelists = project_dir / "CMakeLists.txt"
    if not cmakelists.is_file():
        print(f"ERROR: no CMakeLists.txt in {project_dir}. Point this script at "
              f"the folder your CMake project was generated into.")
        sys.exit(1)
    text = read_text(cmakelists)
    import re
    m = re.search(r'set\(\s*CMAKE_PROJECT_NAME\s+([A-Za-z0-9_\-]+)', text)
    if m:
        return m.group(1)
    m = re.search(r'project\(\s*\$?\{?([A-Za-z0-9_\-]+)', text)
    if m:
        return m.group(1)
    print("ERROR: could not determine project name from CMakeLists.txt.")
    sys.exit(1)


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
# Generators - only ever talk to the toolchain through the profile contract
# --------------------------------------------------------------------------

def build_settings_update(tc, profile):
    sep = path_separator()
    path_value = sep.join(str(d) for d in tc.extra_path_dirs) + sep + "${env:PATH}"
    key = f"terminal.integrated.env.{os_platform_key()}"
    settings = {
        key: {"PATH": path_value},
        "cmake.additionalKits": ["${workspaceFolder}/.vscode/cmake-kits.json"],
        "C_Cpp.default.compilerPath": str(tc.gcc),
    }
    settings.update(profile.extra_settings(tc))
    return settings


def build_kits(tc, profile):
    return [
        {
            "name": f"{profile.name} Toolchain",
            "compilers": {"C": str(tc.gcc), "CXX": str(tc.gxx)},
        }
    ]


def build_tasks(tc, profile, project_name: str, use_presets: bool):
    # Most CMake toolchain files resolve the compiler by bare name (e.g.
    # arm-none-eabi-gcc), so it must be resolvable on PATH at configure/build
    # time. Rather than relying on terminal.integrated.env being applied to
    # task shells, set PATH explicitly for every task here - guaranteed to
    # work regardless of shell/profile.
    sep = path_separator()
    task_path = sep.join(str(d) for d in tc.extra_path_dirs) + sep + "${env:PATH}"
    global_options = {"cwd": "${workspaceFolder}", "env": {"PATH": task_path}}

    def configure(config):
        args = ["--preset", config] if use_presets else [
            "-S", ".", "-B", f"build/{config}",
            "-G", "Ninja",
            f"-DCMAKE_BUILD_TYPE={config}",
            f"-DCMAKE_TOOLCHAIN_FILE={profile.toolchain_cmake_file}",
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
        frag = profile.flash_task(tc, project_name, config)
        return {
            "label": f"Flash ({config})",
            "type": "shell",
            "command": frag["command"],
            "args": frag["args"],
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


def build_launch(tc, profile, project_name: str, device, svd_path):
    debug = profile.debug_config(tc, project_name, device, svd_path)

    def config(name, build_first: bool):
        cfg = {
            "name": name,
            "type": "cortex-debug",
            "request": "launch",
            "cwd": "${workspaceFolder}",
            "executable": "${workspaceFolder}/build/Debug/" + project_name + ".elf",
            "device": debug.device,
            "runToEntryPoint": "main",
        }
        cfg.update(debug.fields)
        if debug.svd_file:
            cfg["svdFile"] = str(debug.svd_file)
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


def build_cpp_properties(tc, profile):
    return {
        "configurations": [
            {
                "name": profile.name,
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

def _resolve_profile(argv):
    """First-pass parse: figure out project_dir and which profile applies,
    before building the real parser (a profile can add its own CLI flags,
    e.g. --clt-path, which argparse needs to know about up front)."""
    pre = argparse.ArgumentParser(add_help=False)
    pre.add_argument("project_dir", nargs="?", default=".")
    pre.add_argument("--profile", choices=list(PROFILES))
    pre_args, _ = pre.parse_known_args(argv)

    project_dir = Path(pre_args.project_dir).expanduser().resolve()

    if pre_args.profile:
        return PROFILES[pre_args.profile](), project_dir

    profile = detect_profile(project_dir)
    if profile is None:
        if "-h" in argv or "--help" in argv:
            # Let --help still work outside a recognized project; profile-
            # specific flags just won't be listed.
            return ToolchainProfile(), project_dir
        print(f"ERROR: could not auto-detect a toolchain profile for {project_dir}.")
        print(f"Pass --profile <{'|'.join(PROFILES)}> explicitly.")
        sys.exit(1)
    return profile, project_dir


def main():
    argv = sys.argv[1:]
    profile, _ = _resolve_profile(argv)

    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("project_dir", nargs="?", default=".", help="Path to the project (default: current dir)")
    parser.add_argument("--profile", choices=list(PROFILES), help="Force a toolchain profile instead of auto-detecting")
    parser.add_argument("--device", help="Override auto-detected MCU device string for cortex-debug (e.g. STM32H7A3xx)")
    parser.add_argument("-y", "--yes", action="store_true", help="Non-interactive: pick defaults instead of prompting")
    parser.add_argument("--dry-run", action="store_true", help="Print what would be written, without writing files")
    profile.add_cli_arguments(parser)
    args = parser.parse_args(argv)

    project_dir = Path(args.project_dir).expanduser().resolve()
    if not project_dir.is_dir():
        print(f"ERROR: {project_dir} is not a directory.")
        sys.exit(1)

    print(f"Project dir : {project_dir}")
    print(f"Profile     : {profile.name}")
    project_name = find_project_name(project_dir)
    print(f"Project name: {project_name}")

    tc = profile.resolve_toolchain(project_dir, args)
    missing = profile.check(tc)
    if missing:
        print("WARNING: some expected tools were not found:")
        print("\n".join(missing))
        print("Generated files will still point at these paths - fix your toolchain install/path if builds fail.")

    device = args.device or profile.detect_device(project_dir)
    if device:
        print(f"MCU device  : {device}")
    else:
        print("WARNING: could not auto-detect MCU device string; pass --device STM32XXxx and re-run, "
              "or edit .vscode/launch.json afterwards.")

    svd_path = profile.find_svd(tc, device) if device else None
    if svd_path:
        print(f"SVD file    : {svd_path}")

    use_presets = has_cmake_presets(project_dir)
    print(f"CMakePresets.json found: {use_presets}")

    vscode_dir = project_dir / ".vscode"
    print(f"\nWriting VS Code config into {vscode_dir}")

    merge_json(vscode_dir / "settings.json", build_settings_update(tc, profile), args.dry_run)
    write_json(vscode_dir / "cmake-kits.json", build_kits(tc, profile), args.dry_run)
    write_json(vscode_dir / "tasks.json", build_tasks(tc, profile, project_name, use_presets), args.dry_run)
    write_json(vscode_dir / "launch.json", build_launch(tc, profile, project_name, device, svd_path), args.dry_run)
    write_json(vscode_dir / "c_cpp_properties.json", build_cpp_properties(tc, profile), args.dry_run)
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
