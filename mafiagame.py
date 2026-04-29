import os
import random
import string
import hashlib
import pymysql
import pymysql.cursors
from flask import Flask, render_template, request, session, jsonify, redirect, url_for
from flask_socketio import SocketIO, emit, join_room, leave_room
from flask_cors import CORS
from datetime import datetime, timedelta

app = Flask(__name__)
app.secret_key = os.environ.get('SECRET_KEY', 'mafia-secret-key-change-in-prod')
CORS(app)

socketio = SocketIO(
    app,
    cors_allowed_origins="*",
    async_mode='gevent',
    ping_timeout=60,
    ping_interval=25,
    logger=False,
    engineio_logger=False
)

# ── Database ──────────────────────────────────────────────────────────────────

def get_db():
    return pymysql.connect(
        host=os.environ.get('DB_HOST', 'shortline.proxy.rlwy.net'),
        port=int(os.environ.get('DB_PORT', 27197)),
        user=os.environ.get('DB_USER', 'root'),
        password=os.environ.get('DB_PASSWORD', 'ZTBeLJxWaaHHeqgjcJiYOvwEHOaOIVxs'),
        database=os.environ.get('DB_NAME', 'railway'),
        charset='utf8mb4',
        cursorclass=pymysql.cursors.DictCursor,
        autocommit=True,
        connect_timeout=10
    )

def init_db():
    db = get_db()
    try:
        with db.cursor() as cur:
            cur.execute("""
                CREATE TABLE IF NOT EXISTS users (
                    id INT AUTO_INCREMENT PRIMARY KEY,
                    username VARCHAR(50) UNIQUE NOT NULL,
                    password_hash VARCHAR(64) NOT NULL,
                    games_played INT DEFAULT 0,
                    games_won INT DEFAULT 0,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            """)
            cur.execute("""
                CREATE TABLE IF NOT EXISTS game_history (
                    id INT AUTO_INCREMENT PRIMARY KEY,
                    room_code VARCHAR(8) NOT NULL,
                    winner VARCHAR(20) NOT NULL,
                    player_count INT NOT NULL,
                    played_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            """)
    finally:
        db.close()

def hash_password(pw):
    return hashlib.sha256(pw.encode()).hexdigest()

# ── In-memory game state ──────────────────────────────────────────────────────

rooms = {}
# rooms[code] = {
#   host, players: {sid: {username, role, alive, voted_for, protected}},
#   phase, day, chat, started, votes, night_actions
# }

sid_to_room = {}   # sid → room_code
sid_to_user = {}   # sid → username

ROLES = {
    4:  ['mafia', 'detective', 'doctor', 'villager'],
    5:  ['mafia', 'detective', 'doctor', 'villager', 'villager'],
    6:  ['mafia', 'mafia', 'detective', 'doctor', 'villager', 'villager'],
    7:  ['mafia', 'mafia', 'detective', 'doctor', 'villager', 'villager', 'villager'],
    8:  ['mafia', 'mafia', 'detective', 'doctor', 'villager', 'villager', 'villager', 'villager'],
    9:  ['mafia', 'mafia', 'mafia', 'detective', 'doctor', 'villager', 'villager', 'villager', 'villager'],
    10: ['mafia', 'mafia', 'mafia', 'detective', 'doctor', 'villager', 'villager', 'villager', 'villager', 'villager'],
}

def make_room_code():
    while True:
        code = ''.join(random.choices(string.ascii_uppercase, k=6))
        if code not in rooms:
            return code

def alive_players(room):
    return {s: p for s, p in room['players'].items() if p['alive']}

def mafia_count(room):
    return sum(1 for p in room['players'].values() if p['role'] == 'mafia' and p['alive'])

def town_count(room):
    return sum(1 for p in room['players'].values() if p['role'] != 'mafia' and p['alive'])

def check_win(room):
    mc = mafia_count(room)
    tc = town_count(room)
    if mc == 0:
        return 'town'
    if mc >= tc:
        return 'mafia'
    return None

