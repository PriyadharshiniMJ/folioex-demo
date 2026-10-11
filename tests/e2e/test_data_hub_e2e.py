"""Data Hub in a real browser (criteria I10, I11, U2, E2, E4)."""

from pathlib import Path

import pytest
from playwright.sync_api import Page

from tests.builders import buy, deposit, dividend, make_csv, net_total, sell
from tests.e2e.conftest import header_alignments, skip_quiz
from tests.helpers import assert_no_technical_text

pytestmark = [pytest.mark.e2e, pytest.mark.enable_socket]

Y2024 = [deposit("02/01/2024", 2000), buy("03/01/2024", "AAA", 10, 100), dividend("15/03/2024", "AAA", 5)]
Y2025 = [deposit("02/01/2025", 1000), sell("10/02/2025", "AAA", 4, 120), buy("10/02/2025", "BBB", 3, 50)]


def write_files(tmp_path: Path) -> list[Path]:
    end_2024 = net_total(Y2024)
    files = {
        "2024.csv": make_csv(
            Y2024, start_cash=0, end_cash=end_2024, period="January 1, 2024 - December 31, 2024"
        ),
        "2025.csv": make_csv(
            Y2025,
            start_cash=end_2024,
            end_cash=end_2024 + net_total(Y2025),
            period="January 1, 2025 - December 31, 2025",
        ),
    }
    paths = []
    for name, raw in files.items():
        path = tmp_path / name
        path.write_bytes(raw)
        paths.append(path)
    return paths


def upload(page: Page, paths: list[Path]) -> None:
    page.set_input_files("#upload input[type=file]", [str(p) for p in paths])


def test_template_download_is_placeholders_only(page: Page, tmp_path: Path) -> None:
    with page.expect_download() as info:
        page.locator("#template-download-btn").click()
    download = info.value
    assert download.suggested_filename == "FolioEx_template.csv"
    target = tmp_path / "t.csv"
    download.save_as(target)
    text = target.read_text(encoding="utf-8")
    assert "<TICKER SYMBOL>" in text and "<DATE DD/MM/YYYY>" in text


def test_column_guide_has_centered_headers(page: Page) -> None:
    page.locator("#column-guide-btn").click()
    page.locator("#column-guide-modal").wait_for(state="visible")
    page.wait_for_selector("#column-guide-grid .ag-row")
    aligns = header_alignments(page, "#column-guide-modal")
    assert aligns and set(aligns) == {"center"}
    assert "<TICKER SYMBOL>" in page.locator("#column-guide-modal").inner_text()


def test_upload_shows_report_with_centered_headers(page: Page, tmp_path: Path) -> None:
    upload(page, write_files(tmp_path))
    page.wait_for_selector("text=We loaded 2 files")
    skip_quiz(page)  # the return-guess pop-up follows every upload
    report = page.locator("#datahub-report")
    report.wait_for(state="visible")
    page.wait_for_selector("#files-grid .ag-row")
    page.wait_for_selector("#tx-grid .ag-row")
    assert "2 files loaded" in page.locator("#files-chip").inner_text()
    assert "Everything checks out" in page.locator("#issues").inner_text()
    assert "Full year" in page.locator("#coverage").inner_text()
    assert set(header_alignments(page, "#datahub-report")) == {"center"}
    assert_no_technical_text(page.locator("#panel-datahub").inner_text())
    # Explain now describes the loaded data
    page.locator("#panel-datahub .fx-explain-btn").click()
    panel = page.locator("#explain-panel")
    panel.wait_for(state="visible")
    assert "You've loaded 2 files" in panel.inner_text()


def test_filter_transactions_by_type(page: Page, tmp_path: Path) -> None:
    upload(page, write_files(tmp_path))
    page.wait_for_selector("#tx-grid .ag-row")
    page.select_option("#tx-type", "Buy")
    page.wait_for_function(
        "[...document.querySelectorAll('#tx-grid .ag-row [col-id=type]')].every(c => c.innerText === 'Buy')"
    )
    assert page.locator("#tx-grid .ag-center-cols-container .ag-row").count() == 2


def test_wrong_file_type_gets_plain_english(page: Page, tmp_path: Path) -> None:
    pdf = tmp_path / "report.pdf"
    pdf.write_bytes(b"%PDF-1.4")
    upload(page, [pdf])
    page.wait_for_selector("text=isn't a CSV file")
    assert_no_technical_text(page.locator("#upload-msg").inner_text())
    assert page.locator("#datahub-report").is_hidden()


def test_clear_data_returns_to_empty(page: Page, tmp_path: Path) -> None:
    upload(page, write_files(tmp_path))
    page.wait_for_selector("#files-grid .ag-row")
    skip_quiz(page)
    page.locator("#clear-data").click()
    page.locator("#datahub-empty").wait_for(state="visible")
    assert "No files loaded" in page.locator("#files-chip").inner_text()
    assert "Session data cleared" in page.locator("#upload-msg").inner_text()


def test_progress_bar_runs_25_candles_to_completion(page: Page, tmp_path: Path) -> None:
    upload(page, write_files(tmp_path))
    card = page.locator("#progress-card")
    card.wait_for(state="visible")
    page.wait_for_function(
        "(() => { const g = document.querySelector('#progress-candles .js-plotly-plot');"
        " return g && g.data && g.data.reduce((n, t) => n + t.x.length, 0) === 25; })()"
    )
    page.wait_for_function(
        "document.querySelector('#progress-caption').innerText.includes('All 25 stages done')"
    )
    label = page.get_attribute("#progress-figure-wrap", "aria-label")
    assert label == "Processing 100% complete"
    page.wait_for_selector("text=We loaded 2 files")


def test_load_sample_data_from_the_data_hub(page: Page) -> None:
    page.locator("#load-sample").click()
    page.locator("#progress-card").wait_for(state="visible")
    page.wait_for_selector("text=We loaded 6 files", timeout=60000)
    assert "6 files loaded" in page.locator("#files-chip").inner_text()
    assert "Everything checks out" in page.locator("#issues").inner_text()
    coverage = page.locator("#coverage").inner_text()
    assert all(str(year) in coverage for year in range(2021, 2027))


def test_load_sample_data_from_an_empty_tab(page: Page) -> None:
    page.get_by_role("tab", name="Holdings").click()
    page.locator("#panel-holdings").get_by_role("button", name="Load sample data").click()
    page.wait_for_function("document.title === 'Data Hub · FolioEx'")
    page.wait_for_selector("text=We loaded 6 files", timeout=60000)
