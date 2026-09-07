from extract import REFLOWED, UNVERIFIED, VERBATIM, claim_key, ground_facts, verify_quote

PAGE = (
    "India's largest integrated logistics platform\n"
    "FY24 revenue from services was ₹8,142 Cr, a growth of 12.7% year on year.\n"
    "EBITDA increased by Rs. 578 Cr to Rs. 127 Cr from Rs. (452 Cr) in FY23.\n"
    "The Board appointed Ms. A. Sharma as an independent director on 12 May 2023."
)

# A multi-column infographic page: pdfplumber interleaves the columns, so the
# labels and their figures never appear together in reading order.
JUMBLED = (
    "a year of impactful growth significant developments 740 Mn 1,429 K tonnes "
    "₹81,415 Mn express parcels shipped ptl freight delivered revenue from services"
)


def test_exact_quote_is_verbatim():
    status, score = verify_quote("FY24 revenue from services was ₹8,142 Cr", PAGE)
    assert status == VERBATIM and score == 1.0


def test_quote_survives_pdf_whitespace_and_unicode_noise():
    assert verify_quote("FY24   revenue from services  was  ₹8,142 Cr", PAGE)[0] == VERBATIM


def test_smart_quotes_normalise():
    page = "The Company’s revenue rose sharply during the year under review."
    assert verify_quote("The Company's revenue rose sharply", page)[0] == VERBATIM


def test_fabricated_quote_is_rejected():
    """The failure mode this whole check exists for."""
    status, _ = verify_quote(
        "FY24 revenue from services was ₹9,500 Cr, a growth of 21% year on year.", PAGE
    )
    assert status == UNVERIFIED


def test_fabricated_figure_is_rejected_even_when_the_wording_is_right():
    """The reflowed path must not become a loophole for invented numbers."""
    status, _ = verify_quote("₹99,999 Mn revenue from services", JUMBLED)
    assert status == UNVERIFIED


def test_plausible_paraphrase_is_rejected():
    status, _ = verify_quote(
        "The company reported revenue of over eight thousand crore in fiscal 2024.", PAGE
    )
    assert status == UNVERIFIED


def test_column_jumbled_page_grounds_as_reflowed():
    """Real annual-report layout: the evidence is present, only its order is not."""
    status, _ = verify_quote("₹81,415 Mn Revenue from services", JUMBLED)
    assert status == REFLOWED


def test_reflowed_requires_every_number_to_be_present():
    status, _ = verify_quote("740 Mn express parcels shipped", JUMBLED)
    assert status == REFLOWED
    assert verify_quote("999 Mn express parcels shipped", JUMBLED)[0] == UNVERIFIED


def test_quote_from_a_different_page_is_rejected():
    other = "Global trade dynamics shifted through the year as tariffs were revised."
    assert verify_quote("FY24 revenue from services was ₹8,142 Cr", other)[0] == UNVERIFIED


def test_trivially_short_quote_is_not_accepted_as_evidence():
    assert verify_quote("FY24", PAGE)[0] == UNVERIFIED


def test_wrong_page_citation_is_corrected_not_discarded():
    """Page attribution is what the model gets wrong most; fix it, don't drop the fact."""
    facts = [{"page": 9, "quote": "FY24 revenue from services was ₹8,142 Cr"}]
    out = ground_facts(facts, {7: JUMBLED, 8: PAGE, 9: "unrelated filler text here"},
                       valid_pages=range(7, 10))
    assert out[0]["quote_verified"] is True
    assert out[0]["page"] == 8
    assert out[0]["grounding"] == VERBATIM


def test_ungroundable_fact_is_kept_and_flagged_not_dropped():
    facts = [{"page": 8, "quote": "Revenue was ₹9,500 Cr in FY24 per the chairman."}]
    out = ground_facts(facts, {8: PAGE}, valid_pages=range(8, 9))
    assert len(out) == 1
    assert out[0]["quote_verified"] is False
    assert out[0]["grounding"] == UNVERIFIED


def test_claim_key_excludes_the_value():
    fact = {
        "subject": "Delhivery Limited",
        "predicate": "revenue from services",
        "qualifiers": [{"key": "basis", "value": "consolidated"}],
        "value_raw": "₹8,142 Cr",
    }
    key = claim_key(fact)
    assert "8,142" not in key
    assert "revenue from services" in key and "consolidated" in key


def test_same_claim_different_values_share_a_key():
    a = {"subject": "India", "predicate": "real GDP growth", "qualifiers": [], "value_raw": "6.5%"}
    b = {"subject": "India", "predicate": "real GDP growth", "qualifiers": [], "value_raw": "7.2%"}
    assert claim_key(a) == claim_key(b)
