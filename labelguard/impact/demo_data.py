"""A small SYNTHETIC product catalog for demonstrating change-impact analysis.

Every product, label, market and review record here is invented for testing. Nothing is read from,
or sent to, a PLM, ERP, SAP or regulatory system.

The data is arranged so that the examples have clear related and unrelated cases:
  NW-IV200 infusion set (two configurations)       the usual product to change
  NW-KIT-A starter kit                              CONTAINS the NW-IV200-20 set (related by an explicit relationship)
  NW-SYR50 syringe, NW-CATH10 catheter kit          unrelated to the infusion set
"""
from __future__ import annotations

from labelguard.impact.models import (
    Catalog,
    LabelReviewRecord,
    LabelVersion,
    Language,
    Market,
    PackagingLevel,
    Product,
    ProductConfiguration,
    ProductRelationship,
)

MANUFACTURER = "Northwind Medical Devices Ltd."
_ALL = ("product_name", "manufacturer", "product_identifier", "udi", "lot_number", "expiration_date",
        "manufacturing_date", "safety_warnings", "storage_instructions", "sterilization")
_UNIT = _ALL
_BOX = ("product_name", "manufacturer", "product_identifier", "udi", "lot_number", "expiration_date",
        "safety_warnings", "storage_instructions", "sterilization")
_SHIPPER = ("product_name", "product_identifier", "udi", "lot_number", "storage_instructions")
_KIT = ("product_name", "manufacturer", "product_identifier", "udi", "lot_number", "expiration_date",
        "safety_warnings", "sterilization")

_WARNINGS = "Single use only. Do not reuse. | Do not use if package is damaged. | Contains no natural rubber latex."


def _label(label_id, code, version, status, product, config, market, language, level, fields) -> LabelVersion:
    return LabelVersion(label_id, code, version, status, product, config, market, language, level, tuple(fields))


