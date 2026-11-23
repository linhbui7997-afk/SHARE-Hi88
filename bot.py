import asyncio
import html
import logging
import os
import re
import secrets
from datetime import datetime
from zoneinfo import ZoneInfo

import aiosqlite
from aiohttp import web
from aiogram import Bot, Dispatcher, F
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.filters import Command, CommandStart
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.types import (
    BotCommand, BotCommandScopeChat, BotCommandScopeDefault,
    CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup,
    KeyboardButton, ReplyKeyboardMarkup, Message
)

logging.basicConfig(level=os.getenv("LOG_LEVEL", "INFO"))
log = logging.getLogger("share-hi88")

BOT_TOKEN = os.getenv("BOT_TOKEN", "")
MAIN_ADMIN_ID = int(os.getenv("MAIN_ADMIN_ID", "6369714149"))
ADMIN_IDS = {MAIN_ADMIN_ID} | {int(x.strip()) for x in os.getenv("ADMIN_IDS", "").split(",") if x.strip().isdigit()}
PORT = int(os.getenv("PORT", "8080"))
DB_PATH = os.getenv("DATABASE_PATH", "share_hi88.sqlite3")
PRODUCT_NAME = os.getenv("PRODUCT_NAME", "HI88")
PRODUCT_LABEL = os.getenv("PRODUCT_LABEL", "HI88 [108/50K]")
PRODUCT_PRICE = int(os.getenv("PRODUCT_PRICE_VND", "50000"))
PRODUCT_ACTIVE = os.getenv("PRODUCT_ACTIVE", "true").lower() == "true"
MIN_TOTAL_DEPOSIT = int(os.getenv("MIN_TOTAL_DEPOSIT_VND", "500000"))
MIN_DEPOSIT_COUNT = int(os.getenv("MIN_DEPOSIT_COUNT", "3"))
SEPAY_API_KEY = os.getenv("SEPAY_API_KEY", "")
SEPAY_BANK = os.getenv("SEPAY_BANK_NAME", "ACB")
SEPAY_ACCOUNT = os.getenv("SEPAY_ACCOUNT_NUMBER", "28682791")
SEPAY_ACCOUNT_NAME = os.getenv("SEPAY_ACCOUNT_NAME", "BUI HOANG YEN LINH")
SEPAY_QR_TEMPLATE = os.getenv("SEPAY_QR_TEMPLATE", "compact2")
ORDER_ANNOUNCE_CHANNEL = os.getenv("ORDER_ANNOUNCE_CHANNEL", "@sharegame1")
SEPAY_PREFIX = re.sub(r"[^A-Z0-9]", "", os.getenv("SEPAY_CODE_PREFIX", "SH").upper())[:5] or "SH"
TZ = ZoneInfo("Asia/Ho_Chi_Minh")
ORDER_UI_DEFAULTS = {"hot":"🎁 ĐẶT ĐƠN KM SHARE","hi88":"🎁 SHARE Hi88","qq88":"🎁 SHARE QQ88","hi88_name":"👤 Vui lòng nhập tên tài khoản Hi88:","hi88_stk":"🏦 Nhập đúng 4 số cuối STK ngân hàng đã liên kết với tài khoản Hi88:","qq88_name":"👤 Vui lòng nhập tên tài khoản QQ88:","qq88_stk":"🏦 Nhập đúng 6 số cuối STK ngân hàng đã liên kết với tài khoản QQ88:","confirm":"✅ XÁC NHẬN THANH TOÁN","complete":"🎉 <b>ĐẶT ĐƠN THÀNH CÔNG</b>\n\n━━━━━━━━━━━━━━━━━━\n✅ Thanh toán đơn hàng thành công!\n\n🎁 Sản phẩm: <b>SHARE {site_name}</b>\n🎟 Gói khuyến mãi: <b>{product_code} điểm</b>\n🧾 Mã đơn: <code>{order_id}</code>\n👤 Tài khoản: <code>{account_name}</code>\n🏦 {digits} số cuối STK: <code>{stk}</code>\n💰 Đã thanh toán: <b>{price}</b>\n⏳ Trạng thái: <b>{status}</b>\n━━━━━━━━━━━━━━━━━━\n\n📢 Đơn hàng đã được gửi đến quản trị viên. Vui lòng chờ thông báo khi đơn được xử lý hoàn tất."}
ORDER_UI_CACHE = dict(ORDER_UI_DEFAULTS)

if not BOT_TOKEN:
    raise RuntimeError("Missing BOT_TOKEN environment variable")

bot = Bot(BOT_TOKEN, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
dp = Dispatcher(storage=MemoryStorage())


def now_text():
    return datetime.now(TZ).strftime("%d/%m/%y • %H:%M:%S")


def money(value):
    return f"{int(value):,}".replace(",", ".") + "đ"


def required_stk_digits(site_name):
    return 6 if "QQ88" in str(site_name).upper() else 4


def esc(value):
    return html.escape(str(value or ""))


async def init_db():
    os.makedirs(os.path.dirname(DB_PATH) or ".", exist_ok=True)
    async with aiosqlite.connect(DB_PATH) as db:
        await db.executescript("""
        PRAGMA journal_mode=WAL;
        CREATE TABLE IF NOT EXISTS users(
          user_id INTEGER PRIMARY KEY, username TEXT, full_name TEXT,
          wallet INTEGER NOT NULL DEFAULT 0, created_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS deposits(
          id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER NOT NULL,
          code TEXT UNIQUE NOT NULL, amount INTEGER NOT NULL,
          status TEXT NOT NULL DEFAULT 'pending', sepay_id TEXT UNIQUE,
          created_at TEXT NOT NULL, paid_at TEXT
        );
        CREATE TABLE IF NOT EXISTS orders(
          id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER NOT NULL,
          product TEXT NOT NULL, account_name TEXT NOT NULL, stk_last4 TEXT NOT NULL,
          price INTEGER NOT NULL, status TEXT NOT NULL DEFAULT 'pending',
          created_at TEXT NOT NULL, updated_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS transactions(
          id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER NOT NULL,
          kind TEXT NOT NULL, amount INTEGER NOT NULL, note TEXT,
          created_at TEXT NOT NULL, ref TEXT
        );
        CREATE TABLE IF NOT EXISTS sepay_events(
          sepay_id TEXT PRIMARY KEY, received_at TEXT NOT NULL, payload TEXT
        );
        CREATE TABLE IF NOT EXISTS settings(key TEXT PRIMARY KEY, value TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS admins(user_id INTEGER PRIMARY KEY, role TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS order_announcements(
          order_id INTEGER PRIMARY KEY,
          status TEXT NOT NULL DEFAULT 'pending',
          message_id INTEGER,
          updated_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS sites(
          id INTEGER PRIMARY KEY AUTOINCREMENT,
          name TEXT NOT NULL UNIQUE,
          product_code TEXT NOT NULL,
          price INTEGER NOT NULL DEFAULT 50000,
          description TEXT NOT NULL DEFAULT '',
          active INTEGER NOT NULL DEFAULT 1,
          created_at TEXT NOT NULL
        );
        """)
        # Safe schema migration for databases created by older bot versions.
        cur = await db.execute("PRAGMA table_info(orders)")
        columns = {row[1] for row in await cur.fetchall()}
        if "product_code" not in columns:
            await db.execute("ALTER TABLE orders ADD COLUMN product_code TEXT NOT NULL DEFAULT ''")
        cur = await db.execute("PRAGMA table_info(sites)")
        site_columns = {row[1] for row in await cur.fetchall()}
        if "description" not in site_columns:
            await db.execute("ALTER TABLE sites ADD COLUMN description TEXT NOT NULL DEFAULT ''")
        await db.execute("INSERT OR IGNORE INTO admins(user_id,role) VALUES(?, 'main')", (MAIN_ADMIN_ID,))
        cur = await db.execute("SELECT user_id FROM admins WHERE role='sub'")
        ADMIN_IDS.update(int(row[0]) for row in await cur.fetchall())
        cur = await db.execute("SELECT COUNT(*) FROM sites")
        if (await cur.fetchone())[0] == 0:
            await db.executemany(
                "INSERT INTO sites(name,product_code,price,active,created_at) VALUES(?,?,?,?,?)",
                [("HI88", "108", 50000, 1, now_text()), ("QQ88", "108", 50000, 1, now_text())]
            )
        await db.commit()
        cur = await db.execute("SELECT key,value FROM settings WHERE key LIKE 'order_ui_%'")
        for key, value in await cur.fetchall():
            tag = key.removeprefix("order_ui_")
            if tag in ORDER_UI_DEFAULTS: ORDER_UI_CACHE[tag] = value


async def ensure_user(user):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "INSERT INTO users(user_id,username,full_name,created_at) VALUES(?,?,?,?) "
            "ON CONFLICT(user_id) DO UPDATE SET username=excluded.username,full_name=excluded.full_name",
            (user.id, user.username or "", user.full_name or "", now_text())
        )
        await db.commit()


async def get_user(user_id):
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        cur = await db.execute("SELECT * FROM users WHERE user_id=?", (user_id,))
        return await cur.fetchone()


async def setting(key, default=""):
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute("SELECT value FROM settings WHERE key=?", (key,))
        row = await cur.fetchone()
        return row[0] if row else default


async def set_setting(key, value):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("INSERT INTO settings(key,value) VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value", (key, str(value)))
        await db.commit()


def main_kb(user_id=None):
    rows = [
        [KeyboardButton(text=ORDER_UI_CACHE["hot"]), KeyboardButton(text="💳 NẠP TIỀN")],
        [KeyboardButton(text="👛 SỐ DƯ TÀI KHOẢN"), KeyboardButton(text="📊 LỊCH SỬ GIAO DỊCH")],
        [KeyboardButton(text="📞 LIÊN HỆ ADMIN"), KeyboardButton(text="📖 HƯỚNG DẪN")],
    ]
    if user_id is not None and is_admin(user_id):
        rows.append([KeyboardButton(text="🛠 QUẢN TRỊ")])
    return ReplyKeyboardMarkup(
        keyboard=rows, resize_keyboard=True, is_persistent=True,
        input_field_placeholder="Chọn chức năng bạn muốn sử dụng"
    )


def back_kb():
    return InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="↩️ QUAY LẠI MENU", callback_data="home")]])


def admin_kb():
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="⏳ ĐANG ĐỢI XỬ LÝ", callback_data="adm_waiting"),
         InlineKeyboardButton(text="👥 QUẢN LÝ KHÁCH HÀNG", callback_data="adm_users")],
        [InlineKeyboardButton(text="📦 QUẢN LÝ ĐƠN HÀNG", callback_data="adm_orders"),
         InlineKeyboardButton(text="💳 YÊU CẦU NẠP TIỀN", callback_data="adm_deposits")],
        [InlineKeyboardButton(text="💰 ĐIỀU CHỈNH SỐ DƯ VÍ", callback_data="adm_wallet"),
         InlineKeyboardButton(text="🏦 TÀI KHOẢN NHẬN TIỀN", callback_data="adm_stks")],
        [InlineKeyboardButton(text="🛍️ SẢN PHẨM – GIÁ", callback_data="adm_price"),
         InlineKeyboardButton(text="➕ QUẢN LÝ SITE", callback_data="adm_sites")],
        [InlineKeyboardButton(text="📢 THÔNG BÁO TOÀN THÀNH VIÊN", callback_data="broadcast_start")],
        [InlineKeyboardButton(text="📨 THÔNG BÁO ID RIÊNG", callback_data="adm_notify"),
         InlineKeyboardButton(text="📊 BÁO CÁO", callback_data="adm_report")],
        [InlineKeyboardButton(text="⚙️ BENI SYSTEM – CÀI ĐẶT HỆ THỐNG", callback_data="adm_settings")],
    ])


def is_admin(user_id):
    return user_id in ADMIN_IDS


