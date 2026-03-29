from flask import Flask, jsonify, render_template, request
import argparse
import serial
import threading
import time
import requests
import json
import re
from collections import deque
from datetime import datetime

# ── CLI argument ──────────────────────────────────────────────────────────────
parser = argparse.ArgumentParser()
parser.add_argument('--demo', type=str, default=None,
    choices=['c','a','d','x','l'],
    help='Override sensors with fake data for a specific emotion')
args, _ = parser.parse_known_args()
DEMO_OVERRIDE = args.demo

# ── Config ────────────────────────────────────────────────────────────────────
SERIAL_PORT       = "COM3"
BAUD_RATE         = 9600
MAX_POINTS        = 700          # 10 min x ~1 reading/sec + buffer
SESSION_DURATION  = 60          # 10 minutes in seconds

OPENWEATHER_KEY   = "5961f3b74746d180f3ea6bb759c17e77"

NIM_API_KEY       = "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"
NIM_BASE_URL      = "https://integrate.api.nvidia.com/v1/chat/completions"
NIM_MODEL         = "meta/llama-3.3-70b-instruct"

AQI_UNHEALTHY_THRESHOLD = 3     # OWM AQI scale 1-5; 3=Moderate, 4=Poor, 5=Very Poor

app = Flask(__name__)
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
    "emotion_log": [],   # {time, mode, risk_level} — full session history
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

blink_minute_data = {
    "last_minute_count": 0,
    "current_count":     0,
    "window_start":      0.0,
}

emotion_last_updated = 0.0
report_data = {}

if DEMO_OVERRIDE:
    _demo_map = {
        'c': {"mode": "CALM",             "risk_level": "safe",    "color": "#32CD32"},
        'a': {"mode": "ANGRY",            "risk_level": "caution", "color": "#DC143C"},
        'd': {"mode": "DROWSY",           "risk_level": "danger",  "color": "#4169E1"},
        'x': {"mode": "ANXIETY",          "risk_level": "anxiety", "color": "#9370DB"},
        'l': {"mode": "ALCOHOL DETECTED", "risk_level": "danger",  "color": "#FFD700"},
    }
    emotion_data.update(_demo_map[DEMO_OVERRIDE])
    emotion_last_updated = time.time()


# ── Traffic Law RAG ───────────────────────────────────────────────────────────
TRAFFIC_LAW_QA = []

