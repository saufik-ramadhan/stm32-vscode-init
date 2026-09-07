"""Editor backend contract.

core.py works out *what* the project is (name, toolchain paths, MCU device,
whether CMakePresets.json exists) and hands that to a backend, which decides
*where* and in *what format* to write it. Adding another editor means writing
one editors/<name>.py implementing EditorBackend and registering it in
editors/__init__.py; core.py and the toolchain profiles do not change.
"""
from dataclasses import dataclass
from pathlib import Path


@dataclass
class ProjectContext:
    """Everything a backend needs to know about the project being set up."""
    project_dir: Path
    project_name: str
    profile: object          # profiles.base.ToolchainProfile
    tc: object               # profiles.base.ResolvedToolchain
    device: str              # MCU device string, or None if undetected
    svd_path: Path           # CMSIS SVD file, or None
    use_presets: bool        # project has a usable CMakePresets.json
    dry_run: bool

    def elf(self, root_var: str, build_dir: str) -> str:
        """Path to the built .elf, expressed with the editor's own
        workspace-root variable (e.g. ${workspaceFolder}, $ZED_WORKTREE_ROOT)."""
        return f"{root_var}/{build_dir}/{self.project_name}.elf"


class EditorBackend:
    name = "base"

    def generate(self, ctx: ProjectContext):
        """Write this editor's config files."""
        raise NotImplementedError

    def next_steps(self, ctx: ProjectContext) -> list:
        """Lines printed after generation, telling the user what to do next."""
        return []
