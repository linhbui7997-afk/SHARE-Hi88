# KM SHARE HI88 Telegram Bot

Telegram bot starter for the agreed customer and admin flows.

## Included
- Customer menu: KM SHARE, SePay top-up, combined transaction history, order history and support.
- Multi-site order flow: HI88 and QQ88 are seeded automatically (product code 108, 50,000 VND each); admin-added sites appear in the customer site list. Each site has its own product code, price and active/inactive status.
- Buyer order flow: choose a site, review its product code and price, enter game account name and last 4 digits of linked bank account, confirm, then debit wallet once and create an order.
- Admin panel: customer list, orders, deposits, wallet adjustments with reason, report, four masked STK labels, per-site product/code pricing, add/toggle sites, and confirmed direct notifications to a Telegram ID with send-result logging.
- SePay incoming-transfer webhook at `POST /sepay-webhook`.
- Duplicate protection using SePay transaction ID and a unique database key.
- Vietnam time display: `DD/MM/YY • HH:mm:ss` (GMT+7).
- SQLite storage; mount a Railway volume at `/data` and set `DATABASE_PATH=/data/share_hi88.sqlite3`.

## Railway setup
1. Create a Railway service from this repository, branch `main`.
2. Add variables from `.env.example` in Railway Variables.
3. Set `BOT_TOKEN` from BotFather. `MAIN_ADMIN_ID` defaults to `6369714149`; add any additional Admin IDs in `ADMIN_IDS` as comma-separated numeric IDs. Set `SUPPORT_URL=https://t.me/nilatao`. Never commit secrets.
4. Set `DATABASE_PATH=/data/share_hi88.sqlite3` and mount a persistent Railway volume at `/data`.
5. Configure bank details and a long random `SEPAY_API_KEY`.
6. Deploy; health endpoint is `/health`.

## SePay webhook setup
Create an incoming-transfer webhook in SePay:
- URL: `https://YOUR-RAILWAY-DOMAIN/sepay-webhook`
- Event: incoming transfers; content type: JSON.
- Authentication: API Key; use the same value as `SEPAY_API_KEY` in Railway.
- Configure the payment-code prefix to match `SEPAY_CODE_PREFIX` (default `SH`).

The bot creates a unique transfer memo per deposit. It checks the payment code, exact amount, incoming direction and duplicate SePay transaction ID before crediting the wallet. SePay expects HTTP 200/201 with JSON `{"success": true}`.

## Important
- No BotFather token, bank details, SePay key or admin ID are included in the repository.
- HI88 and QQ88 are initialized at 50,000 VND each with product code `108`. Use Admin → QUẢN LÝ SẢN PHẨM – GIÁ to edit each site's code and price independently. Use Admin → QUẢN LÝ SITE GAME KHÁC to add or activate/deactivate additional sites.
- Set `MIN_TOTAL_DEPOSIT_VND` and `MIN_DEPOSIT_COUNT` to the final eligibility rules.
- Configure the four masked STK labels using `/setstk`; only the last four digits are stored by that command.
- Product display label is configurable with `PRODUCT_LABEL` (default `HI88 [108/50K]`).
- Telegram menu time is refreshed whenever the menu is reopened or refreshed. A static Telegram message does not tick every second by itself.
- Use the service only where the product and promotions are lawful. Keep clear eligibility/refund terms and back up the persistent database.

## Local run
```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
# Set BOT_TOKEN and other settings, then:
python bot.py
```
