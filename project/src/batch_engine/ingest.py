"""Streaming ingestion: read a JSON array one row at a time, validate, and group into chunks."""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any, AsyncIterator, BinaryIO

import ijson

from .models import FAILED, Chunk, PromptItem, ResultRecord

READ_SIZE = 64 * 1024


class InputFormatError(Exception):
    """The input file as a whole cannot be parsed (not a JSON array, or malformed JSON)."""


class _ThreadedReader:
    """Async file adapter so ijson's async API never blocks the event loop on disk reads."""

    def __init__(self, f: BinaryIO) -> None:
        self._f = f

    async def read(self, n: int) -> bytes:
        return await asyncio.to_thread(self._f.read, n)


def validate_row(row_index: int, row: Any) -> PromptItem | ResultRecord:
    """Return a PromptItem for a valid row, or a failed ResultRecord explaining why it is invalid."""
    default_id = f"row-{row_index}"
    if not isinstance(row, dict):
        return _invalid(row_index, None, f"row must be an object, got {type(row).__name__}")
    raw_id = row.get("id")
    item_id = str(raw_id) if raw_id is not None else default_id
    if "prompt" not in row:
        return _invalid(row_index, item_id, "missing 'prompt'")
    prompt = row["prompt"]
    if not isinstance(prompt, str):
        return _invalid(row_index, item_id, f"'prompt' must be a string, got {type(prompt).__name__}")
    if not prompt.strip():
        return _invalid(row_index, item_id, "'prompt' is empty")
    return PromptItem(row_index=row_index, item_id=item_id, prompt=prompt)


def _invalid(row_index: int, item_id: str | None, message: str) -> ResultRecord:
    return ResultRecord(
        row_index=row_index,
        item_id=item_id,
        status=FAILED,
        error_type="invalid_input",
        error_message=message,
    )


async def read_chunks(path: Path, chunk_size: int) -> AsyncIterator[Chunk]:
    """Yield chunks of up to `chunk_size` rows. Memory use is O(chunk_size), not O(file)."""
    if chunk_size < 1:
        raise ValueError("chunk_size must be >= 1")
    with open(path, "rb") as f:
        await _expect_array(f)
        chunk = Chunk(index=0, items=[])
        row_index = 0
        try:
            async for row in ijson.items(_ThreadedReader(f), "item", use_float=True):
                parsed = validate_row(row_index, row)
                (chunk.items if isinstance(parsed, PromptItem) else chunk.invalid).append(parsed)
                row_index += 1
                if chunk.rows >= chunk_size:
                    yield chunk
                    chunk = Chunk(index=chunk.index + 1, items=[])
        except ijson.JSONError as exc:
            raise InputFormatError(f"malformed JSON after row {row_index}: {exc}") from exc
        if chunk.rows:
            yield chunk


async def _expect_array(f: BinaryIO) -> None:
    head = await asyncio.to_thread(f.read, 1024)
    stripped = head.lstrip()
    if stripped.startswith(b"\xef\xbb\xbf"):
        stripped = stripped[3:].lstrip()
    if not stripped.startswith(b"["):
        raise InputFormatError("input must be a JSON array of prompt objects")
    f.seek(0)