def broadcast_state(code):
    room = rooms[code]
    public_players = []
    for sid, p in room['players'].items():
        public_players.append({
            'username': p['username'],
            'alive': p['alive'],
            'sid': sid
        })
    socketio.emit('game_state', {
        'phase': room['phase'],
        'day': room['day'],
        'players': public_players,
        'host': room['host'],
        'started': room['started']
    }, room=code)

def send_roles(code):
    room = rooms[code]
    for sid, p in room['players'].items():
        mafia_names = [x['username'] for x in room['players'].values() if x['role'] == 'mafia'] if p['role'] == 'mafia' else []
        socketio.emit('your_role', {
            'role': p['role'],
            'mafia_team': mafia_names
        }, room=sid)

def sys_msg(code, text, msg_type='system'):
    rooms[code]['chat'].append({'type': msg_type, 'text': text, 'time': datetime.now().strftime('%H:%M')})
    socketio.emit('chat_message', {'type': msg_type, 'text': text, 'time': datetime.now().strftime('%H:%M')}, room=code)

# ── Routes ────────────────────────────────────────────────────────────────────

@app.route('/')
def index():
    return render_template('index.html')

@app.route('/register', methods=['POST'])
def register():
    data = request.json
    username = data.get('username', '').strip()
    password = data.get('password', '')
    if not username or not password:
        return jsonify({'ok': False, 'error': 'Username and password required'})
    if len(username) < 3 or len(username) > 20:
        return jsonify({'ok': False, 'error': 'Username must be 3–20 characters'})
    db = get_db()
    try:
        with db.cursor() as cur:
            cur.execute('SELECT id FROM users WHERE username=%s', (username,))
            if cur.fetchone():
                return jsonify({'ok': False, 'error': 'Username taken'})
            cur.execute('INSERT INTO users (username, password_hash) VALUES (%s, %s)',
                        (username, hash_password(password)))
        session['username'] = username
        return jsonify({'ok': True})
    except Exception as e:
        return jsonify({'ok': False, 'error': str(e)})
    finally:
        db.close()

@app.route('/login', methods=['POST'])
def login():
    data = request.json
    username = data.get('username', '').strip()
    password = data.get('password', '')
    db = get_db()
    try:
        with db.cursor() as cur:
            cur.execute('SELECT * FROM users WHERE username=%s AND password_hash=%s',
                        (username, hash_password(password)))
            user = cur.fetchone()
        if not user:
            return jsonify({'ok': False, 'error': 'Invalid credentials'})
        session['username'] = username
        return jsonify({'ok': True, 'username': username})
    finally:
        db.close()

@app.route('/logout', methods=['POST'])
def logout():
    session.pop('username', None)
    return jsonify({'ok': True})

@app.route('/leaderboard')
def leaderboard():
    db = get_db()
    try:
        with db.cursor() as cur:
            cur.execute("""
                SELECT username, games_played, games_won,
                       ROUND(games_won / NULLIF(games_played, 0) * 100, 1) AS win_rate
                FROM users WHERE games_played > 0
                ORDER BY games_won DESC LIMIT 20
            """)
            rows = cur.fetchall()
        return jsonify({'ok': True, 'data': rows})
    finally:
        db.close()

@app.route('/me')
def me():
    username = session.get('username')
    if not username:
        return jsonify({'ok': False})
    return jsonify({'ok': True, 'username': username})

# ── SocketIO events ───────────────────────────────────────────────────────────

@socketio.on('connect')
def on_connect():
    username = session.get('username')
    if username:
        sid_to_user[request.sid] = username

@socketio.on('disconnect')
def on_disconnect():
    sid = request.sid
    sid_to_user.pop(sid, None)
    code = sid_to_room.pop(sid, None)
    if not code or code not in rooms:
        return
    room = rooms[code]
    if sid in room['players']:
        username = room['players'][sid]['username']
        if room['started'] and room['players'][sid]['alive']:
            room['players'][sid]['alive'] = False
            sys_msg(code, f'⚡ {username} disconnected and was removed from the game.')
            winner = check_win(room)
            if winner:
                end_game(code, winner)
                return
            broadcast_state(code)
        elif not room['started']:
            del room['players'][sid]
            sys_msg(code, f'👤 {username} left the lobby.')
            if not room['players']:
                del rooms[code]
                return
            if room['host'] == sid and room['players']:
                room['host'] = next(iter(room['players']))
                new_host = room['players'][room['host']]['username']
                sys_msg(code, f'👑 {new_host} is the new host.')
            broadcast_state(code)

