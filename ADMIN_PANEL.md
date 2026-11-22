# BENI / SHARE Hi88 — Cấu trúc chuẩn Admin Panel

Tài liệu đặc tả cấu trúc quản trị cho bot Telegram trong repository này. Đây là tài liệu thiết kế; không tự thay đổi luồng đặt đơn, nạp tiền, SePay hoặc dữ liệu hiện có.

## 1. Nguyên tắc giao diện

- Sử dụng Telegram Inline Keyboard.
- Menu chính: tối đa 2 nút mỗi hàng; các màn hình danh sách có thể dùng 1 nút mỗi hàng để dễ đọc.
- Mọi màn hình con có nút `↩️ QUAY LẠI` và `🏠 MENU QUẢN TRỊ`.
- Mọi thao tác ghi dữ liệu đều phải kiểm tra quyền ở phía server, không chỉ ẩn nút.
- Thao tác rủi ro (xóa Admin, khóa tài khoản, hoàn/điều chỉnh số dư, khôi phục backup, bật bảo trì) phải có màn hình xác nhận.
- Ghi nhật ký: người thao tác, hành động, đối tượng, thời gian GMT+7 và kết quả. Không ghi token, API key hoặc dữ liệu bí mật vào log.

## 2. Menu quản trị chính

Tiêu đề: **👑 BENI SYSTEM — TRUNG TÂM QUẢN TRỊ**

Thông tin:
- Trạng thái Bot: `{bot_status}`
- Phiên bản: `{version}`
- Admin hiện tại: `{admin_name}`
- Telegram ID: `{admin_id}`
- Thời gian: `{datetime_gmt7}`

Bố cục Inline Keyboard (2 nút/hàng):

| Hàng | Nút 1 | Nút 2 |
|---|---|---|
| 1 | 🤖 BOT SETTINGS | 👥 USER MANAGER |
| 2 | 👑 ADMIN MANAGER | 🔐 PHÂN QUYỀN |
| 3 | 🎨 GIAO DIỆN | 📢 THÔNG BÁO |
| 4 | 💾 DATABASE | 📊 THỐNG KÊ |
| 5 | ⚙️ CẤU HÌNH HỆ THỐNG | 🛠 BẢO TRÌ |
| 6 | 📜 NHẬT KÝ HOẠT ĐỘNG | 🔄 LÀM MỚI |

## 3. Danh mục chức năng

### 3.1 BOT SETTINGS
- Xem tên, username và trạng thái Bot.
- Hướng dẫn mở BotFather để sửa tên, mô tả, ảnh đại diện và danh sách lệnh.
- Kiểm tra webhook/server health.
- Bật/tắt các tính năng nghiệp vụ có cờ cấu hình riêng.
- Không yêu cầu hoặc lưu Bot Token trong giao diện chat.

### 3.2 USER MANAGER
- Danh sách người dùng, phân trang.
- Tìm theo Telegram ID hoặc username.
- Xem hồ sơ: ID, username, ngày tham gia, số dư, số đơn, trạng thái.
- Khóa/mở khóa tài khoản.
- Gửi thông báo riêng.
- Xem lịch sử đơn và giao dịch.
- Mọi thay đổi số dư phải đi qua luồng điều chỉnh ví có lý do và ghi transaction.

### 3.3 ADMIN MANAGER
- Hiển thị Admin chính và Admin phụ.
- Thêm/xóa Admin phụ bằng Telegram ID.
- Không cho phép Admin phụ tự nâng quyền hoặc xóa Admin chính.
- Không cho phép xóa Admin cuối cùng có quyền quản trị.
- Ghi nhật ký mọi thay đổi danh sách Admin.

### 3.4 PHÂN QUYỀN
Các quyền gợi ý:
- `users.read`, `users.manage`
- `orders.read`, `orders.review`, `orders.manage`
- `deposits.read`, `deposits.review`
- `wallet.adjust`
- `products.manage`
- `notifications.send`
- `reports.read`
- `settings.manage`
- `admins.manage`
- `database.backup`, `database.restore`
- `maintenance.manage`

Mặc định Admin chính có toàn quyền. Admin phụ chỉ có quyền được cấp. Kiểm tra quyền tại mỗi callback/handler trước khi thực hiện nghiệp vụ.

### 3.5 GIAO DIỆN
- Light Mode / Dark Mode (nếu có Mini App).
- Splash Icon.
- Background Color.
- Header Color.
- Logo và nội dung giới thiệu.
- Xem trước trước khi lưu.
- Có nút đặt lại mặc định.
- Lưu ý: màu và Splash Screen của Telegram Mini App được cấu hình qua BotFather/Mini App settings; bot chat không thể tự đổi giao diện Telegram của người dùng.

