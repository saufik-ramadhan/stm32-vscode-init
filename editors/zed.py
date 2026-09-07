"""Zed backend: .zed/tasks.json, .zed/settings.json, .zed/debug.json, .clangd
and a CMakeUserPresets.json that carries the toolchain PATH.

Design notes (why this differs from the VS Code backend):

* Zed does not expand ${env:PATH} inside a task's "env" values, so PATH is
  never injected per task. Instead every task calls tools by absolute path,
  and the one step that genuinely needs the toolchain on PATH - CMake
  configure, because CubeMX's toolchain file resolves `arm-none-eabi-gcc` by
  bare name - gets it from CMakeUserPresets.json, where CMake itself expands
  $penv{PATH}. That also makes `cmake --preset ...` work from a plain shell.
* Zed has no dependsOn/dependsOrder, so chained tasks (build + flash) are a
  single shell command joined with &&.
* Zed's built-in GDB debug adapter drives `gdb -i dap`, which only exists in
  a GDB built with Python. ST's arm-none-eabi-gdb is built --without-python,
  so .zed/debug.json is only written when a GDB that has both DAP support and
  ARM target support is found (typically gdb-multiarch). Otherwise the
  gdbserver + GDB terminal tasks are the debugging path.
"""
import shutil
import subprocess
from pathlib import Path

from fileio import append_gitignore, load_json, merge_json, write_json, write_text
from editors.base import EditorBackend

ROOT = "$ZED_WORKTREE_ROOT"
GDB_PORT = 61234
GDB_SCRIPT = ".zed/attach.gdb"


def _q(s: str) -> str:
    """Quote a path for the shell command strings Zed runs."""
    s = str(s)
    return f'"{s}"' if " " in s else s


def _join(command, args) -> str:
    return " ".join([_q(command)] + [_q(a) for a in args])


def _preset(ctx, config: str) -> str:
    return f"{ctx.profile.preset_prefix}-{config}"


def _build_dir(ctx, config: str) -> str:
    return f"build/{_preset(ctx, config)}"


def _elf(ctx, config: str) -> str:
    """Path to the built firmware, relative to the project root.

    Every task sets cwd to the worktree root, so relative paths resolve without
    relying on Zed substituting variables inside task arguments."""
    return f"{_build_dir(ctx, config)}/{ctx.project_name}.elf"


# --------------------------------------------------------------------------
# CMakeUserPresets.json - carries the toolchain PATH into every cmake call
# --------------------------------------------------------------------------

def _path_env(tc) -> str:
    # CMake expands $penv{...} (parent environment) itself, on every platform.
    sep = ";" if any("\\" in str(d) for d in tc.extra_path_dirs) else ":"
    return sep.join(str(d) for d in tc.extra_path_dirs) + sep + "$penv{PATH}"


def _base_preset_names(ctx):
    """Configure-preset names in the project's CMakePresets.json we can inherit
    from, or None if there is nothing usable to inherit."""
    if not ctx.use_presets:
        return None
    data = load_json(ctx.project_dir / "CMakePresets.json")
    if not isinstance(data, dict):
        return None
    names = {p.get("name") for p in data.get("configurePresets", []) if isinstance(p, dict)}
    if {"Debug", "Release"} <= names:
        return {"Debug": "Debug", "Release": "Release"}
    return None


def build_user_presets(ctx):
    """CMakeUserPresets.json defining <prefix>-Debug / <prefix>-Release.

    Inherits the project's own presets when CubeMX generated them, and stands
    on its own (generator, toolchain file, build dir) when it did not - CMake
    accepts CMakeUserPresets.json with no CMakePresets.json alongside it.
    """
    tc = ctx.tc
    inherit = _base_preset_names(ctx)

    shared = {
        "name": ctx.profile.preset_prefix,
        "hidden": True,
        "environment": {"PATH": _path_env(tc)},
        "cacheVariables": {"CMAKE_MAKE_PROGRAM": str(tc.ninja)},
    }
    if not inherit:
        shared["generator"] = "Ninja"
        shared["binaryDir"] = "${sourceDir}/build/${presetName}"
        shared["toolchainFile"] = "${sourceDir}/" + ctx.profile.toolchain_cmake_file
        shared["cacheVariables"]["CMAKE_EXPORT_COMPILE_COMMANDS"] = "ON"

    configure_presets = [shared]
    for config in ("Debug", "Release"):
        preset = {
            "name": _preset(ctx, config),
            "displayName": f"{ctx.profile.name} {config}",
            "inherits": [ctx.profile.preset_prefix] + ([inherit[config]] if inherit else []),
        }
        if not inherit:
            preset["cacheVariables"] = {"CMAKE_BUILD_TYPE": config}
        configure_presets.append(preset)

    return {
        "version": 3,
        "configurePresets": configure_presets,
        "buildPresets": [
            {"name": _preset(ctx, c), "configurePreset": _preset(ctx, c)}
            for c in ("Debug", "Release")
        ],
    }


