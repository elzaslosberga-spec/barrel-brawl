import os
import random
import string

from flask import Flask, render_template, request
from flask_socketio import SocketIO, join_room, emit

import game as G

app = Flask(__name__)
app.config["SECRET_KEY"] = os.environ.get("SECRET_KEY", "barrelbrawl-secret")
socketio = SocketIO(app, cors_allowed_origins="*", async_mode="threading")

rooms = {}


def make_code():
    while True:
        code = "".join(random.choices(string.ascii_uppercase, k=4))
        if code not in rooms:
            return code


def state_payload(state, win_lines=None):
    return {
        "board":    state["board"],
        "counts":   {str(k): v for k, v in state["counts"].items()},
        "current":  state["current"],
        "over":     state["over"],
        "winner":   state["winner"],
        "variant":  state["variant"],
        "winCells": win_lines or [],
    }


def emit_state(code, state, win_lines=None):
    payload = state_payload(state, win_lines)
    socketio.emit("state", payload, room=code)
    if not state["over"]:
        socketio.emit("start_turn", {"player": state["current"]}, room=code)


@app.route("/")
def index():
    return render_template("index.html")


@socketio.on("start_bot")
def on_start_bot():
    code  = make_code()
    state = G.new_game_state(1)
    rooms[code] = {"state": state, "players": {}, "mode": "bot", "next_starter": 2}
    join_room(code)
    emit("joined", {"code": code, "player": 1, "mode": "bot"})
    if state["current"] == 2:
        emit("state", state_payload(state))
        emit("status", {"msg": "Bot is thinking…", "color": "#888"})
        _do_bot_move(code)
    else:
        emit_state(code, state)


@socketio.on("start_local")
def on_start_local():
    code  = make_code()
    state = G.new_game_state(1)
    rooms[code] = {"state": state, "players": {}, "mode": "local", "next_starter": 2}
    join_room(code)
    emit("joined", {"code": code, "player": 0, "mode": "local"})
    emit_state(code, state)


@socketio.on("create_online")
def on_create_online():
    code  = make_code()
    state = G.new_game_state(1)
    rooms[code] = {"state": state, "players": {1: request.sid}, "mode": "online", "next_starter": 2}
    join_room(code)
    emit("joined", {"code": code, "player": 1, "mode": "online"})
    emit("status", {"msg": f"Room code: {code} — share with a friend!", "color": "#fdd835"})


@socketio.on("join_online")
def on_join_online(data):
    code = data.get("code", "").upper().strip()
    room = rooms.get(code)
    if not room:
        emit("error", {"msg": "Room not found."}); return
    if room["mode"] != "online":
        emit("error", {"msg": "Not an online room."}); return
    if len(room["players"]) >= 2:
        emit("error", {"msg": "Room is full."}); return
    room["players"][2] = request.sid
    join_room(code)
    emit("joined", {"code": code, "player": 2, "mode": "online"})
    socketio.emit("status", {"msg": "Opponent connected! Game starting.", "color": "#4caf50"}, room=code)
    emit_state(code, room["state"])


@socketio.on("move")
def on_move(data):
    code   = data.get("code")
    tier   = data.get("tier")
    row    = int(data.get("row"))
    col    = int(data.get("col"))
    player = int(data.get("player", 1))
    room   = rooms.get(code)
    if not room or room["state"]["over"]:
        return
    state = room["state"]
    if room["mode"] == "online":
        if request.sid != room["players"].get(state["current"]):
            emit("error", {"msg": "Not your turn."}); return
    actual    = state["current"] if room["mode"] == "local" else player
    new_state, err = G.apply_move(state, actual, tier, row, col)
    if err:
        emit("error", {"msg": err}); return
    room["state"] = new_state
    wc = G.win_cells(new_state["board"], new_state["winner"]) \
         if new_state["winner"] and new_state["winner"] != 0 else []
    emit_state(code, new_state, wc)
    if not new_state["over"] and room["mode"] == "bot" and new_state["current"] == 2:
        socketio.emit("status", {"msg": "Bot is thinking…", "color": "#888"}, room=code)
        _do_bot_move(code)


@socketio.on("turn_timeout")
def on_turn_timeout(data):
    code   = data.get("code")
    player = int(data.get("player", 0))
    room   = rooms.get(code)
    if not room or room["state"]["over"]:
        return
    if room["state"]["current"] != player:
        return
    if room["mode"] == "online":
        if request.sid != room["players"].get(player):
            return
    room["state"]["current"] = 3 - player
    socketio.emit("status", {"msg": "Time's up! Turn passed.", "color": "#e53935"}, room=code)
    emit_state(code, room["state"])
    if room["mode"] == "bot" and room["state"]["current"] == 2:
        socketio.emit("status", {"msg": "Bot is thinking…", "color": "#888"}, room=code)
        _do_bot_move(code)


@socketio.on("restart")
def on_restart(data):
    code = data.get("code")
    room = rooms.get(code)
    if not room:
        return
    if room["mode"] == "online" and len(room["players"]) < 2:
        return
    starter = room["next_starter"]
    room["next_starter"] = 3 - starter
    room["state"] = G.new_game_state(starter)
    socketio.emit("status", {"msg": "New game!", "color": "#4caf50"}, room=code)
    if room["mode"] == "bot" and room["state"]["current"] == 2:
        socketio.emit("state", state_payload(room["state"]), room=code)
        socketio.emit("status", {"msg": "Bot is thinking…", "color": "#888"}, room=code)
        _do_bot_move(code)
    else:
        emit_state(code, room["state"])


@socketio.on("disconnect")
def on_disconnect():
    for code, room in list(rooms.items()):
        if request.sid in room["players"].values():
            socketio.emit("status", {"msg": "Opponent disconnected.", "color": "#e53935"}, room=code)
            del rooms[code]
            break


def _do_bot_move(code):
    room = rooms.get(code)
    if not room or room["state"]["over"]:
        return
    move = G.bot_best_move(room["state"])
    if move:
        tier, r, c = move
        new_state, _ = G.apply_move(room["state"], 2, tier, r, c)
        if new_state:
            room["state"] = new_state
            wc = G.win_cells(new_state["board"], new_state["winner"]) \
                 if new_state["winner"] and new_state["winner"] != 0 else []
            emit_state(code, new_state, wc)
    else:
        room["state"]["current"] = 1
        emit_state(code, room["state"])


port = int(os.environ.get("PORT", 5000))
socketio.run(app, host="0.0.0.0", port=port, allow_unsafe_werkzeug=True)
