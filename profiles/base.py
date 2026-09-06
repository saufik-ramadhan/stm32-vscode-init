"""Toolchain profile contract.

core.py generates all .vscode/*.json files but never talks to a vendor SDK,
compiler, or flash tool directly - it only calls through the interface
defined here. Adding support for a new MCU vendor means writing one
profiles/<name>.py that implements ToolchainProfile and registering it in
profiles/__init__.py; core.py does not change.
"""
from dataclasses import dataclass, field
from pathlib import Path


@dataclass
class ResolvedToolchain:
    """Fields every profile must resolve. A profile is free to return a
    dataclass subclass with extra fields of its own - core.py only reads
    these five, the profile's own methods can read the rest back off the
    same object."""
    gcc: Path
    gxx: Path
    gdb: Path
    cmake: Path
    ninja: Path
    extra_path_dirs: list = field(default_factory=list)


@dataclass
class DebugConfig:
    """Cortex-Debug launch.json fields a profile wants merged in."""
    device: str
    fields: dict
    svd_file: Path = None


class ToolchainProfile:
    """Base class - override whichever methods your vendor needs.

    CMake toolchain file used only when the project has no CMakePresets.json
    (recent CubeMX-style exports do; older/other exports may not).
    """
    name = "base"
    toolchain_cmake_file = "cmake/gcc-arm-none-eabi.cmake"

    def detect(self, project_dir: Path) -> float:
        """Return 0.0-1.0 confidence this profile applies to project_dir."""
        raise NotImplementedError

    def add_cli_arguments(self, parser):
        """Add profile-specific argparse arguments (e.g. --clt-path)."""

    def resolve_toolchain(self, project_dir: Path, args) -> ResolvedToolchain:
        raise NotImplementedError

    def check(self, tc: ResolvedToolchain) -> list:
        """Return human-readable strings for any expected tool that's missing."""
        return []

    def detect_device(self, project_dir: Path):
        """Return an MCU device string for the debugger, or None."""
        return None

    def find_svd(self, tc: ResolvedToolchain, device):
        return None

    def flash_task(self, tc: ResolvedToolchain, project_name: str, config: str) -> dict:
        """Return {"command": ..., "args": [...]} for a standalone Flash task."""
        raise NotImplementedError

    def debug_config(self, tc: ResolvedToolchain, project_name: str, device, svd_path) -> DebugConfig:
        raise NotImplementedError

    def extra_settings(self, tc: ResolvedToolchain) -> dict:
        """Extra keys merged into .vscode/settings.json."""
        return {}
