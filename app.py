import os
import random
import string
import threading
import time

from flask import Flask, render_template, request
from flask_socketio import SocketIO, join_room, leave_room, emit

import game as G

app = Flask(__name__)
app.config["SECRET_KEY"] = os.environ.get("SECRET_KEY", "barrelbrawl-secret")
socketio = SocketIO(app, cors_allowed_origins="*", async_mode="threading")

# rooms[code] = {
#   "state":    game state dict,
#   "players":  {1: sid, 2: sid},   # online only
#   "mode":     "bot" | "local" | "online",
#   "next_starter": 1 or 2,
#   "timer_thread": thread or None,
# }
rooms = {}

TIMER_LIMIT = 15


def make_code():
    while True:
        code = "".join(random.choices(string.ascii_uppercase, k=4))
        if code not in rooms:
            return code


def state_payload(state, win_lines=None):
    """Serialise game state for the client."""
    return {
        "board":   state["board"],
        "counts":  {str(k): v for k, v in state["counts"].items()},
        "current": state["current"],
        "over":    state["over"],
        "winner":  state["winner"],
        "variant": state["variant"],
        "winCells": win_lines or [],
    }


# ── TIMER ─────────────────────────────────────────────────────────────────────

def start_timer(code):
    room = rooms.get(code)
    if not room:
        return
    # Cancel any existing timer
    room["timer_stop"] = True
    time.sleep(0.05)
    room["timer_stop"] = False

    def tick():
        remaining = TIMER_LIMIT
        while remaining >= 0:
            if rooms.get(code) is None or room.get("timer_stop"):
                return
            socketio.emit("timer", {"t": remaining}, room=code)
            if remaining == 0:
                # Time's up — pass turn
                r = rooms.get(code)
                if r and not r["state"]["over"]:
                    r["state"]["current"] = 3 - r["state"]["current"]
                    socketio.emit("state", state_payload(r["state"]), room=code)
                    socketio.emit("status", {"msg": "Time's up! Turn passed.", "color": "#e53935"}, room=code)
                    # Trigger bot if needed
                    if r["mode"] == "bot" and r["state"]["current"] == 2:
                        socketio.sleep(0.5)
                        do_bot_move(code)
                    else:
                        start_timer(code)
                return
            time.sleep(1)
            remaining -= 1

    t = threading.Thread(target=tick, daemon=True)
    room["timer_thread"] = t
    t.start()


def stop_timer(code):
    room = rooms.get(code)
    if room:
        room["timer_stop"] = True


def do_bot_move(code):
    room = rooms.get(code)
    if not room or room["state"]["over"]:
        return
    move = G.bot_best_move(room["state"])
    if move:
        tier, r, c = move
        new_state, err = G.apply_move(room["state"], 2, tier, r, c)
        if new_state:
            room["state"] = new_state
            wc = G.win_cells(new_state["board"], new_state["winner"]) if new_state["winner"] and new_state["winner"] != 0 else []
            socketio.emit("state", state_payload(new_state, wc), room=code)
            if not new_state["over"]:
                start_timer(code)
    else:
        # Bot has no moves, pass
        room["state"]["current"] = 1
        socketio.emit("state", state_payload(room["state"]), room=code)
        start_timer(code)


# ── HTTP ──────────────────────────────────────────────────────────────────────

@app.route("/")
def index():
    return render_template("index.html")


# ── SOCKET EVENTS ─────────────────────────────────────────────────────────────

@socketio.on("start_bot")
def on_start_bot():
    code = make_code()
    starter = 1
    state   = G.new_game_state(starter)
    rooms[code] = {
        "state":        state,
        "players":      {},
        "mode":         "bot",
        "next_starter": 2,
        "timer_stop":   False,
        "timer_thread": None,
    }
    join_room(code)
    emit("joined", {"code": code, "player": 1, "mode": "bot"})
    emit("state", state_payload(state))
    if state["current"] == 2:
        emit("status", {"msg": "Bot is thinking…", "color": "#55556a"})
        socketio.sleep(0.5)
        do_bot_move(code)
    else:
        start_timer(code)


