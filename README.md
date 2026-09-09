# OSRS Todo Discord Bot

A small Discord bot that randomly picks an open to-do thread across one or more registered channels for Old School RuneScape goals. Threads are tagged by difficulty (`[EASY]`, `[MEDIUM]`, `[HARD]`, `[ELITE]`) and can be marked `[COMPLETED]` to hide them from picks.

## Commands

### To-dos

| Command | Description |
|---|---|
| `/pick-todo` | Prompts you to pick a difficulty (Any/Easy/Medium/Hard/Elite), then picks a random open to-do matching it. Includes **Pick Again** and **Change Difficulty** buttons. |
| `/set-status status:<Easy\|Medium\|Hard\|Elite\|Completed>` | Run inside a thread to set its status tag. Any existing tag is replaced. |
| `/complete` | Alias for `/set-status Completed`. |

### Channel registry

| Command | Description |
|---|---|
| `/register-channel [channel]` | Register a channel (defaults to current) as a to-do channel. |
| `/unregister-channel [channel]` | Remove a channel from the registry. |
| `/create-todo-channel name:<str> [category]` | Create a new text channel and auto-register it. |
| `/list-todo-channels` | Show all registered channels and the completed archive channel. |
| `/set-completed-channel channel:<#channel>` | Enable "move on complete": completed threads are deleted from their current channel and recreated in the target channel. |
| `/clear-completed-channel` | Disable move-on-complete; go back to in-place `[COMPLETED]` tagging. |

Registered channel IDs are stored in `channels.json` alongside `bot.py`. The completed-archive channel is stored in `config.json`. Channel management commands require the **Manage Channels** permission by default.

### Quests

Quests get their own workflow because picking a random quest she doesn't meet the requirements for is a bad experience on an ironman. Quest threads live in a dedicated **forum** channel and only get promoted to a regular to-do channel after the bot verifies her account meets every requirement.

| Command | Description |
|---|---|
| `/set-rsn username:<str>` | Store her OSRS RSN. Used for all hiscores lookups. |
| `/set-quests-channel channel:<#forum>` | Register the forum channel that holds one thread per quest. |
| `/clear-quests-channel` | Unset the quests source channel. |
| `/populate-quests-channel` | One-shot: create a forum post for every OSRS quest in `quests_data.json`. Skips ones already present or already in the completed list. Paced to respect Discord rate limits (~1 post/sec). |
| `/import-quests names:<str> [replace]` | Bootstrap the completed-quests list from a comma or newline separated list of quest names. Fuzzy-matches to canonical names and reports anything that didn't match. |
| `/list-completed-quests` | Show tracked completed quests + total QP. |
| `/promote-quest [channel]` | Run inside a quest thread. Bot fetches her hiscores, checks skill/QP/quest prerequisites, and moves the thread to the to-do channel if all pass. Replies with a detailed breakdown if any requirement fails. |
| `/promote-all-eligible [channel]` | Bulk-scan every open quest thread and promote each one she meets the requirements for. One hiscores fetch, then sequential moves. Safe to re-run after leveling up. |
| `/sync-runelite export:<file> [dry_run]` | Reconcile the bot's state with a RuneLite Quest Helper JSON export (upload the file). Safe operations only \u2014 see below. |

**Runelite sync (safe scope)**

`/sync-runelite` takes the JSON export from the Quest Helper plugin (the object with `quests: [{id, name, state}]`) and does the following in one pass:

1. **Adds** any newly finished quest to `completed_quests`. Never removes entries \u2014 if the export says a tracked quest isn't finished, that's surfaced as info only.
2. **Archives stale quest-forum posts.** For any quest the export marks as `FINISHED` that still has an open post in the quests forum, the bot moves it to the configured **completed archive channel** as `[COMPLETED] <quest name>` (same behavior as `/complete`). If no archive channel is configured (`/set-completed-channel`), the post is deleted as a fallback.
3. **Deletes within-channel duplicate threads** for the same canonical quest (keeps the newest).
4. **Deletes cross-channel duplicates** using a priority order: completed archive > any to-do channel > quests forum.

