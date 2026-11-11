"""Non-destructive helpers for individual annotation sidecar files."""

import hashlib
import os
import shutil
import tempfile
from jotmark.data.project_config import BACKUP_ROOT


def file_fingerprint(path):
    """Return a content digest, or None when the file does not exist."""
    try:
        with open(path, "rb") as stream:
            digest = hashlib.sha256()
            for block in iter(lambda: stream.read(1024 * 1024), b""):
                digest.update(block)
            return digest.hexdigest()
    except FileNotFoundError:
        return None


def ensure_unique_image_stem(image_path, image_extensions):
    """Reject two pictures whose annotation sidecar would have the same name."""
    image_path = os.path.abspath(image_path)
    image_dir, filename = os.path.split(image_path)
    stem = os.path.normcase(os.path.splitext(filename)[0])
    if not os.path.isdir(image_dir):
        return
    with os.scandir(image_dir) as entries:
        for entry in entries:
            if (entry.is_file() and entry.name.lower().endswith(image_extensions)
                    and os.path.normcase(os.path.splitext(entry.name)[0]) == stem
                    and os.path.normcase(os.path.abspath(entry.path)) != os.path.normcase(image_path)):
                raise RuntimeError(f"图片“{filename}”与“{entry.name}”共用同名标注文件，请先重命名其中一张图片。")


def backup_previous_annotation(path, project_dir=None):
    """Atomically keep the immediately preceding version before an overwrite.

    This is a managed, single-version backup, not a complete version history.
    A backup failure propagates so the caller does not overwrite the original.
    """
    if not os.path.exists(path):
        return None
    source_dir = os.path.dirname(os.path.abspath(path))
    project_dir = os.path.abspath(project_dir or source_dir)
    directory = os.path.join(project_dir, BACKUP_ROOT, "annotation-history")
    if os.path.normcase(os.path.realpath(source_dir)) != os.path.normcase(os.path.realpath(project_dir)):
        # Distinct external output directories must never share a history slot.
        digest = hashlib.sha256(os.path.normcase(os.path.realpath(source_dir)).encode("utf-8")).hexdigest()[:16]
        directory = os.path.join(directory, "外部标注_" + digest)
    os.makedirs(directory, exist_ok=True)
    destination = os.path.join(directory, os.path.basename(path) + ".previous")
    fd, temporary = tempfile.mkstemp(prefix=".jotmark-backup-", suffix=".tmp", dir=directory)
    try:
        with os.fdopen(fd, "wb") as output, open(path, "rb") as source:
            shutil.copyfileobj(source, output, 1024 * 1024)
            output.flush()
            os.fsync(output.fileno())
        os.replace(temporary, destination)
    except Exception:
        try:
            os.unlink(temporary)
        except FileNotFoundError:
            pass
        raise
    return destination


def archive_annotation(path):
    """Move an empty/deleted sidecar into the dataset's recoverable trash."""
    if not os.path.exists(path):
        return None
    directory = os.path.join(os.path.dirname(os.path.abspath(path)), ".jotmark_trash", "annotations")
    os.makedirs(directory, exist_ok=True)
    stem, suffix = os.path.splitext(os.path.basename(path))
    fd, destination = tempfile.mkstemp(prefix=stem + "-", suffix=suffix, dir=directory)
    os.close(fd)
    try:
        os.replace(path, destination)
    except Exception:
        try:
            os.unlink(destination)
        except FileNotFoundError:
            pass
        raise
    return destination
