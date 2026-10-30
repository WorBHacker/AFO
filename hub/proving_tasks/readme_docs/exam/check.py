"""A hidden check of «README по коду»: README.md names every option, the defaults (10 and 3), both formats and the
JSON default, has an example run of tool.py with --input; tool.py is as given."""

import re
import sys
from pathlib import Path

GIVEN = Path(__file__).resolve().parent.parent / "files"


def main() -> int:
    tool = Path("tool.py")
    if not tool.is_file() or tool.read_bytes().replace(b"\r\n", b"\n") != (GIVEN / "tool.py").read_bytes().replace(
        b"\r\n", b"\n"
    ):
        print("tool.py was changed or removed")
        return 1
    try:
        text = Path("README.md").read_text(encoding="utf-8-sig")
    except OSError:
        print("there is no README.md")
        return 1
    missing = [flag for flag in ("--input", "--limit", "--min-length", "--format", "--verbose") if flag not in text]
    if missing:
        print(f"README.md does not name {', '.join(missing)}")
        return 1
    for need, why in ((r"\b10\b", "the default of --limit (10)"), (r"\b3\b", "the default of --min-length (3)"),
                      (r"\bjson\b", "the json format"), (r"\bcsv\b", "the csv format")):  # fmt: skip
        if not re.search(need, text, re.IGNORECASE):
            print(f"README.md does not say {why}")
            return 1
    if not any("tool.py" in line and "--input" in line for line in text.splitlines()):
        print("README.md has no example run of tool.py with --input")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