def demo_catalog() -> Catalog:
    products = (
        Product("P-IV200", "NW-IV200 Gravity Infusion Set", "Infusion sets"),
        Product("P-KIT-A", "NW-KIT-A Infusion Starter Kit", "Kits"),
        Product("P-SYR50", "NW-SYR50 Luer Syringe 50 mL", "Syringes"),
        Product("P-CATH10", "NW-CATH10 Catheter Kit", "Catheters"),
    )
    configurations = (
        ProductConfiguration("C-IV200-20", "P-IV200", "20 drops/mL", {
            "product_name": "NW-IV200 Gravity Infusion Set (20 drops/mL)", "manufacturer": MANUFACTURER,
            "product_identifier": "NW-IV200-20", "udi": "(01)08901234567890", "sterilization": "STERILE EO",
            "storage_instructions": "Store between 15 and 30 degrees C. Keep dry.", "safety_warnings": _WARNINGS}),
        ProductConfiguration("C-IV200-15", "P-IV200", "15 drops/mL", {
            "product_name": "NW-IV200 Gravity Infusion Set (15 drops/mL)", "manufacturer": MANUFACTURER,
            "product_identifier": "NW-IV200-15", "udi": "(01)08901234568026", "sterilization": "STERILE EO",
            "storage_instructions": "Store between 15 and 30 degrees C. Keep dry.", "safety_warnings": _WARNINGS}),
        ProductConfiguration("C-KIT-A-STD", "P-KIT-A", "Standard kit", {
            "product_name": "NW-KIT-A Infusion Starter Kit", "manufacturer": MANUFACTURER,
            "product_identifier": "NW-KIT-A", "udi": "(01)08901234570005", "sterilization": "STERILE EO",
            "safety_warnings": "Single use only. Do not reuse."}),
        ProductConfiguration("C-SYR50-STD", "P-SYR50", "Standard", {
            "product_name": "NW-SYR50 Luer Syringe 50 mL", "manufacturer": MANUFACTURER,
            "product_identifier": "NW-SYR50", "udi": "(01)08901234580001", "sterilization": "STERILE R",
            "storage_instructions": "Store below 25 degrees C.", "safety_warnings": "Single use only."}),
        ProductConfiguration("C-CATH10-STD", "P-CATH10", "Standard", {
            "product_name": "NW-CATH10 Catheter Kit", "manufacturer": MANUFACTURER,
            "product_identifier": "NW-CATH10", "udi": "(01)08901234590008", "sterilization": "STERILE EO",
            "storage_instructions": "Store between 10 and 30 degrees C.", "safety_warnings": "Single use only."}),
    )
    markets = (Market("US", "United States"), Market("EU", "European Union"), Market("CA", "Canada"), Market("JP", "Japan"))
    languages = (Language("en", "English"), Language("de", "German"), Language("fr", "French"), Language("ja", "Japanese"))
    levels = (PackagingLevel("unit", "Unit", 1), PackagingLevel("box", "Box", 2), PackagingLevel("shipper", "Shipper", 3))
    labels = (
        # NW-IV200, 20 drops/mL
        _label("L-IV200-20-US-EN-U", "LBL-IV200-20-U", 3, "approved", "P-IV200", "C-IV200-20", "US", "en", "unit", _UNIT),
        _label("L-IV200-20-EU-DE-U", "LBL-IV200-20-U", 3, "approved", "P-IV200", "C-IV200-20", "EU", "de", "unit", _UNIT),
        _label("L-IV200-20-EU-FR-U", "LBL-IV200-20-U", 3, "in_review", "P-IV200", "C-IV200-20", "EU", "fr", "unit", _UNIT),
        _label("L-IV200-20-US-EN-B", "LBL-IV200-20-B", 2, "approved", "P-IV200", "C-IV200-20", "US", "en", "box", _BOX),
        _label("L-IV200-20-US-EN-S", "LBL-IV200-20-S", 1, "approved", "P-IV200", "C-IV200-20", "US", "en", "shipper", _SHIPPER),
        _label("L-IV200-20-JP-JA-U", "LBL-IV200-20-U", 1, "retired", "P-IV200", "C-IV200-20", "JP", "ja", "unit", _UNIT),
        # NW-IV200, 15 drops/mL
        _label("L-IV200-15-US-EN-U", "LBL-IV200-15-U", 2, "approved", "P-IV200", "C-IV200-15", "US", "en", "unit", _UNIT),
        _label("L-IV200-15-EU-DE-U", "LBL-IV200-15-U", 1, "draft", "P-IV200", "C-IV200-15", "EU", "de", "unit", _UNIT),
        # NW-KIT-A (contains the 20 drops/mL set)
        _label("L-KIT-A-US-EN-U", "LBL-KIT-A-U", 1, "approved", "P-KIT-A", "C-KIT-A-STD", "US", "en", "unit", _KIT),
        _label("L-KIT-A-EU-DE-B", "LBL-KIT-A-B", 1, "approved", "P-KIT-A", "C-KIT-A-STD", "EU", "de", "box", _KIT),
        # Unrelated products
        _label("L-SYR50-US-EN-U", "LBL-SYR50-U", 4, "approved", "P-SYR50", "C-SYR50-STD", "US", "en", "unit", _UNIT),
        _label("L-SYR50-CA-FR-U", "LBL-SYR50-U", 4, "approved", "P-SYR50", "C-SYR50-STD", "CA", "fr", "unit", _UNIT),
        _label("L-CATH10-US-EN-U", "LBL-CATH10-U", 2, "approved", "P-CATH10", "C-CATH10-STD", "US", "en", "unit", _UNIT),
        _label("L-CATH10-EU-DE-B", "LBL-CATH10-B", 2, "in_review", "P-CATH10", "C-CATH10-STD", "EU", "de", "box", _BOX),
    )
    relationships = (
        ProductRelationship("P-KIT-A", "P-IV200", "contains_component", "The starter kit includes one 20 drops/mL infusion set."),
    )
    records = (
        LabelReviewRecord("RR-001", "L-IV200-20-US-EN-U", "2026-03-04T09:12:00+00:00", "J. Rivera", "Approved",
                          "Artwork and text matched the product record (synthetic)."),
        LabelReviewRecord("RR-002", "L-IV200-20-US-EN-U", "2025-11-18T14:40:00+00:00", "J. Rivera", "Changes requested",
                          "Storage wording clarified (synthetic)."),
        LabelReviewRecord("RR-003", "L-IV200-20-EU-DE-U", "2026-03-09T10:05:00+00:00", "M. Hoffmann", "Approved",
                          "Translation checked against the English source (synthetic)."),
        LabelReviewRecord("RR-004", "L-IV200-20-EU-FR-U", "2026-09-30T16:20:00+00:00", "A. Laurent", "In progress",
                          "French translation under check (synthetic)."),
        LabelReviewRecord("RR-005", "L-KIT-A-US-EN-U", "2026-05-21T11:00:00+00:00", "J. Rivera", "Approved",
                          "Component list verified (synthetic)."),
        LabelReviewRecord("RR-006", "L-SYR50-US-EN-U", "2026-02-02T08:30:00+00:00", "K. Osei", "Approved", "Synthetic record."),
        LabelReviewRecord("RR-007", "L-CATH10-US-EN-U", "2026-04-15T13:45:00+00:00", "K. Osei", "Approved", "Synthetic record."),
    )
    return Catalog(products, configurations, labels, markets, languages, levels, relationships, records)