PUBLIC_BOT_COMMANDS = [
    BotCommand(command="start", description="🚀 Bắt đầu sử dụng BOT"),
    BotCommand(command="help", description="📖 Hướng dẫn sử dụng BOT"),
]
ADMIN_BOT_COMMANDS = [
    BotCommand(command="admin", description="🛠 Bảng điều khiển QTV"),
    BotCommand(command="qtv", description="⏳ Đơn hàng chờ xử lý"),
    BotCommand(command="xuly", description="📦 Quản lý đơn hàng"),
    BotCommand(command="list", description="👥 Quản lý khách hàng"),
    BotCommand(command="sodu", description="💰 Quản lý số dư"),
    BotCommand(command="tb", description="📢 Gửi thông báo"),
    BotCommand(command="tke", description="📊 Thống kê hệ thống"),
    BotCommand(command="caidat", description="⚙️ Cài đặt hệ thống"),
]


async def sync_command_menus():
    try:
        await bot.set_my_commands(PUBLIC_BOT_COMMANDS, scope=BotCommandScopeDefault())
    except Exception:
        log.exception("Could not set the public Telegram command menu")
    for admin_id in sorted(ADMIN_IDS):
        try:
            await bot.set_my_commands(ADMIN_BOT_COMMANDS, scope=BotCommandScopeChat(chat_id=admin_id))
        except Exception:
            log.exception("Could not set the admin command menu for %s", admin_id)


async def clear_admin_command_menu(user_id: int):
    try:
        await bot.delete_my_commands(scope=BotCommandScopeChat(chat_id=user_id))
    except Exception:
        log.exception("Could not clear the admin command menu for %s", user_id)


async def has_admin_permission(user_id, permission):
    if user_id == MAIN_ADMIN_ID:
        return True
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute(
            "SELECT enabled FROM admin_permissions WHERE user_id=? AND permission=?",
            (user_id, permission)
        )
        row = await cur.fetchone()
    return bool(row and row[0])


async def current_price():
    try:
        return int(await setting('product_price_vnd', str(PRODUCT_PRICE)))
    except (TypeError, ValueError):
        return PRODUCT_PRICE


async def home_text(user):
    row = await get_user(user.id)
    wallet = row["wallet"] if row else 0
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute(
            "SELECT COUNT(*) FROM orders WHERE user_id=? AND status IN ('pending','processing')",
            (user.id,)
        )
        waiting = (await cur.fetchone())[0]

    now = datetime.now(TZ)
    date_text = now.strftime("%d/%m/%y")
    time_text = now.strftime("%H:%M:%S")
    username = f"@{esc(user.username)}" if user.username else esc(user.first_name or "bạn")
    return (
        f"📆 {date_text}   ⏰ {time_text} (GMT+7)\n"
        f"💬 Xin chào, {username}\n"
        f"⊕ Số Dư Ví : <b>{money(wallet)}</b>\n"
        f"✐ ID : <code>{user.id}</code>\n"
        f"⏳ Đơn Hàng : <b>{waiting}</b>"
    )


async def show_home(target, user):
    text = await home_text(user)
    if isinstance(target, CallbackQuery):
        await target.message.answer(text, reply_markup=main_kb(user.id))
        await target.answer()
    else:
        await target.answer(text, reply_markup=main_kb(user.id))


class OrderForm(StatesGroup):
    account_name = State()
    stk_last4 = State()


class DepositForm(StatesGroup):
    amount = State()


class AdminWalletForm(StatesGroup):
    user_id = State()
    amount = State()
    reason = State()


class AdminPriceForm(StatesGroup):
    price = State()


class AdminStkForm(StatesGroup):
    slot = State()
    last4 = State()
    label = State()


class AdminSiteForm(StatesGroup):
    name = State()
    code = State()
    price = State()
    description = State()


class SitePriceForm(StatesGroup):
    site_id = State()
    code = State()
    price = State()
    description = State()


class NotifyForm(StatesGroup):
    recipient = State()
    content = State()
    confirm = State()

class BroadcastForm(StatesGroup):
    content = State()
    confirm = State()


class OrderForm(StatesGroup):
    account_name = State()
    stk_last4 = State()
    confirm = State()


@dp.message(CommandStart())
async def start(message: Message, state: FSMContext):
    await state.clear()
    await ensure_user(message.from_user)
    await show_home(message, message.from_user)


@dp.message(Command("help"))
async def help_command(message: Message):
    await message.answer(
        "📖 <b>HƯỚNG DẪN SỬ DỤNG SHARE-Hi88</b>\n\n"
        "🎁 ĐẶT ĐƠN KM SHARE — Chọn chương trình và gửi thông tin tài khoản.\n"
        "💰 NẠP TIỀN — Tạo mã nạp và thanh toán theo QR.\n"
        "👤 TÀI KHOẢN — Xem ID và số dư ví.\n"
        "📜 LỊCH SỬ ĐƠN HÀNG — Theo dõi trạng thái đơn.\n"
        "💳 LỊCH SỬ GIAO DỊCH — Xem lịch sử nạp và biến động ví.\n\n"
        "Chọn chức năng tại các nút bên dưới.",
        reply_markup=main_kb(message.from_user.id)
    )


@dp.message(lambda message: message.text == ORDER_UI_CACHE["hot"])
async def reply_buy(message: Message, state: FSMContext):
    if (message.text or "").strip() != ORDER_UI_CACHE["hot"]: return
    await state.clear()
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute("SELECT id,name,product_code,price FROM sites WHERE active=1 ORDER BY id")
        sites = await cur.fetchall()
    buttons, row = [], []
    for site in sites:
        ui_key = "qq88" if "QQ88" in site[1].upper() else "hi88"
        row.append(InlineKeyboardButton(text=ORDER_UI_CACHE[ui_key], callback_data=f"site:{site[0]}"))
        if len(row) == 2:
            buttons.append(row)
            row = []
    if row:
        buttons.append(row)
    buttons.append([InlineKeyboardButton(text="↩️ QUAY LẠI MENU", callback_data="home")])
    await message.answer(
        "🎁 <b>ĐẶT ĐƠN KM SHARE</b>\n\n"
        "━━━━━━━━━━━━━━━━━━\n\n"
        "🎉 <b>CHỌN CHƯƠNG TRÌNH KHUYẾN MÃI</b>\n\n"
        "➜ 🎁 SHARE Hi88 [108 điểm / 50K]\n"
        "➜ 🎁 SHARE QQ88 [108 điểm / 50K]\n\n"
        "━━━━━━━━━━━━━━━━━━\n\n"
        "👇 Vui lòng chọn chương trình muốn tham gia bên dưới.\n\n"
        "📌 <b>HƯỚNG DẪN ĐẶT ĐƠN</b>\n"
        "🔹 Hi88: Tên tài khoản + 4 số cuối STK đã liên kết.\n"
        "🔹 QQ88: Tên tài khoản + 6 số cuối STK đã liên kết.\n\n"
        "💰 Giá mỗi đơn: <b>50.000đ</b>",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=buttons)
    )


@dp.message(F.text == "💳 NẠP TIỀN")
async def reply_deposit(message: Message, state: FSMContext):
    await state.set_state(DepositForm.amount)
    await message.answer(
        "💳 <b>NẠP TIỀN VÀO VÍ</b>\n\n"
        "Vui lòng nhập số tiền muốn nạp.\n"
        "• Tối thiểu: <b>30.000đ</b>\n"
        "• Tối đa: <b>100.000.000đ</b>\n\n"
        "Ví dụ: <code>50000</code>"
    )


@dp.message(F.text == "👛 SỐ DƯ TÀI KHOẢN")
async def reply_account(message: Message):
    row = await get_user(message.from_user.id)
    await message.answer(
        "👛 <b>SỐ DƯ TÀI KHOẢN</b>\n\n"
        f"🆔 ID: <code>{message.from_user.id}</code>\n"
        f"👤 Username: @{esc(message.from_user.username or 'chưa đặt')}\n"
        f"💰 Số dư ví: <b>{money(row['wallet'] if row else 0)}</b>",
        reply_markup=main_kb(message.from_user.id)
    )


@dp.message(F.text == "📊 LỊCH SỬ GIAO DỊCH")
async def reply_history(message: Message):
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute("SELECT code,amount,status,created_at FROM deposits WHERE user_id=? ORDER BY id DESC LIMIT 10", (message.from_user.id,))
        deposits = await cur.fetchall()
        cur = await db.execute("SELECT id,product,product_code,price,status,created_at FROM orders WHERE user_id=? ORDER BY id DESC LIMIT 10", (message.from_user.id,))
        orders = await cur.fetchall()
    lines = ["📊 <b>LỊCH SỬ GIAO DỊCH</b>", "", "💳 <b>LỊCH SỬ NẠP TIỀN</b>"]
    lines += [f"• {esc(d[0])} | {money(d[1])} | {esc(d[2])} | {esc(d[3])}" for d in deposits] or ["• Chưa có lần nạp."]
    lines += ["", "🎁 <b>LỊCH SỬ ĐƠN HÀNG</b>"]
    lines += [f"• ORD-{o[0]:06d} | {esc(o[1])} / {esc(o[2])} | {money(o[3])} | {esc(o[4])}" for o in orders] or ["• Chưa có đơn hàng."]
    await message.answer("\n".join(lines), reply_markup=main_kb(message.from_user.id))


@dp.message(F.text == "📞 LIÊN HỆ ADMIN")
async def reply_support(message: Message):
    url = os.getenv("SUPPORT_URL", "https://t.me/nilatao")
    await message.answer("📞 <b>LIÊN HỆ HỖ TRỢ</b>\nNhấn nút bên dưới để liên hệ quản trị viên.",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="📩 NHẮN ADMIN", url=url)]]))


@dp.message(F.text == "📖 HƯỚNG DẪN")
async def reply_guide(message: Message):
    await message.answer(
        "📖 <b>HƯỚNG DẪN MUA HÀNG</b>\n\n"
        "1️⃣ Chọn 🎁 ĐẶT ĐƠN KM SHARE.\n"
        "2️⃣ Chọn Hi88 hoặc QQ88.\n"
        "3️⃣ Nhập tên tài khoản và số cuối STK đã liên kết.\n"
        "   • Hi88: đúng 4 số.\n"
        "   • QQ88: đúng 6 số.\n"
        "4️⃣ Kiểm tra thông tin và xác nhận thanh toán.\n"
        "5️⃣ Đơn sẽ vào hàng chờ QTV xử lý.\n\n"
        "💰 Giá mỗi đơn: <b>50.000đ</b>",
        reply_markup=main_kb(message.from_user.id)
    )


@dp.message(F.text == "🛠 QUẢN TRỊ")
async def reply_admin(message: Message):
    if not is_admin(message.from_user.id):
        return await message.answer("⛔ Bạn không có quyền truy cập.")
    await message.answer("🛠 <b>BẢNG QUẢN TRỊ SHARE-Hi88</b>\nChọn chức năng:", reply_markup=admin_kb())


@dp.message(Command("admin"))
async def admin(message: Message):
    if not is_admin(message.from_user.id):
        return await message.answer("⛔ Bạn không có quyền truy cập.")
    await message.answer("👑 <b>QUẢN TRỊ KM SHARE HI88</b>\nChọn chức năng:", reply_markup=admin_kb())


async def admin_command_shortcut(message: Message, title: str, callback_data: str, permission: str):
    user_id = message.from_user.id
    if not is_admin(user_id):
        return await message.answer("⛔ Bạn không có quyền truy cập.")
    if not await has_admin_permission(user_id, permission):
        return await message.answer("⛔ Bạn chưa được cấp quyền: " + permission)
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=title, callback_data=callback_data)],
        [InlineKeyboardButton(text="↩️ MỞ BẢNG QTV", callback_data="admin_home")]
    ])
    await message.answer(title, reply_markup=keyboard)


@dp.message(Command("qtv"))
async def command_qtv(message: Message):
    await admin_command_shortcut(message, "⏳ ĐƠN HÀNG CHỜ XỬ LÝ", "adm_waiting", "orders.review")


@dp.message(Command("xuly"))
async def command_xuly(message: Message):
    await admin_command_shortcut(message, "📦 QUẢN LÝ ĐƠN HÀNG", "adm_orders", "orders.review")


