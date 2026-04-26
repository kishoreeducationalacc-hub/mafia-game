# pythonanywhere_server.py
# This version works on PythonAnywhere!

from flask import Flask, render_template, request, jsonify
from flask_socketio import SocketIO, emit, join_room, leave_room
from flask_cors import CORS
import random
import json
from datetime import datetime
import pymysql

# Initialize Flask app
app = Flask(__name__)
app.config['SECRET_KEY'] = 'mafia-game-secret-key'
CORS(app)

# Initialize SocketIO with eventlet (required for PythonAnywhere)
socketio = SocketIO(app, 
                   cors_allowed_origins="*",
                   async_mode='threading',  # PythonAnywhere doesn't support eventlet
                   ping_timeout=60,
                   ping_interval=25)

# Database configuration for PythonAnywhere MySQL
DB_CONFIG = {
    'host': 'yourusername.mysql.pythonanywhere-services.com',  # PythonAnywhere MySQL host
    'user': 'yourusername',
    'password': 'your_mysql_password',
    'database': 'yourusername$mafia_game'
}

# ============================================
# DATABASE SETUP (Same as before but adapted)
# ============================================
class DatabaseManager:
    def __init__(self):
        self.connection = None
        self.connect()
        self.create_tables()
    
    def connect(self):
        try:
            self.connection = pymysql.connect(
                host=DB_CONFIG['host'],
                user=DB_CONFIG['user'],
                password=DB_CONFIG['password'],
                database=DB_CONFIG['database'],
                cursorclass=pymysql.cursors.DictCursor
            )
            print("✅ Database connected!")
        except Exception as e:
            print(f"❌ Database error: {e}")
            # Fall back to file-based storage
            self.use_file_storage = True
    
    def create_tables(self):
        if hasattr(self, 'use_file_storage'):
            return
        
        try:
            with self.connection.cursor() as cursor:
                cursor.execute("""
                    CREATE TABLE IF NOT EXISTS users (
                        id INT AUTO_INCREMENT PRIMARY KEY,
                        username VARCHAR(50) UNIQUE NOT NULL,
                        password VARCHAR(255) NOT NULL,
                        games_played INT DEFAULT 0,
                        wins INT DEFAULT 0,
                        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                    )
                """)
                cursor.execute("""
                    CREATE TABLE IF NOT EXISTS game_history (
                        id INT AUTO_INCREMENT PRIMARY KEY,
                        room_code VARCHAR(10),
                        winner VARCHAR(20),
                        players_count INT,
                        played_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                    )
                """)
            self.connection.commit()
            print("✅ Tables created!")
        except Exception as e:
            print(f"Table creation error: {e}")

db = DatabaseManager()

# ============================================
# GAME STATE MANAGEMENT
# ============================================
class GameRoom:
    def __init__(self, code, host):
        self.code = code
        self.host = host
        self.players = [host]
        self.game_started = False
        self.phase = 'lobby'  # lobby, night, day, voting, ended
        self.roles = {}  # {username: role}
        self.alive_players = []
        self.votes = {}  # {voter: target}
        self.mafia_target = None
        self.detective_check = None
        self.doctor_save = None
        self.day_count = 1
        self.chat_history = []
        self.night_actions = {}  # {username: action}
    
    def assign_roles(self):
        """Assign roles based on player count"""
        num_players = len(self.players)
        roles = []
        
        if num_players <= 4:
            roles = ['Mafia', 'Detective', 'Doctor']
            roles.extend(['Villager'] * (num_players - 3))
        elif num_players <= 7:
            roles = ['Mafia', 'Detective', 'Doctor']
            roles.extend(['Villager'] * (num_players - 3))
        else:
            roles = ['Mafia', 'Mafia', 'Detective', 'Doctor']
            roles.extend(['Villager'] * (num_players - 4))
        
        random.shuffle(roles)
        for i, player in enumerate(self.players):
            self.roles[player] = roles[i]
        
        self.alive_players = self.players.copy()
        return self.roles
    
    def check_game_end(self):
        """Check if game has ended"""
        mafia_count = sum(1 for p in self.alive_players if self.roles.get(p) == 'Mafia')
        villager_count = len(self.alive_players) - mafia_count
        
        if mafia_count == 0:
            return 'Villagers'
        elif mafia_count >= villager_count:
            return 'Mafia'
        return None
    
    def to_dict(self):
        """Convert room to dictionary for JSON"""
        return {
            'code': self.code,
            'host': self.host,
            'players': self.players,
            'game_started': self.game_started,
            'phase': self.phase,
            'alive_count': len(self.alive_players),
            'day_count': self.day_count
        }

