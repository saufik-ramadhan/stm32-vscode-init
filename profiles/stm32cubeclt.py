"""STM32CubeCLT profile: build/flash/debug an STM32CubeMX CMake project using
ST's official command-line tools (arm-none-eabi-gcc, STM32_Programmer_CLI,
ST-LINK_gdbserver) - no toolchain download, no extra VS Code extensions
beyond Cortex-Debug/C++/CMake Tools.
"""
import glob
import re
import sys
from dataclasses import dataclass
from pathlib import Path

from osutil import IS_WINDOWS, IS_MAC, exe
from profiles.base import ToolchainProfile, ResolvedToolchain, DebugConfig


@dataclass
class STM32CubeCLTToolchain(ResolvedToolchain):
    programmer_cli: Path = None
    programmer_bin_dir: Path = None
    gdbserver: Path = None
    svd_dir: Path = None
    gcc_bin_dir: Path = None


def _candidate_clt_roots():
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
    # de-dup, keep stable order, prefer newest-looking name last -> sort by name
    uniq = sorted({p.resolve() for p in found}, key=lambda p: p.name)
    return uniq


def _looks_like_clt_root(path: Path) -> bool:
    return (path / "GNU-tools-for-STM32" / "bin").is_dir()


def _pick_clt_root(args) -> Path:
    if args.clt_path:
        p = Path(args.clt_path).expanduser().resolve()
        if not _looks_like_clt_root(p):
            print(f"WARNING: {p} does not look like an STM32CubeCLT install "
                  f"(missing GNU-tools-for-STM32/bin) - continuing anyway.")
        return p

    candidates = [p for p in _candidate_clt_roots() if _looks_like_clt_root(p)]
    if not candidates:
        print("Could not auto-detect an STM32CubeCLT installation.")
        if args.yes:
            print("ERROR: pass --clt-path <dir> when using --yes.")
            sys.exit(1)
        entered = input("Enter the full path to your STM32CubeCLT_x.y.z folder: ").strip()
        p = Path(entered).expanduser().resolve()
        if not _looks_like_clt_root(p):
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


def _read_text(path: Path) -> str:
    return path.read_text(encoding="utf-8", errors="ignore")


class STM32CubeCLTProfile(ToolchainProfile):
    name = "stm32cubeclt"

    def detect(self, project_dir: Path) -> float:
        if list(project_dir.glob("*.ioc")):
            return 1.0
        if (project_dir / "cmake" / "stm32cubemx" / "CMakeLists.txt").is_file():
            return 0.9
        return 0.0

    def add_cli_arguments(self, parser):
        parser.add_argument("--clt-path", help="Path to your STM32CubeCLT_x.y.z install (auto-detected if omitted)")

    def resolve_toolchain(self, project_dir: Path, args) -> STM32CubeCLTToolchain:
        clt_root = _pick_clt_root(args)
        print(f"CubeCLT root: {clt_root}")
        gcc_bin_dir = clt_root / "GNU-tools-for-STM32" / "bin"
        cmake_bin_dir = clt_root / "CMake" / "bin"
        ninja_bin_dir = clt_root / "Ninja" / "bin"
        programmer_bin_dir = clt_root / "STM32CubeProgrammer" / "bin"
        gdbserver_bin_dir = clt_root / "STLink-gdb-server" / "bin"
        return STM32CubeCLTToolchain(
            gcc=gcc_bin_dir / exe("arm-none-eabi-gcc"),
            gxx=gcc_bin_dir / exe("arm-none-eabi-g++"),
            gdb=gcc_bin_dir / exe("arm-none-eabi-gdb"),
            cmake=cmake_bin_dir / exe("cmake"),
            ninja=ninja_bin_dir / exe("ninja"),
            extra_path_dirs=[gcc_bin_dir, cmake_bin_dir, ninja_bin_dir, programmer_bin_dir, gdbserver_bin_dir],
            programmer_cli=programmer_bin_dir / exe("STM32_Programmer_CLI"),
            programmer_bin_dir=programmer_bin_dir,
            gdbserver=gdbserver_bin_dir / exe("ST-LINK_gdbserver"),
            svd_dir=clt_root / "STMicroelectronics_CMSIS_SVD",
            gcc_bin_dir=gcc_bin_dir,
        )

    def check(self, tc: STM32CubeCLTToolchain) -> list:
        missing = []
        for label, path in [
            ("arm-none-eabi-gcc", tc.gcc),
            ("arm-none-eabi-gdb", tc.gdb),
            ("cmake", tc.cmake),
            ("ninja", tc.ninja),
            ("STM32_Programmer_CLI", tc.programmer_cli),
            ("ST-LINK_gdbserver", tc.gdbserver),
        ]:
            if not path.is_file():
                missing.append(f"  - {label}: expected at {path}")
        return missing

    def detect_device(self, project_dir: Path):
        search_files = [
            project_dir / "cmake" / "stm32cubemx" / "CMakeLists.txt",
            project_dir / "CMakeLists.txt",
        ]
        search_files += [Path(p) for p in glob.glob(str(project_dir / "cmake" / "*.cmake"))]
        for f in search_files:
            if not f.is_file():
                continue
            text = _read_text(f)
            m = re.search(r'STM32[A-Z0-9]*?xx', text)
            if m:
                return m.group(0)
        return None

    def find_svd(self, tc: STM32CubeCLTToolchain, device):
        if not device or not tc.svd_dir.is_dir():
            return None
        base = device[:-2] if device.endswith("xx") else device
        matches = list(tc.svd_dir.glob(f"{base}*.svd"))
        if not matches:
            matches = list(tc.svd_dir.glob(f"{base}*.[sS][vV][dD]"))
        return matches[0] if matches else None

    def flash_task(self, tc: STM32CubeCLTToolchain, project_name: str, config: str) -> dict:
        return {
            "command": str(tc.programmer_cli),
            "args": [
                "--connect", "port=SWD", "mode=NORMAL", "reset=HWrst",
                "--download", f"${{workspaceFolder}}/build/{config}/{project_name}.elf",
                "--start",
            ],
        }

    def debug_config(self, tc: STM32CubeCLTToolchain, project_name: str, device, svd_path) -> DebugConfig:
        fields = {
            "servertype": "stlink",
            "interface": "swd",
            "serverpath": str(tc.gdbserver),
            "armToolchainPath": str(tc.gcc_bin_dir),
            "gdbPath": str(tc.gdb),
            "stm32cubeprogrammer": str(tc.programmer_bin_dir),
        }
        return DebugConfig(device=device or "REPLACE_WITH_YOUR_MCU_DEVICE", fields=fields, svd_file=svd_path)

    def extra_settings(self, tc: STM32CubeCLTToolchain) -> dict:
        return {"cmake.configureArgs": [f"-DCMAKE_MAKE_PROGRAM={tc.ninja}"]}
