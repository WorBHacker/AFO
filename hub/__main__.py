"""Hub entry point: `python -m hub`. Stage 0 checks the wiring; the server arrives in stage 1."""

from common import __version__
from common.logging_setup import setup_logging
from common.paths import data_paths


def main() -> int:
    log = setup_logging("hub")
    paths = data_paths().ensure()
    log.info("LocalChat hub %s, stage 0", __version__)
    log.info("data folder: %s", paths.root)
    log.info("the server on 127.0.0.1:8765 (D28) arrives in stage 1")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
