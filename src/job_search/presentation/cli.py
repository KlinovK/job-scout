import argparse
import asyncio
import logging
from collections.abc import Sequence
from pathlib import Path
from uuid import UUID

import httpx
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from job_search.application.services import (
    ClassifyVacanciesService,
    CollectJobsService,
    ListCandidateVacanciesService,
    SeedCompaniesService,
)
from job_search.domain.classifier import IOS_CLASSIFIER_VERSION, IOSVacancyClassifier
from job_search.domain.enums import ATSType
from job_search.domain.models import utc_now
from job_search.infrastructure.configuration import Settings
from job_search.infrastructure.persistence.sqlalchemy.database import (
    create_database_engine,
)
from job_search.infrastructure.persistence.sqlalchemy.repositories import (
    SQLAlchemyClassificationRepository,
    SQLAlchemyCompanyRepository,
    SQLAlchemyVacancyRepository,
)
from job_search.infrastructure.registry import load_company_registry
from job_search.infrastructure.seed import development_companies
from job_search.infrastructure.sources.agilefluent import AgileFluentSource
from job_search.infrastructure.sources.ashby import AshbySource
from job_search.infrastructure.sources.greenhouse import GreenhouseSource
from job_search.infrastructure.sources.hh import HHSource
from job_search.infrastructure.sources.lever import LeverSource
from job_search.infrastructure.sources.provider import ConfiguredJobSourceProvider
from job_search.infrastructure.sources.recruitee import RecruiteeSource


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Personal job-search core")
    subparsers = parser.add_subparsers(dest="command", required=True)
    subparsers.add_parser("seed", help="seed development Greenhouse companies")
    collect = subparsers.add_parser("collect", help="collect jobs from enabled sources")
    collect.add_argument(
        "--source",
        action="append",
        choices=tuple(item.value for item in ATSType),
        help="limit collection to a source type; repeat to select several",
    )
    external = subparsers.add_parser(
        "collect-external",
        help="collect supported external sources (currently hh.ru)",
    )
    external.add_argument(
        "--source",
        action="append",
        choices=(ATSType.HH.value,),
        default=None,
    )
    subparsers.add_parser("classify", help="classify stored vacancies for iOS fit")
    candidates = subparsers.add_parser(
        "candidates",
        help="show MATCH and POSSIBLE_MATCH vacancies",
    )
    candidates.add_argument(
        "--limit",
        type=int,
        default=100,
        help="maximum candidate vacancies to display (default: 100)",
    )
    companies = subparsers.add_parser("companies", help="manage Company Registry")
    company_commands = companies.add_subparsers(
        dest="companies_command",
        required=True,
    )
    import_command = company_commands.add_parser(
        "import",
        help="validate and import a JSON registry file",
    )
    import_command.add_argument("file", type=Path)
    company_commands.add_parser("list", help="list configured companies")
    company_commands.add_parser("health", help="show current source health")
    subparsers.add_parser("duplicates", help="show canonical deduplication diagnostics")
    vacancy = subparsers.add_parser("vacancy", help="inspect vacancy provenance")
    vacancy_commands = vacancy.add_subparsers(dest="vacancy_command", required=True)
    vacancy_sources = vacancy_commands.add_parser(
        "sources",
        help="show all source observations for a canonical vacancy",
    )
    vacancy_sources.add_argument("vacancy_id", type=UUID)
    return parser


def _configure_logging(level: str) -> None:
    logging.basicConfig(
        level=getattr(logging, level),
        format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
    )


