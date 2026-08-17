import os
import random

import discord
from discord.ext import commands
from dotenv import load_dotenv

load_dotenv()

DISCORD_TOKEN = os.getenv("DISCORD_TOKEN")
TODO_CHANNEL_ID = int(os.getenv("TODO_CHANNEL_ID", "0"))
GUILD_ID = os.getenv("GUILD_ID")
COMPLETED_TAG = "[COMPLETED]"


def is_completed(thread: discord.Thread) -> bool:
    return COMPLETED_TAG.upper() in thread.name.upper()


async def get_channel(bot: commands.Bot) -> discord.abc.GuildChannel | None:
    channel = bot.get_channel(TODO_CHANNEL_ID)
    if channel is not None:
        return channel
    try:
        fetched = await bot.fetch_channel(TODO_CHANNEL_ID)
    except discord.HTTPException:
        return None
    if isinstance(fetched, discord.abc.GuildChannel):
        return fetched
    return None


async def get_open_threads(
    channel: discord.abc.GuildChannel,
) -> tuple[list[discord.Thread], int]:
    """Return open threads and total active thread count for a channel."""
    threads = list(channel.threads)

    all_active = list({thread.id: thread for thread in threads}.values())
    open_threads = [thread for thread in all_active if not is_completed(thread)]

    return open_threads, len(all_active)


def build_pick_embed(
    thread: discord.Thread,
    open_count: int,
    total_count: int,
) -> discord.Embed:
    embed = discord.Embed(
        title=thread.name,
        url=thread.jump_url,
        description=(
            f"**Remaining:** There is currently {open_count} to-do list items "
            f"left to complete out of {total_count} to-dos that have been added in total."
        ),
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

    embed.set_footer(text="Your OSRS To-Do")
    return embed


def build_all_done_embed() -> discord.Embed:
    return discord.Embed(
        title="All to-dos complete!",
        description="Nothing left to pick. Time to celebrate or add more goals!",
        color=0x57F287,
    )


class PickAgainView(discord.ui.View):
    def __init__(self, bot: commands.Bot):
        super().__init__(timeout=300)
        self.bot = bot

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
        channel = await get_channel(self.bot)
        if channel is None:
            await interaction.response.edit_message(
                content="To-do channel not found. Check TODO_CHANNEL_ID in .env.",
                embed=None,
                view=None,
            )
            return

        open_threads, total = await get_open_threads(channel)
        if not open_threads:
            await interaction.response.edit_message(
                content=None,
                embed=build_all_done_embed(),
                view=None,
            )
            return

        thread = random.choice(open_threads)
        embed = build_pick_embed(thread, len(open_threads), total)
        await interaction.response.edit_message(embed=embed, view=self)


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
    channel = await get_channel(bot)
    if channel is None:
        print(f"Warning: TODO_CHANNEL_ID {TODO_CHANNEL_ID} not found or inaccessible.")
    else:
        print(f"Watching to-do channel: #{channel.name} ({channel.id})")
    print(f"Logged in as {bot.user}")


@bot.tree.command(
    name="pick-todo",
    description="Pick a random open OSRS to-do thread",
)
async def pick_todo(interaction: discord.Interaction) -> None:
    channel = await get_channel(bot)
    if channel is None:
        await interaction.response.send_message(
            "To-do channel not configured or not found. Check TODO_CHANNEL_ID in .env.",
            ephemeral=True,
        )
        return

    open_threads, total = await get_open_threads(channel)
    if not open_threads:
        await interaction.response.send_message(
            embed=build_all_done_embed(),
            ephemeral=True,
        )
        return

    thread = random.choice(open_threads)
    embed = build_pick_embed(thread, len(open_threads), total)
    view = PickAgainView(bot)
    await interaction.response.send_message(
        embed=embed,
        view=view,
        ephemeral=True,
    )


@bot.tree.command(
    name="complete",
    description="Mark this to-do thread as completed",
)
async def complete(interaction: discord.Interaction) -> None:
    if not isinstance(interaction.channel, discord.Thread):
        await interaction.response.send_message(
            "Run this command inside a to-do thread.",
            ephemeral=True,
        )
        return

    thread = interaction.channel
    if is_completed(thread):
        await interaction.response.send_message(
            "Already marked complete.",
            ephemeral=True,
        )
        return

    new_name = f"{COMPLETED_TAG} {thread.name}"
    if len(new_name) > 100:
        await interaction.response.send_message(
            "Cannot rename: title would exceed Discord's 100 character limit.",
            ephemeral=True,
        )
        return

    await thread.edit(name=new_name)
    await interaction.response.send_message(
        f"Marked complete: **{new_name}**",
        ephemeral=True,
    )


def main() -> None:
    if not DISCORD_TOKEN:
        raise SystemExit("Set DISCORD_TOKEN in .env")
    if not TODO_CHANNEL_ID:
        raise SystemExit("Set TODO_CHANNEL_ID in .env")
    bot.run(DISCORD_TOKEN)


if __name__ == "__main__":
    main()
