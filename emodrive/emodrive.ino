#include <Wire.h>
#include <LiquidCrystal_I2C.h>

LiquidCrystal_I2C lcd(0x27, 16, 2);

// ── Sensor pins ───────────────────────────────────────────────────────────────
const int TEMP_PIN  = A0;
const int PULSE_PIN = A1;
const int GAS_PIN   = A2;
const int BLINK_PIN = 2;

// ── Output pins ───────────────────────────────────────────────────────────────
const int BUZZER    = 8;
const int MOTOR     = 7;   // vibration motor
const int PUMP      = 6;   // water pump (drowsy alert)

// ── RGB LED (PWM, common cathode) ─────────────────────────────────────────────
const int RED_PIN   = 9;
const int GREEN_PIN = 10;
const int BLUE_PIN  = 11;

// ── BPM — inter-beat interval ─────────────────────────────────────────────────
const int     PULSE_THRESHOLD = 550;
bool          beatDetected    = false;
unsigned long lastBeatTime    = 0;
int           BPM             = 0;

// ── Blink count per minute ────────────────────────────────────────────────────
int           blinkCount      = 0;
unsigned long lastBlinkWindow = 0;
bool          lastBlinkState  = false;

// ── Manual / demo mode (serial command, times out after 15 s) ─────────────────
char          manualCmd   = 'N';
bool          manualMode  = false;
unsigned long manualStart = 0;
const unsigned long MANUAL_TIMEOUT = 15000UL;

// ── Fail-safe mode (serial 'F', active for 15 s) ──────────────────────────────
bool          failSafe      = false;
unsigned long failSafeStart = 0;
const unsigned long FAILSAFE_TIMEOUT = 15000UL;

// ─────────────────────────────────────────────────────────────────────────────
void setup() {
  Serial.begin(9600);

  pinMode(BLINK_PIN, INPUT);
  pinMode(BUZZER,    OUTPUT);
  pinMode(MOTOR,     OUTPUT);
  pinMode(PUMP,      OUTPUT);
  pinMode(RED_PIN,   OUTPUT);
  pinMode(GREEN_PIN, OUTPUT);
  pinMode(BLUE_PIN,  OUTPUT);

  lcd.init();
  lcd.backlight();
  lcd.setCursor(0, 0); lcd.print("EMO Drive");
  lcd.setCursor(0, 1); lcd.print("Initializing...");
  delay(2000);
  lcd.clear();

  lastBlinkWindow = millis();
}

// ── RGB helper (0-255 PWM) ────────────────────────────────────────────────────
void setColor(int r, int g, int b) {
  analogWrite(RED_PIN,   r);
  analogWrite(GREEN_PIN, g);
  analogWrite(BLUE_PIN,  b);
}

// ── CSV output — field order must match serial_reader.py exactly ──────────────
void outputCSV(float temp, int bpm, int gas, int blinkNow, int blinkCnt) {
  Serial.print(temp, 2); Serial.print(",");
  Serial.print(bpm);     Serial.print(",");
  Serial.print(gas);     Serial.print(",");
  Serial.print(blinkNow); Serial.print(",");
  Serial.println(blinkCnt);
}

