import time
import threading
import requests
from collections import deque
from flask import Blueprint, jsonify, render_template, request
import config as _cfg
from config import lock, data_store, session, location_data, weather_data, aqi_data, road_data, emotion_data
from location import fetch_road_data, fetch_aqi

routes = Blueprint("routes", __name__)


@routes.route("/")
def index():
    return render_template("index.html")


@routes.route("/data")
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


@routes.route("/set_location", methods=["POST"])
def set_location():
    d   = request.get_json()
    lat = d.get("lat")
    lon = d.get("lon")
    if lat is None or lon is None:
        return jsonify({"ok": False})
    try:
        headers = {"User-Agent": "EMODrive/1.0 (college project)"}
        r = requests.get(
            f"https://nominatim.openstreetmap.org/reverse?lat={lat}&lon={lon}&format=json",
            headers=headers, timeout=10)
        nd   = r.json()
        addr = nd.get("address", {})
        city   = addr.get("city") or addr.get("town") or addr.get("village") or "Unknown"
        region = addr.get("state", "Unknown")
        country = addr.get("country", "Unknown")
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


@routes.route("/start_session", methods=["POST"])
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


@routes.route("/report")
def get_report():
    with lock:
        ready = session["report_ready"]
        data  = dict(_cfg.report_data)
    if not ready:
        return jsonify({"ready": False})
    return jsonify({"ready": True, "report": data})
