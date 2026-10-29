#!/usr/bin/env python3
"""Create a CondLaneNet/CurveLanes image list from JotMark lane sidecars.

The upstream CurvelanesDataset expects a text file whose each line is an image
path relative to ``data_root``.  For example, when ``data_root`` is
``/data/Curvelanes/train`` a line can be ``images/frame_001.jpg`` and its lane
sidecar must be ``images/frame_001.lines.txt``.
"""

from __future__ import annotations

import argparse
from pathlib import Path


IMAGE_SUFFIXES = {".jpg", ".jpeg", ".png", ".bmp", ".webp", ".tif", ".tiff"}


def parse_args():
    parser = argparse.ArgumentParser(
        description="Generate a CondLaneNet train.txt/valid.txt from images with .lines.txt sidecars."
    )
    parser.add_argument(
        "root",
        type=Path,
        help="Dataset split directory used as CondLaneNet data_root, e.g. /data/Curvelanes/train",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=None,
        help="Output list path (default: <root>/train.txt)",
    )
    parser.add_argument(
        "--include-empty",
        action="store_true",
        help="Include images whose .lines.txt exists but has no lane. By default they are skipped.",
    )
    return parser.parse_args()


def main():
    args = parse_args()
    root = args.root.expanduser().resolve()
    if not root.is_dir():
        raise SystemExit(f"Dataset root does not exist or is not a directory: {root}")

    output = (args.output or root / "train.txt").expanduser()
    if not output.is_absolute():
        output = (Path.cwd() / output).resolve()
    output.parent.mkdir(parents=True, exist_ok=True)

    image_paths = sorted(
        path for path in root.rglob("*")
        if path.is_file() and path.suffix.lower() in IMAGE_SUFFIXES
    )
    entries = []
    skipped_missing = 0
    skipped_empty = 0
    for image_path in image_paths:
        sidecar = image_path.with_suffix(".lines.txt")
        if not sidecar.is_file():
            skipped_missing += 1
            continue
        if not args.include_empty and not sidecar.read_text(encoding="utf-8").strip():
            skipped_empty += 1
            continue
        entries.append(image_path.relative_to(root).as_posix())

    output.write_text("\n".join(entries) + ("\n" if entries else ""), encoding="utf-8")
    print(f"Wrote {len(entries)} image paths: {output}")
    if skipped_missing:
        print(f"Skipped {skipped_missing} image(s) without a .lines.txt sidecar.")
    if skipped_empty:
        print(f"Skipped {skipped_empty} image(s) with an empty .lines.txt sidecar.")


if __name__ == "__main__":
    main()
