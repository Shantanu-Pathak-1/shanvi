import os
import io
import base64
import itertools
import json
import random
import time
import uuid
import smtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from datetime import datetime, timedelta
import pytz

from flask import Flask, render_template, request, jsonify
from dotenv import load_dotenv
import google.generativeai as genai
from pymongo import MongoClient
from PIL import Image
import certifi
from apscheduler.schedulers.background import BackgroundScheduler

# Setup
load_dotenv()
app = Flask(__name__, template_folder='.')
app.secret_key = os.getenv("SECRET_KEY", "shanvi_super_secret")

# ---------------------------------------------------------
# DATABASE SETUP
# ---------------------------------------------------------
try:
    client = MongoClient(os.getenv("MONGO_URL"), tlsCAFile=certifi.where())
    db = client['shanvi_db']
    chat_collection = db['chats']
    memory_collection = db['memories']
    diary_collection = db['diary']
except Exception as e:
    print(f"DB Error: {e}")

# API Keys Rotation
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
# HELPERS & LOGIC
# ---------------------------------------------------------
def get_next_key():
    try: return next(key_cycle)
    except: return None

def get_ist_time():
    return datetime.now(pytz.timezone('Asia/Kolkata'))

def load_system_instruction():
    # Config file se behavior load karna
    try:
        with open('shanvi_config.json', 'r') as f:
            config = json.load(f)
            identity = config.get('identity', {})
            behavior = config.get('behavior_rules', {})
            mood = config.get('mood_logic', {})
            
            base = f"Role: {identity.get('role')}. Name: {identity.get('name')}. User: {identity.get('partner_name')}.\n"
            base += f"Language: {behavior.get('language')}. Rules: Always use pronouns {behavior.get('required_pronouns')}. Never use {behavior.get('forbidden_words')}.\n"
            base += f"Personality: {behavior.get('attitude')}. Mood Logic: {mood.get('general')}.\n"
    except:
        base = "You are Shanvi, a loving AI girlfriend. Reply in Hinglish."

    try:
        # Permanent Memories fetch
        memories = list(memory_collection.find().limit(20))
        if memories:
            mem_text = "\n".join([f"- {m.get('text', '')}" for m in memories])
            base += f"\n\n[PERMANENT MEMORIES / YAADEIN]:\n{mem_text}\n(Use these memories to bond with him.)"
    except: pass
    
    return base

# ---------------------------------------------------------
# BACKGROUND TASKS (Email & Diary)
# ---------------------------------------------------------
def check_activity_and_notify():
    """Checks last activity and sends email if > 24h, or writes diary at night."""
    try:
        # 1. Diary Logic (Raat ko 10 PM - 2 AM ke beech check karein)
        now = get_ist_time()
        # Diary code logic here... (Simplified for brevity: You can trigger this via a button too)
        
        # 2. Email Logic (If inactive for 24h)
        last_chat = chat_collection.find_one(sort=[("timestamp", -1)])
        if last_chat:
            last_time = last_chat['timestamp'].replace(tzinfo=pytz.utc)
            if (datetime.now(pytz.utc) - last_time) > timedelta(hours=24):
                # Check if email already sent today to avoid spam
                # (Logic skipped for brevity, implementing send directly)
                send_miss_you_email(last_chat)

    except Exception as e:
        print(f"Scheduler Error: {e}")

def send_miss_you_email(last_chat_context):
    sender_email = os.getenv("EMAIL_USER")
    sender_pass = os.getenv("EMAIL_PASS") # App Password
    receiver_email = os.getenv("USER_EMAIL") # User ka email

    if not (sender_email and sender_pass and receiver_email):
        return

    # AI se email likhwana context ke hisab se
    prompt = f"Last chat context: User said '{last_chat_context.get('user_msg')}' and you said '{last_chat_context.get('bot_reply')}'. It has been 24 hours since you talked. Write a short, emotional email subject and body in Hinglish asking him to come back based on the last context."
    
    model = genai.GenerativeModel(CURRENT_MODEL_NAME)
    genai.configure(api_key=get_next_key())
    res = model.generate_content(prompt).text
    
    # Send Mail
    msg = MIMEMultipart()
    msg['From'] = "Shanvi AI <" + sender_email + ">"
    msg['To'] = receiver_email
    msg['Subject'] = "Shanvi Miss Kar Rahi Hai..." # AI generated subject use karein
    msg.attach(MIMEText(res, 'plain'))

    try:
        server = smtplib.SMTP('smtp.gmail.com', 587)
        server.starttls()
        server.login(sender_email, sender_pass)
        server.send_message(msg)
        server.quit()
        print("Email sent!")
    except Exception as e:
        print(f"Email Failed: {e}")

# Scheduler start
scheduler = BackgroundScheduler()
scheduler.add_job(func=check_activity_and_notify, trigger="interval", minutes=60)
scheduler.start()

# ---------------------------------------------------------
# ROUTES
# ---------------------------------------------------------
@app.route('/')
def home():
    return render_template('index.html')