@socketio.on('create_room')
def on_create(data):
    sid = request.sid
    username = sid_to_user.get(sid) or data.get('username')
    if not username:
        emit('error', {'msg': 'Not logged in'})
        return
    code = make_room_code()
    rooms[code] = {
        'host': sid,
        'players': {sid: {'username': username, 'role': None, 'alive': True, 'voted_for': None, 'protected': False}},
        'phase': 'lobby',
        'day': 0,
        'chat': [],
        'started': False,
        'votes': {},
        'night_actions': {}
    }
    sid_to_room[sid] = code
    join_room(code)
    emit('room_created', {'code': code})
    broadcast_state(code)
    sys_msg(code, f'🎮 Room {code} created. Waiting for players…')

@socketio.on('join_room_req')
def on_join(data):
    sid = request.sid
    username = sid_to_user.get(sid) or data.get('username')
    code = data.get('code', '').upper().strip()
    if not username:
        emit('error', {'msg': 'Not logged in'})
        return
    if code not in rooms:
        emit('error', {'msg': 'Room not found'})
        return
    room = rooms[code]
    if room['started']:
        emit('error', {'msg': 'Game already in progress'})
        return
    if len(room['players']) >= 10:
        emit('error', {'msg': 'Room is full (max 10)'})
        return
    # check duplicate username
    for p in room['players'].values():
        if p['username'] == username:
            emit('error', {'msg': 'Username already in this room'})
            return
    room['players'][sid] = {'username': username, 'role': None, 'alive': True, 'voted_for': None, 'protected': False}
    sid_to_room[sid] = code
    join_room(code)
    emit('room_joined', {'code': code})
    # send existing chat
    for msg in room['chat']:
        emit('chat_message', msg)
    broadcast_state(code)
    sys_msg(code, f'👤 {username} joined the room.')

@socketio.on('start_game')
def on_start(data):
    sid = request.sid
    code = sid_to_room.get(sid)
    if not code:
        return
    room = rooms[code]
    if room['host'] != sid:
        emit('error', {'msg': 'Only the host can start'})
        return
    n = len(room['players'])
    if n < 4:
        emit('error', {'msg': 'Need at least 4 players to start'})
        return
    if n > 10:
        emit('error', {'msg': 'Max 10 players'})
        return
    role_list = ROLES.get(n, ROLES[10][:n])
    random.shuffle(role_list)
    for i, (psid, p) in enumerate(room['players'].items()):
        p['role'] = role_list[i]
        p['alive'] = True
        p['voted_for'] = None
        p['protected'] = False
    room['started'] = True
    room['phase'] = 'day'
    room['day'] = 1
    room['votes'] = {}
    room['night_actions'] = {}
    send_roles(code)
    broadcast_state(code)
    sys_msg(code, f'🌅 Day {room["day"]} begins. Discuss and find the Mafia!', 'phase')

@socketio.on('chat')
def on_chat(data):
    sid = request.sid
    code = sid_to_room.get(sid)
    if not code:
        return
    room = rooms[code]
    player = room['players'].get(sid)
    if not player:
        return
    text = data.get('text', '').strip()[:200]
    if not text:
        return
    # Dead players can't chat
    if not player['alive']:
        emit('error', {'msg': 'Dead players cannot speak'})
        return
    # During night, only mafia can chat (mafia channel)
    if room['phase'] == 'night' and player['role'] != 'mafia':
        emit('error', {'msg': 'Only Mafia can chat at night'})
        return
    if room['phase'] == 'night':
        # mafia-only channel
        msg = {'type': 'mafia', 'text': f'[MAFIA] {player["username"]}: {text}', 'time': datetime.now().strftime('%H:%M')}
        for psid, p in room['players'].items():
            if p['role'] == 'mafia':
                socketio.emit('chat_message', msg, room=psid)
        return
    msg = {'type': 'player', 'sender': player['username'], 'text': text, 'time': datetime.now().strftime('%H:%M')}
    room['chat'].append(msg)
    socketio.emit('chat_message', msg, room=code)

