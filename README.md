# 🎭 Mafia Game — Deployment Guide

## Project Structure
```
mafiagame.py          ← Main Flask + SocketIO app
templates/index.html  ← Single-page game UI
requirements.txt      ← Python dependencies
Procfile              ← Railway/Heroku process definition
railway.toml          ← Railway config
```

## Local Development
```bash
pip install -r requirements.txt
python mafiagame.py
# → http://localhost:5000
```

## Railway Deployment

### 1. Set environment variables in Railway dashboard:
```
DB_HOST=shortline.proxy.rlwy.net
DB_PORT=27197
DB_USER=root
DB_PASSWORD=your_password_here
DB_NAME=railway
SECRET_KEY=some-random-secret-string
```

### 2. Push to Railway via GitHub or CLI:
```bash
# Via Railway CLI
railway up
```

### 3. Railway auto-detects the Procfile and deploys.

## Game Rules
- **4–10 players** required to start
- **Roles by player count:**
  - 4 players: 1 Mafia, 1 Detective, 1 Doctor, 1 Villager
  - 6 players: 2 Mafia, 1 Detective, 1 Doctor, 2 Villagers
  - 8 players: 2 Mafia, 1 Detective, 1 Doctor, 4 Villagers
  - 10 players: 3 Mafia, 1 Detective, 1 Doctor, 5 Villagers

## Role Abilities
| Role | Night Action |
|------|-------------|
| Mafia | Kill one player |
| Detective | Investigate one player (learns if Mafia) |
| Doctor | Protect one player from being killed |
| Villager | None — must use logic to vote correctly |

## Win Conditions
- **Town wins** when all Mafia are eliminated
- **Mafia wins** when Mafia outnumber Town

## Tech Stack
- Flask 3.0 + Flask-SocketIO 5.3.4
- MySQL (Railway) via PyMySQL
- Gevent WebSocket worker via Gunicorn
- Vanilla JS + Socket.IO client
