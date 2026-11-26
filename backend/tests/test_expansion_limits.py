import dataclasses
import io
import unittest

import pyarrow as pa
import pyarrow.ipc as pa_ipc
import pyarrow.parquet as pq

from app.adapters.parsers.arrow_parser import ArrowIpcFileParser
from app.adapters.parsers.parquet_parser import ParquetFileParser
from app.domain.errors import LimitExceededError
from app.domain.limits import DEFAULT_DATA_LIMITS

SMALL = dataclasses.replace(DEFAULT_DATA_LIMITS, max_expanded_file_bytes=100_000)
TABLE = pa.table({"x": pa.array(["a" * 20] * 20_000)})


def _parquet() -> bytes:
    buf = io.BytesIO()
    pq.write_table(TABLE, buf, compression="zstd")
    return buf.getvalue()


def _arrow_stream() -> bytes:
    sink = pa.BufferOutputStream()
    with pa_ipc.new_stream(sink, TABLE.schema, options=pa_ipc.IpcWriteOptions(compression="zstd")) as writer:
        for batch in TABLE.to_batches(max_chunksize=1000):
            writer.write_batch(batch)
    return sink.getvalue().to_pybytes()


class TestCompressedFilesCannotExpandUnbounded(unittest.TestCase):
    def test_parquet_is_rejected_from_metadata_before_reading(self) -> None:
        raw = _parquet()
        self.assertLess(len(raw), 100_000)
        with self.assertRaises(LimitExceededError):
            ParquetFileParser().parse(raw, "t", SMALL)
        self.assertEqual(ParquetFileParser().parse(raw, "t", DEFAULT_DATA_LIMITS)[0].row_count, 20_000)

    def test_arrow_stream_stops_once_past_the_limit(self) -> None:
        raw = _arrow_stream()
        self.assertLess(len(raw), 100_000)
        with self.assertRaises(LimitExceededError):
            ArrowIpcFileParser().parse(raw, "t", SMALL)
        self.assertEqual(ArrowIpcFileParser().parse(raw, "t", DEFAULT_DATA_LIMITS)[0].row_count, 20_000)


if __name__ == "__main__":
    unittest.main()