# --------------------------------------------------------------------------
# .zed/tasks.json
# --------------------------------------------------------------------------

def build_tasks(ctx):
    tc, profile = ctx.tc, ctx.profile
    prefix = f"{profile.task_prefix}: " if profile.task_prefix else ""
    tasks = []

    def task(label, command, args=None, **kw):
        entry = {"label": prefix + label, "command": str(command), "cwd": ROOT}
        if args:
            entry["args"] = [str(a) for a in args]
        entry["reveal"] = "always"
        entry.update(kw)
        tasks.append(entry)

    for config in ("Debug", "Release"):
        preset, elf = _preset(ctx, config), _elf(ctx, config)
        flash = profile.flash_task(tc, elf)

        task(f"Configure ({config})", tc.cmake, ["--preset", preset])
        task(f"Build ({config})", tc.cmake, ["--build", "--preset", preset], save="all")
        task(f"Rebuild ({config})", tc.cmake,
             ["--build", "--preset", preset, "--clean-first"], save="all")
        task(f"Clean ({config})", tc.cmake,
             ["--build", "--preset", preset, "--target", "clean"])
        task(f"Flash ({config})", flash["command"], flash["args"])
        # No dependsOn in Zed - chain in the shell instead.
        task(f"Build + Flash ({config})",
             _join(tc.cmake, ["--build", "--preset", preset]) + " && " +
             _join(flash["command"], flash["args"]),
             save="all")

    erase = profile.erase_task(tc)
    if erase:
        task("Erase chip", erase["command"], erase["args"])

    size = tc.gcc.parent / tc.gcc.name.replace("gcc", "size")
    task("Size report (Debug)", size, ["-A", "-x", _elf(ctx, "Debug")])

    server = profile.gdb_server_task(tc, GDB_PORT)
    if server:
        task(f"GDB server (port {GDB_PORT})", server["command"], server["args"],
             use_new_terminal=True, allow_concurrent_runs=False)
        # The attach commands live in .zed/attach.gdb rather than in -ex
        # arguments: no task argument then contains a space, so nothing depends
        # on how Zed quotes args when it hands the task to a shell.
        task("GDB attach (Debug)", tc.gdb,
             ["-x", GDB_SCRIPT, _elf(ctx, "Debug")], use_new_terminal=True)

    return tasks


def build_gdb_script(ctx) -> str:
    return (
        "# Sourced by the 'GDB attach' task. Edit freely - it is only\n"
        "# rewritten when you re-run stm32-init.\n"
        f"target extended-remote localhost:{GDB_PORT}\n"
        "load\n"
        "monitor reset\n"
        "tbreak main\n"
        "continue\n"
    )


# --------------------------------------------------------------------------
# .zed/settings.json and .clangd
# --------------------------------------------------------------------------

def build_settings(ctx, dap_gdb):
    settings = {
        "lsp": {
            "clangd": {
                "binary": {
                    "arguments": [
                        f"--query-driver={ctx.profile.clangd_query_driver(ctx.tc)}",
                        "--background-index",
                        "--clang-tidy",
                        "--completion-style=detailed",
                        "--header-insertion=never",
                        "--pch-storage=memory",
                    ]
                }
            }
        },
        "file_scan_exclusions": [
            "**/.git", "**/.svn", "**/.hg", "**/.jj", "**/CVS",
            "**/.DS_Store", "**/Thumbs.db", "**/.classpath", "**/.settings",
            "**/build", "**/.cache",
        ],
    }
    if dap_gdb:
        settings["dap"] = {"GDB": {"binary": str(dap_gdb)}}
    return settings


def build_clangd(ctx) -> str:
    return (
        "# Generated by stm32-init. clangd reads the compile_commands.json that\n"
        f"# `cmake --preset {_preset(ctx, 'Debug')}` writes into this directory.\n"
        "CompileFlags:\n"
        f"  CompilationDatabase: {_build_dir(ctx, 'Debug')}\n"
        "\n"
        "Diagnostics:\n"
        "  UnusedIncludes: None\n"
        "  MissingIncludes: None\n"
    )


def build_env_sh(ctx) -> str:
    dirs = ":".join(str(d) for d in ctx.tc.extra_path_dirs)
    return (
        "# Put the toolchain on PATH for the current shell only:\n"
        "#   source .zed/env.sh\n"
        "# Not needed for the tasks in .zed/tasks.json (they use absolute paths)\n"
        "# or for `cmake --preset ...` (CMakeUserPresets.json sets PATH itself).\n"
        f'export PATH="{dirs}:$PATH"\n'
    )


# --------------------------------------------------------------------------
# .zed/debug.json - only when a GDB that speaks DAP *and* ARM exists
# --------------------------------------------------------------------------