# Store all game rooms
game_rooms = {}

# Map socket IDs to players
socket_to_player = {}
player_to_socket = {}

# ============================================
# FLASK ROUTES (Web Interface)
# ============================================
@app.route('/')
def index():
    """Serve the game page"""
    return """
    <!DOCTYPE html>
    <html>
    <head>
        <title>Mafia Game - PythonAnywhere</title>
        <style>
            body {
                font-family: Arial, sans-serif;
                background: linear-gradient(135deg, #1a1a2e 0%, #16213e 100%);
                color: white;
                margin: 0;
                padding: 20px;
                min-height: 100vh;
            }
            .container {
                max-width: 800px;
                margin: 0 auto;
            }
            .game-panel {
                background: rgba(255,255,255,0.1);
                border-radius: 10px;
                padding: 20px;
                margin: 10px 0;
            }
            input, button {
                padding: 10px;
                margin: 5px;
                border-radius: 5px;
                border: none;
                font-size: 16px;
            }
            button {
                background: #4CAF50;
                color: white;
                cursor: pointer;
            }
            button:hover {
                background: #45a049;
            }
            .player-list {
                list-style: none;
                padding: 0;
            }
            .player-item {
                padding: 10px;
                background: rgba(255,255,255,0.05);
                margin: 5px 0;
                border-radius: 5px;
            }
            #chat-messages {
                height: 300px;
                overflow-y: auto;
                background: rgba(0,0,0,0.3);
                padding: 10px;
                border-radius: 5px;
                margin: 10px 0;
            }
            .hidden {
                display: none;
            }
            .role-card {
                background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
                padding: 20px;
                border-radius: 10px;
                text-align: center;
                margin: 20px 0;
            }
        </style>
    </head>
    <body>
        <div class="container">
            <h1>🎮 Mafia Game</h1>
            
            <!-- Login Screen -->
            <div id="login-screen" class="game-panel">
                <h2>Login</h2>
                <input type="text" id="username" placeholder="Username">
                <input type="password" id="password" placeholder="Password">
                <button onclick="login()">Login</button>
                <button onclick="register()">Register</button>
            </div>
            
            <!-- Lobby Screen -->
            <div id="lobby-screen" class="game-panel hidden">
                <h2>Game Lobby</h2>
                <button onclick="createRoom()">Create Room</button>
                <input type="text" id="room-code-input" placeholder="Room Code">
                <button onclick="joinRoom()">Join Room</button>
                
                <div id="room-info" class="hidden">
                    <h3>Room: <span id="room-code"></span></h3>
                    <p>Players:</p>
                    <ul id="room-players" class="player-list"></ul>
                    <button id="start-game-btn" class="hidden" onclick="startGame()">Start Game</button>
                </div>
            </div>
            
            <!-- Game Screen -->
            <div id="game-screen" class="game-panel hidden">
                <h2 id="phase-indicator">Phase: Night</h2>
                <div id="role-display" class="role-card hidden">
                    <h3>Your Role: <span id="your-role"></span></h3>
                </div>
                <p>Alive Players:</p>
                <ul id="alive-players" class="player-list"></ul>
                
                <div id="voting-area" class="hidden">
                    <h3>Vote to Eliminate:</h3>
                    <div id="vote-buttons"></div>
                </div>
                
                <div id="night-actions" class="hidden">
                    <h3>Night Action:</h3>
                    <div id="action-buttons"></div>
                </div>
                
                <div id="chat-messages"></div>
                <input type="text" id="chat-input" placeholder="Type a message...">
                <button onclick="sendChat()">Send</button>
            </div>
            
            <!-- Game Over Screen -->
            <div id="game-over-screen" class="game-panel hidden">
                <h2>Game Over!</h2>
                <h3>Winner: <span id="winner-display"></span></h3>
                <div id="all-roles"></div>
                <button onclick="returnToLobby()">Back to Lobby</button>
            </div>
        </div>
        
        <script src="https://cdnjs.cloudflare.com/ajax/libs/socket.io/4.5.4/socket.io.min.js"></script>
        <script>
            // Connect to SocketIO server
            const socket = io();
            let currentUser = null;
            let currentRoom = null;
            let myRole = null;
            
            // ============ AUTH FUNCTIONS ============
            function login() {
                const username = document.getElementById('username').value;
                const password = document.getElementById('password').value;
                
                socket.emit('login', {username, password});
            }
            
            function register() {
                const username = document.getElementById('username').value;
                const password = document.getElementById('password').value;
                
                socket.emit('register', {username, password});
            }
            
            // ============ ROOM FUNCTIONS ============
            function createRoom() {
                socket.emit('create_room', {username: currentUser});
            }
            
            function joinRoom() {
                const roomCode = document.getElementById('room-code-input').value;
                socket.emit('join_room', {
                    username: currentUser,
                    room_code: roomCode
                });
            }
            
            function startGame() {
                socket.emit('start_game', {
                    username: currentUser,
                    room_code: currentRoom
                });
            }
            
            // ============ GAME FUNCTIONS ============
            function vote(player) {
                socket.emit('vote', {
                    username: currentUser,
                    room_code: currentRoom,
                    target: player
                });
            }
            
            function nightAction(target, action) {
                socket.emit('night_action', {
                    username: currentUser,
                    room_code: currentRoom,
                    target: target,
                    action: action
                });
            }
            
            function sendChat() {
                const message = document.getElementById('chat-input').value;
                if (message) {
                    socket.emit('chat', {
                        username: currentUser,
                        room_code: currentRoom,
                        message: message
                    });
                    document.getElementById('chat-input').value = '';
                }
            }
            
            function returnToLobby() {
                document.getElementById('game-screen').classList.add('hidden');
                document.getElementById('game-over-screen').classList.add('hidden');
                document.getElementById('lobby-screen').classList.remove('hidden');
                document.getElementById('room-info').classList.add('hidden');
            }
            
            // ============ SOCKET HANDLERS ============
            socket.on('login_response', (data) => {
                if (data.success) {
                    currentUser = data.user.username;
                    document.getElementById('login-screen').classList.add('hidden');
                    document.getElementById('lobby-screen').classList.remove('hidden');
                    alert('Login successful!');
                } else {
                    alert(data.message);
                }
            });
            
            socket.on('register_response', (data) => {
                alert(data.message);
            });
            
            socket.on('room_created', (data) => {
                currentRoom = data.room_code;
                document.getElementById('room-info').classList.remove('hidden');
                document.getElementById('room-code').textContent = data.room_code;
                document.getElementById('start-game-btn').classList.remove('hidden');
                updatePlayerList(data.players);
            });
            
            socket.on('room_update', (data) => {
                updatePlayerList(data.players);
            });
            
            socket.on('game_started', (data) => {
                myRole = data.role;
                document.getElementById('lobby-screen').classList.add('hidden');
                document.getElementById('game-screen').classList.remove('hidden');
                document.getElementById('role-display').classList.remove('hidden');
                document.getElementById('your-role').textContent = data.role;
                
                updatePhase(data.phase);
                updateAlivePlayers(data.alive_players);
            });
            
            socket.on('phase_change', (data) => {
                updatePhase(data.phase);
                
                if (data.phase === 'voting') {
                    showVotingButtons(data.alive_players);
                } else if (data.phase === 'night' && myRole === 'Mafia') {
                    showNightActions(data.alive_players, 'kill');
                } else if (data.phase === 'night' && myRole === 'Detective') {
                    showNightActions(data.alive_players, 'investigate');
                } else if (data.phase === 'night' && myRole === 'Doctor') {
                    showNightActions(data.alive_players, 'save');
                }
            });
            
            socket.on('player_eliminated', (data) => {
                addChatMessage('System', `${data.player} was eliminated!`);
                updateAlivePlayers(data.alive_players);
            });
            
            socket.on('chat_message', (data) => {
                addChatMessage(data.username, data.message);
            });
            
            socket.on('game_over', (data) => {
                document.getElementById('game-over-screen').classList.remove('hidden');
                document.getElementById('winner-display').textContent = data.winner;
                
                let rolesHtml = '<h4>All Roles:</h4>';
                for (const [player, role] of Object.entries(data.roles)) {
                    rolesHtml += `<p>${player}: ${role}</p>`;
                }
                document.getElementById('all-roles').innerHTML = rolesHtml;
            });
            
            // ============ UI HELPERS ============
            function updatePlayerList(players) {
                let html = '';
                players.forEach(player => {
                    html += `<li class="player-item">${player}</li>`;
                });
                document.getElementById('room-players').innerHTML = html;
            }
            
            function updateAlivePlayers(players) {
                let html = '';
                players.forEach(player => {
                    html += `<li class="player-item">${player}</li>`;
                });
                document.getElementById('alive-players').innerHTML = html;
            }
            
            function updatePhase(phase) {
                document.getElementById('phase-indicator').textContent = `Phase: ${phase}`;
            }
            
            function showVotingButtons(players) {
                document.getElementById('voting-area').classList.remove('hidden');
                let buttons = '';
                players.forEach(player => {
                    if (player !== currentUser) {
                        buttons += `<button onclick="vote('${player}')">Vote ${player}</button>`;
                    }
                });
                document.getElementById('vote-buttons').innerHTML = buttons;
            }
            
            function showNightActions(players, action) {
                document.getElementById('night-actions').classList.remove('hidden');
                let buttons = '';
                players.forEach(player => {
                    if (player !== currentUser) {
                        buttons += `<button onclick="nightAction('${player}', '${action}')">${action} ${player}</button>`;
                    }
                });
                document.getElementById('action-buttons').innerHTML = buttons;
            }
            
            function addChatMessage(username, message) {
                const chatDiv = document.getElementById('chat-messages');
                const timestamp = new Date().toLocaleTimeString();
                chatDiv.innerHTML += `<p><strong>${username}</strong> (${timestamp}): ${message}</p>`;
                chatDiv.scrollTop = chatDiv.scrollHeight;
            }
            
            // Send chat on Enter key
            document.getElementById('chat-input').addEventListener('keypress', (e) => {
                if (e.key === 'Enter') sendChat();
            });
        </script>
    </body>
    </html>
    """

