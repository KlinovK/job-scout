from job_search.domain.canonicalization import (
    extract_original_reference,
    normalize_canonical_url,
    normalize_comparison_text,
)


def test_url_normalization_removes_tracking_but_keeps_identity_parameters() -> None:
    value = (
        "HTTPS://Example.COM:443/jobs/42/?job=ios&utm_source=mail&"
        "trackingId=noise#description"
    )

    assert normalize_canonical_url(value) == "https://example.com/jobs/42?job=ios"


def test_url_normalization_sorts_stable_query_parameters() -> None:
    assert normalize_canonical_url("https://example.com/job?b=2&a=1") == (
        "https://example.com/job?a=1&b=2"
    )


def test_original_ats_references_are_extracted_conservatively() -> None:
    assert extract_original_reference("https://hh.ru/vacancy/123?from=search") == (
        "hh:123"
    )
    assert (
        extract_original_reference(
            "https://www.linkedin.com/jobs/view/senior-ios-engineer-4455001122"
        )
        == "linkedin:4455001122"
    )
    assert (
        extract_original_reference(
            "https://careers.example.com/openings?gh_jid=8780012002&utm_source=feed"
        )
        == "greenhouse:8780012002"
    )
    assert extract_original_reference("https://example.com/jobs/123") is None


def test_comparison_normalization_preserves_meaningful_words() -> None:
    assert normalize_comparison_text("  Senior  iOS SDK Engineer ") == (
        "senior ios sdk engineer"
    )
