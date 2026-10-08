import argparse
import asyncio
import json
import logging
import os
import re
import sys
import tempfile
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path

from dotenv import load_dotenv
from telethon import TelegramClient, events, utils
from telethon.errors import ChatForwardsRestrictedError, RPCError
from telethon.tl.functions.channels import JoinChannelRequest
from telethon.tl.types import Channel, Chat


load_dotenv()

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(message)s",
    stream=sys.stdout,
)

api_id_raw = os.getenv("TELEGRAM_API_ID")
api_hash = os.getenv("TELEGRAM_API_HASH")

if not api_id_raw:
    raise RuntimeError("TELEGRAM_API_ID is missing from .env")

if not api_hash:
    raise RuntimeError("TELEGRAM_API_HASH is missing from .env")

api_id = int(api_id_raw)

client = TelegramClient(
    "telegram_collector",
    api_id,
    api_hash,
)

IOS_KEYWORDS = (
    "Swift",
    "SwiftUI",
    "UIKit",
    "Xcode",
    "iOS",
    "Objective-C",
)

MOBILE_ROLE_KEYWORDS = (
    "iOS Developer",
    "iOS Engineer",
    "Swift Developer",
    "Senior iOS Engineer",
    "Senior Mobile Engineer",
    "Mobile Software Engineer",
    "Swift Engineer",
    "Product Engineer — Mobile",
    "Product Engineer - Mobile",
    "Product Engineer: Mobile",
    "Product Engineer, Mobile",
    "Mobile Product Engineer",
    "Mobile Platform Engineer",
    "FinTech Mobile Engineer",
    "Web3 Mobile Engineer",
    "Crypto Mobile Engineer",
    "AI Mobile Engineer",
    "iOS SDK Engineer",
)

PYTHON_BACKEND_ROLE_KEYWORDS = (
    "Python Developer",
    "Python Engineer",
    "Python Backend",
    "Python Backend Developer",
    "Backend Python Developer",
    "Django Developer",
    "Django Engineer",
    "FastAPI Developer",
    "FastAPI Engineer",
    "Flask Developer",
    "Flask Engineer",
)

PYTHON_ENTRY_LEVEL_ROLE_KEYWORDS = (
    "Junior Python Developer",
    "Junior Python Engineer",
    "Junior Python Backend Developer",
    "Junior Python Backend Engineer",
    "Python Junior Developer",
    "Python Junior Engineer",
    "Trainee Python Developer",
    "Trainee Python Engineer",
    "Python Developer Trainee",
    "Python Engineer Trainee",
    "Python Intern",
    "Python Internship",
    "Entry Level Python Developer",
    "Entry Level Python Engineer",
    "Junior Django Developer",
    "Junior Django Engineer",
    "Trainee Django Developer",
    "Trainee Django Engineer",
    "Junior FastAPI Developer",
    "Junior FastAPI Engineer",
    "Стажер Python",
    "Стажёр Python",
    "Джуниор Python разработчик",
    "Python разработчик стажер",
    "Python разработчик стажёр",
)

FULLSTACK_ROLE_KEYWORDS = (
    "Python Fullstack Developer",
    "Python Full Stack Developer",
    "Fullstack Developer",
    "Full-stack Developer",
    "Full Stack Developer",
    "Fullstack Engineer",
    "Full-stack Engineer",
    "Full Stack Engineer",
    "Junior Fullstack",
    "Junior Full-stack",
    "Junior Full Stack",
)

PYTHON_BACKEND_SIGNALS = (
    "Python",
    "Django",
    "FastAPI",
    "Flask",
)

BACKEND_SIGNALS = (
    "Backend",
    "Back-end",
    "Server-side",
)

FULLSTACK_SIGNALS = (
    "Fullstack",
    "Full-stack",
    "Full stack",
    "Frontend and Backend",
    "Frontend & Backend",
)

JUNIOR_SIGNALS = (
    "Junior",
    "Trainee",
    "Intern",
    "Internship",
    "Entry Level",
    "Entry-level",
    "Graduate",
    "No experience",
    "Без опыта",
    "Начинающий",
    "Начинающего",
    "Стажировка",
    "Джуниор",
    "Джуниора",
    "Стажер",
    "Стажера",
    "Стажёр",
    "Стажёра",
    "Джуніор",
    "Джуніора",
    "Стажерка",
    "Стажування",
    "Без досвіду",
    "Початківець",
)

