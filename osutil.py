"""OS-detection helpers shared by core.py and every profile."""
import platform

IS_WINDOWS = platform.system() == "Windows"
IS_MAC = platform.system() == "Darwin"
IS_LINUX = platform.system() == "Linux"


def exe(name: str) -> str:
    return name + ".exe" if IS_WINDOWS else name


def os_platform_key():
    if IS_WINDOWS:
        return "windows"
    if IS_MAC:
        return "osx"
    return "linux"


def path_separator():
    return ";" if IS_WINDOWS else ":"
