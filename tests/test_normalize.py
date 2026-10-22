from labelguard.extraction.normalize import normalize_line, normalize_text


def test_removes_noise_but_keeps_line_structure():
    assert normalize_text("  LOT:  A24-0917 \r\n\r\n\r\n\r\nEXP:   2029-06-30\t") == "LOT: A24-0917\n\nEXP: 2029-06-30"


def test_ligatures_and_invisible_characters_are_fixed():
    # "ﬁ" and "ﬂ" are single ligature characters; the zero-width space and soft hyphen are invisible.
    assert normalize_text("steriliﬁed​ pro­duct ﬂow") == "sterilified product flow"


def test_meaningful_symbols_are_preserved():
    text = "Store at 2°C – 8°C ± 1 µg/mL ® ™ ²"
    assert normalize_text(text) == "Store at 2°C – 8°C ± 1 µg/mL ® ™ ²"


def test_non_latin_text_is_preserved():
    assert normalize_text("Nur zum einmaligen Gebrauch – 一次性使用") == (
        "Nur zum einmaligen Gebrauch – 一次性使用"
    )


def test_empty_input():
    assert normalize_text("") == ""
    assert normalize_text("   \n \n ") == ""


def test_normalize_line_joins_lines():
    assert normalize_line("do not\nreuse") == "do not reuse"