EXPERIENCED_LEVEL_SIGNALS = (
    "Middle",
    "Mid-level",
    "Mid Level",
    "Senior",
    "Lead",
    "Principal",
    "Staff Engineer",
    "Tech Lead",
    "Team Lead",
    "Мидл",
    "Мідл",
    "Сеньор",
    "Синьор",
    "Лид",
    "Лід",
)

PROJECT_MANAGEMENT_ROLE_KEYWORDS = (
    "Project Manager",
    "Technical Project Manager",
    "IT Project Manager",
    "Digital Project Manager",
    "Delivery Manager",
    "Program Manager",
    "Project Coordinator",
    "Scrum Master",
    "Менеджер проектов",
    "Менеджер проекта",
    "Руководитель проектов",
    "Руководитель проекта",
    "Проджект-менеджер",
    "Проджект менеджер",
    "Координатор проектов",
    "Координатор проекта",
    "Менеджер проєктів",
    "Керівник проєкту",
)

PROJECT_MANAGEMENT_SIGNALS = (
    "Project Management",
    "Project Delivery",
    "Управление проектами",
    "Управление проектом",
    "Проектное управление",
    "Управління проєктами",
)

VIBE_CODING_KEYWORDS = (
    "vibe coding",
    "vibe-coding",
    "vibecoding",
    "vibe coder",
    "vibe-coder",
    "vibecoder",
    "vibe coders",
    "vibe-coders",
    "vibecoders",
    "вайб кодинг",
    "вайб-кодинг",
    "вайбкодинг",
    "вайб кодер",
    "вайб-кодер",
    "вайбкодер",
    "вайб кодера",
    "вайб-кодера",
    "вайбкодера",
    "вайб кодеры",
    "вайб-кодеры",
    "вайбкодеры",
    "AI coding",
    "AI-assisted coding",
    "AI-native developer",
    "AI-first developer",
)

AI_CODING_SIGNALS = (
    "vibe",
    "Cursor",
    "Claude Code",
    "Windsurf",
    "GitHub Copilot",
    "Lovable",
    "Bolt.new",
    "Replit Agent",
    "v0",
)

ROLE_CONTEXT_KEYWORDS = (
    "developer",
    "engineer",
    "manager",
    "coordinator",
    "разработчик",
    "разработчика",
    "инженер",
    "менеджер",
    "руководитель",
    "координатор",
    "розробник",
    "інженер",
    "менеджер",
)

VACANCY_SIGNAL_KEYWORDS = (
    "vacancy",
    "vacancies",
    "hiring",
    "we are looking for",
    "looking for a",
    "looking for an",
    "position",
    "opening",
    "job opening",
    "job opportunity",
    "apply now",
    "вакансия",
    "вакансии",
    "ищем",
    "нанимаем",
    "требуется",
    "требуются",
    "откликнуться",
    "отклик",
    "вакансія",
    "шукаємо",
    "потрібен",
    "потрібна",
)

VACANCY_DETAIL_KEYWORDS = (
    "remote",
    "hybrid",
    "on-site",
    "salary",
    "compensation",
    "responsibilities",
    "requirements",
    "full-time",
    "part-time",
    "contract",
    "удаленно",
    "удалённо",
    "гибрид",
    "зарплата",
    "оплата",
    "обязанности",
    "требования",
    "занятость",
    "локация",
    "релокация",
    "график",
    "ставка",
)

NON_VACANCY_KEYWORDS = (
    "tutorial",
    "roadmap",
    "course",
    "webinar",
    "workshop",
    "meetup",
    "conference",
    "release notes",
    "курс",
    "вебинар",
    "обучение",
    "интенсив",
    "митап",
    "конференция",
    "новости",
    "новость",
)

