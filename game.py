import copy
import random

TIERS    = ["small", "medium", "large", "mega"]
PLAYABLE = ["small", "medium", "large"]

def random_variant():
    return {
        "small":  random.randint(10, 12),
        "medium": random.randint(1, 5),
        "large":  random.randint(1, 5),
        "mega":   0,
    }

LINES = [
    [(0,0),(0,1),(0,2)], [(1,0),(1,1),(1,2)], [(2,0),(2,1),(2,2)],
    [(0,0),(1,0),(2,0)], [(0,1),(1,1),(2,1)], [(0,2),(1,2),(2,2)],
    [(0,0),(1,1),(2,2)], [(0,2),(1,1),(2,0)],
]


def merge_up(tier):
    idx = TIERS.index(tier)
    return TIERS[min(idx + 1, len(TIERS) - 1)]


def new_game_state(starter=1):
    tier_max = random_variant()
    variant_key = f"{tier_max['small']}-{tier_max['medium']}-{tier_max['large']}"
    return {
        "board":   [[[] for _ in range(3)] for _ in range(3)],
        "counts":  {1: dict(tier_max), 2: dict(tier_max)},
        "current": starter,
        "over":    False,
        "winner":  None,
        "variant": variant_key,
    }


def valid_moves(board, counts, player):
    moves = []
    for tier in PLAYABLE:
        if counts[player][tier] == 0:
            continue
        for r in range(3):
            for c in range(3):
                stack = board[r][c]
                if not stack:
                    moves.append((tier, r, c))
                elif (stack[-1][1] != "mega"
                      and stack[-1][1] == tier):
                    moves.append((tier, r, c))
    return moves


def apply_move(state, player, tier, row, col):
    """Returns (new_state, error_string). new_state is None on error."""
    board  = state["board"]
    counts = state["counts"]
    stack  = board[row][col]

    if stack:
        tp, tt = stack[-1]
        if tt == "mega":
            return None, "Mega barrels can't be covered!"
        if tt != tier:
            return None, "You can only merge the same tier!"

    new = copy.deepcopy(state)
    ns  = new["board"][row][col]
    if ns:
        ns[-1] = (player, merge_up(tier))
    else:
        ns.append((player, tier))
    new["counts"][player][tier] -= 1

    winner = check_winner(new["board"])
    if winner:
        new["over"]    = True
        new["winner"]  = winner
        new["current"] = None
    elif check_draw(new["board"], new["counts"]):
        new["over"]    = True
        new["winner"]  = 0  # draw
        new["current"] = None
    else:
        new["current"] = 3 - player

    return new, None


def check_winner(board):
    for line in LINES:
        owners = [board[r][c][-1][0] if board[r][c] else None for r, c in line]
        if owners[0] is not None and owners[0] == owners[1] == owners[2]:
            return owners[0]
    return None


def win_cells(board, winner):
    cells = []
    for line in LINES:
        owners = [board[r][c][-1][0] if board[r][c] else None for r, c in line]
        if owners == [winner, winner, winner]:
            for pos in line:
                if list(pos) not in cells:
                    cells.append(list(pos))
    return cells


def check_draw(board, counts):
    for p in [1, 2]:
        if valid_moves(board, counts, p):
            return False
    return True


# ── BOT ───────────────────────────────────────────────────────────────────────

def _sim(board, counts, player, tier, row, col):
    b = copy.deepcopy(board)
    c = copy.deepcopy(counts)
    s = b[row][col]
    if s:
        s[-1] = (player, merge_up(tier))
    else:
        s.append((player, tier))
    c[player][tier] -= 1
    return b, c


def _heuristic(board, counts):
    bot, human = 2, 1
    score = 0
    for line in LINES:
        cells  = [board[r][c] for r, c in line]
        owners = [s[-1][0] if s else None for s in cells]
        tiers  = [s[-1][1] if s else None for s in cells]
        mine   = owners.count(bot)
        theirs = owners.count(human)
        if theirs == 0 and mine > 0:
            tb = sum(TIERS.index(t) for t in tiers if t)
            score += (30 if mine == 2 else 5) + tb
        if mine == 0 and theirs > 0:
            tb = sum(TIERS.index(t) for t in tiers if t)
            score -= (30 if theirs == 2 else 5) + tb
    center = board[1][1]
    if center:
        score += 6 if center[-1][0] == bot else -6
    score += (counts[bot]["small"]  - counts[human]["small"])  * 0.3
    score += (counts[bot]["medium"] - counts[human]["medium"]) * 1.0
    score += (counts[bot]["large"]  - counts[human]["large"])  * 2.0
    return score


def _minimax(board, counts, is_bot_turn, depth, alpha, beta):
    bot, human = 2, 1
    winner = check_winner(board)
    if winner == bot:
        return 10000 + depth
    if winner == human:
        return -10000 - depth
    player = bot if is_bot_turn else human
    moves  = valid_moves(board, counts, player)
    if not moves or depth == 0:
        return _heuristic(board, counts)

    def priority(m):
        t, r, c = m
        return (bool(board[r][c]),
                {(1,1):0,(0,0):1,(0,2):1,(2,0):1,(2,2):1}.get((r,c), 2))

    moves = sorted(moves, key=priority)
    if is_bot_turn:
        best = float('-inf')
        for m in moves:
            nb, nc = _sim(board, counts, player, *m)
            val = _minimax(nb, nc, False, depth-1, alpha, beta)
            best = max(best, val)
            alpha = max(alpha, best)
            if beta <= alpha:
                break
        return best
    else:
        best = float('inf')
        for m in moves:
            nb, nc = _sim(board, counts, player, *m)
            val = _minimax(nb, nc, True, depth-1, alpha, beta)
            best = min(best, val)
            beta = min(beta, best)
            if beta <= alpha:
                break
        return best


def bot_best_move(state):
    board  = state["board"]
    counts = state["counts"]
    moves  = valid_moves(board, counts, 2)
    if not moves:
        return None
    best_score, best_move = float('-inf'), None
    alpha = float('-inf')
    beta  = float('inf')
    for m in moves:
        nb, nc = _sim(board, counts, 2, *m)
        score  = _minimax(nb, nc, False, 3, alpha, beta)
        if score > best_score:
            best_score = score
            best_move  = m
        alpha = max(alpha, score)
    return best_move
