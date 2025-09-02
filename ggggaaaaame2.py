# server/game/transitions.py
from __future__ import annotations

from dataclasses import asdict
from typing import Callable, Dict, Iterable, Optional, Tuple

from server.game.states import RoomState, PlayerState
from server.game.models import RoomCtx, PlayerCtx, Move, Outcome
from server.game.rules import judge


class TransitionError(RuntimeError):
    pass


EmitFn = Callable[[str, dict], None]


class RoomFSM:
    """
    FSM матча 1x1. Управляет состоянием комнаты и игроков.
    API спроектирован под дальнейшую интеграцию с WebSocket:
      - каждый публичный метод делает одну "диспатч"-операцию
      - сайд-эффекты отдаются через emit(type, data)
    """

    def __init__(self, ctx: Optional[RoomCtx] = None, emit: Optional[EmitFn] = None):
        self.ctx: RoomCtx = ctx or RoomCtx()
        self.emit: EmitFn = emit or (lambda _type, _data: None)

    # --------- Публичные действия (внешние события) ---------

    def player_join(self, pid: str) -> None:
        """Игрок подключился к комнате."""
        if pid in self.ctx.players:
            # идемпотентность
            p = self.ctx.players[pid]
            p.state = PlayerState.CONNECTED
            self._recalc_room_state_after_join()
            self.emit("PLAYER_REJOINED", self._snapshot(pid))
            return

        if len(self.ctx.players) >= 2:
            raise TransitionError("Room is full (2/2).")

        self.ctx.players[pid] = PlayerCtx(pid=pid, state=PlayerState.CONNECTED)
        self._recalc_room_state_after_join()
        self.emit("PLAYER_JOINED", self._snapshot(pid))

    def player_leave(self, pid: str) -> None:
        """Игрок ушёл/разорвал соединение."""
        if pid not in self.ctx.players:
            return
        self.ctx.players.pop(pid, None)
        # Если кто-то вышел посреди матча, комната возвращается к WAITING/EMPTY.
        self._recalc_room_state_after_leave()
        self.emit("PLAYER_LEFT", {"pid": pid, "room": self._room_view()})

    def set_ready(self, pid: str, ready: bool = True) -> None:
        """Игрок подтверждает готовность к раунду."""
        self._require_state(RoomState.READY_CHECK)
        p = self._player(pid)
        if p.state not in (PlayerState.CONNECTED, PlayerState.BETWEEN_ROUNDS, PlayerState.READY):
            raise TransitionError(f"Player {pid} cannot set ready from {p.state.name}")

        p.state = PlayerState.READY if ready else PlayerState.CONNECTED
        self.emit("PLAYER_READY" if ready else "PLAYER_UNREADY", self._snapshot(pid))

        if self._both(lambda pl: pl.state == PlayerState.READY):
            self._start_round()

    def make_move(self, pid: str, move: Move) -> None:
        """Игрок делает ход в текущем раунде."""
        self._require_state(RoomState.ROUND_AWAIT_MOVES)
        p = self._player(pid)
        if p.state not in (PlayerState.READY, PlayerState.MOVED):
            raise TransitionError(f"Player {pid} cannot move from {p.state.name}")

        # идемпотентность — второй вызов просто игнорим (но оставляем первый ход)
        if p.state == PlayerState.MOVED:
            self.emit("MOVE_DUPLICATE", self._snapshot(pid))
            return

        p.last_move = move
        p.state = PlayerState.MOVED
        self.emit("MOVE_ACCEPTED", self._snapshot(pid))

        if self._both(lambda pl: pl.state == PlayerState.MOVED):
            self._resolve_round()

    def round_timeout(self) -> None:
        """
        Таймаут раунда (например, 20с). Если ровно один сходил — он выигрывает.
        Если никто — ничья. Если оба — раунд уже будет резолвлен.
        """
        self._require_state(RoomState.ROUND_AWAIT_MOVES)
        if self._both(lambda pl: pl.state == PlayerState.MOVED):
            return  # уже решится обычным путём

        moves = [(pl.pid, pl.last_move) for pl in self.ctx.players.values()]
        moved = [pid for pid, mv in moves if mv is not None]

        # Никто не сходил — ничья.
        if len(moved) == 0:
            self.ctx.last_result = "draw"
            self.emit("ROUND_TIMEOUT_DRAW", self._room_view())
            self._after_round(draw=True)
            return

        # Один сходил — ему победа техническая.
        winner_pid = moved[0]
        for pl in self.ctx.players.values():
            if pl.pid == winner_pid:
                pl.score += 1
        self.ctx.last_result = f"{winner_pid} wins by timeout"
        self.emit("ROUND_TIMEOUT_WIN", {"winner": winner_pid, "room": self._room_view()})
        self._after_round(draw=False)

    def restart_match(self) -> None:
        """Сбросить счёт и начать матч заново (оба остаются в комнате)."""
        self._require_players_count(2)
        self._reset_scores_and_round()
        # Возвращаем обоих в CONNECTED, просим готовность
        for pl in self.ctx.players.values():
            pl.state = PlayerState.CONNECTED
            pl.last_move = None
        self.ctx.state = RoomState.READY_CHECK
        self.emit("MATCH_RESTARTED", self._room_view())

    def abort(self) -> None:
        """Форс-стоп всей комнаты (например, админская команда)."""
        self.ctx.players.clear()
        self.ctx.state = RoomState.EMPTY
        self.ctx.round_id = 0
        self.ctx.last_result = None
        self.emit("ROOM_ABORTED", self._room_view())

    # --------- Внутренние действия / переходы ---------

    def _start_round(self) -> None:
        """READY_CHECK -> ROUND_AWAIT_MOVES: обнуляем last_move, инкрементируем round_id."""
        self._require_state(RoomState.READY_CHECK)
        self.ctx.round_id += 1
        for pl in self.ctx.players.values():
            pl.last_move = None
            # готов к ходу
            pl.state = PlayerState.READY
        self.ctx.state = RoomState.ROUND_AWAIT_MOVES
        self.emit("ROUND_START", self._room_view())

    def _resolve_round(self) -> None:
        """Считаем результат, начисляем очки, уведомляем и двигаем состояние комнаты."""
        self._require_state(RoomState.ROUND_AWAIT_MOVES)
        p1, p2 = self._two_players()
        outcome = judge(p1.last_move, p2.last_move)  # Outcome

        if outcome == Outcome.DRAW:
            self.ctx.last_result = "draw"
            self.emit("ROUND_RESULT", {
                "round_id": self.ctx.round_id,
                "outcome": "draw",
                "p1": {"pid": p1.pid, "move": p1.last_move.value},
                "p2": {"pid": p2.pid, "move": p2.last_move.value},
                "score": self._score_view(),
            })
            self._after_round(draw=True)
            return

        winner = p1 if outcome == Outcome.WIN else p2
        winner.score += 1
        self.ctx.last_result = f"{winner.pid} wins"
        self.emit("ROUND_RESULT", {
            "round_id": self.ctx.round_id,
            "outcome": "win",
            "winner": winner.pid,
            "p1": {"pid": p1.pid, "move": p1.last_move.value},
            "p2": {"pid": p2.pid, "move": p2.last_move.value},
            "score": self._score_view(),
        })
        self._after_round(draw=False)

    def _after_round(self, draw: bool) -> None:
        """Проверяем матч-пойнт и готовим следующий шаг."""
        if self._has_match_winner():
            self.ctx.state = RoomState.MATCH_OVER
            self._set_players(PlayerState.BETWEEN_ROUNDS)  # финальный экран
            self.emit("MATCH_OVER", {
                "winner": self._match_winner_pid(),
                "score": self._score_view(),
                "rounds": self.ctx.round_id,
            })
            return

        # Иначе — следующий раунд: ждём ready
        self.ctx.state = RoomState.READY_CHECK
        self._set_players(PlayerState.BETWEEN_ROUNDS)
        self.emit("ASK_READY", self._room_view())

    # --------- Хелперы/гарды ---------

    def wins_needed(self) -> int:
        return self.ctx.best_of // 2 + 1

    def _match_winner_pid(self) -> Optional[str]:
        need = self.wins_needed()
        for pl in self.ctx.players.values():
            if pl.score >= need:
                return pl.pid
        return None

    def _has_match_winner(self) -> bool:
        return self._match_winner_pid() is not None

    def _reset_scores_and_round(self) -> None:
        self.ctx.round_id = 0
        self.ctx.last_result = None
        for pl in self.ctx.players.values():
            pl.score = 0
            pl.last_move = None

    def _set_players(self, state: PlayerState) -> None:
        for pl in self.ctx.players.values():
            pl.state = state

    def _both(self, predicate) -> bool:
        self._require_players_count(2)
        return all(predicate(pl) for pl in self.ctx.players.values())

    def _player(self, pid: str) -> PlayerCtx:
        try:
            return self.ctx.players[pid]
        except KeyError as e:
            raise TransitionError(f"Unknown player {pid}") from e

    def _two_players(self) -> Tuple[PlayerCtx, PlayerCtx]:
        self._require_players_count(2)
        p = list(self.ctx.players.values())
        return p[0], p[1]

    def _require_players_count(self, n: int) -> None:
        if len(self.ctx.players) != n:
            raise TransitionError(f"Requires exactly {n} players, have {len(self.ctx.players)}")

    def _require_state(self, state: RoomState) -> None:
        if self.ctx.state != state:
            raise TransitionError(f"Invalid state {self.ctx.state.name}, expected {state.name}")

    def _recalc_room_state_after_join(self) -> None:
        cnt = len(self.ctx.players)
        if cnt == 0:
            self.ctx.state = RoomState.EMPTY
        elif cnt == 1:
            self.ctx.state = RoomState.ONE_WAITING
        else:
            # при двух игроках ждём их ready
            self.ctx.state = RoomState.READY_CHECK

    def _recalc_room_state_after_leave(self) -> None:
        cnt = len(self.ctx.players)
        if cnt == 0:
            self.ctx.state = RoomState.EMPTY
            self._reset_scores_and_round()
        else:
            # один остался — матч логически прерван
            survivor = list(self.ctx.players.values())[0]
            survivor.state = PlayerState.CONNECTED
            self.ctx.state = RoomState.ONE_WAITING
            self.emit("MATCH_ABORTED", self._room_view())

    # --------- Представление/снапшоты для emit ---------

    def _score_view(self) -> dict:
        return {pl.pid: pl.score for pl in self.ctx.players.values()}

    def _players_view(self) -> Iterable[dict]:
        for pl in self.ctx.players.values():
            yield {
                "pid": pl.pid,
                "state": pl.state.name,
                "score": pl.score,
                "last_move": (pl.last_move.value if pl.last_move else None),
            }

    def _room_view(self) -> dict:
        return {
            "state": self.ctx.state.name,
            "best_of": self.ctx.best_of,
            "wins_needed": self.wins_needed(),
            "round_id": self.ctx.round_id,
            "last_result": self.ctx.last_result,
            "players": list(self._players_view()),
        }

    def _snapshot(self, pid: Optional[str] = None) -> dict:
        data = {"room": self._room_view()}
        if pid and pid in self.ctx.players:
            pl = self.ctx.players[pid]
            data["player"] = {
                "pid": pl.pid,
                "state": pl.state.name,
                "score": pl.score,
                "last_move": pl.last_move.value if pl.last_move else None,
            }
        return data


# --------- Пример использования (stdout UI) ---------
if __name__ == "__main__":
    # Демонстрация без WS: вводим команды в консоль
    def printer(ev: str, data: dict) -> None:
        print(f"[{ev}] {data}")

    fsm = RoomFSM(emit=printer)

    # Два игрока входят
    fsm.player_join("P1")
    fsm.player_join("P2")

    # Оба жмут ready -> старт раунда
    fsm.set_ready("P1", True)
    fsm.set_ready("P2", True)

    # Делают ходы
    fsm.make_move("P1", Move.ROCK)
    fsm.make_move("P2", Move.SCISSORS)

    # Следующий раунд: снова ready
    fsm.set_ready("P1", True)
    fsm.set_ready("P2", True)

    # Таймаут кейс (только P1 сходил)
    fsm.make_move("P1", Move.PAPER)
    fsm.round_timeout()