@dp.message(Command("list"))
async def command_list(message: Message):
    await admin_command_shortcut(message, "👥 QUẢN LÝ KHÁCH HÀNG", "adm_users", "users.manage")


@dp.message(Command("sodu"))
async def command_sodu(message: Message):
    await admin_command_shortcut(message, "💰 QUẢN LÝ SỐ DƯ", "adm_wallet", "wallet.adjust")


@dp.message(Command("tb"))
async def command_tb(message: Message):
    await admin_command_shortcut(message, "📢 GỬI THÔNG BÁO", "broadcast_start", "notifications.send")


@dp.message(Command("tke"))
async def command_tke(message: Message):
    await admin_command_shortcut(message, "📊 THỐNG KÊ HỆ THỐNG", "adm_report", "reports.read")


@dp.message(Command("caidat"))
async def command_caidat(message: Message):
    await admin_command_shortcut(message, "⚙️ CÀI ĐẶT HỆ THỐNG", "adm_settings", "settings.manage")


@dp.callback_query(F.data == "home")
async def cb_home(call: CallbackQuery, state: FSMContext):
    await state.clear()
    await show_home(call, call.from_user)


@dp.callback_query(F.data == "buy")
async def buy(call: CallbackQuery, state: FSMContext):
    await state.clear()
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute("SELECT id,name,product_code,price FROM sites WHERE active=1 ORDER BY id")
        sites = await cur.fetchall()
    buttons = []
    row = []
    for r in sites:
        label = f"🎁 SHARE {r[1].title()}"
        ui_key = "qq88" if "QQ88" in str(r[1]).upper() else "hi88"
        row.append(InlineKeyboardButton(text=ORDER_UI_CACHE[ui_key], callback_data=f"site:{r[0]}"))
        if len(row) == 2:
            buttons.append(row)
            row = []
    if row:
        buttons.append(row)
    buttons.append([InlineKeyboardButton(text="🏠 VỀ MENU CHÍNH", callback_data="home")])
    await call.message.edit_text(
        "🎁 <b>ĐẶT ĐƠN KM SHARE</b>\n\n"
        "━━━━━━━━━━━━━━━━━━\n\n"
        "🎉 <b>CHỌN CHƯƠNG TRÌNH KHUYẾN MÃI</b>\n\n"
        "➜ 🎁 SHARE Hi88 [108 điểm / 50K]\n"
        "➜ 🎁 SHARE QQ88 [108 điểm / 50K]\n\n"
        "━━━━━━━━━━━━━━━━━━\n\n"
        "👇 Vui lòng chọn chương trình muốn tham gia bên dưới.\n\n"
        "📌 <b>HƯỚNG DẪN ĐẶT ĐƠN</b>\n"
        "🔹 Hi88: Tên tài khoản + 4 số cuối STK đã liên kết.\n"
        "🔹 QQ88: Tên tài khoản + 6 số cuối STK đã liên kết.\n\n"
        "💰 Giá mỗi đơn: <b>50.000đ</b>",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=buttons)
    )
    await call.answer()


@dp.callback_query(F.data.startswith("site:"))
async def choose_site(call: CallbackQuery, state: FSMContext):
    try:
        site_id = int(call.data.split(":")[1])
    except (ValueError, IndexError):
        return await call.answer("Site không hợp lệ.", show_alert=True)
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute("SELECT id,name,product_code,price,description FROM sites WHERE id=? AND active=1", (site_id,))
        site = await cur.fetchone()
    if not site:
        return await call.answer("Site hiện không hoạt động.", show_alert=True)
    await state.clear()
    await state.update_data(site_id=site[0], site_name=site[1], product_code=site[2], price=site[3], description=site[4])
    await call.message.edit_text(
        "🎁 <b>ĐẶT ĐƠN KM SHARE</b>\n"
        f"🎁 <b>{esc(site[1])} • {esc(site[2])} ĐIỂM</b>\n"
        f"🎟 Mã sản phẩm: <code>{esc(site[2])}</code>\n"
        f"💰 Giá mỗi đơn: <b>{money(site[3])}</b>\n"
        + (f"📝 Mô tả: {esc(site[4])}\n" if site[4] else "") + "\n"
        "━━━━━━━━━━━━━━━━━━\n"
        "📌 <b>HƯỚNG DẪN</b>\n"
        "Sau khi tiếp tục, vui lòng nhập tên tài khoản game và số cuối STK ngân hàng đã liên kết.\n"
        f"🔹 {esc(site[1])}: cần đúng {required_stk_digits(site[1])} số cuối STK.\n\n"
        "⚠️ Kiểm tra kỹ thông tin trước khi xác nhận thanh toán.\n"
        "━━━━━━━━━━━━━━━━━━",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="🛒 TIẾP TỤC", callback_data="order_begin")],
            [InlineKeyboardButton(text="↩️ QUAY LẠI BƯỚC 1", callback_data="buy")],
            [InlineKeyboardButton(text="🏠 VỀ MENU CHÍNH", callback_data="home")]
        ])
    )
    await call.answer()


@dp.callback_query(F.data == "order_begin")
async def order_begin(call: CallbackQuery, state: FSMContext):
    data = await state.get_data()
    if not data.get("site_id"):
        return await call.answer("Vui lòng chọn site trước.", show_alert=True)
    await state.set_state(OrderForm.account_name)
    await call.message.edit_text(
        f"📝 <b>THÔNG TIN ĐẶT HÀNG • {esc(data['site_name'])}</b>\n\n"
        f"{esc(ORDER_UI_CACHE['qq88_name' if 'QQ88' in data['site_name'].upper() else 'hi88_name'])}\n\n"
        "📌 Ví dụ: <code>nguyenvana</code>\n"
        "━━━━━━━━━━━━━━━━━━",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="↩️ QUAY LẠI BƯỚC 1", callback_data="buy")],
            [InlineKeyboardButton(text="🏠 VỀ MENU CHÍNH", callback_data="home")]
        ])
    )
    await call.answer()


@dp.message(OrderForm.account_name)
async def order_account(message: Message, state: FSMContext):
    name = (message.text or "").strip()
    if len(name) < 2 or len(name) > 64:
        return await message.answer("⚠️ Tên tài khoản không hợp lệ. Vui lòng nhập lại.")
    await state.update_data(account_name=name)
    await state.set_state(OrderForm.stk_last4)
    data = await state.get_data()
    await message.answer(
        f"{esc(ORDER_UI_CACHE['qq88_stk' if 'QQ88' in data.get('site_name','').upper() else 'hi88_stk'])}\n"
        f"<i>Hi88 yêu cầu 4 số cuối • QQ88 yêu cầu 6 số cuối.</i>",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="↩️ QUAY LẠI BƯỚC 2", callback_data="order_back_account")],
            [InlineKeyboardButton(text="🏠 VỀ MENU CHÍNH", callback_data="home")]
        ])
    )


@dp.message(OrderForm.stk_last4)
async def order_stk(message: Message, state: FSMContext):
    digits = re.sub(r"\D", "", message.text or "")
    data = await state.get_data()
    expected_digits = required_stk_digits(data.get("site_name", ""))
    if len(digits) != expected_digits:
        return await message.answer(
            f"⚠️ {data.get('site_name', 'Site')} yêu cầu đúng {expected_digits} chữ số cuối STK đã liên kết. "
            "Vui lòng nhập lại."
        )
    price = int(data["price"])
    await state.update_data(stk_last4=digits)
    await state.set_state(OrderForm.confirm)
    await message.answer(
        "📋 <b>XÁC NHẬN THÔNG TIN ĐƠN HÀNG</b>\n\n"
        "━━━━━━━━━━━━━━━━━━\n"
        f"🎁 Sản phẩm: <b>SHARE {esc(data['site_name'].title())}</b>\n"
        f"🎟 Gói khuyến mãi: <b>{esc(data['product_code'])} điểm</b>\n"
        f"👤 Tài khoản: <code>{esc(data['account_name'])}</code>\n"
        f"🏦 {expected_digits} số cuối STK: <code>{digits}</code>\n"
        f"💰 Giá đơn hàng: <b>{money(price)}</b>\n"
        "━━━━━━━━━━━━━━━━━━\n\n"
        "Vui lòng kiểm tra chính xác thông tin trước khi thanh toán.",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text=ORDER_UI_CACHE["confirm"], callback_data="order_confirm")],
            [InlineKeyboardButton(text="↩️ QUAY LẠI BƯỚC 2", callback_data="order_back_stk")],
            [InlineKeyboardButton(text="🏠 VỀ MENU CHÍNH", callback_data="home")]
        ])
    )


@dp.callback_query(F.data == "order_back_account")
async def order_back_account(call: CallbackQuery, state: FSMContext):
    data = await state.get_data()
    if not data.get("site_id"):
        return await call.answer("Phiên đặt đơn đã hết hạn.", show_alert=True)
    await state.set_state(OrderForm.account_name)
    await call.message.edit_text(
        f"📝 <b>THÔNG TIN ĐẶT HÀNG • {esc(data['site_name'])}</b>\n\n"
        f"{esc(ORDER_UI_CACHE['qq88_name' if 'QQ88' in data['site_name'].upper() else 'hi88_name'])}",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="↩️ QUAY LẠI BƯỚC 1", callback_data="buy")],
            [InlineKeyboardButton(text="🏠 VỀ MENU CHÍNH", callback_data="home")]
        ])
    )
    await call.answer()


@dp.callback_query(F.data == "order_back_stk")
async def order_back_stk(call: CallbackQuery, state: FSMContext):
    data = await state.get_data()
    if not data.get("account_name"):
        return await call.answer("Vui lòng nhập lại thông tin tài khoản.", show_alert=True)
    await state.set_state(OrderForm.stk_last4)
    await call.message.edit_text(
        f"{esc(ORDER_UI_CACHE['qq88_stk' if 'QQ88' in data.get('site_name', '').upper() else 'hi88_stk'])}",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="↩️ QUAY LẠI BƯỚC 2", callback_data="order_back_account")],
            [InlineKeyboardButton(text="🏠 VỀ MENU CHÍNH", callback_data="home")]
        ])
    )
    await call.answer()


@dp.callback_query(F.data == "order_retry_confirm")
async def order_retry_confirm(call: CallbackQuery, state: FSMContext):
    if await state.get_state() != OrderForm.confirm.state:
        return await call.answer("Phiên đặt đơn đã hết hạn. Vui lòng thao tác lại.", show_alert=True)
    data = await state.get_data()
    await call.message.edit_text(
        "🔎 <b>XÁC NHẬN THANH TOÁN</b>\n\n"
        f"🎮 Site: <b>{esc(data['site_name'])}</b>\n"
        f"🎟 Mã sản phẩm: <code>{esc(data['product_code'])}</code>\n"
        f"👤 Tài khoản game: <b>{esc(data['account_name'])}</b>\n"
        f"🏦 {required_stk_digits(data.get('site_name', ''))} số cuối STK: <code>{data['stk_last4']}</code>\n"
        f"💰 Giá đơn: <b>{money(data['price'])}</b>\n\n"
        "Thanh toán bằng số dư có trong bot. Nhấn xác nhận để kiểm tra số dư.",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text=ORDER_UI_CACHE["confirm"], callback_data="order_confirm")],
            [InlineKeyboardButton(text="↩️ QUAY LẠI BƯỚC 2", callback_data="order_back_stk")],
            [InlineKeyboardButton(text="🏠 VỀ MENU CHÍNH", callback_data="home")]
        ])
    )
    await call.answer()


