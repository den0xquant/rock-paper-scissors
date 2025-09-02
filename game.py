#!/usr/bin/env python3
# rps.py — Rock/Paper/Scissors с FSM и stdout-UI
from __future__ import annotations

import enum
import random
from dataclasses import dataclass, field
from typing import Callable, Dict, Optional, Tuple


# ===== Домены =====
class Move(enum.Enum):
    ROCK = "rock"
    PAPER = "paper"
    SCISSORS = "scissors"

    @staticmethod
    def from_input(s: str) -> Optional["Move"]:
        s = (s or "").strip().lower()
        if s in ("r", "rock", "к", "камень"):
            return Move.ROCK
        if s in ("p", "paper", "б", "бумага"):
            return Move.PAPER
        if s in ("s", "scissors", "н", "ножницы"):
            return Move.SCISSORS
        return None



# ===== FSM =====
class State(enum.Enum):
    INIT = "INIT"
    MENU = "MENU"
    AWAIT_MOVE = "AWAIT_MOVE"
    SHOW_RESULT = "SHOW_RESULT"
    AWAIT_NEXT = "AWAIT_NEXT"
    GAME_OVER = "GAME_OVER"
    EXIT = "EXIT"


class Event(enum.Enum):
    START = "START"
    PLAY = "PLAY"
    SET_BEST_OF = "SET_BEST_OF"
    PLAYER_MOVE = "PLAYER_MOVE"
    RESOLVE = "RESOLVE"
    NEXT_ROUND = "NEXT_ROUND"
    END_GAME = "END_GAME"
    QUIT = "QUIT"


@dataclass
class GameConfig:
    best_of: int = 5
    def wins_needed(self) -> int:
        return self.best_of // 2 + 1


@dataclass
class GameContext:
    config: GameConfig = field(default_factory=GameConfig)
    player_score: int = 0
    cpu_score: int = 0
    round_idx: int = 0
    last_player_move: Optional[Move] = None
    last_cpu_move: Optional[Move] = None
    last_outcome: Optional[Outcome] = None

    def reset_match(self) -> None:
        self.player_score = 0
        self.cpu_score = 0
        self.round_idx = 0
        self.last_player_move = None
        self.last_cpu_move = None
        self.last_outcome = None


class FSM:
    def __init__(self, initial: State, context: GameContext):
        self.state: State = initial
        self.ctx: GameContext = context
        self._table: Dict[Tuple[State, Event], Callable[..., State]] = {}

    def on(self, state: State, event: Event):
        def decorator(fn: Callable[..., State]):
            self._table[(state, event)] = fn
            return fn
        return decorator

    def send(self, event: Event, **kwargs) -> None:
        handler = self._table.get((self.state, event))
        if not handler:
            # Невалидный переход — просто игнорируем/подсвечиваем
            print(f"[debug] Нет перехода: {self.state.value} --{event.value}--> ?")
            return
        self.state = handler(self, **kwargs)


# ===== UI helpers =====
def print_header(ctx: GameContext) -> None:
    print("\n" + "=" * 48)
    print("       ROCK • PAPER • SCISSORS".center(48))
    print("=" * 48)
    print(
        f"  Формат: best-of {ctx.config.best_of} (до {ctx.config.wins_needed()} побед)\n"
        f"  Счёт: Вы {ctx.player_score} : {ctx.cpu_score} CPU | Раунд #{ctx.round_idx}"
    )
    print("-" * 48)


def ask(prompt: str) -> str:
    try:
        return input(prompt)
    except (EOFError, KeyboardInterrupt):
        return "q"


def choose_best_of() -> Optional[int]:
    raw = ask("Введите нечётное число раундов (напр. 3/5/7) или 'm' для меню: ").strip().lower()
    if raw in ("m", "menu", "меню"):
        return None
    try:
        n = int(raw)
        if n >= 1 and n % 2 == 1 and n <= 99:
            return n
        print("Нужно нечётное число 1..99. Повторите.")
        return choose_best_of()
    except ValueError:
        print("Это не число. Повторите.")
        return choose_best_of()


def cpu_move() -> Move:
    return random.choice(list(Move))


def show_moves_and_result(ctx: GameContext) -> None:
    pm = ctx.last_player_move.value if ctx.last_player_move else "?"
    cm = ctx.last_cpu_move.value if ctx.last_cpu_move else "?"
    outcome = ctx.last_outcome.value if ctx.last_outcome else "?"
    print(f"\nВы: {pm}  |  CPU: {cm}  ->  результат: {outcome.upper()}")
    if ctx.last_outcome == Outcome.WIN:
        print("✅ Вы победили раунд!")
    elif ctx.last_outcome == Outcome.LOSE:
        print("❌ Раунд за CPU.")
    else:
        print("➖ Ничья.")