HARD_EXCLUSION_KEYWORDS = (
    "#резюме",
    "#resume",
    "ищу работу",
    "ищу вакансию",
    "ищу проект",
    "open to work",
    "available for work",
    "available for hire",
    "рассматриваю предложения",
    "предлагаю услуги",
    "шукаю роботу",
)


def compile_keyword_pattern(keywords: tuple[str, ...]) -> re.Pattern:
    longest_first = sorted(keywords, key=len, reverse=True)
    return re.compile(
        r"\b(?:" + "|".join(map(re.escape, longest_first)) + r")\b",
        flags=re.IGNORECASE,
    )


IOS_KEYWORD_PATTERN = compile_keyword_pattern(IOS_KEYWORDS)
MOBILE_ROLE_PATTERN = compile_keyword_pattern(MOBILE_ROLE_KEYWORDS)
PYTHON_BACKEND_ROLE_PATTERN = compile_keyword_pattern(
    PYTHON_BACKEND_ROLE_KEYWORDS
)
PYTHON_ENTRY_LEVEL_ROLE_PATTERN = compile_keyword_pattern(
    PYTHON_ENTRY_LEVEL_ROLE_KEYWORDS
)
FULLSTACK_ROLE_PATTERN = compile_keyword_pattern(
    FULLSTACK_ROLE_KEYWORDS
)
PYTHON_BACKEND_SIGNAL_PATTERN = compile_keyword_pattern(
    PYTHON_BACKEND_SIGNALS
)
BACKEND_SIGNAL_PATTERN = compile_keyword_pattern(BACKEND_SIGNALS)
FULLSTACK_SIGNAL_PATTERN = compile_keyword_pattern(FULLSTACK_SIGNALS)
JUNIOR_SIGNAL_PATTERN = compile_keyword_pattern(JUNIOR_SIGNALS)
EXPERIENCED_LEVEL_SIGNAL_PATTERN = compile_keyword_pattern(
    EXPERIENCED_LEVEL_SIGNALS
)
PROJECT_MANAGEMENT_ROLE_PATTERN = compile_keyword_pattern(
    PROJECT_MANAGEMENT_ROLE_KEYWORDS
)
PROJECT_MANAGEMENT_SIGNAL_PATTERN = compile_keyword_pattern(
    PROJECT_MANAGEMENT_SIGNALS
)
VIBE_CODING_PATTERN = compile_keyword_pattern(VIBE_CODING_KEYWORDS)
AI_CODING_SIGNAL_PATTERN = compile_keyword_pattern(AI_CODING_SIGNALS)
ROLE_CONTEXT_PATTERN = compile_keyword_pattern(ROLE_CONTEXT_KEYWORDS)
VACANCY_SIGNAL_PATTERN = compile_keyword_pattern(
    VACANCY_SIGNAL_KEYWORDS
)
VACANCY_DETAIL_PATTERN = compile_keyword_pattern(
    VACANCY_DETAIL_KEYWORDS
)
NON_VACANCY_PATTERN = compile_keyword_pattern(
    NON_VACANCY_KEYWORDS
)
HARD_EXCLUSION_PATTERN = re.compile(
    r"(?<!\w)(?:"
    + "|".join(map(re.escape, HARD_EXCLUSION_KEYWORDS))
    + r")(?!\w)",
    flags=re.IGNORECASE,
)

MATCH_THRESHOLD = 6


@dataclass(frozen=True)
class MatchResult:
    matched: bool
    categories: tuple[str, ...]
    score: int
    reasons: tuple[str, ...]

STATE_FILE = Path("collector_state.json")
MATCH_LOG_FILE = Path("logs/matches.jsonl")

EXCLUDED_CHAT_IDS = {
    -1003557704238,
}

CHANNEL_USERNAMES = (
    "iOS_Devv_Jobs",
    "softwaredevelopervacancies",
    "rabotar_razrabotchik",
    "it_jobs_remote",
    "cryptojobs",
    "DefinitiveWeb3",
)

