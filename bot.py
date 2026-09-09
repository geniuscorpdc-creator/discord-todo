import asyncio
import difflib
import json
import os
import random
import re
import time
import urllib.parse
from pathlib import Path

import aiohttp
import discord
from discord import app_commands
from discord.ext import commands
from dotenv import load_dotenv

load_dotenv()

DISCORD_TOKEN = os.getenv("DISCORD_TOKEN")
LEGACY_TODO_CHANNEL_ID = int(os.getenv("TODO_CHANNEL_ID", "0"))
GUILD_ID = os.getenv("GUILD_ID")


def _parse_id_list(raw: str | None) -> list[int]:
    if not raw:
        return []
    ids: list[int] = []
    for part in raw.replace(";", ",").split(","):
        part = part.strip()
        if not part:
            continue
        try:
            ids.append(int(part))
        except ValueError:
            continue
    return ids


def _env_todo_channel_ids() -> list[int]:
    return _parse_id_list(os.getenv("TODO_CHANNEL_IDS"))


def _env_completed_channel_id() -> int | None:
    raw = os.getenv("COMPLETED_CHANNEL_ID", "").strip()
    if not raw:
        return None
    try:
        return int(raw)
    except ValueError:
        return None

STATUS_TAGS = ["EASY", "MEDIUM", "HARD", "ELITE", "COMPLETED"]
DIFFICULTY_TAGS = ["EASY", "MEDIUM", "HARD", "ELITE"]
COMPLETED_TAG = "COMPLETED"

TAG_PATTERN = re.compile(
    r"^\s*(?:\[(?:EASY|MEDIUM|HARD|ELITE|COMPLETED)\]\s*)+",
    re.IGNORECASE,
)
SINGLE_TAG_PATTERN = re.compile(
    r"\[(EASY|MEDIUM|HARD|ELITE|COMPLETED)\]",
    re.IGNORECASE,
)

CHANNELS_FILE = Path(__file__).parent / "channels.json"
CONFIG_FILE = Path(__file__).parent / "config.json"
QUESTS_FILE = Path(__file__).parent / "quests_data.json"

# OSRS hiscores endpoints. Ironman first; fall back to main hiscores if she
# de-irons or was never on the ironman table.
HISCORES_URL_IRON = "https://secure.runescape.com/m=hiscore_oldschool_ironman/index_lite.ws"
HISCORES_URL_MAIN = "https://secure.runescape.com/m=hiscore_oldschool/index_lite.ws"

# Fixed order of skills in the hiscores CSV. Matches the OSRS hiscores lite
# format (24 rows, first is Overall). Names are canonicalized to Title Case so
# they line up with the skill names inside quests_data.json.
HISCORES_SKILL_ORDER = [
    "Overall", "Attack", "Defence", "Strength", "Hitpoints", "Ranged",
    "Prayer", "Magic", "Cooking", "Woodcutting", "Fletching", "Fishing",
    "Firemaking", "Crafting", "Smithing", "Mining", "Herblore", "Agility",
    "Thieving", "Slayer", "Farming", "Runecraft", "Hunter", "Construction",
]

# Aliases for skills whose canonical name in quests_data.json differs from the
# hiscores name (or from common shorthand).
_SKILL_ALIASES = {
    "runecrafting": "Runecraft",
    "hp": "Hitpoints",
    "range": "Ranged",
}


# ---------------------------------------------------------------------------
# Channel registry
# ---------------------------------------------------------------------------

