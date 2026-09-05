# stm32-vscode-init

Turns an STM32CubeMX-generated CMake project into a ready-to-use VS Code
workspace, wired to **STM32CubeCLT**, without adding anything to your
global PATH/environment. Works on Windows, Linux and macOS.

Run it once per machine (or after cloning a project onto a new machine, or
after upgrading STM32CubeCLT) and it regenerates `.vscode/` with the correct
absolute paths for that machine.

## Prerequisites

- [STM32CubeMX](https://www.st.com/en/development-tools/stm32cubemx.html)
  project generated with **Toolchain / IDE = CMake**.
- [STM32CubeCLT](https://www.st.com/en/development-tools/stm32cubeclt.html)
  installed (default install location is auto-detected).
- Python 3.8+ (only needed to run this generator, not for building).
- VS Code with the **Cortex-Debug**, **C/C++**, and (optional) **CMake
  Tools** extensions - the script writes `.vscode/extensions.json` so VS
  Code will offer to install them for you.

## Usage

```bash
# Linux / macOS
./init.sh /path/to/your/CubeMX/project

# Windows (PowerShell)
.\init.ps1 C:\path\to\your\CubeMX\project

# or directly with python
python3 stm32_vscode_init.py /path/to/your/CubeMX/project
```

Run with no argument to target the current directory. Useful flags:

| Flag | Purpose |
|---|---|
| `--clt-path <dir>` | Point at a specific STM32CubeCLT install instead of auto-detecting |
| `--device STM32XXxx` | Override the MCU device string used by the debugger, if auto-detection fails |
| `-y` / `--yes` | Non-interactive (picks the newest auto-detected CLT install without prompting) |
| `--dry-run` | Print what would be written without touching any files |

The script only ever writes inside `<project>/.vscode/` and appends
`build/` and `.vscode/` to `<project>/.gitignore` (these are
machine-specific / generated, so they shouldn't be committed - re-run the
script after cloning on another machine instead).

## What it generates

- **`.vscode/tasks.json`** - build tasks, each with the correct
  STM32CubeCLT `bin` directories injected into `PATH` for that task only
  (nothing leaks into your shell/global environment):
  - `Build (Debug)` - default build task (Ctrl+Shift+B / Cmd+Shift+B)
  - `Build (Release)`
  - `Flash (Debug)` / `Flash (Release)` - flashes the existing ELF via
    `STM32_Programmer_CLI`
  - `Build + Flash (Debug)` / `Build + Flash (Release)`
  - `Clean (Debug)` / `Clean (Release)`
- **`.vscode/launch.json`** - Cortex-Debug configurations (F5):
  - `Debug (Build + Flash)` - rebuilds first, then flashes and starts
    debugging (this is your build+flash+debug workflow)
  - `Debug (No Rebuild)` - flashes and debugs whatever is already built
    (your plain "debug" workflow)
- **`.vscode/settings.json`** - `terminal.integrated.env.<os>` PATH (so
  the integrated terminal can also run bare `cmake`/`ninja`/etc.), plus
  CMake Tools kit/configure-arg settings.
- **`.vscode/cmake-kits.json`** - a CMake Tools kit pointing at the
  STM32CubeCLT ARM GCC compilers (only used if you also use the CMake
  Tools extension's own configure UI; the tasks above don't depend on it).
- **`.vscode/c_cpp_properties.json`** - IntelliSense config, pointed at
  `build/Debug/compile_commands.json` (CubeMX's generated CMakeLists.txt
  already enables `CMAKE_EXPORT_COMPILE_COMMANDS`).
- **`.vscode/extensions.json`** - recommends Cortex-Debug, C/C++, CMake
  Tools.

Requested workflow -> generated entry:

| Workflow | Where |
|---|---|
| build | Task: `Build (Debug)` (default) |
| build debug | Task: `Build (Debug)` |
| build release | Task: `Build (Release)` |
| build+flash | Task: `Build + Flash (Debug)` |
| build+flash+debug | Launch: `Debug (Build + Flash)` (F5) |
| debug | Launch: `Debug (No Rebuild)` |

## How it works

- **Toolchain paths**: the script looks for STM32CubeCLT in its default
  install locations (`C:\ST\STM32CubeCLT_*` on Windows,
  `/opt/st/stm32cubeclt_*` or `/opt/ST/STM32CubeCLT_*` on Linux/macOS), or
  wherever you point it with `--clt-path`. It resolves `arm-none-eabi-gcc`,
  `cmake`, `ninja`, `STM32_Programmer_CLI` and `ST-LINK_gdbserver` under
  that root.
- **Why PATH is injected per-task**: STM32CubeMX's generated
  `cmake/gcc-arm-none-eabi.cmake` toolchain file looks up the compiler by
  bare name (`arm-none-eabi-gcc`), so it must be resolvable on `PATH` at
  configure time. Rather than requiring you to add STM32CubeCLT to your
  system PATH, each generated task sets `PATH` for itself via
  `options.env` in `tasks.json`.
- **Project name / MCU device**: parsed from `CMakeLists.txt`
  (`CMAKE_PROJECT_NAME`) and from the compile definitions in
  `cmake/stm32cubemx/CMakeLists.txt` (e.g. `STM32H7A3xxQ` -> device
  `STM32H7A3xx`). If the device can't be parsed, pass `--device` yourself.
- **CMakePresets.json**: if CubeMX generated one (recent CubeMX versions
  do), tasks use `cmake --preset <Debug|Release>`; otherwise the script
  falls back to an explicit `-B build/<config>` invocation.
- **Debugging**: uses Cortex-Debug's `servertype: "stlink"`, pointed at
  STM32CubeCLT's official `ST-LINK_gdbserver` and `STM32CubeProgrammer`
  (via `serverpath` / `stm32cubeprogrammer`) - not the third-party
  `st-util`. If a matching `.svd` file is bundled in STM32CubeCLT, it's
  wired in automatically for the peripheral register view.

## Troubleshooting

- **"compiler not found" during configure**: double-check `--clt-path`, or
  that your STM32CubeCLT install has a `GNU-tools-for-STM32/bin` folder.
- **Flash fails to connect**: check the board is plugged in and `-c
  port=SWD` matches your debug probe; try running the exact command from
  `tasks.json` in a terminal to see the full STM32_Programmer_CLI output.
- **Debug session won't start**: run `.vscode/launch.json`'s `serverpath`
  binary by hand once to confirm it starts, and check the Cortex-Debug
  output channel in VS Code for the actual error.
- Re-run the script any time paths look stale (new CLT version, project
  moved, cloned to a new machine) - it's idempotent and backs up any file
  it overwrites as `<file>.bak`.
