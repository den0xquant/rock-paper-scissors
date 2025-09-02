from server.game.models import Move, Outcome


wins = {
    Move.PAPER: Move.ROCK,
    Move.ROCK: Move.SCISSORS,
    Move.SCISSORS: Move.PAPER,
}


def judge(a: Move, b: Move) -> Outcome:
    if a == b:
        return Outcome.DRAW
    return Outcome.WIN if wins[a] == b else Outcome.LOSE
