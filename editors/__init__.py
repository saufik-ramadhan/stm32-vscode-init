"""Editor backend registry.

To support another editor: add editors/<name>.py implementing EditorBackend
(see editors/base.py), then list it in EDITORS below. core.py never changes.
"""
from editors.vscode import VSCodeBackend
from editors.zed import ZedBackend

EDITORS = {
    "vscode": VSCodeBackend,
    "zed": ZedBackend,
}

DEFAULT_EDITOR = "vscode"