PUBLIC_SEARCH_CHANNEL_USERNAMES = (
    "wbnahodkychat",
    "Infographics_chat",
    "WBOZSellers",
    "MPlace_jobs",
    "MP_Seller",
    "rabota_is_doma_vakansii",
    "GetFrilans",
    "ruwiw",
    "Remotjob",
    "GetClient",
    "work_oi",
    "Info_Job",
    "Edit_Jobs",
    "SMM_Jobss",
    "Content_Jobss",
    "Marketing_Jobss",
    "udalendwork",
    "ydalenkarussian",
    "liberty_job",
    "smmtheworkkp",
    "LeadMagnet_MP",
    "butukovuy_lux",
    "startfreelancer",
    "board_axolotl",
    "travel_2027",
    "neiro_ai_vacancy",
    "eagle_to_work",
    "smm_top_vacancy",
    "sferadeytelnosti",
    "vakansii_ai",
    "Python_Jbs",
    "vakansii_dizaynerov",
    "FreelanceMirr",
    "For_case",
    "design_talking",
    "smm_vakansiii",
    "MPcase",
    "menedgermarketpleisov",
    "freelancekontinent",
    "Designs_squad",
    "prostranstvowork",
    "Jobs_MP",
    "infobizzer",
    "Designs_job",
    "MPmanagers",
    "Copywriters_Job",
    "GetClient_TG",
    "MPdesigns",
    "MPphotographer",
    "Jobs_for_IT",
    "Koteyka_Freelancer",
    "sova_freelance",
    "frilancekomfort",
    "prodviggator",
    "it_vacancy_relocation",
    "vibe_coding_jobs",
    "llm_jobs",
    "remote_ai_jobs",
    "rabota_v_ii",
    "aivacancychannel",
    "Remoteit",
    "jobs_in_it_remoute",
    "datasciencejobs",
    "cryptojobslist",
    "web3hiring",
    "remoteweb3jobs",
    "unicastjobs",
    "stablegram",
    "workingincrypto",
    "web3_jobs_crypto_vazima",
    "Social3Jobs",
    "jobs",
    "fxjobseurope",
)

HISTORY_HOURS = 24

state_lock = asyncio.Lock()


def first_match(pattern: re.Pattern, text: str) -> str | None:
    match = pattern.search(text)
    return match.group(0) if match else None


