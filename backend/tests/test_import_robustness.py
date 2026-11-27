import unittest

from app.adapters.parsers.csv_parser import CsvFileParser, _sniff_delimiter
from app.adapters.parsers.inference import DECIMAL_SCALE_NOTE, infer_column
from app.application.services.charts import build_answer_charts
from app.domain.limits import DEFAULT_DATA_LIMITS


class TestCsvDelimiter(unittest.TestCase):
    def test_a_guess_that_splits_rows_unevenly_is_not_used(self) -> None:
        text = "name;note\n" + "\n".join(f"item {i};a, b, c" for i in range(20))
        self.assertEqual(_sniff_delimiter(text), ";")

    def test_wide_rows_are_sniffed_from_whole_lines(self) -> None:
        header = "\t".join(f"col{i}" for i in range(400))
        row = "\t".join("1,5" for _ in range(400))
        self.assertEqual(_sniff_delimiter(f"{header}\n{row}\n{row}\n"), "\t")

    def test_single_column_files_still_parse(self) -> None:
        parsed = CsvFileParser().parse(b"amount\n10\n20\n", "t", DEFAULT_DATA_LIMITS)[0]
        self.assertEqual([c.name for c in parsed.columns], ["amount"])


class TestDecimalScale(unittest.TestCase):
    def test_values_beyond_decimal_precision_stay_text(self) -> None:
        values = ["1." + "1" * 20, "2.5"]
        array, schema = infer_column("ratio", values)
        self.assertEqual(schema.arrow_type, "string")
        self.assertEqual(schema.note, DECIMAL_SCALE_NOTE)
        self.assertEqual(array.to_pylist(), values)

    def test_ordinary_decimals_are_unchanged(self) -> None:
        _, schema = infer_column("amount", ["10.25", "3.5"])
        self.assertEqual(schema.arrow_type, "decimal128(38,2)")


class TestChartLabels(unittest.TestCase):
    def test_long_categories_keep_distinct_labels(self) -> None:
        prefix = "Region " + "x" * 90
        evidence = [{
            "evidence_id": "ev_0123456789ab", "operation": "aggregate", "columns": ["region", "total"],
            "rows": [[prefix + " north", 10], [prefix + " south", 20]], "row_count": 2,
        }]
        charts = build_answer_charts(evidence, {"ev_0123456789ab"})
        labels = [row[0] for row in charts[0].rows]
        self.assertEqual(len(set(labels)), 2)


if __name__ == "__main__":
    unittest.main()