@socketio.on('vote')
def on_vote(data):
    sid = request.sid
    code = sid_to_room.get(sid)
    if not code:
        return
    room = rooms[code]
    player = room['players'].get(sid)
    if not player or not player['alive'] or room['phase'] != 'day':
        return
    target_sid = data.get('target_sid')
    if target_sid not in room['players']:
        return
    target = room['players'][target_sid]
    if not target['alive']:
        return
    if target_sid == sid:
        emit('error', {'msg': 'Cannot vote for yourself'})
        return
    # Remove previous vote
    old = player.get('voted_for')
    if old and old in room['votes']:
        room['votes'][old] = [v for v in room['votes'][old] if v != sid]
    player['voted_for'] = target_sid
    room['votes'].setdefault(target_sid, [])
    if sid not in room['votes'][target_sid]:
        room['votes'][target_sid].append(sid)
    sys_msg(code, f'🗳️ {player["username"]} voted for {target["username"]}')
    # broadcast vote tally
    tally = {room['players'][t]['username']: len(vs) for t, vs in room['votes'].items() if vs}
    socketio.emit('vote_update', {'tally': tally, 'votes': {room['players'][t]['username']: [room['players'][v]['username'] for v in vs] for t, vs in room['votes'].items()}}, room=code)
    # check if all alive players voted
    alive = alive_players(room)
    voted = sum(1 for p in alive.values() if p['voted_for'])
    if voted == len(alive):
        resolve_day_vote(code)

@socketio.on('night_action')
def on_night_action(data):
    sid = request.sid
    code = sid_to_room.get(sid)
    if not code:
        return
    room = rooms[code]
    player = room['players'].get(sid)
    if not player or not player['alive'] or room['phase'] != 'night':
        return
    action = data.get('action')
    target_sid = data.get('target_sid')
    role = player['role']
    if role not in ('mafia', 'detective', 'doctor'):
        return
    if target_sid not in room['players']:
        return
    if not room['players'][target_sid]['alive']:
        return
    room['night_actions'][role] = {'actor': sid, 'target': target_sid}
    if role == 'mafia':
        sys_msg(code, f'🔪 Mafia chose their target…', 'night')
        # Notify mafia team
        for psid, p in room['players'].items():
            if p['role'] == 'mafia' and p['alive']:
                socketio.emit('chat_message', {'type': 'mafia', 'text': f'[MAFIA] Target set: {room["players"][target_sid]["username"]}', 'time': datetime.now().strftime('%H:%M')}, room=psid)
    elif role == 'detective':
        is_mafia = room['players'][target_sid]['role'] == 'mafia'
        result = '🔴 MAFIA' if is_mafia else '🟢 Innocent'
        socketio.emit('chat_message', {'type': 'detective', 'text': f'[DETECTIVE] {room["players"][target_sid]["username"]} is {result}', 'time': datetime.now().strftime('%H:%M')}, room=sid)
    elif role == 'doctor':
        sys_msg(code, f'💉 Doctor chose someone to protect…', 'night')
    emit('action_confirmed', {'msg': 'Action submitted!'})
    check_night_complete(code)

@socketio.on('force_next_phase')
def on_force_next(data):
    sid = request.sid
    code = sid_to_room.get(sid)
    if not code:
        return
    room = rooms[code]
    if room['host'] != sid:
        return
    if room['phase'] == 'day':
        resolve_day_vote(code, forced=True)
    elif room['phase'] == 'night':
        resolve_night(code)

def resolve_day_vote(code, forced=False):
    room = rooms[code]
    if not room['votes']:
        sys_msg(code, '🌕 No consensus. Night falls…', 'phase')
        start_night(code)
        return
    # Find max votes
    max_votes = 0
    eliminated_sid = None
    for tsid, voters in room['votes'].items():
        if len(voters) > max_votes:
            max_votes = len(voters)
            eliminated_sid = tsid
    # Check tie
    top = [t for t, v in room['votes'].items() if len(v) == max_votes]
    if len(top) > 1:
        sys_msg(code, f'⚖️ It\'s a tie! No one is eliminated. Night falls…', 'phase')
    else:
        p = room['players'][eliminated_sid]
        p['alive'] = False
        sys_msg(code, f'☠️ {p["username"]} was eliminated! They were a {p["role"].upper()}.', 'phase')
        socketio.emit('player_eliminated', {'username': p['username'], 'role': p['role']}, room=code)
        winner = check_win(room)
        if winner:
            end_game(code, winner)
            return
    # Reset votes
    room['votes'] = {}
    for p in room['players'].values():
        p['voted_for'] = None
    broadcast_state(code)
    start_night(code)