def classify_message(text: str) -> MatchResult:
    excluded = first_match(HARD_EXCLUSION_PATTERN, text)
    if excluded:
        return MatchResult(
            matched=False,
            categories=(),
            score=0,
            reasons=(f"excluded: {excluded}",),
        )

    vacancy = first_match(VACANCY_SIGNAL_PATTERN, text)
    details = first_match(VACANCY_DETAIL_PATTERN, text)
    junior = first_match(JUNIOR_SIGNAL_PATTERN, text)
    experienced_level = first_match(
        EXPERIENCED_LEVEL_SIGNAL_PATTERN,
        text,
    )
    non_vacancy = first_match(NON_VACANCY_PATTERN, text)
    role_context = first_match(ROLE_CONTEXT_PATTERN, text)

    shared_score = 0
    shared_reasons = []

    if vacancy:
        shared_score += 3
        shared_reasons.append(f"vacancy: {vacancy}")

    if details:
        shared_score += 1
        shared_reasons.append(f"details: {details}")

    if junior:
        shared_score += 1
        shared_reasons.append(f"seniority: {junior}")

    if non_vacancy and not vacancy:
        shared_score -= 5
        shared_reasons.append(f"non-vacancy: {non_vacancy}")

    candidates: list[tuple[str, int, tuple[str, ...]]] = []

    def add_candidate(
        category: str,
        base_score: int,
        *category_reasons: str | None,
    ) -> None:
        reasons = tuple(
            reason
            for reason in category_reasons
            if reason is not None
        ) + tuple(shared_reasons)
        candidates.append(
            (category, base_score + shared_score, reasons)
        )

    mobile_role = first_match(MOBILE_ROLE_PATTERN, text)
    ios_technology = first_match(IOS_KEYWORD_PATTERN, text)

    if mobile_role:
        add_candidate("iOS/Mobile", 6, f"role: {mobile_role}")
    elif ios_technology:
        base_score = 3 + (2 if role_context else 0)
        add_candidate(
            "iOS/Mobile",
            base_score,
            f"technology: {ios_technology}",
            f"role context: {role_context}" if role_context else None,
        )

    python_entry_level_role = first_match(
        PYTHON_ENTRY_LEVEL_ROLE_PATTERN,
        text,
    )
    python_role = first_match(PYTHON_BACKEND_ROLE_PATTERN, text)
    python_technology = first_match(
        PYTHON_BACKEND_SIGNAL_PATTERN,
        text,
    )
    backend = first_match(BACKEND_SIGNAL_PATTERN, text)

    if python_entry_level_role:
        add_candidate(
            "Python Backend",
            6,
            f"entry-level role: {python_entry_level_role}",
        )
    elif (
        python_technology
        and junior
        and not experienced_level
        and (python_role or backend or role_context)
    ):
        base_score = 3
        if python_role:
            base_score += 2
        if backend:
            base_score += 2
        if role_context:
            base_score += 1
        add_candidate(
            "Python Backend",
            base_score,
            f"role: {python_role}" if python_role else None,
            f"technology: {python_technology}",
            f"entry level: {junior}",
            f"backend: {backend}" if backend else None,
            f"role context: {role_context}" if role_context else None,
        )

    fullstack_role = first_match(FULLSTACK_ROLE_PATTERN, text)
    fullstack = first_match(FULLSTACK_SIGNAL_PATTERN, text)

    if fullstack_role:
        add_candidate(
            "Fullstack",
            6,
            f"role: {fullstack_role}",
        )
    elif fullstack:
        base_score = 4 + (2 if role_context else 0)
        add_candidate(
            "Fullstack",
            base_score,
            f"specialization: {fullstack}",
            f"role context: {role_context}" if role_context else None,
        )

    project_role = first_match(
        PROJECT_MANAGEMENT_ROLE_PATTERN,
        text,
    )
    project_management = first_match(
        PROJECT_MANAGEMENT_SIGNAL_PATTERN,
        text,
    )

    if project_role:
        add_candidate(
            "Project Management",
            6,
            f"role: {project_role}",
        )
    elif project_management:
        base_score = 4 + (2 if role_context else 0)
        add_candidate(
            "Project Management",
            base_score,
            f"specialization: {project_management}",
            f"role context: {role_context}" if role_context else None,
        )

    vibe_role = first_match(VIBE_CODING_PATTERN, text)
    ai_signal = first_match(AI_CODING_SIGNAL_PATTERN, text)

    if vibe_role:
        add_candidate(
            "Vibe Coding",
            6,
            f"role: {vibe_role}",
        )
    elif ai_signal:
        base_score = 3 + (3 if role_context else 0)
        add_candidate(
            "Vibe Coding",
            base_score,
            f"AI signal: {ai_signal}",
            f"role context: {role_context}" if role_context else None,
        )

    matched_candidates = [
        candidate
        for candidate in candidates
        if candidate[1] >= MATCH_THRESHOLD
    ]

    if not matched_candidates:
        best_score = max(
            (candidate[1] for candidate in candidates),
            default=0,
        )
        best_reasons = max(
            candidates,
            key=lambda candidate: candidate[1],
            default=("", 0, ()),
        )[2]
        return MatchResult(
            matched=False,
            categories=(),
            score=best_score,
            reasons=best_reasons,
        )

    categories = tuple(
        candidate[0] for candidate in matched_candidates
    )
    score = max(candidate[1] for candidate in matched_candidates)
    reasons = tuple(
        dict.fromkeys(
            reason
            for candidate in matched_candidates
            for reason in candidate[2]
        )
    )

    return MatchResult(
        matched=True,
        categories=categories,
        score=score,
        reasons=reasons,
    )


def contains_keyword(text: str) -> bool:
    return classify_message(text).matched


def is_channel_or_group(chat: object) -> bool:
    return isinstance(chat, (Channel, Chat))


def load_state() -> dict[str, int]:
    if not STATE_FILE.exists():
        return {}

    try:
        data = json.loads(
            STATE_FILE.read_text(encoding="utf-8")
        )

        return {
            str(chat_id): int(message_id)
            for chat_id, message_id in data.items()
        }

    except (json.JSONDecodeError, ValueError, OSError):
        logging.exception("Could not read collector_state.json")
        return {}


