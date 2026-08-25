import json
import os
import random
import re
from pathlib import Path

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

    starter_content, starter_author = await get_starter_content(thread)

    header_lines: list[str] = []
    if starter_author is not None:
        header_lines.append(f"Originally posted by {starter_author.mention}")
    header_lines.append(f"Archived from #{thread.parent.name}" if thread.parent else "Archived thread")
    header = "\n".join(header_lines)

    body_parts = [header]
    if starter_content:
        body_parts.append("")
        body_parts.append(starter_content)
    body = "\n".join(body_parts)

    if len(body) > 2000:
        body = body[:1997] + "..."

    new_thread: discord.Thread
    try:
        if isinstance(target, discord.ForumChannel):
            result = await target.create_thread(
                name=new_name,
                content=body or new_name,
                reason="Moved from active to-do channel on completion",
            )
            new_thread = result.thread
        else:
            new_thread = await target.create_thread(
                name=new_name,
                type=discord.ChannelType.public_thread,
                reason="Moved from active to-do channel on completion",
            )
            try:
                await new_thread.send(body)
            except discord.HTTPException:
                pass
    except discord.Forbidden:
        return None, "I don't have permission to create threads/posts in the completed channel."
    except discord.HTTPException as e:
        return None, f"Failed to create thread in completed channel: {e}"

    try:
        await thread.delete()
    except discord.HTTPException as e:
        return new_thread, (
            f"Created new thread {new_thread.mention}, but failed to delete original: {e}"
        )

    return new_thread, None


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
            msg = f"Marked complete and moved to {new_thread.mention}."
            if err:
                msg += f"\nNote: {err}"
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
    await interaction.followup.send(
        f"Status set to **{status.name}**: {new_name}",
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
            msg = f"Marked complete and moved to {new_thread.mention}."
            if err:
                msg += f"\nNote: {err}"
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
    await interaction.followup.send(
        f"Marked complete: **{new_name}**",
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

    source_notes: list[str] = []
    if channels_env_override():
        source_notes.append("Channel list from `TODO_CHANNEL_IDS` env var")
    else:
        source_notes.append("Channel list from `channels.json`")
    if completed_channel_env_override():
        source_notes.append("Completed channel from `COMPLETED_CHANNEL_ID` env var")
    elif get_completed_channel_id() is not None:
        source_notes.append("Completed channel from `config.json`")

    embed = discord.Embed(
        title="Registered to-do channels",
        description="\n".join(lines),
        color=0x5865F2,
    )
    embed.add_field(name="Completed archive channel", value=completed_line, inline=False)
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
