"""BENI SYSTEM admin panel for SHARE-Hi88.

This module adds a safe, persistent inline administration dashboard without
changing customer checkout, wallet, SePay or order processing flows.
"""
import os
import json
import re
import sqlite3
from datetime import datetime
from pathlib import Path

import aiosqlite
from aiogram import F
from aiogram.exceptions import TelegramBadRequest
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, Message, InlineKeyboardButton, InlineKeyboardMarkup, BotCommand

VERSION = "1.1.0"

class AdminAddForm(StatesGroup):
    user_id = State()

class BotSettingForm(StatesGroup):
    value = State()

class BotCommandForm(StatesGroup):
    value = State()

class OrderUiForm(StatesGroup):
    label = State()
    value = State()

class MenuForm(StatesGroup):
    label = State()
    content = State()
    url = State()
    button_text = State()
    photo = State()
    rows = State()
    greeting = State()
    greeting_photo = State()

PERMISSIONS = [("bot.manage", "Quản lý Bot"), ("users.read", "Xem User"), ("users.manage", "Quản lý User"), ("orders.review", "Duyệt đơn"), ("deposits.review", "Duyệt nạp tiền"), ("wallet.adjust", "Điều chỉnh ví"), ("notifications.send", "Gửi thông báo"), ("reports.read", "Xem thống kê"), ("settings.manage", "Cài đặt hệ thống"), ("admins.manage", "Quản lý Admin"), ("database.backup", "Sao lưu Database"), ("maintenance.manage", "Bảo trì"), ("orders.buttons.edit", "Chỉnh sửa nút & nội dung đặt đơn"), ("main.menu.edit", "Quản lý Menu chính")]


def _kb(rows):
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=label, callback_data=data) for label, data in row]
        for row in rows
    ])


async def safe_edit(message, text, *, reply_markup=None):
    """Edit an admin panel message without logging harmless no-op edits."""
    try:
        return await message.edit_text(text, reply_markup=reply_markup)
    except TelegramBadRequest as exc:
        if "message is not modified" in str(exc).lower():
            return None
        raise