state = load_state()


def persist_state(path: Path, data: dict[str, int]) -> None:
    temporary_path: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            dir=path.parent,
            prefix=f".{path.name}.",
            suffix=".tmp",
            delete=False,
        ) as temporary_file:
            temporary_path = Path(temporary_file.name)
            os.fchmod(temporary_file.fileno(), 0o600)
            json.dump(data, temporary_file, indent=2, sort_keys=True)
            temporary_file.flush()
            os.fsync(temporary_file.fileno())

        os.replace(temporary_path, path)
        temporary_path = None
    finally:
        if temporary_path is not None:
            temporary_path.unlink(missing_ok=True)


def record_match(message, chat, result: MatchResult) -> None:
    payload = {
        "recorded_at": datetime.now(timezone.utc).isoformat(),
        "chat_id": message.chat_id,
        "chat_title": getattr(chat, "title", "Unknown channel"),
        "chat_username": getattr(chat, "username", None),
        "message_id": message.id,
        "message_date": (
            message.date.isoformat() if message.date else None
        ),
        "categories": result.categories,
        "score": result.score,
        "reasons": result.reasons,
    }

    try:
        MATCH_LOG_FILE.parent.mkdir(parents=True, exist_ok=True)
        with MATCH_LOG_FILE.open("a", encoding="utf-8") as log_file:
            log_file.write(
                json.dumps(payload, ensure_ascii=False) + "\n"
            )
    except OSError:
        logging.exception("Could not write match audit log")


async def join_configured_channels() -> None:
    for username in CHANNEL_USERNAMES:
        try:
            await client(JoinChannelRequest(username))
            logging.info("Channel ready: @%s", username)
        except RPCError:
            logging.exception(
                "Could not join configured channel @%s",
                username,
            )


async def update_last_processed(
    chat_id: int,
    message_id: int,
) -> None:
    async with state_lock:
        key = str(chat_id)
        current_id = state.get(key, 0)

        if message_id <= current_id:
            return

        state[key] = message_id

        persist_state(STATE_FILE, state)


async def save_text_copy(message, chat) -> None:
    text = message.raw_text or ""
    title = getattr(chat, "title", "Unknown channel")
    username = getattr(chat, "username", None)
    lines = [f"Source: {title}"]

    if username:
        lines.append(
            f"Original: https://t.me/{username}/{message.id}"
        )

    lines.extend(["", text])

    await client.send_message(
        entity="me",
        message="\n".join(lines),
        link_preview=False,
    )


async def save_matching_message(
    message,
    chat,
    result: MatchResult,
) -> None:
    title = getattr(chat, "title", "Unknown channel")

    try:
        await client.forward_messages(
            entity="me",
            messages=message,
        )

        action = "Saved"

    except ChatForwardsRestrictedError:
        await save_text_copy(message, chat)
        action = "Copied forwarding-restricted"

    record_match(message, chat, result)

    logging.info(
        "%s message %s from %s | categories=%s | score=%d | %s",
        action,
        message.id,
        title,
        ", ".join(result.categories),
        result.score,
        "; ".join(result.reasons),
    )


async def process_message(message, chat) -> None:
    chat_id = message.chat_id

    if chat_id is None:
        return

    if chat_id in EXCLUDED_CHAT_IDS:
        return

    last_processed_id = state.get(str(chat_id), 0)

    if message.id <= last_processed_id:
        return

    try:
        result = classify_message(message.raw_text or "")
        if result.matched:
            await save_matching_message(message, chat, result)

        await update_last_processed(
            chat_id=chat_id,
            message_id=message.id,
        )

    except Exception:
        logging.exception(
            "Failed to process message %s from chat %s",
            message.id,
            chat_id,
        )


async def scan_chat_messages(
    chat,
    chat_id: int,
    cutoff: datetime,
) -> int:
    last_processed_id = state.get(str(chat_id), 0)
    messages = []

    async for message in client.iter_messages(
        chat,
        min_id=last_processed_id,
    ):
        if message.date < cutoff:
            break

        messages.append(message)

    for message in reversed(messages):
        await process_message(message, chat)

    return len(messages)


