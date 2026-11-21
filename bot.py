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
    CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup,
    Message
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
SEPAY_BANK = os.getenv("SEPAY_BANK_NAME", "")
SEPAY_ACCOUNT = os.getenv("SEPAY_ACCOUNT_NUMBER", "")
SEPAY_ACCOUNT_NAME = os.getenv("SEPAY_ACCOUNT_NAME", "")
SEPAY_QR_TEMPLATE = os.getenv("SEPAY_QR_TEMPLATE", "compact2")
SEPAY_PREFIX = re.sub(r"[^A-Z0-9]", "", os.getenv("SEPAY_CODE_PREFIX", "SH").upper())[:5] or "SH"
TZ = ZoneInfo("Asia/Ho_Chi_Minh")

if not BOT_TOKEN:
    raise RuntimeError("Missing BOT_TOKEN environment variable")

bot = Bot(BOT_TOKEN, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
dp = Dispatcher(storage=MemoryStorage())


def now_text():
    return datetime.now(TZ).strftime("%d/%m/%y • %H:%M:%S")


def money(value):
    return f"{int(value):,}".replace(",", ".") + "đ"


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
        CREATE TABLE IF NOT EXISTS sites(
          id INTEGER PRIMARY KEY AUTOINCREMENT,
          name TEXT NOT NULL UNIQUE,
          product_code TEXT NOT NULL,
          price INTEGER NOT NULL DEFAULT 50000,
          active INTEGER NOT NULL DEFAULT 1,
          created_at TEXT NOT NULL
        );
        """)
        # Safe schema migration for databases created by older bot versions.
        cur = await db.execute("PRAGMA table_info(orders)")
        columns = {row[1] for row in await cur.fetchall()}
        if "product_code" not in columns:
            await db.execute("ALTER TABLE orders ADD COLUMN product_code TEXT NOT NULL DEFAULT ''")
        cur = await db.execute("SELECT COUNT(*) FROM sites")
        if (await cur.fetchone())[0] == 0:
            await db.executemany(
                "INSERT INTO sites(name,product_code,price,active,created_at) VALUES(?,?,?,?,?)",
                [("HI88", "108", 50000, 1, now_text()), ("QQ88", "108", 50000, 1, now_text())]
            )
        await db.commit()


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
        [InlineKeyboardButton(text="🎁 ĐẶT ĐƠN KM SHARE", callback_data="buy")],
        [InlineKeyboardButton(text="💰 NẠP TIỀN", callback_data="deposit")],
        [InlineKeyboardButton(text="👤 TÀI KHOẢN", callback_data="account")],
        [InlineKeyboardButton(text="📜 LỊCH SỬ ĐƠN HÀNG", callback_data="myorders")],
        [InlineKeyboardButton(text="💳 LỊCH SỬ GIAO DỊCH", callback_data="history")],
        [InlineKeyboardButton(text="🎧 LIÊN HỆ HỖ TRỢ", url=os.getenv("SUPPORT_URL", "https://t.me/nilatao"))],
    ]
    if user_id is not None and is_admin(user_id):
        rows.append([InlineKeyboardButton(text="👑 BẢNG QUẢN LÝ ADMIN", callback_data="admin_home")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def back_kb():
    return InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="↩️ QUAY LẠI MENU", callback_data="home")]])


def admin_kb():
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="👥 QUẢN LÝ KHÁCH HÀNG", callback_data="adm_users")],
        [InlineKeyboardButton(text="📦 QUẢN LÝ ĐƠN HÀNG", callback_data="adm_orders")],
        [InlineKeyboardButton(text="💳 YÊU CẦU NẠP TIỀN", callback_data="adm_deposits")],
        [InlineKeyboardButton(text="💰 ĐIỀU CHỈNH SỐ DƯ VÍ", callback_data="adm_wallet")],
        [InlineKeyboardButton(text="🏦 CẤU HÌNH 4 STK", callback_data="adm_stks")],
        [InlineKeyboardButton(text="🛍️ QUẢN LÝ SẢN PHẨM – GIÁ", callback_data="adm_price")],
        [InlineKeyboardButton(text="➕ QUẢN LÝ SITE GAME KHÁC", callback_data="adm_sites")],
        [InlineKeyboardButton(text="📨 THÔNG BÁO ĐẾN ID RIÊNG", callback_data="adm_notify")],
        [InlineKeyboardButton(text="📊 BÁO CÁO DOANH THU", callback_data="adm_report")],
        [InlineKeyboardButton(text="⚙️ CÀI ĐẶT HỆ THỐNG", callback_data="adm_settings")],
    ])


def is_admin(user_id):
    return user_id in ADMIN_IDS


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
        f"📆 {date_text}   ⏰ {time_text} (GMT+7)\\n"
        f"💬 Xin chào, {username}\\n"
        f"⊕ Số Dư Ví : <b>{money(wallet)}</b>\\n"
        f"✐ ID : <code>{user.id}</code>\\n"
        f"⏳ Đơn Hàng : <b>{waiting}</b>"
    )


async def show_home(target, user):
    text = await home_text(user)
    if isinstance(target, CallbackQuery):
        await target.message.edit_text(text, reply_markup=main_kb(user.id))
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


class SitePriceForm(StatesGroup):
    site_id = State()
    code = State()
    price = State()


class NotifyForm(StatesGroup):
    recipient = State()
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


@dp.message(Command("admin"))
async def admin(message: Message):
    if not is_admin(message.from_user.id):
        return await message.answer("⛔ Bạn không có quyền truy cập.")
    await message.answer("👑 <b>QUẢN TRỊ KM SHARE HI88</b>\nChọn chức năng:", reply_markup=admin_kb())


@dp.callback_query(F.data == "home")
async def cb_home(call: CallbackQuery, state: FSMContext):
    await state.clear()
    await show_home(call, call.from_user)


@dp.callback_query(F.data == "buy")
async def buy(call: CallbackQuery, state: FSMContext):
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute("SELECT id,name,product_code,price FROM sites WHERE active=1 ORDER BY id")
        sites = await cur.fetchall()
    buttons = [[InlineKeyboardButton(text=f"🎁 {r[1]} • Mã {r[2]} • {money(r[3])}", callback_data=f"site:{r[0]}")] for r in sites]
    buttons.append([InlineKeyboardButton(text="↩️ QUAY LẠI", callback_data="home")])
    await call.message.edit_text(
        "🎁 <b>ĐẶT ĐƠN KM SHARE</b>\\n\\n"
        "Vui lòng chọn site game muốn đặt đơn.\\n"
        "Mã 108 là mã sản phẩm, không phải số lượng.",
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
        cur = await db.execute("SELECT id,name,product_code,price FROM sites WHERE id=? AND active=1", (site_id,))
        site = await cur.fetchone()
    if not site:
        return await call.answer("Site hiện không hoạt động.", show_alert=True)
    await state.clear()
    await state.update_data(site_id=site[0], site_name=site[1], product_code=site[2], price=site[3])
    await call.message.edit_text(
        f"🎁 <b>{esc(site[1])}</b>\\n"
        f"🎟 Mã sản phẩm: <code>{esc(site[2])}</code>\\n"
        f"💰 Giá mỗi đơn: <b>{money(site[3])}</b>\\n\\n"
        f"⚙️ <b>ĐIỀU KIỆN ĐẶT ĐƠN</b>\\n"
        f"• Tổng nạp thành công trên {money(MIN_TOTAL_DEPOSIT)}.\\n"
        f"• Có ít nhất {MIN_DEPOSIT_COUNT} lần nạp thành công.\\n\\n"
        "Nhấn tiếp tục để nhập thông tin.",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="🛒 TIẾP TỤC", callback_data="order_begin")],
            [InlineKeyboardButton(text="↩️ CHỌN SITE KHÁC", callback_data="buy")]
        ])
    )
    await call.answer()


@dp.callback_query(F.data == "order_begin")
async def order_begin(call: CallbackQuery, state: FSMContext):
    data = await state.get_data()
    if not data.get("site_id"):
        return await call.answer("Vui lòng chọn site trước.", show_alert=True)
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute(
            "SELECT COALESCE(SUM(amount),0), COUNT(*) FROM deposits WHERE user_id=? AND status='paid'",
            (call.from_user.id,)
        )
        total, count = await cur.fetchone()
    if total <= MIN_TOTAL_DEPOSIT or count < MIN_DEPOSIT_COUNT:
        await call.answer(
            f"Chưa đủ điều kiện: tổng nạp phải trên {money(MIN_TOTAL_DEPOSIT)} và từ {MIN_DEPOSIT_COUNT} lần nạp thành công.",
            show_alert=True
        )
        return
    await state.set_state(OrderForm.account_name)
    await call.message.edit_text(
        f"📝 <b>THÔNG TIN ĐẶT HÀNG • {esc(data['site_name'])}</b>\\n\\n"
        f"👤 Vui lòng nhập tên tài khoản {esc(data['site_name'])}:",
        reply_markup=back_kb()
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
        f"🏦 Nhập <b>4 số cuối STK đã liên kết</b> với tài khoản {esc(data.get('site_name','game'))}:",
        reply_markup=back_kb()
    )


@dp.message(OrderForm.stk_last4)
async def order_stk(message: Message, state: FSMContext):
    digits = re.sub(r"\\D", "", message.text or "")
    if len(digits) != 4:
        return await message.answer("⚠️ Vui lòng nhập đúng 4 chữ số cuối của STK.")
    data = await state.get_data()
    row = await get_user(message.from_user.id)
    price = int(data["price"])
    if not row or row["wallet"] < price:
        await state.clear()
        return await message.answer(f"💳 Số dư không đủ. Giá đơn {money(price)}. Vui lòng nạp tiền.", reply_markup=main_kb(message.from_user.id))
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute(
            "SELECT COALESCE(SUM(amount),0), COUNT(*) FROM deposits WHERE user_id=? AND status='paid'",
            (message.from_user.id,)
        )
        total, count = await cur.fetchone()
    if total <= MIN_TOTAL_DEPOSIT or count < MIN_DEPOSIT_COUNT:
        await state.clear()
        return await message.answer("⚠️ Tài khoản chưa đáp ứng điều kiện nhận thưởng.", reply_markup=main_kb(message.from_user.id))
    await state.update_data(stk_last4=digits)
    await state.set_state(OrderForm.confirm)
    await message.answer(
        "🔎 <b>XÁC NHẬN THÔNG TIN ĐƠN</b>\\n\\n"
        f"🎮 Site: <b>{esc(data['site_name'])}</b>\\n"
        f"🎟 Mã sản phẩm: <code>{esc(data['product_code'])}</code>\\n"
        f"👤 Tài khoản game: <b>{esc(data['account_name'])}</b>\\n"
        f"🏦 4 số cuối STK: <code>{digits}</code>\\n"
        f"💰 Giá đơn: <b>{money(price)}</b>\\n\\n"
        "Vui lòng kiểm tra trước khi xác nhận.",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="✅ XÁC NHẬN ĐẶT ĐƠN", callback_data="order_confirm")],
            [InlineKeyboardButton(text="↩️ HỦY / QUAY LẠI", callback_data="home")]
        ])
    )


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
            await state.clear()
            return await call.answer("Số dư không đủ hoặc site đã ngừng hoạt động.", show_alert=True)
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
    await call.message.edit_text(
        f"✅ <b>ĐÃ GHI NHẬN ĐƠN HÀNG</b>\\n\\n"
        f"🧾 Mã đơn: <code>ORD-{order_id:06d}</code>\\n"
        f"🎮 Site: <b>{esc(data['site_name'])}</b>\\n"
        f"🎟 Mã sản phẩm: <code>{esc(data['product_code'])}</code>\\n"
        f"👤 Tài khoản: {esc(data['account_name'])}\\n"
        f"🏦 4 số cuối STK: <code>{data['stk_last4']}</code>\\n"
        f"💰 Đã trừ ví: <b>{money(price)}</b>\\n"
        f"⏳ Trạng thái: Chờ Admin xử lý",
        reply_markup=main_kb(call.from_user.id)
    )
    for admin_id in ADMIN_IDS:
        try:
            await bot.send_message(
                admin_id,
                f"📦 <b>ĐƠN MỚI ORD-{order_id:06d}</b>\\n"
                f"User: <code>{call.from_user.id}</code>\\n"
                f"Site: <b>{esc(data['site_name'])}</b> • Mã {esc(data['product_code'])}\\n"
                f"Tài khoản: {esc(data['account_name'])}\\n"
                f"4 số cuối STK: <code>{data['stk_last4']}</code>\\n"
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
        "💳 <b>NẠP TIỀN TỰ ĐỘNG QUA SEPAY</b>\n\n"
        "Nhập số tiền muốn nạp (tối thiểu 10.000đ):",
        reply_markup=back_kb()
    )
    await call.answer()


@dp.message(DepositForm.amount)
async def deposit_amount(message: Message, state: FSMContext):
    raw = re.sub(r"[^\d]", "", message.text or "")
    if not raw or int(raw) < 10000 or int(raw) > 100000000:
        return await message.answer("⚠️ Số tiền không hợp lệ. Nhập từ 10.000đ đến 100.000.000đ.")
    amount = int(raw)
    code = f"{SEPAY_PREFIX}{secrets.token_hex(4).upper()}"
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "INSERT INTO deposits(user_id,code,amount,status,created_at) VALUES(?,?,?,'pending',?)",
            (message.from_user.id, code, amount, now_text())
        )
        await db.commit()
    await state.clear()
    if not (SEPAY_BANK and SEPAY_ACCOUNT and SEPAY_ACCOUNT_NAME):
        return await message.answer(
            "⚠️ Hệ thống chưa cấu hình thông tin ngân hàng nhận tiền. Vui lòng liên hệ Admin.",
            reply_markup=main_kb()
        )
    qr = (
        f"https://qr.sepay.vn/img?acc={SEPAY_ACCOUNT}&bank={SEPAY_BANK}"
        f"&amount={amount}&des={code}&template={SEPAY_QR_TEMPLATE}"
    )
    await message.answer(
        f"💳 <b>THÔNG TIN CHUYỂN KHOẢN</b>\n\n"
        f"🏦 Ngân hàng: <b>{esc(SEPAY_BANK)}</b>\n"
        f"🔢 Số tài khoản: <code>{esc(SEPAY_ACCOUNT)}</code>\n"
        f"👤 Chủ tài khoản: <b>{esc(SEPAY_ACCOUNT_NAME)}</b>\n"
        f"💰 Số tiền: <b>{money(amount)}</b>\n"
        f"📝 Nội dung CK: <code>{code}</code>\n\n"
        f"📷 Quét mã QR bên dưới hoặc chuyển khoản đúng số tiền và nội dung.\n"
        f"⏳ Ví được cộng tự động sau khi SePay xác nhận giao dịch.",
        reply_markup=main_kb()
    )
    await message.answer_photo(qr, caption=f"QR nạp tiền • {code}")


@dp.callback_query(F.data == "account")
async def account_screen(call: CallbackQuery):
    row = await get_user(call.from_user.id)
    await call.message.edit_text(
        "👤 <b>THÔNG TIN TÀI KHOẢN</b>\\n\\n"
        f"🆔 ID: <code>{call.from_user.id}</code>\\n"
        f"👤 Username: @{esc(call.from_user.username or 'chưa đặt')}\\n"
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
    oid = int(call.data.split(":")[1])
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("UPDATE orders SET status='processing',updated_at=? WHERE id=? AND status='pending'", (now_text(), oid))
        await db.commit()
        cur = await db.execute("SELECT user_id FROM orders WHERE id=?", (oid,))
        row = await cur.fetchone()
    if row:
        await bot.send_message(row[0], f"🔄 Đơn ORD-{oid:06d} đang được xử lý.")
    await call.answer("Đã cập nhật.")


@dp.callback_query(F.data.startswith("orddone:"))
async def admin_order_done(call: CallbackQuery):
    if not is_admin(call.from_user.id):
        return await call.answer("Không có quyền.", show_alert=True)
    oid = int(call.data.split(":")[1])
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("UPDATE orders SET status='completed',updated_at=? WHERE id=? AND status IN ('pending','processing')", (now_text(), oid))
        await db.commit()
        cur = await db.execute("SELECT user_id FROM orders WHERE id=?", (oid,))
        row = await cur.fetchone()
    if row:
        await bot.send_message(row[0], f"✅ Đơn ORD-{oid:06d} đã hoàn thành.")
    await call.answer("Đã hoàn thành.")


@dp.callback_query(F.data.startswith("ordrefund:"))
async def admin_order_refund(call: CallbackQuery):
    if not is_admin(call.from_user.id):
        return await call.answer("Không có quyền.", show_alert=True)
    oid = int(call.data.split(":")[1])
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("BEGIN IMMEDIATE")
        cur = await db.execute("SELECT user_id,price,status FROM orders WHERE id=?", (oid,))
        row = await cur.fetchone()
        if not row or row[2] in ("refunded", "completed"):
            await db.rollback()
            return await call.answer("Không thể hoàn đơn này.", show_alert=True)
        uid, price, _ = row
        await db.execute("UPDATE users SET wallet=wallet+? WHERE user_id=?", (price, uid))
        await db.execute("UPDATE orders SET status='refunded',updated_at=? WHERE id=?", (now_text(), oid))
        await db.execute("INSERT INTO transactions(user_id,kind,amount,note,created_at,ref) VALUES(?,?,?,?,?,?)",
                         (uid, "order_refund", price, "Hoàn tiền đơn hàng", now_text(), f"ORD-{oid:06d}"))
        await db.commit()
    await bot.send_message(uid, f"↩️ Đơn ORD-{oid:06d} đã được hoàn {money(price)} vào ví.")
    await call.answer("Đã hoàn tiền.")


@dp.callback_query(F.data.startswith("adm_"))
async def admin_menu(call: CallbackQuery, state: FSMContext):
    if not is_admin(call.from_user.id):
        return await call.answer("Không có quyền.", show_alert=True)
    action = call.data
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
        await state.set_state(AdminPriceForm.price)
        await call.message.edit_text(f"🏷️ Giá hiện tại: {money(await current_price())}\nNhập giá mới bằng VNĐ:", reply_markup=back_kb())
        return await call.answer()
    elif action == "adm_stks":
        slots = []
        for i in range(1, 5):
            slots.append(f"STK {i}: {await setting(f'stk{i}', 'Chưa cấu hình')}")
        text = "🏦 <b>4 STK ĐÃ LIÊN KẾT</b>\n" + "\n".join(slots) + "\n\nDùng /setstk để cấu hình từng STK."
    elif action == "adm_sites":
        async with aiosqlite.connect(DB_PATH) as db:
            cur = await db.execute("SELECT id,name,product_code,price,active FROM sites ORDER BY id")
            rows = await cur.fetchall()
        lines = ["➕ <b>QUẢN LÝ SITE GAME KHÁC</b>"]
        lines += [f"• ID {r[0]} | {esc(r[1])} | Mã {esc(r[2])} | {money(r[3])} | {'Đang bật' if r[4] else 'Đã tắt'}" for r in rows]
        lines += ["", "Dùng các nút bên dưới để thêm site hoặc bật/tắt site."]
        text = "\\n".join(lines)
        await call.message.edit_text(text, reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="➕ THÊM SITE", callback_data="site_add")],
            [InlineKeyboardButton(text="🔄 BẬT / TẮT SITE", callback_data="site_toggle_list")],
            [InlineKeyboardButton(text="↩️ VỀ QUẢN TRỊ", callback_data="admin_home")]
        ]))
        return await call.answer()
    elif action == "adm_notify":
        await state.set_state(NotifyForm.recipient)
        await call.message.edit_text("📨 <b>THÔNG BÁO ĐẾN ID RIÊNG</b>\\n\\nNhập Telegram ID người nhận:", reply_markup=back_kb())
        return await call.answer()
    elif action == "adm_settings":
        text = ("⚙️ <b>CÀI ĐẶT HỆ THỐNG</b>\\n"
                f"Điều kiện tổng nạp: {money(MIN_TOTAL_DEPOSIT)}\\n"
                f"Số lần nạp tối thiểu: {MIN_DEPOSIT_COUNT}\\n"
                f"Bot timezone: Asia/Ho_Chi_Minh")
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
    await message.answer("Nhập 4 số cuối STK:")


@dp.message(AdminStkForm.last4)
async def stk_last4(message: Message, state: FSMContext):
    digits = re.sub(r"\D", "", message.text or "")
    if len(digits) != 4:
        return await message.answer("Cần đúng 4 chữ số.")
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
    raw = re.sub(r"\\D", "", message.text or "")
    if not raw or int(raw) < 1000:
        return await message.answer("Giá phải từ 1.000đ trở lên.")
    data = await state.get_data()
    try:
        async with aiosqlite.connect(DB_PATH) as db:
            await db.execute("INSERT INTO sites(name,product_code,price,active,created_at) VALUES(?,?,?,?,?)",
                             (data["site_name"], data["product_code"], int(raw), 1, now_text()))
            await db.commit()
    except aiosqlite.IntegrityError:
        return await message.answer("Tên site đã tồn tại. Nhập /admin để kiểm tra.")
    await state.clear()
    await message.answer(f"✅ Đã thêm site {esc(data['site_name'])} • mã {esc(data['product_code'])} • {money(int(raw))}. Site đã xuất hiện trong danh sách đặt đơn.")


@dp.callback_query(F.data.startswith("site_price:"))
async def site_price_start(call: CallbackQuery, state: FSMContext):
    if not is_admin(call.from_user.id):
        return await call.answer("Không có quyền.", show_alert=True)
    site_id = int(call.data.split(":")[1])
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute("SELECT name,product_code,price FROM sites WHERE id=?", (site_id,))
        row = await cur.fetchone()
    if not row:
        return await call.answer("Không tìm thấy site.", show_alert=True)
    await state.set_state(SitePriceForm.code)
    await state.update_data(site_id=site_id)
    await call.message.edit_text(f"🛍️ <b>{esc(row[0])}</b>\\nMã hiện tại: {esc(row[1])}\\nGiá hiện tại: {money(row[2])}\\n\\nNhập mã sản phẩm mới:", reply_markup=back_kb())
    await call.answer()


@dp.message(SitePriceForm.code)
async def site_price_code(message: Message, state: FSMContext):
    code = (message.text or "").strip()
    if not code or len(code) > 32:
        return await message.answer("Mã sản phẩm không hợp lệ.")
    await state.update_data(product_code=code)
    await state.set_state(SitePriceForm.price)
    await message.answer("Nhập giá mới bằng VNĐ:")


@dp.message(SitePriceForm.price)
async def site_price_value(message: Message, state: FSMContext):
    if not is_admin(message.from_user.id):
        return
    raw = re.sub(r"\\D", "", message.text or "")
    if not raw or int(raw) < 1000:
        return await message.answer("Giá không hợp lệ.")
    data = await state.get_data()
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("UPDATE sites SET product_code=?,price=? WHERE id=?", (data["product_code"], int(raw), data["site_id"]))
        await db.commit()
    await state.clear()
    await message.answer(f"✅ Đã cập nhật mã {esc(data['product_code'])} và giá {money(int(raw))}.")


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
    await call.answer("Đã đổi trạng thái site.")
    await site_toggle_list(call)


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
        "🔎 <b>XÁC NHẬN GỬI THÔNG BÁO</b>\\n\\n"
        f"👤 Người nhận ID: <code>{data['recipient']}</code>\\n"
        f"📝 Nội dung:\\n{esc(content)}",
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
        await bot.send_message(data["recipient"], data["content"])
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
    await call.message.edit_text(f"{msg}\\nID người nhận: <code>{data['recipient']}</code>\\nKết quả: <code>{esc(result)}</code>", reply_markup=admin_kb())
    await call.answer()


async def sepay_webhook(request: web.Request):
    # Fail closed: never accept payment callbacks without a configured shared secret.
    if not SEPAY_API_KEY:
        log.error("SEPAY_API_KEY is not configured; webhook disabled")
        return web.json_response({"success": False, "error": "webhook_not_configured"}, status=503)
    auth = request.headers.get("Authorization", "")
    if auth != f"Apikey {SEPAY_API_KEY}":
        return web.json_response({"success": False}, status=401)
    try:
        payload = await request.json()
        sepay_id = str(payload["id"])
        amount = int(payload["transferAmount"])
        content = str(payload.get("content") or "")
        transfer_type = payload.get("transferType")
        if transfer_type != "in" or amount <= 0:
            return web.json_response({"success": True})
        match = re.search(rf"\b({re.escape(SEPAY_PREFIX)}[A-Z0-9]{{6,}})\b", content.upper())
        code = match.group(1) if match else str(payload.get("code") or "").upper()
        if not code:
            return web.json_response({"success": True})
        async with aiosqlite.connect(DB_PATH) as db:
            await db.execute("BEGIN IMMEDIATE")
            cur = await db.execute("SELECT user_id,amount,status FROM deposits WHERE code=?", (code,))
            dep = await cur.fetchone()
            if not dep or dep[1] != amount or dep[2] != "pending":
                await db.rollback()
                return web.json_response({"success": True})
            try:
                await db.execute("INSERT INTO sepay_events(sepay_id,received_at,payload) VALUES(?,?,?)",
                                 (sepay_id, now_text(), str(payload)))
            except aiosqlite.IntegrityError:
                await db.rollback()
                return web.json_response({"success": True})
            uid = dep[0]
            await db.execute("UPDATE deposits SET status='paid',sepay_id=?,paid_at=? WHERE code=?",
                             (sepay_id, now_text(), code))
            await db.execute("UPDATE users SET wallet=wallet+? WHERE user_id=?", (amount, uid))
            await db.execute("INSERT INTO transactions(user_id,kind,amount,note,created_at,ref) VALUES(?,?,?,?,?,?)",
                             (uid, "deposit", amount, "Nạp tiền qua SePay", now_text(), code))
            await db.commit()
        try:
            await bot.send_message(uid, f"✅ <b>NẠP TIỀN THÀNH CÔNG</b>\nMã: <code>{esc(code)}</code>\nSố tiền: <b>{money(amount)}</b>\nVí đã được cộng tiền.")
        except Exception:
            log.exception("Could not notify user")
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


async def main():
    await init_db()
    runner = await start_web_server()
    try:
        await dp.start_polling(bot)
    finally:
        await runner.cleanup()
        await bot.session.close()


if __name__ == "__main__":
    asyncio.run(main())
