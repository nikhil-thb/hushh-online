# app.py - Updated with Server-Side Photo Upload Logic

import eventlet
eventlet.monkey_patch()

from flask import Flask, render_template, request, session, redirect, url_for, jsonify, send_from_directory
import os
from flask_socketio import SocketIO, emit, join_room, leave_room, disconnect
from better_profanity import profanity
from datetime import datetime, timedelta, timezone
import secrets
import mysql.connector
from mysql.connector import errorcode
from mysql.connector import pooling
import hashlib
from functools import wraps
import threading
import time
import logging
import re
import json
import base64 
from PIL import Image 
from io import BytesIO 

import firebase_admin
from firebase_admin import credentials, auth, firestore

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

active_users_lock = threading.Lock()
video_state_lock = threading.Lock()

# Firebase Admin SDK Setup
try:
    cred = credentials.Certificate("hushh-63300-firebase-adminsdk-fbsvc-199e052150.json") 
    if not firebase_admin._apps:
        firebase_admin.initialize_app(cred)
    firestore_db = firestore.client()
    logger.info("Firebase Admin SDK initialized successfully.")
except Exception as e:
    logger.error(f"FATAL: Failed to initialize Firebase Admin SDK: {e}")

# Flask & SocketIO Setup
# ----------------------------------------------------
# 1. DEFINE FLASK APP FIRST!
app = Flask(__name__)
app.config['SECRET_KEY'] = secrets.token_hex(32)
# ----------------------------------------------------

# --- NEW: Image Upload Setup (MOVED HERE) ---
# 2. NOW you can configure the app object
UPLOAD_FOLDER = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'verification_uploads')
app.config['UPLOAD_FOLDER'] = UPLOAD_FOLDER
os.makedirs(UPLOAD_FOLDER, exist_ok=True)
logger.info(f"Verification upload folder created at: {UPLOAD_FOLDER}")
# -------------------------------


# MySQL Configuration
db_config = {
    'host': 'localhost', 
    'user': 'admin',
    'password': 'RoadE@202406', 
    'database': 'hushh_db',
}
app.config['MYSQL_HOST'] = db_config['host']
app.config['MYSQL_USER'] = db_config['user']
app.config['MYSQL_PASSWORD'] = db_config['password']
app.config['MYSQL_DB'] = db_config['database']

socketio = SocketIO(
    app,
    cors_allowed_origins="*",
    logger=False,
    engineio_logger=False,
    async_mode='eventlet',
    ping_timeout=60,
    ping_interval=25
)

# Prompt Data Structure and Loading
PROMPT_FILE = 'prompts.json'
# ... (load_prompts_from_json, Utility Functions, Database Setup, etc. remain unchanged) ...

def load_prompts_from_json():
    """Loads all prompts from the external JSON file."""
    try:
        with open(PROMPT_FILE, 'r', encoding='utf-8') as f:
            prompts_data = json.load(f)
        logger.info(f"Loaded {len(prompts_data)} prompts from {PROMPT_FILE}")
        return prompts_data
    except FileNotFoundError:
        logger.error(f"FATAL: Prompt file '{PROMPT_FILE}' not found.")
        return []
    except json.JSONDecodeError as e:
        logger.error(f"FATAL: Error decoding JSON from '{PROMPT_FILE}': {e}")
        return []

# Utility Functions
def get_client_ip(request):
    if 'X-Forwarded-For' in request.headers:
        ip = request.headers['X-Forwarded-For'].split(',')[0].strip()
        return ip
    return request.remote_addr

def get_client_fingerprint(request):
    user_agent = request.headers.get('User-Agent', '')
    accept_language = request.headers.get('Accept-Language', '')
    accept_encoding = request.headers.get('Accept-Encoding', '')
    fingerprint_data = f"{user_agent}|{accept_language}|{accept_encoding}"
    return hashlib.md5(fingerprint_data.encode()).hexdigest()

def check_message(message):
    profanity.load_censor_words()
    return profanity.contains_profanity(message)

def get_current_time():
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")

def create_video_room():
    room_id = f"hushh-video-{secrets.token_hex(8)}"
    return room_id

