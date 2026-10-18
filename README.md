# بانک بازی
کارت‌خوان RFID (RC522 + HC-05) + سرور Flask روی PythonAnywhere.

- `reader.ino` کد آردوینو (در Arduino IDE داخل پوشه‌ای به نام `reader` بگذار)
- `app.py`, `bank.py`, `index.html` سرور و اپ
- `test_bank.py` تست‌ها: `python3 -m unittest test_bank`

رمزها (`TERMINAL_KEY`, `ADMIN_PASS`) فقط در فایل WSGI خود PythonAnywhere تنظیم می‌شوند، نه در این ریپو.
