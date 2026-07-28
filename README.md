# Hosting Bot

A standalone Telegram bot that lets any user send a `.zip` and have it hosted
on your VPS — static sites, Node.js apps, or Java apps (Maven/Gradle).
No web login — Telegram user ID is the identity.

## 1. Create the bot

- Talk to **@BotFather** on Telegram → `/newbot` → copy the token.

## 2. VPS setup

```bash
chmod +x install.sh
./install.sh
```

Installs Python, Node.js, PM2, Java (JDK+Maven+Gradle), and creates
`/var/www/hosted` for deployed projects.

## 3. Configure

```bash
cp .env.example .env
nano .env
```

Fill in:
- `BOT_TOKEN` — from BotFather
- `PUBLIC_BASE_URL` — your VPS's IP or domain, e.g. `http://1.2.3.4` (this is
  what gets shown as each project's live URL)
- `ALLOWED_USER_IDS` — optional, comma-separated Telegram user IDs. Leave
  empty to let anyone who finds the bot deploy code on your server (not
  recommended — see Security notes).

To find your own Telegram user ID, message **@userinfobot**.

## 4. Run it

```bash
source venv/bin/activate
pm2 start bot.py --name hosting-bot --interpreter venv/bin/python3
pm2 start webserver.py --name hosting-web --interpreter venv/bin/python3
pm2 save && pm2 startup   # survive reboots
```

`webserver.py` listens on port `8080` by default (`WEB_PORT` in `.env`) and
is what actually serves `/sites/<id>/` and proxies `/app/<id>/`. Put it
behind nginx on port 80/443 if you want a clean URL — see the
`nginx.conf.example` pattern from the web-panel version, same idea, just
point `proxy_pass` at `127.0.0.1:8080`.

## 5. Using the bot

1. `/start` in Telegram.
2. Send a `.zip` (full project) or a single `.html` file:
   - Zip has `package.json` → **Node.js**, run via PM2, reachable at `/app/<id>/`.
   - Zip has `pom.xml` or `build.gradle` → built with Maven/Gradle, run via PM2.
   - Otherwise (zip or a lone `.html`) → **static** site, reachable at `/sites/<id>/`.
3. **Custom subdomain/name:** add a caption to the file with your desired name
   (e.g. `my-shop`) before sending — that becomes the project's URL slug. If
   it's taken, the bot appends a short random suffix automatically. No
   caption → a random ID is used.
4. **Multiple free domains:** if you set `FREE_DOMAINS` in `.env` to more than
   one URL (e.g. two Railway domains pointing at the same service/port), the
   bot asks the user to pick one via colored buttons before deploying. With
   zero or one domain configured, it's used automatically — no prompt.
5. Bot replies with colored inline buttons: **Open (blue) / Redeploy (green)
   / Stop / Delete (red)**.
6. `/projects` lists everything you've deployed.
7. **Public bot:** leave `ALLOWED_USER_IDS` empty in `.env` and *anyone* who
   finds the bot can deploy their own projects.
8. **Admin panel:** set `ADMIN_USER_IDS` in `.env` to your Telegram ID(s).
   Admins get an "🛠 Admin Panel" button on `/start` (or use `/admin`)
   showing every user's deployed projects, types, statuses, and live URLs.

## Security notes — please read

If you make the bot public (`ALLOWED_USER_IDS` empty), anyone who messages it
can **execute arbitrary code** on your VPS via Node.js/Java projects. Keep
`ALLOWED_USER_IDS` restricted unless you specifically want this open, and use
`ADMIN_USER_IDS` to keep an eye on what's been deployed via the admin panel.
For real isolation between users' projects, the next step would be running
each deployed app inside its own Docker container — this version runs
everything directly on the host via PM2, fine for trusted/small-scale use,
not a hard security boundary.