def calculate_interest_match(interests1, interests2):
    if not isinstance(interests1, set) or not isinstance(interests2, set): return 0.0
    if not interests1 or not interests2: return 0.0
    try:
        intersection = len(interests1.intersection(interests2))
        union = len(interests1.union(interests2))
        similarity = float(intersection) / union if union > 0 else 0.0
        return similarity
    except Exception:
        return 0.0

def broadcast_user_count():
    count = 0
    with active_users_lock:
        count = len(active_users)
    socketio.emit('updateUserCount', count)

def check_dating_compatibility(user1_profile, user2_profile):
    """
    Check if two users are compatible based on dating preferences.
    Returns True if they should be matched, False otherwise.
    """
    user1_gender = user1_profile.get('gender', '').lower()
    user1_pref = user1_profile.get('datingPreference', '').lower()
    user2_gender = user2_profile.get('gender', '').lower()
    user2_pref = user2_profile.get('datingPreference', '').lower()
    
    # Bisexual matches with everyone
    if user1_pref == 'bisexual' or user2_pref == 'bisexual':
        return True
    
    # Straight: male wants female, female wants male
    if user1_pref == 'straight':
        if user1_gender == 'male' and user2_gender != 'female':
            return False
        if user1_gender == 'female' and user2_gender != 'male':
            return False
    
    if user2_pref == 'straight':
        if user2_gender == 'male' and user1_gender != 'female':
            return False
        if user2_gender == 'female' and user1_gender != 'male':
            return False
    
    # Gay: male wants male
    if user1_pref == 'gay' and (user1_gender != 'male' or user2_gender != 'male'):
        return False
    if user2_pref == 'gay' and (user2_gender != 'male' or user1_gender != 'male'):
        return False
    
    # Lesbian: female wants female
    if user1_pref == 'lesbian' and (user1_gender != 'female' or user2_gender != 'female'):
        return False
    if user2_pref == 'lesbian' and (user2_gender != 'female' or user1_gender != 'female'):
        return False
    
    return True

# Database Setup
db_pool = None

