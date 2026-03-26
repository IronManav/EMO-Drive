#include <Wire.h>
#include <LiquidCrystal_I2C.h>

LiquidCrystal_I2C lcd(0x27, 16, 2);

// Sensor pins
const int tempPin  = A0;
const int pulsePin = A1;
const int gasPin   = A2;
const int blinkPin = 2;

// Output pins
const int buzzer         = 8;
const int vibrationMotor = 7;
const int pump           = 6;

// RGB pins (digital — common cathode)
const int redPin   = 9;
const int greenPin = 10;
const int bluePin  = 11;

// Blink tracking
int blinkCount = 0;
unsigned long lastBlinkTime = 0;
bool lastBlinkState = HIGH;

void setup() {
  Serial.begin(9600);
  pinMode(blinkPin, INPUT);
  pinMode(buzzer, OUTPUT);
  pinMode(vibrationMotor, OUTPUT);
  pinMode(pump, OUTPUT);
  pinMode(redPin, OUTPUT);
  pinMode(greenPin, OUTPUT);
  pinMode(bluePin, OUTPUT);

  lcd.init();
  lcd.backlight();
  lcd.setCursor(0, 0);
  lcd.print("EMO Drive");
  lcd.setCursor(0, 1);
  lcd.print("Initializing...");
  delay(2000);
  lcd.clear();
}

void setColor(int r, int g, int b) {
  digitalWrite(redPin,   r);
  digitalWrite(greenPin, g);
  digitalWrite(bluePin,  b);
}

void loop() {
  // ── Demo mode via Serial command ──────────────────────────
  if (Serial.available()) {
    char cmd = Serial.read();
    if (cmd == 'C') calmMode();
    if (cmd == 'A') angryMode();
    if (cmd == 'D') drowsyMode();
    if (cmd == 'X') anxietyMode();
    if (cmd == 'L') alcoholMode();
  }

  // ── Temperature (LM35) ────────────────────────────────────
  int tempValue = analogRead(tempPin);
  float temperature = (tempValue * 5.0 / 1023.0) * 100.0;

  // ── Pulse ─────────────────────────────────────────────────
  int pulseValue = analogRead(pulsePin);
  int bpm = map(pulseValue, 0, 1023, 40, 130);

  // ── Gas ───────────────────────────────────────────────────
  int gasValue = analogRead(gasPin);

  // ── Blink (edge detection, count per minute) ──────────────
  bool currentBlink = digitalRead(blinkPin);
  if (currentBlink == LOW && lastBlinkState == HIGH) {
    blinkCount++;
  }
  lastBlinkState = currentBlink;

  if (millis() - lastBlinkTime > 60000) {
    blinkCount = 0;
    lastBlinkTime = millis();
  }

  int blinkNow = (currentBlink == LOW) ? 1 : 0;

  // ── Emotion logic (mirrors app.py) ────────────────────────
  if (gasValue > 400) {
    alcoholMode();
  } else if (bpm < 65 && blinkCount < 8) {
    drowsyMode();
  } else if (bpm >= 85 && bpm <= 100 && blinkCount > 20) {
    anxietyMode();
  } else if (bpm > 100 && temperature > 37.2) {
    angryMode();
  } else if (bpm >= 70 && bpm <= 80 && temperature >= 36.5 && temperature <= 37.2) {
    calmMode();
  } else {
    // Normal — no strong signal
    lcd.clear();
    lcd.print("Monitoring...");
    setColor(LOW, HIGH, HIGH);
    digitalWrite(vibrationMotor, LOW);
    digitalWrite(pump, LOW);
    noTone(buzzer);
  }

  // ── CSV output: temp,bpm,gas,blink_now,blink_count ────────
  Serial.print(temperature, 2); Serial.print(",");
  Serial.print(bpm);            Serial.print(",");
  Serial.print(gasValue);       Serial.print(",");
  Serial.print(blinkNow);       Serial.print(",");
  Serial.println(blinkCount);

  delay(1000);
}

// ── Emotion modes ─────────────────────────────────────────────
void calmMode() {
  lcd.clear();
  lcd.print("CALM DRIVER");
  lcd.setCursor(0, 1);
  lcd.print("DRIVE SAFE");
  setColor(LOW, HIGH, LOW);
  digitalWrite(vibrationMotor, LOW);
  digitalWrite(pump, LOW);
  noTone(buzzer);
}

void angryMode() {
  lcd.clear();
  lcd.print("ANGRY DRIVER");
  lcd.setCursor(0, 1);
  lcd.print("CALM DOWN");
  setColor(HIGH, LOW, LOW);
  digitalWrite(vibrationMotor, HIGH);
  digitalWrite(pump, LOW);
  tone(buzzer, 600);
}

void drowsyMode() {
  lcd.clear();
  lcd.print("DROWSY DRIVER");
  lcd.setCursor(0, 1);
  lcd.print("WAKE UP!");
  setColor(LOW, LOW, HIGH);
  digitalWrite(pump, HIGH);
  digitalWrite(vibrationMotor, LOW);
  tone(buzzer, 700);
}

void anxietyMode() {
  lcd.clear();
  lcd.print("ANXIETY");
  lcd.setCursor(0, 1);
  lcd.print("RELAX");
  setColor(HIGH, LOW, HIGH);
  digitalWrite(pump, LOW);
  digitalWrite(vibrationMotor, LOW);
  tone(buzzer, 500);
}

void alcoholMode() {
  lcd.clear();
  lcd.print("ALCOHOL DETECT");
  lcd.setCursor(0, 1);
  lcd.print("STOP VEHICLE");
  setColor(HIGH, HIGH, LOW);
  digitalWrite(pump, LOW);
  digitalWrite(vibrationMotor, LOW);
  tone(buzzer, 1000);
}
