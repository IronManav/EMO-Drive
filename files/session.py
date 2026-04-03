import time
import config as _cfg
from datetime import datetime
from config import lock, data_store, session, location_data, weather_data, aqi_data, road_data
from scoring import get_circadian_risk, calculate_safety_score, score_to_badge, build_emotion_breakdown
from ai_report import generate_ai_report


def build_report():
    print("[Session] Building end-of-session report...")

    with lock:
        temps      = list(data_store["temp"])
        bpms       = list(data_store["heartrate"])
        gases      = list(data_store["mq"])
        blinks     = list(data_store["blink_count"])
        timestamps = list(data_store["timestamps"])
        emo_log    = list(data_store["emotion_log"])
        loc        = dict(location_data)
        wthr       = dict(weather_data)
        aqi        = dict(aqi_data)
        road       = dict(road_data)

    avg_bpm  = round(sum(bpms)  / len(bpms),  1) if bpms  else 0
    avg_temp = round(sum(temps) / len(temps),  2) if temps else 0
    avg_gas  = round(sum(gases) / len(gases),  1) if gases else 0
    peak_bpm = max(bpms)  if bpms  else 0
    peak_gas = max(gases) if gases else 0

    # blink_count is a rolling per-minute counter that climbs then resets each minute.
    # to get avg blinks/min, take the peak of each 60-sample window (1 sample/sec),
    # then average those peaks across all completed windows.
    if blinks:
        window_size = 60
        windows = [blinks[i:i + window_size] for i in range(0, len(blinks), window_size)]
        # only include windows with at least half the samples (handles short sessions / last partial window)
        completed = [w for w in windows if len(w) >= window_size // 2]
        if completed:
            avg_blink = round(sum(max(w) for w in completed) / len(completed), 1)
        else:
            avg_blink = round(max(blinks), 1)
    else:
        avg_blink = 0

    circadian    = get_circadian_risk()
    emotion_bd   = build_emotion_breakdown(emo_log)
    safety_score = calculate_safety_score(emo_log, avg_bpm, avg_temp, aqi.get("aqi"))
    badge        = score_to_badge(safety_score)

    summary = {
        "avg_bpm": avg_bpm, "avg_temp": avg_temp,
        "avg_blink": avg_blink, "avg_gas": avg_gas,
        "peak_bpm": peak_bpm, "peak_gas": peak_gas,
        "emotion_breakdown": emotion_bd,
        "safety_score": safety_score,
        "badge": badge,
        "circadian": circadian,
        "weather": wthr,
        "aqi": aqi,
        "road": road,
        "location": f"{loc['city']}, {loc['region']}, {loc['country']}",
        "timestamps": timestamps,
        "temp_series":  temps,
        "bpm_series":   bpms,
        "gas_series":   gases,
        "blink_series": blinks,
        "emotion_log":  emo_log,
        "session_time": datetime.now().strftime("%d %b %Y, %H:%M"),
    }

    summary["ai"] = generate_ai_report(summary)

    with lock:
        _cfg.report_data = summary
        session["report_ready"] = True

    print(f"[Session] Report ready. Score={safety_score}, Badge={badge['label']}")


def session_timer():
    while True:
        time.sleep(1)
        should_build = False
        with lock:
            if session["active"]:
                elapsed = time.time() - session["start_time"]
                session["elapsed"] = int(elapsed)
                if elapsed >= _cfg.SESSION_DURATION:
                    session["active"]   = False
                    session["end_time"] = datetime.now().strftime("%H:%M:%S")
                    should_build = True
        if should_build:
            build_report()