def init_db():
    db_name = app.config['MYSQL_DB']
    conn = None
    cursor = None
    try:
        conn = mysql.connector.connect(host=app.config['MYSQL_HOST'], user=app.config['MYSQL_USER'], password=app.config['MYSQL_PASSWORD'])
        cursor = conn.cursor()
        try:
            cursor.execute(f"CREATE DATABASE {db_name} DEFAULT CHARACTER SET 'utf8mb4'")
        except mysql.connector.Error as err:
            if err.errno != errorcode.ER_DB_CREATE_EXISTS: raise err
        cursor.execute(f"USE {db_name}")
        
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS banned_ips (
                id INT PRIMARY KEY AUTO_INCREMENT, ip_address VARCHAR(45) NOT NULL, browser_fingerprint VARCHAR(32) NOT NULL,
                ban_reason TEXT, ban_expires TIMESTAMP NULL DEFAULT NULL, ads_watched INT DEFAULT 0,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP, UNIQUE KEY ip_fingerprint (ip_address, browser_fingerprint)
            )
        ''')
        
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS reports (
                id INT PRIMARY KEY AUTO_INCREMENT, reporter_ip VARCHAR(45), reported_ip VARCHAR(45),
                reported_fingerprint VARCHAR(32), reason TEXT, chat_room VARCHAR(255),
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        ''')
        
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS match_prompts (
                id INT PRIMARY KEY AUTO_INCREMENT, 
                prompt TEXT NOT NULL, 
                category VARCHAR(255) NOT NULL,
                region VARCHAR(255) NOT NULL DEFAULT 'global',
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        ''')
        
        cursor.execute("SELECT COUNT(*) FROM match_prompts")
        if cursor.fetchone()[0] == 0:
            prompts_data = load_prompts_from_json()
            if prompts_data:
                data_to_insert = [(p['prompt'], p['category'], p.get('region', 'global')) for p in prompts_data]
                insert_query = "INSERT INTO match_prompts (prompt, category, region) VALUES (%s, %s, %s)"
                cursor.executemany(insert_query, data_to_insert)
                logger.info(f"Seeded {len(data_to_insert)} prompts into the database.")
            else:
                logger.warning("No prompts loaded from JSON to seed the database.")
        
        conn.commit()
    except mysql.connector.Error as err:
        logger.error(f"Database initialization failed: {err}")
        if conn: conn.rollback()
        raise err
    finally:
        if cursor: cursor.close()
        if conn: conn.close()

def init_db_pool():
    global db_pool
    if db_pool is not None: return True
    try:
        db_pool = mysql.connector.pooling.MySQLConnectionPool(pool_name="hushh_pool", pool_size=25, pool_reset_session=True, **db_config)
        conn = db_pool.get_connection()
        conn.close()
        return True
    except mysql.connector.Error as err:
        db_pool = None
        return False
    
def get_db_connection():
    global db_pool
    if db_pool is None:
        if not init_db_pool(): return None
    try:
        conn = db_pool.get_connection()
        return conn
    except mysql.connector.Error as err:
        db_pool = None
        return None

def check_ip_ban(ip_address, browser_fingerprint):
    conn = get_db_connection()
    if not conn: return False, 0, "DB Error", 0
    cursor = conn.cursor()
    result = None
    try:
        cursor.execute('''SELECT ban_expires, ban_reason, ads_watched FROM banned_ips WHERE ip_address = %s AND browser_fingerprint = %s''', (ip_address, browser_fingerprint))
        result = cursor.fetchone()
    except mysql.connector.Error:
        return False, 0, "DB Error", 0
    finally:
        cursor.close()
        conn.close()

    if not result: return False, 0, None, 0
    ban_expires_db, ban_reason, ads_watched = result

    if ban_expires_db is None: return True, 999999, ban_reason, ads_watched
    
    now_utc = datetime.now(timezone.utc)
    ban_expires_aware = ban_expires_db.replace(tzinfo=timezone.utc)
        
    if now_utc > ban_expires_aware: return False, 0, None, 0

    remaining_delta = ban_expires_aware - now_utc
    remaining_minutes = max(0, int(remaining_delta.total_seconds() / 60))
    return True, remaining_minutes, ban_reason, ads_watched

def get_random_match_prompt(user_region=None):
    """Fetches a random prompt, prioritizing global and user's region."""
    conn = get_db_connection()
    if not conn: return "What's your biggest guilty pleasure?"
    cursor = conn.cursor(dictionary=True)
    prompt = "What's your biggest guilty pleasure?"
    
    try:
        if user_region and user_region != 'global':
            cursor.execute("SELECT prompt FROM match_prompts WHERE region IN ('global', %s) ORDER BY RAND() LIMIT 1", (user_region,))
        else:
            cursor.execute("SELECT prompt FROM match_prompts WHERE region = 'global' ORDER BY RAND() LIMIT 1")
        
        result = cursor.fetchone()
        if result: prompt = result['prompt']

    except mysql.connector.Error as err:
        logger.error(f"Error fetching match prompt: {err}")
    finally:
        cursor.close()
        conn.close()
    return prompt

# Run DB Init
logger.info("Attempting initial database check...")
try:
    init_db()
except Exception as e:
    logger.error(f"FATAL: Database initialization failed at startup: {e}")
logger.info("Initial database check complete.")

if not init_db_pool():
    logger.error("FATAL: Could not initialize database pool at startup")

def check_user_ban(uid, ip_address, browser_fingerprint):
    return check_ip_ban(ip_address, browser_fingerprint)

# Active User and Waiting List Structure
active_users = {}
video_waiting_users = []
video_active_rooms = {}

# Decorator
def firebase_authenticated(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        uid = request.args.get('firebase_uid')
        profile_json = request.args.get('profile')
        
        if not uid or not profile_json:
             disconnect(request.sid, silent=True)
             return False
        
        try:
            auth.get_user(uid) 
            profile_data = json.loads(profile_json)
            
            if not all(k in profile_data for k in ['name', 'age', 'gender', 'interests']):
                 disconnect(request.sid, silent=True)
                 return False

            request.uid = uid
            request.profile_data = profile_data
            
            return f(*args, **kwargs)

        except (firebase_admin.exceptions.FirebaseError, json.JSONDecodeError, KeyError) as e:
            logger.warning(f"Invalid Firebase UID or Profile data for SID {request.sid}: {e}")
            disconnect(request.sid, silent=True)
            return False
            
    return decorated_function

# Routes

# NEW: Route to serve uploaded verification photos
@app.route('/verification_uploads/<filename>')
def serve_verification_photo(filename):
    # This route serves files saved by the upload_verification_photo endpoint
    # Security note: In production, these should be stored in a secured cloud bucket/CDN.
    return send_from_directory(app.config['UPLOAD_FOLDER'], filename)

# NEW: API Endpoint for Server-Side Photo Upload (Saves image locally)
@app.route('/upload_verification_photo', methods=['POST'])
def upload_verification_photo():
    data = request.get_json()
    uid = data.get('uid')
    image_data_base64 = data.get('image_data')

    if not uid or not image_data_base64:
        return jsonify({"error": "Missing UID or image data"}), 400

    try:
        # 1. Decode Base64 image data
        # Remove header like "data:image/jpeg;base64,"
        if 'base64,' not in image_data_base64:
             raise ValueError("Invalid Base64 format. Header not found.")

        encoded = image_data_base64.split(',', 1)[1]
        image_bytes = base64.b64decode(encoded)
        
        # 2. Process and save the image using PIL
        image = Image.open(BytesIO(image_bytes))
        
        # Generate unique filename: UID_TIMESTAMP.jpg
        uid_hash = hashlib.sha256(uid.encode()).hexdigest()[:16] 
        filename = f"{uid_hash}_{int(time.time() * 1000)}.jpg"
        file_path = os.path.join(app.config['UPLOAD_FOLDER'], filename)
        
        # Save the image
        # You may want to resize/optimize the image here before saving
        image.save(file_path, 'jpeg')
        
        # 3. Construct the server URL/path to be stored
        photo_url = url_for('serve_verification_photo', filename=filename, _external=True)
        
        logger.info(f"Photo uploaded for {uid}. URL: {photo_url}")
        
        # 4. Return the URL to the client
        return jsonify({"message": "Upload successful", "photo_url": photo_url}), 200

    except ValueError as e:
        logger.error(f"Base64 error for {uid}: {e}")
        return jsonify({"error": "Invalid image data format. Ensure JPEG Base64 is correct."}), 400
    except Exception as e:
        logger.error(f"Error processing image upload for {uid}: {e}")
        return jsonify({"error": f"Internal server error during image processing: {e}"}), 500


@app.route('/')
def chat():
    return render_template('index.html')

@app.route('/video-chat')
def video_chat():
    return render_template('video_chat.html')

@app.route('/profile-setup')
def profile_setup():
    return render_template('profile-setup.html')

@app.route('/<filename>')
def serve_static(filename):
    return send_from_directory(os.path.dirname(os.path.abspath(__file__)), filename)

@app.route('/service-worker.js')
def service_worker():
    return send_from_directory(os.path.dirname(os.path.abspath(__file__)), 'service-worker.js')

# SOCKETIO HANDLERS
@socketio.on('connect')
@firebase_authenticated
def handle_connect():
    client_ip = get_client_ip(request)
    browser_fingerprint = get_client_fingerprint(request)
    session_id = request.sid
    uid = request.uid
    profile_data = request.profile_data

    try:
        is_banned, remaining_minutes, ban_reason, ads_watched = check_user_ban(uid, client_ip, browser_fingerprint)
        if is_banned:
            emit('banned', {'message': f'You are banned. Reason: {ban_reason}', 'duration': remaining_minutes, 'ads_watched': ads_watched, 'timestamp': get_current_time()})
            return False

        with active_users_lock:
            active_users[session_id] = {
                'uid': uid, 'ip': client_ip, 'fingerprint': browser_fingerprint,
                'room': None, 'last_activity': time.time(), 'profile': profile_data
            }

        broadcast_user_count()
        emit('connected', {'user_id': session_id, 'username': profile_data.get('name', 'Hushh User')})
        logger.info(f"User connected: {session_id} ({profile_data.get('name')})")

    except Exception as e:
        logger.error(f"Error during connect for SID {session_id}: {e}", exc_info=True)
        disconnect(session_id, silent=True)
        return False

@socketio.on('find-video-match')
@firebase_authenticated
def handle_find_video_match(data=None):
    user_sid = request.sid
    client_ip = get_client_ip(request)
    uid = request.uid

    try:
        user_data = active_users.get(user_sid)
        if not user_data: return

        current_user_profile = user_data['profile']
        current_user_interests = set(current_user_profile.get('interests', []))
        user_region = current_user_profile.get('region', 'global')
        user_date_scope = current_user_profile.get('dateScope', 'global')
        user_verified = current_user_profile.get('photo_verified', False)

        is_banned, rem_mins, ban_reason, ads_w = check_user_ban(uid, client_ip, user_data['fingerprint'])
        if is_banned:
            emit('banned', {'message': f'Banned: {ban_reason}', 'duration': rem_mins, 'ads_watched': ads_w, 'timestamp': get_current_time()})
            return

        current_user_data = {
            'sid': user_sid, 'uid': uid, 'ip': client_ip, 'fingerprint': user_data['fingerprint'],
            'profile': current_user_profile, 'joined': time.time()
        }

        matched_user_data = None
        room_id = None
        shared_interests_str = ""
        
        with video_state_lock:
            video_waiting_users[:] = [u for u in video_waiting_users if u['sid'] != user_sid]

            best_match_partner = None
            best_match_score = -1.0 
            best_match_index = -1

            for i in range(len(video_waiting_users) - 1, -1, -1):
                potential_partner = video_waiting_users[i]
                
                # Skip same IP/UID
                if potential_partner['ip'] == client_ip or potential_partner['uid'] == uid: 
                    continue

                partner_profile = potential_partner['profile']
                
                # Check dating compatibility
                if not check_dating_compatibility(current_user_profile, partner_profile):
                    continue
                
                # Check location compatibility
                if user_date_scope == 'local':
                    partner_date_scope = partner_profile.get('dateScope', 'global')
                    partner_region = partner_profile.get('region', 'global')
                    
                    # Both must be local and from same region
                    if partner_date_scope != 'local' or partner_region != user_region:
                        continue

                partner_interests = set(partner_profile.get('interests', []))
                match_score = calculate_interest_match(current_user_interests, partner_interests)
                
                if match_score > best_match_score:
                    best_match_score = match_score
                    best_match_partner = potential_partner
                    best_match_index = i

            if best_match_partner is not None and best_match_index != -1:
                matched_user_data = video_waiting_users.pop(best_match_index)
                
                initiator_profile = current_user_data['profile']
                receiver_profile = matched_user_data['profile']
                
                receiver_verified = receiver_profile.get('photo_verified', False) 
                initiator_verified = initiator_profile.get('photo_verified', False)

                shared_interests = current_user_interests.intersection(set(receiver_profile.get('interests', [])))
                shared_interests_str = ", ".join(sorted(list(shared_interests)))
                
                room_id = create_video_room()
                prompt = get_random_match_prompt(user_region if user_date_scope == 'local' else None)
                
                video_active_rooms[room_id] = {
                    "users": [current_user_data, matched_user_data], 
                    "created": time.time(),
                    "status": "timed_date", 
                    "prompt": prompt, 
                    "match_decision": {}
                 }

        if matched_user_data and room_id:
            try:
                join_room(room_id, sid=user_sid)
                join_room(room_id, sid=matched_user_data['sid'])

                match_data_initiator = {
                    'room': room_id, 
                    'initiator': True, 
                    'shared_interests': shared_interests_str,
                    'remote_name': receiver_profile.get('name', 'Stranger'),
                    'remote_photo': receiver_profile.get('photoURL'),
                    'remote_verified': receiver_verified
                }
                match_data_receiver = {
                    'room': room_id, 
                    'initiator': False, 
                    'shared_interests': shared_interests_str,
                    'remote_name': initiator_profile.get('name', 'Stranger'),
                    'remote_photo': initiator_profile.get('photoURL'),
                    'remote_verified': initiator_verified
                }

                emit('video-matched', match_data_initiator, room=user_sid)
                emit('video-matched', match_data_receiver, room=matched_user_data['sid'])

                logger.info(f"Match created: {room_id} - {initiator_profile.get('name')} <-> {receiver_profile.get('name')}")

                def send_timed_date_start(room_id, prompt):
                    socketio.sleep(3) 
                    socketio.emit('start_timed_date', {'prompt': prompt}, room=room_id)
                    logger.info(f"Timed date started in room {room_id}")
                    
                socketio.start_background_task(send_timed_date_start, room_id, prompt)

            except Exception as e:
                logger.error(f"Error joining/emitting for video pair {room_id}: {e}", exc_info=True)
        elif not matched_user_data:
            with video_state_lock:
                 if not any(u['sid'] == current_user_data['sid'] for u in video_waiting_users):
                     video_waiting_users.append(current_user_data)
            emit('video-waiting')
            logger.info(f"User {user_sid} added to waiting queue")

    except Exception as e:
        logger.error(f"Find video match fatal error for {user_sid}: {e}", exc_info=True)
        emit('error', {'message': 'An internal error occurred finding a match.'})

# WebRTC Signaling Handlers
@socketio.on('video-offer')
@firebase_authenticated
def handle_video_offer(data):
    room_id = data.get('room')
    offer = data.get('offer')
    
    if not room_id or not offer:
        return
    
    logger.info(f"Relaying offer in room {room_id}")
    emit('video-offer', {'offer': offer}, room=room_id, skip_sid=request.sid)

@socketio.on('video-answer')
@firebase_authenticated
def handle_video_answer(data):
    room_id = data.get('room')
    answer = data.get('answer')
    
    if not room_id or not answer:
        return
    
    logger.info(f"Relaying answer in room {room_id}")
    emit('video-answer', {'answer': answer}, room=room_id, skip_sid=request.sid)

@socketio.on('ice-candidate')
@firebase_authenticated
def handle_ice_candidate(data):
    room_id = data.get('room')
    candidate = data.get('candidate')
    
    if not room_id or not candidate:
        return
    
    logger.info(f"Relaying ICE candidate in room {room_id}")
    emit('ice-candidate', {'candidate': candidate}, room=room_id, skip_sid=request.sid)

@socketio.on('match_decision')
@firebase_authenticated
def handle_match_decision(data):
    user_sid = request.sid
    room_id = data.get('room')
    action = data.get('action')

    if action not in ['continue', 'end'] or not room_id: return

    partner_sid = None
    
    with video_state_lock:
        if room_id not in video_active_rooms: return
        
        room_data = video_active_rooms[room_id]
        room_data['match_decision'][user_sid] = action
        
        for user in room_data['users']:
            if user['sid'] != user_sid:
                partner_sid = user['sid']
                break
        
        partner_action = room_data['match_decision'].get(partner_sid)
        
        if action == 'end' or partner_action == 'end':
            leave_room(room_id, sid=user_sid)
            leave_room(room_id, sid=partner_sid)

            if room_id in video_active_rooms: 
                del video_active_rooms[room_id]
                
            emit('video-user-disconnected', room=user_sid)
            emit('video-user-disconnected', room=partner_sid)
            logger.info(f"Date ended in room {room_id}")
            return

        elif action == 'continue' and partner_action == 'continue':
            room_data['status'] = 'matched'
            emit('paired_match', room=room_id)
            logger.info(f"Successful match in room {room_id}")
            return
            
        elif action == 'continue' and not partner_action:
            emit('match_decision_received', {'action': 'continue'}, room=partner_sid)

@socketio.on('disconnect')
def handle_disconnect():
    user_sid = request.sid
    if not user_sid: return
    
    logger.info(f"User disconnecting: {user_sid}")
    
    with active_users_lock:
        if user_sid in active_users: 
            del active_users[user_sid]

    partner_sid_to_notify = None
    room_id_to_delete = None
    
    with video_state_lock:
        video_waiting_users[:] = [u for u in video_waiting_users if u['sid'] != user_sid]

        for room_id, room_data in list(video_active_rooms.items()):
            users_in_room = room_data.get('users', [])
            user_index = -1
            
            for i, user in enumerate(users_in_room):
                if user.get('sid') == user_sid: 
                    user_index = i
                elif user.get('sid') != user_sid: 
                    partner_sid_to_notify = user.get('sid')

            if user_index != -1: 
                users_in_room.pop(user_index)
                if not users_in_room: 
                    room_id_to_delete = room_id
                break 

        if room_id_to_delete and room_id_to_delete in video_active_rooms: 
            del video_active_rooms[room_id_to_delete]
            logger.info(f"Room {room_id_to_delete} deleted")

    if partner_sid_to_notify:
        leave_room(room_id_to_delete, sid=user_sid)
        emit('video-user-disconnected', room=partner_sid_to_notify)
        logger.info(f"Notified partner {partner_sid_to_notify} of disconnection")

    broadcast_user_count()

# Main Execution
if __name__ == '__main__':
    logger.info("--- Starting Hushh Application ---")
    socketio.run(app, host="0.0.0.0", port=5000, debug=True, use_reloader=True)