def load_traffic_law(path="traffic_rules_dataset.jsonl"):
    qa_pairs = []
    try:
        with open(path, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                obj = json.loads(line)
                text = obj.get("text", "")
                user_match = re.search(
                    r"<\|start_header_id\|>user<\|end_header_id\|>\s*(.*?)<\|eot_id\|>",
                    text, re.DOTALL)
                asst_match = re.search(
                    r"<\|start_header_id\|>assistant<\|end_header_id\|>\s*(.*?)<\|eot_id\|>",
                    text, re.DOTALL)
                if user_match and asst_match:
                    qa_pairs.append({
                        "question": user_match.group(1).strip(),
                        "answer":   asst_match.group(1).strip(),
                    })
        print(f"[RAG] Loaded {len(qa_pairs)} traffic law entries.")
    except FileNotFoundError:
        print("[RAG] WARNING: traffic_rules_dataset.jsonl not found.")
    return qa_pairs


EMOTION_KEYWORDS = {
    "ALCOHOL DETECTED": ["drunk","alcohol","drink","section 185","breathalyser","blood alcohol","intoxicat","section 202"],
    "DROWSY":           ["drowsy","fatigue","sleep","rest","dangerous driving","section 184","rash","negligent","accident"],
    "ANGRY":            ["aggressive","rage","dangerous","racing","speed","reckless","section 184","section 189","road rage"],
    "ANXIETY":          ["stress","anxiety","distracted","mobile","phone","section 184","attention","section 177","seatbelt"],
    "CALM":             ["safe","licence","registration","insurance","documents","section 130","section 3","permit"],
    "POOR AIR QUALITY": ["pollution","air","health","environment","road","public place","ventilat"],
}

def get_relevant_laws(emotion_modes, top_n=5):
    if not TRAFFIC_LAW_QA:
        return ""
    keywords = []
    for em in emotion_modes:
        keywords += EMOTION_KEYWORDS.get(em, [])
    keywords = list(set(keywords))
    scored = []
    for qa in TRAFFIC_LAW_QA:
        combined = (qa["question"] + " " + qa["answer"]).lower()
        score = sum(1 for kw in keywords if kw.lower() in combined)
        scored.append((score, qa))
    scored.sort(key=lambda x: x[0], reverse=True)
    top = [qa for score, qa in scored if score > 0][:top_n]
    if len(top) < top_n:
        extras = [qa for score, qa in scored if score == 0]
        top += extras[:top_n - len(top)]
    if not top:
        return ""
    lines = ["### Relevant Indian Traffic Law (Motor Vehicles Act, 1988)"]
    for i, qa in enumerate(top, 1):
        lines.append(f"\nQ{i}: {qa['question']}")
        lines.append(f"A{i}: {qa['answer']}")
    return "\n".join(lines)


# ── Emotion logic ─────────────────────────────────────────────────────────────
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


# ── Location ──────────────────────────────────────────────────────────────────
def fetch_location():
    try:
        r = requests.get("http://ip-api.com/json/", timeout=8)
        d = r.json()
        if d.get("status") == "success":
            with lock:
                location_data.update({
                    "city":    d.get("city",       "Unknown"),
                    "region":  d.get("regionName", "Unknown"),
                    "country": d.get("country",    "Unknown"),
                    "lat":     d.get("lat",  0.0),
                    "lon":     d.get("lon",  0.0),
                    "ip":      d.get("query","Unknown"),
                })
            print(f"[Location] {location_data['city']}, {location_data['country']}")
            threading.Thread(target=fetch_road_data, daemon=True).start()
            threading.Thread(target=fetch_aqi,       daemon=True).start()
    except Exception as e:
        print(f"[Location] Error: {e}")


# ── Weather ───────────────────────────────────────────────────────────────────
def fetch_weather():
    while True:
        try:
            with lock:
                city = location_data["city"]
                lat  = location_data["lat"]
                lon  = location_data["lon"]
            if city == "Unknown" or (lat == 0.0 and lon == 0.0):
                time.sleep(2)
                continue
            url = (f"http://api.openweathermap.org/data/2.5/weather"
                   f"?lat={lat}&lon={lon}&appid={OPENWEATHER_KEY}&units=metric")
            r = requests.get(url, timeout=15)
            d = r.json()
            if r.status_code == 200:
                with lock:
                    weather_data.update({
                        "temp_c":      round(d["main"]["temp"],       1),
                        "feels_like":  round(d["main"]["feels_like"], 1),
                        "humidity":    d["main"]["humidity"],
                        "description": d["weather"][0]["description"].title(),
                        "wind_kph":    round(d["wind"]["speed"] * 3.6, 1),
                    })
                print(f"[Weather] {weather_data['temp_c']}C, {weather_data['description']}")
            else:
                print(f"[Weather] Bad response: HTTP {r.status_code}")
        except Exception as e:
            print(f"[Weather] Error: {e}")
        time.sleep(300)  


# ── AQI (same OWM key) ────────────────────────────────────────────────────────
AQI_LABELS = {1:"Good", 2:"Fair", 3:"Moderate", 4:"Poor", 5:"Very Poor"}

def fetch_aqi():
    while True:
        try:
            with lock:
                lat = location_data["lat"]
                lon = location_data["lon"]
            if lat == 0.0 and lon == 0.0:
                time.sleep(3)
                continue
            url = (f"http://api.openweathermap.org/data/2.5/air_pollution"
                   f"?lat={lat}&lon={lon}&appid={OPENWEATHER_KEY}")
            r = requests.get(url, timeout=15)
            d = r.json()
            if r.status_code == 200:
                aqi_val = d["list"][0]["main"]["aqi"]
                comp    = d["list"][0]["components"]
                with lock:
                    aqi_data.update({
                        "aqi":         aqi_val,
                        "aqi_label":   AQI_LABELS.get(aqi_val, "Unknown"),
                        "pm2_5":       round(comp.get("pm2_5", 0), 1),
                        "pm10":        round(comp.get("pm10",  0), 1),
                        "co":          round(comp.get("co",    0), 1),
                        "no2":         round(comp.get("no2",   0), 1),
                        "is_polluted": aqi_val >= AQI_UNHEALTHY_THRESHOLD,
                    })
                print(f"[AQI] {aqi_data['aqi_label']} (AQI {aqi_val}), PM2.5={aqi_data['pm2_5']}")
            else:
                print(f"[AQI] Bad response: HTTP {r.status_code}")
        except Exception as e:
            print(f"[AQI] Error: {e}")
        time.sleep(300)


# ── OSM Road Intelligence ─────────────────────────────────────────────────────
def fetch_road_data():
    with lock:
        lat = location_data["lat"]
        lon = location_data["lon"]
    if lat == 0.0 and lon == 0.0:
        return

    # ── Step 1: Nominatim reverse geocode ─────────────────────
    road_nm    = "Unknown Road"
    road_type  = "Urban Road"
    is_hw      = False
    try:
        headers = {"User-Agent": "EMODrive/1.0 (college project)"}
        nom_url = (f"https://nominatim.openstreetmap.org/reverse"
                   f"?lat={lat}&lon={lon}&format=json")
        r = requests.get(nom_url, headers=headers, timeout=10)
        if r.status_code == 200 and r.text.strip():
            d = r.json()
            addr      = d.get("address", {})
            road_nm   = (addr.get("road") or addr.get("motorway") or addr.get("trunk")
                         or addr.get("primary") or "Unknown Road")
            road_class = d.get("type", "") or d.get("class", "")

            highway_keywords = ["motorway","trunk","primary","national",
                                "highway","expressway","NH","SH"]
            is_hw = any(kw.lower() in road_nm.lower() or kw.lower() in road_class.lower()
                        for kw in highway_keywords)
            if is_hw:
                road_type = "Highway"
            elif any(x in road_class for x in ["secondary","tertiary","residential"]):
                road_type = "City Road"
            elif "track" in road_class or "rural" in road_class:
                road_type = "Rural Road"
            print(f"[OSM] Nominatim OK — {road_type}: {road_nm}")
        else:
            print(f"[OSM] Nominatim bad response: HTTP {r.status_code}")
    except Exception as e:
        print(f"[OSM] Nominatim error: {e}")

    # Commit road type even if Overpass fails below
    with lock:
        road_data.update({
            "road_type":  road_type,
            "road_name":  road_nm,
            "is_highway": is_hw,
            "osm_loaded": True,
        })

    # ── Step 2: Overpass (hospitals, rest stops) ───────────────
    hospitals  = []
    rest_stops = []
    try:
        overpass_url = "https://overpass-api.de/api/interpreter"
        query = f"""
        [out:json][timeout:15];
        (
          node["amenity"="hospital"](around:5000,{lat},{lon});
          node["amenity"="clinic"](around:5000,{lat},{lon});
          node["highway"="rest_area"](around:5000,{lat},{lon});
          node["amenity"="fuel"](around:3000,{lat},{lon});
        );
        out body 8;
        """
        r2 = requests.post(overpass_url, data={"data": query}, timeout=25)
        if r2.status_code == 200 and r2.text.strip():
            elements = r2.json().get("elements", [])
            for el in elements:
                tags    = el.get("tags", {})
                name    = tags.get("name", "Unnamed")
                amenity = tags.get("amenity", "")
                hw      = tags.get("highway", "")
                el_lat  = el.get("lat", 0)
                el_lon  = el.get("lon", 0)
                dist    = round(((el_lat-lat)**2 + (el_lon-lon)**2)**0.5 * 111, 1)
                if amenity in ("hospital", "clinic") and len(hospitals) < 3:
                    hospitals.append({"name": name, "dist_km": dist})
                elif hw == "rest_area" or amenity == "fuel":
                    rest_stops.append({
                        "name": name if name != "Unnamed" else "Rest/Fuel Stop",
                        "dist_km": dist
                    })
            print(f"[OSM] Overpass OK — {len(hospitals)} hospitals, {len(rest_stops)} rest stops")
        else:
            print(f"[OSM] Overpass bad response: HTTP {r2.status_code}")
    except Exception as e:
        print(f"[OSM] Overpass error: {e}")

    # Update with whatever Overpass returned (even if empty)
    with lock:
        road_data["nearby_hospitals"] = hospitals
        road_data["nearby_rest"]      = rest_stops[:3]


# ── Circadian risk ────────────────────────────────────────────────────────────
def get_circadian_risk():
    hour = datetime.now().hour
    if   2 <= hour < 6:  return {"label":"Very High", "reason":"Late night — peak drowsiness window (2AM–6AM)"}
    elif 13 <= hour < 15: return {"label":"Elevated",  "reason":"Post-lunch dip — common drowsiness window (1PM–3PM)"}
    elif 6 <= hour < 9:  return {"label":"Moderate",  "reason":"Early morning — body not fully alert yet"}
    elif hour >= 22:     return {"label":"High",      "reason":"Night driving — reduced visibility and alertness"}
    else:                return {"label":"Normal",    "reason":"Daytime hours — optimal driving window"}


# ── Safety score ──────────────────────────────────────────────────────────────
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
    if score >= 85: return {"label":"EXCELLENT DRIVER", "color":"#2e9e4f"}
    elif score >= 70: return {"label":"GOOD DRIVER",      "color":"#4caf50"}
    elif score >= 50: return {"label":"NEEDS IMPROVEMENT","color":"#d4820a"}
    elif score >= 30: return {"label":"NEEDS REST",       "color":"#e67e22"}
    else:             return {"label":"UNFIT TO DRIVE",   "color":"#c0392b"}


# ── Emotion breakdown ─────────────────────────────────────────────────────────
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


# ── NIM Report Generator ──────────────────────────────────────────────────────
def generate_ai_report(summary):
    emotion_modes = list({e["mode"] for e in summary["emotion_breakdown"]})
    law_context   = get_relevant_laws(emotion_modes, top_n=5)

    system_prompt = f"""You are a driver safety analyst reviewing a completed 10-minute drive session recorded by EMO Drive, an IoT vehicle monitoring system deployed in India.

You will be given averaged sensor data, emotion state breakdown, environmental conditions, and road context.
Produce a thorough, honest, and human-friendly safety analysis.

{law_context}

WRITING RULES:
1. Write in plain, warm, conversational English — like a caring expert talking to the driver.
2. Never quote section numbers directly in your analysis. Law knowledge should inform your advice naturally.
3. Be specific to the actual data — reference the actual emotions, BPM, and conditions given.
4. Each bullet point must be one clear sentence under 15 words.
5. fitness_to_drive must be exactly one of: "FIT TO DRIVE" / "DRIVE WITH CAUTION" / "REST BEFORE DRIVING" / "DO NOT DRIVE"
6. Reply with ONLY valid JSON. No markdown, no explanation."""

    user_prompt = f"""SESSION SUMMARY:
Duration: 10 minutes
Safety Score: {summary['safety_score']}/100
Emotion Breakdown: {json.dumps(summary['emotion_breakdown'])}
Average BPM: {summary['avg_bpm']}
Average Temp: {summary['avg_temp']}C
Average Blinks/min: {summary['avg_blink']}
Peak BPM: {summary['peak_bpm']}
Peak Gas: {summary['peak_gas']}

ENVIRONMENT:
Weather: {summary['weather'].get('description','Unknown')}, {summary['weather'].get('temp_c','?')}C, Humidity {summary['weather'].get('humidity','?')}%, Wind {summary['weather'].get('wind_kph','?')} km/h
AQI: {summary['aqi'].get('aqi_label','Unknown')} (AQI {summary['aqi'].get('aqi','?')}), PM2.5={summary['aqi'].get('pm2_5','?')} ug/m3
Road: {summary['road'].get('road_type','Unknown')} — {summary['road'].get('road_name','Unknown')}
Time of Day Risk: {summary['circadian']['label']} — {summary['circadian']['reason']}
Location: {summary['location']}

Respond with ONLY this JSON:
{{
  "fitness_to_drive": "<verdict>",
  "fitness_risk": "<safe|caution|danger>",
  "why_it_happened": ["<point 1>","<point 2>","<point 3>"],
  "recommendations": ["<tip 1>","<tip 2>","<tip 3>","<tip 4>"],
  "pre_drive_checklist": ["<item 1>","<item 2>","<item 3>"],
  "law_note": "<one plain-English sentence about a relevant law>",
  "session_summary": "<2 sentence plain English summary of the overall drive>"
}}"""

    print(f"[NIM] Sending request... prompt chars: {len(system_prompt + user_prompt)}")
    try:
        r = requests.post(NIM_BASE_URL,
            headers={"Authorization": f"Bearer {NIM_API_KEY}", "Content-Type": "application/json"},
            json={"model": NIM_MODEL, "messages": [
                {"role":"system","content":"detailed thinking off"},
                {"role":"user",  "content":system_prompt + "\n\n" + user_prompt},
            ], "max_tokens":2000, "temperature":0.6, "top_p":0.7},
            timeout=90)
        r.raise_for_status()
        raw = r.json()["choices"][0]["message"]["content"].strip()
        print(f"[NIM] Raw response ({len(raw)} chars): {raw}")
        raw = re.sub(r"```json|```","",raw).strip()
        start = raw.find("{"); end = raw.rfind("}") + 1
        if start != -1 and end > start:
            return json.loads(raw[start:end])
        else:
            print(f"[NIM] Could not find JSON object in response")
    except requests.exceptions.Timeout:
        print(f"[NIM] TIMEOUT — request exceeded 90s")
    except requests.exceptions.ConnectionError as e:
        print(f"[NIM] CONNECTION ERROR: {e}")
    except requests.exceptions.HTTPError as e:
        print(f"[NIM] HTTP ERROR {e.response.status_code}: {e.response.text[:500]}")
    except json.JSONDecodeError as e:
        print(f"[NIM] JSON PARSE ERROR: {e}")
        print(f"[NIM] Attempted to parse: {raw[:500]}")
    except Exception as e:
        print(f"[NIM] UNEXPECTED ERROR ({type(e).__name__}): {e}")

    return {
        "fitness_to_drive":   "DRIVE WITH CAUTION",
        "fitness_risk":       "caution",
        "why_it_happened":    ["AI analysis unavailable — check your NIM API key."],
        "recommendations":    ["Please verify your NIM API key and try again."],
        "pre_drive_checklist":["Ensure all sensors are connected before next session."],
        "law_note":           "Always carry your driving licence and vehicle documents when driving.",
        "session_summary":    "AI analysis could not be completed for this session.",
    }


# ── End-of-session report builder ─────────────────────────────────────────────
def build_report():
    global report_data
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

    avg_bpm   = round(sum(bpms)   / len(bpms),   1) if bpms   else 0
    avg_temp  = round(sum(temps)  / len(temps),   2) if temps  else 0
    avg_blink = round(sum(blinks) / len(blinks),  1) if blinks else 0
    avg_gas   = round(sum(gases)  / len(gases),   1) if gases  else 0
    peak_bpm  = max(bpms)  if bpms  else 0
    peak_gas  = max(gases) if gases else 0

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
        report_data = summary
        session["report_ready"] = True

    print(f"[Session] Report ready. Score={safety_score}, Badge={badge['label']}")


# ── Session timer ─────────────────────────────────────────────────────────────
def session_timer():
    while True:
        time.sleep(1)
        should_build = False
        with lock:
            if session["active"]:
                elapsed = time.time() - session["start_time"]
                session["elapsed"] = int(elapsed)
                if elapsed >= SESSION_DURATION:
                    session["active"]   = False
                    session["end_time"] = datetime.now().strftime("%H:%M:%S")
                    should_build = True
        if should_build:
            build_report()


# ── Serial reader ─────────────────────────────────────────────────────────────
def read_serial():
    try:
        ser = serial.Serial(SERIAL_PORT, BAUD_RATE, timeout=2)
        print(f"[Serial] Connected to {SERIAL_PORT}")
        if DEMO_OVERRIDE:
            cmd_map = {'c':'C','a':'A','d':'D','x':'X','l':'L'}
            cmd = cmd_map.get(DEMO_OVERRIDE)
            if cmd:
                time.sleep(3)
                ser.write(cmd.encode()); time.sleep(0.1); ser.write(cmd.encode())

        while True:
            line = ser.readline().decode("utf-8").strip()
            if not line or not line[0].isdigit():
                continue
            parts = line.split(",")
            if len(parts) == 5:
                try:
                    temp, bpm, gas, blink, blink_count = parts
                    temp=float(temp); bpm=int(bpm); gas=int(gas)
                    blink=int(blink); blink_count=int(blink_count)
                    now = datetime.now().strftime("%H:%M:%S")

                    import random as _r
                    _now_ts = time.time()
                    _blink_ranges = {'c':(15,20),'a':(10,16),'d':(2,7),'x':(18,24),'l':(10,18)}

                    if DEMO_OVERRIDE:
                        if not hasattr(read_serial,'demo_blink_val'):
                            lo,hi=_blink_ranges.get(DEMO_OVERRIDE,(10,20))
                            read_serial.demo_blink_val    = _r.randint(lo,hi)
                            read_serial.demo_blink_window = _now_ts
                        if _now_ts - read_serial.demo_blink_window >= 60:
                            lo,hi=_blink_ranges.get(DEMO_OVERRIDE,(10,20))
                            read_serial.demo_blink_val    = _r.randint(lo,hi)
                            read_serial.demo_blink_window = _now_ts
                        if DEMO_OVERRIDE=='c':
                            temp=round(36.6+_r.uniform(-0.05,0.05),2);bpm=_r.randint(74,76);gas=_r.randint(100,200)
                        elif DEMO_OVERRIDE=='a':
                            temp=round(37.6+_r.uniform(-0.05,0.05),2);bpm=_r.randint(108,112);gas=_r.randint(100,200)
                        elif DEMO_OVERRIDE=='d':
                            temp=round(36.2+_r.uniform(-0.05,0.05),2);bpm=_r.randint(57,59);gas=_r.randint(100,200)
                        elif DEMO_OVERRIDE=='x':
                            temp=round(36.9+_r.uniform(-0.05,0.05),2);bpm=_r.randint(91,93);gas=_r.randint(100,200)
                        elif DEMO_OVERRIDE=='l':
                            temp=round(37.0+_r.uniform(-0.05,0.05),2);bpm=_r.randint(97,99);gas=_r.randint(450,950)
                        blink_count = read_serial.demo_blink_val

                    with lock:
                        now_ts = time.time()
                        completed_blinks = blink_count

                        data_store["timestamps"].append(now)
                        data_store["temp"].append(temp)
                        data_store["heartrate"].append(bpm)
                        data_store["mq"].append(gas)
                        data_store["blink"].append(blink)
                        data_store["blink_count"].append(completed_blinks)

                        global emotion_last_updated
                        if DEMO_OVERRIDE:
                            if DEMO_OVERRIDE != 'l' and _r.random() < 0.10:
                                other_keys = [k for k in _demo_map if k != DEMO_OVERRIDE and k != 'l']
                                emotion_data.update(_demo_map[_r.choice(other_keys)])
                            else:
                                emotion_data.update(_demo_map[DEMO_OVERRIDE])
                            emotion_last_updated = now_ts
                        else:
                            result = derive_emotion(temp, bpm, gas, completed_blinks)
                            if result:
                                emotion_data.update(result)
                                emotion_last_updated = now_ts

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
    import random, math
    global emotion_last_updated
    i = 0
    _blink_ranges = {'c':(15,20),'a':(10,16),'d':(2,7),'x':(18,24),'l':(10,18)}
    lo,hi = _blink_ranges.get(DEMO_OVERRIDE,(10,20)) if DEMO_OVERRIDE else (10,20)
    blink_val    = __import__('random').randint(lo,hi)
    blink_window = time.time()

    while True:
        now    = datetime.now().strftime("%H:%M:%S")
        now_ts = time.time()

        if DEMO_OVERRIDE:
            if now_ts - blink_window >= 60:
                lo,hi=_blink_ranges.get(DEMO_OVERRIDE,(10,20))
                blink_val=random.randint(lo,hi); blink_window=now_ts
            if blink_val == 0:
                lo,hi=_blink_ranges.get(DEMO_OVERRIDE,(10,20))
                blink_val=random.randint(lo,hi)
            if DEMO_OVERRIDE=='c':
                temp=round(36.6+random.uniform(-0.05,0.05),2);bpm=random.randint(74,76);gas=random.randint(100,200)
            elif DEMO_OVERRIDE=='a':
                temp=round(37.6+random.uniform(-0.05,0.05),2);bpm=random.randint(108,112);gas=random.randint(100,200)
            elif DEMO_OVERRIDE=='d':
                temp=round(36.2+random.uniform(-0.05,0.05),2);bpm=random.randint(57,59);gas=random.randint(100,200)
            elif DEMO_OVERRIDE=='x':
                temp=round(36.9+random.uniform(-0.05,0.05),2);bpm=random.randint(91,93);gas=random.randint(100,200)
            elif DEMO_OVERRIDE=='l':
                temp=round(37.0+random.uniform(-0.05,0.05),2);bpm=random.randint(97,99);gas=random.randint(450,950)
            completed_blinks = min(blink_val, round(blink_val * min((now_ts - blink_window) / 60.0, 1.0)))
            blink = 0
            with lock:
                data_store["timestamps"].append(now)
                data_store["temp"].append(temp)
                data_store["heartrate"].append(bpm)
                data_store["mq"].append(gas)
                data_store["blink"].append(blink)
                data_store["blink_count"].append(completed_blinks)
                # Use preset emotion 90% of time, sprinkle others 10%
                # Alcohol is never sprinkled in or replaced — only shows when explicitly set
                if DEMO_OVERRIDE != 'l' and random.random() < 0.10:
                    other_keys = [k for k in _demo_map if k != DEMO_OVERRIDE and k != 'l']
                    emotion_data.update(_demo_map[random.choice(other_keys)])
                else:
                    emotion_data.update(_demo_map[DEMO_OVERRIDE])
                emotion_last_updated = now_ts
                if session["active"]:
                    data_store["emotion_log"].append({
                        "time": now,
                        "mode": emotion_data["mode"],
                        "risk_level": emotion_data["risk_level"],
                    })
        else:
            temp  = round(36.5 + random.uniform(-0.5,1.2), 2)
            bpm   = round(72 + 10*math.sin(i*0.3) + random.uniform(-4,4))
            gas   = round(220 + random.uniform(-30,180))
            blink = random.choices([0,1], weights=[85,15])[0]
            now_ts2 = time.time()
            with lock:
                data_store["timestamps"].append(now)
                data_store["temp"].append(temp)
                data_store["heartrate"].append(bpm)
                data_store["mq"].append(gas)
                data_store["blink"].append(blink)
                data_store["blink_count"].append(0)
                if now_ts2 - emotion_last_updated >= 30:
                    result = derive_emotion(temp, bpm, gas, 0)
                    if result:
                        emotion_data.update(result)
                        emotion_last_updated = now_ts2
                if session["active"]:
                    data_store["emotion_log"].append({
                        "time": now,
                        "mode": emotion_data["mode"],
                        "risk_level": emotion_data["risk_level"],
                    })
        i += 1
        time.sleep(1)


# ── Routes ────────────────────────────────────────────────────────────────────
@app.route("/")
def index():
    return render_template("index.html")

@app.route("/data")
def get_data():
    with lock:
        payload = {
            "timestamps":  list(data_store["timestamps"]),
            "temp":        list(data_store["temp"]),
            "heartrate":   list(data_store["heartrate"]),
            "mq":          list(data_store["mq"]),
            "blink":       list(data_store["blink"]),
            "blink_count": list(data_store["blink_count"]),
            "emotion":     dict(emotion_data),
            "location":    dict(location_data),
            "weather":     dict(weather_data),
            "aqi":         dict(aqi_data),
            "road":        dict(road_data),
            "session":     dict(session),
        }
    return jsonify(payload)

@app.route("/set_location", methods=["POST"])
def set_location():
    d = request.get_json()
    lat = d.get("lat")
    lon = d.get("lon")
    if lat is None or lon is None:
        return jsonify({"ok": False})
    # Reverse geocode with Nominatim
    try:
        headers = {"User-Agent": "EMODrive/1.0 (college project)"}
        r = requests.get(
            f"https://nominatim.openstreetmap.org/reverse?lat={lat}&lon={lon}&format=json",
            headers=headers, timeout=10)
        nd = r.json()
        addr = nd.get("address", {})
        city   = addr.get("city") or addr.get("town") or addr.get("village") or "Unknown"
        region = addr.get("state", "Unknown")
        country= addr.get("country", "Unknown")
    except Exception as e:
        print(f"[Location] Reverse geocode error: {e}")
        city = "Unknown"; region = "Unknown"; country = "Unknown"
    with lock:
        location_data.update({
            "city": city, "region": region, "country": country,
            "lat": lat, "lon": lon,
        })
    print(f"[Location] GPS override → {city}, {region}, {country}")
    threading.Thread(target=fetch_road_data, daemon=True).start()
    threading.Thread(target=fetch_aqi,       daemon=True).start()
    return jsonify({"ok": True, "city": city, "region": region, "country": country})


@app.route("/start_session", methods=["POST"])
def start_session():
    with lock:
        if session["active"]:
            return jsonify({"ok": False, "msg": "Session already active"})
        for k in data_store:
            if isinstance(data_store[k], deque):
                data_store[k].clear()
            elif isinstance(data_store[k], list):
                data_store[k].clear()
        session.update({
            "active":       True,
            "start_time":   time.time(),
            "end_time":     None,
            "elapsed":      0,
            "report_ready": False,
        })
    print("[Session] Started.")
    return jsonify({"ok": True})

@app.route("/report")
def get_report():
    with lock:
        ready = session["report_ready"]
        data  = dict(report_data)
    if not ready:
        return jsonify({"ready": False})
    return jsonify({"ready": True, "report": data})


if __name__ == "__main__":
    TRAFFIC_LAW_QA = load_traffic_law("traffic_rules_dataset.jsonl")
    threading.Thread(target=fetch_location,  daemon=True).start()
    threading.Thread(target=demo_mode if DEMO_OVERRIDE else read_serial, daemon=True).start()
    threading.Thread(target=fetch_weather,   daemon=True).start()
    threading.Thread(target=fetch_aqi,       daemon=True).start()
    threading.Thread(target=session_timer,   daemon=True).start()
    app.run(debug=False, host="0.0.0.0", port=5000)