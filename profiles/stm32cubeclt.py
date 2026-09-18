"""STM32CubeCLT profile: build/flash/debug an STM32CubeMX CMake project using
ST's official command-line tools (arm-none-eabi-gcc, STM32_Programmer_CLI,
ST-LINK_gdbserver) - no toolchain download, no extra VS Code extensions
beyond Cortex-Debug/C++/CMake Tools.
"""
import glob
import re
import shutil
import sys
from dataclasses import dataclass
from pathlib import Path

from osutil import IS_WINDOWS, IS_MAC, exe
from profiles.base import ToolchainProfile, ResolvedToolchain, DebugConfig
from profiles.serasidis_hid import configure_serasidis_f103


@dataclass
class STM32CubeCLTToolchain(ResolvedToolchain):
    programmer_cli: Path = None
    programmer_bin_dir: Path = None
    gdbserver: Path = None
    svd_dir: Path = None
    gcc_bin_dir: Path = None
    objcopy: Path = None
    flash_method: str = "stlink"
    hid_flash: Path = None
    hid_port: str = None
    hid_delay: int = None


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


def _bundled_hid_flash() -> Path:
    platform_dir = "windows" if IS_WINDOWS else ("macos" if IS_MAC else "linux")
    filename = "hid-flash.exe" if IS_WINDOWS else "hid-flash"
    return Path(__file__).resolve().parent.parent / "tools" / "hid-flash" / "bin" / platform_dir / filename


