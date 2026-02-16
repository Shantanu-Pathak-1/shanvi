import os
import io
import base64
import itertools
import json
import random
import time
import uuid  # Naya import
from datetime import datetime
import pytz

from flask import Flask, render_template, request, jsonify
from dotenv import load_dotenv
import google.generativeai as genai
from pymongo import MongoClient
from PIL import Image
import certifi

# Setup
load_dotenv()
app = Flask(__name__, template_folder='.')
app.secret_key = os.getenv("SECRET_KEY", "shanvi_super_secret")

# ---------------------------------------------------------
# DATABASE & API SETUP
# ---------------------------------------------------------
try:
    client = MongoClient(os.getenv("MONGO_URL"), tlsCAFile=certifi.where())
    db = client['shanvi_db']
    chat_collection = db['chats']
    memory_collection = db['memories']
    diary_collection = db['diary'] # Diary wapas
except Exception as e:
    print(f"DB Error: {e}")

api_keys = [
    os.getenv("GEMINI_KEY_1"),
    os.getenv("GEMINI_KEY_2"),
    os.getenv("GEMINI_KEY_3"),
    os.getenv("GEMINI_KEY_4")
]
api_keys = [k for k in api_keys if k and k.strip()]
random.shuffle(api_keys)
key_cycle = itertools.cycle(api_keys)

CURRENT_MODEL_NAME = "gemini-1.5-flash"

# ---------------------------------------------------------
# HELPERS
# ---------------------------------------------------------
def get_next_key():
    try: return next(key_cycle)
    except: return None

def load_system_instruction():
    base = "You are Shanvi, a loving and helpful AI girlfriend. Always reply in Hinglish."
    try:
        # Memory fetch
        memories = list(memory_collection.find().limit(10))
        mem_text = "\n".join([f"- {m['text']}" for m in memories])
        if mem_text:
            base += f"\n\nYAADEIN (Permanent Memories):\n{mem_text}"
    except: pass
    return base

# ---------------------------------------------------------
# ROUTES
# ---------------------------------------------------------
@app.route('/')
def home():
    return render_template('index.html')

# --- SIDEBAR API: Get List of Sessions ---
@app.route('/get_chat_list')
def get_chat_list():
    # Saare unique session_ids nikalo aur unka pehla message as title
    pipeline = [
        {"$sort": {"timestamp": -1}},
        {"$group": {
            "_id": "$session_id",
            "last_msg": {"$first": "$user_msg"},
            "timestamp": {"$first": "$timestamp"},
            "title": {"$first": "$title"} # Agar title saved hai
        }},
        {"$sort": {"timestamp": -1}}
    ]
    chats = list(chat_collection.aggregate(pipeline))
    
    formatted = []
    for c in chats:
        if not c.get('_id'): continue # Skip null sessions
        title = c.get('title') or c.get('last_msg') or "New Chat"
        formatted.append({
            "session_id": c['_id'],
            "title": title[:30] + "..." if len(title) > 30 else title,
            "date": c['timestamp'].strftime("%d/%m") if c.get('timestamp') else ""
        })
    return jsonify(formatted)

# --- Chat History for Specific Session ---
@app.route('/get_history')
def get_history():
    session_id = request.args.get('session_id')
    if not session_id: return jsonify([])
    
    cursor = chat_collection.find({"session_id": session_id}).sort("timestamp", 1)
    history = []
    for doc in cursor:
        ts = doc.get('timestamp')
        ist_ts = ts.replace(tzinfo=pytz.utc).astimezone(pytz.timezone('Asia/Kolkata'))
        
        if doc.get('user_msg'):
            history.append({
                "role": "user",
                "content": doc.get('user_msg'),
                "image": doc.get('image_data'),
                "time": ist_ts.strftime("%I:%M %p"),
                "date": ist_ts.strftime("%Y-%m-%d")
            })
        if doc.get('bot_reply'):
            history.append({
                "role": "model",
                "content": doc.get('bot_reply'),
                "time": ist_ts.strftime("%I:%M %p"),
                "date": ist_ts.strftime("%Y-%m-%d")
            })
    return jsonify(history)

@app.route('/chat', methods=['POST'])
def chat():
    try:
        data = request.json
        user_input = data.get("message")
        image_b64 = data.get("image")
        session_id = data.get("session_id")

        # Naya session agar ID nahi hai
        if not session_id:
            session_id = str(uuid.uuid4())

        # 1. AI Setup
        key = get_next_key()
        genai.configure(api_key=key)
        system_prompt = load_system_instruction()

        # 2. Context (Last 6 msg from THIS session)
        recent = list(chat_collection.find({"session_id": session_id}).sort("timestamp", -1).limit(6))
        history_text = ""
        for c in reversed(recent):
            if c.get('user_msg'):
                history_text += f"User: {c['user_msg']}\nShanvi: {c['bot_reply']}\n"
        
        full_prompt = f"{system_prompt}\n\nCHAT SO FAR:\n{history_text}\n\nUser: {user_input}"

        # 3. Generate
        model = genai.GenerativeModel(CURRENT_MODEL_NAME)
        if image_b64:
            # Image logic same as before...
            try:
                header, encoded = image_b64.split(',', 1)
                img = Image.open(io.BytesIO(base64.b64decode(encoded)))
                response = model.generate_content([full_prompt, img]).text
            except: response = "Image Error"
        else:
            response = model.generate_content(full_prompt).text

        # 4. Save
        # Agar ye pehla message hai, to title bhi set kar sakte hain (optional)
        chat_collection.insert_one({
            "session_id": session_id,
            "user_msg": user_input,
            "bot_reply": response,
            "image_data": image_b64,
            "timestamp": datetime.utcnow(),
            "title": user_input[:40] # Default title
        })

        # Check for [SAVE: ...] logic here (Memory) - Same as old code
        if "[SAVE:" in response:
            try:
                s = response.find("[SAVE:") + 6
                e = response.find("]", s)
                memory_collection.insert_one({"text": response[s:e], "timestamp": datetime.utcnow()})
                response = response.replace(f"[SAVE:{response[s:e]}]", "")
            except: pass

        return jsonify({
            "reply": response,
            "session_id": session_id,
            "time": datetime.now(pytz.timezone('Asia/Kolkata')).strftime("%I:%M %p")
        })

    except Exception as e:
        return jsonify({"reply": f"Error: {str(e)}"})

# --- Rename & Delete Routes ---
@app.route('/rename_chat', methods=['POST'])
def rename_chat():
    try:
        data = request.json
        sid = data.get('session_id')
        new_title = data.get('new_title')
        # Update ALL documents in this session with new title (or keep a separate sessions collection - keeping simple here)
        chat_collection.update_many({"session_id": sid}, {"$set": {"title": new_title}})
        return jsonify({"status": "success"})
    except: return jsonify({"status": "error"})

@app.route('/delete_chat', methods=['POST'])
def delete_chat():
    try:
        sid = request.json.get('session_id')
        chat_collection.delete_many({"session_id": sid})
        return jsonify({"status": "success"})
    except: return jsonify({"status": "error"})

# --- Old Memory/Diary Routes (Restore kiye) ---
@app.route('/get_diary')
def get_diary():
    e = list(diary_collection.find().sort("date", -1).limit(10))
    fmt = []
    for i in e:
        d = i['date']
        if isinstance(d, str): d = datetime.fromisoformat(d)
        fmt.append({"date": d.strftime("%d %B"), "entry": i['entry']})
    return jsonify(fmt)

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=7860)
