from uuid import uuid4

import pytest

from job_search.application.models import (
    CollectionCoverage,
    SourceCollectionResult,
)
from tests.job_search.factories import make_vacancy


def test_complete_full_board_result_is_reconciliation_eligible() -> None:
    vacancy = make_vacancy(uuid4())
    result = SourceCollectionResult(
        vacancies=(vacancy,),
        coverage=CollectionCoverage.FULL_BOARD,
        complete=True,
        raw_count=1,
        malformed_count=0,
        pagination_exhausted=True,
    )

    assert result.reconciliation_eligible is True


@pytest.mark.parametrize(
    ("coverage", "complete", "malformed_count", "pagination_exhausted"),
    [
        (CollectionCoverage.FILTERED_SUBSET, True, 0, True),
        (CollectionCoverage.ROLLING_WINDOW, True, 0, True),
        (CollectionCoverage.FULL_BOARD, False, 0, True),
        (CollectionCoverage.FULL_BOARD, False, 1, True),
        (CollectionCoverage.FULL_BOARD, False, 0, False),
    ],
)
def test_non_authoritative_results_are_not_reconciliation_eligible(
    coverage: CollectionCoverage,
    complete: bool,
    malformed_count: int,
    pagination_exhausted: bool,
) -> None:
    result = SourceCollectionResult(
        vacancies=(),
        coverage=coverage,
        complete=complete,
        raw_count=malformed_count,
        malformed_count=malformed_count,
        pagination_exhausted=pagination_exhausted,
    )

    assert result.reconciliation_eligible is False


@pytest.mark.parametrize(
    ("raw_count", "malformed_count", "expected"),
    [
        (-1, 0, "raw_count"),
        (0, -1, "malformed_count"),
        (1, 2, "must not exceed"),
    ],
)
def test_collection_counts_must_be_valid(
    raw_count: int,
    malformed_count: int,
    expected: str,
) -> None:
    with pytest.raises(ValueError, match=expected):
        SourceCollectionResult(
            vacancies=(),
            coverage=CollectionCoverage.FULL_BOARD,
            complete=False,
            raw_count=raw_count,
            malformed_count=malformed_count,
            pagination_exhausted=True,
        )


def test_collection_counts_include_normalized_vacancies() -> None:
    with pytest.raises(ValueError, match="must not exceed raw_count"):
        SourceCollectionResult(
            vacancies=(make_vacancy(uuid4()),),
            coverage=CollectionCoverage.FULL_BOARD,
            complete=False,
            raw_count=0,
            malformed_count=0,
            pagination_exhausted=True,
        )


@pytest.mark.parametrize(
    ("malformed_count", "pagination_exhausted", "expected"),
    [
        (1, True, "malformed"),
        (0, False, "exhaust pagination"),
    ],
)
def test_complete_result_requires_lossless_exhausted_collection(
    malformed_count: int,
    pagination_exhausted: bool,
    expected: str,
) -> None:
    with pytest.raises(ValueError, match=expected):
        SourceCollectionResult(
            vacancies=(),
            coverage=CollectionCoverage.FULL_BOARD,
            complete=True,
            raw_count=malformed_count,
            malformed_count=malformed_count,
            pagination_exhausted=pagination_exhausted,
        )
