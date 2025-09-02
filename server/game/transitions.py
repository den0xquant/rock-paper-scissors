from typing import Callable
from server.game.states import RoomState, PlayerState
from server.game.events import ClientEvent
from server.game.models import Move, Outcome, PlayerCtx, RoomCtx
from server.game.rules import judge


class FSM:
    def __init__(self, initial: RoomState) -> None:
        self.state: RoomState = initial
        self._table: dict[tuple[RoomState, ClientEvent], Callable[..., RoomState]] = {}

    def on(self, state: RoomState, event: ClientEvent):
        def decorator(fn: Callable[..., RoomState]):
            self._table[(state, event)] = fn
            return fn
        return decorator

    def send(self, event: ClientEvent, **kwargs):
        handler = self._table.get((self.state, event))
        if not handler:
            print(f"[debug] Нет перехода {self.state} -> {event}?")
            return
        self.state = handler(self, **kwargs)

    def on_player_join(self, ctx: RoomCtx, pid: str) -> str:
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
        return "WAITING_MOVE"

    def on_start(self, ctx: RoomCtx) -> str:
        """
        Возврат server-side события для удобства бродкаста:
        'WAITING_OPP' | 'WAITING_MOVE'
        """
        if self.state == RoomState.EMPTY:
            # не сработает, т.к. без игроков; для надёжности
            return "WAITING_OPP"
        if self.state == RoomState.ONE_WAITING:
            # второй игрок ещё не зашёл — просто ждём оппа
            return "WAITING_OPP"
        # если 2 игрока в комнате — идём в ожидание ходов (новый раунд)
        ctx.round_id += 1
        for p in ctx.players.values():
            p.last_move = None
            p.state = PlayerState.READY
        self.state = RoomState.ROUND_AWAIT_MOVES
        return "WAITING_MOVE"

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
        from server.game.models import Outcome
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
            "outcome": "draw" if winner_pid is None else "win",
            "winner": winner_pid,
            "score": {a.pid: a.score, b.pid: b.score},
        }

        # переходим в ROUND_RESULT на миг
        self.state = RoomState.ROUND_RESULT
        return payload

    def has_match_winner(self, ctx: RoomCtx) -> str | None:
        need = ctx.best_of // 2 + 1
        for p in ctx.players.values():
            if p.score >= need:
                return p.pid
        return

    def next_round_or_over(self, ctx: RoomCtx) -> str:
        """
        Возвращает тип сообщения для фронта:
          'MATCH_OVER' или 'WAITING_MOVE' (для следующего раунда)
        """
        winner = self.has_match_winner(ctx)
        if winner:
            self.state = RoomState.MATCH_OVER
            return "MATCH_OVER"
        # иначе продолжаем матч
        ctx.round_id += 1
        for p in ctx.players.values():
            p.last_move = None
            p.state = PlayerState.READY
        self.state = RoomState.ROUND_AWAIT_MOVES
        return "WAITING_MOVE"

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


def build_transitions() -> FSM:
    fsm = FSM(RoomState.EMPTY)

    @fsm.on(state=RoomState.EMPTY, event=ClientEvent.START)
    def one_player_connected(self: FSM, **kwargs):
        return RoomState.ONE_WAITING
    
    @fsm.on(state=RoomState.ONE_WAITING, event=ClientEvent.START)
    def start_game(self: FSM, **kwargs):
        return RoomState.ROUND_AWAIT_MOVES

    @fsm.on(state=RoomState.ROUND_AWAIT_MOVES, event=ClientEvent.MOVE)
    def move(self: FSM, move: Move, **kwargs):
        return RoomState.ROUND_RESULT
    
    @fsm.on(state=RoomState.ROUND_RESULT, event=ClientEvent.START)
    def start_again(self: FSM, **kwargs):
        return RoomState.ONE_WAITING

    return fsm


fsm = build_transitions()