class STM32CubeCLTProfile(ToolchainProfile):
    name = "stm32cubeclt"
    preset_prefix = "clt"
    task_prefix = "STM32"

    def detect(self, project_dir: Path) -> float:
        if list(project_dir.glob("*.ioc")):
            return 1.0
        if (project_dir / "cmake" / "stm32cubemx" / "CMakeLists.txt").is_file():
            return 0.9
        return 0.0

    def add_cli_arguments(self, parser):
        parser.add_argument("--clt-path", help="Path to your STM32CubeCLT_x.y.z install (auto-detected if omitted)")
        parser.add_argument(
            "--flash-method", choices=("stlink", "hid"), default="stlink",
            help="Firmware uploader used by generated tasks (default: stlink)",
        )
        parser.add_argument(
            "--hid-flash",
            help="Path to hid-flash executable (default: auto-detect tools/hid-flash[.exe] or PATH)",
        )
        parser.add_argument(
            "--hid-port",
            help="Serial/CDC port for automatic bootloader entry (default: a non-existent dummy port for manual HID mode)",
        )
        parser.add_argument(
            "--hid-delay", type=int,
            help="Optional hid-flash delay after toggling the serial port, in microseconds",
        )
        parser.add_argument(
            "--hid-app-setup", choices=("auto", "on", "off"), default="auto",
            help="Prepare an STM32F103 CubeMX CDC application for the Serasidis bootloader "
                 "(default: auto when --flash-method hid)",
        )
        parser.add_argument(
            "--hid-usb-delay-ms", type=int, default=1000,
            help="D+ disconnect/re-enumeration delay added by HID app setup (default: 1000 ms)",
        )

    def resolve_toolchain(self, project_dir: Path, args) -> STM32CubeCLTToolchain:
        clt_root = _pick_clt_root(args)
        print(f"CubeCLT root: {clt_root}")
        gcc_bin_dir = clt_root / "GNU-tools-for-STM32" / "bin"
        cmake_bin_dir = clt_root / "CMake" / "bin"
        ninja_bin_dir = clt_root / "Ninja" / "bin"
        programmer_bin_dir = clt_root / "STM32CubeProgrammer" / "bin"
        gdbserver_bin_dir = clt_root / "STLink-gdb-server" / "bin"
        hid_flash = None
        if args.hid_flash:
            candidate = Path(args.hid_flash).expanduser()
            if not candidate.is_absolute():
                candidate = project_dir / candidate
            hid_flash = candidate.resolve()
        else:
            names = ["hid-flash.exe", "hid-flash"] if IS_WINDOWS else ["hid-flash", "hid-flash.exe"]
            candidates = [project_dir / "tools" / name for name in names]
            candidates.append(_bundled_hid_flash())
            candidates += [Path(found) for name in names if (found := shutil.which(name))]
            hid_flash = next((path.resolve() for path in candidates if path.is_file()), None)

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
            objcopy=gcc_bin_dir / exe("arm-none-eabi-objcopy"),
            flash_method=args.flash_method,
            hid_flash=hid_flash,
            hid_port=args.hid_port or ("COM256" if IS_WINDOWS else "stm32-hid-manual"),
            hid_delay=args.hid_delay,
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
            ("arm-none-eabi-objcopy", tc.objcopy),
        ]:
            if not path.is_file():
                missing.append(f"  - {label}: expected at {path}")
        if tc.flash_method == "hid" and (tc.hid_flash is None or not tc.hid_flash.is_file()):
            missing.append(
                "  - hid-flash: not found; put it in <project>/tools or pass --hid-flash <path>"
            )
        return missing

    def configure_project(self, project_dir: Path, args, device, dry_run: bool):
        enabled = args.hid_app_setup == "on" or (
            args.hid_app_setup == "auto" and args.flash_method == "hid"
        )
        if enabled:
            configure_serasidis_f103(
                project_dir, device, args.hid_usb_delay_ms, dry_run,
                required=args.hid_app_setup == "on",
            )

    def detect_device(self, project_dir: Path):
        # The .ioc carries the concrete ordering code (for example
        # STM32F103C8T6),
        # while generated CMake often only contains a broad family symbol
        # such as STM32F1xx. Prefer the concrete value and remove CubeMX's
        # two-character package suffix (Tx, Ux, ...).
        for ioc in project_dir.glob("*.ioc"):
            text = _read_text(ioc)
            m = re.search(r"(?m)^Mcu\.CPN=(STM32[A-Z0-9]+)$", text)
            if m:
                return m.group(1)
            m = re.search(r"(?m)^Mcu\.Name=(STM32[A-Z0-9]+)$", text)
            if m:
                return re.sub(r"[A-Z]x$", "", m.group(1))

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
        prefixes = [base]
        family = re.match(r"(STM32[A-Z][A-Z0-9]{3})", base)
        if family and family.group(1) not in prefixes:
            prefixes.append(family.group(1))
        for prefix in prefixes:
            matches = list(tc.svd_dir.glob(f"{prefix}*.svd"))
            if not matches:
                matches = list(tc.svd_dir.glob(f"{prefix}*.[sS][vV][dD]"))
            if matches:
                return matches[0]
        return None

    def flash_task(self, tc: STM32CubeCLTToolchain, elf_path: str) -> dict:
        if tc.flash_method == "hid":
            bin_path = re.sub(r"\.elf$", ".bin", elf_path, flags=re.IGNORECASE)
            args = [bin_path, tc.hid_port]
            if tc.hid_delay is not None:
                args.append(str(tc.hid_delay))
            return {
                "label": "Flash via HID",
                "command": str(tc.hid_flash) if tc.hid_flash else "hid-flash",
                "args": args,
                "pre_commands": [{
                    "command": str(tc.objcopy),
                    "args": ["-O", "binary", elf_path, bin_path],
                }],
            }
        return {
            "label": "Flash via ST-Link",
            "command": str(tc.programmer_cli),
            "args": [
                "--connect", "port=SWD", "mode=NORMAL", "reset=HWrst",
                "--download", elf_path,
                "--start",
            ],
        }

    def erase_task(self, tc: STM32CubeCLTToolchain) -> dict:
        return {
            "command": str(tc.programmer_cli),
            "args": ["--connect", "port=SWD", "mode=UR", "--erase", "all"],
        }

    def gdb_server_task(self, tc: STM32CubeCLTToolchain, port: int) -> dict:
        # -d selects SWD and is *not* optional: without it the server tries
        # JTAG and dies with "No device found on target" on SWD-only probes
        # such as the on-board ST-LINK of a NUCLEO board - even though
        # STM32_Programmer_CLI connects to the same board just fine.
        # -e keeps the server alive across GDB sessions, -s verifies the
        # download, -k connects under reset, -cp lets it find the flash
        # loaders that ship with STM32CubeProgrammer.
        return {
            "command": str(tc.gdbserver),
            "args": [
                "-p", str(port), "-l", "1", "-e", "-s", "-k", "-d", "-m", "0",
                "-cp", str(tc.programmer_bin_dir),
            ],
        }

    def clangd_query_driver(self, tc: STM32CubeCLTToolchain) -> str:
        return str(tc.gcc_bin_dir / "arm-none-eabi-*")

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
