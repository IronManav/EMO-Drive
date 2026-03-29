import json
import re
import requests
from config import NIM_API_KEY, NIM_BASE_URL, NIM_MODEL
from rag import get_relevant_laws


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
    raw = ""
    try:
        r = requests.post(NIM_BASE_URL,
            headers={"Authorization": f"Bearer {NIM_API_KEY}", "Content-Type": "application/json"},
            json={"model": NIM_MODEL, "messages": [
                {"role": "system", "content": "detailed thinking off"},
                {"role": "user",   "content": system_prompt + "\n\n" + user_prompt},
            ], "max_tokens": 2000, "temperature": 0.6, "top_p": 0.7},
            timeout=90)
        r.raise_for_status()
        raw = r.json()["choices"][0]["message"]["content"].strip()
        print(f"[NIM] Raw response ({len(raw)} chars): {raw}")
        raw = re.sub(r"```json|```", "", raw).strip()
        start = raw.find("{"); end = raw.rfind("}") + 1
        if start != -1 and end > start:
            return json.loads(raw[start:end])
        else:
            print("[NIM] Could not find JSON object in response")
    except requests.exceptions.Timeout:
        print("[NIM] TIMEOUT — request exceeded 90s")
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
        "pre_drive_checklist": ["Ensure all sensors are connected before next session."],
        "law_note":           "Always carry your driving licence and vehicle documents when driving.",
        "session_summary":    "AI analysis could not be completed for this session.",
    }
