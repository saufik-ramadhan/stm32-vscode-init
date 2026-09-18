"""VS Code backend: tasks.json / launch.json / settings.json / cmake-kits.json
/ c_cpp_properties.json, wired to Cortex-Debug for stepping.

Build dirs are build/Debug and build/Release, driven by CMakePresets.json when
the project has one.
"""
from fileio import append_gitignore, merge_json, write_json
from editors.base import EditorBackend
from osutil import os_platform_key, path_separator

ROOT = "${workspaceFolder}"


def _path_value(tc):
    sep = path_separator()
    return sep.join(str(d) for d in tc.extra_path_dirs) + sep + "${env:PATH}"


def build_settings_update(ctx):
    tc, profile = ctx.tc, ctx.profile
    settings = {
        f"terminal.integrated.env.{os_platform_key()}": {"PATH": _path_value(tc)},
        "cmake.additionalKits": ["${workspaceFolder}/.vscode/cmake-kits.json"],
        "C_Cpp.default.compilerPath": str(tc.gcc),
    }
    settings.update(profile.extra_settings(tc))
    return settings


def build_kits(ctx):
    return [
        {
            "name": f"{ctx.profile.name} Toolchain",
            "compilers": {"C": str(ctx.tc.gcc), "CXX": str(ctx.tc.gxx)},
        }
    ]


def build_tasks(ctx):
    # Most CMake toolchain files resolve the compiler by bare name (e.g.
    # arm-none-eabi-gcc), so it must be resolvable on PATH at configure/build
    # time. Rather than relying on terminal.integrated.env being applied to
    # task shells, set PATH explicitly for every task here - guaranteed to
    # work regardless of shell/profile.
    tc, profile = ctx.tc, ctx.profile
    global_options = {"cwd": ROOT, "env": {"PATH": _path_value(tc)}}

    def configure(config):
        args = ["--preset", config] if ctx.use_presets else [
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
        frag = profile.flash_task(tc, ctx.elf(ROOT, f"build/{config}"))
        prepare_labels = []
        prepare_tasks = []
        for index, step in enumerate(frag.get("pre_commands", []), start=1):
            label = f"Prepare {frag.get('label', 'Flash')} ({config}) #{index}"
            prepare_labels.append(label)
            prepare_tasks.append({
                "label": label,
                "type": "shell",
                "command": step["command"],
                "args": step.get("args", []),
                "problemMatcher": [],
            })
        flash_task = {
            "label": f"{frag.get('label', 'Flash')} ({config})",
            "type": "shell",
            "command": frag["command"],
            "args": frag.get("args", []),
            "problemMatcher": [],
            "group": "build",
        }
        if prepare_labels:
            flash_task["dependsOn"] = prepare_labels
            flash_task["dependsOrder"] = "sequence"
        return prepare_tasks + [flash_task]

    def build_flash(config):
        frag = profile.flash_task(tc, ctx.elf(ROOT, f"build/{config}"))
        flash_label = frag.get("label", "Flash")
        return {
            "label": f"Build + {flash_label} ({config})",
            "dependsOn": [f"Build ({config})", f"{flash_label} ({config})"],
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
        tasks += [configure(config), build(config)]
        tasks += flash(config)
        tasks += [build_flash(config), clean(config)]

    return {"version": "2.0.0", "options": global_options, "tasks": tasks}


def build_launch(ctx):
    debug = ctx.profile.debug_config(ctx.tc, ctx.project_name, ctx.device, ctx.svd_path)

    def config(name, build_first: bool):
        cfg = {
            "name": name,
            "type": "cortex-debug",
            "request": "launch",
            "cwd": ROOT,
            "executable": ctx.elf(ROOT, "build/Debug"),
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


def build_cpp_properties(ctx):
    return {
        "configurations": [
            {
                "name": ctx.profile.name,
                "compileCommands": "${workspaceFolder}/build/Debug/compile_commands.json",
                "compilerPath": str(ctx.tc.gcc),
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


class VSCodeBackend(EditorBackend):
    name = "vscode"

    def generate(self, ctx):
        out = ctx.project_dir / ".vscode"
        print(f"\nWriting VS Code config into {out}")
        merge_json(out / "settings.json", build_settings_update(ctx), ctx.dry_run)
        write_json(out / "cmake-kits.json", build_kits(ctx), ctx.dry_run)
        write_json(out / "tasks.json", build_tasks(ctx), ctx.dry_run)
        write_json(out / "launch.json", build_launch(ctx), ctx.dry_run)
        write_json(out / "c_cpp_properties.json", build_cpp_properties(ctx), ctx.dry_run)
        write_json(out / "extensions.json", build_extensions(), ctx.dry_run)
        append_gitignore(ctx.project_dir, ["build/", ".vscode/"], ctx.dry_run)

    def next_steps(self, ctx):
        flash_label = ctx.profile.flash_task(
            ctx.tc, ctx.elf(ROOT, "build/Debug")
        ).get("label", "Flash")
        steps = [
            "Open the project folder in VS Code.",
            "Install the recommended extensions if prompted (Cortex-Debug, C/C++, CMake Tools).",
            "Ctrl+Shift+B (Cmd+Shift+B on macOS) -> 'Build (Debug)' is the default build task.",
            f"Terminal > Run Task... for 'Build + {flash_label} (Debug)' / '(Release)' variants.",
            "F5 -> 'Debug (Build + Flash)' to build, flash and start debugging in one go,",
            "  or pick 'Debug (No Rebuild)' to just re-flash+debug the existing binary.",
        ]
        if not ctx.device:
            steps.append("NOTE: set the correct 'device' field in .vscode/launch.json before debugging.")
        return steps
