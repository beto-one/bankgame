// کارت‌خوان بازی بانک: فقط RC522 + HC-05.
// با هر بار زدن تگ، یک خط می‌فرستد:  UID:04A1B2C3
// RC522: SDA=10 RST=9 MOSI=11 MISO=12 SCK=13 (تغذیه 3.3V!)
// HC-05: TX ماژول ← پایه 2 ؛ RX ماژول ← پایه 3 از طریق تقسیم‌ولتاژ (۱کΩ و ۲کΩ) ؛ VCC=5V
#include <SPI.h>
#include <MFRC522.h>
#include <SoftwareSerial.h>

MFRC522 rfid(10, 9);
SoftwareSerial bt(2, 3);               // RX, TX

void setup() {
  Serial.begin(9600);                  // برای دیدن همین خروجی روی کامپیوتر (اختیاری)
  bt.begin(9600);                      // سرعت پیش‌فرض HC-05 در حالت داده
  SPI.begin();
  rfid.PCD_Init();
}

void loop() {
  if (!rfid.PICC_IsNewCardPresent() || !rfid.PICC_ReadCardSerial()) return;

  char hex[21];                        // تا ۱۰ بایت UID
  uint8_t n = rfid.uid.size > 10 ? 10 : rfid.uid.size;
  for (uint8_t i = 0; i < n; i++) sprintf(hex + i * 2, "%02X", rfid.uid.uidByte[i]);

  bt.print(F("UID:")); bt.println(hex);
  Serial.print(F("UID:")); Serial.println(hex);

  rfid.PICC_HaltA();                   // تا کارت برداشته نشود دوباره نمی‌خواند
  rfid.PCD_StopCrypto1();
  delay(800);
}