@app.route('/health')
def health_check():
    """Health check endpoint"""
    return jsonify({'status': 'ok', 'players': len(socket_to_player)})

# ============================================
# SOCKETIO EVENTS (Game Logic)
# ============================================
@socketio.on('connect')
def handle_connect():
    """Handle client connection"""
    print(f"🔗 Client connected: {request.sid}")
    emit('connected', {'message': 'Connected to Mafia Game Server!'})

@socketio.on('disconnect')
def handle_disconnect():
    """Handle client disconnection"""
    print(f"🔴 Client disconnected: {request.sid}")
    
    if request.sid in socket_to_player:
        player_info = socket_to_player[request.sid]
        username = player_info['username']
        room_code = player_info.get('room')
        
        if room_code and room_code in game_rooms:
            room = game_rooms[room_code]
            leave_room(room_code)
            
            # Remove player from room
            if username in room.players:
                room.players.remove(username)
                if room.game_started and username in room.alive_players:
                    room.alive_players.remove(username)
                
                # Check game end immediately
                if room.game_started:
                    winner = room.check_game_end()
                    if winner:
                        end_game(room_code, winner)
                
                # Notify remaining players
                emit('room_update', {
                    'players': room.players,
                    'room_code': room_code
                }, room=room_code)
            
            # Delete empty rooms
            if not room.players:
                del game_rooms[room_code]
        
        del socket_to_player[request.sid]