@dp.callback_query(F.data == "order_confirm")
async def order_confirm(call: CallbackQuery, state: FSMContext):
    if await state.get_state() != OrderForm.confirm.state:
        return await call.answer("Phiên đặt đơn đã hết hạn. Vui lòng thao tác lại.", show_alert=True)
    data = await state.get_data()
    price = int(data["price"])
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("BEGIN IMMEDIATE")
        cur = await db.execute("SELECT wallet FROM users WHERE user_id=?", (call.from_user.id,))
        wallet = await cur.fetchone()
        cur = await db.execute("SELECT active FROM sites WHERE id=?", (data["site_id"],))
        active = await cur.fetchone()
        if not wallet or wallet[0] < price or not active or not active[0]:
            await db.rollback()
            current_wallet = wallet[0] if wallet else 0
            reason = ("Số dư ví không đủ để thanh toán." if wallet and wallet[0] < price
                      else "Site đã ngừng hoạt động, vui lòng chọn site khác.")
            await call.message.edit_text(
                "❌ <b>XÁC NHẬN GIAO DỊCH THẤT BẠI</b>\n\n"
                f"{reason}\n"
                f"💰 Giá đơn: <b>{money(price)}</b>\n"
                f"👛 Số dư hiện tại: <b>{money(current_wallet)}</b>\n"
                "⛔ Chưa trừ tiền và chưa tạo đơn hàng.\n\n"
                "Bạn có thể quay lại xác nhận sau khi nạp tiền hoặc chọn lại site.",
                reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                    [InlineKeyboardButton(text="↩️ QUAY LẠI BƯỚC 3", callback_data="order_retry_confirm")],
                    [InlineKeyboardButton(text="💰 NẠP TIỀN", callback_data="deposit")],
                    [InlineKeyboardButton(text="🏠 VỀ MENU CHÍNH", callback_data="home")]
                ])
            )
            return await call.answer("Giao dịch thất bại: không đủ điều kiện thanh toán.", show_alert=True)
        await db.execute("UPDATE users SET wallet=wallet-? WHERE user_id=? AND wallet>=?", (price, call.from_user.id, price))
        cur = await db.execute(
            "INSERT INTO orders(user_id,product,product_code,account_name,stk_last4,price,status,created_at,updated_at) VALUES(?,?,?,?,?,?,'pending',?,?)",
            (call.from_user.id, data["site_name"], data["product_code"], data["account_name"], data["stk_last4"], price, now_text(), now_text())
        )
        order_id = cur.lastrowid
        await db.execute(
            "INSERT INTO transactions(user_id,kind,amount,note,created_at,ref) VALUES(?,?,?,?,?,?)",
            (call.from_user.id, "order_payment", -price, f"Thanh toán đơn {data['site_name']}", now_text(), f"ORD-{order_id:06d}")
        )
        await db.commit()
    await state.clear()
    completion = ORDER_UI_CACHE["complete"]
    replacements = {
        "{site_name}": esc(data["site_name"].title()), "{product_code}": esc(data["product_code"]),
        "{order_id}": f"ORD-{order_id:06d}", "{account_name}": esc(data["account_name"]),
        "{stk}": esc(data["stk_last4"]), "{digits}": str(required_stk_digits(data.get("site_name", ""))),
        "{price}": money(price), "{status}": "ĐANG CHỜ XỬ LÝ"
    }
    for token, replacement in replacements.items():
        completion = completion.replace(token, replacement)
    await call.message.edit_text(completion, reply_markup=main_kb(call.from_user.id))
    for admin_id in ADMIN_IDS:
        try:
            await bot.send_message(
                admin_id,
                f"📦 <b>ĐƠN MỚI ORD-{order_id:06d}</b>\n"
                f"User: <code>{call.from_user.id}</code>\n"
                f"Site: <b>{esc(data['site_name'])}</b> • Mã {esc(data['product_code'])}\n"
                f"Tài khoản: {esc(data['account_name'])}\n"
                f"{required_stk_digits(data.get('site_name', ''))} số cuối STK: <code>{data['stk_last4']}</code>\n"
                f"Giá: {money(price)}",
                reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                    [InlineKeyboardButton(text="🔄 Đang xử lý", callback_data=f"ordproc:{order_id}")],
                    [InlineKeyboardButton(text="✅ Hoàn thành", callback_data=f"orddone:{order_id}")],
                    [InlineKeyboardButton(text="↩️ Hoàn tiền", callback_data=f"ordrefund:{order_id}")]
                ])
            )
        except Exception:
            log.exception("Could not notify admin")
    await call.answer("Đặt đơn thành công.")


@dp.callback_query(F.data == "deposit")
async def deposit_start(call: CallbackQuery, state: FSMContext):
    await state.set_state(DepositForm.amount)
    await call.message.edit_text(
        "💎 <b>NẠP TIỀN VÀO VÍ 💎</b>\n\n"
        "─────────────────────────\n\n"
        "Vui lòng nhập số tiền bạn muốn nạp vào hệ thống.\n\n"
        "⚠️ <b>Quy định nạp tiền:</b>\n"
        "• Mức nạp tối thiểu: <b>30.000đ</b>\n"
        "• Vui lòng chỉ nhập số (VD: <code>30000</code> hoặc <code>100000</code>).",
        reply_markup=back_kb()
    )
    await call.answer()


@dp.message(DepositForm.amount)
async def deposit_amount(message: Message, state: FSMContext):
    raw = re.sub(r"[^\d]", "", message.text or "")
    if not raw or int(raw) < 30000 or int(raw) > 100000000:
        return await message.answer("⚠️ Số tiền không hợp lệ. Mức nạp tối thiểu 30.000đ, tối đa 100.000.000đ.")
    amount = int(raw)
    code = f"{SEPAY_PREFIX}{secrets.token_hex(4).upper()}"
    async with aiosqlite.connect(DB_PATH) as db:
        # Chỉ giữ một yêu cầu nạp đang chờ cho mỗi khách để ID Telegram
        # kết hợp với số tiền có thể nhận diện giao dịch một cách rõ ràng.
        await db.execute(
            "UPDATE deposits SET status='replaced' WHERE user_id=? AND status='pending'",
            (message.from_user.id,)
        )
        await db.execute(
            "INSERT INTO deposits(user_id,code,amount,status,created_at) VALUES(?,?,?,'pending',?)",
            (message.from_user.id, code, amount, now_text())
        )
        await db.commit()
    await state.clear()
    qr = (
        f"https://qr.sepay.vn/img?acc={SEPAY_ACCOUNT}&bank={SEPAY_BANK}"
        f"&amount={amount}&des={code}&template={SEPAY_QR_TEMPLATE}"
    )
    await message.answer(
        "🏦 <b>THÔNG TIN CHUYỂN KHOẢN 🏦</b>\n\n"
        "─────────────────────────\n\n"
        "Vui lòng quét mã QR hoặc chuyển khoản thủ công theo thông tin bên dưới:\n\n"
        f"🏦 Ngân hàng: <b>ACB</b>\n"
        f"🔢 STK: <code>{SEPAY_ACCOUNT}</code>\n"
        f"👤 Tên: <b>{esc(SEPAY_ACCOUNT_NAME)}</b>\n\n"
        f"💵 Số tiền cần nạp: <b>{money(amount)}</b>\n"
        f"📝 Nội dung chuyển khoản: <code>{code}</code>\n\n"
        "⚠️ <b>QUAN TRỌNG:</b> Hãy chuyển ĐÚNG số tiền và ĐÚNG mã giao dịch hiển thị ở trên "
        "để hệ thống tự động nhận diện và cộng tiền vào ví sau khi SePay xác nhận giao dịch.",
        reply_markup=main_kb(message.from_user.id)
    )
    await message.answer_photo(qr, caption=f"QR nạp tiền • ID {message.from_user.id}")


@dp.callback_query(F.data == "account")
async def account_screen(call: CallbackQuery):
    row = await get_user(call.from_user.id)
    await call.message.edit_text(
        "👤 <b>THÔNG TIN TÀI KHOẢN</b>\n\n"
        f"🆔 ID: <code>{call.from_user.id}</code>\n"
        f"👤 Username: @{esc(call.from_user.username or 'chưa đặt')}\n"
        f"💰 Số dư ví: <b>{money(row['wallet'] if row else 0)}</b>",
        reply_markup=back_kb()
    )
    await call.answer()


@dp.callback_query(F.data == "history")
async def history(call: CallbackQuery):
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute(
            "SELECT kind,amount,note,created_at,ref FROM transactions WHERE user_id=? ORDER BY id DESC LIMIT 10",
            (call.from_user.id,)
        )
        txs = await cur.fetchall()
        cur = await db.execute(
            "SELECT code,amount,status,created_at FROM deposits WHERE user_id=? ORDER BY id DESC LIMIT 10",
            (call.from_user.id,)
        )
        deps = await cur.fetchall()
        cur = await db.execute(
            "SELECT id,product,product_code,price,status,created_at FROM orders WHERE user_id=? ORDER BY id DESC LIMIT 10",
            (call.from_user.id,)
        )
        orders = await cur.fetchall()
    lines = ["📊 <b>LỊCH SỬ GIAO DỊCH</b>", "", "💳 <b>LỊCH SỬ NẠP TIỀN</b>"]
    lines += [f"• {esc(d[0])} | {money(d[1])} | {esc(d[2])} | {esc(d[3])}" for d in deps] or ["• Chưa có lần nạp."]
    lines += ["", "🎁 <b>LỊCH SỬ ĐƠN HÀNG</b>"]
    lines += [f"• ORD-{o[0]:06d} | {esc(o[1])} / {esc(o[2])} | {money(o[3])} | {esc(o[4])}" for o in orders] or ["• Chưa có đơn hàng."]
    await call.message.edit_text("\n".join(lines), reply_markup=back_kb())
    await call.answer()


@dp.callback_query(F.data == "myorders")
async def myorders(call: CallbackQuery):
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute("SELECT id,product,product_code,price,status,created_at FROM orders WHERE user_id=? ORDER BY id DESC LIMIT 10", (call.from_user.id,))
        rows = await cur.fetchall()
    lines = ["⏳ <b>ĐƠN HÀNG CỦA TÔI</b>"]
    lines += [f"• ORD-{r[0]:06d} | {esc(r[1])} / {esc(r[2])} | {money(r[3])} | {esc(r[4])} | {esc(r[5])}" for r in rows] or ["Chưa có đơn hàng."]
    await call.message.edit_text("\n".join(lines), reply_markup=back_kb())
    await call.answer()


@dp.callback_query(F.data.startswith("ordproc:"))
async def admin_order_processing(call: CallbackQuery):
    if not is_admin(call.from_user.id):
        return await call.answer("Không có quyền.", show_alert=True)
    if not await has_admin_permission(call.from_user.id, "orders.review"):
        return await call.answer("⛔ Bạn chưa được cấp quyền: orders.review", show_alert=True)
    try:
        oid = int(call.data.split(":", 1)[1])
    except (ValueError, IndexError):
        return await call.answer("Mã đơn không hợp lệ.", show_alert=True)

    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("BEGIN IMMEDIATE")
        cur = await db.execute("SELECT user_id,status FROM orders WHERE id=?", (oid,))
        row = await cur.fetchone()
        if not row or row[1] not in ("pending", "processing"):
            await db.rollback()
            return await call.answer("Đơn không còn ở trạng thái chờ xử lý.", show_alert=True)
        uid, old_status = row
        if old_status == "processing":
            await db.rollback()
            return await call.answer("Đơn này đang được xử lý.")
        cur = await db.execute(
            "UPDATE orders SET status='processing',updated_at=? WHERE id=? AND status='pending'",
            (now_text(), oid)
        )
        if cur.rowcount != 1:
            await db.rollback()
            return await call.answer("Trạng thái đơn vừa thay đổi, vui lòng tải lại.", show_alert=True)
        await db.commit()

    try:
        await bot.send_message(
            uid,
            "🔄 <b>ĐƠN HÀNG ĐANG ĐƯỢC XỬ LÝ</b>\n\n"
            f"Mã đơn: <code>DH{oid:08d}</code>\n"
            "Quản trị viên đã tiếp nhận đơn. Vui lòng chờ thông báo hoàn tất."
        )
    except Exception:
        log.exception("Could not notify user %s that order %s is processing", uid, oid)
    await call.answer("Đã chuyển đơn sang ĐANG XỬ LÝ.")
    try:
        await call.message.edit_reply_markup(reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="🔄 ĐANG XỬ LÝ", callback_data=f"ordproc:{oid}")],
            [InlineKeyboardButton(text="✅ DONE DUYỆT KM", callback_data=f"orddone:{oid}")],
            [InlineKeyboardButton(text="↩️ HOÀN ĐƠN", callback_data=f"ordrefund:{oid}")],
            [InlineKeyboardButton(text="× LẠM DỤNG", callback_data=f"ordabuse:{oid}")]
        ]))
    except Exception:
        pass