def register_system_admin(dp, *, db_path, main_admin_id, admin_ids, timezone, now_text, esc, admin_keyboard, sync_command_menus, clear_admin_command_menu, on_order_ui_update=None, on_main_menu_update=None, on_start_greeting_update=None, on_start_greeting_photo_update=None):
    async def allowed(call):
        uid = call.from_user.id
        if uid not in admin_ids:
            await call.answer("⛔ Bạn không có quyền truy cập.", show_alert=True)
            return False
        if uid == main_admin_id:
            return True
        data = call.data or ""
        if data in ("bs:home", "bs:back"):
            async with aiosqlite.connect(db_path) as db:
                cur = await db.execute("SELECT COUNT(*) FROM admin_permissions WHERE user_id=? AND enabled=1", (uid,))
                has_any = (await cur.fetchone())[0] > 0
            if has_any: return True
            await call.answer("Admin chưa được cấp quyền sử dụng BENI SYSTEM.", show_alert=True)
            return False
        if data.startswith(("bs:admins", "bs:admin", "bs:perms", "bs:perm")):
            required = "admins.manage"
        elif data.startswith("bs:users"):
            required = "users.read"
        elif data.startswith("bs:stats"):
            required = "reports.read"
        elif data.startswith(("bs:db", "bs:backup", "bs:backups")):
            required = "database.backup"
        elif data.startswith("bs:notify") or data.startswith("bs:auto"):
            required = "notifications.send"
        elif data.startswith("bs:maintenance") or data.startswith("bs:maint"):
            required = "maintenance.manage"
        elif data.startswith(("bs:bot", "bs:cmd")):
            required = "bot.manage"
        elif data.startswith("bs:orderui"):
            required = "orders.buttons.edit"
        elif data.startswith("bs:menu"):
            required = "main.menu.edit"
        else:
            required = "settings.manage"
        async with aiosqlite.connect(db_path) as db:
            cur = await db.execute("SELECT enabled FROM admin_permissions WHERE user_id=? AND permission=?", (uid, required))
            row = await cur.fetchone()
        if row and row[0]:
            return True
        await call.answer("⛔ Bạn chưa được cấp quyền: " + required, show_alert=True)
        return False

    async def audit(actor, action, target="", details=""):
        async with aiosqlite.connect(db_path) as db:
            await db.execute(
                "INSERT INTO admin_audit(actor_id,action,target,details,created_at) VALUES(?,?,?,?,?)",
                (actor, action, str(target)[:100], str(details)[:500], now_text())
            )
            await db.commit()

    async def ensure_tables():
        async with aiosqlite.connect(db_path) as db:
            await db.executescript("""
            CREATE TABLE IF NOT EXISTS admin_audit(
              id INTEGER PRIMARY KEY AUTOINCREMENT, actor_id INTEGER NOT NULL,
              action TEXT NOT NULL, target TEXT, details TEXT, created_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS admin_permissions(
              user_id INTEGER NOT NULL, permission TEXT NOT NULL,
              enabled INTEGER NOT NULL DEFAULT 0,
              PRIMARY KEY(user_id, permission)
            );
            """)
            await db.execute("INSERT OR IGNORE INTO admins(user_id,role) VALUES(?, 'main')", (main_admin_id,))
            cur = await db.execute("SELECT user_id FROM admins WHERE role='sub'")
            for (row,) in await cur.fetchall():
                admin_ids.add(int(row))
            await db.commit()

    async def show(call, title, body, rows):
        await safe_edit(call.message, title + "\n\n" + body, reply_markup=_kb(rows))
        await call.answer()

    async def home(call):
        me = call.from_user
        now = datetime.now(timezone)
        body = (
            "TRUNG TÂM QUẢN TRỊ HỆ THỐNG\n"
            "🟢 SYSTEM ONLINE\n\n"
            f"👑 Admin: {esc(me.full_name)}\n"
            f"🆔 Admin ID: <code>{me.id}</code>\n"
            f"📅 Ngày: {now.strftime('%d/%m/%Y')}\n"
            f"⏰ Thời gian: {now.strftime('%H:%M:%S')} GMT+7\n"
            f"⚙️ Phiên bản: {VERSION}\n\n"
            "BENI MANAGEMENT • ALL RIGHTS RESERVED"
        )
        rows = [
            [("🤖 BOT SETTINGS", "bs:bot"), ("👥 USER MANAGER", "bs:users")],
            [("🧩 QUẢN LÝ NÚT ĐẶT ĐƠN", "bs:orderui"), ("🧩 QUẢN LÝ MENU CHÍNH", "bs:menu")],
            [("👑 ADMIN MANAGER", "bs:admins"), ("🔐 PHÂN QUYỀN", "bs:perms")],
            [("🎨 GIAO DIỆN", "bs:ui"), ("📢 THÔNG BÁO", "bs:notify")],
            [("💾 DATABASE", "bs:db"), ("📊 THỐNG KÊ", "bs:stats")],
            [("⚙️ SYSTEM CONFIG", "bs:config"), ("🛠 BẢO TRÌ", "bs:maintenance")],
            [("📜 NHẬT KÝ", "bs:logs"), ("🔄 LÀM MỚI", "bs:home")],
            [("↩️ QUAY LẠI BẢNG QTV", "bs:back")],
        ]
        await show(call, "⚙️ <b>BENI SYSTEM</b>", body, rows)
        await audit(me.id, "open_system_panel")

    @dp.callback_query(F.data == "bs:home")
    async def bs_home(call: CallbackQuery):
        if await allowed(call):
            await home(call)

    @dp.callback_query(F.data == "bs:back")
    async def bs_back(call: CallbackQuery):
        if await allowed(call):
            await safe_edit(call.message, "👑 <b>QUẢN TRỊ KM SHARE HI88</b>", reply_markup=admin_keyboard())
            await call.answer()

    @dp.callback_query(F.data == "bs:bot")
    async def bs_bot(call: CallbackQuery):
        if not await allowed(call): return
        me = await call.bot.get_me()
        try:
            commands = await call.bot.get_my_commands()
            command_text = "\n".join(f"/{x.command} — {x.description}" for x in commands) or "Chưa cài lệnh."
        except Exception:
            command_text = "Không đọc được danh sách lệnh."
        body = (
            f"Trạng thái: 🟢 Đang chạy\nTên Bot: {esc(me.full_name)}\n"
            f"Username: @{esc(me.username)}\nPhiên bản panel: {VERSION}\n\n"
            f"<b>LỆNH BOT</b>\n{esc(command_text)}\n\n"
            "Có thể chỉnh tên, mô tả, mô tả ngắn và danh sách lệnh ngay tại đây. "
            "Username và ảnh đại diện phải đổi qua @BotFather. Không gửi Token vào chat."
        )
        await show(call, "🤖 <b>BOT SETTINGS</b>", body, [
            [("✏️ ĐỔI TÊN BOT", "bs:botedit:name"), ("🗑 XÓA TÊN BOT", "bs:botclear:name")],
            [("📝 SỬA MÔ TẢ", "bs:botedit:description"), ("🗑 XÓA MÔ TẢ", "bs:botclear:description")],
            [("🗑 XÓA MÔ TẢ", "bs:botclear:description"), ("📌 SỬA MÔ TẢ NGẮN", "bs:botedit:short")],
            [("🗑 XÓA MÔ TẢ NGẮN", "bs:botclear:short"), ("📋 QUẢN LÝ LỆNH", "bs:botedit:commands")],
            [("🔄 KIỂM TRA TRẠNG THÁI", "bs:bot")],
            [("↩️ QUAY LẠI", "bs:home")]
        ])
        await audit(call.from_user.id, "view_bot_settings")

    async def command_menu(call: CallbackQuery):
        commands = await call.bot.get_my_commands()
        rows = []
        for item in commands:
            rows.append([(f"✏️ /{item.command}", f"bs:cmdedit:{item.command}"), (f"🗑 /{item.command}", f"bs:cmddelete:{item.command}")])
        rows.append([("➕ THÊM LỆNH", "bs:cmdadd")])
        rows.append([("↩️ BOT SETTINGS", "bs:bot")])
        body = "\n".join(f"• /{esc(x.command)} — {esc(x.description)}" for x in commands) or "Chưa có lệnh nào."
        await show(call, "📋 <b>QUẢN LÝ LỆNH BOT</b>", body + "\n\nChọn ✏️ để sửa hoặc 🗑 để xóa từng lệnh.", rows)

    @dp.callback_query(F.data == "bs:cmdadd")
    async def bs_cmd_add(call: CallbackQuery, state: FSMContext):
        if not await allowed(call): return
        await state.update_data(command_action="add", old_command=None)
        await state.set_state(BotCommandForm.value)
        await safe_edit(call.message, "➕ <b>THÊM LỆNH MỚI</b>\nGửi theo mẫu: <code>ten_lenh - Mô tả lệnh</code>\nVí dụ: <code>help - Trợ giúp</code>", reply_markup=_kb([[("❌ HỦY", "bs:cmdcancel")]]))
        await call.answer()

    @dp.callback_query(F.data.startswith("bs:cmdedit:"))
    async def bs_cmd_edit(call: CallbackQuery, state: FSMContext):
        if not await allowed(call): return
        name = call.data.split(":", 2)[2]
        commands = await call.bot.get_my_commands()
        item = next((x for x in commands if x.command == name), None)
        if not item: return await call.answer("Không tìm thấy lệnh.", show_alert=True)
        await state.update_data(command_action="edit", old_command=name)
        await state.set_state(BotCommandForm.value)
        await safe_edit(call.message, f"✏️ <b>SỬA LỆNH /{esc(name)}</b>\nGửi tên và mô tả mới theo mẫu:\n<code>ten_lenh_moi - Mô tả mới</code>\n\nHiện tại: {esc(item.description)}", reply_markup=_kb([[("❌ HỦY", "bs:cmdcancel")]]))
        await call.answer()

    @dp.callback_query(F.data.startswith("bs:cmddelete:"))
    async def bs_cmd_delete_prompt(call: CallbackQuery):
        if not await allowed(call): return
        name = call.data.split(":", 2)[2]
        await safe_edit(call.message, f"⚠️ <b>XÁC NHẬN XÓA LỆNH /{esc(name)}?</b>", reply_markup=_kb([[("✅ XÁC NHẬN XÓA", f"bs:cmddeleteyes:{name}"), ("❌ HỦY", "bs:cmdmenu")]]))
        await call.answer()

    @dp.callback_query(F.data.startswith("bs:cmddeleteyes:"))
    async def bs_cmd_delete(call: CallbackQuery):
        if not await allowed(call): return
        name = call.data.split(":", 2)[2]
        commands = await call.bot.get_my_commands()
        updated = [x for x in commands if x.command != name]
        if len(updated) == len(commands): return await call.answer("Lệnh không còn tồn tại.", show_alert=True)
        try:
            await call.bot.set_my_commands(updated)
        except Exception as exc:
            return await call.answer("Không thể xóa: " + str(exc)[:160], show_alert=True)
        await audit(call.from_user.id, "delete_bot_command", name)
        await command_menu(call)

    @dp.callback_query(F.data == "bs:cmdmenu")
    async def bs_cmd_menu(call: CallbackQuery):
        if not await allowed(call): return
        await command_menu(call)

    @dp.callback_query(F.data == "bs:cmdcancel")
    async def bs_cmd_cancel(call: CallbackQuery, state: FSMContext):
        if not await allowed(call): return
        await state.clear()
        await safe_edit(call.message, "Đã hủy thao tác.", reply_markup=_kb([[("📋 QUẢN LÝ LỆNH", "bs:cmdmenu"), ("↩️ BOT SETTINGS", "bs:bot")]]))
        await call.answer()

    @dp.message(BotCommandForm.value)
    async def bs_cmd_receive(message, state: FSMContext):
        if message.from_user.id not in admin_ids: return
        data = await state.get_data()
        raw = (message.text or "").strip()
        if " - " not in raw:
            return await message.answer("Sai định dạng. Gửi: ten_lenh - Mô tả lệnh")
        name, description = raw.split(" - ", 1)
        name, description = name.strip(), description.strip()
        if not name.replace("_", "").isalnum() or name.lower() != name or not 1 <= len(name) <= 32 or not description or len(description) > 256:
            return await message.answer("Tên lệnh hoặc mô tả không hợp lệ. Tên chỉ gồm chữ thường, số và dấu gạch dưới.")
        commands = await message.bot.get_my_commands()
        old = data.get("old_command")
        if data.get("command_action") == "add" and any(x.command == name for x in commands):
            return await message.answer("Lệnh này đã tồn tại. Hãy chọn tên khác.")
        if data.get("command_action") == "edit" and any(x.command == name and x.command != old for x in commands):
            return await message.answer("Tên lệnh mới đã được sử dụng.")
        await state.update_data(new_command=name, new_description=description)
        await message.answer(f"⚠️ <b>XÁC NHẬN {('THÊM' if data.get('command_action') == 'add' else 'SỬA')} LỆNH</b>\n\n/{esc(name)} — {esc(description)}", reply_markup=_kb([[("✅ XÁC NHẬN", "bs:cmdconfirm"), ("❌ HỦY", "bs:cmdcancel")]]))

    @dp.callback_query(F.data == "bs:cmdconfirm")
    async def bs_cmd_confirm(call: CallbackQuery, state: FSMContext):
        if not await allowed(call): return
        data = await state.get_data()
        action, old = data.get("command_action"), data.get("old_command")
        name, description = data.get("new_command"), data.get("new_description")
        if action not in ("add", "edit") or not name or not description:
            return await call.answer("Không có dữ liệu chờ xác nhận.", show_alert=True)
        commands = await call.bot.get_my_commands()
        if action == "edit":
            commands = [x for x in commands if x.command != old]
        if any(x.command == name for x in commands):
            return await call.answer("Tên lệnh đã tồn tại. Hãy hủy và chọn tên khác.", show_alert=True)
        commands.append(BotCommand(command=name, description=description))
        if len(commands) > 100:
            return await call.answer("Telegram chỉ hỗ trợ tối đa 100 lệnh.", show_alert=True)
        try:
            await call.bot.set_my_commands(commands)
        except Exception as exc:
            return await call.answer("Không thể cập nhật: " + str(exc)[:160], show_alert=True)
        await audit(call.from_user.id, action + "_bot_command", name, description)
        await state.clear()
        await command_menu(call)

    @dp.callback_query(F.data.startswith("bs:botclear:"))
    async def bs_bot_clear(call: CallbackQuery):
        if not await allowed(call): return
        field = call.data.rsplit(":", 1)[-1]
        if field not in ("name", "description", "short"):
            return await call.answer("Không thể xóa trường này.", show_alert=True)
        await safe_edit(call.message, 
            f"⚠️ <b>XÁC NHẬN XÓA {'TÊN BOT' if field == 'name' else ('MÔ TẢ BOT' if field == 'description' else 'MÔ TẢ NGẮN')}?</b>\n\n"
            "Nội dung hiện tại sẽ được làm trống.",
            reply_markup=_kb([
                [("✅ XÁC NHẬN XÓA", f"bs:botclearconfirm:{field}")],
                [("❌ HỦY", "bs:bot")]
            ])
        )
        await call.answer()

    @dp.callback_query(F.data.startswith("bs:botclearconfirm:"))
    async def bs_bot_clear_confirm(call: CallbackQuery):
        if not await allowed(call): return
        field = call.data.rsplit(":", 1)[-1]
        try:
            if field == "name":
                await call.bot.set_my_name("")
            elif field == "description":
                await call.bot.set_my_description("")
            elif field == "short":
                await call.bot.set_my_short_description("")
            else:
                return await call.answer("Trường không hợp lệ.", show_alert=True)
        except Exception as exc:
            return await call.answer("Telegram không cho phép xóa: " + str(exc)[:160], show_alert=True)
        await audit(call.from_user.id, "clear_bot_" + field)
        await safe_edit(call.message, 
            "✅ Đã xóa nội dung đã chọn.",
            reply_markup=_kb([[("🤖 BOT SETTINGS", "bs:bot"), ("↩️ BENI SYSTEM", "bs:home")]])
        )
        await call.answer("Đã xóa.")

    @dp.callback_query(F.data.startswith("bs:botedit:"))
    async def bs_bot_edit(call: CallbackQuery, state: FSMContext):
        if not await allowed(call): return
        field = call.data.rsplit(":", 1)[-1]
        prompts = {
            "name": ("ĐỔI TÊN BOT", "Gửi tên mới (1–64 ký tự)."),
            "description": ("ĐỔI MÔ TẢ BOT", "Gửi mô tả mới (tối đa 512 ký tự)."),
            "short": ("ĐỔI MÔ TẢ NGẮN", "Gửi mô tả ngắn (tối đa 120 ký tự)."),
            "commands": ("QUẢN LÝ LỆNH", "Gửi từng dòng theo mẫu: start - Mở menu\nhelp - Trợ giúp\nChỉ dùng chữ thường, số và dấu gạch dưới cho tên lệnh.")
        }
        if field == "commands":
            await command_menu(call)
            return
        if field not in prompts: return await call.answer("Mục không hợp lệ.", show_alert=True)
        await state.update_data(bot_field=field)
        await state.set_state(BotSettingForm.value)
        title, prompt = prompts[field]
        await safe_edit(call.message, f"✏️ <b>{title}</b>\n\n{prompt}", reply_markup=_kb([[("❌ HỦY", "bs:botcancel")]]))
        await call.answer()

    @dp.callback_query(F.data == "bs:botcancel")
    async def bs_bot_cancel(call: CallbackQuery, state: FSMContext):
        if not await allowed(call): return
        await state.clear()
        await safe_edit(call.message, "Đã hủy chỉnh sửa.", reply_markup=_kb([[("↩️ BOT SETTINGS", "bs:bot")]]))
        await call.answer()

    @dp.message(BotSettingForm.value)
    async def bs_bot_value(message, state: FSMContext):
        if message.from_user.id not in admin_ids: return
        data = await state.get_data()
        field = data.get("bot_field")
        value = (message.text or "").strip()
        limits = {"name": 64, "description": 512, "short": 120, "commands": 3000}
        if field not in limits or not value or len(value) > limits[field]:
            return await message.answer("Nội dung không hợp lệ hoặc vượt giới hạn. Hãy gửi lại.")
        if field == "commands":
            parsed = []
            for line in value.splitlines():
                if not line.strip(): continue
                if " - " not in line:
                    return await message.answer("Sai định dạng. Mỗi dòng: start - Mở menu")
                command, description = line.split(" - ", 1)
                command = command.strip()
                description = description.strip()
                if not command.replace("_", "").isalnum() or command.lower() != command or not 1 <= len(command) <= 32 or not description or len(description) > 256:
                    return await message.answer(f"Lệnh không hợp lệ: {esc(command)}")
                parsed.append((command, description))
            if not parsed or len(parsed) > 100:
                return await message.answer("Cần từ 1 đến 100 lệnh hợp lệ.")
            value = "\n".join(f"{c} - {d}" for c,d in parsed)
        await state.update_data(bot_value=value)
        preview = esc(value[:2500])
        await message.answer(
            f"⚠️ <b>XÁC NHẬN CẬP NHẬT BOT?</b>\n\n{preview}",
            reply_markup=_kb([[("✅ XÁC NHẬN", "bs:botconfirm"), ("❌ HỦY", "bs:botcancel")]])
        )

    @dp.callback_query(F.data == "bs:botconfirm")
    async def bs_bot_confirm(call: CallbackQuery, state: FSMContext):
        if not await allowed(call): return
        data = await state.get_data()
        field, value = data.get("bot_field"), data.get("bot_value")
        try:
            if field == "name":
                await call.bot.set_my_name(value)
            elif field == "description":
                await call.bot.set_my_description(value)
            elif field == "short":
                await call.bot.set_my_short_description(value)
            elif field == "commands":
                parsed = []
                for line in value.splitlines():
                    command, description = line.split(" - ", 1)
                    parsed.append(BotCommand(command=command, description=description))
                await call.bot.set_my_commands(parsed)
            else:
                return await call.answer("Không tìm thấy nội dung chờ xác nhận.", show_alert=True)
        except Exception as exc:
            await state.clear()
            await call.answer("Telegram từ chối cập nhật: " + str(exc)[:180], show_alert=True)
            return
        await audit(call.from_user.id, "update_bot_" + field, details=value[:500])
        await state.clear()
        await safe_edit(call.message, "✅ Đã cập nhật cài đặt Bot thành công.", reply_markup=_kb([[("🤖 BOT SETTINGS", "bs:bot"), ("↩️ BENI SYSTEM", "bs:home")]]))
        await call.answer("Đã lưu.")

    @dp.callback_query(F.data == "bs:users")
    async def bs_users(call: CallbackQuery):
        if not await allowed(call): return
        async with aiosqlite.connect(db_path) as db:
            cur = await db.execute("SELECT COUNT(*), COUNT(CASE WHEN created_at LIKE ? THEN 1 END) FROM users", (datetime.now(timezone).strftime("%d/%m/%y") + "%",))
            total, today = await cur.fetchone()
            cur = await db.execute("SELECT user_id,username,wallet,created_at FROM users ORDER BY created_at DESC LIMIT 10")
            rows = await cur.fetchall()
        body = f"Tổng người dùng: {total}\nNgười dùng mới hôm nay (ước tính theo định dạng ngày): {today}\n\n"
        body += "\n".join(f"• <code>{r[0]}</code> @{esc(r[1] or 'N/A')} • {int(r[2]):,}đ" for r in rows) or "Chưa có người dùng."
        await show(call, "👥 <b>USER MANAGER</b>", body, [
            [("🔄 LÀM MỚI", "bs:users"), ("↩️ QUAY LẠI", "bs:home")]
        ])
        await audit(call.from_user.id, "view_users")

    @dp.callback_query(F.data == "bs:admins")
    async def bs_admins(call: CallbackQuery):
        if not await allowed(call): return
        async with aiosqlite.connect(db_path) as db:
            cur = await db.execute("SELECT user_id FROM admins WHERE role='sub' ORDER BY user_id")
            ids = [row[0] for row in await cur.fetchall()]
        rows = [[(f"👤 {uid}", f"bs:admin:{uid}")] for uid in ids]
        rows += [[("➕ THÊM ADMIN", "bs:adminadd"), ("↩️ QUAY LẠI", "bs:home")]]
        await show(call, "👑 <b>ADMIN MANAGER</b>", f"Admin chính: <code>{main_admin_id}</code>\nChọn Admin phụ để quản lý.", rows)

    @dp.callback_query(F.data == "bs:adminadd")
    async def bs_admin_add(call: CallbackQuery, state: FSMContext):
        if not await allowed(call): return
        if call.from_user.id != main_admin_id: return await call.answer("Chỉ Admin chính được thêm Admin.", show_alert=True)
        await state.set_state(AdminAddForm.user_id)
        await safe_edit(call.message, "➕ <b>THÊM ADMIN PHỤ</b>\nGửi Telegram ID dạng số.", reply_markup=_kb([[("❌ HỦY", "bs:admins")]]))
        await call.answer()

    @dp.message(AdminAddForm.user_id)
    async def bs_admin_add_receive(message, state: FSMContext):
        if message.from_user.id != main_admin_id:
            await state.clear()
            return
        raw = (message.text or "").strip()
        if not raw.isdigit() or not 5 <= len(raw) <= 15:
            return await message.answer("ID không hợp lệ. Hãy gửi Telegram ID chỉ gồm chữ số.")
        uid = int(raw)
        if uid == main_admin_id:
            await state.clear()
            return await message.answer("Đây là Admin chính.")
        async with aiosqlite.connect(db_path) as db:
            await db.execute("INSERT INTO admins(user_id,role) VALUES(?, 'sub') ON CONFLICT(user_id) DO UPDATE SET role='sub'", (uid,))
            await db.commit()
        admin_ids.add(uid)
        await sync_command_menus()
        await audit(message.from_user.id, "add_sub_admin", str(uid))
        await state.clear()
        await message.answer(f"✅ Đã thêm Admin phụ <code>{uid}</code>. Mặc định tất cả quyền đều TẮT.", reply_markup=_kb([[("👑 ADMIN MANAGER", "bs:admins"), ("🔐 PHÂN QUYỀN", "bs:perms")]]))

    @dp.callback_query(F.data.startswith("bs:admin:"))
    async def bs_admin_detail(call: CallbackQuery):
        if not await allowed(call): return
        uid = int(call.data.split(":")[-1])
        if uid == main_admin_id: return await call.answer("Không thể sửa Admin chính.", show_alert=True)
        await safe_edit(call.message, f"👤 <b>ADMIN PHỤ</b>\nID: <code>{uid}</code>", reply_markup=_kb([[("🔐 CẤU HÌNH QUYỀN", f"bs:permuser:{uid}")], [("🗑 XÓA ADMIN", f"bs:admindel:{uid}")], [("↩️ DANH SÁCH", "bs:admins")]]))
        await call.answer()

    @dp.callback_query(F.data.startswith("bs:admindel:"))
    async def bs_admin_delete_prompt(call: CallbackQuery):
        if not await allowed(call): return
        if call.from_user.id != main_admin_id: return await call.answer("Chỉ Admin chính được xóa.", show_alert=True)
        uid = int(call.data.split(":")[-1])
        if uid == main_admin_id: return await call.answer("Không thể xóa Admin chính.", show_alert=True)
        await safe_edit(call.message, f"⚠️ <b>XÁC NHẬN XÓA ADMIN?</b>\nID: <code>{uid}</code>", reply_markup=_kb([[("⚠️ XÁC NHẬN XÓA", f"bs:admindelconfirm:{uid}"), ("❌ HỦY", f"bs:admin:{uid}")]]))
        await call.answer()

    @dp.callback_query(F.data.startswith("bs:admindelconfirm:"))
    async def bs_admin_delete(call: CallbackQuery):
        if not await allowed(call): return
        if call.from_user.id != main_admin_id: return await call.answer("Chỉ Admin chính được xóa.", show_alert=True)
        uid = int(call.data.split(":")[-1])
        if uid == main_admin_id: return await call.answer("Không thể xóa Admin chính.", show_alert=True)
        async with aiosqlite.connect(db_path) as db:
            await db.execute("DELETE FROM admins WHERE user_id=? AND role='sub'", (uid,))
            await db.execute("DELETE FROM admin_permissions WHERE user_id=?", (uid,))
            await db.commit()
        admin_ids.discard(uid)
        await clear_admin_command_menu(uid)
        await sync_command_menus()
        await audit(call.from_user.id, "delete_sub_admin", str(uid))
        await safe_edit(call.message, "✅ Admin phụ đã bị xóa và quyền truy cập đã thu hồi.", reply_markup=_kb([[("👑 ADMIN MANAGER", "bs:admins"), ("↩️ BENI SYSTEM", "bs:home")]]))
        await call.answer()

    @dp.callback_query(F.data == "bs:perms")
    async def bs_perms(call: CallbackQuery):
        if not await allowed(call): return
        async with aiosqlite.connect(db_path) as db:
            cur = await db.execute("SELECT user_id FROM admins WHERE role='sub' ORDER BY user_id")
            ids = [row[0] for row in await cur.fetchall()]
        rows = [[(f"👤 {uid}", f"bs:permuser:{uid}")] for uid in ids]
        rows.append([("↩️ QUAY LẠI", "bs:home")])
        await show(call, "🔐 <b>PERMISSION MANAGER</b>", "Chọn Admin phụ để bật/tắt từng quyền. Admin chính luôn toàn quyền.", rows)

    @dp.callback_query(F.data.startswith("bs:permuser:"))
    async def bs_perm_user(call: CallbackQuery):
        if not await allowed(call): return
        uid = int(call.data.split(":")[-1])
        if uid == main_admin_id: return await call.answer("Admin chính luôn toàn quyền.", show_alert=True)
        async with aiosqlite.connect(db_path) as db:
            cur = await db.execute("SELECT permission,enabled FROM admin_permissions WHERE user_id=?", (uid,))
            current = dict(await cur.fetchall())
        rows = [[(("🟢 " if current.get(key, 0) else "⚫ ") + label, f"bs:permtoggle:{uid}:{key}")] for key,label in PERMISSIONS]
        rows.append([("↩️ DANH SÁCH QUYỀN", "bs:perms")])
        await show(call, f"🔐 <b>PHÂN QUYỀN ADMIN</b>\nID: <code>{uid}</code>", "Chạm vào quyền để bật hoặc tắt.", rows)

    @dp.callback_query(F.data.startswith("bs:permtoggle:"))
    async def bs_perm_toggle(call: CallbackQuery):
        if not await allowed(call): return
        if call.from_user.id != main_admin_id: return await call.answer("Chỉ Admin chính được cấp/thu hồi quyền.", show_alert=True)
        parts = call.data.split(":")
        uid, permission = int(parts[2]), parts[3]
        if permission not in {item[0] for item in PERMISSIONS} or uid == main_admin_id:
            return await call.answer("Thao tác không hợp lệ.", show_alert=True)
        async with aiosqlite.connect(db_path) as db:
            cur = await db.execute("SELECT enabled FROM admin_permissions WHERE user_id=? AND permission=?", (uid, permission))
            row = await cur.fetchone()
            enabled = 0 if row and row[0] else 1
            await db.execute("INSERT INTO admin_permissions(user_id,permission,enabled) VALUES(?,?,?) ON CONFLICT(user_id,permission) DO UPDATE SET enabled=excluded.enabled", (uid, permission, enabled))
            await db.commit()
        await audit(call.from_user.id, "toggle_admin_permission", str(uid), permission + "=" + str(enabled))
        await bs_perm_user(call)

    @dp.callback_query(F.data == "bs:ui")
    async def bs_ui(call: CallbackQuery):
        if not await allowed(call): return
        async with aiosqlite.connect(db_path) as db:
            cur = await db.execute("SELECT value FROM settings WHERE key='beni_ui_theme'")
            row = await cur.fetchone()
        theme = row[0] if row else "dark"
        await show(call, "🎨 <b>UI MANAGER</b>", f"Chế độ hiển thị đã lưu: <b>{esc(theme.upper())}</b>\n\nLưu ý: theme chỉ là cấu hình nội bộ; Telegram chat không thể đổi màu giao diện ứng dụng Telegram.", [
            [("🌙 DARK MODE", "bs:theme:dark"), ("☀️ LIGHT MODE", "bs:theme:light")],
            [("↩️ QUAY LẠI", "bs:home")]
        ])

    @dp.callback_query(F.data.startswith("bs:theme:"))
    async def bs_theme(call: CallbackQuery):
        if not await allowed(call): return
        theme = call.data.rsplit(":", 1)[-1]
        if theme not in ("dark", "light"): return await call.answer("Giá trị không hợp lệ.", show_alert=True)
        await safe_edit(call.message, 
            f"⚠️ <b>XÁC NHẬN CHUYỂN SANG {theme.upper()} MODE?</b>",
            reply_markup=_kb([[("✅ XÁC NHẬN", f"bs:themeconfirm:{theme}"), ("❌ HỦY", "bs:ui")]])
        )
        await call.answer()

    @dp.callback_query(F.data.startswith("bs:themeconfirm:"))
    async def bs_theme_confirm(call: CallbackQuery):
        if not await allowed(call): return
        theme = call.data.rsplit(":", 1)[-1]
        if theme not in ("dark", "light"): return await call.answer("Giá trị không hợp lệ.", show_alert=True)
        async with aiosqlite.connect(db_path) as db:
            await db.execute("INSERT INTO settings(key,value) VALUES('beni_ui_theme',?) ON CONFLICT(key) DO UPDATE SET value=excluded.value", (theme,))
            await db.commit()
        await audit(call.from_user.id, "set_ui_theme", details=theme)
        await safe_edit(call.message, 
            f"🎨 <b>UI MANAGER</b>\n\nChế độ hiển thị đã lưu: <b>{esc(theme.upper())}</b>\n\nLưu ý: theme chỉ là cấu hình nội bộ; Telegram chat không thể đổi màu giao diện ứng dụng Telegram.",
            reply_markup=_kb([[("🌙 DARK MODE", "bs:theme:dark"), ("☀️ LIGHT MODE", "bs:theme:light")], [("↩️ QUAY LẠI", "bs:home")]])
        )
        await call.answer("Đã lưu giao diện.")

    @dp.callback_query(F.data == "bs:notify")
    async def bs_notify(call: CallbackQuery):
        if not await allowed(call): return
        async with aiosqlite.connect(db_path) as db:
            cur = await db.execute("SELECT value FROM settings WHERE key='beni_auto_announce'")
            row = await cur.fetchone()
        enabled = row[0] if row else "1"
        await show(call, "📢 <b>NOTIFICATION MANAGER</b>",
            f"Kênh thông báo đơn: <code>{esc(os.getenv('ORDER_ANNOUNCE_CHANNEL', '@sharegame1'))}</code>\n"
            f"Thông báo tự động: {'🟢 BẬT' if enabled == '1' else '⚫ TẮT'}\n\n"
            "Cấu hình kênh có thể thay đổi bằng Railway Variables: ORDER_ANNOUNCE_CHANNEL.",
            [[("🔔 BẬT TỰ ĐỘNG", "bs:auto:1"), ("🔕 TẮT TỰ ĐỘNG", "bs:auto:0")],
             [("↩️ QUAY LẠI", "bs:home")]])

    @dp.callback_query(F.data.startswith("bs:auto:"))
    async def bs_auto(call: CallbackQuery):
        if not await allowed(call): return
        value = call.data.rsplit(":", 1)[-1]
        if value not in ("0", "1"): return await call.answer("Giá trị không hợp lệ.", show_alert=True)
        await safe_edit(call.message, 
            f"⚠️ <b>XÁC NHẬN {'BẬT' if value == '1' else 'TẮT'} THÔNG BÁO TỰ ĐỘNG?</b>",
            reply_markup=_kb([[("✅ XÁC NHẬN", f"bs:autoconfirm:{value}"), ("❌ HỦY", "bs:notify")]])
        )
        await call.answer()

    @dp.callback_query(F.data.startswith("bs:autoconfirm:"))
    async def bs_auto_confirm(call: CallbackQuery):
        if not await allowed(call): return
        value = call.data.rsplit(":", 1)[-1]
        if value not in ("0", "1"): return await call.answer("Giá trị không hợp lệ.", show_alert=True)
        async with aiosqlite.connect(db_path) as db:
            await db.execute("INSERT INTO settings(key,value) VALUES('beni_auto_announce',?) ON CONFLICT(key) DO UPDATE SET value=excluded.value", (value,))
            await db.commit()
        await audit(call.from_user.id, "set_auto_announce", details=value)
        enabled = value
        await safe_edit(call.message, 
            "📢 <b>NOTIFICATION MANAGER</b>\n\n"
            f"Kênh thông báo đơn: <code>{esc(os.getenv('ORDER_ANNOUNCE_CHANNEL', '@sharegame1'))}</code>\n"
            f"Thông báo tự động: {'🟢 BẬT' if enabled == '1' else '⚫ TẮT'}\n\n"
            "Cấu hình kênh có thể thay đổi bằng Railway Variables: ORDER_ANNOUNCE_CHANNEL.",
            reply_markup=_kb([[("🔔 BẬT TỰ ĐỘNG", "bs:auto:1"), ("🔕 TẮT TỰ ĐỘNG", "bs:auto:0")], [("↩️ QUAY LẠI", "bs:home")]])
        )
        await call.answer("Đã lưu cấu hình thông báo.")

    @dp.callback_query(F.data == "bs:db")
    async def bs_db(call: CallbackQuery):
        if not await allowed(call): return
        size = os.path.getsize(db_path) if os.path.exists(db_path) else 0
        await show(call, "💾 <b>DATABASE MANAGER</b>",
            f"Database: <code>{esc(db_path)}</code>\nDung lượng: {size:,} bytes\nTrạng thái: {'🟢 Có thể truy cập' if os.path.exists(db_path) else '⚠️ Chưa tạo'}\n\nBackup được lưu trong thư mục database_backups cạnh file DB. Khôi phục cần thao tác thủ công sau khi dừng bot để tránh ghi đè dữ liệu đang mở.",
            [[("📦 TẠO BACKUP", "bs:backup"), ("📜 BACKUP GẦN ĐÂY", "bs:backups")],
             [("↩️ QUAY LẠI", "bs:home")]])

    @dp.callback_query(F.data == "bs:backup")
    async def bs_backup(call: CallbackQuery):
        if not await allowed(call): return
        if not os.path.isfile(db_path):
            return await call.answer("Database chưa tồn tại.", show_alert=True)
        backup_dir = Path(db_path).resolve().parent / "database_backups"
        backup_dir.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now(timezone).strftime("%Y%m%d_%H%M%S")
        target = backup_dir / f"share_hi88_{stamp}.sqlite3"
        try:
            source = sqlite3.connect(db_path)
            destination = sqlite3.connect(str(target))
            with destination:
                source.backup(destination)
            destination.close()
            source.close()
        except Exception:
            if target.exists(): target.unlink()
            return await call.answer("Backup thất bại. Kiểm tra quyền ghi và dung lượng volume.", show_alert=True)
        await audit(call.from_user.id, "database_backup", str(target.name))
        await show(call, "💾 <b>BACKUP HOÀN TẤT</b>", f"Đã tạo bản sao lưu:\n<code>{esc(target.name)}</code>\nKích thước: {target.stat().st_size:,} bytes", [
            [("📜 DANH SÁCH BACKUP", "bs:backups"), ("↩️ DATABASE", "bs:db")]
        ])

    @dp.callback_query(F.data == "bs:backups")
    async def bs_backups(call: CallbackQuery):
        if not await allowed(call): return
        folder = Path(db_path).resolve().parent / "database_backups"
        files = sorted(folder.glob("*.sqlite3"), reverse=True)[:10] if folder.exists() else []
        body = "\n".join(f"• <code>{esc(p.name)}</code> • {p.stat().st_size:,} bytes" for p in files) or "Chưa có bản backup."
        rows = [[("🗑 XÓA " + p.name[-19:], "bs:backupdel:" + p.name)] for p in files]
        rows.append([("↩️ DATABASE", "bs:db")])
        await show(call, "📜 <b>LỊCH SỬ SAO LƯU</b>", body + "\n\nChọn bản sao lưu muốn xóa:", rows)

    @dp.callback_query(F.data.startswith("bs:backupdel:"))
    async def bs_backup_delete_prompt(call: CallbackQuery):
        if not await allowed(call): return
        name = call.data.split(":", 2)[2]
        if not re.fullmatch(r"share_hi88_\d{8}_\d{6}\.sqlite3", name):
            return await call.answer("Tên bản sao lưu không hợp lệ.", show_alert=True)
        await safe_edit(call.message, 
            f"⚠️ <b>XÁC NHẬN XÓA BẢN SAO LƯU?</b>\n\n<code>{esc(name)}</code>\n"
            "Thao tác này chỉ xóa file backup đã chọn, không ảnh hưởng database đang chạy.",
            reply_markup=_kb([
                [("✅ XÁC NHẬN XÓA", "bs:backupdelconfirm:" + name)],
                [("❌ HỦY", "bs:backups")]
            ])
        )
        await call.answer()

    @dp.callback_query(F.data.startswith("bs:backupdelconfirm:"))
    async def bs_backup_delete(call: CallbackQuery):
        if not await allowed(call): return
        name = call.data.split(":", 2)[2]
        if not re.fullmatch(r"share_hi88_\d{8}_\d{6}\.sqlite3", name):
            return await call.answer("Tên bản sao lưu không hợp lệ.", show_alert=True)
        folder = Path(db_path).resolve().parent / "database_backups"
        target = folder / name
        if target.parent != folder.resolve() or not target.is_file():
            return await call.answer("Không tìm thấy bản sao lưu.", show_alert=True)
        try:
            target.unlink()
        except OSError as exc:
            return await call.answer("Không thể xóa file: " + str(exc)[:120], show_alert=True)
        await audit(call.from_user.id, "delete_database_backup", name)
        await safe_edit(call.message, 
            f"🗑 Đã xóa bản sao lưu <code>{esc(name)}</code>.",
            reply_markup=_kb([[("📜 DANH SÁCH BACKUP", "bs:backups"), ("↩️ DATABASE", "bs:db")]])
        )
        await call.answer("Đã xóa backup.")

    @dp.callback_query(F.data == "bs:stats")
    async def bs_stats(call: CallbackQuery):
        if not await allowed(call): return
        async with aiosqlite.connect(db_path) as db:
            async def scalar(sql):
                cur = await db.execute(sql); row = await cur.fetchone(); return row[0] or 0
            users = await scalar("SELECT COUNT(*) FROM users")
            orders = await scalar("SELECT COUNT(*) FROM orders")
            pending = await scalar("SELECT COUNT(*) FROM orders WHERE status IN ('pending','processing')")
            deposits, total = await (await db.execute("SELECT COUNT(*),COALESCE(SUM(amount),0) FROM deposits WHERE status='paid'")).fetchone()
            admins = await scalar("SELECT COUNT(*) FROM admins")
        body = f"👥 Người dùng: {users}\n📦 Tổng đơn: {orders}\n⏳ Đơn đang chờ: {pending}\n💳 Lần nạp thành công: {deposits}\n💰 Tổng nạp: {total:,}đ\n👑 Admin ghi nhận trong DB: {admins}"
        await show(call, "📊 <b>STATISTICS</b>", body, [[("🔄 LÀM MỚI", "bs:stats"), ("↩️ QUAY LẠI", "bs:home")]])
        await audit(call.from_user.id, "view_statistics")

    @dp.callback_query(F.data == "bs:config")
    async def bs_config(call: CallbackQuery):
        if not await allowed(call): return
        await show(call, "⚙️ <b>SYSTEM CONFIG</b>",
            f"Tên hệ thống: BENI SYSTEM / SHARE Hi88\nPhiên bản panel: {VERSION}\nNgôn ngữ: Tiếng Việt\nMúi giờ: {timezone.key}\n"
            f"Database path: <code>{esc(db_path)}</code>\n\nThông tin máy chủ và biến bí mật được quản lý trong Railway Variables. Token/API key không được hiển thị trong panel.",
            [[("↩️ QUAY LẠI", "bs:home")]])
        await audit(call.from_user.id, "view_system_config")

    @dp.callback_query(F.data == "bs:maintenance")
    async def bs_maintenance(call: CallbackQuery):
        if not await allowed(call): return
        async with aiosqlite.connect(db_path) as db:
            cur = await db.execute("SELECT value FROM settings WHERE key='beni_maintenance'")
            row = await cur.fetchone()
        enabled = row[0] if row else "0"
        await show(call, "🛠 <b>MAINTENANCE</b>",
            f"Chế độ bảo trì: {'🔴 ĐANG BẬT' if enabled == '1' else '🟢 ĐANG TẮT'}\n\n"
            "Cờ bảo trì đã được lưu. Luồng chặn giao dịch trong toàn bộ bot và thao tác restart Railway chưa được nối vào phiên bản này; restart cần Railway Dashboard/API.",
            [[("🔴 BẬT BẢO TRÌ", "bs:maint:1"), ("🟢 TẮT BẢO TRÌ", "bs:maint:0")],
             [("↩️ QUAY LẠI", "bs:home")]])

    @dp.callback_query(F.data.startswith("bs:maint:"))
    async def bs_maint_set(call: CallbackQuery):
        if not await allowed(call): return
        value = call.data.rsplit(":", 1)[-1]
        if value not in ("0", "1"): return await call.answer("Giá trị không hợp lệ.", show_alert=True)
        await safe_edit(call.message, 
            f"⚠️ <b>XÁC NHẬN {'BẬT' if value == '1' else 'TẮT'} CHẾ ĐỘ BẢO TRÌ?</b>\n"
            "Thay đổi sẽ được lưu vào database.",
            reply_markup=_kb([[("✅ XÁC NHẬN", f"bs:maintconfirm:{value}"), ("❌ HỦY", "bs:maintenance")]])
        )
        await call.answer()

    @dp.callback_query(F.data.startswith("bs:maintconfirm:"))
    async def bs_maint_confirm(call: CallbackQuery):
        if not await allowed(call): return
        value = call.data.rsplit(":", 1)[-1]
        if value not in ("0", "1"): return await call.answer("Giá trị không hợp lệ.", show_alert=True)
        async with aiosqlite.connect(db_path) as db:
            await db.execute("INSERT INTO settings(key,value) VALUES('beni_maintenance',?) ON CONFLICT(key) DO UPDATE SET value=excluded.value", (value,))
            await db.commit()
        await audit(call.from_user.id, "set_maintenance", details=value)
        await bs_maintenance(call)

    @dp.callback_query(F.data == "bs:logs")
    async def bs_logs(call: CallbackQuery):
        if not await allowed(call): return
        async with aiosqlite.connect(db_path) as db:
            cur = await db.execute("SELECT actor_id,action,target,created_at FROM admin_audit ORDER BY id DESC LIMIT 15")
            rows = await cur.fetchall()
        body = "\n".join(f"• {esc(t)} | <code>{uid}</code> | {esc(a)} {esc(target)}" for uid,a,target,t in rows) or "Chưa có nhật ký."
        await show(call, "📜 <b>NHẬT KÝ QUẢN TRỊ</b>", body, [[("🔄 LÀM MỚI", "bs:logs"), ("↩️ QUAY LẠI", "bs:home")]])



    MENU_DEFAULTS = [
        {"id":"hot","label":"🎁 ĐẶT ĐƠN KM SHARE","enabled":True,"locked":True,"action":"hot","content":"","url":"","photo":""},
        {"id":"deposit","label":"💳 NẠP TIỀN","enabled":True,"locked":False,"action":"deposit","content":"","url":"","photo":""},
        {"id":"wallet","label":"👛 SỐ DƯ TÀI KHOẢN","enabled":True,"locked":False,"action":"wallet","content":"","url":"","photo":""},
        {"id":"history","label":"📊 LỊCH SỬ GIAO DỊCH","enabled":True,"locked":False,"action":"history","content":"","url":"","photo":""},
        {"id":"support","label":"📞 LIÊN HỆ ADMIN","enabled":True,"locked":False,"action":"support","content":"","url":"","photo":""},
        {"id":"guide","label":"📖 HƯỚNG DẪN","enabled":True,"locked":False,"action":"guide","content":"","url":"","photo":""},
        {"id":"admin","label":"🛠 QUẢN TRỊ","enabled":True,"locked":True,"action":"admin","content":"","url":"","photo":""}
    ]

    async def menu_load():
        async with aiosqlite.connect(db_path) as db:
            cur = await db.execute("SELECT value FROM settings WHERE key='main_menu_config'")
            row = await cur.fetchone()
            cur = await db.execute("SELECT value FROM settings WHERE key='main_menu_rows'")
            row_count = await cur.fetchone()
            cur = await db.execute("SELECT value FROM settings WHERE key='start_greeting'")
            greeting = await cur.fetchone()
        try: items = json.loads(row[0]) if row else [dict(x) for x in MENU_DEFAULTS]
        except Exception: items = [dict(x) for x in MENU_DEFAULTS]
        # Every menu item has a permanent internal code. Existing IDs are retained
        # so previously stored callbacks and customer menu actions remain compatible.
        changed = False
        used_codes = set()
        for index, item in enumerate(items, 1):
            if not item.get("id"):
                item["id"] = f"menu_{index}"
                changed = True
            code = str(item.get("code") or item["id"]).strip()
            if not code or code in used_codes:
                code = str(item["id"])
            if item.get("code") != code:
                item["code"] = code
                changed = True
            used_codes.add(code)
        if changed:
            async with aiosqlite.connect(db_path) as db:
                await db.execute("INSERT INTO settings(key,value) VALUES('main_menu_config',?) ON CONFLICT(key) DO UPDATE SET value=excluded.value", (json.dumps(items, ensure_ascii=False),))
                await db.commit()
        try: count = max(1, min(4, int(row_count[0]))) if row_count else 2
        except Exception: count = 2
        return items, count, greeting[0] if greeting else "Xin chào! Chào mừng bạn đến với SHARE-Hi88."

    async def menu_save(items, count):
        async with aiosqlite.connect(db_path) as db:
            await db.execute("INSERT INTO settings(key,value) VALUES('main_menu_config',?) ON CONFLICT(key) DO UPDATE SET value=excluded.value", (json.dumps(items, ensure_ascii=False),))
            await db.execute("INSERT INTO settings(key,value) VALUES('main_menu_rows',?) ON CONFLICT(key) DO UPDATE SET value=excluded.value", (str(count),))
            await db.commit()
        if on_main_menu_update: on_main_menu_update(items, count)

    async def menu_panel(call, state):
        items, count, greeting = await menu_load()
        rows = []
        for i, item in enumerate(items):
            mark = "🔒" if item.get("locked") else ("✅" if item.get("enabled", True) else "⬜")
            code = esc(str(item.get("code") or item["id"]))
            controls = [(f"{mark} [{code}] {item['label']}", f"bs:menu:toggle:{item['id']}"), ("✏️", f"bs:menu:edit:{item['id']}")]
            if i > 0: controls.append(("⬆️", f"bs:menu:up:{item['id']}"))
            if i < len(items)-1: controls.append(("⬇️", f"bs:menu:down:{item['id']}"))
            if item.get("action") == "custom": controls.append(("🗑", f"bs:menu:delete:{item['id']}"))
            rows.append(controls)
        rows += [[(f"🔢 BỐ CỤC HÀNG: {count}", "bs:menu:rows")],
                 [("👋 LỜI CHÀO /START", "bs:menu:greeting"), ("➕ THÊM MENU", "bs:menu:add")],
                 [("↩️ BENI SYSTEM", "bs:home")]]
        body = "✅ đang hiện · ⬜ đang ẩn · 🔒 cố định\n⬆️⬇️ đổi thứ tự · Bấm tên để bật/tắt (🔒 không tắt)\n✏️ sửa tên / nội dung / ảnh / nút bấm\n🏷 Mỗi nút có mã [code] riêng, mã giữ nguyên khi đổi tên.\n🗑 xóa menu tự thêm · ➕ thêm menu mới"
        await show(call, "🧩 <b>QUẢN LÝ MENU CHÍNH</b>", body, rows)

    @dp.callback_query(F.data == "bs:menu")
    async def bs_menu(call: CallbackQuery, state: FSMContext):
        if not await allowed(call): return
        await state.clear()
        await menu_panel(call, state)

    @dp.callback_query(F.data == "bs:menu:add")
    async def bs_menu_add(call: CallbackQuery, state: FSMContext):
        if not await allowed(call): return
        await state.clear()
        await state.set_state(MenuForm.label)
        await safe_edit(call.message, "➕ <b>THÊM MENU MỚI</b>\n\nGửi tên nút menu mới (tối đa 64 ký tự).")
        await call.answer()

    @dp.callback_query(F.data.startswith("bs:menu:toggle:"))
    async def bs_menu_toggle(call: CallbackQuery, state: FSMContext):
        if not await allowed(call): return
        key = call.data.rsplit(":", 1)[-1]
        items, count, _ = await menu_load()
        item = next((x for x in items if x["id"] == key), None)
        if not item: return await call.answer("Không tìm thấy menu.", show_alert=True)
        if item.get("locked"): return await call.answer("🔒 Menu cố định, không thể ẩn.", show_alert=True)
        item["enabled"] = not item.get("enabled", True)
        await menu_save(items, count)
        await menu_panel(call, state)

    @dp.callback_query(F.data.startswith("bs:menu:up:"))
    async def bs_menu_up(call: CallbackQuery, state: FSMContext):
        if not await allowed(call): return
        key = call.data.rsplit(":", 1)[-1]
        items, count, _ = await menu_load()
        i = next((n for n,x in enumerate(items) if x["id"] == key), -1)
        if i > 0: items[i-1], items[i] = items[i], items[i-1]; await menu_save(items, count)
        await menu_panel(call, state)

    @dp.callback_query(F.data.startswith("bs:menu:down:"))
    async def bs_menu_down(call: CallbackQuery, state: FSMContext):
        if not await allowed(call): return
        key = call.data.rsplit(":", 1)[-1]
        items, count, _ = await menu_load()
        i = next((n for n,x in enumerate(items) if x["id"] == key), -1)
        if 0 <= i < len(items)-1: items[i+1], items[i] = items[i], items[i+1]; await menu_save(items, count)
        await menu_panel(call, state)

    @dp.callback_query(F.data.startswith("bs:menu:delete:"))
    async def bs_menu_delete(call: CallbackQuery, state: FSMContext):
        if not await allowed(call): return
        key = call.data.rsplit(":", 1)[-1]
        items, count, _ = await menu_load()
        item = next((x for x in items if x["id"] == key), None)
        if not item or item.get("action") != "custom": return await call.answer("Chỉ xóa được menu tự thêm.", show_alert=True)
        items = [x for x in items if x["id"] != key]
        await menu_save(items, count)
        await audit(call.from_user.id, "delete_main_menu", key)
        await menu_panel(call, state)

    @dp.callback_query(F.data.startswith("bs:menu:edit:"))
    async def bs_menu_edit(call: CallbackQuery, state: FSMContext):
        if not await allowed(call): return
        key = call.data.rsplit(":", 1)[-1]
        items, _, _ = await menu_load()
        item = next((x for x in items if x["id"] == key), None)
        if not item: return await call.answer("Không tìm thấy menu.", show_alert=True)
        await state.clear()
        await state.update_data(menu_id=key)
        await state.set_state(MenuForm.label)
        code = esc(str(item.get("code") or item["id"]))
        await safe_edit(call.message, "✏️ <b>CHỈNH SỬA MENU</b>\nMã nút: <code>[" + code + "]</code>\nTên hiện tại: " + esc(item["label"]) + "\nGửi tên nút mới (hoặc giữ nguyên bằng cách gửi đúng tên hiện tại):")
        await call.answer()

    @dp.message(MenuForm.label)
    async def menu_label_save(message: Message, state: FSMContext):
        if message.from_user.id not in admin_ids: return
        label = (message.text or "").strip()
        if not label or len(label) > 64: return await message.answer("Tên nút tối đa 64 ký tự.")
        data = await state.get_data()
        key = data.get("menu_id")
        items, count, _ = await menu_load()
        item = next((x for x in items if x["id"] == key), None)
        if item:
            item["label"] = label
            await menu_save(items, count)
            await state.update_data(menu_label=label)
        else:
            new_id = "custom_" + str(int(datetime.now().timestamp() * 1000))
            item = {"id":new_id,"code":new_id,"label":label,"enabled":True,"locked":False,"action":"custom","content":"Bạn đã chọn menu mới.","url":"","button_text":"🔗 MỞ LIÊN KẾT","photo":""}
            items.append(item)
            await menu_save(items, count)
            await state.update_data(menu_id=new_id, menu_label=label)
            await audit(message.from_user.id, "add_main_menu", new_id, label)
        await state.set_state(MenuForm.content)
        await message.answer("Gửi nội dung khi khách bấm nút này. Gửi dấu - để giữ nội dung mặc định.")

    @dp.message(MenuForm.content)
    async def menu_content_save(message: Message, state: FSMContext):
        if message.from_user.id not in admin_ids: return
        value = (message.text or "").strip()
        data = await state.get_data()
        await state.update_data(menu_content=None if value == "-" else value)
        await state.set_state(MenuForm.url)
        await message.answer("Gửi link https:// cho nút hoặc dấu - để bỏ link.")

    @dp.message(MenuForm.url)
    async def menu_url_save(message: Message, state: FSMContext):
        if message.from_user.id not in admin_ids: return
        value = (message.text or "").strip()
        if value != "-" and value and not value.startswith(("https://", "http://")):
            return await message.answer("Link phải bắt đầu bằng https:// hoặc http://, hoặc gửi -.")
        await state.update_data(menu_url="" if value == "-" else value)
        await state.set_state(MenuForm.button_text)
        await message.answer("Gửi tên nút liên kết (ví dụ: 🔗 MỞ LIÊN KẾT), hoặc gửi - để giữ mặc định.")

    @dp.message(MenuForm.button_text)
    async def menu_button_text_save(message: Message, state: FSMContext):
        if message.from_user.id not in admin_ids: return
        value = (message.text or "").strip()
        if value != "-" and (not value or len(value) > 64):
            return await message.answer("Tên nút liên kết phải từ 1 đến 64 ký tự, hoặc gửi - để giữ mặc định.")
        await state.update_data(menu_button_text=None if value == "-" else value)
        await state.set_state(MenuForm.photo)
        await message.answer("Gửi ảnh muốn gắn với menu, gửi - để giữ ảnh hiện tại hoặc /remove để xóa ảnh.")


    @dp.message(MenuForm.photo)
    async def menu_photo_save(message: Message, state: FSMContext):
        if message.from_user.id not in admin_ids: return
        data = await state.get_data()
        key = data.get("menu_id")
        items, count, _ = await menu_load()
        item = next((x for x in items if x["id"] == key), None)
        if not item:
            await state.clear()
            return await message.answer("Menu không còn tồn tại.")
        item["label"] = data.get("menu_label", item["label"])
        if data.get("menu_content") is not None: item["content"] = data["menu_content"]
        item["url"] = data.get("menu_url", item.get("url", ""))
        if data.get("menu_button_text") is not None:
            item["button_text"] = data["menu_button_text"]
        if message.photo: item["photo"] = message.photo[-1].file_id
        elif (message.text or "").strip() == "/remove": item["photo"] = ""
        elif (message.text or "").strip() == "-": pass
        else: return await message.answer("Vui lòng gửi ảnh, dấu - để giữ ảnh, hoặc /remove để xóa ảnh.")
        await menu_save(items, count)
        await audit(message.from_user.id, "edit_main_menu", key, item["label"])
        await state.clear()
        await message.answer("✅ Đã lưu tên, nội dung, ảnh và link nút.", reply_markup=_kb([[("🧩 QUẢN LÝ MENU CHÍNH", "bs:menu")]]))

    @dp.callback_query(F.data == "bs:menu:rows")
    async def bs_menu_rows_prompt(call: CallbackQuery, state: FSMContext):
        if not await allowed(call): return
        await state.clear()
        await state.set_state(MenuForm.rows)
        await safe_edit(call.message, "🔢 Gửi số nút trên mỗi hàng (1–4).")
        await call.answer()

    @dp.message(MenuForm.rows)
    async def menu_rows_save(message: Message, state: FSMContext):
        if message.from_user.id not in admin_ids: return
        try: count = int((message.text or "").strip())
        except ValueError: return await message.answer("Chỉ nhập số từ 1 đến 4.")
        if count not in (1,2,3,4): return await message.answer("Chỉ nhập số từ 1 đến 4.")
        items, _, _ = await menu_load()
        await menu_save(items, count)
        await state.clear()
        await message.answer(f"✅ Đã đặt {count} nút mỗi hàng.", reply_markup=_kb([[("🧩 QUẢN LÝ MENU CHÍNH", "bs:menu")]]))

    @dp.callback_query(F.data == "bs:menu:greeting")
    async def bs_menu_greeting(call: CallbackQuery, state: FSMContext):
        if not await allowed(call): return
        _, _, greeting = await menu_load()
        async with aiosqlite.connect(db_path) as db:
            cur = await db.execute("SELECT value FROM settings WHERE key='start_greeting_photo'")
            row = await cur.fetchone()
        photo_status = "Đã có ảnh" if row and row[0] else "Chưa có ảnh"
        await state.clear()
        await state.set_state(MenuForm.greeting)
        await safe_edit(
            call.message,
            "👋 <b>LỜI CHÀO /START</b>\\n\\n"
            "<b>Nội dung hiện tại:</b>\\n" + esc(greeting) +
            "\\n\\n🖼 Ảnh: " + photo_status +
            "\\n\\nGửi lời chào mới. Có thể dùng {username} và {id}. "
            "Sau đó gửi ảnh hoặc chọn bỏ qua / xóa ảnh."
        )
        await call.answer()

    @dp.message(MenuForm.greeting)
    async def menu_greeting_text(message: Message, state: FSMContext):
        if message.from_user.id not in admin_ids: return
        value = (message.text or "").strip()
        if not value or len(value) > 1000:
            return await message.answer("Lời chào phải có nội dung và tối đa 1.000 ký tự. Hãy gửi lại.")
        async with aiosqlite.connect(db_path) as db:
            cur = await db.execute("SELECT value FROM settings WHERE key='start_greeting_photo'")
            row = await cur.fetchone()
        await state.update_data(greeting_pending_text=value, greeting_pending_photo=(row[0] if row else ""))
        await state.set_state(MenuForm.greeting_photo)
        await message.answer(
            "🖼️ <b>THÊM ẢNH CHO /START (TÙY CHỌN)</b>\\n\\n"
            "Gửi ảnh muốn hiển thị cùng lời chào.\\n"
            "Gửi <code>-</code> để giữ ảnh hiện tại hoặc <code>/remove</code> để xóa ảnh."
        )

    @dp.message(MenuForm.greeting_photo)
    async def menu_greeting_photo(message: Message, state: FSMContext):
        if message.from_user.id not in admin_ids: return
        data = await state.get_data()
        photo = data.get("greeting_pending_photo", "")
        if message.photo:
            photo = message.photo[-1].file_id
        else:
            choice = (message.text or "").strip()
            if choice == "/remove":
                photo = ""
            elif choice == "-":
                pass
            else:
                return await message.answer("Gửi ảnh, dấu <code>-</code> để giữ ảnh hiện tại hoặc <code>/remove</code> để xóa ảnh.")
        await state.update_data(greeting_pending_photo=photo)
        preview_text = data.get("greeting_pending_text", "")
        if photo:
            await message.answer_photo(photo, caption="🖼️ <b>ẢNH XEM TRƯỚC LỜI CHÀO /START</b>")
        await message.answer(
            "🔎 <b>XEM TRƯỚC LỜI CHÀO /START</b>\\n\\n" + esc(preview_text) +
            "\\n\\n🖼 Ảnh: " + ("Đã chọn" if photo else "Không sử dụng") +
            "\\n\\nXác nhận lưu cấu hình này?",
            reply_markup=_kb([
                [("✅ XÁC NHẬN LƯU", "bs:menu:greeting:save")],
                [("❌ HỦY", "bs:menu:greeting:cancel")]
            ])
        )

    @dp.callback_query(F.data == "bs:menu:greeting:save")
    async def menu_greeting_confirm(call: CallbackQuery, state: FSMContext):
        if not await allowed(call): return
        data = await state.get_data()
        value = data.get("greeting_pending_text")
        photo = data.get("greeting_pending_photo", "")
        if not value:
            return await call.answer("Không có nội dung chờ lưu.", show_alert=True)
        async with aiosqlite.connect(db_path) as db:
            await db.execute("INSERT INTO settings(key,value) VALUES('start_greeting',?) ON CONFLICT(key) DO UPDATE SET value=excluded.value", (value,))
            await db.execute("INSERT INTO settings(key,value) VALUES('start_greeting_photo',?) ON CONFLICT(key) DO UPDATE SET value=excluded.value", (photo,))
            await db.commit()
        if on_start_greeting_update: on_start_greeting_update(value)
        if on_start_greeting_photo_update: on_start_greeting_photo_update(photo)
        await audit(call.from_user.id, "edit_start_greeting", "start", "photo=" + ("yes" if photo else "no"))
        await state.clear()
        await safe_edit(
            call.message,
            "✅ <b>ĐÃ LƯU LỜI CHÀO /START</b>\\nVăn bản và ảnh đã được cập nhật.",
            reply_markup=_kb([ [("🧩 QUẢN LÝ MENU CHÍNH", "bs:menu")] ])
        )
        await call.answer("Đã lưu.")

    @dp.callback_query(F.data == "bs:menu:greeting:cancel")
    async def menu_greeting_cancel(call: CallbackQuery, state: FSMContext):
        if not await allowed(call): return
        await state.clear()
        await safe_edit(call.message, "❌ Đã hủy. Lời chào /start cũ vẫn được giữ nguyên.", reply_markup=_kb([ [("🧩 QUẢN LÝ MENU CHÍNH", "bs:menu")] ]))
        await call.answer("Đã hủy.")




    ORDER_UI_ITEMS = {
        "hot": ("[hot] • Nút ĐẶT ĐƠN", "📦 ĐẶT ĐƠN"),
        "code_game": ("[code_game] • Nút CODE GAME", "🎮 CODE GAME"),
        "km_share": ("[km_share] • Nút KM SHARE", "🎁 KM SHARE"),
        "rr88": ("[rr88] • Nút RR88", "🎮 RR88"),
        "mm88": ("[mm88] • Nút MM88", "🎮 MM88"),
        "code_game_name": ("[code_game_name] • Nội dung nhập tên tài khoản CODE GAME", "👤 Vui lòng nhập tên tài khoản {site_name}:"),
        "code_game_stk": ("[code_game_stk] • Nội dung nhập 4 số cuối STK CODE GAME", "🏦 Vui lòng nhập đúng 4 số cuối STK đã liên kết với game {site_name}:"),
        "code_game_complete": ("[code_game_complete] • Nội dung hoàn tất CODE GAME", "🎉 <b>ĐẶT ĐƠN CODE GAME THÀNH CÔNG</b>\n\n🧾 Mã đơn: <code>{order_id}</code>\n🎮 Game: <b>{site_name}</b>\n👤 Tài khoản: <code>{account_name}</code>\n🏦 4 số cuối STK: <code>{stk}</code>\n💰 Đã thanh toán: <b>{price}</b>\n⏳ Trạng thái: <b>{status}</b>"),
        "code_game_back": ("[code_game_back] • Nút quay lại danh mục CODE GAME", "↩️ QUAY LẠI"),
        "code_game_back_home": ("[code_game_back_home] • Nút quay lại menu", "↩️ QUAY LẠI MENU"),
        "code_game_back_game": ("[code_game_back_game] • Nút quay lại chọn game", "↩️ QUAY LẠI CHỌN GAME"),
        "code_game_back_name": ("[code_game_back_name] • Nút quay lại nhập tên", "↩️ QUAY LẠI NHẬP TÊN"),
        "code_game_back_stk": ("[code_game_back_stk] • Nút quay lại nhập STK", "↩️ QUAY LẠI NHẬP STK"),
        "code_game_home": ("[code_game_home] • Nút menu chính", "🏠 MENU CHÍNH"),
        "hi88": ("[hi88] • Nút SHARE Hi88", "🎁 SHARE Hi88"),
        "qq88": ("[qq88] • Nút SHARE QQ88", "🎁 SHARE QQ88"),
        "hi88_name": ("[hi88_name] • Mời nhập username Hi88", "👤 Vui lòng nhập tên tài khoản Hi88:"),
        "hi88_stk": ("[hi88_stk] • Mời nhập STK Hi88", "🏦 Nhập đúng 4 số cuối STK ngân hàng đã liên kết với tài khoản Hi88:"),
        "qq88_name": ("[qq88_name] • Mời nhập username QQ88", "👤 Vui lòng nhập tên tài khoản QQ88:"),
        "qq88_stk": ("[qq88_stk] • Mời nhập STK QQ88", "🏦 Nhập đúng 6 số cuối STK ngân hàng đã liên kết với tài khoản QQ88:"),
        "confirm": ("[confirm] • Nút xác nhận đơn", "✅ XÁC NHẬN THANH TOÁN"),
        "complete": ("[complete] • Nội dung hoàn tất đơn", "🎉 <b>ĐẶT ĐƠN THÀNH CÔNG</b>\n\n🎁 Sản phẩm: <b>SHARE {site_name}</b>\n🎟 Gói: <b>{product_code} điểm</b>\n🧾 Mã đơn: <code>{order_id}</code>\n👤 Tài khoản: <code>{account_name}</code>\n🏦 {digits} số cuối STK: <code>{stk}</code>\n💰 Đã thanh toán: <b>{price}</b>\n⏳ Trạng thái: <b>{status}</b>")
    }

    async def orderui_custom_items():
        async with aiosqlite.connect(db_path) as db:
            cur = await db.execute("SELECT value FROM settings WHERE key='order_ui_custom_items'")
            row = await cur.fetchone()
        try:
            value = json.loads(row[0]) if row and row[0] else []
            return value if isinstance(value, list) else []
        except Exception:
            return []

    async def orderui_save_custom_items(items):
        async with aiosqlite.connect(db_path) as db:
            await db.execute("INSERT INTO settings(key,value) VALUES('order_ui_custom_items',?) ON CONFLICT(key) DO UPDATE SET value=excluded.value", (json.dumps(items, ensure_ascii=False),))
            await db.commit()

    @dp.callback_query(F.data == "bs:orderui")
    async def bs_orderui(call: CallbackQuery, state: FSMContext):
        if not await allowed(call): return
        await state.clear()
        rows = []
        async with aiosqlite.connect(db_path) as db:
            for key, (label, default) in ORDER_UI_ITEMS.items():
                cur = await db.execute("SELECT value FROM settings WHERE key=?", ("order_ui_" + key,))
                row = await cur.fetchone()
                status = "📝 ĐÃ TÙY CHỈNH" if row else "⚪ MẶC ĐỊNH"
                rows.append([(f"{label} · {status}", f"bs:orderui:item:{key}")])
            custom_items = await orderui_custom_items()
            for item in custom_items:
                cur = await db.execute("SELECT value FROM settings WHERE key=?", ("order_ui_" + item["key"],))
                saved = await cur.fetchone()
                status = "📝 ĐÃ TÙY CHỈNH" if saved else "⚪ MẶC ĐỊNH"
                rows.append([(f"[{item['key']}] • {item['label']} · {status}", f"bs:orderui:item:{item['key']}")])
        rows += [[("➕ THÊM NÚT", "bs:orderui:add")],
                 [("↩️ QUAY LẠI BENI SYSTEM", "bs:home")]]
        await show(call, "🧩 <b>QUẢN LÝ NÚT & NỘI DUNG ĐẶT ĐƠN</b>",
                   "Mã trong ngoặc vuông là mã nội bộ, không hiển thị cho khách. Chọn từng mục để sửa, xem trước và xác nhận lưu.", rows)

    @dp.callback_query(F.data == "bs:orderui:add")
    async def bs_orderui_add(call: CallbackQuery, state: FSMContext):
        if not await allowed(call): return
        await state.clear()
        await state.set_state(OrderUiForm.label)
        await safe_edit(call.message, "➕ <b>THÊM NÚT MỚI VÀO LUỒNG ĐẶT ĐƠN</b>\n\nGửi tên nút / tên bước mới. Hệ thống tự tạo mã nội bộ riêng. Mã không hiển thị cho khách.")
        await call.answer()

    @dp.message(OrderUiForm.label)
    async def bs_orderui_add_label(message: Message, state: FSMContext):
        if message.from_user.id not in admin_ids: return
        label = (message.text or "").strip()
        if not label or len(label) > 64:
            return await message.answer("Tên nút phải từ 1 đến 64 ký tự. Hãy gửi lại.")
        key = "custom_" + str(int(datetime.now().timestamp() * 1000))
        items = await orderui_custom_items()
        items.append({"key": key, "label": label, "default": label})
        await orderui_save_custom_items(items)
        await state.update_data(order_ui_key=key)
        await state.set_state(OrderUiForm.value)
        await message.answer(f"✅ Đã tạo mã nội bộ <code>[{key}]</code> cho <b>{esc(label)}</b>.\n\nGửi nội dung mặc định cho bước này để xem trước và xác nhận lưu.")

    @dp.callback_query(F.data.startswith("bs:orderui:item:"))
    async def bs_orderui_item(call: CallbackQuery, state: FSMContext):
        if not await allowed(call): return
        key = call.data.rsplit(":", 1)[-1]
        custom = next((x for x in await orderui_custom_items() if x.get("key") == key), None)
        if key in ORDER_UI_ITEMS:
            label, default = ORDER_UI_ITEMS[key]
        elif custom:
            label, default = f"[{key}] • {custom['label']}", custom.get("default", custom["label"])
        else:
            return await call.answer("Mục không hợp lệ.", show_alert=True)
        async with aiosqlite.connect(db_path) as db:
            cur = await db.execute("SELECT value FROM settings WHERE key=?", ("order_ui_" + key,))
            row = await cur.fetchone()
        current = row[0] if row else default
        await state.clear()
        await state.update_data(order_ui_key=key)
        await state.set_state(OrderUiForm.value)
        await safe_edit(call.message, 
            f"✏️ <b>SỬA {esc(label)}</b>\n\nNội dung hiện tại:\n<code>{esc(current[:1200])}</code>\n\nGửi nội dung mới. Có thể dùng xuống dòng. Với [complete], có thể dùng biến: {{site_name}}, {{product_code}}, {{order_id}}, {{account_name}}, {{stk}}, {{price}}, {{status}}.",
            reply_markup=_kb([[("♻️ KHÔI PHỤC MẶC ĐỊNH", f"bs:orderui:reset:{key}")], [("↩️ DANH SÁCH", "bs:orderui")]])
        )
        await call.answer()

    @dp.message(OrderUiForm.value)
    async def bs_orderui_value(message, state: FSMContext):
        if message.from_user.id not in admin_ids: return
        value = (message.text or "").strip()
        data = await state.get_data()
        key = data.get("order_ui_key")
        valid_custom = any(x.get("key") == key for x in await orderui_custom_items()) if key else False
        if (key not in ORDER_UI_ITEMS and not valid_custom) or not value or len(value) > 3500:
            return await message.answer("Nội dung không hợp lệ hoặc vượt 3.500 ký tự. Hãy gửi lại.")
        await state.update_data(order_ui_value=value)
        await message.answer(
            "🔎 <b>XEM TRƯỚC NỘI DUNG</b>\n\n" + esc(value[:3500]) + "\n\nXác nhận lưu nội dung này?",
            reply_markup=_kb([[("✅ XÁC NHẬN LƯU", "bs:orderui:save"), ("❌ HỦY", "bs:orderui")]])
        )

    @dp.callback_query(F.data == "bs:orderui:save")
    async def bs_orderui_save(call: CallbackQuery, state: FSMContext):
        if not await allowed(call): return
        data = await state.get_data()
        key, value = data.get("order_ui_key"), data.get("order_ui_value")
        valid_custom = any(x.get("key") == key for x in await orderui_custom_items()) if key else False
        if (key not in ORDER_UI_ITEMS and not valid_custom) or not value:
            return await call.answer("Không có nội dung chờ lưu.", show_alert=True)
        async with aiosqlite.connect(db_path) as db:
            await db.execute("INSERT INTO settings(key,value) VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value", ("order_ui_" + key, value))
            await db.commit()
        await audit(call.from_user.id, "edit_order_ui", key, value[:500])
        if on_order_ui_update and key in ORDER_UI_ITEMS:
            on_order_ui_update(key, value)
        await state.clear()
        await safe_edit(call.message, "✅ Đã lưu nội dung cấu hình. Nội dung được lưu bền vững trong database.", reply_markup=_kb([[("🧩 QUẢN LÝ NÚT ĐẶT ĐƠN", "bs:orderui")], [("↩️ BENI SYSTEM", "bs:home")]]))
        await call.answer("Đã lưu.")

    @dp.callback_query(F.data.startswith("bs:orderui:reset:"))
    async def bs_orderui_reset_prompt(call: CallbackQuery, state: FSMContext):
        if not await allowed(call): return
        await state.clear()
        key = call.data.rsplit(":", 1)[-1]
        custom = next((x for x in await orderui_custom_items() if x.get("key") == key), None)
        if key not in ORDER_UI_ITEMS and not custom: return await call.answer("Mục không hợp lệ.", show_alert=True)
        label = ORDER_UI_ITEMS[key][0] if key in ORDER_UI_ITEMS else f"[{key}] • {custom['label']}"
        await safe_edit(call.message, f"⚠️ <b>KHÔI PHỤC MẶC ĐỊNH?</b>\n\n{esc(label)}", reply_markup=_kb([[("✅ XÁC NHẬN", f"bs:orderui:resetconfirm:{key}")], [("❌ HỦY", f"bs:orderui:item:{key}")]]))
        await call.answer()

    @dp.callback_query(F.data.startswith("bs:orderui:resetconfirm:"))
    async def bs_orderui_reset(call: CallbackQuery, state: FSMContext):
        if not await allowed(call): return
        await state.clear()
        key = call.data.rsplit(":", 1)[-1]
        custom = next((x for x in await orderui_custom_items() if x.get("key") == key), None)
        if key not in ORDER_UI_ITEMS and not custom: return await call.answer("Mục không hợp lệ.", show_alert=True)
        default = ORDER_UI_ITEMS[key][1] if key in ORDER_UI_ITEMS else custom.get("default", custom["label"])
        async with aiosqlite.connect(db_path) as db:
            await db.execute("DELETE FROM settings WHERE key=?", ("order_ui_" + key,))
            await db.commit()
        await audit(call.from_user.id, "reset_order_ui", key)
        if on_order_ui_update and key in ORDER_UI_ITEMS:
            on_order_ui_update(key, default)
        await bs_orderui(call, state)


    @dp.message(F.text)
    async def custom_main_menu_handler(message: Message):
        if not message.text: return
        items, _, _ = await menu_load()
        item = next((x for x in items if x.get("label") == message.text and x.get("action") == "custom" and x.get("enabled", True)), None)
        if not item: return
        body = item.get("content") or item.get("label", "")
        markup = _kb([[("🔗 MỞ LIÊN KẾT", "noop")]]) if False else None
        if item.get("url"):
            markup = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text=item.get("button_text") or "🔗 MỞ LIÊN KẾT", url=item["url"])]])
        if item.get("photo"):
            await message.answer_photo(item["photo"], caption=body, reply_markup=markup)
        else:
            await message.answer(body, reply_markup=markup)


    # Called from the legacy admin settings button after its authorization check.
    async def open_panel(call):
        await ensure_tables()
        await home(call)

    return open_panel
