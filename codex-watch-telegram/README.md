# Codex Watch → Telegram

A free monitor focused on **OpenAI Codex usage resets, extra/free usage, limits, credits and quota changes**.

It runs in GitHub Actions and sends alerts to Telegram. No ChatGPT/Codex credits are used.

## Sources

- OpenAI Codex changelog
- OpenAI release notes
- OpenAI Status Atom feed
- `openai/codex` GitHub issues
- comments by GitHub users marked MEMBER / COLLABORATOR / OWNER in `openai/codex`
- `r/codex` search RSS
- Google News RSS searches for Codex resets/usage and Thibault Sottiaux / `thsottiaux`
- `openai/codex` release feed (usage-related items only)

This deliberately prioritizes usage/reset information rather than general Codex news.

## 1. Create your Telegram bot

1. Open Telegram.
2. Search for **@BotFather**.
3. Send `/newbot`.
4. Follow the instructions.
5. BotFather gives you a token. Keep it private.

## 2. Get your Telegram chat ID

### Easiest: private chat with the bot

1. Open the new bot and press **Start**.
2. Send it any message, e.g. `hello`.
3. In a browser, open:

   `https://api.telegram.org/botYOUR_BOT_TOKEN/getUpdates`

4. Find:

   `"chat":{"id":123456789,...}`

   The number is your `TELEGRAM_CHAT_ID`.

### Private channel instead

1. Create a private Telegram channel, e.g. **Codex Watch**.
2. Add your bot as an administrator with permission to post.
3. Forward a channel message to a bot/tool that shows raw Telegram updates, or use the Bot API `getUpdates` after posting.
4. Channel IDs normally look like `-100...`.

If you want the simplest setup, start with a private chat. You can switch to a channel later.

## 3. Create a PUBLIC GitHub repository

A public repository avoids GitHub Actions minute charges on standard GitHub-hosted runners.

Upload all files from this package, keeping this structure:

```text
.github/
  workflows/
    codex-watch.yml
monitor.py
requirements.txt
state.json
README.md
```

Do **not** put your Telegram bot token in any file.

## 4. Add GitHub secrets

In your repository:

**Settings → Secrets and variables → Actions → New repository secret**

Add:

- `TELEGRAM_BOT_TOKEN` = token from BotFather
- `TELEGRAM_CHAT_ID` = your chat/channel ID

The workflow automatically uses GitHub's built-in `GITHUB_TOKEN` for GitHub API requests.

## 5. Enable Actions

Open the repository's **Actions** tab and enable workflows if GitHub asks.

Then choose **Codex Watch → Run workflow** once.

The first run creates a baseline and sends:

> ✅ CODEX WATCH is active

Old posts are not dumped into Telegram.

After that, the schedule checks roughly every 15 minutes.

## What triggers an alert?

Strong phrases such as:

- usage reset
- limits reset
- reset usage
- free reset
- extra usage
- bonus usage
- free usage
- increased limits
- usage limit
- weekly limit
- rate limit
- credits
- quota

The monitor also scans recent matching `openai/codex` issues for new comments from GitHub users whose repository association is MEMBER, COLLABORATOR or OWNER. This is useful when OpenAI developers explain a reset or limit change inside GitHub rather than publishing a formal announcement.

## Important limitations

- This is free secondary-source monitoring, so it **cannot guarantee** catching every developer post on X/Twitter immediately.
- Google News/Reddit/GitHub often relay important developer announcements, but there may be delays.
- GitHub scheduled workflows are not real-time cron jobs and can start late during high load.
- A public repository makes the code public, but GitHub repository secrets remain separate from the code. Never print the token in logs.
- If a source changes its HTML/feed format, that source may need a small code update.

## Test Telegram manually

Replace values and open/call:

```text
https://api.telegram.org/botBOT_TOKEN/sendMessage?chat_id=CHAT_ID&text=Codex%20Watch%20test
```

## Change frequency

Edit `.github/workflows/codex-watch.yml`.

Current:

```yaml
- cron: "*/15 * * * *"
```

Examples:

```yaml
# Every 30 minutes
- cron: "*/30 * * * *"

# Every hour
- cron: "7 * * * *"
```

## Security

If you ever expose the bot token, revoke it in **@BotFather** and generate a new one.
