"""Comparison: identical labels, modified identifiers, added/removed/modified warnings, missing fields."""
from dataclasses import replace

from labelguard.comparison.engine import compare_labels, compare_text, comparison_key, inline_diff, similarity
from labelguard.comparison.models import KIND_LABELS
from labelguard.extraction.pdf_text import extract_pdf
from labelguard.fields.extractor import extract_fields
from labelguard.samples.synthetic_labels import APPROVED
from tests.helpers import fields_from_lines, make_lines


def compare_pdfs(first: bytes, second: bytes):
    doc_a, doc_b = extract_pdf(first), extract_pdf(second)
    return compare_labels(doc_a.all_lines, extract_fields(doc_a), doc_b.all_lines, extract_fields(doc_b))


def field(result, key):
    return next(item for item in result.fields if item.key == key)


def kinds(item):
    return sorted(change.kind for change in item.changes)


# --- Identical labels -------------------------------------------------------------------------
def test_identical_labels_show_no_differences(approved_pdf):
    data = approved_pdf.read_bytes()
    result = compare_pdfs(data, data)
    assert result.text_identical
    assert result.text_changes == ()
    assert result.unchanged_lines > 0
    assert result.changed_fields == 0
    assert result.matching_fields == 10
    assert all(change.kind == "unchanged" for item in result.fields for change in item.changes)


def test_two_separately_rendered_identical_labels_match(render_spec):
    result = compare_pdfs(render_spec(APPROVED), render_spec(APPROVED))
    assert result.text_identical and result.changed_fields == 0


# --- Modified product identifiers ---------------------------------------------------------------
def test_modified_product_identifier_is_reported_as_modified(render_spec):
    revised = replace(APPROVED, ref="NW-IV200-25")
    result = compare_pdfs(render_spec(APPROVED), render_spec(revised))
    item = field(result, "product_identifier")
    assert item.status == "changed"
    (change,) = item.changes
    assert (change.kind, change.approved.value, change.revised.value) == ("modified", "NW-IV200-20", "NW-IV200-25")
    # Only that line differs; every other field still matches exactly.
    assert result.changed_fields == 1
    assert [t.kind for t in result.text_changes] == ["modified"]
    assert result.text_changes[0].approved_text == "REF: NW-IV200-20"
    assert result.text_changes[0].revised_text == "REF: NW-IV200-25"


def test_modified_udi_is_detected(render_spec):
    revised = replace(APPROVED, udi_di_body="0890123456802")
    result = compare_pdfs(render_spec(APPROVED), render_spec(revised))
    (change,) = field(result, "udi").changes
    assert change.kind == "modified"
    assert change.approved.value != change.revised.value
    assert result.changed_fields == 1


def test_changed_lot_and_expiry_are_detected(render_spec):
    revised = replace(APPROVED, lot="B25-0101", expiry="2030-01-31")
    result = compare_pdfs(render_spec(APPROVED), render_spec(revised))
    assert field(result, "lot_number").status == "changed"
    assert field(result, "expiration_date").status == "changed"
    assert field(result, "manufacturer").status == "match"


# --- Warnings added / removed / modified ---------------------------------------------------------
def test_added_warning_is_reported_as_added(render_spec):
    revised = replace(APPROVED, warnings=APPROVED.warnings + ("Keep away from heat sources.",))
    result = compare_pdfs(render_spec(APPROVED), render_spec(revised))
    item = field(result, "safety_warnings")
    assert kinds(item) == ["added", "unchanged", "unchanged", "unchanged", "unchanged"]
    added = next(c for c in item.changes if c.kind == "added")
    assert added.revised.value == "Keep away from heat sources." and added.approved is None
    assert [t.kind for t in result.text_changes] == ["added"]


def test_removed_warning_is_reported_as_missing_from_revised(render_spec):
    revised = replace(APPROVED, warnings=APPROVED.warnings[:1] + APPROVED.warnings[2:])
    result = compare_pdfs(render_spec(APPROVED), render_spec(revised))
    removed = [c for c in field(result, "safety_warnings").changes if c.kind == "removed"]
    assert [c.approved.value for c in removed] == ["Do not use if package is damaged."]
    assert [t.kind for t in result.text_changes] == ["removed"]
    assert result.text_changes[0].approved_text == "- Do not use if package is damaged."


