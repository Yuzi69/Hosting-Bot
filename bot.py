import logging
import os
import tempfile
from pathlib import Path

from dotenv import load_dotenv
from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import (
    Application,
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
)

import store
from deploy import DeployError, deploy_zip, deploy_single_html, delete_project, stop_project, sanitize_slug

load_dotenv()
logging.basicConfig(level=logging.INFO)
log = logging.getLogger("hosting-bot")

BOT_TOKEN = os.getenv("BOT_TOKEN")
PUBLIC_BASE_URL = os.getenv("PUBLIC_BASE_URL", "").rstrip("/")
FREE_DOMAINS = [d.strip().rstrip("/") for d in os.getenv("FREE_DOMAINS", "").split(",") if d.strip()] or (
    [PUBLIC_BASE_URL] if PUBLIC_BASE_URL else []
)

# Empty ALLOWED_USER_IDS = bot is public, anyone can deploy.
ALLOWED_USER_IDS = {
    int(x) for x in os.getenv("ALLOWED_USER_IDS", "").split(",") if x.strip().isdigit()
}
ADMIN_USER_IDS = {
    int(x) for x in os.getenv("ADMIN_USER_IDS", "").split(",") if x.strip().isdigit()
}

PENDING = {}  # user_id -> {"file_path": str, "filename": str, "slug": str|None, "kind": "zip"|"html"}


def is_allowed(user_id: int) -> bool:
    return not ALLOWED_USER_IDS or user_id in ALLOWED_USER_IDS


def is_admin(user_id: int) -> bool:
    return user_id in ADMIN_USER_IDS


def project_url(project: dict) -> str:
    base = project.get("domain") or PUBLIC_BASE_URL
    if project["type"] == "static":
        return f"{base}/sites/{project['id']}/"
    return f"{base}/app/{project['id']}/"


def project_keyboard(project: dict) -> InlineKeyboardMarkup:
    rows = [
        [InlineKeyboardButton("🌐 Open", url=project_url(project), style="primary")],
        [
            InlineKeyboardButton("🔁 Redeploy", callback_data=f"redeploy_{project['id']}", style="success"),
            InlineKeyboardButton("⏹ Stop", callback_data=f"stop_{project['id']}", style="danger"),
        ],
        [InlineKeyboardButton("🗑 Delete", callback_data=f"delete_{project['id']}", style="danger")],
    ]
    return InlineKeyboardMarkup(rows)


def domain_choice_keyboard() -> InlineKeyboardMarkup:
    styles = ("primary", "success", "danger")
    rows = [
        [InlineKeyboardButton(d.replace("https://", "").replace("http://", ""),
                               callback_data=f"domain_{i}", style=styles[i % 3])]
        for i, d in enumerate(FREE_DOMAINS)
    ]
    return InlineKeyboardMarkup(rows)


def project_summary(p: dict) -> str:
    return (
        f"<b>{p['name']}</b>\n"
        f"Type: {p['type']}\n"
        f"Status: {p['status']}\n"
        f"URL: {project_url(p)}"
    )


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_allowed(update.effective_user.id):
        await update.message.reply_text("Sorry, you're not allowed to use this bot.")
        return

    text = (
        "Zip file (full project) ba ekta .html file pathao — automatically host hoye jabe.\n"
        "Static (HTML/CSS/JS), Node.js (package.json), r Java (Maven/Gradle) — shob support kore.\n\n"
        "Custom subdomain naam dite chao? File pathanor age <b>caption</b> e naam likho (e.g. \"my-shop\").\n\n"
        "/projects — tomar shob deployed project dekhte."
    )
    rows = []
    if is_admin(update.effective_user.id):
        rows.append([InlineKeyboardButton("🛠 Admin Panel", callback_data="admin_panel", style="primary")])
    await update.message.reply_text(
        text, parse_mode="HTML", reply_markup=InlineKeyboardMarkup(rows) if rows else None
    )


async def list_projects(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_allowed(update.effective_user.id):
        return
    projects = store.for_owner(update.effective_user.id)
    if not projects:
        await update.message.reply_text("Kono project deploy kora nai. Ekta .zip ba .html pathao.")
        return
    for p in projects:
        await update.message.reply_text(
            project_summary(p), parse_mode="HTML", reply_markup=project_keyboard(p)
        )


async def admin_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id):
        await update.message.reply_text("Admin only.")
        return
    await send_admin_panel(update.message)