async def publish_order_announcement(order_id):
    """Publish one successful order to the announcement channel; never auto-duplicate."""
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("BEGIN IMMEDIATE")
        cur = await db.execute("SELECT status FROM order_announcements WHERE order_id=?", (order_id,))
        existing = await cur.fetchone()
        if existing and existing[0] in ("sending", "sent"):
            await db.rollback()
            return existing[0]
        await db.execute(
            "INSERT INTO order_announcements(order_id,status,updated_at) VALUES(?,?,?) "
            "ON CONFLICT(order_id) DO UPDATE SET status='sending',updated_at=excluded.updated_at",
            (order_id, "sending", now_text())
        )
        cur = await db.execute("SELECT product,product_code,account_name,price FROM orders WHERE id=? AND status='completed'", (order_id,))
        order = await cur.fetchone()
        if not order:
            await db.rollback()
            return "not_completed"
        await db.commit()

    product, product_code, account_name, price = order
    approved_at = datetime.now(TZ).strftime("%d/%m/%Y - %H:%M")
    post = (
        "🎉 <b>ĐƠN HÀNG ĐÃ XỬ LÝ THÀNH CÔNG</b>\n\n"
        "✅ <b>GIAO DỊCH THÀNH CÔNG</b>\n\n"
        f"🎁 <b>KM SHARE {esc(product)}</b>\n\n"
        "━━━━━━━━━━━━━━━\n\n"
        f"👤 Tài khoản: <code>{esc(account_name)}</code>\n\n"
        f"🎟 Gói khuyến mãi: <code>KM SHARE {esc(product)} [{esc(product_code)}]</code>\n\n"
        f"💰 Giá trị đơn: <code>{money(price)}</code>\n\n"
        f"🆔 Mã đơn hàng: <code>DH{order_id:08d}</code>\n\n"
        f"📆 Thời gian: <code>{approved_at}</code>\n\n"
        "━━━━━━━━━━━━━━━\n\n"
        "✅ Yêu cầu của quý khách đã được xét duyệt thành công! Vui lòng làm mới trang và kiểm tra số dư."
    )
    try:
        sent = await bot.send_message(ORDER_ANNOUNCE_CHANNEL, post)
    except Exception:
        async with aiosqlite.connect(DB_PATH) as db:
            await db.execute("UPDATE order_announcements SET status='failed',updated_at=? WHERE order_id=? AND status='sending'", (now_text(), order_id))
            await db.commit()
        log.exception("Could not publish completed order %s to %s", order_id, ORDER_ANNOUNCE_CHANNEL)
        return "failed"

    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("UPDATE order_announcements SET status='sent',message_id=?,updated_at=? WHERE order_id=?", (sent.message_id, now_text(), order_id))
        await db.commit()
    return "sent"

@dp.callback_query(F.data.startswith("orddone:"))
async def admin_order_done(call: CallbackQuery):
    if not is_admin(call.from_user.id):
        return await call.answer("Không có quyền.", show_alert=True)
    if not await has_admin_permission(call.from_user.id, "orders.review"):
        return await call.answer("⛔ Bạn chưa được cấp quyền: orders.review", show_alert=True)
    try:
        oid = int(call.data.split(":")[1])
    except (ValueError, IndexError):
        return await call.answer("Mã đơn không hợp lệ.", show_alert=True)

    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("BEGIN IMMEDIATE")
        cur = await db.execute(
            "SELECT o.user_id,o.status,u.username,o.product,o.product_code,o.account_name "
            "FROM orders o LEFT JOIN users u ON u.user_id=o.user_id WHERE o.id=?",
            (oid,)
        )
        row = await cur.fetchone()
        if not row:
            await db.rollback()
            return await call.answer("Không tìm thấy đơn hàng.", show_alert=True)
        uid, current_status, username, product, product_code, account_name = row
        if current_status == "completed":
            await db.rollback()
            return await call.answer("Đơn này đã hoàn thành trước đó.", show_alert=True)
        if current_status not in ("pending", "processing"):
            await db.rollback()
            return await call.answer("Trạng thái đơn không cho phép hoàn thành.", show_alert=True)
        await db.execute("UPDATE orders SET status='completed',updated_at=? WHERE id=?", (now_text(), oid))
        await db.execute("INSERT INTO order_announcements(order_id,status,updated_at) VALUES(?,?,?) ON CONFLICT(order_id) DO NOTHING", (oid, "pending", now_text()))
        await db.commit()

    customer_username = f"@{esc(username)}" if username else "Không có username"
    product_name = f"KM SHARE {product} [{product_code}]"
    customer_notice = (
        "🎉 <b>THÔNG BÁO ĐƠN HÀNG ĐÃ XỬ LÝ</b>\n\n"
        "━━━━━━━━━━━━━━━━━━\n\n"
        "✅ <b>ĐƠN HÀNG CỦA BẠN ĐÃ ĐƯỢC ADMIN XỬ LÝ THÀNH CÔNG!</b>\n\n"
        "✎ <b>THÔNG TIN KHÁCH HÀNG:</b>\n"
        f"➜ Telegram ID: <code>{uid}</code>\n"
        f"➜ Tài khoản đặt: {customer_username}\n"
        f"➜ Mã đơn hàng: <code>DH{oid:08d}</code>\n\n"
        "🎁 <b>THÔNG TIN KHUYẾN MÃI:</b>\n"
        f"• Gói dịch vụ: <b>{esc(product_name)}</b>\n"
        "• Trạng thái: <b>✅ ĐÃ HOÀN TẤT</b>\n\n"
        "━━━━━━━━━━━━━━━━━━\n\n"
        "⤷ <b>LƯU Ý:</b>\n"
        "Vui lòng kiểm tra lại tài khoản đã đăng ký khuyến mãi.\n\n"
        "➜ Cảm ơn bạn đã sử dụng dịch vụ tại <b>SHARE-Hi88</b> ♡\n\n"
        "🤖 Hỗ trợ: @sharehi88bot"
    )
    try:
        await bot.send_message(uid, customer_notice)
        customer_notice_result = "sent"
    except Exception as exc:
        customer_notice_result = f"failed:{type(exc).__name__}"
        log.exception("Could not notify user %s about completed order %s", uid, oid)
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "INSERT INTO transactions(user_id,kind,amount,note,created_at,ref) VALUES(?,?,?,?,?,?)",
            (uid, "order_customer_notice", 0,
             f"Order completed notice {customer_notice_result}", now_text(), f"DH{oid:08d}")
        )
        await db.commit()

    publish_result = await publish_order_announcement(oid)
    if publish_result == "sent":
        await call.answer("Đã hoàn thành đơn và đăng thông báo lên kênh.")
    elif publish_result == "failed":
        await call.answer("Đơn đã hoàn thành; đăng kênh thất bại. Cần kiểm tra quyền bot/kênh.", show_alert=True)
    else:
        await call.answer("Đơn đã được xử lý trước đó.")

@dp.callback_query(F.data.startswith("ordrefund:"))
async def admin_order_refund(call: CallbackQuery):
    if not is_admin(call.from_user.id):
        return await call.answer("Không có quyền.", show_alert=True)
    if not await has_admin_permission(call.from_user.id, "orders.review"):
        return await call.answer("⛔ Bạn chưa được cấp quyền: orders.review", show_alert=True)
    try:
        oid = int(call.data.split(":")[1])
    except (ValueError, IndexError):
        return await call.answer("Mã đơn không hợp lệ.", show_alert=True)
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("BEGIN IMMEDIATE")
        cur = await db.execute("SELECT user_id,price,status FROM orders WHERE id=?", (oid,))
        row = await cur.fetchone()
        if not row or row[2] not in ("pending", "processing"):
            await db.rollback()
            return await call.answer("Đơn không còn ở trạng thái chờ; không thể hoàn tiền lần nữa.", show_alert=True)
        uid, price, _ = row
        await db.execute("UPDATE users SET wallet=wallet+? WHERE user_id=?", (price, uid))
        await db.execute("UPDATE orders SET status='refunded',updated_at=? WHERE id=?", (now_text(), oid))
        await db.execute("INSERT INTO transactions(user_id,kind,amount,note,created_at,ref) VALUES(?,?,?,?,?,?)",
                         (uid, "order_refund", price, "Admin hoàn đơn và hoàn tiền vào ví", now_text(), f"DH{oid:08d}"))
        await db.commit()
    try:
        await bot.send_message(uid,
            "↩️ <b>ĐƠN HÀNG ĐÃ ĐƯỢC HOÀN TIỀN</b>\n\n"
            f"Mã đơn: <code>DH{oid:08d}</code>\n"
            f"Số tiền hoàn: <b>{money(price)}</b>\n"
            "💰 Tiền đã được cộng lại vào ví SHARE-Hi88 của bạn."
        )
    except Exception:
        log.exception("Could not notify user %s about refund order %s", uid, oid)
    await call.answer(f"Đã hoàn {money(price)} vào ví khách.")


@dp.callback_query(F.data.startswith("ordabuse:"))
async def admin_order_abuse(call: CallbackQuery):
    if not is_admin(call.from_user.id):
        return await call.answer("Không có quyền.", show_alert=True)
    if not await has_admin_permission(call.from_user.id, "orders.review"):
        return await call.answer("⛔ Bạn chưa được cấp quyền: orders.review", show_alert=True)
    try:
        oid = int(call.data.split(":")[1])
    except (ValueError, IndexError):
        return await call.answer("Mã đơn không hợp lệ.", show_alert=True)
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("BEGIN IMMEDIATE")
        cur = await db.execute("SELECT user_id,status FROM orders WHERE id=?", (oid,))
        row = await cur.fetchone()
        if not row or row[1] not in ("pending", "processing"):
            await db.rollback()
            return await call.answer("Đơn không còn chờ xét duyệt hoặc đã được xử lý.", show_alert=True)
        uid, _ = row
        await db.execute("UPDATE orders SET status='rejected_abuse',updated_at=? WHERE id=?", (now_text(), oid))
        await db.execute(
            "INSERT INTO transactions(user_id,kind,amount,note,created_at,ref) VALUES(?,?,?,?,?,?)",
            (uid, "order_rejected_abuse", 0, "Từ chối đơn do tài khoản lạm dụng; không hoàn tiền", now_text(), f"DH{oid:08d}")
        )
        await db.commit()
    try:
        await bot.send_message(uid,
            "❌ <b>ĐƠN HÀNG BỊ TỪ CHỐI</b>\n\n"
            f"Mã đơn: <code>DH{oid:08d}</code>\n"
            "Lý do: Tài khoản bị xác định có dấu hiệu lạm dụng khuyến mãi.\n"
            "Theo quy định chương trình, đơn này không được hoàn tiền."
        )
    except Exception:
        log.exception("Could not notify user %s about abuse rejection %s", uid, oid)
    await call.answer("Đã từ chối đơn do lạm dụng. Không hoàn tiền.", show_alert=True)


