import time
import random
import math
import serial
from datetime import datetime
import config
from config import (lock, data_store, emotion_data, session,
                    SERIAL_PORT, BAUD_RATE, DEMO_OVERRIDE, DEMO_MAP)
from emotion import derive_emotion

# Module-level reference so session.py can read/write it via config
import config as _cfg


def read_serial():
    try:
        ser = serial.Serial(SERIAL_PORT, BAUD_RATE, timeout=2)
        print(f"[Serial] Connected to {SERIAL_PORT}")
        if DEMO_OVERRIDE:
            cmd_map = {'c': 'C', 'a': 'A', 'd': 'D', 'x': 'X', 'l': 'L'}
            cmd = cmd_map.get(DEMO_OVERRIDE)
            if cmd:
                time.sleep(3)
                ser.write(cmd.encode()); time.sleep(0.1); ser.write(cmd.encode())

        _blink_ranges = {'c': (15, 20), 'a': (10, 16), 'd': (2, 7), 'x': (18, 24), 'l': (10, 18)}

        while True:
            line = ser.readline().decode("utf-8").strip()
            if not line or not line[0].isdigit():
                continue
            parts = line.split(",")
            if len(parts) == 5:
                try:
                    temp, bpm, gas, blink, blink_count = parts
                    temp = float(temp); bpm = int(bpm); gas = int(gas)
                    blink = int(blink); blink_count = int(blink_count)
                    now     = datetime.now().strftime("%H:%M:%S")
                    _now_ts = time.time()

                    if DEMO_OVERRIDE:
                        if not hasattr(read_serial, 'demo_blink_val'):
                            lo, hi = _blink_ranges.get(DEMO_OVERRIDE, (10, 20))
                            read_serial.demo_blink_val    = random.randint(lo, hi)
                            read_serial.demo_blink_window = _now_ts
                        if _now_ts - read_serial.demo_blink_window >= 60:
                            lo, hi = _blink_ranges.get(DEMO_OVERRIDE, (10, 20))
                            read_serial.demo_blink_val    = random.randint(lo, hi)
                            read_serial.demo_blink_window = _now_ts
                        if DEMO_OVERRIDE == 'c':
                            temp = round(36.6 + random.uniform(-0.05, 0.05), 2); bpm = random.randint(74, 76);  gas = random.randint(100, 200)
                        elif DEMO_OVERRIDE == 'a':
                            temp = round(37.6 + random.uniform(-0.05, 0.05), 2); bpm = random.randint(108, 112); gas = random.randint(100, 200)
                        elif DEMO_OVERRIDE == 'd':
                            temp = round(36.2 + random.uniform(-0.05, 0.05), 2); bpm = random.randint(57, 59);  gas = random.randint(100, 200)
                        elif DEMO_OVERRIDE == 'x':
                            temp = round(36.9 + random.uniform(-0.05, 0.05), 2); bpm = random.randint(91, 93);  gas = random.randint(100, 200)
                        elif DEMO_OVERRIDE == 'l':
                            temp = round(37.0 + random.uniform(-0.05, 0.05), 2); bpm = random.randint(97, 99);  gas = random.randint(450, 950)
                        blink_count = read_serial.demo_blink_val

                    with lock:
                        now_ts           = time.time()
                        completed_blinks = blink_count

                        data_store["timestamps"].append(now)
                        data_store["temp"].append(temp)
                        data_store["heartrate"].append(bpm)
                        data_store["mq"].append(gas)
                        data_store["blink"].append(blink)
                        data_store["blink_count"].append(completed_blinks)

                        if DEMO_OVERRIDE:
                            if DEMO_OVERRIDE != 'l' and random.random() < 0.10:
                                other_keys = [k for k in DEMO_MAP if k != DEMO_OVERRIDE and k != 'l']
                                emotion_data.update(DEMO_MAP[random.choice(other_keys)])
                            else:
                                emotion_data.update(DEMO_MAP[DEMO_OVERRIDE])
                            _cfg.emotion_last_updated = now_ts
                        else:
                            result = derive_emotion(temp, bpm, gas, completed_blinks)
                            if result:
                                emotion_data.update(result)
                                _cfg.emotion_last_updated = now_ts

                        if session["active"]:
                            data_store["emotion_log"].append({
                                "time": now,
                                "mode": emotion_data["mode"],
                                "risk_level": emotion_data["risk_level"],
                            })
                except ValueError:
                    pass
    except serial.SerialException as e:
        print(f"[Serial] Error: {e} — switching to demo mode")
        demo_mode()


