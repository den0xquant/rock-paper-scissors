from typing import Callable
from server.game.states import RoomState, PlayerState
from server.game.events import ClientEvent, ServerEvent
from server.game.models import Move, Outcome, PlayerCtx, RoomCtx
from server.game.rules import judge


class FSM:
    def __init__(self, initial: RoomState) -> None:
        self.state: RoomState = initial
        self._table: dict[tuple[RoomState, ClientEvent], Callable[..., RoomState]] = {}

    def on_player_join(self, ctx: RoomCtx, pid: str) -> ServerEvent:
        if pid not in ctx.players:
            ctx.players[pid] = PlayerCtx(pid=pid, state=PlayerState.CONNECTED)

        n = len(ctx.players)
        if n == 0:
            self.state = RoomState.EMPTY
        elif n == 1:
            self.state = RoomState.ONE_WAITING
        else:
            ctx.round_id += 1
            for p in ctx.players.values():
                p.last_move = None
                p.state = PlayerState.READY
            self.state = RoomState.ROUND_AWAIT_MOVES
        return ServerEvent.WAITING_MOVE

    def on_start(self, ctx: RoomCtx) -> ServerEvent:
        if self.state in (RoomState.EMPTY, RoomState.ONE_WAITING,):
            return ServerEvent.WAITING_OPP
        # если 2 игрока в комнате — идём в ожидание ходов (новый раунд)
        ctx.round_id += 1
        for p in ctx.players.values():
            p.last_move = None
            p.state = PlayerState.READY
        self.state = RoomState.ROUND_AWAIT_MOVES
        return ServerEvent.WAITING_MOVE

    def on_move(self, ctx: RoomCtx, pid: str, move: Move) -> dict | None:
        """
        Возврат:
          None — ещё ждём второй ход (первый сделал)
          dict (ROUND_RESULT payload) — оба сходили, можно бродкастить результат
        """
        if self.state != RoomState.ROUND_AWAIT_MOVES:
            return None
        p = ctx.players[pid]
        if p.last_move is not None:
            # идемпотентность — игнор
            return None
        p.last_move = move
        p.state = PlayerState.MOVED

        # соберём два хода
        players = list(ctx.players.values())
        if len(players) != 2:
            return None
        a, b = players[0], players[1]
        if a.last_move is None or b.last_move is None:
            return None

        # оба ход есть — считаем
        out = judge(a.last_move, b.last_move)
        winner_pid: str | None = None
        if out == Outcome.WIN:
            a.score += 1
            winner_pid = a.pid
        elif out == Outcome.LOSE:
            b.score += 1
            winner_pid = b.pid

        payload = {
            "round_id": ctx.round_id,
            "p1": {"pid": a.pid, "move": a.last_move.value},
            "p2": {"pid": b.pid, "move": b.last_move.value},
            "winner": winner_pid,
            "score": {a.pid: a.score, b.pid: b.score},
        }
        self.state = RoomState.ROUND_RESULT
        return payload

    def has_match_winner(self, ctx: RoomCtx) -> str | None:
        need = ctx.best_of // 2 + 1
        for p in ctx.players.values():
            if p.score >= need:
                return p.pid
        return

    def next_round_or_over(self, ctx: RoomCtx) -> ServerEvent:
        """
        Возвращает тип сообщения для фронта:
          'MATCH_OVER' или 'WAITING_MOVE' (для следующего раунда)
        """
        winner = self.has_match_winner(ctx)
        if winner:
            self.state = RoomState.MATCH_OVER
            ctx.last_result = None
            ctx.round_id = 0
            ctx.players.clear()
            return ServerEvent.MATCH_OVER

        ctx.round_id += 1
        for p in ctx.players.values():
            p.last_move = None
            p.state = PlayerState.READY
        self.state = RoomState.ROUND_AWAIT_MOVES
        return ServerEvent.WAITING_MOVE

    def on_leave(self, ctx: RoomCtx, pid: str) -> None:
        ctx.players.pop(pid, None)
        n = len(ctx.players)
        if n == 0:
            self.state = RoomState.EMPTY
            ctx.round_id = 0
        else:
            # остался один — ждём оппонента
            solo = next(iter(ctx.players.values()))
            solo.state = PlayerState.CONNECTED
            self.state = RoomState.ONE_WAITING

    def on_restart(self, ctx: RoomCtx, pid: str) -> ServerEvent:
        if pid not in ctx.players:
            ctx.players[pid] = PlayerCtx(pid=pid, state=PlayerState.READY)

        n = len(ctx.players)
        if n == 2:
            ctx.round_id += 1
            for p in ctx.players.values():
                p.last_move = None
                p.state = PlayerState.READY
            self.state = RoomState.ROUND_AWAIT_MOVES
            return ServerEvent.WAITING_MOVE
        return ServerEvent.WAITING_OPP