@dp.callback_query(F.data.startswith("adm_"))
async def admin_menu(call: CallbackQuery, state: FSMContext):
    if not is_admin(call.from_user.id):
        return await call.answer("Không có quyền.", show_alert=True)
    action = call.data
    required = {
        "adm_waiting": "orders.review", "adm_orders": "orders.review",
        "adm_users": "users.manage", "adm_deposits": "deposits.review",
        "adm_wallet": "wallet.adjust", "adm_notify": "notifications.send",
        "adm_report": "reports.read", "adm_stks": "settings.manage",
        "adm_price": "settings.manage", "adm_sites": "settings.manage",
        "adm_settings": "settings.manage"
    }.get(action)
    if required and not await has_admin_permission(call.from_user.id, required):
        return await call.answer("⛔ Bạn chưa được cấp quyền: " + required, show_alert=True)
    if action == "adm_waiting":
        async with aiosqlite.connect(DB_PATH) as db:
            cur = await db.execute(
                "SELECT o.id,o.user_id,u.username,o.product,o.product_code,o.account_name,o.stk_last4,o.price,o.status,o.created_at "
                "FROM orders o LEFT JOIN users u ON u.user_id=o.user_id "
                "WHERE o.status IN ('pending','processing') ORDER BY o.id ASC LIMIT 50"
            )
            rows = await cur.fetchall()
        await call.message.edit_text(
            f"⏳ <b>ĐANG ĐỢI XỬ LÝ</b>\n\nCó <b>{len(rows)}</b> đơn hàng đang chờ Admin xét duyệt.",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="↻ LÀM MỚI", callback_data="adm_waiting"),
                 InlineKeyboardButton(text="↩️ QUAY LẠI", callback_data="admin_home")]
            ])
        )
        for r in rows:
            oid, uid, username, product, code, account, last4, price, status, created = r
            detail = (
                f"📦 <b>ĐƠN DH{oid:08d}</b>\n"
                f"👤 Khách: @{esc(username) if username else 'Không có username'} (<code>{uid}</code>)\n"
                f"🎁 Gói: <b>KM SHARE {esc(product)}</b> • Mã {esc(code)}\n"
                f"🎮 Tài khoản: <code>{esc(account)}</code>\n"
                f"🏦 {required_stk_digits(product)} số cuối STK: <code>{esc(last4)}</code>\n"
                f"💰 Giá trị: <b>{money(price)}</b>\n"
                f"📌 Trạng thái: <b>{'Đang xử lý' if status == 'processing' else 'Chờ xét duyệt'}</b>\n"
                f"🕒 Thời gian: {esc(created)}"
            )
            await call.message.answer(
                detail,
                reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                    [InlineKeyboardButton(text="↻ HOÀN ĐƠN", callback_data=f"ordrefund:{oid}"),
                     InlineKeyboardButton(text="DONE DUYỆT KM", callback_data=f"orddone:{oid}"),
                     InlineKeyboardButton(text="× LẠM DỤNG", callback_data=f"ordabuse:{oid}")]
                ])
            )
        if not rows:
            await call.message.answer("✅ Hiện không có đơn hàng nào đang chờ xử lý.")
        return await call.answer("Đã tải danh sách đơn chờ xử lý.")
    if action == "adm_users":
        async with aiosqlite.connect(DB_PATH) as db:
            cur = await db.execute("SELECT user_id,username,wallet FROM users ORDER BY created_at DESC LIMIT 15")
            rows = await cur.fetchall()
        text = "👥 <b>KHÁCH HÀNG GẦN ĐÂY</b>\n" + ("\n".join(f"• {r[0]} @{esc(r[1])} — {money(r[2])}" for r in rows) or "Chưa có khách hàng.")
    elif action == "adm_orders":
        async with aiosqlite.connect(DB_PATH) as db:
            cur = await db.execute("SELECT id,user_id,price,status FROM orders ORDER BY id DESC LIMIT 15")
            rows = await cur.fetchall()
        text = "📦 <b>ĐƠN HÀNG GẦN ĐÂY</b>\n" + ("\n".join(f"• ORD-{r[0]:06d} | UID {r[1]} | {money(r[2])} | {r[3]}" for r in rows) or "Chưa có đơn.")
    elif action == "adm_deposits":
        async with aiosqlite.connect(DB_PATH) as db:
            cur = await db.execute("SELECT id,user_id,code,amount,status FROM deposits ORDER BY id DESC LIMIT 15")
            rows = await cur.fetchall()
        text = "💳 <b>LỊCH SỬ NẠP</b>\n" + ("\n".join(f"• {r[2]} | UID {r[1]} | {money(r[3])} | {r[4]}" for r in rows) or "Chưa có giao dịch.")
    elif action == "adm_wallet":
        await state.set_state(AdminWalletForm.user_id)
        await call.message.edit_text("💰 Nhập Telegram ID khách hàng:", reply_markup=back_kb())
        return await call.answer()
    elif action == "adm_price":
        async with aiosqlite.connect(DB_PATH) as db:
            cur = await db.execute("SELECT id,name,product_code,price,description,active FROM sites ORDER BY id")
            rows = await cur.fetchall()
        buttons = []
        for r in rows:
            buttons.append([
                InlineKeyboardButton(text=f"✏️ {r[1]} • {money(r[3])}", callback_data=f"site_price:{r[0]}"),
                InlineKeyboardButton(text="🗑 XÓA", callback_data=f"site_delete:{r[0]}")
            ])
        buttons.append([InlineKeyboardButton(text="↩️ VỀ QUẢN TRỊ", callback_data="admin_home")])
        await call.message.edit_text(
            "👑 <b>QUẢN TRỊ KM SHARE HI88</b>\n\n"
            "🛍️ <b>QUẢN LÝ SẢN PHẨM – GIÁ</b>\n\n"
            "Chọn site để chỉnh mã sản phẩm, giá bán và mô tả.\n"
            "⚠️ Khi lưu nội dung mới, nội dung cũ của trường đó sẽ được thay thế hoàn toàn.\n"
            "Mô tả nhập dấu <code>-</code> nếu muốn xóa mô tả hiện tại.",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=buttons)
        )
        return await call.answer()
    elif action == "adm_stks":
        text = (
            "🏦 <b>TÀI KHOẢN NHẬN TIỀN</b>\n\n"
            "Ngân hàng: <b>ACB</b>\n"
            f"STK: <code>{SEPAY_ACCOUNT}</code>\n"
            f"Chủ tài khoản: <b>{esc(SEPAY_ACCOUNT_NAME)}</b>\n\n"
            "Nội dung chuyển khoản: ID Telegram của khách hàng."
        )
    elif action == "adm_sites":
        async with aiosqlite.connect(DB_PATH) as db:
            cur = await db.execute("SELECT id,name,product_code,price,active,description FROM sites ORDER BY id")
            rows = await cur.fetchall()
        lines = ["➕ <b>QUẢN LÝ SITE GAME KHÁC</b>"]
        lines += [f"• ID {r[0]} | {esc(r[1])} | Mã {esc(r[2])} | {money(r[3])} | {'Đang bật' if r[4] else 'Đã tắt'} | Mô tả: {esc(r[5] or 'Chưa có')}" for r in rows]
        lines += ["", "Dùng các nút bên dưới để thêm site hoặc bật/tắt site."]
        text = "\n".join(lines)
        site_buttons = [
            [InlineKeyboardButton(text=f"✏️ SỬA {r[1]}", callback_data=f"site_price:{r[0]}"),
             InlineKeyboardButton(text="🗑 XÓA", callback_data=f"site_delete:{r[0]}")]
            for r in rows
        ]
        await call.message.edit_text(text, reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="➕ THÊM SITE", callback_data="site_add")],
            [InlineKeyboardButton(text="🔄 BẬT / TẮT SITE", callback_data="site_toggle_list")],
            *site_buttons,
            [InlineKeyboardButton(text="↩️ VỀ QUẢN TRỊ", callback_data="admin_home")]
        ]))
        return await call.answer()
    elif action == "adm_notify":
        await state.set_state(NotifyForm.recipient)
        await call.message.edit_text("📨 <b>THÔNG BÁO ĐẾN ID RIÊNG</b>\n\nNhập Telegram ID người nhận:", reply_markup=back_kb())
        return await call.answer()
    elif action == "adm_settings":
        await open_beni_system(call)
        return
    elif action == "adm_report":
        async with aiosqlite.connect(DB_PATH) as db:
            cur = await db.execute("SELECT COUNT(*),COALESCE(SUM(price),0) FROM orders")
            orders, sales = await cur.fetchone()
            cur = await db.execute("SELECT COUNT(*),COALESCE(SUM(amount),0) FROM deposits WHERE status='paid'")
            deposits, total = await cur.fetchone()
        text = f"📊 <b>BÁO CÁO</b>\nĐơn hàng: {orders}\nDoanh số đơn: {money(sales)}\nLần nạp thành công: {deposits}\nTổng nạp: {money(total)}"
    else:
        text = "Chức năng chưa cấu hình."
    await call.message.edit_text(text, reply_markup=InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="↩️ VỀ QUẢN TRỊ", callback_data="admin_home")],
        [InlineKeyboardButton(text="🏠 MENU NGƯỜI MUA", callback_data="home")]
    ]))
    await call.answer()


@dp.callback_query(F.data == "admin_home")
async def admin_home(call: CallbackQuery):
    if not is_admin(call.from_user.id):
        return await call.answer("Không có quyền.", show_alert=True)
    await call.message.edit_text("👑 <b>QUẢN TRỊ KM SHARE HI88</b>", reply_markup=admin_kb())
    await call.answer()


@dp.message(AdminWalletForm.user_id)
async def admin_wallet_uid(message: Message, state: FSMContext):
    if not is_admin(message.from_user.id):
        return
    if not (message.text or "").isdigit():
        return await message.answer("Telegram ID phải là số.")
    await state.update_data(target_id=int(message.text))
    await state.set_state(AdminWalletForm.amount)
    await message.answer("Nhập số tiền điều chỉnh: số dương để cộng, số âm để trừ.")


@dp.message(AdminWalletForm.amount)
async def admin_wallet_amount(message: Message, state: FSMContext):
    try:
        amount = int((message.text or "").replace(".", "").replace(",", ""))
        if amount == 0 or abs(amount) > 100000000:
            raise ValueError()
    except ValueError:
        return await message.answer("Số tiền không hợp lệ.")
    await state.update_data(amount=amount)
    await state.set_state(AdminWalletForm.reason)
    await message.answer("Nhập lý do điều chỉnh (bắt buộc):")


@dp.message(AdminWalletForm.reason)
async def admin_wallet_reason(message: Message, state: FSMContext):
    data = await state.get_data()
    reason = (message.text or "").strip()
    if len(reason) < 3:
        return await message.answer("Vui lòng nhập lý do cụ thể.")
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("BEGIN IMMEDIATE")
        cur = await db.execute("SELECT wallet FROM users WHERE user_id=?", (data["target_id"],))
        row = await cur.fetchone()
        if not row or row[0] + data["amount"] < 0:
            await db.rollback()
            await state.clear()
            return await message.answer("Không tìm thấy khách hoặc số dư không đủ.")
        await db.execute("UPDATE users SET wallet=wallet+? WHERE user_id=?", (data["amount"], data["target_id"]))
        await db.execute("INSERT INTO transactions(user_id,kind,amount,note,created_at) VALUES(?,?,?,?,?)",
                         (data["target_id"], "admin_adjustment", data["amount"], reason, now_text()))
        await db.commit()
    await state.clear()
    await message.answer("✅ Đã điều chỉnh ví và lưu nhật ký.")


@dp.message(AdminPriceForm.price)
async def admin_set_price(message: Message, state: FSMContext):
    if not is_admin(message.from_user.id):
        return
    raw = re.sub(r"\D", "", message.text or "")
    if not raw or int(raw) < 1000:
        return await message.answer("Giá không hợp lệ.")
    await set_setting("product_price_vnd", raw)
    await state.clear()
    await message.answer(f"✅ Đã lưu giá mới: {money(int(raw))}. Giá mới được áp dụng cho đơn tiếp theo.")


@dp.message(Command("setstk"))
async def set_stk(message: Message, state: FSMContext):
    if not is_admin(message.from_user.id):
        return
    await state.set_state(AdminStkForm.slot)
    await message.answer("Nhập số thứ tự STK cần cấu hình (1–4):")


@dp.message(AdminStkForm.slot)
async def stk_slot(message: Message, state: FSMContext):
    if (message.text or "") not in ("1", "2", "3", "4"):
        return await message.answer("Chỉ chọn 1, 2, 3 hoặc 4.")
    await state.update_data(slot=message.text)
    await state.set_state(AdminStkForm.last4)
    await message.answer("Nhập 6 số cuối STK:")


