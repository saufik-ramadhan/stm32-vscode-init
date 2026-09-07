# stm32-vscode-init

[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)

Turns an STM32CubeMX-generated CMake project into a ready-to-use **VS Code**
or **Zed** workspace, wired to **STM32CubeCLT**, without adding anything to
your global PATH/environment. Works on Windows, Linux and macOS.

Run it once per project (and again after cloning onto a new machine, or after
upgrading STM32CubeCLT) and it regenerates the editor config with the correct
absolute paths for that machine.

## Prerequisites

- [STM32CubeMX](https://www.st.com/en/development-tools/stm32cubemx.html)
  project generated with **Toolchain / IDE = CMake**.
- [STM32CubeCLT](https://www.st.com/en/development-tools/stm32cubeclt.html)
  installed (default install location is auto-detected).
- Python 3.8+ (only needed to run this generator, not for building).
- For VS Code: the **Cortex-Debug**, **C/C++**, and (optional) **CMake Tools**
  extensions - the script writes `.vscode/extensions.json` so VS Code will
  offer to install them for you.
- For Zed: nothing extra. Zed installs `clangd` itself the first time you open
  a C file.

## Usage

```bash
# Linux / macOS
./init.sh --editor zed /path/to/your/CubeMX/project

# Windows (PowerShell)
.\init.ps1 --editor zed C:\path\to\your\CubeMX\project

# or directly with python
python3 stm32_vscode_init.py --editor zed /path/to/your/CubeMX/project
```

Run with no project argument to target the current directory. Useful flags:

| Flag | Purpose |
|---|---|
| `--editor <name>` | `vscode` (default), `zed`, a comma-separated list, or `all` |
| `--clt-path <dir>` | Point at a specific STM32CubeCLT install instead of auto-detecting |
| `--device STM32XXxx` | Override the MCU device string used by the debugger, if auto-detection fails |
| `-y` / `--yes` | Non-interactive (picks the newest auto-detected CLT install without prompting) |
| `--dry-run` | Print what would be written without touching any files |

Set `STM32_INIT_EDITOR=zed` in your shell profile to make Zed the default and
drop the flag. A one-word alias is convenient for new projects:

```bash
alias stm32init='/path/to/stm32-vscode-init/init.sh --editor zed -y'
# then, in any freshly generated CubeMX project:
cd ~/STM32Projects/my-new-project && stm32init .
```

Generated files are machine-specific (they contain absolute toolchain paths),
so the script adds them to `<project>/.gitignore` rather than expecting them to
be committed - re-run the script after cloning on another machine instead.

## What it generates

### Zed (`--editor zed`)

- **`CMakeUserPresets.json`** - `clt-Debug` / `clt-Release` presets that put
  the STM32CubeCLT `bin` directories on `PATH` for CMake only. This is what
  makes every other file work without touching your shell environment, and it
  also means `cmake --build --preset clt-Debug` works from a plain terminal.
  Inherits CubeMX's own `Debug`/`Release` presets when they exist, and defines
  the generator/toolchain file itself when they don't.
- **`.zed/tasks.json`** (`task: spawn` in the command palette) - `STM32:`
  prefixed so they're one fuzzy search away:
  - `Configure (Debug)` / `(Release)`
  - `Build (Debug)` / `(Release)`, `Rebuild`, `Clean`
  - `Flash (Debug)` / `(Release)` - flashes the existing ELF via
    `STM32_Programmer_CLI`
  - `Build + Flash (Debug)` / `(Release)`
  - `Erase chip`, `Size report (Debug)`
  - `GDB server (port 61234)` - ST-LINK GDB server in its own terminal tab
  - `GDB attach (Debug)` - `arm-none-eabi-gdb` connected to that server
- **`.zed/settings.json`** - clangd started with `--query-driver` pointed at
  the ARM GCC, so cross-compiler built-in includes and target resolve
  correctly. Merged into your existing settings, not overwritten.
- **`.clangd`** - points clangd's compilation database at
  `build/clt-Debug/compile_commands.json`.
- **`.zed/attach.gdb`** - the GDB script the attach task runs (connect, load,
  reset, break at `main`). Edit it freely.
- **`.zed/debug.json`** - only when a GDB usable by Zed's debug adapter is
  found; see *Debugging in Zed* below.
- **`.zed/env.sh`** - `source` it to get the toolchain on `PATH` in one shell,
  if you want to run the tools by bare name yourself.

### VS Code (`--editor vscode`)

- **`.vscode/tasks.json`** - build tasks, each with the correct STM32CubeCLT
  `bin` directories injected into `PATH` for that task only:
  - `Build (Debug)` - default build task (Ctrl+Shift+B / Cmd+Shift+B)
  - `Build (Release)`
  - `Flash (Debug)` / `Flash (Release)`
  - `Build + Flash (Debug)` / `Build + Flash (Release)`
  - `Clean (Debug)` / `Clean (Release)`
- **`.vscode/launch.json`** - Cortex-Debug configurations (F5):
  - `Debug (Build + Flash)` - rebuilds, flashes, then starts debugging
  - `Debug (No Rebuild)` - flashes and debugs whatever is already built
- **`.vscode/settings.json`** - `terminal.integrated.env.<os>` PATH, plus
  CMake Tools kit/configure-arg settings.
- **`.vscode/cmake-kits.json`** - a CMake Tools kit pointing at the
  STM32CubeCLT ARM GCC compilers.
- **`.vscode/c_cpp_properties.json`** - IntelliSense, pointed at
  `build/Debug/compile_commands.json`.
- **`.vscode/extensions.json`** - recommends Cortex-Debug, C/C++, CMake Tools.

Requested workflow -> generated entry:

| Workflow | VS Code | Zed |
|---|---|---|
| build | Task: `Build (Debug)` (default) | Task: `STM32: Build (Debug)` |
| build release | Task: `Build (Release)` | Task: `STM32: Build (Release)` |
| build+flash | Task: `Build + Flash (Debug)` | Task: `STM32: Build + Flash (Debug)` |
| build+flash+debug | Launch: `Debug (Build + Flash)` (F5) | Tasks: `GDB server`, then `GDB attach` |
| debug | Launch: `Debug (No Rebuild)` | as above |

## How it works

Two pluggable axes, so neither half has to know about the other:

- `profiles/` - **which toolchain** (currently STM32CubeCLT): where the
  compiler, CMake, flasher and GDB server live, how to flash, how to erase.
- `editors/` - **which editor** (VS Code, Zed): which files to write and in
  what format.

Notes that explain most of the generated content:

- **Toolchain paths**: the script looks for STM32CubeCLT in its default
  install locations (`C:\ST\STM32CubeCLT_*` on Windows,
  `/opt/st/stm32cubeclt_*` or `/opt/ST/STM32CubeCLT_*` on Linux/macOS), or
  wherever you point it with `--clt-path`.
- **Why PATH keeps coming up**: STM32CubeMX's generated
  `cmake/gcc-arm-none-eabi.cmake` looks up the compiler by bare name
  (`arm-none-eabi-gcc`), so it must be resolvable on `PATH` at configure time.
  The VS Code backend injects `PATH` per task via `options.env`; the Zed
  backend puts it in `CMakeUserPresets.json` instead, because Zed does not
  expand `${env:PATH}` inside a task's `env` values - and CMake expands
  `$penv{PATH}` itself on every platform.
- **Project name / MCU device**: parsed from `CMakeLists.txt`
  (`CMAKE_PROJECT_NAME`) and from the compile definitions in
  `cmake/stm32cubemx/CMakeLists.txt` (e.g. `STM32H7A3xxQ` -> device
  `STM32H7A3xx`). If the device can't be parsed, pass `--device` yourself.
- **Debugging in VS Code**: Cortex-Debug's `servertype: "stlink"`, pointed at
  STM32CubeCLT's official `ST-LINK_gdbserver` and `STM32CubeProgrammer` - not
  the third-party `st-util`. A matching `.svd` from STM32CubeCLT is wired in
  automatically for the peripheral register view.
- **Debugging in Zed**: Zed's GDB debug adapter drives `gdb -i dap`, and that
  interpreter only exists in a GDB built with Python. ST ships
  `arm-none-eabi-gdb` built `--without-python`, so the script probes for a GDB
  that has *both* DAP and ARM support (typically `gdb-multiarch`) and writes
  `.zed/debug.json` only if it finds one. Otherwise the `GDB server` + `GDB
  attach` tasks give you a console GDB against the same ST-LINK server. To get
  the graphical debugger on Debian/Ubuntu: `sudo apt install gdb-multiarch`,
  then re-run this script.

## Troubleshooting

- **"compiler not found" during configure**: double-check `--clt-path`, or
  that your STM32CubeCLT install has a `GNU-tools-for-STM32/bin` folder.
- **Flash fails to connect**: check the board is plugged in and `port=SWD`
  matches your debug probe; run the exact command from the generated task in a
  terminal to see the full `STM32_Programmer_CLI` output.
- **`ST-LINK error (DEV_CONNECT_ERR)`**: something else already owns the
  probe - almost always a `GDB server` task still running in another terminal
  tab. Only one process can hold the ST-LINK at a time; stop the server first.
- **GDB server says `No device found on target`**: it is trying JTAG. The
  generated task passes `-d` (SWD) for exactly this reason; if you edited the
  task, put it back.
- **clangd shows red squiggles on HAL headers**: run `STM32: Configure
  (Debug)` once - clangd needs `build/clt-Debug/compile_commands.json` to
  exist - then `editor: restart language server`.
- **Debug session won't start**: run the GDB server task alone first and check
  that it prints a listening port; only then attach.
- Re-run the script any time paths look stale (new CLT version, project moved,
  cloned to a new machine) - it's idempotent and backs up any file it
  overwrites as `<file>.bak`.
