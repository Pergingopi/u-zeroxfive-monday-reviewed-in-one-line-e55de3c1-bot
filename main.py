#!/usr/bin/env python3
"""Write the Monday one-line review entry to $MAKEFUN_OUTPUT_DIR/entry.txt.

Deterministic, stdlib-only. The entry is validated against the bounty gate
(UTF-8 text, 1-200 characters, single line, no leading/trailing whitespace)
and then written atomically: temp file in the same directory, fsync,
read-back verification, os.replace, and a final directory fsync so the
rename is durable. Output permissions are 0644.

Exit status is 0 on success; any validation or I/O failure raises and the
process exits non-zero with the temp file removed.
"""

import os
import sys
import tempfile
import time

ENTRY = (
    "My Monday review: the 9 a.m. standup lasted 47 minutes, solved nothing, "
    "and ended with a new meeting to discuss the meeting."
)

# Bounty gate: /output/entry.txt must be UTF-8 text of 1 to 200 characters.
GATE_MIN_CHARS = 1
GATE_MAX_CHARS = 200

DEFAULT_OUTPUT_DIR = "/output"
OUTPUT_FILENAME = "entry.txt"
TMP_PREFIX = ".entry-"
TMP_SUFFIX = ".tmp"
TMP_CLEANUP_ATTEMPTS = 3
TMP_CLEANUP_RETRY_DELAY = 0.05


def resolve_output_dir():
    """Return the output directory, honouring $MAKEFUN_OUTPUT_DIR.

    An unset variable, an empty value, or a value that is only whitespace
    all fall back to the default, so a stray empty export can never make
    the program write to the current directory by accident.
    """
    raw = os.environ.get("MAKEFUN_OUTPUT_DIR")
    if raw is None:
        return DEFAULT_OUTPUT_DIR
    cleaned = raw.strip()
    if not cleaned:
        return DEFAULT_OUTPUT_DIR
    return cleaned


def validate_entry(entry):
    """Check one entry against the bounty gate, raising ValueError on failure.

    Split out from main() so the gate logic can be read, tested and changed
    in one place. Every failure is an explicit raise with a message naming
    the violated rule; nothing here relies on assert, so the checks survive
    `python -O`.
    """
    if not isinstance(entry, str):
        raise ValueError("entry must be a str, got %s" % type(entry).__name__)
    if not entry:
        raise ValueError("entry is empty")
    if entry != entry.strip():
        raise ValueError("entry has leading/trailing whitespace")
    if not entry.strip():
        raise ValueError("entry is only whitespace")
    if "\n" in entry or "\r" in entry:
        raise ValueError("entry must be a single line")
    for ch in entry:
        code = ord(ch)
        if code < 0x20 or code == 0x7F:
            raise ValueError("entry contains a control character U+%04X" % code)
    length = len(entry)
    if length < GATE_MIN_CHARS or length > GATE_MAX_CHARS:
        raise ValueError(
            "entry length %d outside gate range %d-%d"
            % (length, GATE_MIN_CHARS, GATE_MAX_CHARS)
        )
    # Gate counts characters, but the file must also decode as UTF-8.
    try:
        entry.encode("utf-8")
    except UnicodeEncodeError as exc:
        raise ValueError("entry is not encodable as UTF-8: %s" % exc)


def clean_stale_tmp(out_dir, attempts=TMP_CLEANUP_ATTEMPTS):
    """Best-effort removal of stale temp files left by our own earlier runs.

    Only files carrying our own prefix/suffix are touched, and the output
    directory itself is never removed. A file that is briefly locked by a
    concurrent run is retried a few times with a short delay before giving
    up silently; cleanup must never fail the entry write.
    """
    try:
        names = os.listdir(out_dir)
    except OSError:
        return
    stale = [
        name
        for name in names
        if name.startswith(TMP_PREFIX) and name.endswith(TMP_SUFFIX)
    ]
    for name in stale:
        path = os.path.join(out_dir, name)
        for attempt in range(attempts):
            try:
                os.unlink(path)
                break
            except FileNotFoundError:
                break
            except OSError:
                if attempt == attempts - 1:
                    break
                time.sleep(TMP_CLEANUP_RETRY_DELAY)


def fsync_dir(out_dir):
    """Flush the directory itself so the rename below is durable on disk."""
    dir_fd = os.open(out_dir, os.O_RDONLY)
    try:
        os.fsync(dir_fd)
    finally:
        os.close(dir_fd)


def write_atomic(out_dir, data):
    """Write data to entry.txt atomically with read-back verification.

    Returns the path of the written file. On any failure the temp file is
    removed and the exception propagates; a half-written entry.txt is
    never left behind.
    """
    fd, tmp_path = tempfile.mkstemp(
        prefix=TMP_PREFIX, suffix=TMP_SUFFIX, dir=out_dir
    )
    try:
        os.chmod(tmp_path, 0o644)
        with os.fdopen(fd, "wb") as f:
            f.write(data)
            f.flush()
            os.fsync(f.fileno())
        with open(tmp_path, "rb") as f:
            if f.read() != data:
                raise IOError("read-back verification failed")
        target = os.path.join(out_dir, OUTPUT_FILENAME)
        os.replace(tmp_path, target)
        os.chmod(target, 0o644)
        fsync_dir(out_dir)
        return target
    except BaseException:
        try:
            os.unlink(tmp_path)
        except OSError:
            pass
        raise


def main():
    validate_entry(ENTRY)
    data = ENTRY.encode("utf-8")

    out_dir = resolve_output_dir()
    os.makedirs(out_dir, exist_ok=True)
    clean_stale_tmp(out_dir)

    target = write_atomic(out_dir, data)
    print("wrote %s (%d chars)" % (target, len(ENTRY)))
    return 0


if __name__ == "__main__":
    sys.exit(main())
