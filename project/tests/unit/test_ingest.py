import pytest

from batch_engine.ingest import InputFormatError, read_chunks, validate_row
from batch_engine.models import FAILED, PromptItem, ResultRecord
from tests.conftest import prompts, write_input


async def collect(path, chunk_size):
    return [c async for c in read_chunks(path, chunk_size)]


class TestValidateRow:
    def test_valid_row(self):
        assert validate_row(3, {"id": "a", "prompt": "hi"}) == PromptItem(3, "a", "hi")

    def test_missing_id_gets_row_based_id(self):
        assert validate_row(7, {"prompt": "hi"}).item_id == "row-7"

    def test_numeric_id_is_stringified(self):
        assert validate_row(0, {"id": 42, "prompt": "hi"}).item_id == "42"

    def test_extra_fields_are_ignored(self):
        assert isinstance(validate_row(0, {"prompt": "hi", "meta": {"x": 1}}), PromptItem)

    @pytest.mark.parametrize(
        "row, message",
        [
            ({"id": "x"}, "missing 'prompt'"),
            ({"id": "x", "prompt": ""}, "'prompt' is empty"),
            ({"id": "x", "prompt": "   \n"}, "'prompt' is empty"),
            ({"id": "x", "prompt": 123}, "'prompt' must be a string, got int"),
            ({"id": "x", "prompt": None}, "'prompt' must be a string, got NoneType"),
            ({"id": "x", "prompt": ["a"]}, "'prompt' must be a string, got list"),
            ("just a string", "row must be an object, got str"),
            (None, "row must be an object, got NoneType"),
            (5, "row must be an object, got int"),
        ],
    )
    def test_invalid_rows_become_failed_records(self, row, message):
        result = validate_row(9, row)
        assert isinstance(result, ResultRecord)
        assert (result.status, result.error_type, result.error_message, result.row_index) == (
            FAILED, "invalid_input", message, 9
        )


class TestReadChunks:
    async def test_chunks_are_sized_and_ordered(self, tmp_path):
        path = write_input(tmp_path / "in.json", prompts(25))
        chunks = await collect(path, 10)
        assert [c.rows for c in chunks] == [10, 10, 5]
        assert [c.index for c in chunks] == [0, 1, 2]
        assert [i.row_index for c in chunks for i in c.items] == list(range(25))

    async def test_exact_multiple_has_no_empty_tail(self, tmp_path):
        path = write_input(tmp_path / "in.json", prompts(20))
        assert [c.rows for c in await collect(path, 10)] == [10, 10]

    async def test_invalid_rows_ride_along_in_their_chunk(self, tmp_path):
        rows = prompts(3) + [{"id": "bad"}] + prompts(2, start=3) + [None]
        path = write_input(tmp_path / "in.json", rows)
        chunks = await collect(path, 4)
        assert [c.rows for c in chunks] == [4, 3]
        assert [r.row_index for r in chunks[0].invalid] == [3]
        assert [r.row_index for r in chunks[1].invalid] == [6]

    async def test_empty_array_yields_nothing(self, tmp_path):
        path = write_input(tmp_path / "in.json", [])
        assert await collect(path, 10) == []

    async def test_floats_and_unicode_survive(self, tmp_path):
        path = write_input(tmp_path / "in.json", [{"id": 1.5, "prompt": "héllo ✓"}])
        [chunk] = await collect(path, 10)
        assert chunk.items[0] == PromptItem(0, "1.5", "héllo ✓")

    async def test_top_level_object_is_rejected(self, tmp_path):
        path = tmp_path / "in.json"
        path.write_text('{"prompt": "hi"}')
        with pytest.raises(InputFormatError, match="JSON array"):
            await collect(path, 10)

    async def test_truncated_file_raises_after_good_rows(self, tmp_path):
        path = tmp_path / "in.json"
        path.write_text('[{"prompt": "a"}, {"prompt": "b"}, {"prompt": "c"')
        seen = []
        with pytest.raises(InputFormatError, match="malformed JSON"):
            async for chunk in read_chunks(path, 1):
                seen.append(chunk)
        assert len(seen) == 2

    async def test_invalid_chunk_size(self, tmp_path):
        path = write_input(tmp_path / "in.json", prompts(1))
        with pytest.raises(ValueError):
            await collect(path, 0)

    async def test_large_file_streams_in_constant_chunks(self, tmp_path):
        path = write_input(tmp_path / "in.json", prompts(5000))
        sizes = [c.rows async for c in read_chunks(path, 100)]
        assert len(sizes) == 50 and max(sizes) == 100
