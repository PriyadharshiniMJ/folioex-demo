"""Data Hub view helpers and callbacks (criteria I10, I11, E2, E4, U2)."""

import base64

import dash_ag_grid as dag

from folioex.config import Settings
from folioex.errors import MessageCatalog
from folioex.ingestion.pipeline import Upload, ingest
from folioex.market_data.cache import CachedMarketData
from folioex.ui.app import create_app
from folioex.ui.tabs import data_hub as dh
from tests.builders import buy, deposit, make_csv, net_total, sell
from tests.fakes import FakeProvider
from tests.helpers import assert_no_technical_text, text_of, walk


def as_upload(raw: bytes, mime: str = "text/csv") -> str:
    return f"data:{mime};base64,{base64.b64encode(raw).decode()}"


ROWS_2025 = [deposit("02/01/2025", 1000), buy("03/01/2025", "AAA", 5, 100), sell("10/02/2025", "AAA", 2, 120)]
MARKET = CachedMarketData(FakeProvider(), None)
GOOD = make_csv(
    ROWS_2025, start_cash=0, end_cash=net_total(ROWS_2025), period="January 1, 2025 - December 31, 2025"
)


def test_decode_uploads_handles_good_and_malformed_payloads() -> None:
    uploads = dh.decode_uploads([as_upload(GOOD), "not-a-data-url"], ["a.csv", "b.csv"])
    assert uploads[0] == Upload("a.csv", GOOD)
    assert uploads[1] == Upload("b.csv", b"")


def test_process_uploads_stores_dataset_and_reports(settings: Settings, catalog: MessageCatalog) -> None:
    dataset_id, message = dh.process_uploads([as_upload(GOOD)], ["2025.csv"], settings, MARKET)
    assert dataset_id
    assert dh.REGISTRY.get(dataset_id) is not None
    assert "1 file" in text_of(message)
    assert_no_technical_text(text_of(message))


def test_process_uploads_rejects_everything_bad_in_plain_english(settings: Settings) -> None:
    dataset_id, message = dh.process_uploads(
        [as_upload(b"%PDF", "application/pdf")], ["report.pdf"], settings, MARKET
    )
    assert dataset_id is None
    text = text_of(message)
    assert "report.pdf" in text and "isn't a CSV file" in text
    assert_no_technical_text(text)


def test_report_tables(settings: Settings, catalog: MessageCatalog) -> None:
    result = ingest([Upload("2025.csv", GOOD), Upload("report.pdf", b"%PDF")], settings)
    files = dh.files_rows(result)
    assert files[0] == {
        "file": "2025.csv",
        "period": "Jan–Dec 2025",
        "rows": 3,
        "skipped": 0,
        "cash": "✓ Adds up",
        "status": "✓ Loaded",
    }
    assert files[1]["status"] == "✗ Not loaded"
    shuffled = ingest([Upload("report.pdf", b"%PDF"), Upload("2025.csv", GOOD)], settings)
    assert [r["file"] for r in dh.files_rows(shuffled)] == ["2025.csv", "report.pdf"]
    years = dh.coverage_rows(result)
    assert years == [{"year": 2025, "share": 1.0, "label": "Full year"}]
    tx = dh.transaction_rows(result, type_filter=None, search="")
    assert [r["type"] for r in tx] == ["Sell", "Buy", "Deposit"]  # newest first
    assert dh.transaction_rows(result, type_filter="Buy", search="")[0]["symbol"] == "AAA"
    assert dh.transaction_rows(result, type_filter=None, search="zzz") == []


def test_things_to_know_are_plain_english(settings: Settings, catalog: MessageCatalog) -> None:
    rows = [sell("12/01/2026", "ZZZ", 1, 1), deposit("2026-13-01", 1)]
    result = ingest([Upload("x.csv", make_csv(rows, sections=("Transaction History",)))], settings)
    items = dh.issue_items(result, catalog)
    text = text_of(items)
    assert "ZZZ" in text and "31/12/2026" in text
    assert_no_technical_text(text)
    clean = ingest([Upload("2025.csv", GOOD)], settings)
    assert "Everything checks out" in text_of(dh.issue_items(clean, catalog))


def test_template_download_and_column_guide(settings: Settings) -> None:
    sent = dh.template_download(settings)
    assert sent["filename"] == "FolioEx_template.csv"
    assert "<TICKER SYMBOL>" in sent["content"]
    guide = dh.column_guide_table(settings)
    assert isinstance(guide, dag.AgGrid)
    assert all(c["headerClass"] == "fx-center" for c in guide.columnDefs)


def test_layout_has_every_data_hub_piece(settings: Settings) -> None:
    layout = create_app(settings).layout
    ids = {str(getattr(n, "id", "")) for n in walk(layout)}
    for wanted in (
        "template-download-btn",
        "column-guide-btn",
        "upload",
        "upload-msg",
        "files-grid",
        "coverage",
        "issues",
        "tx-grid",
        "tx-type",
        "tx-search",
        "clear-data",
        "datahub-report",
        "load-sample",
    ):
        assert wanted in ids, wanted
    by_id = {str(getattr(n, "id", "")): n for n in walk(layout)}
    assert by_id["tx-search"].value == ""  # an empty search must show every row
    assert by_id["tx-type"].value == dh.ALL_TYPES
    grids = [n for n in walk(layout) if isinstance(n, dag.AgGrid)]
    assert grids
    for grid in grids:
        assert grid.defaultColDef["headerClass"] == "fx-center"


def test_issue_list_classes_never_clash_with_the_info_marker(
    settings: Settings, catalog: MessageCatalog
) -> None:
    x = make_csv([deposit("02/01/2025", 1)], sections=("Transaction History",))
    result = ingest([Upload("a.csv", x), Upload("b.csv", x)], settings)  # duplicates → an info note
    classes = [str(getattr(n, "className", "")) for n in walk(dh.issue_items(result, catalog))]
    assert not [c for c in classes if "fx-info" in c.split()], classes
