import time
import random
import math
import serial
from datetime import datetime

import config as _cfg
from config import lock, data_store, emotion_data, session, SERIAL_PORT, BAUD_RATE
from emotion import derive_emotion

# ── Demo sensor value generators — one place, used by both read_serial and demo_mode ──
_BLINK_RANGES = {
    'c': (15, 20), 'a': (10, 16), 'd': (2, 7), 'x': (18, 24), 'l': (10, 18),
}

def _demo_sensors(key):
    """Return (temp, bpm, gas) for a given demo key."""
    if key == 'c': return round(36.6 + random.uniform(-0.05, 0.05), 2), random.randint(74,  76),  random.randint(100, 200)
    if key == 'a': return round(37.6 + random.uniform(-0.05, 0.05), 2), random.randint(108, 112), random.randint(100, 200)
    if key == 'd': return round(36.2 + random.uniform(-0.05, 0.05), 2), random.randint(57,  59),  random.randint(100, 200)
    if key == 'x': return round(36.9 + random.uniform(-0.05, 0.05), 2), random.randint(91,  93),  random.randint(100, 200)
    if key == 'l': return round(37.0 + random.uniform(-0.05, 0.05), 2), random.randint(97,  99),  random.randint(450, 950)
    return round(36.5 + random.uniform(-0.2, 0.2), 2), random.randint(70, 80), random.randint(100, 200)


def _apply_emotion(demo_override, demo_map, now_ts):
    """Set emotion_data from demo preset. No random drift — preset stays fixed."""
    emotion_data.update(demo_map[demo_override])
    _cfg.emotion_last_updated = now_ts


def _store_sample(now, temp, bpm, gas, blink, blink_count, demo_override, demo_map, now_ts):
    """Append one sensor sample and update emotion. Must be called inside lock."""
    data_store["timestamps"].append(now)
    data_store["temp"].append(temp)
    data_store["heartrate"].append(bpm)
    data_store["mq"].append(gas)
    data_store["blink"].append(blink)
    data_store["blink_count"].append(blink_count)

    if demo_override:
        # 10% chance of briefly showing a related emotion — keeps demo dynamic
        if demo_override != 'l' and random.random() < 0.10:
            other_keys = [k for k in demo_map if k != demo_override and k != 'l']
            emotion_data.update(demo_map[random.choice(other_keys)])
            _cfg.emotion_last_updated = now_ts
        else:
            _apply_emotion(demo_override, demo_map, now_ts)
    else:
        result = derive_emotion(temp, bpm, gas, blink_count)
        if result:
            emotion_data.update(result)
            _cfg.emotion_last_updated = now_ts

    if session["active"]:
        data_store["emotion_log"].append({
            "time":       now,
            "mode":       emotion_data["mode"],
            "risk_level": emotion_data["risk_level"],
        })


# ── Blink state for demo_mode (climbs linearly to target over 60 s) ───────────
class _BlinkTracker:
    def __init__(self, demo_key):
        lo, hi        = _BLINK_RANGES.get(demo_key, (10, 20))
        self.target   = random.randint(lo, hi)
        self.window   = time.time()

    def get(self, now_ts):
        elapsed = now_ts - self.window
        if elapsed >= 60:
            lo, hi      = _BLINK_RANGES.get(_cfg.DEMO_OVERRIDE, (10, 20))
            self.target = random.randint(lo, hi)
            self.window = now_ts
            elapsed     = 0
        # Linearly ramp from 0 to target over the 60-second window
        # so the graph is non-zero from second ~3 and looks realistic
        return min(self.target, round(self.target * min(elapsed / 60.0, 1.0)))


