import pytest

from labelguard import config
from labelguard.extraction.pdf_text import extract_pdf
from labelguard.fields.extractor import extract_fields
from labelguard.samples.synthetic_labels import (
    APPROVED_NAME,
    INCOMPLETE_NAME,
    REVISED_NAME,
    SCANNED_NAME,
    ensure_sample_files,
    render_label_pdf,
)


@pytest.fixture(autouse=True)
def isolated_database(tmp_path, monkeypatch):
    """Every test gets its own database file, so tests never touch the real run log."""
    monkeypatch.setattr(config, "DB_PATH", tmp_path / "test_labelguard.db")


@pytest.fixture(scope="session")
def sample_pdfs(tmp_path_factory):
    """The synthetic labels, generated into a temporary folder."""
    return ensure_sample_files(tmp_path_factory.mktemp("samples"))


@pytest.fixture(scope="session")
def approved_pdf(sample_pdfs):
    return sample_pdfs[APPROVED_NAME]


@pytest.fixture(scope="session")
def revised_pdf(sample_pdfs):
    return sample_pdfs[REVISED_NAME]


@pytest.fixture(scope="session")
def incomplete_pdf(sample_pdfs):
    return sample_pdfs[INCOMPLETE_NAME]


@pytest.fixture(scope="session")
def scanned_pdf(sample_pdfs):
    return sample_pdfs[SCANNED_NAME]


@pytest.fixture(scope="session")
def approved_fields(approved_pdf):
    return extract_fields(extract_pdf(approved_pdf))


@pytest.fixture(scope="session")
def revised_fields(revised_pdf):
    return extract_fields(extract_pdf(revised_pdf))


@pytest.fixture(scope="session")
def incomplete_fields(incomplete_pdf):
    return extract_fields(extract_pdf(incomplete_pdf))


@pytest.fixture
def render_spec(tmp_path):
    """Render a (modified) synthetic LabelSpec to a PDF and return its bytes."""
    counter = iter(range(1000))

    def _render(spec) -> bytes:
        return render_label_pdf(spec, tmp_path / f"label_{next(counter)}.pdf").read_bytes()

    return _render


@pytest.fixture(scope="session")
def tesseract_available():
    from labelguard.extraction.ocr import get_ocr_status

    return get_ocr_status().available


@pytest.fixture(scope="session")
def sample_verification(approved_pdf, revised_pdf):
    """A real verification of the v1 -> v2 sample pair (computed once; results are immutable)."""
    from labelguard.verification import verify_pair

    return verify_pair((approved_pdf.read_bytes(), "approved_label_v1.pdf"), (revised_pdf.read_bytes(), "revised_label_v2.pdf"))