async def send_admin_panel(message):
    projects = store.all_projects()
    if not projects:
        await message.reply_text("Ekhono kono user kichu deploy kore nai.")
        return

    by_owner = {}
    for p in projects:
        by_owner.setdefault(p["owner_id"], []).append(p)

    lines = [f"<b>Admin Panel</b> - {len(by_owner)} user, {len(projects)} project\n"]
    for owner_id, plist in by_owner.items():
        lines.append(f"User {owner_id}")
        for p in plist:
            lines.append(f"  - {p['name']} - {p['type']} - {p['status']} - {project_url(p)}")
        lines.append("")
    text = "\n".join(lines)

    for i in range(0, len(text), 3800):
        await message.reply_text(text[i:i + 3800], parse_mode="HTML", disable_web_page_preview=True)


async def handle_file(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    if not is_allowed(user.id):
        await update.message.reply_text("Sorry, you're not allowed to use this bot.")
        return

    doc = update.message.document
    name = (doc.file_name or "").lower()
    if name.endswith(".zip"):
        kind = "zip"
    elif name.endswith(".html") or name.endswith(".htm"):
        kind = "html"
    else:
        await update.message.reply_text("Ekta .zip (full project) ba .html file pathao.")
        return

    slug = sanitize_slug(update.message.caption)

    tmp_dir = tempfile.mkdtemp()
    file_path = Path(tmp_dir) / doc.file_name
    tg_file = await doc.get_file()
    await tg_file.download_to_drive(str(file_path))

    project_name = doc.file_name.rsplit(".", 1)[0]
    redeploy_target = context.user_data.pop("redeploy_target", None)

    pending = {
        "file_path": str(file_path),
        "project_name": project_name,
        "kind": kind,
        "slug": slug,
        "existing_id": redeploy_target,
    }

    if len(FREE_DOMAINS) > 1 and not redeploy_target:
        PENDING[user.id] = pending
        await update.message.reply_text(
            "Kon domain e host korte chao?", reply_markup=domain_choice_keyboard()
        )
        return

    domain = FREE_DOMAINS[0] if FREE_DOMAINS else None
    await run_deploy(update.message, user.id, pending, domain)


async def run_deploy(message, owner_id: int, pending: dict, domain):
    status_msg = await message.reply_text("Deploy hocche, wait koro...")
    try:
        if pending["kind"] == "zip":
            project = deploy_zip(
                pending["file_path"], pending["project_name"], owner_id,
                slug=pending["slug"], domain=domain, existing_id=pending["existing_id"],
            )
        else:
            project = deploy_single_html(
                pending["file_path"], pending["project_name"], owner_id,
                slug=pending["slug"], domain=domain, existing_id=pending["existing_id"],
            )
    except DeployError as e:
        await status_msg.edit_text(f"Deploy failed:\n{str(e)[:500]}")
        return
    except Exception as e:
        log.exception("deploy crashed")
        await status_msg.edit_text(f"Unexpected error: {e}")
        return
    finally:
        try:
            os.remove(pending["file_path"])
        except OSError:
            pass

    await status_msg.edit_text(
        f"Deployed!\n\n{project_summary(project)}",
        parse_mode="HTML",
        reply_markup=project_keyboard(project),
    )


async def button_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    user_id = update.effective_user.id

    if query.data == "admin_panel":
        if not is_admin(user_id):
            return
        await send_admin_panel(query.message)
        return

    if query.data.startswith("domain_"):
        idx = int(query.data.split("_", 1)[1])
        pending = PENDING.pop(user_id, None)
        if not pending:
            await query.edit_message_text("Session expired, abar file pathao.")
            return
        domain = FREE_DOMAINS[idx] if idx < len(FREE_DOMAINS) else FREE_DOMAINS[0]
        await query.edit_message_text(f"Deploying to {domain} ...")
        await run_deploy(query.message, user_id, pending, domain)
        return

    action, _, project_id = query.data.partition("_")
    project = store.get(project_id)
    if not project or (project["owner_id"] != user_id and not is_admin(user_id)):
        await query.edit_message_text("Project not found (or it's not yours).")
        return

    if action == "stop":
        stop_project(project_id)
        project = store.get(project_id)
        await query.edit_message_text(
            project_summary(project), parse_mode="HTML", reply_markup=project_keyboard(project)
        )
    elif action == "delete":
        delete_project(project_id)
        await query.edit_message_text(f"Deleted \"{project['name']}\".")
    elif action == "redeploy":
        context.user_data["redeploy_target"] = project_id
        await query.message.reply_text(
            f"Notun .zip ba .html pathao \"{project['name']}\" redeploy korte."
        )


def main():
    if not BOT_TOKEN:
        raise SystemExit("BOT_TOKEN missing in .env")

    app = Application.builder().token(BOT_TOKEN).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("projects", list_projects))
    app.add_handler(CommandHandler("admin", admin_command))
    app.add_handler(MessageHandler(filters.Document.ALL, handle_file))
    app.add_handler(CallbackQueryHandler(button_callback))

    log.info("Hosting bot starting...")
    app.run_polling()


if __name__ == "__main__":
    main()