@socketio.on('login')
def handle_login(data):
    """Handle user login"""
    username = data.get('username')
    password = data.get('password')
    
    # Simple validation
    if username and password:
        # In real app, check database
        success = db.login_user(username, password) if hasattr(db, 'connection') else (True, {'username': username})
        
        if success[0] if isinstance(success, tuple) else True:
            socket_to_player[request.sid] = {'username': username}
            emit('login_response', {
                'success': True,
                'user': {'username': username}
            })
        else:
            emit('login_response', {
                'success': False,
                'message': 'Invalid credentials'
            })
    else:
        emit('login_response', {
            'success': False,
            'message': 'Username and password required'
        })

@socketio.on('register')
def handle_register(data):
    """Handle user registration"""
    username = data.get('username')
    password = data.get('password')
    
    if username and password:
        success, message = db.register_user(username, password) if hasattr(db, 'connection') else (True, 'Registered!')
        emit('register_response', {
            'success': success,
            'message': message
        })
    else:
        emit('register_response', {
            'success': False,
            'message': 'Username and password required'
        })

@socketio.on('create_room')
def handle_create_room(data):
    """Create a new game room"""
    username = data['username']
    room_code = str(random.randint(1000, 9999))
    
    while room_code in game_rooms:
        room_code = str(random.randint(1000, 9999))
    
    # Create room
    game_rooms[room_code] = GameRoom(room_code, username)
    
    # Join the room
    join_room(room_code)
    socket_to_player[request.sid] = {
        'username': username,
        'room': room_code
    }
    
    print(f"🏠 Room {room_code} created by {username}")
    
    emit('room_created', {
        'room_code': room_code,
        'players': [username]
    })

