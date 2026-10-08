import re
import unicodedata
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from job_search.domain.enums import VacancySource

_TRACKING_PARAMETERS = {
    "fbclid",
    "gclid",
    "gh_src",
    "pagenum",
    "position",
    "refid",
    "trackingid",
}

_ATS_PATTERNS: tuple[tuple[VacancySource, re.Pattern[str]], ...] = (
    (
        VacancySource.GREENHOUSE,
        re.compile(
            r"/(?:[^/]+/)?jobs/(\d+)(?:/|$)",
            re.IGNORECASE,
        ),
    ),
    (
        VacancySource.LEVER,
        re.compile(r"/[^/]+/([0-9a-f-]{20,})(?:/|$)", re.IGNORECASE),
    ),
    (
        VacancySource.ASHBY,
        re.compile(r"/[^/]+/([0-9a-f-]{20,})(?:/|$)", re.IGNORECASE),
    ),
    (
        VacancySource.RECRUITEE,
        re.compile(r"/o/([^/]+)(?:/|$)", re.IGNORECASE),
    ),
    (VacancySource.HH, re.compile(r"/vacancy/(\d+)(?:/|$)", re.IGNORECASE)),
    (
        VacancySource.LINKEDIN,
        re.compile(r"/jobs/view/(?:.*-)?(\d{6,})(?:/|$)", re.IGNORECASE),
    ),
)


def normalize_comparison_text(value: str | None) -> str | None:
    if value is None:
        return None
    normalized = unicodedata.normalize("NFKC", value)
    collapsed = " ".join(normalized.split()).casefold()
    return collapsed or None


def normalize_canonical_url(value: str) -> str:
    parts = urlsplit(value.strip())
    scheme = parts.scheme.casefold()
    hostname = (parts.hostname or "").casefold()
    port = parts.port
    if port is not None and not (
        (scheme == "http" and port == 80) or (scheme == "https" and port == 443)
    ):
        hostname = f"{hostname}:{port}"
    path = re.sub(r"/{2,}", "/", parts.path).rstrip("/") or "/"
    query = urlencode(
        sorted(
            (key, value)
            for key, value in parse_qsl(parts.query, keep_blank_values=True)
            if not key.casefold().startswith("utm_")
            and key.casefold() not in _TRACKING_PARAMETERS
        ),
        doseq=True,
    )
    return urlunsplit((scheme, hostname, path, query, ""))


def extract_original_reference(value: str) -> str | None:
    parts = urlsplit(value)
    host = (parts.hostname or "").casefold()
    query = dict(parse_qsl(parts.query, keep_blank_values=True))
    greenhouse_id = query.get("gh_jid")
    if greenhouse_id and greenhouse_id.isdigit():
        return f"{VacancySource.GREENHOUSE.value}:{greenhouse_id}"
    eligible: set[VacancySource] = set()
    if "greenhouse.io" in host:
        eligible.add(VacancySource.GREENHOUSE)
    if host == "jobs.lever.co":
        eligible.add(VacancySource.LEVER)
    if host == "jobs.ashbyhq.com":
        eligible.add(VacancySource.ASHBY)
    if host.endswith(".recruitee.com"):
        eligible.add(VacancySource.RECRUITEE)
    if host.endswith("hh.ru") or host.endswith("headhunter.ru"):
        eligible.add(VacancySource.HH)
    if host.endswith("linkedin.com"):
        eligible.add(VacancySource.LINKEDIN)
    for source, pattern in _ATS_PATTERNS:
        if source not in eligible:
            continue
        match = pattern.search(parts.path)
        if match:
            return f"{source.value}:{match.group(1).casefold()}"
    return None


def source_trust_rank(source: VacancySource) -> int:
    if source in {
        VacancySource.GREENHOUSE,
        VacancySource.LEVER,
        VacancySource.ASHBY,
        VacancySource.RECRUITEE,
        VacancySource.WORKABLE,
        VacancySource.TEAMTAILOR,
        VacancySource.SMARTRECRUITERS,
    }:
        return 400
    if source is VacancySource.HH:
        return 300
    if source is VacancySource.AGILEFLUENT:
        return 200
    if source is VacancySource.LINKEDIN:
        return 150
    if source is VacancySource.DREAMOFFER:
        return 100
    return 50
