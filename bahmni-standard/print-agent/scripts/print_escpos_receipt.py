#!/usr/bin/env python3
from __future__ import annotations

import argparse
import subprocess
import sys
import unicodedata
from pathlib import Path


MAX_COLUMNS = 42


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--queue", required=True)
    parser.add_argument("pdf_path")
    args = parser.parse_args()

    text = extract_text(Path(args.pdf_path))
    payload = build_escpos_payload(text)
    completed = subprocess.run(
        ["lp", "-d", args.queue, "-o", "raw"],
        input=payload,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )
    sys.stdout.buffer.write(completed.stdout)
    sys.stderr.buffer.write(completed.stderr)
    return completed.returncode


def extract_text(pdf_path: Path) -> str:
    completed = subprocess.run(
        ["pdftotext", "-layout", "-enc", "UTF-8", pdf_path.as_posix(), "-"],
        capture_output=True,
        text=True,
        check=True,
    )
    return completed.stdout.replace("\f", "\n")


def build_escpos_payload(text: str) -> bytes:
    lines = []
    for raw_line in text.splitlines():
        line = " ".join(raw_line.rstrip().split()) if len(raw_line) > MAX_COLUMNS else raw_line.rstrip()
        if len(line) <= MAX_COLUMNS:
            lines.append(line)
            continue
        while line:
            lines.append(line[:MAX_COLUMNS].rstrip())
            line = line[MAX_COLUMNS:].lstrip()

    body = "\n".join(lines).strip() + "\n\n\n"
    body = ascii_safe(body)
    return b"\x1b@" + b"\x1ba\x00" + body.encode("ascii", "replace") + b"\x1dV\x42\x00"


def ascii_safe(value: str) -> str:
    normalized = unicodedata.normalize("NFKD", value)
    return normalized.encode("ascii", "ignore").decode("ascii")


if __name__ == "__main__":
    raise SystemExit(main())