@dp.message(AdminStkForm.last4)
async def stk_last4(message: Message, state: FSMContext):
    digits = re.sub(r"\D", "", message.text or "")
    if len(digits) != 4:
        return await message.answer("Cần đúng 6 chữ số.")
    await state.update_data(last4=digits)
    await state.set_state(AdminStkForm.label)
    await message.answer("Nhập nhãn/mô tả STK (không nhập toàn bộ số tài khoản):")


@dp.message(AdminStkForm.label)
async def stk_label(message: Message, state: FSMContext):
    data = await state.get_data()
    await set_setting(f"stk{data['slot']}", f"{(message.text or '').strip()} • ****{data['last4']}")
    await state.clear()
    await message.answer(f"✅ Đã lưu STK {data['slot']}.")


@dp.callback_query(F.data == "site_add")
async def site_add(call: CallbackQuery, state: FSMContext):
    if not is_admin(call.from_user.id):
        return await call.answer("Không có quyền.", show_alert=True)
    await state.set_state(AdminSiteForm.name)
    await call.message.edit_text("Nhập tên site game mới:", reply_markup=back_kb())
    await call.answer()


@dp.message(AdminSiteForm.name)
async def site_add_name(message: Message, state: FSMContext):
    if not is_admin(message.from_user.id):
        return
    name = (message.text or "").strip().upper()
    if not name or len(name) > 32:
        return await message.answer("Tên site không hợp lệ (1–32 ký tự).")
    await state.update_data(site_name=name)
    await state.set_state(AdminSiteForm.code)
    await message.answer("Nhập mã sản phẩm (ví dụ 108):")


@dp.message(AdminSiteForm.code)
async def site_add_code(message: Message, state: FSMContext):
    code = (message.text or "").strip()
    if not code or len(code) > 32:
        return await message.answer("Mã sản phẩm không hợp lệ.")
    await state.update_data(product_code=code)
    await state.set_state(AdminSiteForm.price)
    await message.answer("Nhập giá mỗi đơn bằng VNĐ:")


@dp.message(AdminSiteForm.price)
async def site_add_price(message: Message, state: FSMContext):
    if not is_admin(message.from_user.id):
        return
    raw = re.sub(r"\D", "", message.text or "")
    if not raw or int(raw) < 1000:
        return await message.answer("Giá phải từ 1.000đ trở lên.")
    data = await state.get_data()
    await state.update_data(site_price=int(raw))
    await state.set_state(AdminSiteForm.description)
    return await message.answer("Nhập mô tả sản phẩm (gửi dấu - nếu không cần):")


@dp.message(AdminSiteForm.description)
async def site_add_description(message: Message, state: FSMContext):
    if not is_admin(message.from_user.id): return
    data = await state.get_data()
    description = (message.text or "").strip()
    if description == "-": description = ""
    if len(description) > 500: return await message.answer("Mô tả tối đa 500 ký tự.")
    try:
        async with aiosqlite.connect(DB_PATH) as db:
            cur = await db.execute(
                "INSERT INTO sites(name,product_code,price,description,active,created_at) VALUES(?,?,?,?,?,?)",
                (data["site_name"], data["product_code"], data["site_price"], description, 1, now_text())
            )
            site_id = cur.lastrowid
            await db.commit()
    except aiosqlite.IntegrityError:
        await state.clear()
        return await message.answer("Tên site đã tồn tại. Nhập /admin để kiểm tra.")
    await state.clear()
    await message.answer(
        f"✅ Đã thêm site {esc(data['site_name'])} • mã {esc(data['product_code'])} • {money(data['site_price'])}.",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="✏️ CHỈNH SỬA", callback_data=f"site_price:{site_id}"),
             InlineKeyboardButton(text="🗑 XÓA", callback_data=f"site_delete:{site_id}")],
            [InlineKeyboardButton(text="↩️ QUẢN LÝ SITE", callback_data="adm_sites")]
        ])
    )


@dp.callback_query(F.data.startswith("site_price:"))
async def site_price_start(call: CallbackQuery, state: FSMContext):
    if not is_admin(call.from_user.id):
        return await call.answer("Không có quyền.", show_alert=True)
    site_id = int(call.data.split(":")[1])
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute("SELECT name,product_code,price,description FROM sites WHERE id=?", (site_id,))
        row = await cur.fetchone()
    if not row:
        return await call.answer("Không tìm thấy site.", show_alert=True)
    await state.set_state(SitePriceForm.code)
    await state.update_data(site_id=site_id)
    await call.message.edit_text(
        f"👑 <b>CHỈNH SỬA SITE {esc(row[0])}</b>\n\n"
        f"🔖 Mã hiện tại: <code>{esc(row[1])}</code>\n"
        f"💰 Giá hiện tại: <b>{money(row[2])}</b>\n"
        f"📝 Mô tả hiện tại: {esc(row[3] or 'Chưa có')}\n\n"
        "BƯỚC 1/3 — Nhập mã sản phẩm mới.\n"
        "⚠️ Mã mới sẽ thay thế mã cũ.",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="🗑 XÓA SITE NÀY", callback_data=f"site_delete:{site_id}")],
            [InlineKeyboardButton(text="↩️ QUAY LẠI MENU", callback_data="adm_price")]
        ])
    )
    await call.answer()


@dp.message(SitePriceForm.code)
async def site_price_code(message: Message, state: FSMContext):
    if not is_admin(message.from_user.id):
        return
    code = (message.text or "").strip()
    if not code or len(code) > 32:
        return await message.answer("Mã sản phẩm không hợp lệ.")
    await state.update_data(product_code=code)
    await state.set_state(SitePriceForm.price)
    await message.answer("BƯỚC 2/3 — Nhập giá mới bằng VNĐ. Giá mới sẽ thay thế giá cũ:")


@dp.message(SitePriceForm.price)
async def site_price_value(message: Message, state: FSMContext):
    if not is_admin(message.from_user.id):
        return
    raw = re.sub(r"\D", "", message.text or "")
    if not raw or int(raw) < 1000:
        return await message.answer("Giá không hợp lệ.")
    await state.update_data(site_price=int(raw))
    await state.set_state(SitePriceForm.description)
    await message.answer("BƯỚC 3/3 — Nhập mô tả mới cho sản phẩm. Nội dung mới sẽ thay thế hoàn toàn mô tả cũ. Gửi dấu - nếu muốn xóa mô tả:")


@dp.message(SitePriceForm.description)
async def site_price_description(message: Message, state: FSMContext):
    if not is_admin(message.from_user.id): return
    data = await state.get_data()
    description = (message.text or "").strip()
    if description == "-": description = ""
    if len(description) > 500: return await message.answer("Mô tả tối đa 500 ký tự.")
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute(
            "UPDATE sites SET product_code=?,price=?,description=? WHERE id=?",
            (data["product_code"], data["site_price"], description, data["site_id"])
        )
        if cur.rowcount != 1:
            await db.rollback()
            await state.clear()
            return await message.answer("❌ Không tìm thấy site để cập nhật. Dữ liệu cũ không bị thay đổi.")
        await db.execute(
            "INSERT INTO transactions(user_id,kind,amount,note,created_at,ref) VALUES(?,?,?,?,?,?)",
            (message.from_user.id, "admin_site_update", 0,
             f"Site ID {data['site_id']}: replaced product code, price and description",
             now_text(), str(data["site_id"]))
        )
        await db.commit()
    await state.clear()
    await message.answer(
        f"✅ <b>ĐÃ CẬP NHẬT SITE</b>\n\n"
        f"🔖 Mã sản phẩm mới: <code>{esc(data['product_code'])}</code>\n"
        f"💰 Giá mới: <b>{money(data['site_price'])}</b>\n"
        f"📝 Mô tả: {esc(description or 'Đã xóa')}\n\n"
        "Nội dung mới đã thay thế nội dung cũ. Các đơn hàng đã tạo trước đó không bị thay đổi.",
        reply_markup=admin_kb()
    )


@dp.callback_query(F.data.startswith("site_delete:"))
async def site_delete_prompt(call: CallbackQuery):
    if not is_admin(call.from_user.id):
        return await call.answer("Không có quyền.", show_alert=True)
    try:
        site_id = int(call.data.split(":", 1)[1])
    except ValueError:
        return await call.answer("Site ID không hợp lệ.", show_alert=True)
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute("SELECT name FROM sites WHERE id=?", (site_id,))
        site = await cur.fetchone()
        if not site:
            return await call.answer("Site không còn tồn tại.", show_alert=True)
        cur = await db.execute("SELECT COUNT(*) FROM orders WHERE product=?", (site[0],))
        order_count = (await cur.fetchone())[0]
    note = (
        f"Site {site[0]} đã có {order_count} đơn lịch sử. Xác nhận sẽ TẮT site khỏi danh sách mua hàng, "
        "giữ nguyên bản ghi để bảo toàn lịch sử đơn."
        if order_count else
        f"Site {site[0]} chưa có đơn lịch sử. Xác nhận sẽ xóa site khỏi danh sách quản trị và mua hàng."
    )
    await call.message.edit_text(
        f"⚠️ <b>XÁC NHẬN XÓA SITE {esc(site[0])}?</b>\n\n{note}",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="✅ XÁC NHẬN XÓA", callback_data=f"site_delete_confirm:{site_id}")],
            [InlineKeyboardButton(text="❌ HỦY", callback_data="adm_sites")]
        ])
    )
    await call.answer()


@dp.callback_query(F.data.startswith("site_delete_confirm:"))
async def site_delete_confirm(call: CallbackQuery):
    if not is_admin(call.from_user.id):
        return await call.answer("Không có quyền.", show_alert=True)
    try:
        site_id = int(call.data.split(":", 1)[1])
    except ValueError:
        return await call.answer("Site ID không hợp lệ.", show_alert=True)
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("BEGIN IMMEDIATE")
        cur = await db.execute("SELECT name FROM sites WHERE id=?", (site_id,))
        site = await cur.fetchone()
        if not site:
            await db.rollback()
            return await call.answer("Site không còn tồn tại.", show_alert=True)
        cur = await db.execute("SELECT COUNT(*) FROM orders WHERE product=?", (site[0],))
        order_count = (await cur.fetchone())[0]
        if order_count:
            await db.execute("UPDATE sites SET active=0 WHERE id=?", (site_id,))
            action = "admin_site_deactivate"
            result = "Đã gỡ site khỏi danh sách mua hàng. Dữ liệu và đơn hàng cũ được giữ nguyên."
        else:
            await db.execute("DELETE FROM sites WHERE id=?", (site_id,))
            action = "admin_site_delete"
            result = "Đã xóa site."
        await db.execute(
            "INSERT INTO transactions(user_id,kind,amount,note,created_at,ref) VALUES(?,?,?,?,?,?)",
            (call.from_user.id, action, 0, f"{action}: {site[0]}", now_text(), str(site_id))
        )
        await db.commit()
    await call.message.edit_text(
        f"✅ <b>{esc(result)}</b>",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="🛍️ QUẢN LÝ SẢN PHẨM – GIÁ", callback_data="adm_price")],
            [InlineKeyboardButton(text="➕ QUẢN LÝ SITE", callback_data="adm_sites")],
            [InlineKeyboardButton(text="↩️ BẢNG QTV", callback_data="admin_home")]
        ])
    )
    await call.answer("Đã xử lý site.")


@dp.callback_query(F.data == "site_toggle_list")
async def site_toggle_list(call: CallbackQuery):
    if not is_admin(call.from_user.id):
        return await call.answer("Không có quyền.", show_alert=True)
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute("SELECT id,name,active FROM sites ORDER BY id")
        rows = await cur.fetchall()
    buttons = [[InlineKeyboardButton(text=f"{'🟢' if r[2] else '⚫'} {r[1]} — ĐỔI TRẠNG THÁI", callback_data=f"site_toggle:{r[0]}")] for r in rows]
    buttons.append([InlineKeyboardButton(text="↩️ QUẢN LÝ SITE", callback_data="adm_sites")])
    await call.message.edit_text("Chọn site muốn bật hoặc tắt:", reply_markup=InlineKeyboardMarkup(inline_keyboard=buttons))
    await call.answer()