def test_reworded_warning_is_modified_not_removed_and_added(approved_pdf, revised_pdf):
    result = compare_pdfs(approved_pdf.read_bytes(), revised_pdf.read_bytes())
    changes = {c.kind: c for c in field(result, "safety_warnings").changes if c.kind != "unchanged"}
    assert changes["modified"].approved.value == "Contains no natural rubber latex."
    assert changes["modified"].revised.value == "Contains natural rubber latex."
    assert changes["modified"].similarity >= 0.6
    assert changes["removed"].approved.value == "Do not use if package is damaged."


def test_full_sample_pair_matches_the_documented_ground_truth(approved_pdf, revised_pdf):
    result = compare_pdfs(approved_pdf.read_bytes(), revised_pdf.read_bytes())
    changed = {item.key for item in result.fields if item.status == "changed"}
    matching = {item.key for item in result.fields if item.status == "match"}
    assert changed == {"product_name", "product_identifier", "udi", "safety_warnings", "storage_instructions"}
    assert matching == {"manufacturer", "lot_number", "expiration_date", "manufacturing_date", "sterilization"}
    # Modified lines: product name, REF, UDI, latex statement, storage. Removed: the package-damage warning.
    assert result.count_text("modified") == 5 and result.count_text("removed") == 1
    assert result.count_text("added") == 0


# --- Missing / unrecognised fields ---------------------------------------------------------------
def test_field_missing_from_revised_label_is_reported_as_removed(render_spec):
    result = compare_pdfs(render_spec(APPROVED), render_spec(replace(APPROVED, lot=None)))
    (change,) = field(result, "lot_number").changes
    assert change.kind == "removed" and change.approved.value == "A24-0917" and change.revised is None


def test_field_only_in_revised_label_is_reported_as_added(render_spec):
    result = compare_pdfs(render_spec(replace(APPROVED, lot=None)), render_spec(APPROVED))
    (change,) = field(result, "lot_number").changes
    assert change.kind == "added" and change.approved is None


def test_field_missing_from_both_labels_is_not_detected_rather_than_a_match(render_spec):
    spec = replace(APPROVED, lot=None)
    result = compare_pdfs(render_spec(spec), render_spec(spec))
    item = field(result, "lot_number")
    assert item.status == "not_detected" and item.changes == ()
    assert result.undetected_fields == 1


# --- Text comparison -------------------------------------------------------------------------------
def test_compare_text_finds_added_removed_and_modified_lines():
    old = make_lines(["Same line", "Shelf life 3 years", "Remove me completely"])
    new = make_lines(["Same line", "Shelf life 5 years", "A brand new sentence here"])
    changes, unchanged = compare_text(old, new)
    assert unchanged == 1
    by_kind = {change.kind: change for change in changes}
    assert by_kind["modified"].approved_text == "Shelf life 3 years"
    assert by_kind["modified"].revised_text == "Shelf life 5 years"
    assert by_kind["removed"].approved_text == "Remove me completely"
    assert by_kind["added"].revised_text == "A brand new sentence here"


def test_formatting_only_differences_are_not_reported():
    old = make_lines(["Store at 2 – 8 °C.", "Do   not reuse."])
    new = make_lines(["store at 2 - 8 °c", "Do not reuse"])
    changes, unchanged = compare_text(old, new)
    assert changes == [] and unchanged == 2
    assert comparison_key("A  –  B.") == comparison_key("a - b")


def test_inline_diff_marks_only_the_changed_words():
    segments = inline_diff("Contains no natural rubber latex.", "Contains natural rubber latex.")
    assert segments == [("equal", "Contains"), ("removed", "no"), ("equal", "natural rubber latex.")]
    assert similarity("abc", "abc") == 1.0


def test_field_comparison_of_plain_text_fields_without_pdfs():
    result = compare_labels(
        make_lines(["REF: AB-100"]), fields_from_lines(["REF: AB-100"]),
        make_lines(["REF: AB-200"]), fields_from_lines(["REF: AB-200"]),
    )
    assert field(result, "product_identifier").changes[0].kind == "modified"


# --- Wording: differences are not violations -------------------------------------------------------
def test_comparison_wording_never_declares_a_violation():
    banned = ("violation", "non-compliant", "noncompliant", "fail", "error", "illegal", "approved by")
    for label in KIND_LABELS.values():
        assert not any(word in label.lower() for word in banned), label
