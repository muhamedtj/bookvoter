"""Tracked BookVoter bug-report flow and lightweight report queue."""

import os
import aiosqlite
from aiogram.types import ForceReply
import bot_core as core
from runtime_ux import label, schedule_message_delete, cleanup_command_message, send_ephemeral_reply

pending_error_reports = {}
_installed = False

async def notify_superadmin_error(error_title: str, error_traceback: str, user_id=None, chat_id=None, context_info: str = ""):
    report=(f"🚨 <b>Critical Error Report</b>\n\n📌 <b>Title:</b> {core.escape_html(error_title)}\n👤 <b>User ID:</b> {user_id or 'N/A'}\n💬 <b>Chat ID:</b> {chat_id or 'N/A'}\n⏰ <b>Timestamp:</b> {core.datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n📝 <b>Context:</b> {core.escape_html(context_info or 'N/A')}\n\n📋 <b>Traceback:</b>\n<pre>{core.escape_html(error_traceback[-1500:])}</pre>")
    delivered=False
    for admin_id in core.SUPER_ADMIN_IDS:
        try: await core.bot.send_message(admin_id,report,parse_mode="HTML"); delivered=True
        except Exception as exc: core.logger.error(f"Failed to send error report to superadmin {admin_id}: {exc}")
    if not delivered and error_title.startswith("User Error Report") and chat_id is not None and chat_id < 0:
        try: administrators=await core.bot.get_chat_administrators(chat_id)
        except Exception as exc: administrators=[]; core.logger.error(f"Failed to get group admins for legacy report fallback: {exc}")
        for member in administrators:
            user=getattr(member,"user",None); admin_id=getattr(user,"id",None)
            if not admin_id or getattr(user,"is_bot",False): continue
            try: await core.bot.send_message(admin_id,report,parse_mode="HTML"); delivered=True; break
            except Exception as exc: core.logger.warning(f"Could not DM group admin {admin_id} with legacy report: {exc}")
    if not delivered: core.logger.error(f"[Error report not delivered by DM] {report}")
    return delivered

async def _ensure_error_reports_table():
    async with core.database.open_db(core.DATABASE_PATH) as db:
        await db.execute("""CREATE TABLE IF NOT EXISTS error_reports (id INTEGER PRIMARY KEY AUTOINCREMENT,user_tg_id INTEGER NOT NULL,username TEXT,user_name TEXT,chat_id INTEGER,chat_title TEXT,source_message_id INTEGER,source_screen TEXT,description TEXT NOT NULL,attachment_type TEXT,attachment_file_id TEXT,status TEXT NOT NULL DEFAULT 'open',created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP)""")
        async with db.execute("PRAGMA table_info(error_reports)") as cursor: columns={row[1] for row in await cursor.fetchall()}
        if "updated_at" not in columns:
            await db.execute("ALTER TABLE error_reports ADD COLUMN updated_at TIMESTAMP"); await db.execute("UPDATE error_reports SET updated_at = COALESCE(updated_at, created_at, CURRENT_TIMESTAMP)")
        await db.commit()

async def _save_error_report(message,pending,description,attachment_type,attachment_file_id):
    await _ensure_error_reports_table(); user=message.from_user
    async with core.database.open_db(core.DATABASE_PATH) as db:
        cursor=await db.execute("""INSERT INTO error_reports (user_tg_id,username,user_name,chat_id,chat_title,source_message_id,source_screen,description,attachment_type,attachment_file_id,status,updated_at) VALUES (?,?,?,?,?,?,?,?,?,?,'open',CURRENT_TIMESTAMP)""",(user.id,user.username,user.full_name,pending.get("origin_chat_id"),pending.get("origin_chat_title"),pending.get("source_message_id"),pending.get("source_screen"),description,attachment_type,attachment_file_id)); await db.commit(); return cursor.lastrowid

async def _manual_report_recipients(chat_id):
    if core.SUPER_ADMIN_IDS: return list(dict.fromkeys(core.SUPER_ADMIN_IDS))
    recipients=[]
    if chat_id is not None and chat_id < 0:
        try:
            for member in await core.bot.get_chat_administrators(chat_id):
                user=getattr(member,"user",None); admin_id=getattr(user,"id",None)
                if admin_id and not getattr(user,"is_bot",False): recipients.append(admin_id)
        except Exception as exc: core.logger.warning(f"Could not resolve group admins for error report fallback: {exc}")
    return list(dict.fromkeys(recipients))