@socketio.on('join_room')
def handle_join_room(data):
    """Join an existing room"""
    username = data['username']
    room_code = data['room_code']
    
    if room_code in game_rooms:
        room = game_rooms[room_code]
        
        if not room.game_started:
            if username not in room.players:
                room.players.append(username)
                
                join_room(room_code)
                socket_to_player[request.sid] = {
                    'username': username,
                    'room': room_code
                }
                
                print(f"👤 {username} joined room {room_code}")
                
                # Notify everyone in room
                emit('room_update', {
                    'players': room.players,
                    'room_code': room_code
                }, room=room_code)
            else:
                emit('error', {'message': 'Already in this room!'})
        else:
            emit('error', {'message': 'Game already started!'})
    else:
        emit('error', {'message': 'Room not found!'})

@socketio.on('start_game')
def handle_start_game(data):
    """Start the game"""
    username = data['username']
    room_code = data['room_code']
    
    if room_code in game_rooms:
        room = game_rooms[room_code]
        
        if room.host == username and len(room.players) >= 3:
            # Assign roles
            roles = room.assign_roles()
            room.game_started = True
            room.phase = 'night'
            
            print(f"🎮 Game started in room {room_code}")
            
            # Send private roles to each player
            for player in room.players:
                # Find socket for this player
                for sid, info in socket_to_player.items():
                    if info['username'] == player and info.get('room') == room_code:
                        emit('game_started', {
                            'role': roles[player],
                            'phase': 'night',
                            'alive_players': room.alive_players
                        }, room=sid)
                        break
        else:
            emit('error', {'message': 'Not enough players or not the host!'})

@socketio.on('vote')
def handle_vote(data):
    """Handle player vote"""
    username = data['username']
    room_code = data['room_code']
    target = data['target']
    
    if room_code in game_rooms:
        room = game_rooms[room_code]
        
        if room.phase == 'voting' and username in room.alive_players:
            room.votes[username] = target
            
            # Check if all alive players have voted
            if len(room.votes) >= len(room.alive_players):
                process_votes(room_code)

@socketio.on('night_action')
def handle_night_action(data):
    """Handle night actions (mafia kill, detective check, etc.)"""
    username = data['username']
    room_code = data['room_code']
    target = data['target']
    action = data['action']
    
    if room_code in game_rooms:
        room = game_rooms[room_code]
        
        if room.phase == 'night':
            room.night_actions[username] = {
                'action': action,
                'target': target
            }
            
            # Check if all required actions are done
            required_actions = 0
            for player in room.alive_players:
                role = room.roles.get(player)
                if role in ['Mafia', 'Detective', 'Doctor']:
                    required_actions += 1
            
            if len(room.night_actions) >= required_actions:
                process_night_actions(room_code)

@socketio.on('chat')
def handle_chat(data):
    """Handle chat messages"""
    username = data['username']
    room_code = data['room_code']
    message = data['message']
    
    if room_code in game_rooms:
        room = game_rooms[room_code]
        room.chat_history.append({
            'username': username,
            'message': message,
            'timestamp': datetime.now().isoformat()
        })
        
        emit('chat_message', {
            'username': username,
            'message': message
        }, room=room_code)

