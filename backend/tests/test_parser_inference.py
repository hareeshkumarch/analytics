import codecs
import io
import zipfile
from datetime import date, datetime
from decimal import Decimal

import openpyxl
import pyarrow as pa
import pyarrow.ipc as pa_ipc
import pytest

from app.adapters.parsers.csv_parser import CsvFileParser, TsvFileParser
from app.adapters.parsers.inference import infer_column
from app.adapters.parsers.xlsx_parser import XlsxFileParser
from app.adapters.query.duckdb_executor import DuckDbQueryExecutor
from app.adapters.query.sqlglot_compiler import SqlGlotQueryCompiler
from app.application.services.profiling import propose_metrics_from_schema
from app.domain.errors import SecurityPolicyError, ValidationError
from app.domain.limits import DEFAULT_DATA_LIMITS
from app.domain.models import TableSchema
from app.domain.query_ir import (
    AggregateFunction,
    AggregateMetricSpec,
    AggregateQueryIR,
    ComparePeriodsQueryIR,
)

CSV_TEXT = 'name,amount\nCafé – Señor,"₹1,23,456.78"\nNaïve Traders,Rs 250\n'


def _parse_csv(raw: bytes, **options: str):
    return CsvFileParser().parse(raw, "t", DEFAULT_DATA_LIMITS, options or None)[0]


@pytest.mark.parametrize(
    "raw",
    [
        CSV_TEXT.encode("utf-8"),
        codecs.BOM_UTF8 + CSV_TEXT.encode("utf-8"),
        codecs.BOM_UTF16_LE + CSV_TEXT.encode("utf-16-le"),
        codecs.BOM_UTF16_BE + CSV_TEXT.encode("utf-16-be"),
    ],
)
def test_csv_decodes_unicode_encodings_and_boms(raw: bytes) -> None:
    parsed = _parse_csv(raw)
    assert parsed.arrow_table.column_names == ["name", "amount"]
    assert parsed.arrow_table["name"].to_pylist() == ["Café – Señor", "Naïve Traders"]
    assert parsed.arrow_table["amount"].to_pylist() == [Decimal("123456.78"), Decimal("250.00")]


def test_csv_falls_back_to_cp1252_then_latin1() -> None:
    cp1252 = "vendor,city\nCafé – Señor,Pondichéry\n".encode("cp1252")
    assert _parse_csv(cp1252).arrow_table["vendor"].to_pylist() == ["Café – Señor"]
    latin1_only = b"vendor\nAc\x81me\n"
    assert _parse_csv(latin1_only).arrow_table["vendor"].to_pylist() == ["Ac\x81me"]


def test_csv_explicit_encoding_stays_strict() -> None:
    with pytest.raises(ValidationError):
        _parse_csv("v\nCafé\n".encode("cp1252"), encoding="utf-8")


@pytest.mark.parametrize(
    ("text", "expected", "currency"),
    [
        ("₹1,23,456.78", Decimal("123456.78"), "INR"),
        ("Rs. 1,234", Decimal("1234.00"), "INR"),
        ("Rs 1,234.50", Decimal("1234.50"), "INR"),
        ("INR 1,234.50", Decimal("1234.50"), "INR"),
        ("-₹500", Decimal("-500.00"), "INR"),
        ("(₹1,234.00)", Decimal("-1234.00"), "INR"),
        ("(1,234.00)", Decimal("-1234.00"), None),
        ("12,34,567.00", Decimal("1234567.00"), None),
        ("1,234,567.89", Decimal("1234567.89"), None),
    ],
)
def test_currency_and_grouped_numbers_become_decimals(text: str, expected: Decimal, currency: str | None) -> None:
    array, schema = infer_column("amount", [text, "", None])
    assert array.type == pa.decimal128(38, 2)
    assert array.to_pylist() == [expected, None, None]
    assert (schema.arrow_type, schema.currency_code, schema.decimal_scale) == ("decimal128(38,2)", currency, 2)


def test_grouped_integers_without_currency_stay_int64() -> None:
    array, schema = infer_column("qty", ["1,23,456", "12,34,567", "(5)", "1,234"])
    assert schema.arrow_type == "int64"
    assert array.to_pylist() == [123456, 1234567, -5, 1234]