def demo_mode():
    print("[Demo] Demo mode thread started.")
    import config as _cfg
    _blink_ranges = {'c': (15, 20), 'a': (10, 16), 'd': (2, 7), 'x': (18, 24), 'l': (10, 18)}
    lo, hi = _blink_ranges.get(DEMO_OVERRIDE, (10, 20)) if DEMO_OVERRIDE else (10, 20)
    blink_val    = random.randint(lo, hi)
    blink_window = time.time()
    i = 0

    while True:
        now    = datetime.now().strftime("%H:%M:%S")
        now_ts = time.time()

        if DEMO_OVERRIDE:
            if now_ts - blink_window >= 60:
                lo, hi = _blink_ranges.get(DEMO_OVERRIDE, (10, 20))
                blink_val = random.randint(lo, hi); blink_window = now_ts
            if blink_val == 0:
                lo, hi = _blink_ranges.get(DEMO_OVERRIDE, (10, 20))
                blink_val = random.randint(lo, hi)
            if DEMO_OVERRIDE == 'c':
                temp = round(36.6 + random.uniform(-0.05, 0.05), 2); bpm = random.randint(74, 76);  gas = random.randint(100, 200)
            elif DEMO_OVERRIDE == 'a':
                temp = round(37.6 + random.uniform(-0.05, 0.05), 2); bpm = random.randint(108, 112); gas = random.randint(100, 200)
            elif DEMO_OVERRIDE == 'd':
                temp = round(36.2 + random.uniform(-0.05, 0.05), 2); bpm = random.randint(57, 59);  gas = random.randint(100, 200)
            elif DEMO_OVERRIDE == 'x':
                temp = round(36.9 + random.uniform(-0.05, 0.05), 2); bpm = random.randint(91, 93);  gas = random.randint(100, 200)
            elif DEMO_OVERRIDE == 'l':
                temp = round(37.0 + random.uniform(-0.05, 0.05), 2); bpm = random.randint(97, 99);  gas = random.randint(450, 950)
            completed_blinks = min(blink_val, round(blink_val * min((now_ts - blink_window) / 60.0, 1.0)))
            blink = 0
            with lock:
                data_store["timestamps"].append(now)
                data_store["temp"].append(temp)
                data_store["heartrate"].append(bpm)
                data_store["mq"].append(gas)
                data_store["blink"].append(blink)
                data_store["blink_count"].append(completed_blinks)
                if DEMO_OVERRIDE != 'l' and random.random() < 0.10:
                    other_keys = [k for k in DEMO_MAP if k != DEMO_OVERRIDE and k != 'l']
                    emotion_data.update(DEMO_MAP[random.choice(other_keys)])
                else:
                    emotion_data.update(DEMO_MAP[DEMO_OVERRIDE])
                _cfg.emotion_last_updated = now_ts
                if session["active"]:
                    data_store["emotion_log"].append({
                        "time": now,
                        "mode": emotion_data["mode"],
                        "risk_level": emotion_data["risk_level"],
                    })
        else:
            temp  = round(36.5 + random.uniform(-0.5, 1.2), 2)
            bpm   = round(72 + 10 * math.sin(i * 0.3) + random.uniform(-4, 4))
            gas   = round(220 + random.uniform(-30, 180))
            blink = random.choices([0, 1], weights=[85, 15])[0]
            now_ts2 = time.time()
            with lock:
                data_store["timestamps"].append(now)
                data_store["temp"].append(temp)
                data_store["heartrate"].append(bpm)
                data_store["mq"].append(gas)
                data_store["blink"].append(blink)
                data_store["blink_count"].append(0)
                if now_ts2 - _cfg.emotion_last_updated >= 30:
                    result = derive_emotion(temp, bpm, gas, 0)
                    if result:
                        emotion_data.update(result)
                        _cfg.emotion_last_updated = now_ts2
                if session["active"]:
                    data_store["emotion_log"].append({
                        "time": now,
                        "mode": emotion_data["mode"],
                        "risk_level": emotion_data["risk_level"],
                    })
        i += 1
        time.sleep(1)