# ============================================
# GAME LOGIC FUNCTIONS
# ============================================
def process_votes(room_code):
    """Process all votes and eliminate a player"""
    room = game_rooms[room_code]
    
    # Count votes
    vote_count = {}
    for voter, target in room.votes.items():
        if target in room.alive_players:
            vote_count[target] = vote_count.get(target, 0) + 1
    
    if not vote_count:
        return
    
    # Find most voted
    max_votes = max(vote_count.values())
    eliminated = [p for p, v in vote_count.items() if v == max_votes]
    
    if len(eliminated) == 1:
        eliminated_player = eliminated[0]
        room.alive_players.remove(eliminated_player)
        
        emit('player_eliminated', {
            'player': eliminated_player,
            'role': room.roles[eliminated_player],
            'alive_players': room.alive_players
        }, room=room_code)
        
        # Reset votes
        room.votes = {}
        
        # Check game end
        winner = room.check_game_end()
        if winner:
            end_game(room_code, winner)
        else:
            # Move to night phase
            room.phase = 'night'
            room.night_actions = {}
            emit('phase_change', {
                'phase': 'night',
                'alive_players': room.alive_players
            }, room=room_code)
    else:
        # Tie - no elimination
        emit('chat_message', {
            'username': 'System',
            'message': f"Vote tied between {', '.join(eliminated)}. No one eliminated!"
        }, room=room_code)
        
        room.votes = {}
        room.phase = 'night'
        room.night_actions = {}
        
        emit('phase_change', {
            'phase': 'night',
            'alive_players': room.alive_players
        }, room=room_code)

def process_night_actions(room_code):
    """Process all night actions"""
    room = game_rooms[room_code]
    
    # Find mafia target
    mafia_targets = {}
    detective_results = {}
    doctor_saves = set()
    
    for player, action_data in room.night_actions.items():
        role = room.roles.get(player)
        target = action_data['target']
        action_type = action_data['action']
        
        if role == 'Mafia' and action_type == 'kill':
            mafia_targets[target] = mafia_targets.get(target, 0) + 1
        elif role == 'Detective' and action_type == 'investigate':
            detective_results[player] = {
                'target': target,
                'role': room.roles.get(target)
            }
        elif role == 'Doctor' and action_type == 'save':
            doctor_saves.add(target)
    
    # Mafia kills the most targeted player
    killed_player = None
    if mafia_targets:
        max_targets = max(mafia_targets.values())
        mafia_choices = [p for p, v in mafia_targets.items() if v == max_targets]
        killed_player = random.choice(mafia_choices) if mafia_choices else None
    
    # Check if doctor saved the target
    if killed_player and killed_player in doctor_saves:
        killed_player = None  # Doctor saved them!
    
    # Apply kill
    if killed_player and killed_player in room.alive_players:
        room.alive_players.remove(killed_player)
        
        emit('player_eliminated', {
            'player': killed_player,
            'role': room.roles[killed_player],
            'alive_players': room.alive_players
        }, room=room_code)
    else:
        emit('chat_message', {
            'username': 'System',
            'message': 'No one died during the night!'
        }, room=room_code)
    
    # Send detective results
    for detective, result in detective_results.items():
        for sid, info in socket_to_player.items():
            if info['username'] == detective and info.get('room') == room_code:
                emit('detective_result', {
                    'target': result['target'],
                    'role': result['role']
                }, room=sid)
    
    # Reset night actions
    room.night_actions = {}
    
    # Check game end
    winner = room.check_game_end()
    if winner:
        end_game(room_code, winner)
    else:
        # Move to day phase
        room.phase = 'day'
        room.day_count += 1
        
        emit('phase_change', {
            'phase': 'day',
            'alive_players': room.alive_players,
            'day_count': room.day_count
        }, room=room_code)
        
        # After discussion time, move to voting
        # You could add a timer here
        import threading
        def move_to_voting():
            room.phase = 'voting'
            room.votes = {}
            emit('phase_change', {
                'phase': 'voting',
                'alive_players': room.alive_players
            }, room=room_code)
        
        # Auto-advance to voting after 60 seconds (adjustable)
        # threading.Timer(60, move_to_voting).start()

def end_game(room_code, winner):
    """End the game and notify players"""
    room = game_rooms[room_code]
    
    if hasattr(db, 'connection'):
        db.save_game_history(room_code, winner, len(room.players))
    
    emit('game_over', {
        'winner': winner,
        'roles': room.roles,
        'alive_players': room.alive_players
    }, room=room_code)
    
    # Reset room for new game
    room.game_started = False
    room.phase = 'lobby'
    room.roles = {}
    room.alive_players = room.players.copy()
    room.votes = {}
    room.night_actions = {}

# ============================================
# MAIN APPLICATION ENTRY POINT
# ============================================
if __name__ == '__main__':
    print("🎮 Starting Mafia Game Server...")
    print("🌐 Connect players to your PythonAnywhere URL")
    socketio.run(app, host='0.0.0.0', port=5000, debug=False)