@dp.callback_query(F.data.startswith("site_toggle:"))
async def site_toggle(call: CallbackQuery):
    if not is_admin(call.from_user.id):
        return await call.answer("Không có quyền.", show_alert=True)
    site_id = int(call.data.split(":")[1])
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("UPDATE sites SET active=CASE active WHEN 1 THEN 0 ELSE 1 END WHERE id=?", (site_id,))
        await db.commit()
    await site_toggle_list(call)


@dp.callback_query(F.data == "broadcast_start")
async def broadcast_start(call: CallbackQuery, state: FSMContext):
    if not is_admin(call.from_user.id):
        return await call.answer("Không có quyền.", show_alert=True)
    await state.clear()
    await state.set_state(BroadcastForm.content)
    await call.message.edit_text(
        "📢 <b>THÔNG BÁO TOÀN THÀNH VIÊN</b>\n\n"
        "Gửi nội dung hoặc media (ảnh/video/tệp kèm chú thích). Bot sẽ phát tới tất cả ID đã từng nhấn /start.\n"
        "Tin nhắn sẽ được sao chép, không hiển thị người gửi gốc.",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="↩️ QUAY LẠI QTV", callback_data="admin_home")]
        ])
    )
    await call.answer()


@dp.message(BroadcastForm.content)
async def broadcast_content(message: Message, state: FSMContext):
    if not is_admin(message.from_user.id):
        return
    if message.text and message.text.startswith("/"):
        return await message.answer("Vui lòng gửi nội dung thông báo, không gửi lệnh.")
    await state.update_data(source_chat_id=message.chat.id, source_message_id=message.message_id)
    await state.set_state(BroadcastForm.confirm)
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute("SELECT COUNT(*) FROM users")
        total = (await cur.fetchone())[0]
    try:
        await message.copy_to(message.chat.id)
    except Exception:
        pass
    await message.answer(
        "🔎 <b>XÁC NHẬN PHÁT THÔNG BÁO</b>\n\n"
        f"👥 Số thành viên trong danh sách: <b>{total:,}</b>\n"
        "📌 Đối tượng: Tất cả thành viên từng nhấn /start\n\n"
        "Bạn có chắc chắn muốn gửi thông báo này?",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="✅ XÁC NHẬN GỬI TOÀN BỘ", callback_data="broadcast_confirm")],
            [InlineKeyboardButton(text="❌ HỦY", callback_data="admin_home")]
        ])
    )


@dp.callback_query(F.data == "broadcast_confirm")
async def broadcast_confirm(call: CallbackQuery, state: FSMContext):
    if not is_admin(call.from_user.id):
        return await call.answer("Không có quyền.", show_alert=True)
    if await state.get_state() != BroadcastForm.confirm.state:
        return await call.answer("Phiên phát thông báo đã hết hạn.", show_alert=True)
    data = await state.get_data()
    await state.clear()
    await call.answer("Bắt đầu gửi thông báo.")
    await call.message.edit_text("📢 Đang phát thông báo...\nVui lòng chờ báo cáo kết quả.")
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute("SELECT user_id FROM users ORDER BY user_id")
        user_ids = [int(row[0]) for row in await cur.fetchall()]
    sent = failed = blocked = 0
    for uid in user_ids:
        try:
            await bot.copy_message(
                chat_id=uid,
                from_chat_id=data["source_chat_id"],
                message_id=data["source_message_id"]
            )
            sent += 1
        except Exception as exc:
            failed += 1
            if "blocked" in str(exc).lower() or "deactivated" in str(exc).lower():
                blocked += 1
            log.warning("Broadcast failed to %s: %s", uid, type(exc).__name__)
        await asyncio.sleep(0.05)
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "INSERT INTO transactions(user_id,kind,amount,note,created_at,ref) VALUES(?,?,?,?,?,?)",
            (call.from_user.id, "admin_broadcast", 0,
             f"Broadcast total={len(user_ids)} sent={sent} failed={failed} blocked={blocked}",
             now_text(), str(call.from_user.id))
        )
        await db.commit()
    await call.message.edit_text(
        "✅ <b>ĐÃ HOÀN TẤT PHÁT THÔNG BÁO</b>\n\n"
        f"👥 Tổng thành viên: <b>{len(user_ids):,}</b>\n"
        f"✅ Gửi thành công: <b>{sent:,}</b>\n"
        f"❌ Gửi thất bại: <b>{failed:,}</b>\n"
        f"🚫 Có dấu hiệu đã chặn bot/ngừng hoạt động: <b>{blocked:,}</b>",
        reply_markup=admin_kb()
    )


@dp.message(NotifyForm.recipient)
async def notify_recipient(message: Message, state: FSMContext):
    if not is_admin(message.from_user.id):
        return
    raw = (message.text or "").strip()
    if not raw.isdigit():
        return await message.answer("Telegram ID phải là dãy số.")
    await state.update_data(recipient=int(raw))
    await state.set_state(NotifyForm.content)
    await message.answer("Nhập nội dung thông báo. Có thể dùng emoji, xuống dòng và đường dẫn:")


@dp.message(NotifyForm.content)
async def notify_content(message: Message, state: FSMContext):
    if not is_admin(message.from_user.id):
        return
    content = (message.text or "").strip()
    if not content or len(content) > 4000:
        return await message.answer("Nội dung không được để trống và tối đa 4.000 ký tự.")
    await state.update_data(content=content)
    data = await state.get_data()
    await state.set_state(NotifyForm.confirm)
    await message.answer(
        "🔎 <b>XÁC NHẬN GỬI THÔNG BÁO</b>\n\n"
        f"👤 Người nhận ID: <code>{data['recipient']}</code>\n"
        f"📝 Nội dung:\n{esc(content)}",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="✅ XÁC NHẬN GỬI", callback_data="notify_send")],
            [InlineKeyboardButton(text="❌ HỦY", callback_data="admin_home")]
        ])
    )


@dp.callback_query(F.data == "notify_send")
async def notify_send(call: CallbackQuery, state: FSMContext):
    if not is_admin(call.from_user.id):
        return await call.answer("Không có quyền.", show_alert=True)
    if await state.get_state() != NotifyForm.confirm.state:
        return await call.answer("Phiên gửi thông báo đã hết hạn.", show_alert=True)
    data = await state.get_data()
    try:
        await bot.send_message(data["recipient"], data["content"], parse_mode=None)
        result = "success"
        msg = "✅ Gửi thông báo thành công."
    except Exception as exc:
        result = f"failed: {type(exc).__name__}"
        msg = "⚠️ Không gửi được. Người nhận có thể chưa từng mở bot hoặc đã chặn bot."
        log.warning("Admin notification failed to %s: %s", data["recipient"], exc)
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("INSERT INTO transactions(user_id,kind,amount,note,created_at,ref) VALUES(?,?,?,?,?,?)",
                         (call.from_user.id, "admin_notification", 0, f"To {data['recipient']} | {result} | {data['content'][:500]}", now_text(), str(data["recipient"])))
        await db.commit()
    await state.clear()
    await call.message.edit_text(f"{msg}\nID người nhận: <code>{data['recipient']}</code>\nKết quả: <code>{esc(result)}</code>", reply_markup=admin_kb())
    await call.answer()


async def sepay_webhook(request: web.Request):
    # API Key authentication: configure the same secret in Railway and SePay.
    if not SEPAY_API_KEY:
        log.error("SEPAY_API_KEY is not configured; webhook disabled")
        return web.json_response({"success": False, "error": "webhook_not_configured"}, status=503)
    auth = request.headers.get("Authorization", "")
    if not secrets.compare_digest(auth, f"Apikey {SEPAY_API_KEY}"):
        return web.json_response({"success": False}, status=401)
    try:
        payload = await request.json()
        sepay_id = str(payload.get("id", "")).strip()
        amount = int(payload.get("transferAmount") or 0)
        content = str(payload.get("content") or "").upper()
        transfer_type = str(payload.get("transferType") or "").lower()
        receiver_account = re.sub(r"\D", "", str(payload.get("accountNumber") or ""))
        expected_account = re.sub(r"\D", "", SEPAY_ACCOUNT)

        if not sepay_id or transfer_type != "in" or amount <= 0:
            return web.json_response({"success": True})
        if receiver_account and expected_account and receiver_account != expected_account:
            log.warning("Ignoring SePay event for an unexpected receiving account")
            return web.json_response({"success": True})

        # The QR memo, pending deposit and webhook must all use the same unique code.
        code_match = re.search(rf"(?<![A-Z0-9])({re.escape(SEPAY_PREFIX)}[A-F0-9]{{8}})(?![A-Z0-9])", content)
        if not code_match:
            return web.json_response({"success": True})
        payment_code = code_match.group(1)

        async with aiosqlite.connect(DB_PATH) as db:
            await db.execute("BEGIN IMMEDIATE")
            cur = await db.execute(
                "SELECT user_id,amount,status FROM deposits WHERE code=? LIMIT 1",
                (payment_code,)
            )
            dep = await cur.fetchone()
            if not dep or dep[2] != "pending" or int(dep[1]) != amount:
                await db.rollback()
                return web.json_response({"success": True})

            uid = int(dep[0])
            try:
                await db.execute(
                    "INSERT INTO sepay_events(sepay_id,received_at,payload) VALUES(?,?,?)",
                    (sepay_id, now_text(), str(payload))
                )
            except aiosqlite.IntegrityError:
                await db.rollback()
                return web.json_response({"success": True})

            cur = await db.execute(
                "UPDATE deposits SET status='paid',sepay_id=?,paid_at=? WHERE code=? AND status='pending'",
                (sepay_id, now_text(), payment_code)
            )
            if cur.rowcount != 1:
                await db.rollback()
                return web.json_response({"success": True})

            await db.execute("UPDATE users SET wallet=wallet+? WHERE user_id=?", (amount, uid))
            await db.execute(
                "INSERT INTO transactions(user_id,kind,amount,note,created_at,ref) VALUES(?,?,?,?,?,?)",
                (uid, "deposit", amount, "Nạp tiền tự động qua SePay ACB", now_text(), payment_code)
            )
            await db.commit()

        try:
            await bot.send_message(
                uid,
                f"✅ <b>NẠP TIỀN THÀNH CÔNG</b>\n"
                f"Mã giao dịch: <code>{esc(payment_code)}</code>\n"
                f"Số tiền: <b>{money(amount)}</b>\n"
                "Ví đã được cộng tiền tự động."
            )
        except Exception:
            log.exception("Could not notify user after successful deposit")
        return web.json_response({"success": True})
    except Exception:
        log.exception("SePay webhook processing failed")
        return web.json_response({"success": False}, status=400)

async def health(request):
    return web.json_response({"status": "ok", "service": "KM SHARE HI88"})


async def start_web_server():
    app = web.Application()
    app.router.add_get("/", health)
    app.router.add_get("/health", health)
    app.router.add_post("/sepay-webhook", sepay_webhook)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "0.0.0.0", PORT)
    await site.start()
    log.info("HTTP server listening on %s", PORT)
    return runner


from handlers.admin_system import register_system_admin

open_beni_system = register_system_admin(
    dp,
    db_path=DB_PATH,
    main_admin_id=MAIN_ADMIN_ID,
    admin_ids=ADMIN_IDS,
    timezone=TZ,
    now_text=now_text,
    esc=esc,
    admin_keyboard=admin_kb,
    sync_command_menus=sync_command_menus,
    clear_admin_command_menu=clear_admin_command_menu,
    on_order_ui_update=lambda key, value: ORDER_UI_CACHE.__setitem__(key, value),
)


async def main():
    await init_db()
    await sync_command_menus()
    runner = await start_web_server()
    try:
        await dp.start_polling(bot)
    finally:
        await runner.cleanup()
        await bot.session.close()


if __name__ == "__main__":
    asyncio.run(main())
