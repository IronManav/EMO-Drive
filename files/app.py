import time
import threading
from flask import Flask

import config as _cfg
from config import DEMO_OVERRIDE, emotion_data, DEMO_MAP
from rag import load_traffic_law
import rag
from location import fetch_location, fetch_weather, fetch_aqi
from serial_reader import read_serial, demo_mode
from session import session_timer
from routes import routes

app = Flask(__name__)
app.register_blueprint(routes)

if __name__ == "__main__":
    # Apply demo emotion preset if flag was passed
    if DEMO_OVERRIDE:
        emotion_data.update(DEMO_MAP[DEMO_OVERRIDE])
        _cfg.emotion_last_updated = time.time()

    rag.TRAFFIC_LAW_QA = load_traffic_law("traffic_rules_dataset.jsonl")

    threading.Thread(target=fetch_location,                              daemon=True).start()
    threading.Thread(target=demo_mode if DEMO_OVERRIDE else read_serial, daemon=True).start()
    threading.Thread(target=fetch_weather,                               daemon=True).start()
    threading.Thread(target=fetch_aqi,                                   daemon=True).start()
    threading.Thread(target=session_timer,                               daemon=True).start()

    app.run(debug=False, host="0.0.0.0", port=5000)
