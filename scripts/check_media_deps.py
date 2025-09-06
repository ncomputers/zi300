#!/usr/bin/env python3
"""Check presence of media processing dependencies."""
import sys
from shutil import which

import logging_config  # noqa: F401


def main() -> int:
    ffmpeg = which("ffmpeg") is not None
    if ffmpeg:
        print("ffmpeg available")
        return 0
    print("Error: ffmpeg not found", file=sys.stderr)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