Set `dry_run:true` to preview the plan without applying anything. Names in the export that aren't in `quests_data.json` (miniquests, brand-new quests) are still recorded verbatim in `completed_quests` so quest-prerequisite matching keeps working; they simply won't contribute to computed QP until you add them to the bundled data.

**How quest requirements are checked**

- **Skills:** compared against the OSRS ironman hiscores (falls back to the main hiscores if unranked as an ironman).
- **Quest prerequisites:** compared against the `completed_quests` list in `config.json`.
- **Quest points:** computed as the sum of `qp_reward` across all completed quests known to the bot.

The completed-quests list is bootstrapped once via `/import-quests`. After that, every time a quest thread is marked `[COMPLETED]` (via `/complete` or `/set-status`), the bot auto-appends the canonical quest name — so the list stays accurate without ongoing manual work.

Quest data (names, skill requirements, QP, prerequisites) is bundled in `quests_data.json`. It covers ~147 quests. If a quest is missing or has stale requirements, edit `quests_data.json` directly.

### Completed archive channel (optional)

If you set a completed channel with `/set-completed-channel`, marking a thread `[COMPLETED]` (via `/complete` or `/set-status Completed`) will:

1. Create a new thread in the archive channel with the completed name (e.g. `[COMPLETED] [HARD] Fire cape`).
2. Repost the **starter message only** (with a header noting the original author and source channel).
3. **Delete** the original thread — replies and follow-up messages are lost.

If the completed channel is not set, threads are just renamed in place (original behavior). This also side-steps Discord's thread-rename rate limit of ~2 per 10 minutes.

All commands reply ephemerally (only visible to the person who ran them).

## Setup

### 1. Install Python

You need **Python 3.10 or newer**. Confirm with:

```powershell
python --version
```

### 2. Install dependencies

```powershell
cd C:\Users\getgo\Desktop\Project\osrs-todo-bot
python -m venv venv
.\venv\Scripts\Activate.ps1
pip install -r requirements.txt
```

### 3. Create a Discord application