def _gdb_speaks_dap(gdb: Path) -> bool:
    """GDB's DAP interpreter is implemented in Python, so a --without-python
    build (ST ships one) cannot serve Zed's GDB adapter."""
    try:
        out = subprocess.run([str(gdb), "--configuration"], capture_output=True,
                             text=True, timeout=15).stdout
    except (OSError, subprocess.SubprocessError):
        return False
    return "--with-python" in out


def _gdb_speaks_arm(gdb: Path) -> bool:
    try:
        res = subprocess.run([str(gdb), "-batch", "-ex", "set architecture arm"],
                             capture_output=True, text=True, timeout=15)
    except (OSError, subprocess.SubprocessError):
        return False
    return "Undefined item" not in (res.stdout + res.stderr)


def find_dap_gdb(ctx):
    """A GDB usable as Zed's GDB debug adapter for this target, or None."""
    seen, candidates = set(), []
    for name in ("gdb-multiarch", "arm-none-eabi-gdb", "gdb"):
        found = shutil.which(name)
        if found:
            candidates.append(Path(found))
    candidates.append(Path(ctx.tc.gdb))
    for gdb in candidates:
        key = str(gdb)
        if key in seen or not Path(gdb).is_file():
            continue
        seen.add(key)
        if _gdb_speaks_dap(gdb) and _gdb_speaks_arm(gdb):
            return gdb
    return None


def build_debug(ctx):
    prefix = f"{ctx.profile.task_prefix}: " if ctx.profile.task_prefix else ""
    return [
        {
            "label": f"Attach via {prefix}GDB server (Debug)",
            "adapter": "GDB",
            "request": "attach",
            "program": ctx.elf(ROOT, _build_dir(ctx, "Debug")),  # absolute: not run from a shell
            "target": f"localhost:{GDB_PORT}",
            "cwd": ROOT,
        }
    ]


# --------------------------------------------------------------------------

class ZedBackend(EditorBackend):
    name = "zed"

    def generate(self, ctx):
        out = ctx.project_dir / ".zed"
        print(f"\nWriting Zed config into {out}")

        dap_gdb = find_dap_gdb(ctx)
        self._dap_gdb = dap_gdb

        write_json(ctx.project_dir / "CMakeUserPresets.json", build_user_presets(ctx), ctx.dry_run)
        merge_json(out / "settings.json", build_settings(ctx, dap_gdb), ctx.dry_run, deep=True)
        write_json(out / "tasks.json", build_tasks(ctx), ctx.dry_run)
        write_text(out / "env.sh", build_env_sh(ctx), ctx.dry_run)
        write_text(ctx.project_dir / ".clangd", build_clangd(ctx), ctx.dry_run)
        if ctx.profile.gdb_server_task(ctx.tc, GDB_PORT):
            write_text(ctx.project_dir / GDB_SCRIPT, build_gdb_script(ctx), ctx.dry_run)
        if dap_gdb:
            print(f"  DAP-capable GDB: {dap_gdb}")
            write_json(out / "debug.json", build_debug(ctx), ctx.dry_run)
        else:
            print("  No DAP-capable ARM GDB found -> skipping .zed/debug.json "
                  "(use the GDB server + GDB attach tasks instead).")

        append_gitignore(ctx.project_dir, [
            "build/", ".cache/", "compile_commands.json",
            "CMakeUserPresets.json", ".zed/",
        ], ctx.dry_run)

    def next_steps(self, ctx):
        prefix = f"{ctx.profile.task_prefix}: " if ctx.profile.task_prefix else ""
        debug_preset = _preset(ctx, "Debug")
        steps = [
            f"Open the project in Zed:  zed {ctx.project_dir}",
            "task: spawn (ctrl-shift-p) -> "
            f"'{prefix}Configure (Debug)' once, then '{prefix}Build (Debug)'.",
            "  Afterwards task::Rerun (ctrl-alt-t / F4-style) repeats the last task.",
            f"'{prefix}Build + Flash (Debug)' builds and flashes in one task.",
            "clangd picks up "
            f"{_build_dir(ctx, 'Debug')}/compile_commands.json after the first configure.",
        ]
        if getattr(self, "_dap_gdb", None):
            steps.append(
                f"Debugging: run '{prefix}GDB server (port {GDB_PORT})', then start the "
                "'Attach via GDB server (Debug)' configuration from the debug panel."
            )
        else:
            steps.append(
                f"Debugging: run '{prefix}GDB server (port {GDB_PORT})', then "
                f"'{prefix}GDB attach (Debug)' - a GDB console in a second terminal tab."
            )
            steps.append(
                "  For Zed's graphical debugger, install a GDB with Python + ARM support "
                "(Debian/Ubuntu: sudo apt install gdb-multiarch) and re-run this script."
            )
        steps.append(f"From a plain shell, the same build is: cmake --build --preset {debug_preset}")
        return steps