# ─────────────────────────────────────────────────────────────────────────────
def read_serial():
    """
    Primary data thread. Reads CSV from Arduino over serial.
    Falls back to demo_mode() if no serial port is found.
    If DEMO_OVERRIDE is set, sensor values are replaced with preset values
    but the Arduino still drives the LCD and actuators via serial commands.
    """
    DEMO_OVERRIDE = _cfg.DEMO_OVERRIDE
    DEMO_MAP      = _cfg.DEMO_MAP

    try:
        ser = serial.Serial(SERIAL_PORT, BAUD_RATE, timeout=2)
        print(f"[Serial] Connected to {SERIAL_PORT}")

        # Tell Arduino which demo mode to display on its LCD
        if DEMO_OVERRIDE:
            cmd = DEMO_OVERRIDE.upper()
            time.sleep(3)
            ser.write(cmd.encode())
            time.sleep(0.1)
            ser.write(cmd.encode())

        blink_tracker = _BlinkTracker(DEMO_OVERRIDE) if DEMO_OVERRIDE else None

        while True:
            line = ser.readline().decode("utf-8", errors="ignore").strip()
            if not line or not line[0].isdigit():
                # Check for fail-safe marker from Arduino
                if line == "FAILSAFE":
                    with lock:
                        emotion_data.update({
                            "mode":       "FAIL SAFE",
                            "risk_level": "failsafe",
                            "color":      "#FF0000",
                        })
                        _cfg.emotion_last_updated = time.time()
                continue
            parts = line.split(",")
            if len(parts) != 5:
                continue
            try:
                temp, bpm, gas, blink, blink_count = parts
                temp        = float(temp)
                bpm         = int(bpm)
                gas         = int(gas)
                blink       = int(blink)
                blink_count = int(blink_count)
            except ValueError:
                continue

            now    = datetime.now().strftime("%H:%M:%S")
            now_ts = time.time()

            # Replace sensor values with demo presets if flag is set
            if DEMO_OVERRIDE:
                temp, bpm, gas = _demo_sensors(DEMO_OVERRIDE)
                blink_count    = blink_tracker.get(now_ts)

            with lock:
                _store_sample(now, temp, bpm, gas, blink, blink_count,
                              DEMO_OVERRIDE, DEMO_MAP, now_ts)

    except serial.SerialException as e:
        print(f"[Serial] Error: {e} — switching to demo mode")
        if DEMO_OVERRIDE:
            with lock:
                emotion_data.update(DEMO_MAP[DEMO_OVERRIDE])
                _cfg.emotion_last_updated = time.time()
        demo_mode()


# ─────────────────────────────────────────────────────────────────────────────
def demo_mode():
    """
    Software-only demo — no Arduino needed.
    Generates realistic sensor data at 1 Hz.
    If DEMO_OVERRIDE is set, uses preset sensor values and emotion.
    If not, generates sinusoidal BPM and random values for a neutral demo.
    """
    print("[Demo] Demo mode thread started.")
    DEMO_OVERRIDE = _cfg.DEMO_OVERRIDE
    DEMO_MAP      = _cfg.DEMO_MAP

    # Apply emotion preset immediately so dashboard shows correct state from second 1
    if DEMO_OVERRIDE:
        with lock:
            emotion_data.update(DEMO_MAP[DEMO_OVERRIDE])
            _cfg.emotion_last_updated = time.time()
        print(f"[Demo] Preset: {DEMO_MAP[DEMO_OVERRIDE]['mode']}")

    blink_tracker = _BlinkTracker(DEMO_OVERRIDE) if DEMO_OVERRIDE else None
    i = 0

    while True:
        now    = datetime.now().strftime("%H:%M:%S")
        now_ts = time.time()

        if DEMO_OVERRIDE:
            temp, bpm, gas  = _demo_sensors(DEMO_OVERRIDE)
            blink_count     = blink_tracker.get(now_ts)
            blink           = 0
        else:
            temp        = round(36.5 + random.uniform(-0.5, 1.2), 2)
            bpm         = round(72 + 10 * math.sin(i * 0.3) + random.uniform(-4, 4))
            gas         = round(220 + random.uniform(-30, 180))
            blink       = random.choices([0, 1], weights=[85, 15])[0]
            blink_count = 0

        with lock:
            _store_sample(now, temp, bpm, gas, blink, blink_count,
                          DEMO_OVERRIDE, DEMO_MAP, now_ts)

        i += 1
        time.sleep(1)