1. Go to [https://discord.com/developers/applications](https://discord.com/developers/applications)
2. Click **New Application** and give it a name (e.g. "OSRS Todo Bot")
3. Open the **Bot** tab → **Add Bot** → **Reset Token** → copy the token
4. Open **OAuth2 → URL Generator**:
   - Scopes: `bot`, `applications.commands`
   - Bot permissions: `View Channels`, `Send Messages`, `Read Message History`, `Manage Threads`
5. Copy the generated URL, open it in your browser, and invite the bot to your server

### 4. Configure environment variables

```powershell
copy .env.example .env
```

Edit `.env`:

```env
DISCORD_TOKEN=your_bot_token_here
TODO_CHANNEL_ID=
GUILD_ID=123456789012345678
```

- **DISCORD_TOKEN** — bot token from the Developer Portal
- **TODO_CHANNEL_ID** *(optional/legacy)* — if set, this channel is auto-migrated into `channels.json` on startup. Leave blank and use `/register-channel` or `/create-todo-channel` to manage channels.
- **GUILD_ID** — right-click your server name → **Copy Server ID** (recommended; syncs slash commands instantly)

### 5. Run the bot

**Windows (easiest):** double-click `start-bot.bat`

**Or manually:**

```powershell
python bot.py
```

You should see:

```
Watching to-do channels: #your-channel-name (123456789...), ...
Logged in as OSRS Todo Bot#1234
```

If no channels are registered yet, use `/register-channel` (in the channel you want) or `/create-todo-channel name:my-todos`.

Leave the terminal open while the bot is running.

## Deploy to Railway (24/7 hosting)

Railway keeps the bot online without leaving your PC running. This bot is lightweight — on the **Hobby plan ($5/month)**, it typically stays within the included usage credit.

### 1. Push to GitHub

Create a repo and push this folder. Do **not** commit `.env` — it is already in `.gitignore`.

### 2. Create a Railway project

1. Go to [https://railway.com](https://railway.com) and sign in
2. **New Project → Deploy from GitHub repo**
3. Select your `osrs-todo-bot` repository

Railway auto-detects Python from `requirements.txt`. The included `Procfile` tells it to run `python bot.py`.

### 3. Add environment variables

In your Railway service, open **Variables** and add:

| Variable | Value |
|---|---|
| `DISCORD_TOKEN` | Your bot token from the Discord Developer Portal |
| `GUILD_ID` | Your Discord server ID |
| `TODO_CHANNEL_IDS` | **(recommended on Railway)** Comma-separated list of to-do channel IDs. When set, this overrides `channels.json` and survives redeploys. Example: `123456789,987654321` |
| `COMPLETED_CHANNEL_ID` | **(recommended on Railway)** Single channel ID (text or forum) where completed threads are moved. When set, overrides `config.json`. |
| `TODO_CHANNEL_ID` | *(legacy)* Single channel; auto-migrated into `channels.json` on startup. Prefer `TODO_CHANNEL_IDS`. |
| `QUESTS_CHANNEL_ID` | Forum channel ID that holds quest threads. Overrides `config.json`. |
| `OSRS_USERNAME` | Her OSRS RSN, used for hiscores lookups. Overrides `config.json`. |

**Important on Railway:** `channels.json` and `config.json` are stored on the container filesystem and are wiped on every redeploy. Use `TODO_CHANNEL_IDS` and `COMPLETED_CHANNEL_ID` env vars for persistence, or mount a Railway Volume at the repo path.

These are the same values from your local `.env` file.

### 4. Configure the service

- **Disable serverless / sleep** — Discord bots need a persistent connection
- Set memory to **256 MB** if you want to keep costs low (Settings → Resources)
- Redeploy if you change variables

### 5. Verify deployment

Open **Deployments → View Logs**. You should see:

```
Watching to-do channel: #your-channel-name (123456789...)
Logged in as OSRS Todo Bot#1234
```

Test `/pick-todo` in Discord. If slash commands don't appear, confirm `GUILD_ID` is set and redeploy.

### Railway cost notes

| Plan | What to expect |
|---|---|
| **Free** ($1/month credit) | Not enough for 24/7 — good for testing only |
| **Trial** ($5 one-time credit) | Fine for trying Railway for ~a month |
| **Hobby** ($5/month) | Best fit — this bot usually uses ~$1–3 of the included $5 credit |

For zero cost, keep running `start-bot.bat` locally instead.

## How it works

- Every `/pick-todo` fetches **live** active threads across all registered channels — new threads appear immediately, no restart needed
- Status is stored as a bracket prefix on the thread name, e.g. `[EASY] Barrows grind`. Recognized tags: `[EASY]`, `[MEDIUM]`, `[HARD]`, `[ELITE]`, `[COMPLETED]` (case-insensitive)
- `[COMPLETED]` threads are excluded from picks; the other tags act as difficulty filters
- `/set-status` strips any existing tag before applying the new one, so switching difficulty is safe
- The **Pick Again** button re-rolls with the same difficulty; **Change Difficulty** re-opens the picker
- Ephemeral views last up to 5 minutes (Discord limit)

## Troubleshooting

**Slash commands don't appear**
- Make sure `GUILD_ID` is set in `.env` and matches your server
- Restart the bot after changing `.env`
- Confirm the bot was invited with the `applications.commands` scope

**"No to-do channels registered"**
- Run `/register-channel` from inside the channel you want to use, or `/create-todo-channel name:<name>` to make a new one
- Confirm the bot can see the channel (channel permissions for the bot role)
- `channels.json` lives next to `bot.py`; delete it to reset the registry

**`/set-status` or `/complete` doesn't rename the thread**
- Run the command **inside** the thread, not in the parent channel
- Confirm the bot has **Manage Threads** permission
- If the title would exceed 100 characters after tagging, shorten it first

**Bot stops responding**
- The terminal must stay open — closing it stops the bot
- Check for errors in the terminal output