@pytest.mark.parametrize(
    "values",
    [["1,2,3", "100"], ["1,23,4567"], ["12,34"], ["1,234,56"], ["(-5)"], ["-₹-5"], ["₹1,000", "n/a"]],
)
def test_malformed_numbers_keep_column_as_string(values: list[str]) -> None:
    array, schema = infer_column("amount", values)
    assert schema.arrow_type == "string"
    assert array.to_pylist() == values


def test_integers_beyond_int64_become_decimals() -> None:
    array, schema = infer_column("reference", ["9" * 25, "12"])
    assert schema.arrow_type == "decimal128(38,2)"
    assert array.to_pylist()[0] == Decimal("9" * 25)


@pytest.mark.parametrize("values", [["9" * 40], ["0." + "1" * 20]])
def test_numbers_that_do_not_fit_decimal128_stay_strings(values: list[str]) -> None:
    array, schema = infer_column("raw", values)
    assert schema.arrow_type == "string"
    assert array.to_pylist() == values


def test_leading_zero_ids_stay_strings() -> None:
    array, schema = infer_column("order_id", ["00123", "456", "1,000"])
    assert (schema.arrow_type, schema.is_leading_zero_id) == ("string", True)
    assert array.to_pylist() == ["00123", "456", "1,000"]


def test_csv_currency_metadata_and_profiling_proposal() -> None:
    parsed = _parse_csv('id,debit,qty\nA1,"₹1,000.50",3\nA2,Rs. 200,4\n'.encode("utf-8"))
    debit, qty = parsed.columns[1], parsed.columns[2]
    assert (debit.arrow_type, debit.currency_code) == ("decimal128(38,2)", "INR")
    assert (qty.arrow_type, qty.currency_code) == ("int64", None)
    schema = TableSchema("t", parsed.columns, parsed.row_count, parsed.normalized_bytes)
    debit_metric = next(m for m in propose_metrics_from_schema(schema) if m.expression == "SUM(debit)")
    assert (debit_metric.currency, debit_metric.unit) == ("INR", "INR")
    assert debit_metric.display_name == f"{schema.table_name} total debit"


def test_tsv_shares_inference() -> None:
    parsed = TsvFileParser().parse("amount\tpaid_on\n₹1,234\t31/10/2026\n".encode("utf-8"), "t", DEFAULT_DATA_LIMITS)[0]
    assert [c.arrow_type for c in parsed.columns] == ["decimal128(38,2)", "date32[day]"]


@pytest.mark.parametrize(
    ("values", "expected"),
    [
        (["2026-10-31", "2026-01-05"], [date(2026, 10, 31), date(2026, 1, 5)]),
        (["31/10/2026", "01-11-2026", "5.11.2026"], [date(2026, 10, 31), date(2026, 11, 1), date(2026, 11, 5)]),
        (["10/31/2026", "11/01/2026"], [date(2026, 10, 31), date(2026, 11, 1)]),
        (["01/02/2026", "03/04/2026"], [date(2026, 2, 1), date(2026, 4, 3)]),
        (["2026-10-31 00:00:00", "31/10/2026"], [date(2026, 10, 31), date(2026, 10, 31)]),
    ],
)
def test_dates_become_date32(values: list[str], expected: list[date]) -> None:
    array, schema = infer_column("paid_on", [*values, ""])
    assert schema.arrow_type == "date32[day]"
    assert array.to_pylist() == [*expected, None]


def test_date_times_become_timestamps() -> None:
    array, schema = infer_column("paid_at", ["2026-10-31 15:45", "2026-10-31T15:45:00", "31/10/2026 09:05:30.5"])
    assert schema.arrow_type == "timestamp[us]"
    assert array.to_pylist() == [
        datetime(2026, 10, 31, 15, 45),
        datetime(2026, 10, 31, 15, 45),
        datetime(2026, 10, 31, 9, 5, 30, 500000),
    ]


@pytest.mark.parametrize(
    "values",
    [["31/10/2026", "10/31/2026"], ["31/02/2026"], ["31/10/26"], ["31/10-2026"], ["2026-10-31T15:45:00Z"]],
)
def test_conflicting_or_unsupported_dates_stay_strings(values: list[str]) -> None:
    assert infer_column("paid_on", values)[1].arrow_type == "string"


