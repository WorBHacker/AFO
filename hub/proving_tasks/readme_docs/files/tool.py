"""Prints the most frequent words of a text file."""

import argparse
import collections
import csv
import json
import re
import sys


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="The most frequent words of a text file.")
    parser.add_argument("--input", required=True, help="the text file to read (UTF-8)")
    parser.add_argument("--limit", type=int, default=10, help="how many words to print")
    parser.add_argument("--min-length", type=int, default=3, help="skip words shorter than this")
    parser.add_argument("--format", choices=["json", "csv"], default="json", help="how to print the result")
    parser.add_argument("--verbose", action="store_true", help="also print how many words were read")
    args = parser.parse_args(argv)
    with open(args.input, encoding="utf-8") as f:
        words = [w for w in re.findall(r"\w+", f.read().lower()) if len(w) >= args.min_length]
    top = collections.Counter(words).most_common(args.limit)
    if args.verbose:
        print(f"{len(words)} words read", file=sys.stderr)
    if args.format == "json":
        print(json.dumps(dict(top), ensure_ascii=False))
    else:
        writer = csv.writer(sys.stdout)
        writer.writerows(top)
    return 0


if __name__ == "__main__":
    sys.exit(main())
