import unittest
from datetime import date

from app.adapters.parsers.csv_parser import CsvFileParser
from app.adapters.parsers.inference import AMBIGUOUS_DATE_NOTE, infer_column
from app.domain.limits import DEFAULT_DATA_LIMITS


class TestDateOrder(unittest.TestCase):
    def test_all_ambiguous_dates_stay_text_with_a_note(self) -> None:
        array, schema = infer_column("sold_on", ["03/04/2026", "05/06/2026", None])
        self.assertEqual(schema.arrow_type, "string")
        self.assertEqual(schema.note, AMBIGUOUS_DATE_NOTE)
        self.assertEqual(array.to_pylist(), ["03/04/2026", "05/06/2026", None])

    def test_an_explicit_order_is_used(self) -> None:
        array, schema = infer_column("sold_on", ["03/04/2026", "05/06/2026"], date_order="month_first")
        self.assertTrue(schema.arrow_type.startswith("date32"))
        self.assertEqual(array.to_pylist(), [date(2026, 3, 4), date(2026, 5, 6)])
        array, _ = infer_column("sold_on", ["03/04/2026"], date_order="day_first")
        self.assertEqual(array.to_pylist(), [date(2026, 4, 3)])

    def test_orders_proven_by_the_data_still_parse(self) -> None:
        array, schema = infer_column("d", ["03/04/2026", "25/12/2026"])
        self.assertEqual(array.to_pylist(), [date(2026, 4, 3), date(2026, 12, 25)])
        self.assertIsNone(schema.note)
        array, _ = infer_column("d", ["03/04/2026", "12/25/2026"])
        self.assertEqual(array.to_pylist(), [date(2026, 3, 4), date(2026, 12, 25)])
        array, _ = infer_column("d", ["2026-03-04"])
        self.assertEqual(array.to_pylist(), [date(2026, 3, 4)])

    def test_an_explicit_order_that_the_data_contradicts_is_not_applied(self) -> None:
        _, schema = infer_column("d", ["03/04/2026", "25/12/2026"], date_order="month_first")
        self.assertEqual(schema.arrow_type, "string")

    def test_csv_upload_options_carry_the_order(self) -> None:
        raw = b"sold_on,amount\n03/04/2026,10\n05/06/2026,20\n"
        parsed = CsvFileParser().parse(raw, "sales", DEFAULT_DATA_LIMITS, {"date_order": "month_first"})[0]
        column = next(c for c in parsed.columns if c.name == "sold_on")
        self.assertTrue(column.arrow_type.startswith("date32"))


if __name__ == "__main__":
    unittest.main()
