import time
import threading
import requests
from config import (lock, location_data, weather_data, aqi_data, road_data,
                    OPENWEATHER_KEY, AQI_UNHEALTHY_THRESHOLD)

AQI_LABELS = {1: "Good", 2: "Fair", 3: "Moderate", 4: "Poor", 5: "Very Poor"}


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
                    "ip":      d.get("query", "Unknown"),
                })
            print(f"[Location] {location_data['city']}, {location_data['country']}")
            threading.Thread(target=fetch_road_data, daemon=True).start()
            threading.Thread(target=fetch_aqi,       daemon=True).start()
    except Exception as e:
        print(f"[Location] Error: {e}")


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


def fetch_road_data():
    with lock:
        lat = location_data["lat"]
        lon = location_data["lon"]
    if lat == 0.0 and lon == 0.0:
        return

    # ── Step 1: Nominatim reverse geocode ─────────────────────────────────────
    road_nm   = "Unknown Road"
    road_type = "Urban Road"
    is_hw     = False
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

            highway_keywords = ["motorway", "trunk", "primary", "national",
                                "highway", "expressway", "NH", "SH"]
            is_hw = any(kw.lower() in road_nm.lower() or kw.lower() in road_class.lower()
                        for kw in highway_keywords)
            if is_hw:
                road_type = "Highway"
            elif any(x in road_class for x in ["secondary", "tertiary", "residential"]):
                road_type = "City Road"
            elif "track" in road_class or "rural" in road_class:
                road_type = "Rural Road"
            print(f"[OSM] Nominatim OK — {road_type}: {road_nm}")
        else:
            print(f"[OSM] Nominatim bad response: HTTP {r.status_code}")
    except Exception as e:
        print(f"[OSM] Nominatim error: {e}")

    with lock:
        road_data.update({
            "road_type":  road_type,
            "road_name":  road_nm,
            "is_highway": is_hw,
            "osm_loaded": True,
        })

    # ── Step 2: Overpass (hospitals, rest stops) ───────────────────────────────
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
                dist    = round(((el_lat - lat) ** 2 + (el_lon - lon) ** 2) ** 0.5 * 111, 1)
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

    with lock:
        road_data["nearby_hospitals"] = hospitals
        road_data["nearby_rest"]      = rest_stops[:3]
