"""BENI SYSTEM admin panel for SHARE-Hi88.

This module adds a safe, persistent inline administration dashboard without
changing customer checkout, wallet, SePay or order processing flows.
"""
import os
import sqlite3
from datetime import datetime
from pathlib import Path

import aiosqlite
from aiogram import F
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup

VERSION = "1.1.0"

class AdminAddForm(StatesGroup):
    user_id = State()

PERMISSIONS = [("users.read", "Xem User"), ("users.manage", "Quản lý User"), ("orders.review", "Duyệt đơn"), ("deposits.review", "Duyệt nạp tiền"), ("wallet.adjust", "Điều chỉnh ví"), ("notifications.send", "Gửi thông báo"), ("reports.read", "Xem thống kê"), ("settings.manage", "Cài đặt hệ thống"), ("admins.manage", "Quản lý Admin"), ("database.backup", "Sao lưu Database"), ("maintenance.manage", "Bảo trì")]


def _kb(rows):
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=label, callback_data=data) for label, data in row]
        for row in rows
    ])


def register_system_admin(dp, *, db_path, main_admin_id, admin_ids, timezone, now_text, esc, admin_keyboard):
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
        elif data.startswith("bs:bot"):
            required = "bot.manage"
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
        await call.message.edit_text(title + "\n\n" + body, reply_markup=_kb(rows))
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
            await call.message.edit_text("👑 <b>QUẢN TRỊ KM SHARE HI88</b>", reply_markup=admin_keyboard())
            await call.answer()

    @dp.callback_query(F.data == "bs:bot")
    async def bs_bot(call: CallbackQuery):
        if not await allowed(call): return
        me = await call.bot.get_me()
        body = (
            f"Trạng thái: 🟢 Đang chạy\nTên Bot: {esc(me.full_name)}\n"
            f"Username: @{esc(me.username)}\nPhiên bản panel: {VERSION}\n\n"
            "Đổi tên, username, ảnh đại diện, mô tả và lệnh Bot được thực hiện an toàn qua "
            "BotFather. Không nhập Bot Token vào chat. Bật/tắt toàn bộ tiến trình cần thao tác Railway."
        )
        await show(call, "🤖 <b>BOT SETTINGS</b>", body, [
            [("🔄 KIỂM TRA TRẠNG THÁI", "bs:bot")],
            [("↩️ QUAY LẠI", "bs:home")]
        ])
        await audit(call.from_user.id, "view_bot_settings")

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
        await call.message.edit_text("➕ <b>THÊM ADMIN PHỤ</b>\nGửi Telegram ID dạng số.", reply_markup=_kb([[("❌ HỦY", "bs:admins")]]))
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
        await audit(message.from_user.id, "add_sub_admin", str(uid))
        await state.clear()
        await message.answer(f"✅ Đã thêm Admin phụ <code>{uid}</code>. Mặc định tất cả quyền đều TẮT.", reply_markup=_kb([[("👑 ADMIN MANAGER", "bs:admins"), ("🔐 PHÂN QUYỀN", "bs:perms")]]))

    @dp.callback_query(F.data.startswith("bs:admin:"))
    async def bs_admin_detail(call: CallbackQuery):
        if not await allowed(call): return
        uid = int(call.data.split(":")[-1])
        if uid == main_admin_id: return await call.answer("Không thể sửa Admin chính.", show_alert=True)
        await call.message.edit_text(f"👤 <b>ADMIN PHỤ</b>\nID: <code>{uid}</code>", reply_markup=_kb([[("🔐 CẤU HÌNH QUYỀN", f"bs:permuser:{uid}")], [("🗑 XÓA ADMIN", f"bs:admindel:{uid}")], [("↩️ DANH SÁCH", "bs:admins")]]))
        await call.answer()

    @dp.callback_query(F.data.startswith("bs:admindel:"))
    async def bs_admin_delete_prompt(call: CallbackQuery):
        if not await allowed(call): return
        if call.from_user.id != main_admin_id: return await call.answer("Chỉ Admin chính được xóa.", show_alert=True)
        uid = int(call.data.split(":")[-1])
        if uid == main_admin_id: return await call.answer("Không thể xóa Admin chính.", show_alert=True)
        await call.message.edit_text(f"⚠️ <b>XÁC NHẬN XÓA ADMIN?</b>\nID: <code>{uid}</code>", reply_markup=_kb([[("⚠️ XÁC NHẬN XÓA", f"bs:admindelconfirm:{uid}"), ("❌ HỦY", f"bs:admin:{uid}")]]))
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
        await audit(call.from_user.id, "delete_sub_admin", str(uid))
        await call.message.edit_text("✅ Admin phụ đã bị xóa và quyền truy cập đã thu hồi.", reply_markup=_kb([[("👑 ADMIN MANAGER", "bs:admins"), ("↩️ BENI SYSTEM", "bs:home")]]))
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
        await call.message.edit_text(
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
        await call.message.edit_text(
            f"🎨 <b>UI MANAGER</b>\\n\\nChế độ hiển thị đã lưu: <b>{esc(theme.upper())}</b>\\n\\nLưu ý: theme chỉ là cấu hình nội bộ; Telegram chat không thể đổi màu giao diện ứng dụng Telegram.",
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
        await call.message.edit_text(
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
        await call.message.edit_text(
            "📢 <b>NOTIFICATION MANAGER</b>\\n\\n"
            f"Kênh thông báo đơn: <code>{esc(os.getenv('ORDER_ANNOUNCE_CHANNEL', '@sharegame1'))}</code>\\n"
            f"Thông báo tự động: {'🟢 BẬT' if enabled == '1' else '⚫ TẮT'}\\n\\n"
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
        await show(call, "📜 <b>LỊCH SỬ SAO LƯU</b>", body, [[("↩️ DATABASE", "bs:db")]])

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
        await call.message.edit_text(
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

    # Called from the legacy admin settings button after its authorization check.
    async def open_panel(call):
        await ensure_tables()
        await home(call)

    return open_panel