def _load_channel_ids_from_file() -> list[int]:
    if not CHANNELS_FILE.exists():
        return []
    try:
        data = json.loads(CHANNELS_FILE.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return []
    if not isinstance(data, list):
        return []
    result: list[int] = []
    for item in data:
        try:
            result.append(int(item))
        except (TypeError, ValueError):
            continue
    return result


def load_channel_ids() -> list[int]:
    """Return effective channel IDs.

    If the TODO_CHANNEL_IDS env var is set, it wins (survives Railway redeploys).
    Otherwise, fall back to channels.json.
    """
    env_ids = _env_todo_channel_ids()
    if env_ids:
        return env_ids
    return _load_channel_ids_from_file()


def save_channel_ids(ids: list[int]) -> None:
    unique: list[int] = []
    seen: set[int] = set()
    for cid in ids:
        if cid not in seen:
            seen.add(cid)
            unique.append(cid)
    CHANNELS_FILE.write_text(json.dumps(unique, indent=2), encoding="utf-8")


def add_channel_id(channel_id: int) -> bool:
    ids = _load_channel_ids_from_file()
    if channel_id in ids:
        return False
    ids.append(channel_id)
    save_channel_ids(ids)
    return True


def remove_channel_id(channel_id: int) -> bool:
    ids = _load_channel_ids_from_file()
    if channel_id not in ids:
        return False
    ids = [cid for cid in ids if cid != channel_id]
    save_channel_ids(ids)
    return True


def channels_env_override() -> bool:
    return bool(_env_todo_channel_ids())


def ensure_legacy_migrated() -> None:
    """If a legacy TODO_CHANNEL_ID is set in .env, migrate it into the registry."""
    if channels_env_override():
        return
    if LEGACY_TODO_CHANNEL_ID and LEGACY_TODO_CHANNEL_ID not in _load_channel_ids_from_file():
        add_channel_id(LEGACY_TODO_CHANNEL_ID)


# ---------------------------------------------------------------------------
# General config (config.json)
# ---------------------------------------------------------------------------

def load_config() -> dict:
    if not CONFIG_FILE.exists():
        return {}
    try:
        data = json.loads(CONFIG_FILE.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {}
    return data if isinstance(data, dict) else {}


def save_config(config: dict) -> None:
    CONFIG_FILE.write_text(json.dumps(config, indent=2), encoding="utf-8")


def get_completed_channel_id() -> int | None:
    """Return the effective completed channel ID.

    COMPLETED_CHANNEL_ID env var wins if set (survives Railway redeploys).
    Otherwise, fall back to config.json.
    """
    env_id = _env_completed_channel_id()
    if env_id is not None:
        return env_id
    value = load_config().get("completed_channel_id")
    try:
        return int(value) if value is not None else None
    except (TypeError, ValueError):
        return None


def completed_channel_env_override() -> bool:
    return _env_completed_channel_id() is not None


def set_completed_channel_id(channel_id: int | None) -> None:
    config = load_config()
    if channel_id is None:
        config.pop("completed_channel_id", None)
    else:
        config["completed_channel_id"] = int(channel_id)
    save_config(config)


# ---------------------------------------------------------------------------
# Quest config (quests channel, RSN, completed-quest tracker)
# ---------------------------------------------------------------------------

def _env_quests_channel_id() -> int | None:
    raw = os.getenv("QUESTS_CHANNEL_ID", "").strip()
    if not raw:
        return None
    try:
        return int(raw)
    except ValueError:
        return None


def get_quests_channel_id() -> int | None:
    """Return the effective quests-source channel ID.

    QUESTS_CHANNEL_ID env var wins if set (survives Railway redeploys).
    Otherwise falls back to config.json.
    """
    env_id = _env_quests_channel_id()
    if env_id is not None:
        return env_id
    value = load_config().get("quests_channel_id")
    try:
        return int(value) if value is not None else None
    except (TypeError, ValueError):
        return None


def quests_channel_env_override() -> bool:
    return _env_quests_channel_id() is not None


def set_quests_channel_id(channel_id: int | None) -> None:
    config = load_config()
    if channel_id is None:
        config.pop("quests_channel_id", None)
    else:
        config["quests_channel_id"] = int(channel_id)
    save_config(config)


def get_osrs_username() -> str | None:
    env = os.getenv("OSRS_USERNAME", "").strip()
    if env:
        return env
    value = load_config().get("osrs_username")
    return str(value) if value else None


def osrs_username_env_override() -> bool:
    return bool(os.getenv("OSRS_USERNAME", "").strip())


def set_osrs_username(name: str | None) -> None:
    config = load_config()
    if not name:
        config.pop("osrs_username", None)
    else:
        config["osrs_username"] = str(name).strip()
    save_config(config)


def get_completed_quests() -> list[str]:
    value = load_config().get("completed_quests", [])
    if not isinstance(value, list):
        return []
    return [str(x) for x in value if str(x).strip()]


def save_completed_quests(names: list[str]) -> None:
    config = load_config()
    unique: list[str] = []
    seen: set[str] = set()
    for n in names:
        key = _normalize_quest_name(n)
        if not key or key in seen:
            continue
        seen.add(key)
        unique.append(n)
    config["completed_quests"] = unique
    save_config(config)


def add_completed_quest(name: str) -> bool:
    """Append a canonical quest name to the completed list. Returns True if newly added."""
    existing = get_completed_quests()
    key = _normalize_quest_name(name)
    for x in existing:
        if _normalize_quest_name(x) == key:
            return False
    existing.append(name)
    save_completed_quests(existing)
    return True


# ---------------------------------------------------------------------------
# Quest data (bundled JSON) and fuzzy matcher
# ---------------------------------------------------------------------------

_QUESTS_DATA: list[dict] | None = None


def load_quests_data() -> list[dict]:
    global _QUESTS_DATA
    if _QUESTS_DATA is None:
        try:
            data = json.loads(QUESTS_FILE.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError, FileNotFoundError):
            data = []
        if not isinstance(data, list):
            data = []
        _QUESTS_DATA = data
    return _QUESTS_DATA


def _normalize_quest_name(name: str) -> str:
    """Normalize a quest name for fuzzy comparison."""
    n = strip_tags(name or "")
    n = n.lower()
    # Drop punctuation but keep spaces
    n = re.sub(r"[^a-z0-9 ]+", " ", n)
    n = re.sub(r"\s+", " ", n).strip()
    return n


def resolve_quest(name: str) -> tuple[str, object]:
    """Fuzzy-match a free-form title to a canonical quest.

    Returns one of:
      ("match", quest_dict)
      ("ambiguous", [quest_dict, ...])
      ("none", None)
    """
    quests = load_quests_data()
    if not quests:
        return ("none", None)
    target = _normalize_quest_name(name)
    if not target:
        return ("none", None)

    by_norm: dict[str, dict] = {}
    for q in quests:
        key = _normalize_quest_name(q.get("name", ""))
        if key:
            by_norm[key] = q

    if target in by_norm:
        return ("match", by_norm[target])

    candidates = list(by_norm.keys())
    matches = difflib.get_close_matches(target, candidates, n=3, cutoff=0.75)
    if not matches:
        # Fall back to substring containment as a last resort (helpful for
        # "Dragon Slayer 2" vs canonical "Dragon Slayer II" etc).
        contained = [k for k in candidates if target in k or k in target]
        if len(contained) == 1:
            return ("match", by_norm[contained[0]])
        if contained:
            return ("ambiguous", [by_norm[k] for k in contained[:3]])
        return ("none", None)

    top_ratio = difflib.SequenceMatcher(None, target, matches[0]).ratio()
    if len(matches) == 1 or top_ratio >= 0.92:
        return ("match", by_norm[matches[0]])
    return ("ambiguous", [by_norm[m] for m in matches])


def canonicalize_quest_names(names: list[str]) -> tuple[list[str], list[str]]:
    """Resolve a list of user-provided quest names to canonical form.

    Returns (canonical_matched, unmatched_originals).
    """
    matched: list[str] = []
    unmatched: list[str] = []
    seen: set[str] = set()
    for raw in names:
        raw = raw.strip()
        if not raw:
            continue
        status, payload = resolve_quest(raw)
        if status == "match":
            canonical = payload["name"]  # type: ignore[index]
            key = _normalize_quest_name(canonical)
            if key not in seen:
                seen.add(key)
                matched.append(canonical)
        else:
            unmatched.append(raw)
    return matched, unmatched


# ---------------------------------------------------------------------------
# OSRS hiscores
# ---------------------------------------------------------------------------

_HISCORES_CACHE: dict[str, tuple[float, dict[str, int]]] = {}
_HISCORES_TTL = 60.0


def _parse_hiscores(text: str) -> dict[str, int]:
    result: dict[str, int] = {}
    lines = text.strip().splitlines()
    for skill, line in zip(HISCORES_SKILL_ORDER, lines):
        parts = line.split(",")
        if len(parts) < 2:
            continue
        try:
            lvl = int(parts[1])
        except ValueError:
            continue
        # Unranked skills return -1; treat those as level 1.
        if lvl < 1:
            lvl = 1
        result[skill] = lvl
    return result


async def fetch_hiscores(rsn: str) -> dict[str, int] | None:
    """Fetch skill levels from the OSRS hiscores. Returns None if not found."""
    key = rsn.strip().lower()
    if not key:
        return None
    now = time.monotonic()
    cached = _HISCORES_CACHE.get(key)
    if cached and (now - cached[0]) < _HISCORES_TTL:
        return cached[1]

    timeout = aiohttp.ClientTimeout(total=15)
    async with aiohttp.ClientSession(timeout=timeout) as session:
        for url in (HISCORES_URL_IRON, HISCORES_URL_MAIN):
            try:
                async with session.get(url, params={"player": rsn}) as resp:
                    if resp.status == 200:
                        text = await resp.text()
                        parsed = _parse_hiscores(text)
                        if parsed:
                            _HISCORES_CACHE[key] = (now, parsed)
                            return parsed
                    # 404 = not on this table; try the next one.
            except (aiohttp.ClientError, asyncio.TimeoutError):
                continue
    return None


# ---------------------------------------------------------------------------
# Requirement checker
# ---------------------------------------------------------------------------

def _canonical_skill(name: str) -> str:
    lower = name.strip().lower()
    if lower in _SKILL_ALIASES:
        return _SKILL_ALIASES[lower]
    return lower.capitalize()


def compute_quest_points(completed: list[str]) -> int:
    """Sum qp_reward across all completed quests known to quests_data.json."""
    total = 0
    lookup: dict[str, dict] = {
        _normalize_quest_name(q.get("name", "")): q for q in load_quests_data()
    }
    for name in completed:
        q = lookup.get(_normalize_quest_name(name))
        if q:
            try:
                total += int(q.get("qp_reward") or 0)
            except (TypeError, ValueError):
                pass
    return total


class RequirementReport:
    def __init__(self) -> None:
        self.skill_failures: list[tuple[str, int, int]] = []  # (skill, required, have)
        self.quest_failures: list[str] = []
        self.qp_required: int = 0
        self.qp_have: int = 0

    @property
    def qp_pass(self) -> bool:
        return self.qp_have >= self.qp_required

    @property
    def ok(self) -> bool:
        return (
            not self.skill_failures
            and not self.quest_failures
            and self.qp_pass
        )


def check_requirements(
    quest: dict,
    hiscores: dict[str, int],
    completed_quests: list[str],
) -> RequirementReport:
    report = RequirementReport()
    skills_req = quest.get("skills") or {}
    for raw_skill, required in skills_req.items():
        try:
            required_int = int(required)
        except (TypeError, ValueError):
            continue
        canonical = _canonical_skill(raw_skill)
        have = int(hiscores.get(canonical, 1))
        if have < required_int:
            report.skill_failures.append((canonical, required_int, have))

    completed_norm = {_normalize_quest_name(n) for n in completed_quests}
    for prereq in quest.get("quest_prerequisites") or []:
        if _normalize_quest_name(prereq) not in completed_norm:
            report.quest_failures.append(prereq)

    try:
        report.qp_required = int(quest.get("qp_required") or 0)
    except (TypeError, ValueError):
        report.qp_required = 0
    report.qp_have = compute_quest_points(completed_quests)

    return report


def format_requirement_failure(quest: dict, report: RequirementReport, rsn: str) -> str:
    lines = [f'Cannot promote **"{quest["name"]}"** \u2014 requirements not met.', ""]
    if report.skill_failures:
        lines.append("**Skills:**")
        for skill, req, have in report.skill_failures:
            short = req - have
            lines.append(f"  \u2022 {skill} {req} (has {have}) \u2014 {short} short")
    if report.quest_failures:
        lines.append("**Quests:**")
        for q in report.quest_failures:
            lines.append(f"  \u2022 {q} (not completed)")
    if not report.qp_pass:
        lines.append(
            f"**Quest points:** {report.qp_have} / {report.qp_required} \u2014 "
            f"{report.qp_required - report.qp_have} short"
        )
    lines.append("")
    lines.append(f"_Hiscores fetched for: {rsn}_")
    return "\n".join(lines)


def build_requirements_summary(quest: dict) -> str:
    """Human-readable requirements block for use as a forum-post body."""
    lines: list[str] = ["**Requirements:**"]
    skills = quest.get("skills") or {}
    if skills:
        parts = [f"{_canonical_skill(k)} {v}" for k, v in skills.items()]
        lines.append(f"  Skills: {', '.join(parts)}")
    else:
        lines.append("  Skills: (none)")
    if quest.get("qp_required"):
        lines.append(f"  Quest points: {quest['qp_required']}")
    prereqs = quest.get("quest_prerequisites") or []
    if prereqs:
        lines.append(f"  Quest prerequisites: {', '.join(prereqs)}")
    if quest.get("difficulty"):
        lines.append(f"  Difficulty: {quest['difficulty']}")
    url = quest.get("url")
    if not url:
        url = "https://oldschool.runescape.wiki/w/" + urllib.parse.quote(
            quest["name"].replace(" ", "_"), safe="_"
        )
    lines.append(f"Wiki: {url}")
    return "\n".join(lines)


def record_quest_completion(thread_name: str) -> str | None:
    """If the thread title resolves to a canonical quest, add it to completed_quests.

    Returns the canonical name if newly added, else None.
    """
    status, payload = resolve_quest(thread_name)
    if status != "match":
        return None
    canonical = payload["name"]  # type: ignore[index]
    if add_completed_quest(canonical):
        return canonical
    return None


# ---------------------------------------------------------------------------
# Status tag helpers
# ---------------------------------------------------------------------------

def strip_tags(name: str) -> str:
    """Remove any leading [STATUS] prefixes and return a cleaned title."""
    return TAG_PATTERN.sub("", name).strip()


def _find_tags(name: str) -> list[str]:
    """Return all recognized tags found in the leading tag block, uppercase."""
    prefix_match = TAG_PATTERN.match(name)
    if prefix_match is None:
        return []
    return [m.group(1).upper() for m in SINGLE_TAG_PATTERN.finditer(prefix_match.group(0))]


def get_difficulty(thread: discord.Thread) -> str | None:
    """Return the uppercase difficulty tag on the thread, or None if untagged."""
    for tag in _find_tags(thread.name):
        if tag in DIFFICULTY_TAGS:
            return tag
    return None


def get_status(thread: discord.Thread) -> str | None:
    """Backwards-compat: return the first recognized tag on the thread."""
    tags = _find_tags(thread.name)
    return tags[0] if tags else None


def is_completed(thread: discord.Thread) -> bool:
    return COMPLETED_TAG in _find_tags(thread.name)


def apply_status(name: str, status: str) -> str:
    """Apply a status tag while preserving the other tag slot.

    - Setting COMPLETED preserves any existing difficulty tag.
    - Setting a difficulty preserves the COMPLETED tag if present.
    """
    existing = _find_tags(name)
    base = strip_tags(name)
    status = status.upper()

    completed = COMPLETED_TAG in existing
    difficulty: str | None = next((t for t in existing if t in DIFFICULTY_TAGS), None)

    if status == COMPLETED_TAG:
        completed = True
    elif status in DIFFICULTY_TAGS:
        difficulty = status

    parts: list[str] = []
    if completed:
        parts.append(f"[{COMPLETED_TAG}]")
    if difficulty:
        parts.append(f"[{difficulty}]")
    parts.append(base)
    return " ".join(p for p in parts if p).strip()


# ---------------------------------------------------------------------------
# Channel + thread helpers
# ---------------------------------------------------------------------------

async def get_channels(bot: commands.Bot) -> list[discord.abc.GuildChannel]:
    channels: list[discord.abc.GuildChannel] = []
    for cid in load_channel_ids():
        channel = bot.get_channel(cid)
        if channel is None:
            try:
                channel = await bot.fetch_channel(cid)
            except discord.HTTPException:
                continue
        if isinstance(channel, discord.abc.GuildChannel):
            channels.append(channel)
    return channels


async def get_open_threads(
    channels: list[discord.abc.GuildChannel],
) -> tuple[list[discord.Thread], int]:
    """Return open (non-completed) threads and total active thread count across all channels."""
    seen: dict[int, discord.Thread] = {}
    for channel in channels:
        threads = getattr(channel, "threads", None)
        if not threads:
            continue
        for thread in threads:
            seen[thread.id] = thread

    all_active = list(seen.values())
    open_threads = [t for t in all_active if not is_completed(t)]
    return open_threads, len(all_active)


def filter_by_difficulty(
    threads: list[discord.Thread],
    difficulty: str | None,
) -> list[discord.Thread]:
    """Filter open threads by difficulty tag. None or 'ANY' = no filter."""
    if difficulty is None or difficulty.upper() == "ANY":
        return threads
    target = difficulty.upper()
    return [t for t in threads if get_difficulty(t) == target]


async def get_starter_content(thread: discord.Thread) -> tuple[str, discord.User | discord.Member | None]:
    """Return (content, author) of the thread's starter message, best-effort.

    Falls back to the oldest message in history if starter_message is unavailable.
    """
    starter = thread.starter_message
    if starter is None:
        try:
            starter = await thread.parent.fetch_message(thread.id)  # type: ignore[union-attr]
        except (discord.HTTPException, AttributeError):
            starter = None
    if starter is None:
        try:
            async for msg in thread.history(limit=1, oldest_first=True):
                starter = msg
                break
        except discord.HTTPException:
            starter = None
    if starter is None:
        return "", None
    return starter.content or "", starter.author


async def recreate_thread_in(
    target: discord.abc.GuildChannel,
    thread: discord.Thread,
    new_name: str,
    *,
    reason: str,
    archive_note: str | None = None,
) -> tuple[discord.Thread | None, str | None]:
    """Recreate ``thread`` inside ``target`` (Text or Forum) and delete the original.

    Returns (new_thread, error_message). Copies the starter message content and
    author into a short header + body so the essential context isn't lost.
    """
    if not isinstance(target, (discord.TextChannel, discord.ForumChannel)):
        return None, "Target channel must be a text or forum channel."

    starter_content, starter_author = await get_starter_content(thread)

    header_lines: list[str] = []
    if starter_author is not None:
        header_lines.append(f"Originally posted by {starter_author.mention}")
    if archive_note:
        header_lines.append(archive_note)
    elif thread.parent is not None:
        header_lines.append(f"Moved from #{thread.parent.name}")
    header = "\n".join(header_lines)

    body_parts = [header]
    if starter_content:
        body_parts.append("")
        body_parts.append(starter_content)
    body = "\n".join(part for part in body_parts if part)

    if len(body) > 2000:
        body = body[:1997] + "..."

    new_thread: discord.Thread
    try:
        if isinstance(target, discord.ForumChannel):
            result = await target.create_thread(
                name=new_name,
                content=body or new_name,
                reason=reason,
            )
            new_thread = result.thread
        else:
            new_thread = await target.create_thread(
                name=new_name,
                type=discord.ChannelType.public_thread,
                reason=reason,
            )
            if body:
                try:
                    await new_thread.send(body)
                except discord.HTTPException:
                    pass
    except discord.Forbidden:
        return None, "I don't have permission to create threads/posts in the target channel."
    except discord.HTTPException as e:
        return None, f"Failed to create thread in target channel: {e}"

    try:
        await thread.delete()
    except discord.HTTPException as e:
        return new_thread, (
            f"Created new thread {new_thread.mention}, but failed to delete original: {e}"
        )

    return new_thread, None


async def move_thread_to_completed(
    bot: commands.Bot,
    thread: discord.Thread,
    new_name: str,
) -> tuple[discord.Thread | None, str | None]:
    """Recreate the thread in the configured completed channel and delete the original.

    Returns (new_thread, error_message). If completed channel is not configured,
    returns (None, None) so the caller can fall back to an in-place rename.
    """
    completed_channel_id = get_completed_channel_id()
    if completed_channel_id is None:
        return None, None

    target = bot.get_channel(completed_channel_id)
    if target is None:
        try:
            target = await bot.fetch_channel(completed_channel_id)
        except discord.HTTPException:
            return None, "Completed channel is not accessible. Reset it with /set-completed-channel."

    if not isinstance(target, (discord.TextChannel, discord.ForumChannel)):
        return None, "Completed channel must be a text or forum channel. Reset it with /set-completed-channel."

    archive_note = (
        f"Archived from #{thread.parent.name}" if thread.parent else "Archived thread"
    )
    return await recreate_thread_in(
        target,
        thread,
        new_name,
        reason="Moved from active to-do channel on completion",
        archive_note=archive_note,
    )


# ---------------------------------------------------------------------------
# Embeds
# ---------------------------------------------------------------------------

def build_pick_embed(
    thread: discord.Thread,
    open_count: int,
    total_count: int,
    difficulty: str | None = None,
) -> discord.Embed:
    filter_label = (
        f" ({difficulty.title()})"
        if difficulty and difficulty.upper() != "ANY"
        else ""
    )
    description = (
        f"**Remaining{filter_label}:** There is currently {open_count} to-do list "
        f"item(s) matching out of {total_count} total to-dos added."
    )
    embed = discord.Embed(
        title=thread.name,
        url=thread.jump_url,
        description=description,
        color=0xFFD700,
    )

    embed.add_field(
        name="\u200b",
        value=(
            "You have been doing so well, keep up the good work! "
            "If you have any questions, I will be happy to help you out!"
        ),
        inline=True,
    )

    embed.add_field(
        name="This to-do list item was created on:",
        value=discord.utils.format_dt(thread.created_at, style="D"),
        inline=True,
    )

    difficulty = get_difficulty(thread)
    if difficulty:
        embed.add_field(name="Difficulty", value=difficulty.title(), inline=True)

    embed.set_footer(text="Your OSRS To-Do")
    return embed


def build_all_done_embed(difficulty: str | None = None) -> discord.Embed:
    if difficulty and difficulty.upper() != "ANY":
        title = f"No open {difficulty.title()} tasks!"
        desc = (
            f"There are no open {difficulty.title()} to-dos right now. "
            "Try a different difficulty."
        )
    else:
        title = "All to-dos complete!"
        desc = "Nothing left to pick. Time to celebrate or add more goals!"
    return discord.Embed(title=title, description=desc, color=0x57F287)


def build_no_channels_embed() -> discord.Embed:
    return discord.Embed(
        title="No to-do channels registered",
        description=(
            "Register a channel with `/register-channel` (run it in the channel), "
            "or create a new one with `/create-todo-channel name:<name>`."
        ),
        color=0xED4245,
    )


# ---------------------------------------------------------------------------
# Views
# ---------------------------------------------------------------------------

class PickAgainView(discord.ui.View):
    def __init__(self, bot: commands.Bot, difficulty: str | None):
        super().__init__(timeout=300)
        self.bot = bot
        self.difficulty = difficulty

    @discord.ui.button(
        label="Pick Again",
        style=discord.ButtonStyle.primary,
        emoji="\N{ANTICLOCKWISE DOWNWARDS AND UPWARDS OPEN CIRCLE ARROWS}",
    )
    async def pick_again(
        self,
        interaction: discord.Interaction,
        button: discord.ui.Button,
    ) -> None:
        await do_pick(interaction, self.bot, self.difficulty, edit=True)

    @discord.ui.button(
        label="Change Difficulty",
        style=discord.ButtonStyle.secondary,
    )
    async def change_difficulty(
        self,
        interaction: discord.Interaction,
        button: discord.ui.Button,
    ) -> None:
        view = DifficultySelectView(self.bot)
        await interaction.response.edit_message(
            content="Pick a difficulty:",
            embed=None,
            view=view,
        )


class DifficultySelect(discord.ui.Select):
    def __init__(self, bot: commands.Bot):
        options = [
            discord.SelectOption(label="Any", value="ANY", description="Any open to-do"),
            discord.SelectOption(label="Easy", value="EASY"),
            discord.SelectOption(label="Medium", value="MEDIUM"),
            discord.SelectOption(label="Hard", value="HARD"),
            discord.SelectOption(label="Elite", value="ELITE"),
        ]
        super().__init__(
            placeholder="Choose a difficulty...",
            min_values=1,
            max_values=1,
            options=options,
        )
        self.bot = bot

    async def callback(self, interaction: discord.Interaction) -> None:
        choice = self.values[0]
        await do_pick(interaction, self.bot, choice, edit=True)


class DifficultySelectView(discord.ui.View):
    def __init__(self, bot: commands.Bot):
        super().__init__(timeout=300)
        self.add_item(DifficultySelect(bot))


async def do_pick(
    interaction: discord.Interaction,
    bot: commands.Bot,
    difficulty: str | None,
    edit: bool,
) -> None:
    channels = await get_channels(bot)
    if not channels:
        embed = build_no_channels_embed()
        if edit:
            await interaction.response.edit_message(content=None, embed=embed, view=None)
        else:
            await interaction.response.send_message(embed=embed, ephemeral=True)
        return

    open_threads, total = await get_open_threads(channels)
    filtered = filter_by_difficulty(open_threads, difficulty)

    if not filtered:
        embed = build_all_done_embed(difficulty)
        view = PickAgainView(bot, difficulty)
        view.pick_again.disabled = True
        if edit:
            await interaction.response.edit_message(content=None, embed=embed, view=view)
        else:
            await interaction.response.send_message(embed=embed, view=view, ephemeral=True)
        return

    thread = random.choice(filtered)
    embed = build_pick_embed(thread, len(filtered), total, difficulty)
    view = PickAgainView(bot, difficulty)
    if edit:
        await interaction.response.edit_message(content=None, embed=embed, view=view)
    else:
        await interaction.response.send_message(embed=embed, view=view, ephemeral=True)


# ---------------------------------------------------------------------------
# Bot
# ---------------------------------------------------------------------------

class TodoBot(commands.Bot):
    def __init__(self) -> None:
        intents = discord.Intents.default()
        super().__init__(command_prefix="!", intents=intents)

    async def setup_hook(self) -> None:
        if GUILD_ID:
            guild = discord.Object(id=int(GUILD_ID))
            self.tree.copy_global_to(guild=guild)
            await self.tree.sync(guild=guild)
        else:
            await self.tree.sync()


bot = TodoBot()


@bot.event
async def on_ready() -> None:
    ensure_legacy_migrated()
    channels = await get_channels(bot)
    if not channels:
        print("Warning: no to-do channels registered. Use /register-channel or /create-todo-channel.")
    else:
        names = ", ".join(f"#{c.name} ({c.id})" for c in channels)
        print(f"Watching to-do channels: {names}")
    print(f"Logged in as {bot.user}")


# ---------------------------------------------------------------------------
# Slash commands
# ---------------------------------------------------------------------------

@bot.tree.command(
    name="pick-todo",
    description="Pick a random open OSRS to-do by difficulty",
)
async def pick_todo(interaction: discord.Interaction) -> None:
    channels = await get_channels(bot)
    if not channels:
        await interaction.response.send_message(
            embed=build_no_channels_embed(),
            ephemeral=True,
        )
        return

    view = DifficultySelectView(bot)
    await interaction.response.send_message(
        content="Pick a difficulty:",
        view=view,
        ephemeral=True,
    )


STATUS_CHOICES = [
    app_commands.Choice(name="Easy", value="EASY"),
    app_commands.Choice(name="Medium", value="MEDIUM"),
    app_commands.Choice(name="Hard", value="HARD"),
    app_commands.Choice(name="Elite", value="ELITE"),
    app_commands.Choice(name="Completed", value="COMPLETED"),
]


@bot.tree.command(
    name="set-status",
    description="Set the status/difficulty of the current to-do thread",
)
@app_commands.choices(status=STATUS_CHOICES)
async def set_status(
    interaction: discord.Interaction,
    status: app_commands.Choice[str],
) -> None:
    if not isinstance(interaction.channel, discord.Thread):
        await interaction.response.send_message(
            "Run this command inside a to-do thread.",
            ephemeral=True,
        )
        return

    thread = interaction.channel
    new_name = apply_status(thread.name, status.value)

    if len(new_name) > 100:
        await interaction.response.send_message(
            "Cannot rename: title would exceed Discord's 100 character limit.",
            ephemeral=True,
        )
        return

    completed_id = get_completed_channel_id()
    should_move = (
        status.value == COMPLETED_TAG
        and completed_id is not None
        and (thread.parent_id is None or thread.parent_id != completed_id)
    )

    if new_name == thread.name and not should_move:
        await interaction.response.send_message(
            f"Already set to **{status.name}**.",
            ephemeral=True,
        )
        return

    await interaction.response.defer(ephemeral=True, thinking=True)

    if should_move:
        new_thread, err = await move_thread_to_completed(bot, thread, new_name)
        if new_thread is not None:
            recorded = record_quest_completion(thread.name)
            msg = f"Marked complete and moved to {new_thread.mention}."
            if err:
                msg += f"\nNote: {err}"
            if recorded:
                msg += f"\nAdded **{recorded}** to the completed-quests list."
            await interaction.followup.send(msg, ephemeral=True)
            return
        if err:
            await interaction.followup.send(err, ephemeral=True)
            return

    try:
        await thread.edit(name=new_name)
    except discord.HTTPException as e:
        await interaction.followup.send(
            f"Failed to rename thread: {e}. "
            "Discord rate-limits thread renames (usually 2 per 10 minutes).",
            ephemeral=True,
        )
        return
    recorded = record_quest_completion(new_name) if status.value == COMPLETED_TAG else None
    extra = f"\nAdded **{recorded}** to the completed-quests list." if recorded else ""
    await interaction.followup.send(
        f"Status set to **{status.name}**: {new_name}{extra}",
        ephemeral=True,
    )


@bot.tree.command(
    name="complete",
    description="Mark this to-do thread as completed (alias for /set-status Completed)",
)
async def complete(interaction: discord.Interaction) -> None:
    if not isinstance(interaction.channel, discord.Thread):
        await interaction.response.send_message(
            "Run this command inside a to-do thread.",
            ephemeral=True,
        )
        return

    thread = interaction.channel
    completed_id = get_completed_channel_id()
    should_move = (
        completed_id is not None
        and (thread.parent_id is None or thread.parent_id != completed_id)
    )

    if is_completed(thread) and not should_move:
        await interaction.response.send_message("Already marked complete.", ephemeral=True)
        return

    new_name = apply_status(thread.name, COMPLETED_TAG)
    if len(new_name) > 100:
        await interaction.response.send_message(
            "Cannot rename: title would exceed Discord's 100 character limit.",
            ephemeral=True,
        )
        return

    await interaction.response.defer(ephemeral=True, thinking=True)

    if should_move:
        new_thread, err = await move_thread_to_completed(bot, thread, new_name)
        if new_thread is not None:
            recorded = record_quest_completion(thread.name)
            msg = f"Marked complete and moved to {new_thread.mention}."
            if err:
                msg += f"\nNote: {err}"
            if recorded:
                msg += f"\nAdded **{recorded}** to the completed-quests list."
            await interaction.followup.send(msg, ephemeral=True)
            return
        if err:
            await interaction.followup.send(err, ephemeral=True)
            return

    try:
        await thread.edit(name=new_name)
    except discord.HTTPException as e:
        await interaction.followup.send(
            f"Failed to rename thread: {e}. "
            "Discord rate-limits thread renames (usually 2 per 10 minutes).",
            ephemeral=True,
        )
        return
    recorded = record_quest_completion(new_name)
    extra = f"\nAdded **{recorded}** to the completed-quests list." if recorded else ""
    await interaction.followup.send(
        f"Marked complete: **{new_name}**{extra}",
        ephemeral=True,
    )


# ---------------------------------------------------------------------------
# Channel management commands
# ---------------------------------------------------------------------------

@bot.tree.command(
    name="register-channel",
    description="Register a channel as a to-do channel (defaults to current channel)",
)
@app_commands.describe(channel="The channel to register (defaults to the current channel)")
@app_commands.default_permissions(manage_channels=True)
async def register_channel(
    interaction: discord.Interaction,
    channel: discord.TextChannel | None = None,
) -> None:
    target = channel or interaction.channel
    if not isinstance(target, discord.TextChannel):
        await interaction.response.send_message(
            "Please specify a text channel (or run this from inside one).",
            ephemeral=True,
        )
        return

    added = add_channel_id(target.id)
    note = ""
    if channels_env_override():
        note = (
            "\nNote: `TODO_CHANNEL_IDS` env var is set and takes precedence over "
            "channels.json. Add this ID to that env var to see it take effect."
        )
    if added:
        await interaction.response.send_message(
            f"Registered {target.mention} as a to-do channel.{note}",
            ephemeral=True,
        )
    else:
        await interaction.response.send_message(
            f"{target.mention} is already registered.{note}",
            ephemeral=True,
        )


@bot.tree.command(
    name="unregister-channel",
    description="Unregister a channel from the to-do registry",
)
@app_commands.describe(channel="The channel to unregister (defaults to the current channel)")
@app_commands.default_permissions(manage_channels=True)
async def unregister_channel(
    interaction: discord.Interaction,
    channel: discord.TextChannel | None = None,
) -> None:
    target = channel or interaction.channel
    if not isinstance(target, discord.TextChannel):
        await interaction.response.send_message(
            "Please specify a text channel (or run this from inside one).",
            ephemeral=True,
        )
        return

    removed = remove_channel_id(target.id)
    if removed:
        await interaction.response.send_message(
            f"Unregistered {target.mention}.",
            ephemeral=True,
        )
    else:
        await interaction.response.send_message(
            f"{target.mention} was not registered.",
            ephemeral=True,
        )


@bot.tree.command(
    name="create-todo-channel",
    description="Create a new text channel and register it as a to-do channel",
)
@app_commands.describe(
    name="Name for the new channel",
    category="Optional category to place it under",
)
@app_commands.default_permissions(manage_channels=True)
async def create_todo_channel(
    interaction: discord.Interaction,
    name: str,
    category: discord.CategoryChannel | None = None,
) -> None:
    guild = interaction.guild
    if guild is None:
        await interaction.response.send_message(
            "This command must be used in a server.",
            ephemeral=True,
        )
        return

    if category is None and isinstance(interaction.channel, discord.TextChannel):
        category = interaction.channel.category

    await interaction.response.defer(ephemeral=True, thinking=True)
    try:
        new_channel = await guild.create_text_channel(name=name, category=category)
    except discord.Forbidden:
        await interaction.followup.send(
            "I don't have permission to create channels here.",
            ephemeral=True,
        )
        return
    except discord.HTTPException as e:
        await interaction.followup.send(
            f"Failed to create channel: {e}",
            ephemeral=True,
        )
        return

    add_channel_id(new_channel.id)
    await interaction.followup.send(
        f"Created and registered {new_channel.mention}.",
        ephemeral=True,
    )


@bot.tree.command(
    name="list-todo-channels",
    description="List all registered to-do channels",
)
async def list_todo_channels(interaction: discord.Interaction) -> None:
    ids = load_channel_ids()
    if not ids:
        await interaction.response.send_message(
            embed=build_no_channels_embed(),
            ephemeral=True,
        )
        return

    lines: list[str] = []
    for cid in ids:
        ch = bot.get_channel(cid)
        if ch is None:
            try:
                ch = await bot.fetch_channel(cid)
            except discord.HTTPException:
                ch = None
        if ch is None:
            lines.append(f"- `{cid}` (not accessible)")
        else:
            lines.append(f"- {ch.mention} (`{cid}`)")

    completed_id = get_completed_channel_id()
    completed_line = "*(not set)*"
    if completed_id is not None:
        cch = bot.get_channel(completed_id)
        if cch is None:
            try:
                cch = await bot.fetch_channel(completed_id)
            except discord.HTTPException:
                cch = None
        completed_line = (
            f"{cch.mention} (`{completed_id}`)"
            if cch is not None
            else f"`{completed_id}` (not accessible)"
        )

    quests_id = get_quests_channel_id()
    quests_line = "*(not set)*"
    if quests_id is not None:
        qch = bot.get_channel(quests_id)
        if qch is None:
            try:
                qch = await bot.fetch_channel(quests_id)
            except discord.HTTPException:
                qch = None
        quests_line = (
            f"{qch.mention} (`{quests_id}`)"
            if qch is not None
            else f"`{quests_id}` (not accessible)"
        )

    rsn = get_osrs_username()
    rsn_line = f"`{rsn}`" if rsn else "*(not set)*"

    source_notes: list[str] = []
    if channels_env_override():
        source_notes.append("Channel list from `TODO_CHANNEL_IDS` env var")
    else:
        source_notes.append("Channel list from `channels.json`")
    if completed_channel_env_override():
        source_notes.append("Completed channel from `COMPLETED_CHANNEL_ID` env var")
    elif get_completed_channel_id() is not None:
        source_notes.append("Completed channel from `config.json`")
    if quests_channel_env_override():
        source_notes.append("Quests channel from `QUESTS_CHANNEL_ID` env var")
    if osrs_username_env_override():
        source_notes.append("RSN from `OSRS_USERNAME` env var")

    embed = discord.Embed(
        title="Registered to-do channels",
        description="\n".join(lines),
        color=0x5865F2,
    )
    embed.add_field(name="Completed archive channel", value=completed_line, inline=False)
    embed.add_field(name="Quests source channel", value=quests_line, inline=False)
    embed.add_field(name="OSRS RSN", value=rsn_line, inline=False)
    embed.set_footer(text=" | ".join(source_notes))
    await interaction.response.send_message(embed=embed, ephemeral=True)


@bot.tree.command(
    name="set-completed-channel",
    description="Set the text or forum channel where completed to-do threads are moved",
)
@app_commands.describe(channel="A text or forum channel to send completed threads to")
@app_commands.default_permissions(manage_channels=True)
async def set_completed_channel(
    interaction: discord.Interaction,
    channel: discord.TextChannel | discord.ForumChannel,
) -> None:
    kind = "forum posts" if isinstance(channel, discord.ForumChannel) else "threads"
    set_completed_channel_id(channel.id)
    note = ""
    if completed_channel_env_override():
        note = (
            "\n**Warning:** `COMPLETED_CHANNEL_ID` env var is set and takes precedence "
            "over this setting. Update that env var to change the target."
        )
    await interaction.response.send_message(
        f"Completed to-dos will now be moved to {channel.mention} as {kind}. "
        "Note: the original thread (and all its messages/replies) will be **deleted** on completion; "
        "only the starter message is preserved." + note,
        ephemeral=True,
    )


@bot.tree.command(
    name="clear-completed-channel",
    description="Stop moving completed threads; revert to in-place [COMPLETED] tagging",
)
@app_commands.default_permissions(manage_channels=True)
async def clear_completed_channel(interaction: discord.Interaction) -> None:
    set_completed_channel_id(None)
    await interaction.response.send_message(
        "Cleared. Completed threads will stay in place and just be tagged `[COMPLETED]`.",
        ephemeral=True,
    )


# ---------------------------------------------------------------------------
# Quest commands
# ---------------------------------------------------------------------------

@bot.tree.command(
    name="set-rsn",
    description="Set the OSRS RSN used for quest requirement checks",
)
@app_commands.describe(username="Her Old School RuneScape display name (case-insensitive)")
@app_commands.default_permissions(manage_channels=True)
async def set_rsn(interaction: discord.Interaction, username: str) -> None:
    username = username.strip()
    if not username:
        await interaction.response.send_message("Provide a non-empty RSN.", ephemeral=True)
        return
    set_osrs_username(username)
    note = ""
    if osrs_username_env_override():
        note = (
            "\n**Warning:** `OSRS_USERNAME` env var is set and takes precedence "
            "over this setting."
        )
    await interaction.response.send_message(
        f"OSRS RSN set to `{username}`.{note}", ephemeral=True
    )


@bot.tree.command(
    name="set-quests-channel",
    description="Set the forum channel where quest threads live before promotion",
)
@app_commands.describe(channel="A forum channel that holds one thread per quest")
@app_commands.default_permissions(manage_channels=True)
async def set_quests_channel(
    interaction: discord.Interaction,
    channel: discord.ForumChannel,
) -> None:
    set_quests_channel_id(channel.id)
    note = ""
    if quests_channel_env_override():
        note = (
            "\n**Warning:** `QUESTS_CHANNEL_ID` env var is set and takes precedence "
            "over this setting."
        )
    await interaction.response.send_message(
        f"Quest source channel set to {channel.mention}. "
        "Use `/populate-quests-channel` to auto-create a post per quest, and "
        "`/promote-quest` from inside a quest thread to move it to the to-do list "
        f"if requirements are met.{note}",
        ephemeral=True,
    )


@bot.tree.command(
    name="clear-quests-channel",
    description="Unset the quests source channel",
)
@app_commands.default_permissions(manage_channels=True)
async def clear_quests_channel(interaction: discord.Interaction) -> None:
    set_quests_channel_id(None)
    await interaction.response.send_message(
        "Cleared quests source channel.", ephemeral=True
    )


@bot.tree.command(
    name="import-quests",
    description="Bootstrap the completed-quests list from a comma/newline separated list",
)
@app_commands.describe(
    names="Quest names separated by commas or new lines",
    replace="If true, replace the existing list. Otherwise merge.",
)
@app_commands.default_permissions(manage_channels=True)
async def import_quests(
    interaction: discord.Interaction,
    names: str,
    replace: bool = False,
) -> None:
    raw_parts = re.split(r"[,\n;]+", names)
    matched, unmatched = canonicalize_quest_names(raw_parts)

    if replace:
        save_completed_quests(matched)
        added_count = len(matched)
        new_total = len(get_completed_quests())
    else:
        existing = get_completed_quests()
        combined = list(existing)
        added = 0
        existing_norms = {_normalize_quest_name(x) for x in existing}
        for m in matched:
            if _normalize_quest_name(m) not in existing_norms:
                combined.append(m)
                existing_norms.add(_normalize_quest_name(m))
                added += 1
        save_completed_quests(combined)
        added_count = added
        new_total = len(get_completed_quests())

    total_qp = compute_quest_points(get_completed_quests())
    lines = [
        f"Imported **{added_count}** new quest(s). "
        f"Completed list now has **{new_total}** entries totaling **{total_qp} QP**.",
    ]
    if unmatched:
        preview = ", ".join(unmatched[:10])
        more = f" (+{len(unmatched) - 10} more)" if len(unmatched) > 10 else ""
        lines.append(f"Unmatched: {preview}{more}")
    await interaction.response.send_message("\n".join(lines), ephemeral=True)


@bot.tree.command(
    name="list-completed-quests",
    description="List quests currently tracked as completed",
)
async def list_completed_quests(interaction: discord.Interaction) -> None:
    completed = get_completed_quests()
    if not completed:
        await interaction.response.send_message(
            "No completed quests recorded yet. Use `/import-quests` to bootstrap.",
            ephemeral=True,
        )
        return
    total_qp = compute_quest_points(completed)
    body = "\n".join(f"- {q}" for q in completed)
    # Discord message body limit is 2000 chars. If we overflow, truncate.
    header = (
        f"**{len(completed)}** completed quest(s), **{total_qp} QP** total:\n"
    )
    if len(header) + len(body) > 1900:
        body = body[: 1900 - len(header)] + "\n... (truncated)"
    await interaction.response.send_message(header + body, ephemeral=True)


async def _resolve_target_todo_channel(
    override: discord.TextChannel | None,
) -> tuple[discord.TextChannel | None, str | None]:
    if override is not None:
        return override, None
    ids = load_channel_ids()
    for cid in ids:
        ch = bot.get_channel(cid)
        if ch is None:
            try:
                ch = await bot.fetch_channel(cid)
            except discord.HTTPException:
                continue
        if isinstance(ch, discord.TextChannel):
            return ch, None
    return None, "No accessible to-do channel is registered. Use `/register-channel` first."


async def _promote_thread(
    thread: discord.Thread,
    quest: dict,
    target: discord.TextChannel,
) -> tuple[discord.Thread | None, str | None]:
    new_name = quest["name"]
    if len(new_name) > 100:
        new_name = new_name[:100]
    archive_note = f"Promoted from #{thread.parent.name}" if thread.parent else "Promoted quest"
    return await recreate_thread_in(
        target,
        thread,
        new_name,
        reason=f"Promoted quest '{quest['name']}' after requirement check",
        archive_note=archive_note,
    )


@bot.tree.command(
    name="promote-quest",
    description="Move this quest thread to the to-do list if her account meets the requirements",
)
@app_commands.describe(
    channel="Optional to-do channel to promote into (defaults to first registered)",
)
async def promote_quest(
    interaction: discord.Interaction,
    channel: discord.TextChannel | None = None,
) -> None:
    thread = interaction.channel
    if not isinstance(thread, discord.Thread):
        await interaction.response.send_message(
            "Run this command inside a quest thread.", ephemeral=True
        )
        return

    quests_channel_id = get_quests_channel_id()
    if quests_channel_id is None:
        await interaction.response.send_message(
            "No quests channel is set. Use `/set-quests-channel` first.",
            ephemeral=True,
        )
        return
    if thread.parent_id != quests_channel_id:
        await interaction.response.send_message(
            "This thread isn't inside the configured quests channel.",
            ephemeral=True,
        )
        return

    rsn = get_osrs_username()
    if not rsn:
        await interaction.response.send_message(
            "No RSN configured. Use `/set-rsn` first.",
            ephemeral=True,
        )
        return

    target, err = await _resolve_target_todo_channel(channel)
    if target is None:
        await interaction.response.send_message(err or "No target channel.", ephemeral=True)
        return

    status, payload = resolve_quest(thread.name)
    if status == "none":
        await interaction.response.send_message(
            f"Couldn't identify a quest from the thread title `{thread.name}`. "
            "Rename the thread to match the quest name and try again.",
            ephemeral=True,
        )
        return
    if status == "ambiguous":
        options = ", ".join(q["name"] for q in payload)  # type: ignore[union-attr]
        await interaction.response.send_message(
            f"Couldn't uniquely identify the quest. Did you mean: {options}? "
            "Rename the thread to match exactly and try again.",
            ephemeral=True,
        )
        return

    quest = payload  # type: ignore[assignment]

    await interaction.response.defer(ephemeral=True, thinking=True)

    hiscores = await fetch_hiscores(rsn)
    if hiscores is None:
        await interaction.followup.send(
            f"Couldn't fetch OSRS hiscores for `{rsn}`. Check the RSN and try again.",
            ephemeral=True,
        )
        return

    report = check_requirements(quest, hiscores, get_completed_quests())
    if not report.ok:
        await interaction.followup.send(
            format_requirement_failure(quest, report, rsn), ephemeral=True
        )
        return

    new_thread, move_err = await _promote_thread(thread, quest, target)
    if new_thread is None:
        await interaction.followup.send(
            move_err or "Failed to promote thread.", ephemeral=True
        )
        return
    note = f"\nNote: {move_err}" if move_err else ""
    await interaction.followup.send(
        f"Promoted **{quest['name']}** to {new_thread.mention}.{note}",
        ephemeral=True,
    )


async def _existing_forum_thread_names(forum: discord.ForumChannel) -> set[str]:
    seen: set[str] = set()
    for t in forum.threads:
        seen.add(_normalize_quest_name(t.name))
    try:
        async for t in forum.archived_threads(limit=None):
            seen.add(_normalize_quest_name(t.name))
    except discord.HTTPException:
        pass
    return seen


@bot.tree.command(
    name="populate-quests-channel",
    description="Create a forum post for every OSRS quest (skips completed and existing)",
)
@app_commands.default_permissions(manage_channels=True)
async def populate_quests_channel(interaction: discord.Interaction) -> None:
    quests_channel_id = get_quests_channel_id()
    if quests_channel_id is None:
        await interaction.response.send_message(
            "No quests channel is set. Use `/set-quests-channel` first.",
            ephemeral=True,
        )
        return
    forum = bot.get_channel(quests_channel_id)
    if forum is None:
        try:
            forum = await bot.fetch_channel(quests_channel_id)
        except discord.HTTPException:
            forum = None
    if not isinstance(forum, discord.ForumChannel):
        await interaction.response.send_message(
            "Quests channel must be a **forum** channel. Reset it with `/set-quests-channel`.",
            ephemeral=True,
        )
        return

    quests = load_quests_data()
    if not quests:
        await interaction.response.send_message(
            "quests_data.json is empty or missing.", ephemeral=True
        )
        return

    await interaction.response.defer(ephemeral=True, thinking=True)

    completed_norm = {_normalize_quest_name(n) for n in get_completed_quests()}
    existing_norm = await _existing_forum_thread_names(forum)

    created = 0
    skipped_completed = 0
    skipped_existing = 0
    failed = 0
    total = len(quests)

    last_progress_edit = 0.0
    for idx, quest in enumerate(quests, start=1):
        norm = _normalize_quest_name(quest.get("name", ""))
        if not norm:
            continue
        if norm in completed_norm:
            skipped_completed += 1
            continue
        if norm in existing_norm:
            skipped_existing += 1
            continue

        body = build_requirements_summary(quest)
        try:
            await forum.create_thread(
                name=quest["name"][:100],
                content=body[:2000],
                reason="Populate quests channel",
            )
            created += 1
            existing_norm.add(norm)
        except discord.HTTPException as e:
            retry_after = getattr(e, "retry_after", None)
            if getattr(e, "status", None) == 429 and retry_after:
                await asyncio.sleep(float(retry_after) + 0.5)
                # Retry once
                try:
                    await forum.create_thread(
                        name=quest["name"][:100],
                        content=body[:2000],
                        reason="Populate quests channel (retry after 429)",
                    )
                    created += 1
                    existing_norm.add(norm)
                except discord.HTTPException:
                    failed += 1
            else:
                failed += 1

        await asyncio.sleep(1.0)

        # Progress ping every ~10 posts, at most every 3s.
        now = time.monotonic()
        if created and created % 10 == 0 and (now - last_progress_edit) > 3.0:
            try:
                await interaction.edit_original_response(
                    content=(
                        f"Populating quests forum: {idx}/{total} processed, "
                        f"{created} created, {skipped_existing + skipped_completed} skipped, {failed} failed..."
                    )
                )
                last_progress_edit = now
            except discord.HTTPException:
                pass

    await interaction.followup.send(
        (
            f"Done. Created **{created}** post(s). "
            f"Skipped **{skipped_existing}** (already present), "
            f"**{skipped_completed}** (already completed). "
            f"Failed: **{failed}**."
        ),
        ephemeral=True,
    )


@bot.tree.command(
    name="promote-all-eligible",
    description="Promote every quest thread whose requirements are met",
)
@app_commands.describe(
    channel="Optional to-do channel to promote into (defaults to first registered)",
)
@app_commands.default_permissions(manage_channels=True)
async def promote_all_eligible(
    interaction: discord.Interaction,
    channel: discord.TextChannel | None = None,
) -> None:
    quests_channel_id = get_quests_channel_id()
    if quests_channel_id is None:
        await interaction.response.send_message(
            "No quests channel is set. Use `/set-quests-channel` first.",
            ephemeral=True,
        )
        return
    forum = bot.get_channel(quests_channel_id)
    if forum is None:
        try:
            forum = await bot.fetch_channel(quests_channel_id)
        except discord.HTTPException:
            forum = None
    if not isinstance(forum, discord.ForumChannel):
        await interaction.response.send_message(
            "Quests channel must be a **forum** channel.", ephemeral=True
        )
        return

    rsn = get_osrs_username()
    if not rsn:
        await interaction.response.send_message(
            "No RSN configured. Use `/set-rsn` first.", ephemeral=True
        )
        return

    target, err = await _resolve_target_todo_channel(channel)
    if target is None:
        await interaction.response.send_message(err or "No target channel.", ephemeral=True)
        return

    await interaction.response.defer(ephemeral=True, thinking=True)

    hiscores = await fetch_hiscores(rsn)
    if hiscores is None:
        await interaction.followup.send(
            f"Couldn't fetch OSRS hiscores for `{rsn}`.", ephemeral=True
        )
        return

    # Only consider active, non-completed threads.
    threads = [t for t in forum.threads if not is_completed(t)]

    completed_snapshot = get_completed_quests()
    eligible: list[tuple[discord.Thread, dict]] = []
    not_ready = 0
    unmatched: list[str] = []

    for t in threads:
        status, payload = resolve_quest(t.name)
        if status == "match":
            quest = payload  # type: ignore[assignment]
            report = check_requirements(quest, hiscores, completed_snapshot)
            if report.ok:
                eligible.append((t, quest))
            else:
                not_ready += 1
        elif status == "ambiguous":
            unmatched.append(t.name)
        else:
            unmatched.append(t.name)

    promoted = 0
    failures: list[str] = []
    for t, quest in eligible:
        new_thread, move_err = await _promote_thread(t, quest, target)
        if new_thread is not None:
            promoted += 1
        else:
            failures.append(f"{quest['name']}: {move_err}")
        await asyncio.sleep(1.0)

    lines = [
        f"Promoted **{promoted}** quest(s) to {target.mention}.",
        f"Not ready: **{not_ready}** (use `/promote-quest` inside a thread to see details).",
    ]
    if unmatched:
        preview = ", ".join(unmatched[:5])
        more = f" (+{len(unmatched) - 5} more)" if len(unmatched) > 5 else ""
        lines.append(f"Unmatched titles: **{len(unmatched)}** \u2014 {preview}{more}")
    if failures:
        lines.append("Failures:")
        for f in failures[:5]:
            lines.append(f"  \u2022 {f}")
        if len(failures) > 5:
            lines.append(f"  ...and {len(failures) - 5} more")

    await interaction.followup.send("\n".join(lines), ephemeral=True)


# ---------------------------------------------------------------------------

def main() -> None:
    if not DISCORD_TOKEN:
        raise SystemExit("Set DISCORD_TOKEN in .env")
    ensure_legacy_migrated()
    if not load_channel_ids():
        print(
            "Note: no to-do channels registered yet. "
            "Use /register-channel or /create-todo-channel after startup, "
            "or set TODO_CHANNEL_ID in .env for legacy migration."
        )
    bot.run(DISCORD_TOKEN)


if __name__ == "__main__":
    main()
