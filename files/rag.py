import json
import re

TRAFFIC_LAW_QA = []

EMOTION_KEYWORDS = {
    "ALCOHOL DETECTED": ["drunk", "alcohol", "drink", "section 185", "breathalyser",
                         "blood alcohol", "intoxicat", "section 202"],
    "DROWSY":           ["drowsy", "fatigue", "sleep", "rest", "dangerous driving",
                         "section 184", "rash", "negligent", "accident"],
    "ANGRY":            ["aggressive", "rage", "dangerous", "racing", "speed",
                         "reckless", "section 184", "section 189", "road rage"],
    "ANXIETY":          ["stress", "anxiety", "distracted", "mobile", "phone",
                         "section 184", "attention", "section 177", "seatbelt"],
    "CALM":             ["safe", "licence", "registration", "insurance", "documents",
                         "section 130", "section 3", "permit"],
    "POOR AIR QUALITY": ["pollution", "air", "health", "environment", "road",
                         "public place", "ventilat"],
}


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
