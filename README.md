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
| `/list-todo-channels` | Show all registered channels. |

Registered channel IDs are stored in `channels.json` alongside `bot.py`. Channel management commands require the **Manage Channels** permission by default.

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
| `TODO_CHANNEL_ID` | *(optional/legacy)* Auto-migrated into `channels.json` on startup |
| `GUILD_ID` | Your Discord server ID |

Note: `channels.json` is stored on the container filesystem. On Railway, this file is ephemeral between deployments unless you attach a volume. If you rely on the registry, either mount a volume at the repo path or re-run `/register-channel` after each deploy.

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