def _workbook_bytes(extra_parts: dict[str, bytes], rows: list[list[object]]) -> bytes:
    wb = openpyxl.Workbook()
    for row in rows:
        wb.active.append(row)
    plain = io.BytesIO()
    wb.save(plain)
    out = io.BytesIO()
    with zipfile.ZipFile(io.BytesIO(plain.getvalue())) as src, zipfile.ZipFile(out, "w") as dst:
        for info in src.infolist():
            dst.writestr(info, src.read(info))
        for name, payload in extra_parts.items():
            dst.writestr(name, payload)
    return out.getvalue()


def test_xlsx_with_printer_settings_is_accepted_and_typed() -> None:
    rows = [
        ["paid_on", "paid_at", "amount", "fee", "ref"],
        [date(2026, 10, 31), datetime(2026, 10, 31, 15, 45), "₹1,23,456.78", 12.5, "00101"],
        [date(2026, 11, 1), datetime(2026, 11, 1, 9, 0), 2500, 3, "00102"],
    ]
    raw = _workbook_bytes({"xl/printerSettings/printerSettings1.bin": b"\x00\x01"}, rows)
    parsed = XlsxFileParser().parse(raw, "book", DEFAULT_DATA_LIMITS)[0]
    types = {c.name: (c.arrow_type, c.currency_code, c.is_leading_zero_id) for c in parsed.columns}
    assert types == {
        "paid_on": ("date32[day]", None, False),
        "paid_at": ("timestamp[us]", None, False),
        "amount": ("decimal128(38,2)", "INR", False),
        "fee": ("decimal128(38,2)", None, False),
        "ref": ("string", None, True),
    }
    assert parsed.arrow_table["paid_on"].to_pylist() == [date(2026, 10, 31), date(2026, 11, 1)]
    assert parsed.arrow_table["amount"].to_pylist() == [Decimal("123456.78"), Decimal("2500.00")]


@pytest.mark.parametrize(
    "part",
    [
        "xl/vbaProject.bin",
        "xl/embeddings/oleObject1.bin",
        "xl/embeddings/Microsoft_Word_Document.docx",
        "xl/activeX/activeX1.xml",
        "xl/macrosheets/sheet1.xml",
        "xl/printerSettings/../vbaProject.bin",
        "xl/customData/blob.bin",
    ],
)
def test_xlsx_rejects_macros_and_embedded_objects(part: str) -> None:
    raw = _workbook_bytes({part: b"payload"}, [["a"], [1]])
    with pytest.raises(SecurityPolicyError):
        XlsxFileParser().parse(raw, "book", DEFAULT_DATA_LIMITS)


def test_parsed_currency_and_date_columns_query_through_duckdb() -> None:
    raw = (
        'order_id,amount,order_date\n00101,"₹1,23,456.78",31/10/2026\n'
        "00102,Rs. 1000,01/11/2026\n00103,(500.00),15/09/2026\n"
    ).encode("utf-8")
    parsed = _parse_csv(raw)
    schemas = {"orders": TableSchema("orders", parsed.columns, parsed.row_count, parsed.normalized_bytes)}
    tables = {"orders": pa_ipc.open_stream(pa.BufferReader(parsed.ipc_bytes)).read_all()}
    compiler, executor = SqlGlotQueryCompiler(), DuckDbQueryExecutor()
    total = AggregateMetricSpec("total", AggregateFunction.SUM, "amount")
    aggregate = AggregateQueryIR(table_id="orders", metrics=(total,))
    result = executor.execute_tables(compiler.compile(aggregate, schemas), tables, "snap", "sem")
    assert result.rows == ((Decimal("123956.78"),),)

    compare = ComparePeriodsQueryIR(
        table_id="orders",
        date_field="order_date",
        metric=total,
        current_start="2026-10-01",
        current_end="2026-11-30",
        previous_start="2026-09-01",
        previous_end="2026-09-30",
    )
    compiled = compiler.compile(compare, schemas)
    assert "TRY_CAST" not in compiled.sql
    assert executor.execute_tables(compiled, tables, "snap", "sem").rows == (
        (Decimal("124456.78"), Decimal("-500.00")),
    )