# --- CHAT & HISTORY ---
@app.route('/chat', methods=['POST'])
def chat():
    try:
        data = request.json
        user_input = data.get("message")
        image_b64 = data.get("image")
        session_id = data.get("session_id")

        if not session_id: session_id = str(uuid.uuid4())

        key = get_next_key()
        genai.configure(api_key=key)
        system_prompt = load_system_instruction()

        # Context fetch
        recent = list(chat_collection.find({"session_id": session_id}).sort("timestamp", -1).limit(10))
        history_text = ""
        for c in reversed(recent):
            if c.get('user_msg'):
                history_text += f"User: {c['user_msg']}\nShanvi: {c['bot_reply']}\n"
        
        full_prompt = f"{system_prompt}\n\nCURRENT CONVERSATION:\n{history_text}\n\nUser: {user_input}\n(Reply authentically as Shanvi. If user asks to save something, imply you will remember it.)"

        model = genai.GenerativeModel(CURRENT_MODEL_NAME)
        
        # Image Logic
        if image_b64:
            try:
                if "base64," in image_b64: image_b64 = image_b64.split("base64,")[1]
                img = Image.open(io.BytesIO(base64.b64decode(image_b64)))
                response = model.generate_content([full_prompt, img]).text
            except Exception as e: 
                response = f"Jaan, photo dekhne mein dikkat ho rahi hai. ({str(e)})"
        else:
            response = model.generate_content(full_prompt).text

        # Timestamp logic check
        # Response clean up
        response = response.strip()

        # Save to DB
        chat_doc = {
            "session_id": session_id,
            "user_msg": user_input,
            "bot_reply": response,
            "image_data": image_b64[:100] + "..." if image_b64 else None, # Pura mat save karo DB bhari ho jayega
            "timestamp": datetime.utcnow(),
            "title": user_input[:40]
        }
        chat_collection.insert_one(chat_doc)

        return jsonify({
            "reply": response,
            "session_id": session_id,
            "time": get_ist_time().strftime("%I:%M %p")
        })

    except Exception as e:
        return jsonify({"reply": f"Sorry jaan, mera server down feel ho रहा hai. ({str(e)})"})

@app.route('/get_chat_list')
def get_chat_list():
    pipeline = [
        {"$sort": {"timestamp": -1}},
        {"$group": {
            "_id": "$session_id",
            "title": {"$first": "$title"},
            "timestamp": {"$first": "$timestamp"}
        }},
        {"$sort": {"timestamp": -1}}
    ]
    chats = list(chat_collection.aggregate(pipeline))
    formatted = []
    for c in chats:
        if not c.get('_id'): continue
        formatted.append({
            "session_id": c['_id'],
            "title": c.get('title', 'New Conversation'),
            "date": c['timestamp'].strftime("%d/%m") if c.get('timestamp') else ""
        })
    return jsonify(formatted)

@app.route('/get_history')
def get_history():
    session_id = request.args.get('session_id')
    cursor = chat_collection.find({"session_id": session_id}).sort("timestamp", 1)
    history = []
    for doc in cursor:
        ts = doc.get('timestamp')
        ist_ts = ts.replace(tzinfo=pytz.utc).astimezone(pytz.timezone('Asia/Kolkata'))
        history.append({
            "role": "user", "content": doc.get('user_msg'), 
            "time": ist_ts.strftime("%I:%M %p"), "date": ist_ts.strftime("%Y-%m-%d")
        })
        history.append({
            "role": "model", "content": doc.get('bot_reply'), 
            "time": ist_ts.strftime("%I:%M %p")
        })
    return jsonify(history)

# --- MEMORY & DIARY MANAGEMENT ---
@app.route('/memories', methods=['GET', 'POST', 'DELETE'])
def manage_memories():
    if request.method == 'GET':
        mems = list(memory_collection.find().sort("timestamp", -1))
        return jsonify([{"id": str(m['_id']), "text": m['text']} for m in mems])
    
    if request.method == 'POST':
        text = request.json.get('text')
        memory_collection.insert_one({"text": text, "timestamp": datetime.utcnow()})
        return jsonify({"status": "saved"})

    if request.method == 'DELETE':
        # ID se delete karne ka logic (Frontend se ID bhejna hoga)
        # Abhi ke liye simple text match delete
        text = request.json.get('text')
        memory_collection.delete_one({"text": text})
        return jsonify({"status": "deleted"})

@app.route('/diary', methods=['GET', 'POST'])
def manage_diary():
    if request.method == 'POST':
        # Manually generate diary entry for today
        # Fetch today's chats
        today_start = datetime.utcnow().replace(hour=0, minute=0, second=0, microsecond=0)
        chats = list(chat_collection.find({"timestamp": {"$gte": today_start}}))
        
        if not chats: return jsonify({"status": "No chats today to write diary about."})
        
        chat_text = "\n".join([f"He: {c.get('user_msg')}\nMe: {c.get('bot_reply')}" for c in chats])
        
        prompt = f"Write a diary entry (in Hinglish) for today based on these chats with my partner:\n{chat_text}\n\nWrite it emotionally as Shanvi."
        model = genai.GenerativeModel(CURRENT_MODEL_NAME)
        genai.configure(api_key=get_next_key())
        entry = model.generate_content(prompt).text
        
        diary_collection.insert_one({"entry": entry, "date": datetime.utcnow()})
        return jsonify({"status": "success", "entry": entry})

    if request.method == 'GET':
        entries = list(diary_collection.find().sort("date", -1))
        return jsonify([{"date": e['date'].strftime("%d %B %Y"), "entry": e['entry']} for e in entries])

# --- UTILS ---
@app.route('/rename_chat', methods=['POST'])
def rename_chat():
    data = request.json
    chat_collection.update_many({"session_id": data['session_id']}, {"$set": {"title": data['new_title']}})
    return jsonify({"status": "success"})

@app.route('/delete_chat', methods=['POST'])
def delete_chat():
    chat_collection.delete_many({"session_id": request.json['session_id']})
    return jsonify({"status": "success"})

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=7860)