def start_night(code):
    room = rooms[code]
    room['phase'] = 'night'
    room['night_actions'] = {}
    for p in room['players'].values():
        p['protected'] = False
    broadcast_state(code)
    sys_msg(code, f'🌙 Night falls. Mafia, make your move…', 'phase')

def check_night_complete(code):
    room = rooms[code]
    na = room['night_actions']
    alive = alive_players(room)
    has_mafia = any(p['role'] == 'mafia' for p in alive.values())
    has_detective = any(p['role'] == 'detective' for p in alive.values())
    has_doctor = any(p['role'] == 'doctor' for p in alive.values())
    mafia_done = 'mafia' in na if has_mafia else True
    detective_done = 'detective' in na if has_detective else True
    doctor_done = 'doctor' in na if has_doctor else True
    if mafia_done and detective_done and doctor_done:
        resolve_night(code)

def resolve_night(code):
    room = rooms[code]
    na = room['night_actions']
    killed_sid = None
    if 'mafia' in na:
        killed_sid = na['mafia']['target']
    if 'doctor' in na:
        protected_sid = na['doctor']['target']
        if protected_sid == killed_sid:
            killed_sid = None
            sys_msg(code, '💉 The Doctor saved someone tonight!', 'phase')
    room['day'] += 1
    room['phase'] = 'day'
    room['votes'] = {}
    for p in room['players'].values():
        p['voted_for'] = None
    if killed_sid and killed_sid in room['players']:
        victim = room['players'][killed_sid]
        victim['alive'] = False
        sys_msg(code, f'🌅 Day {room["day"]}. Last night, {victim["username"]} was killed! They were a {victim["role"].upper()}.', 'phase')
        socketio.emit('player_eliminated', {'username': victim['username'], 'role': victim['role']}, room=code)
    else:
        if killed_sid is None and 'mafia' not in na:
            sys_msg(code, f'🌅 Day {room["day"]}. A peaceful night — no one was killed.', 'phase')
        elif killed_sid is None:
            sys_msg(code, f'🌅 Day {room["day"]}. The Doctor saved someone — no one died!', 'phase')
    winner = check_win(room)
    if winner:
        end_game(code, winner)
        return
    broadcast_state(code)
    sys_msg(code, 'Discuss and vote for who you think is Mafia!', 'system')

def end_game(code, winner):
    room = rooms[code]
    room['phase'] = 'ended'
    # Reveal all roles
    roles_reveal = {p['username']: p['role'] for p in room['players'].values()}
    msg = '🎉 TOWN WINS!' if winner == 'town' else '💀 MAFIA WINS!'
    sys_msg(code, msg, 'phase')
    socketio.emit('game_over', {
        'winner': winner,
        'roles': roles_reveal,
        'message': msg
    }, room=code)
    broadcast_state(code)
    # Update DB
    try:
        db = get_db()
        with db.cursor() as cur:
            player_count = len(room['players'])
            cur.execute('INSERT INTO game_history (room_code, winner, player_count) VALUES (%s, %s, %s)',
                        (code, winner, player_count))
            for p in room['players'].values():
                won = (winner == 'town' and p['role'] != 'mafia') or (winner == 'mafia' and p['role'] == 'mafia')
                cur.execute('UPDATE users SET games_played=games_played+1, games_won=games_won+%s WHERE username=%s',
                            (1 if won else 0, p['username']))
        db.close()
    except Exception as e:
        print(f'DB update error: {e}')

# ── Entry point ───────────────────────────────────────────────────────────────

if __name__ == '__main__':
    init_db()
    port = int(os.environ.get('PORT', 5000))
    socketio.run(app, host='0.0.0.0', port=port, debug=False)
