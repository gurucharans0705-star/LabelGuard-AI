from labelguard.samples.synthetic_labels import (
    APPROVED,
    REVISED,
    build_udi_di,
    gtin14_check_digit,
)


def test_check_digit_matches_known_gs1_example():
    # UPC-A 036000291452 -> body 03600029145, check digit 2 (padded to 13 digits)
    assert gtin14_check_digit("0003600029145") == 2


def test_udi_di_has_expected_shape():
    udi = build_udi_di("0890123456789")
    assert udi.startswith("(01)")
    assert len(udi) == 4 + 14


def test_approved_and_revised_use_different_udi():
    assert APPROVED.udi_di != REVISED.udi_di


def test_sample_pdfs_are_created(approved_pdf, revised_pdf):
    assert approved_pdf.exists() and approved_pdf.stat().st_size > 0
    assert revised_pdf.exists() and revised_pdf.stat().st_size > 0
