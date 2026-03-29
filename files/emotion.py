from config import lock, aqi_data, AQI_UNHEALTHY_THRESHOLD


def derive_emotion(temp, bpm, gas, blink_count):
    with lock:
        is_polluted = aqi_data["is_polluted"]
        current_aqi = aqi_data["aqi"] or 1

    if gas > 400:
        if is_polluted and current_aqi >= AQI_UNHEALTHY_THRESHOLD:
            return {"mode": "POOR AIR QUALITY", "risk_level": "caution", "color": "#FF8C00"}
        else:
            return {"mode": "ALCOHOL DETECTED", "risk_level": "danger",  "color": "#FFD700"}
    elif bpm < 65 and blink_count < 8:
        return {"mode": "DROWSY",           "risk_level": "danger",  "color": "#4169E1"}
    elif 85 <= bpm <= 100 and blink_count > 17:
        return {"mode": "ANXIETY",          "risk_level": "anxiety", "color": "#9370DB"}
    elif bpm > 100 and temp > 37.2:
        return {"mode": "ANGRY",            "risk_level": "caution", "color": "#DC143C"}
    elif 70 <= bpm <= 80 and 36.5 <= temp <= 37.2:
        return {"mode": "CALM",             "risk_level": "safe",    "color": "#32CD32"}
    else:
        return None