@socketio.on("start_local")
def on_start_local():
    code = make_code()
    starter = 1
    state   = G.new_game_state(starter)
    rooms[code] = {
        "state":        state,
        "players":      {},
        "mode":         "local",
        "next_starter": 2,
        "timer_stop":   False,
        "timer_thread": None,
    }
    join_room(code)
    emit("joined", {"code": code, "player": 0, "mode": "local"})  # player 0 = both
    emit("state", state_payload(state))
    start_timer(code)


@socketio.on("create_online")
def on_create_online():
    code = make_code()
    starter = 1
    state   = G.new_game_state(starter)
    rooms[code] = {
        "state":        state,
        "players":      {1: request.sid},
        "mode":         "online",
        "next_starter": 2,
        "timer_stop":   True,
        "timer_thread": None,
    }
    join_room(code)
    emit("joined", {"code": code, "player": 1, "mode": "online"})
    emit("status", {"msg": f"Your room code is {code} — share it with a friend!", "color": "#fdd835"})


@socketio.on("join_online")
def on_join_online(data):
    code = data.get("code", "").upper().strip()
    room = rooms.get(code)
    if not room:
        emit("error", {"msg": "Room not found."})
        return
    if room["mode"] != "online":
        emit("error", {"msg": "That room is not an online game."})
        return
    if len(room["players"]) >= 2:
        emit("error", {"msg": "Room is full."})
        return

    room["players"][2] = request.sid
    join_room(code)
    emit("joined", {"code": code, "player": 2, "mode": "online"})
    # Tell player 1 the game is starting
    socketio.emit("state", state_payload(room["state"]), room=code)
    socketio.emit("status", {"msg": "Opponent connected! Game on.", "color": "#4caf50"}, room=code)
    room["timer_stop"] = False
    start_timer(code)


@socketio.on("move")
def on_move(data):
    code   = data.get("code")
    tier   = data.get("tier")
    row    = int(data.get("row"))
    col    = int(data.get("col"))
    player = int(data.get("player", 1))

    room = rooms.get(code)
    if not room or room["state"]["over"]:
        return

    state = room["state"]

    # Online: verify it's this socket's turn
    if room["mode"] == "online":
        expected_sid = room["players"].get(state["current"])
        if request.sid != expected_sid:
            emit("error", {"msg": "Not your turn."})
            return

    if state["current"] != player and room["mode"] != "local":
        return

    actual_player = state["current"] if room["mode"] == "local" else player

    new_state, err = G.apply_move(state, actual_player, tier, row, col)
    if err:
        emit("error", {"msg": err})
        return

    stop_timer(code)
    room["state"] = new_state
    wc = G.win_cells(new_state["board"], new_state["winner"]) if new_state["winner"] and new_state["winner"] != 0 else []
    socketio.emit("state", state_payload(new_state, wc), room=code)

    if new_state["over"]:
        return

    if room["mode"] == "bot" and new_state["current"] == 2:
        socketio.emit("status", {"msg": "Bot is thinking…", "color": "#55556a"}, room=code)
        socketio.sleep(0.5)
        do_bot_move(code)
    else:
        start_timer(code)


@socketio.on("restart")
def on_restart(data):
    code = data.get("code")
    room = rooms.get(code)
    if not room:
        return
    stop_timer(code)
    starter = room["next_starter"]
    room["next_starter"] = 3 - starter
    room["state"]      = G.new_game_state(starter)
    room["timer_stop"] = False
    socketio.emit("state", state_payload(room["state"]), room=code)
    socketio.emit("status", {"msg": "New game!", "color": "#4caf50"}, room=code)

    if room["mode"] == "bot" and room["state"]["current"] == 2:
        socketio.emit("status", {"msg": "Bot is thinking…", "color": "#55556a"}, room=code)
        socketio.sleep(0.5)
        do_bot_move(code)
    else:
        start_timer(code)


@socketio.on("disconnect")
def on_disconnect():
    for code, room in list(rooms.items()):
        if request.sid in room["players"].values():
            stop_timer(code)
            socketio.emit("status", {"msg": "Opponent disconnected.", "color": "#e53935"}, room=code)
            # Clean up room
            del rooms[code]
            break


port = int(os.environ.get("PORT", 5000))
socketio.run(app, host="0.0.0.0", port=port, allow_unsafe_werkzeug=True)
