import threading
from collections import deque

# ── CLI / Demo ────────────────────────────────────────────────────────────────
import argparse
parser = argparse.ArgumentParser()
parser.add_argument('--demo', type=str, default=None,
    choices=['c', 'a', 'd', 'x', 'l'],
    help='Override sensors with fake data for a specific emotion')
args, _ = parser.parse_known_args()
DEMO_OVERRIDE = args.demo
print(f"[Config] DEMO_OVERRIDE = {repr(DEMO_OVERRIDE)}")
# ── Hardware / API config ─────────────────────────────────────────────────────
SERIAL_PORT      = "COM3"
BAUD_RATE        = 9600
MAX_POINTS       = 700
SESSION_DURATION = 600

OPENWEATHER_KEY  = "5961f3b74746d180f3ea6bb759c17e77"
NIM_API_KEY      = "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"
NIM_BASE_URL     = "https://integrate.api.nvidia.com/v1/chat/completions"
NIM_MODEL        = "meta/llama-3.3-70b-instruct"

AQI_UNHEALTHY_THRESHOLD = 3

# ── Shared lock ───────────────────────────────────────────────────────────────
lock = threading.RLock()

# ── Session state ─────────────────────────────────────────────────────────────
session = {
    "active":       False,
    "start_time":   None,
    "end_time":     None,
    "elapsed":      0,
    "report_ready": False,
}

# ── Live data store ───────────────────────────────────────────────────────────
data_store = {
    "timestamps":  deque(maxlen=MAX_POINTS),
    "temp":        deque(maxlen=MAX_POINTS),
    "heartrate":   deque(maxlen=MAX_POINTS),
    "mq":          deque(maxlen=MAX_POINTS),
    "blink":       deque(maxlen=MAX_POINTS),
    "blink_count": deque(maxlen=MAX_POINTS),
    "emotion_log": [],
}

location_data = {
    "city": "Unknown", "region": "Unknown",
    "country": "Unknown", "lat": 0.0, "lon": 0.0, "ip": "Unknown",
}

weather_data = {
    "temp_c": None, "feels_like": None, "humidity": None,
    "description": "Unknown", "wind_kph": None,
}

aqi_data = {
    "aqi":         None,
    "aqi_label":   "Unknown",
    "pm2_5":       None,
    "pm10":        None,
    "co":          None,
    "no2":         None,
    "is_polluted": False,
}

road_data = {
    "road_type":        "Unknown",
    "road_name":        "Unknown",
    "is_highway":       False,
    "nearby_hospitals": [],
    "nearby_rest":      [],
    "osm_loaded":       False,
}

emotion_data = {
    "mode":       "MONITORING",
    "risk_level": "unknown",
    "color":      "#87CEEB",
}

# ── Demo emotion presets ──────────────────────────────────────────────────────
DEMO_MAP = {
    'c': {"mode": "CALM",             "risk_level": "safe",    "color": "#32CD32"},
    'a': {"mode": "ANGRY",            "risk_level": "caution", "color": "#DC143C"},
    'd': {"mode": "DROWSY",           "risk_level": "danger",  "color": "#4169E1"},
    'x': {"mode": "ANXIETY",          "risk_level": "anxiety", "color": "#9370DB"},
    'l': {"mode": "ALCOHOL DETECTED", "risk_level": "danger",  "color": "#FFD700"},
}

emotion_last_updated = 0.0
report_data = {}
