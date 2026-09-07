"""Generate editor configuration for an embedded CMake project - build, flash
and debug entries that drive the project's toolchain, without needing that
toolchain on your global PATH or installing a vendor's all-in-one extension.

Two axes, both pluggable:
  * profiles/ - which toolchain (STM32CubeCLT, ...)  -> where the tools live
  * editors/  - which editor (VS Code, Zed, ...)     -> what files to write

Run it again any time (new machine, new clone, toolchain upgrade) to
regenerate the config with the paths that are correct for that machine.
"""
import argparse
import os
import re
import sys
from pathlib import Path

from editors import DEFAULT_EDITOR, EDITORS
from editors.base import ProjectContext
from profiles import PROFILES, detect_profile
from profiles.base import ToolchainProfile
from fileio import load_json, read_text


# --------------------------------------------------------------------------
# Project introspection (vendor-neutral: just reads CMakeLists.txt)
# --------------------------------------------------------------------------

def find_project_name(project_dir: Path) -> str:
    cmakelists = project_dir / "CMakeLists.txt"
    if not cmakelists.is_file():
        print(f"ERROR: no CMakeLists.txt in {project_dir}. Point this script at "
              f"the folder your CMake project was generated into.")
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


def has_cmake_presets(project_dir: Path) -> bool:
    return isinstance(load_json(project_dir / "CMakePresets.json"), dict)


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
    # Every other flag has to be declared here too, including the profiles'
    # own: an option argparse does not know about leaves its *value* looking
    # like the positional, so `--editor zed` would be read as project_dir.
    pre.add_argument("--editor")
    pre.add_argument("--device")
    pre.add_argument("-y", "--yes", action="store_true")
    pre.add_argument("--dry-run", action="store_true")
    for cls in PROFILES.values():
        try:
            cls().add_cli_arguments(pre)
        except argparse.ArgumentError:
            pass  # two profiles sharing a flag name: first one wins, fine here
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


def _selected_editors(value: str):
    if value == "all":
        return list(EDITORS)
    return [name.strip() for name in value.split(",") if name.strip()]


def main():
    argv = sys.argv[1:]
    profile, _ = _resolve_profile(argv)

    default_editor = os.environ.get("STM32_INIT_EDITOR", DEFAULT_EDITOR)

    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("project_dir", nargs="?", default=".", help="Path to the project (default: current dir)")
    parser.add_argument("--editor", default=default_editor,
                        help=f"Editor config to generate: {', '.join(EDITORS)}, a comma-separated "
                             f"list of those, or 'all' (default: {default_editor}; "
                             f"override with $STM32_INIT_EDITOR)")
    parser.add_argument("--profile", choices=list(PROFILES), help="Force a toolchain profile instead of auto-detecting")
    parser.add_argument("--device", help="Override auto-detected MCU device string for the debugger (e.g. STM32H7A3xx)")
    parser.add_argument("-y", "--yes", action="store_true", help="Non-interactive: pick defaults instead of prompting")
    parser.add_argument("--dry-run", action="store_true", help="Print what would be written, without writing files")
    profile.add_cli_arguments(parser)
    args = parser.parse_args(argv)

    editor_names = _selected_editors(args.editor)
    unknown = [name for name in editor_names if name not in EDITORS]
    if unknown or not editor_names:
        print(f"ERROR: unknown editor {', '.join(unknown) or '(none given)'}. "
              f"Choose from: {', '.join(EDITORS)}, or 'all'.")
        sys.exit(1)

    project_dir = Path(args.project_dir).expanduser().resolve()
    if not project_dir.is_dir():
        print(f"ERROR: {project_dir} is not a directory.")
        sys.exit(1)

    print(f"Project dir : {project_dir}")
    print(f"Profile     : {profile.name}")
    print(f"Editor(s)   : {', '.join(editor_names)}")
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
              "or edit the generated debug configuration afterwards.")

    svd_path = profile.find_svd(tc, device) if device else None
    if svd_path:
        print(f"SVD file    : {svd_path}")

    use_presets = has_cmake_presets(project_dir)
    print(f"CMakePresets.json found: {use_presets}")

    ctx = ProjectContext(
        project_dir=project_dir,
        project_name=project_name,
        profile=profile,
        tc=tc,
        device=device,
        svd_path=svd_path,
        use_presets=use_presets,
        dry_run=args.dry_run,
    )

    backends = [EDITORS[name]() for name in editor_names]
    for backend in backends:
        backend.generate(ctx)

    print("\nDone. Next steps:")
    for backend in backends:
        steps = backend.next_steps(ctx)
        if len(backends) > 1:
            print(f"\n  [{backend.name}]")
        n = 0
        for step in steps:
            if step.startswith("  "):   # continuation of the previous step
                print(f"   {step}")
                continue
            n += 1
            print(f"  {n}. {step}")


if __name__ == "__main__":
    main()
