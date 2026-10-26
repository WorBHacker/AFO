"""App entry point: `python -m app`. Stage 0 checks the wiring; the window arrives in stage 1."""

from PySide6 import __version__ as pyside_version

from common import __version__
from common.logging_setup import setup_logging


def main() -> int:
    log = setup_logging("app")
    log.info("LocalChat app %s, stage 0 (PySide6 %s)", __version__, pyside_version)
    log.info("the chat window arrives in stage 1")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