async def scan_last_day() -> None:
    cutoff = datetime.now(timezone.utc) - timedelta(
        hours=HISTORY_HOURS
    )

    logging.info(
        "Scanning channel messages from the last %d hours...",
        HISTORY_HOURS,
    )

    scanned_dialogs = 0
    scanned_messages = 0
    scanned_chat_ids = set()
    scanned_usernames = set()

    async for dialog in client.iter_dialogs():
        chat = dialog.entity
        chat_id = dialog.id

        if not is_channel_or_group(chat):
            continue

        if chat_id in EXCLUDED_CHAT_IDS:
            logging.info(
                "Skipping excluded chat: %s",
                chat_id,
            )
            continue

        scanned_messages += await scan_chat_messages(
            chat,
            chat_id,
            cutoff,
        )

        scanned_chat_ids.add(chat_id)
        username = getattr(chat, "username", None)
        if username:
            scanned_usernames.add(username.casefold())
        scanned_dialogs += 1

    for username in PUBLIC_SEARCH_CHANNEL_USERNAMES:
        if username.casefold() in scanned_usernames:
            continue

        try:
            chat = await client.get_entity(username)

            if not is_channel_or_group(chat):
                logging.warning(
                    "Public source is not a channel: @%s",
                    username,
                )
                continue

            chat_id = utils.get_peer_id(chat)

            if (
                chat_id in EXCLUDED_CHAT_IDS
                or chat_id in scanned_chat_ids
            ):
                continue

            new_message_count = await scan_chat_messages(
                chat,
                chat_id,
                cutoff,
            )
            scanned_messages += new_message_count
            scanned_chat_ids.add(chat_id)
            scanned_usernames.add(username.casefold())
            scanned_dialogs += 1

            logging.info(
                "Scanned public channel @%s: %d new messages",
                username,
                new_message_count,
            )

        except (RPCError, ValueError):
            logging.exception(
                "Could not scan public channel @%s",
                username,
            )

    logging.info(
        "Last-day scan complete: %d dialogs, %d messages checked",
        scanned_dialogs,
        scanned_messages,
    )


@client.on(events.NewMessage(incoming=True))
async def handle_new_message(
    event: events.NewMessage.Event,
) -> None:
    if event.chat_id in EXCLUDED_CHAT_IDS:
        return

    chat = await event.get_chat()

    if not is_channel_or_group(chat):
        return

    await process_message(
        message=event.message,
        chat=chat,
    )


async def main(run_once: bool = False) -> None:
    await client.start()

    try:
        me = await client.get_me()

        logging.info(
            "Authorized as %s",
            me.username or me.first_name,
        )
        logging.info(
            "Excluded chat IDs: %s",
            sorted(EXCLUDED_CHAT_IDS),
        )
        logging.info(
            "Configured public search channels: %d",
            len(PUBLIC_SEARCH_CHANNEL_USERNAMES),
        )

        await join_configured_channels()

        await scan_last_day()

        if run_once:
            logging.info("One-time scan complete")
            return

        logging.info("Monitoring all channels and groups")
        logging.info(
            "Direct keywords: %s",
            ", ".join(
                IOS_KEYWORDS
                + MOBILE_ROLE_KEYWORDS
                + PYTHON_BACKEND_ROLE_KEYWORDS
                + PYTHON_ENTRY_LEVEL_ROLE_KEYWORDS
                + FULLSTACK_ROLE_KEYWORDS
                + PROJECT_MANAGEMENT_ROLE_KEYWORDS
                + VIBE_CODING_KEYWORDS
            ),
        )
        logging.info(
            "Smart filter threshold: %d; audit log: %s",
            MATCH_THRESHOLD,
            MATCH_LOG_FILE,
        )
        logging.info("Matches will be sent to Saved Messages")

        await client.run_until_disconnected()

    finally:
        if client.is_connected():
            await client.disconnect()


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Collect matching Telegram job posts",
    )
    parser.add_argument(
        "--once",
        action="store_true",
        help="scan new messages once and exit",
    )
    return parser.parse_args()


if __name__ == "__main__":
    args = parse_args()
    asyncio.run(main(run_once=args.once))
