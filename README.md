# OSRS Todo Discord Bot

A small Discord bot that randomly picks an open to-do thread from your threads channel for Old School RuneScape goals. Threads marked with `[COMPLETED]` in the title are skipped.

## Commands

| Command | Description |
|---|---|
| `/pick-todo` | Pick a random open to-do. Shows title, link, remaining count, created date, and a **Pick Again** button. |
| `/complete` | Run inside a thread to prefix its title with `[COMPLETED]`. |

Both commands reply ephemerally (only visible to the person who ran them).

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
TODO_CHANNEL_ID=123456789012345678
GUILD_ID=123456789012345678
```

- **DISCORD_TOKEN** — bot token from the Developer Portal
- **TODO_CHANNEL_ID** — right-click your threads channel in Discord → **Copy Channel ID** (enable Developer Mode in Discord → User Settings → Advanced if needed)
- **GUILD_ID** — right-click your server name → **Copy Server ID** (recommended; syncs slash commands instantly)

### 5. Run the bot

**Windows (easiest):** double-click `start-bot.bat`

**Or manually:**

```powershell
python bot.py
```

You should see:

```
Watching to-do channel: #your-channel-name (123456789...)
Logged in as OSRS Todo Bot#1234
```

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
| `TODO_CHANNEL_ID` | Your threads channel ID |
| `GUILD_ID` | Your Discord server ID |

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

- Every `/pick-todo` fetches **live** active threads from Discord — new threads appear immediately, no restart needed
- Any thread whose title contains `[COMPLETED]` (case-insensitive) is excluded
- Renaming a thread to add `[COMPLETED]` after creation works — the bot checks the current title each time
- The **Pick Again** button re-rolls for up to 5 minutes (Discord ephemeral message limit)

## Troubleshooting

**Slash commands don't appear**
- Make sure `GUILD_ID` is set in `.env` and matches your server
- Restart the bot after changing `.env`
- Confirm the bot was invited with the `applications.commands` scope

**"To-do channel not found"**
- Double-check `TODO_CHANNEL_ID` in `.env`
- Confirm the bot can see that channel (channel permissions for the bot role)

**`/complete` doesn't rename the thread**
- Run the command **inside** the thread, not in the parent channel
- Confirm the bot has **Manage Threads** permission

**Bot stops responding**
- The terminal must stay open — closing it stops the bot
- Check for errors in the terminal output
