from datetime import datetime


def get_circadian_risk():
    hour = datetime.now().hour
    if   2 <= hour < 6:   return {"label": "Very High", "reason": "Late night — peak drowsiness window (2AM–6AM)"}
    elif 13 <= hour < 15: return {"label": "Elevated",  "reason": "Post-lunch dip — common drowsiness window (1PM–3PM)"}
    elif 6 <= hour < 9:   return {"label": "Moderate",  "reason": "Early morning — body not fully alert yet"}
    elif hour >= 22:      return {"label": "High",      "reason": "Night driving — reduced visibility and alertness"}
    else:                 return {"label": "Normal",    "reason": "Daytime hours — optimal driving window"}


def calculate_safety_score(emotion_log, avg_bpm, avg_temp, session_aqi):
    if not emotion_log:
        return 50
    total  = len(emotion_log)
    counts = {}
    for entry in emotion_log:
        m = entry["mode"]
        counts[m] = counts.get(m, 0) + 1

    score = 100
    penalty_map = {
        "ALCOHOL DETECTED": 40, "DROWSY": 30, "ANGRY": 20,
        "ANXIETY": 15, "POOR AIR QUALITY": 5, "MONITORING": 0, "CALM": 0,
    }
    for mode, count in counts.items():
        score -= penalty_map.get(mode, 10) * (count / total)

    if avg_bpm > 100 or avg_bpm < 60: score -= 5
    if avg_temp > 37.5:               score -= 5
    if session_aqi and session_aqi >= 4: score -= 3

    return max(0, min(100, round(score)))


def score_to_badge(score):
    if score >= 85:   return {"label": "EXCELLENT DRIVER",   "color": "#2e9e4f"}
    elif score >= 70: return {"label": "GOOD DRIVER",        "color": "#4caf50"}
    elif score >= 50: return {"label": "NEEDS IMPROVEMENT",  "color": "#d4820a"}
    elif score >= 30: return {"label": "NEEDS REST",         "color": "#e67e22"}
    else:             return {"label": "UNFIT TO DRIVE",     "color": "#c0392b"}


def build_emotion_breakdown(emotion_log):
    counts = {}
    for entry in emotion_log:
        m = entry["mode"]
        counts[m] = counts.get(m, 0) + 1
    breakdown = []
    for mode, count in sorted(counts.items(), key=lambda x: -x[1]):
        mins  = count // 60
        secs  = count % 60
        label = f"{mins}:{secs:02d}" if mins > 0 else f"0:{secs:02d}"
        breakdown.append({"mode": mode, "seconds": count, "label": label})
    return breakdown