# ===== Приложение / wiring =====
def build_app() -> FSM:
    ctx = GameContext()
    fsm = FSM(State.INIT, ctx)

    @fsm.on(State.INIT, Event.START)
    def init_start(self: FSM) -> State:
        print_header(self.ctx)
        return State.MENU

    @fsm.on(State.MENU, Event.PLAY)
    def menu_play(self: FSM) -> State:
        self.ctx.reset_match()
        print_header(self.ctx)
        print("Начинаем матч! Вводите ход: [r]ock / [p]aper / [s]cissors")
        print("Команды: [b] — сменить best-of, [m] — меню, [q] — выход")
        return State.AWAIT_MOVE

    @fsm.on(State.MENU, Event.SET_BEST_OF)
    def menu_set_best(self: FSM) -> State:
        val = choose_best_of()
        if val:
            self.ctx.config.best_of = val
            print(f"✔ Формат обновлён: best-of {val}")
        print_header(self.ctx)
        return State.MENU

    @fsm.on(State.MENU, Event.QUIT)
    def menu_quit(self: FSM) -> State:
        print("Пока! 👋")
        return State.EXIT

    @fsm.on(State.AWAIT_MOVE, Event.PLAYER_MOVE)
    def await_player_move(self: FSM, move: Move) -> State:
        self.ctx.last_player_move = move
        self.ctx.last_cpu_move = cpu_move()
        self.ctx.last_outcome = judge(move, self.ctx.last_cpu_move)
        if self.ctx.last_outcome == Outcome.WIN:
            self.ctx.player_score += 1
        elif self.ctx.last_outcome == Outcome.LOSE:
            self.ctx.cpu_score += 1
        self.ctx.round_idx += 1
        return State.SHOW_RESULT

    @fsm.on(State.AWAIT_MOVE, Event.SET_BEST_OF)
    def await_move_set_best(self: FSM) -> State:
        val = choose_best_of()
        if val:
            self.ctx.config.best_of = val
            print(f"✔ Формат обновлён: best-of {val}")
            print("Продолжаем текущий матч.")
        print_header(self.ctx)
        return State.AWAIT_MOVE

    @fsm.on(State.AWAIT_MOVE, Event.QUIT)
    def await_move_quit(self: FSM) -> State:
        print("Пока! 👋")
        return State.EXIT

    @fsm.on(State.SHOW_RESULT, Event.RESOLVE)
    def show_result_resolve(self: FSM) -> State:
        print_header(self.ctx)
        show_moves_and_result(self.ctx)
        # Проверка конца матча
        if self.ctx.player_score >= self.ctx.config.wins_needed() or \
           self.ctx.cpu_score >= self.ctx.config.wins_needed():
            return State.GAME_OVER
        return State.AWAIT_NEXT

    @fsm.on(State.AWAIT_NEXT, Event.NEXT_ROUND)
    def await_next_round(self: FSM) -> State:
        print_header(self.ctx)
        return State.AWAIT_MOVE

    @fsm.on(State.AWAIT_NEXT, Event.QUIT)
    def await_next_quit(self: FSM) -> State:
        print("Пока! 👋")
        return State.EXIT

    @fsm.on(State.AWAIT_NEXT, Event.SET_BEST_OF)
    def await_next_set_best(self: FSM) -> State:
        val = choose_best_of()
        if val:
            self.ctx.config.best_of = val
            print(f"✔ Формат обновлён: best-of {val}")
        print_header(self.ctx)
        return State.AWAIT_NEXT

    @fsm.on(State.GAME_OVER, Event.END_GAME)
    def game_over_end(self: FSM) -> State:
        print_header(self.ctx)
        if self.ctx.player_score > self.ctx.cpu_score:
            print("🎉 Поздравляем! Вы выиграли матч.")
        else:
            print("🤖 CPU оказался сильнее на этот раз.")
        print("\nНажмите [p] — сыграть снова, [m] — меню, [q] — выход.")
        while True:
            cmd = ask("> ").strip().lower()
            if cmd in ("p", "play", "с", "сыграть"):
                return State.MENU  # из меню перейдём в PLAY
            if cmd in ("m", "menu", "меню"):
                return State.MENU
            if cmd in ("q", "quit", "exit", "в", "выход"):
                return State.EXIT
            print("Команда не распознана. Выберите p/m/q.")
    
    return fsm


def main() -> None:
    fsm = build_app()

    # Запуск
    fsm.send(Event.START)

    while fsm.state != State.EXIT:
        if fsm.state == State.MENU:
            print("\nМеню: [p] — играть, [b] — выбрать best-of, [q] — выход")
            cmd = ask("> ").strip().lower()
            if cmd in ("p", "play", "играть"):
                fsm.send(Event.PLAY)
            elif cmd in ("b", "best", "bestof"):
                fsm.send(Event.SET_BEST_OF)
            elif cmd in ("q", "quit", "exit", "выход"):
                fsm.send(Event.QUIT)
            else:
                print("Не понял. Доступно: p/b/q.")

        elif fsm.state == State.AWAIT_MOVE:
            inp = ask("Ход [r/p/s], или [b] — формат, [m] — меню, [q] — выход: ").strip().lower()
            if inp in ("m", "menu", "меню"):
                print_header(fsm.ctx)
                fsm.state = State.MENU
                continue
            if inp in ("b", "best", "bestof"):
                fsm.send(Event.SET_BEST_OF)
                continue
            if inp in ("q", "quit", "exit", "выход"):
                fsm.send(Event.QUIT)
                continue
            mv = Move.from_input(inp)
            if mv is None:
                print("Нужен ход: r/p/s (rock/paper/scissors).")
                continue
            fsm.send(Event.PLAYER_MOVE, move=mv)

        elif fsm.state == State.SHOW_RESULT:
            fsm.send(Event.RESOLVE)

        elif fsm.state == State.AWAIT_NEXT:
            nxt = ask("Enter — следующий раунд | [m] — меню | [b] — формат | [q] — выход: ")
            if nxt.strip() == "":
                fsm.send(Event.NEXT_ROUND)
            else:
                s = nxt.strip().lower()
                if s in ("m", "menu", "меню"):
                    print_header(fsm.ctx)
                    fsm.state = State.MENU
                elif s in ("b", "best", "bestof"):
                    fsm.send(Event.SET_BEST_OF)
                elif s in ("q", "quit", "exit", "выход"):
                    fsm.send(Event.QUIT)
                else:
                    print("Не понял. Нажмите Enter, либо m/b/q.")

        elif fsm.state == State.GAME_OVER:
            fsm.send(Event.END_GAME)

    # State.EXIT — выходим
    # (прощание уже напечатано в хендлерах)


if __name__ == "__main__":
    main()