### 3.6 THÔNG BÁO
- Cấu hình kênh công bố đơn thành công.
- Cấu hình nhóm quản trị.
- Gửi thông báo đến một Telegram ID.
- Gửi thông báo toàn bộ người dùng (có xác nhận và giới hạn tốc độ).
- Xem kết quả gửi thành công/thất bại.
- Không gửi nội dung quảng cáo khi chưa được người nhận cho phép hoặc trái quy định nền tảng.

### 3.7 DATABASE
- Kiểm tra trạng thái SQLite.
- Sao lưu database.
- Liệt kê thời gian và trạng thái các bản backup.
- Khôi phục backup với xác nhận hai bước.
- Kiểm tra dung lượng và lỗi database.
- Trên Railway, dùng persistent volume và đường dẫn `DATABASE_PATH`; không lưu backup duy nhất trong filesystem tạm của container.

### 3.8 THỐNG KÊ
- Tổng người dùng và người dùng mới theo ngày/tuần/tháng.
- Tổng đơn theo trạng thái.
- Tổng tiền nạp, tổng tiền đã trừ ví và doanh thu theo kỳ.
- Số dư ví toàn hệ thống.
- Tỷ lệ gửi thông báo thành công.
- Các số liệu tài chính phải lấy từ bảng giao dịch, không suy ra chỉ từ trạng thái đơn.

### 3.9 CẤU HÌNH HỆ THỐNG
- Tên hiển thị hệ thống.
- Múi giờ (mặc định `Asia/Ho_Chi_Minh`).
- Kênh thông báo.
- URL hỗ trợ.
- Cấu hình sản phẩm, giá và trạng thái hoạt động.
- Điều kiện khuyến mãi hiển thị rõ ràng.
- Các biến bí mật chỉ được cấu hình trong Railway Variables, không hiển thị lại trong Telegram.

### 3.10 BẢO TRÌ
- Bật/tắt chế độ bảo trì.
- Hiển thị thông báo bảo trì cho người dùng.
- Kiểm tra health endpoint.
- Kiểm tra kết nối database.
- Xem lỗi gần nhất.
- Khởi động lại dịch vụ bằng Railway Dashboard/CLI; bot không tự có quyền khởi động Railway nếu chưa tích hợp API an toàn.

### 3.11 NHẬT KÝ HOẠT ĐỘNG
Các trường tối thiểu:
- `id`
- `actor_user_id`
- `action`
- `target_type`
- `target_id`
- `details_redacted`
- `status`
- `created_at`

Không lưu token, mật khẩu, OTP, API key hoặc toàn bộ payload nhạy cảm.

## 4. Quy trình chuẩn cho thao tác chỉnh sửa

1. Admin chọn chức năng.
2. Bot kiểm tra quyền.
3. Bot hiển thị giá trị hiện tại.
4. Admin chọn **✏️ CHỈNH SỬA**.
5. Admin nhập giá trị mới.
6. Bot hiển thị bản xem trước.
7. Admin chọn **✅ XÁC NHẬN** hoặc **❌ HỦY**.
8. Bot kiểm tra dữ liệu lần cuối, lưu DB và ghi nhật ký.
9. Bot thông báo kết quả.

## 5. Cấu trúc source code mục tiêu

```text
SHARE-Hi88/
├── bot.py                  # Điểm chạy hiện tại; tách dần handler khi refactor
├── config.py               # Đọc và kiểm tra biến môi trường
├── requirements.txt
├── Procfile
├── railway.json
├── database/
│   ├── database.py
│   ├── users.py
│   ├── admins.py
│   ├── settings.py
│   ├── transactions.py
│   └── audit_logs.py
├── handlers/
│   ├── customer.py
│   ├── admin_home.py
│   ├── admin_users.py
│   ├── admin_orders.py
│   ├── admin_deposits.py
│   ├── admin_wallet.py
│   ├── admin_products.py
│   ├── admin_permissions.py
│   ├── admin_notifications.py
│   ├── admin_reports.py
│   └── admin_system.py
├── keyboards/
│   ├── customer.py
│   ├── admin.py
│   └── common.py
├── services/
│   ├── backup.py
│   ├── permissions.py
│   ├── audit.py
│   └── notifications.py
└── tests/
    ├── test_permissions.py
    ├── test_wallet.py
    └── test_orders.py
```

## 6. Lộ trình triển khai an toàn

1. Giữ nguyên các luồng đặt đơn, nạp tiền, webhook SePay và thông báo đơn hiện tại.
2. Bổ sung kiểm tra quyền tập trung và audit log.
3. Hoàn thiện Admin Manager và Permission Manager.
4. Bổ sung User Manager, thống kê và cấu hình.
5. Bổ sung backup/restore có kiểm tra an toàn.
6. Chạy kiểm thử trước khi triển khai Railway.

> Tài liệu này là bản thiết kế chuẩn để triển khai từng phần. Không coi các chức năng liệt kê ở đây là đã được lập trình nếu chưa có handler, kiểm thử và xác nhận hoạt động.