async def _send_tracked_error_report(report_id,message,pending,description,attachment_type):
    user=message.from_user; username=f"@{user.username}" if user.username else "—"; chat_id=pending.get("origin_chat_id"); chat_title=pending.get("origin_chat_title") or "Private chat"; source_screen=(pending.get("source_screen") or "—")[:1000]
    build_sha=os.getenv("RAILWAY_GIT_COMMIT_SHA") or os.getenv("GIT_COMMIT_SHA") or "unknown"; build_sha=build_sha[:12] if build_sha!="unknown" else build_sha; deployment_id=os.getenv("RAILWAY_DEPLOYMENT_ID","unknown"); service_name=os.getenv("RAILWAY_SERVICE_NAME","BookVoter")
    report=(f"🐛 <b>BookVoter Error Report #{report_id}</b>\n\n📍 <b>Status:</b> OPEN\n👤 <b>Reporter:</b> {core.escape_html(user.full_name or 'User')} ({core.escape_html(username)})\n🆔 <b>User ID:</b> {user.id}\n💬 <b>Club:</b> {core.escape_html(chat_title)}\n🔢 <b>Chat ID:</b> {chat_id or 'N/A'}\n✉️ <b>Source message:</b> {pending.get('source_message_id') or 'N/A'}\n📎 <b>Attachment:</b> {core.escape_html(attachment_type)}\n\n📝 <b>User description:</b>\n{core.escape_html(description[:1500])}\n\n🖥 <b>Source screen:</b>\n<pre>{core.escape_html(source_screen)}</pre>\n\n🚀 <b>Build:</b> {core.escape_html(service_name)} · {core.escape_html(build_sha)}\n🧩 <b>Deployment:</b> {core.escape_html(deployment_id)}\n⏰ <b>Received:</b> {core.datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    delivered=[]
    for admin_id in await _manual_report_recipients(chat_id):
        try: await core.bot.send_message(admin_id,report,parse_mode="HTML"); delivered.append(admin_id)
        except Exception as exc: core.logger.warning(f"Failed to deliver tracked report #{report_id} to {admin_id}: {exc}")
    if attachment_type!="none":
        for admin_id in delivered:
            try: await core.bot.copy_message(chat_id=admin_id,from_chat_id=message.chat.id,message_id=message.message_id)
            except Exception as exc: core.logger.warning(f"Failed to copy attachment for report #{report_id} to {admin_id}: {exc}")
    if not delivered: core.logger.error(f"Tracked report #{report_id} was saved but could not be delivered by DM.\n{report}")

async def _send_report_prompt_to_dm(user_id,pending,lang):
    prompt_text=label(lang,en="🐛 <b>Describe what went wrong</b>\n\nReply directly to this message with one message. You may attach a screenshot, file, video or voice message. I already saved the club and the BookVoter screen from which you opened the report.",ru="🐛 <b>Опишите, что пошло не так</b>\n\nОтветьте прямо на это сообщение одним сообщением. Можно приложить скриншот, файл, видео или голосовое. Клуб и экран BookVoter, с которого вы открыли отчёт, я уже сохранил.")
    placeholder=label(lang,en="Describe the bug or attach a screenshot",ru="Опишите ошибку или приложите скриншот")
    prompt=await core.bot.send_message(user_id,prompt_text,parse_mode="HTML",reply_markup=ForceReply(selective=True,input_field_placeholder=placeholder[:64])); pending=dict(pending); pending["prompt_chat_id"]=user_id; pending["prompt_message_id"]=prompt.message_id; pending_error_reports[user_id]=pending; return prompt

async def _start_tracked_error_report(callback: core.CallbackQuery):
    user_id=callback.from_user.id; lang=await core.get_lang(callback.message.chat.id,user_id); chat=callback.message.chat; existing=pending_error_reports.get(user_id,{})
    if chat.type==core.ChatType.PRIVATE and existing.get("origin_chat_id"):
        pending={k:existing.get(k) for k in ("origin_chat_id","origin_chat_title","source_message_id","source_screen")}
    else:
        pending={"origin_chat_id":chat.id,"origin_chat_title":getattr(chat,"title",None) or getattr(chat,"full_name",None) or "Private chat","source_message_id":callback.message.message_id,"source_screen":callback.message.text or callback.message.caption or ""}
    pending_error_reports[user_id]=pending
    try:
        await _send_report_prompt_to_dm(user_id,pending,lang); await callback.answer(label(lang,en="I sent the report form to your private chat.",ru="Форму отчёта отправил вам в личные сообщения."),show_alert=True); return
    except Exception as exc: core.logger.info(f"Could not proactively DM report form to {user_id}: {exc}")
    info=await core.bot.get_me(); markup=core.InlineKeyboardMarkup(inline_keyboard=[[core.InlineKeyboardButton(text=label(lang,en="Open private chat",ru="Открыть ЛС с ботом"),url=f"https://t.me/{info.username}")]])
    notice=await callback.message.answer(label(lang,en="🐛 Open the bot in private chat, press Start, then tap “Report an error”. I have kept the club context.",ru="🐛 Откройте бота в личных сообщениях, нажмите Start, затем «Сообщить об ошибке». Контекст клуба уже сохранён."),reply_markup=markup); schedule_message_delete(notice.chat.id,notice.message_id,60); await callback.answer(label(lang,en="Telegram has not allowed me to DM you yet. Open the private chat first.",ru="Telegram пока не разрешает написать вам первым. Сначала откройте ЛС с ботом."),show_alert=True)

async def _load_active_reports(limit=12):
    await _ensure_error_reports_table()
    async with core.database.open_db(core.DATABASE_PATH) as db:
        db.row_factory=aiosqlite.Row
        async with db.execute("SELECT id,user_tg_id,username,user_name,chat_id,chat_title,description,attachment_type,status,created_at,updated_at FROM error_reports WHERE status IN ('open','fixed') ORDER BY CASE status WHEN 'open' THEN 0 ELSE 1 END,id DESC LIMIT ?",(limit,)) as cursor: return [dict(row) for row in await cursor.fetchall()]

async def _can_manage_report(user_id,report):
    if user_id in core.SUPER_ADMIN_IDS: return True
    chat_id=report.get("chat_id"); return bool(chat_id and chat_id<0 and await core.is_admin(chat_id,user_id))

async def _reports_view(user_id,lang):
    visible=[]
    for report in await _load_active_reports():
        if await _can_manage_report(user_id,report): visible.append(report)
    if not visible: return label(lang,en="🐛 No active error reports.",ru="🐛 Активных отчётов об ошибках нет."),core.InlineKeyboardMarkup(inline_keyboard=[[core.InlineKeyboardButton(text="🔄",callback_data="reports_refresh")]])
    text=label(lang,en="🐛 <b>Active BookVoter reports</b>\n\n",ru="🐛 <b>Активные отчёты BookVoter</b>\n\n"); keyboard=[]
    for r in visible:
        status=str(r.get("status") or "open").upper(); reporter=r.get("user_name") or r.get("username") or str(r.get("user_tg_id")); club=r.get("chat_title") or str(r.get("chat_id") or "Private"); desc=(r.get("description") or "").replace("\n"," ").strip(); desc=desc[:177]+"..." if len(desc)>180 else desc
        text+=f"<b>#{r['id']} · {status}</b> · {core.escape_html(club)}\n👤 {core.escape_html(reporter)}\n📝 {core.escape_html(desc)}\n⏰ {core.escape_html(str(r.get('created_at') or ''))}\n\n"
        if r.get("status")=="open": keyboard.append([core.InlineKeyboardButton(text=f"🛠 #{r['id']} FIXED",callback_data=f"report_status:{r['id']}:fixed")])
        else: keyboard.append([core.InlineKeyboardButton(text=f"✅ #{r['id']} CLOSE",callback_data=f"report_status:{r['id']}:closed")])
    keyboard.append([core.InlineKeyboardButton(text="🔄 Refresh",callback_data="reports_refresh")]); return text,core.InlineKeyboardMarkup(inline_keyboard=keyboard)

async def _set_report_status(report_id,status):
    await _ensure_error_reports_table()
    async with core.database.open_db(core.DATABASE_PATH) as db: await db.execute("UPDATE error_reports SET status=?,updated_at=CURRENT_TIMESTAMP WHERE id=?",(status,report_id)); await db.commit()

async def _get_report(report_id):
    await _ensure_error_reports_table()
    async with core.database.open_db(core.DATABASE_PATH) as db:
        db.row_factory=aiosqlite.Row
        async with db.execute("SELECT * FROM error_reports WHERE id=?",(report_id,)) as cursor:
            row=await cursor.fetchone(); return dict(row) if row else None

async def _reports_command(message: core.Message):
    if message.chat.type in (core.ChatType.GROUP,core.ChatType.SUPERGROUP): await cleanup_command_message(message)
    user=message.from_user
    if not user: return
    lang=await core.get_lang(message.chat.id,user.id); text,markup=await _reports_view(user.id,lang)
    if message.chat.type==core.ChatType.PRIVATE: await message.answer(text,parse_mode="HTML",reply_markup=markup); return
    try:
        await core.bot.send_message(user.id,text,parse_mode="HTML",reply_markup=markup); await send_ephemeral_reply(message,label(lang,en="🐛 Reports were sent to your private chat.",ru="🐛 Отчёты отправлены вам в ЛС."),delay_seconds=20)
    except Exception: await send_ephemeral_reply(message,label(lang,en="Open the bot in private chat first, then use /reports.",ru="Сначала откройте бота в ЛС, затем используйте /reports."),delay_seconds=45)

async def _reports_refresh(callback):
    lang=await core.get_lang(callback.message.chat.id,callback.from_user.id); text,markup=await _reports_view(callback.from_user.id,lang); await callback.answer(); await callback.message.edit_text(text,parse_mode="HTML",reply_markup=markup)

async def _report_status_callback(callback):
    lang=await core.get_lang(callback.message.chat.id,callback.from_user.id); parts=callback.data.split(":")
    if len(parts)!=3 or parts[2] not in {"fixed","closed"}: await callback.answer(); return
    try: report_id=int(parts[1])
    except ValueError: await callback.answer(); return
    report=await _get_report(report_id)
    if not report or not await _can_manage_report(callback.from_user.id,report): await callback.answer(label(lang,en="No access to this report.",ru="Нет доступа к этому отчёту."),show_alert=True); return
    await _set_report_status(report_id,parts[2]); await callback.answer(f"#{report_id}: {parts[2].upper()}"); text,markup=await _reports_view(callback.from_user.id,lang); await callback.message.edit_text(text,parse_mode="HTML",reply_markup=markup)

async def _capture_tracked_error_report(message: core.Message):
    user=message.from_user
    if not user: return
    pending=pending_error_reports.get(user.id)
    if not pending: return
    reply=message.reply_to_message
    if not reply or reply.message_id!=pending.get("prompt_message_id"): return
    lang=await core.get_lang(message.chat.id,user.id); description=(message.text or message.caption or "").strip(); attachment_type="none"; attachment_file_id=None
    if message.photo: attachment_type="photo"; attachment_file_id=message.photo[-1].file_id
    elif message.document: attachment_type="document"; attachment_file_id=message.document.file_id
    elif message.video: attachment_type="video"; attachment_file_id=message.video.file_id
    elif message.voice: attachment_type="voice"; attachment_file_id=message.voice.file_id
    if not description:
        if attachment_type!="none": description=label(lang,en="Attachment submitted without a text description.",ru="Вложение отправлено без текстового описания.")
        else: await message.answer(label(lang,en="Please add a short description or attach a screenshot.",ru="Добавьте короткое описание или приложите скриншот.")); return
    report_id=await _save_error_report(message,pending,description,attachment_type,attachment_file_id); pending_error_reports.pop(user.id,None); await _send_tracked_error_report(report_id,message,pending,description,attachment_type); await message.answer(label(lang,en=f"✅ Report #{report_id} saved and sent. Thank you!",ru=f"✅ Отчёт #{report_id} сохранён и отправлен. Спасибо!"))

def install():
    global _installed
    if _installed: return
    _installed=True; core.notify_superadmin_error=notify_superadmin_error
    core.router.callback_query(core.F.data=="report_error_v2")(_start_tracked_error_report); core.router.message(core.Command("reports"))(_reports_command); core.router.callback_query(core.F.data=="reports_refresh")(_reports_refresh); core.router.callback_query(core.F.data.startswith("report_status:"))(_report_status_callback); core.router.message(core.F.chat.type==core.ChatType.PRIVATE)(_capture_tracked_error_report)