// ─────────────────────────────────────────────────────────────────────────────
void loop() {

  // ── Serial commands: C A D X L = demo emotion, F = fail-safe ─────────────
  while (Serial.available()) {
    char c = toupper((char)Serial.read());
    if (c == '\n' || c == '\r') continue;
    if (c == 'F') {
      failSafe      = true;
      failSafeStart = millis();
    } else if (c >= 'A' && c <= 'Z') {
      manualCmd   = c;
      manualMode  = true;
      manualStart = millis();
    }
  }

  // ── Fail-safe — highest priority, overrides everything ───────────────────
  if (failSafe) {
    if (millis() - failSafeStart >= FAILSAFE_TIMEOUT) {
      failSafe = false;
      noTone(BUZZER);
      digitalWrite(MOTOR, LOW);
      digitalWrite(PUMP,  LOW);
      lcd.clear();
    } else {
      lcd.clear();
      lcd.setCursor(0, 0); lcd.print("!! FAIL SAFE !!");
      lcd.setCursor(0, 1); lcd.print("System Alert");
      setColor(255, 0, 0);
      tone(BUZZER, 1500);
      digitalWrite(MOTOR, HIGH);
      Serial.println("FAILSAFE");
      return;
    }
  }

  // ── BPM — inter-beat interval (far more accurate than linear map) ─────────
  int pulseValue = analogRead(PULSE_PIN);
  if (pulseValue > PULSE_THRESHOLD && !beatDetected) {
    beatDetected = true;
    unsigned long now  = millis();
    unsigned long diff = now - lastBeatTime;
    if (diff > 300) {        // debounce: 300 ms minimum = 200 BPM max
      BPM          = 60000 / diff;
      lastBeatTime = now;
    }
  }
  if (pulseValue < PULSE_THRESHOLD) {
    beatDetected = false;
  }

  // ── Temperature — LM35 ───────────────────────────────────────────────────
  float temperature = (analogRead(TEMP_PIN) * 5.0 / 1023.0) * 100.0;

  // ── Gas sensor ───────────────────────────────────────────────────────────
  int gasValue = analogRead(GAS_PIN);

  // ── Blink — falling-edge detection + per-minute rolling counter ──────────
  bool currentBlink = (digitalRead(BLINK_PIN) == LOW);
  if (currentBlink && !lastBlinkState) {
    blinkCount++;
  }
  lastBlinkState = currentBlink;

  if (millis() - lastBlinkWindow >= 60000UL) {
    blinkCount      = 0;
    lastBlinkWindow = millis();
  }

  // ── CSV output ────────────────────────────────────────────────────────────
  outputCSV(temperature, BPM, gasValue, currentBlink ? 1 : 0, blinkCount);

  // ── Manual (demo) mode — times out after MANUAL_TIMEOUT ──────────────────
  if (manualMode) {
    if (millis() - manualStart >= MANUAL_TIMEOUT) {
      manualMode = false;
      manualCmd  = 'N';
    } else {
      displayEmotion(manualCmd);
      delay(300);
      return;
    }
  }

  // ── Auto emotion detection ────────────────────────────────────────────────
  // Thresholds mirror emotion.py exactly so LCD and dashboard always agree
  if (gasValue > 400) {
    displayEmotion('L');                                         // Alcohol
  } else if (BPM < 65 && blinkCount < 8) {
    displayEmotion('D');                                         // Drowsy
  } else if (BPM >= 85 && BPM <= 100 && blinkCount > 17) {
    displayEmotion('X');                                         // Anxiety
  } else if (BPM > 100 && temperature > 37.2) {
    displayEmotion('A');                                         // Angry
  } else if (BPM >= 70 && BPM <= 80 &&
             temperature >= 36.5 && temperature <= 37.2) {
    displayEmotion('C');                                         // Calm
  } else {
    lcd.clear();
    lcd.setCursor(0, 0); lcd.print("Monitoring...");
    setColor(0, 150, 150);
    digitalWrite(MOTOR, LOW);
    digitalWrite(PUMP,  LOW);
    noTone(BUZZER);
  }

  delay(300);
}

// ── Emotion display + actuators ───────────────────────────────────────────────
void displayEmotion(char emotion) {
  lcd.clear();
  switch (emotion) {

    case 'C':
      lcd.setCursor(0, 0); lcd.print("CALM DRIVER");
      lcd.setCursor(0, 1); lcd.print("DRIVE SAFE");
      setColor(0, 255, 0);
      noTone(BUZZER);
      digitalWrite(MOTOR, LOW);
      digitalWrite(PUMP,  LOW);
      break;

    case 'A':
      lcd.setCursor(0, 0); lcd.print("ANGRY DRIVER");
      lcd.setCursor(0, 1); lcd.print("CALM DOWN");
      setColor(255, 0, 0);
      tone(BUZZER, 600);
      digitalWrite(MOTOR, HIGH);
      digitalWrite(PUMP,  LOW);
      break;

    case 'D':
      lcd.setCursor(0, 0); lcd.print("DROWSY DRIVER");
      lcd.setCursor(0, 1); lcd.print("WAKE UP!");
      setColor(0, 0, 255);
      tone(BUZZER, 700);
      digitalWrite(MOTOR, LOW);
      digitalWrite(PUMP,  HIGH);
      break;

    case 'X':
      lcd.setCursor(0, 0); lcd.print("ANXIETY");
      lcd.setCursor(0, 1); lcd.print("RELAX");
      setColor(255, 0, 255);
      tone(BUZZER, 500);
      digitalWrite(MOTOR, LOW);
      digitalWrite(PUMP,  LOW);
      break;

    case 'L':
      lcd.setCursor(0, 0); lcd.print("ALCOHOL DETECT");
      lcd.setCursor(0, 1); lcd.print("STOP VEHICLE");
      setColor(255, 255, 0);
      tone(BUZZER, 1000);
      digitalWrite(MOTOR, LOW);
      digitalWrite(PUMP,  LOW);
      break;
  }
}
