#include <Adafruit_SSD1306.h>
#include <Adafruit_GFX.h>
#include <Wire.h>
#include <WiFi.h>
#include <time.h>
#include <UniversalTelegramBot.h>
#include <WiFiClientSecure.h>
#include <kitler.h>
#include <moon.h>
#define LED 2
#define BTN_PIN 5
// put function declarations here:

const char *wifi_name = "brain_fryer_2.4GHz";
const char *wifi_password = "5NCFXFGK";

// Инициализация Telegram бота
#define BOTtoken "7657395532:AAEtlP7VMdI3ORfaY1kHq9qOALEcIg3oZ78"  // Создать своего бота можно через бота https://t.me/BotFather
#define CHAT_ID "239209455"     // Узнать свой айди можно через бота https://t.me/userinfobot
WiFiClientSecure client;
UniversalTelegramBot bot(BOTtoken, client);

// --- Параметры дисплея SSD1306
#define SCREEN_WIDTH 128  // OLED display width, in pixels
#define SCREEN_HEIGHT 64  // OLED display height, in pixels

#define OLED_RESET -1        // Reset pin # (or -1 if sharing Arduino reset pin)
#define SCREEN_ADDRESS 0x3C  ///< See datasheet for Address; 0x3D for 128x64, 0x3C for 128x32
Adafruit_SSD1306 display(SCREEN_WIDTH, SCREEN_HEIGHT, &Wire, OLED_RESET);


// --- Параметры NTP ---
const char* ntpServer = "ntp0.ntp-servers.net";  // Адрес публичного NTP сервера
// Смещение относительно времени по Гринвичу (GMT) в секундах.
// Пример: для GMT+3 установите 3 * 3600. Для Москвы (UTC+3) значение 10800.
const long gmtOffset_sec = 10800;
// Смещение для перехода на летнее время в секундах.
// Обычно 1 час (3600 секунд) или 0, если летнее время не используется.
const int daylightOffset_sec = 0;
// Структура для хранения синхронизированного времени
struct tm timeinfo;

// Прототипы функций, реализацию см. ниже
void connect_wifi_serial(const char *ssid, const char *pass);
String timestamp();


void setup() {
  Serial.begin(115200);
  
  pinMode(LED,OUTPUT); // Set pin mode

  connect_wifi_serial(wifi_name, wifi_password);

  configTime(gmtOffset_sec, daylightOffset_sec, "ntp1.vniiftri.ru", "ntp2.vniiftri.ru");
  setenv("TZ", "MSK-3", 1);
  tzset();
  
  client.setCACert(TELEGRAM_CERTIFICATE_ROOT);
  Serial.println(timestamp());
  bot.sendMessage(CHAT_ID, "Устройство вышло в сеть " + timestamp());

  
  // SSD1306_SWITCHCAPVCC = generate display voltage from 3.3V internally
  if (!display.begin(SSD1306_SWITCHCAPVCC, SCREEN_ADDRESS)) {
    Serial.println(F("SSD1306 allocation failed"));
    for (;;) {
      // Don't proceed, loop forever
    }
  }

  display.clearDisplay();

  //display.drawBitmap(0, 0, kitler, KITLER_WIDTH, KITLER_HEIGHT, SSD1306_WHITE);
  display.drawBitmap(0, 0, moon, MOON_WIDTH, MOON_HEIGHT, SSD1306_WHITE);
  display.display();
}

void loop() {
  // put your main code here, to run repeatedly:
  int press = 0;
  while (WiFi.status() == WL_CONNECTED) {
    // delay(500);
    // digitalWrite(LED,HIGH);
    // delay(500);
    // digitalWrite(LED,LOW);
    delay(100);
    if (digitalRead(BTN_PIN) == LOW) {
      Serial.println("Button pressed! ");
      Serial.print(++press);
    }
  }
  Serial.println("WiFi disconnected! Retrying...");
  connect_wifi_serial(wifi_name, wifi_password);
  delay(3000);
  
}

/*

connect_wifi_serial

Функция для подключения к сети wifi.
Вывод сообщений по серийному интерфейсу (UART) в консоль компа.

Аргументы:
ssid - имя сети
pass - пароль сети

*/
void connect_wifi_serial(const char *ssid, const char *pass) {
  WiFi.mode(WIFI_STA);
  WiFi.disconnect();
  delay(100);

  Serial.println();
  Serial.println("******************************************************");
  Serial.print("Connecting to ");
  Serial.println(ssid);

  WiFi.begin(ssid, pass);
  int tries = 5;
  while (tries > 0) {
    if (WiFi.status() != WL_CONNECTED) {
      delay(500);
      Serial.print(".");
      tries--;
    } else {
        tries = 0;
        Serial.println("");
        Serial.println("WiFi connected");
        Serial.println("IP address: ");
        Serial.println(WiFi.localIP());
    }
  }
}

/*
timestamp

Функция генерации временной метки. Использует время, полученное с помощью configTime(), в качестве опорного.
Возвращает строку в формате %Y-%m-%d %H:%M:%S %Z.

*/

String timestamp() {

  if (!getLocalTime(&timeinfo)) {
    Serial.println("Не удалось получить время с NTP сервера.");
    return "NO TIMESTAMP";
  }
  // Вывод времени в стандартном формате: День недели, Месяц День Год Час:Минута:Секунда
  // Функция strftime форматирует время из структуры tm в строку согласно спецификаторам формата.
  char timeStringBuff[50];  // Буфер для отформатированной строки времени
  strftime(timeStringBuff, sizeof(timeStringBuff), "%Y-%m-%d %H:%M:%S %Z", &timeinfo);
  //Serial.println(timeStringBuff);  // Вывод полной строки времени
  String s;
  return timeStringBuff;
}

