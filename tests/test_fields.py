"""Field extraction: values, evidence, missing fields and 'never invent a value'."""
import pytest

from labelguard.fields.language import detect_languages
from labelguard.fields.models import FIELD_LABELS, NOT_DETECTED
from labelguard.samples.synthetic_labels import APPROVED
from tests.helpers import fields_from_lines


# --- Complete label ---------------------------------------------------------------------
def test_all_fields_are_found_on_the_approved_sample(approved_fields):
    assert approved_fields.detected_count == len(FIELD_LABELS)
    assert approved_fields.get("product_name").value == APPROVED.product_name
    assert approved_fields.get("manufacturer").value.startswith("Northwind Medical Devices Ltd.")
    assert approved_fields.get("product_identifier").value == "NW-IV200-20"
    assert approved_fields.get("udi").value == APPROVED.udi_di
    assert approved_fields.get("lot_number").value == "A24-0917"
    assert approved_fields.get("expiration_date").value == "2029-06-30"
    assert approved_fields.get("manufacturing_date").value == "2024-07-01"
    assert approved_fields.get("sterilization").value == "STERILE EO"
    assert "15 and 30 degrees C" in approved_fields.get("storage_instructions").value


def test_all_four_warnings_are_found_and_the_wrapped_one_is_joined(approved_fields):
    warnings = approved_fields.get("safety_warnings").values
    assert warnings == [
        "Single use only. Do not reuse.",
        "Do not use if package is damaged.",
        "Contains no natural rubber latex.",
        "Caution: Rx only. Federal law restricts this device to sale by or on the order of a physician.",
    ]


def test_every_detected_value_keeps_its_evidence_and_location(approved_fields, revised_fields):
    for fields in (approved_fields, revised_fields):
        for key in FIELD_LABELS:
            for hit in fields.get(key).hits:
                compact_evidence = " ".join(hit.evidence.split())
                assert " ".join(hit.value.split()) in compact_evidence, (key, hit)
                assert hit.page == 1
                assert hit.method in {"labelled", "pattern", "heuristic", "warning section"}


def test_product_name_is_marked_as_an_inference(approved_fields):
    # No "Product:" keyword on the label, so the name comes from the largest line. That must be visible.
    assert approved_fields.get("product_name").hits[0].method == "heuristic"


# --- Missing fields -----------------------------------------------------------------------
def test_missing_fields_are_reported_as_not_detected_never_invented(incomplete_fields):
    for key in ("lot_number", "manufacturing_date", "expiration_date", "manufacturer"):
        field = incomplete_fields.get(key)
        assert not field.detected
        assert field.value is None
        assert field.values == []
    assert incomplete_fields.get("product_identifier").detected  # the rest is still read
    assert NOT_DETECTED == "Not detected"


def test_text_with_no_label_structure_yields_nothing():
    fields = fields_from_lines(["This paragraph mentions nothing that looks like a label.", "It is just prose."])
    assert fields.detected_count == 0
    assert all(fields.get(key).value is None for key in FIELD_LABELS)


def test_empty_document_yields_nothing():
    assert fields_from_lines([]).detected_count == 0


def test_lot_keyword_without_a_number_is_not_taken_as_a_lot_number():
    fields = fields_from_lines(["Lot of items shipped together", "Batch details follow"])
    assert not fields.get("lot_number").detected


# --- Formats ------------------------------------------------------------------------------
@pytest.mark.parametrize(
    "line, expected",
    [
        ("EXP: 30 JUN 2029", "30 JUN 2029"),
        ("Use by 2029-06", "2029-06"),
        ("EXP 06/2029", "06/2029"),
        ("Expiration date: June 30, 2029", "June 30, 2029"),
    ],
)
def test_expiry_date_formats(line, expected):
    assert fields_from_lines([line]).get("expiration_date").value == expected


def test_manufacturing_date_is_not_confused_with_expiry():
    fields = fields_from_lines(["MFG: 2024/07/01", "EXP: 2029/06/30"])
    assert fields.get("manufacturing_date").value == "2024/07/01"
    assert fields.get("expiration_date").value == "2029/06/30"


def test_gs1_udi_string_with_application_identifiers_is_captured_whole():
    fields = fields_from_lines(["(01)08901234567890(17)290630(10)A24-0917"])
    assert fields.get("udi").value == "(01)08901234567890(17)290630(10)A24-0917"


def test_udi_label_and_gtin_are_recognised():
    assert fields_from_lines(["UDI-DI: 08901234567890"]).get("udi").value == "08901234567890"
    assert fields_from_lines(["GTIN: 08901234567890"]).get("udi").value == "08901234567890"


def test_batch_number_wording_and_catalog_number():
    fields = fields_from_lines(["Batch No. B-2291", "Cat. No: CX-4410"])
    assert fields.get("lot_number").value == "B-2291"
    assert fields.get("product_identifier").value == "CX-4410"


def test_labelled_product_and_manufacturer():
    fields = fields_from_lines(["Product: Sample Syringe 5 mL", "Manufacturer: Acme Devices GmbH"])
    assert fields.get("product_name").value == "Sample Syringe 5 mL"
    assert fields.get("product_name").hits[0].method == "labelled"
    assert fields.get("manufacturer").value == "Acme Devices GmbH"


def test_no_product_name_is_invented_when_no_line_stands_out():
    fields = fields_from_lines(["Some text", "More text of the same size", "Even more text"])
    assert not fields.get("product_name").detected


def test_sterilization_wording():
    assert fields_from_lines(["NON-STERILE"]).get("sterilization").value == "NON-STERILE"
    assert fields_from_lines(["Sterilized using ethylene oxide."]).get("sterilization").detected


def test_wrapped_warning_lines_are_joined_but_separate_bullets_stay_separate():
    fields = fields_from_lines([
        "WARNINGS",
        "- Do not use if the sterile barrier is",
        "  damaged or opened.",
        "- Single use only.",
    ])
    assert fields.get("safety_warnings").values == [
        "Do not use if the sterile barrier is damaged or opened.",
        "Single use only.",
    ]


# --- Language -----------------------------------------------------------------------------------
def test_english_is_detected_on_the_sample(approved_fields):
    assert approved_fields.language_summary == "English"


def test_german_text_is_detected():
    text = "Nur für den einmaligen Gebrauch. Nicht verwenden, wenn die Verpackung beschädigt ist und das Produkt nicht steril ist."
    assert [g.name for g in detect_languages(text)][0] == "German"


def test_too_little_text_gives_no_language_instead_of_a_guess():
    assert detect_languages("REF 123") == ()
    assert fields_from_lines(["REF: AB-123"]).language_summary == NOT_DETECTED