async def _run(
    command: str,
    settings: Settings,
    *,
    candidate_limit: int = 100,
    companies_command: str | None = None,
    registry_path: Path | None = None,
    selected_sources: Sequence[ATSType] | None = None,
    vacancy_command: str | None = None,
    vacancy_id: UUID | None = None,
) -> int:
    engine = create_database_engine(settings.database_url)
    session_factory = async_sessionmaker(
        engine,
        class_=AsyncSession,
        expire_on_commit=False,
    )
    companies = SQLAlchemyCompanyRepository(session_factory)
    vacancies = SQLAlchemyVacancyRepository(session_factory)
    classifications = SQLAlchemyClassificationRepository(session_factory)

    try:
        if command == "seed":
            seed_service = SeedCompaniesService(companies)
            result = await seed_service.seed(development_companies(utc_now()))
            print(f"Companies created: {result.created}")
            print(f"Companies updated: {result.updated}")
            return 0

        if command == "companies":
            if companies_command == "import":
                if registry_path is None:
                    raise ValueError("registry file is required")
                imported = load_company_registry(registry_path, utc_now())
                result = await SeedCompaniesService(companies).seed(imported)
                print(f"Registry entries validated: {len(imported)}")
                print(f"Companies created: {result.created}")
                print(f"Companies updated: {result.updated}")
                return 0
            configured = await companies.list_all()
            if companies_command == "list":
                for company in configured:
                    print(
                        f"{company.name} | {company.ats_type.value} | "
                        f"{company.ats_identifier} | priority={company.priority} | "
                        f"enabled={str(company.enabled).lower()}"
                    )
                print(f"Total companies: {len(configured)}")
                return 0
            if companies_command == "health":
                for company in configured:
                    status = (
                        company.last_status.value
                        if company.last_status
                        else "never_checked"
                    )
                    job_count = (
                        company.last_job_count
                        if company.last_job_count is not None
                        else "-"
                    )
                    checked_at = (
                        company.last_checked_at.isoformat()
                        if company.last_checked_at
                        else "-"
                    )
                    print(
                        f"{company.name} | {company.ats_type.value} | "
                        f"status={status} | jobs={job_count} | "
                        f"error={company.last_error_category or '-'} | "
                        f"checked={checked_at}"
                    )
                return 0
            raise ValueError(f"Unknown companies command: {companies_command}")

        if command == "classify":
            classification_service = ClassifyVacanciesService(
                vacancies,
                classifications,
                IOSVacancyClassifier(),
            )
            classification_summary = await classification_service.classify()
            print(f"Classifier: {classification_summary.classifier_version}")
            print(f"Vacancies processed: {classification_summary.processed}")
            print(f"MATCH: {classification_summary.matches}")
            print(f"POSSIBLE_MATCH: {classification_summary.possible_matches}")
            print(f"REJECT: {classification_summary.rejected}")
            print(f"Classifications created: {classification_summary.created}")
            print(f"Classifications updated: {classification_summary.updated}")
            return 0

        if command == "duplicates":
            stats = await vacancies.deduplication_stats()
            print(f"Source observations: {stats.observations}")
            print(f"Canonical vacancies: {stats.canonical_vacancies}")
            print(
                "Cross-source duplicates collapsed: "
                f"{stats.cross_source_duplicates_collapsed}"
            )
            print(
                "Possible duplicate groups not merged: "
                f"{stats.possible_duplicate_groups}"
            )
            return 0

        if command == "vacancy":
            if vacancy_command != "sources" or vacancy_id is None:
                raise ValueError("vacancy sources requires a vacancy UUID")
            canonical = await vacancies.get(vacancy_id)
            if canonical is None:
                print(f"Vacancy not found: {vacancy_id}")
                return 1
            canonical_company = await companies.get(canonical.company_id)
            employer = canonical.employer_name or (
                canonical_company.name if canonical_company else "-"
            )
            print(f"Canonical: {employer} | {canonical.title}")
            print(f"Preferred source: {canonical.source.value}")
            for observation in await vacancies.list_observations(vacancy_id):
                original = (
                    observation.original_source.value
                    if observation.original_source
                    else "unknown"
                )
                print()
                print(f"Discovered via: {observation.discovered_via.value}")
                print(f"Source job ID: {observation.source_job_id}")
                print(f"Original source: {original}")
                print(f"URL: {observation.observation_url}")
            return 0

        if command == "candidates":
            if candidate_limit <= 0:
                raise ValueError("--limit must be greater than zero")
            candidate_service = ListCandidateVacanciesService(
                companies,
                vacancies,
                classifications,
            )
            candidates = await candidate_service.list_candidates(
                IOS_CLASSIFIER_VERSION,
                limit=candidate_limit,
            )
            if not candidates:
                print("No MATCH or POSSIBLE_MATCH vacancies found.")
                return 0
            for candidate in candidates:
                classification = candidate.classification
                signal_text = ", ".join(
                    item.value for item in classification.positive_signals
                )
                warning_text = ", ".join(item.value for item in classification.warnings)
                print(f"Decision: {classification.decision.value.upper()}")
                employer = candidate.vacancy.employer_name or candidate.company.name
                print(f"Company: {employer}")
                print(f"Title: {candidate.vacancy.title}")
                print(f"Location: {candidate.vacancy.work_location or 'Unknown'}")
                print(f"Work mode: {classification.work_mode.value}")
                print(f"Seniority: {classification.seniority.value}")
                discovered_via = ", ".join(
                    sorted(
                        {item.discovered_via.value for item in candidate.observations}
                    )
                )
                original_sources = ", ".join(
                    sorted(
                        {
                            item.original_source.value
                            for item in candidate.observations
                            if item.original_source is not None
                        }
                    )
                )
                displayed_source = discovered_via or candidate.vacancy.source.value
                print(f"Discovered via: {displayed_source}")
                print(f"Original source: {original_sources or '-'}")
                official_sources = {
                    ATSType.GREENHOUSE.value,
                    ATSType.LEVER.value,
                    ATSType.ASHBY.value,
                    ATSType.RECRUITEE.value,
                }
                has_official = any(
                    item.discovered_via.value in official_sources
                    for item in candidate.observations
                )
                print(f"Official source available: {'yes' if has_official else 'no'}")
                print(f"Positive signals: {signal_text or '-'}")
                print(f"Warnings: {warning_text or '-'}")
                print(f"URL: {candidate.vacancy.url}")
                print()
            return 0

        if command not in {"collect", "collect-external"}:
            raise ValueError(f"Unknown command: {command}")

        timeout = httpx.Timeout(
            connect=settings.http_connect_timeout,
            read=settings.http_read_timeout,
            write=settings.http_write_timeout,
            pool=settings.http_pool_timeout,
        )
        limits = httpx.Limits(
            max_connections=settings.http_max_connections,
            max_keepalive_connections=settings.http_max_keepalive_connections,
        )
        async with httpx.AsyncClient(
            timeout=timeout,
            limits=limits,
            headers={"User-Agent": settings.user_agent},
            follow_redirects=True,
        ) as http_client:
            greenhouse = GreenhouseSource(http_client)
            lever = LeverSource(http_client)
            ashby = AshbySource(http_client)
            recruitee = RecruiteeSource(http_client)
            agilefluent = AgileFluentSource(http_client)
            hh = HHSource(
                http_client,
                oauth_token=settings.hh_api_token,
                max_age_hours=settings.external_discovery_max_age_hours,
                max_pages_per_query=settings.external_discovery_max_pages,
            )
            provider = ConfiguredJobSourceProvider(
                {
                    ATSType.GREENHOUSE: greenhouse,
                    ATSType.LEVER: lever,
                    ATSType.ASHBY: ashby,
                    ATSType.RECRUITEE: recruitee,
                    ATSType.AGILEFLUENT: agilefluent,
                    ATSType.HH: hh,
                }
            )
            collection_service = CollectJobsService(
                companies,
                vacancies,
                provider,
                max_concurrency=settings.collection_concurrency,
            )
            collection_summary = await collection_service.collect(
                selected_sources,
                include_disabled=command == "collect-external",
            )

        print(f"Companies checked: {collection_summary.companies_checked}")
        for provider_summary in collection_summary.provider_summaries:
            print(f"{provider_summary.ats_type.value.title()}:")
            print(f"  checked: {provider_summary.checked}")
            print(f"  healthy: {provider_summary.healthy}")
            print(f"  empty: {provider_summary.empty}")
            print(f"  failed: {provider_summary.failed}")
            print(f"  invalid: {provider_summary.invalid_configuration}")
            print(f"  jobs fetched: {provider_summary.jobs_fetched}")
            print(f"  observations created: {provider_summary.observations_created}")
            print(
                "  cross-source duplicates collapsed: "
                f"{provider_summary.cross_source_duplicates_collapsed}"
            )
        print(f"Jobs fetched: {collection_summary.jobs_fetched}")
        print(f"New jobs: {collection_summary.new_jobs}")
        print(f"Updated jobs: {collection_summary.updated_jobs}")
        print(f"Failures: {collection_summary.failure_count}")
        for failure in collection_summary.failures:
            print(
                f"- {failure.company_name}: {failure.error_category}: {failure.message}"
            )
        return 1 if collection_summary.failure_count else 0
    finally:
        await engine.dispose()


def main(argv: Sequence[str] | None = None) -> None:
    args = _parser().parse_args(argv)
    settings = Settings()
    _configure_logging(settings.log_level)
    candidate_limit = getattr(args, "limit", 100)
    companies_command = getattr(args, "companies_command", None)
    registry_path = getattr(args, "file", None)
    source_values = getattr(args, "source", None)
    if args.command == "collect-external" and not source_values:
        source_values = [ATSType.HH.value]
    selected_sources = (
        tuple(ATSType(item) for item in source_values) if source_values else None
    )
    vacancy_command = getattr(args, "vacancy_command", None)
    vacancy_id = getattr(args, "vacancy_id", None)
    raise SystemExit(
        asyncio.run(
            _run(
                args.command,
                settings,
                candidate_limit=candidate_limit,
                companies_command=companies_command,
                registry_path=registry_path,
                selected_sources=selected_sources,
                vacancy_command=vacancy_command,
                vacancy_id=vacancy_id,
            )
        )
    )
