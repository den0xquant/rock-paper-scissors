// ===== CONFIG =====
const API_PATH = "/api"
const WS_PATH = "/ws/rooms/room-1"; // поменяй, если у тебя другой путь (например, "/ws")
const queryParameters = new URLSearchParams(location.search);
const t = queryParameters.get("t") || "";
const TG_ID = queryParameters.get("tg_id") || "";
const ROOM = queryParameters.get("room") || "room-1";
const IMG = (name) => `images/${name}.png`; // относительный путь к картинкам


// ===== STATE =====
let SCORE = 0;
let ws = null;
let wsConnected = false;
let reconnectTimer = null;

let currentRoundId = null;
let lastUserHand = null;

// ===== DOM =====
const $ = (s) => document.querySelector(s);
const handsBox = $(".game-hands");
const contestBox = $(".contest");
const mobileContest = $(".mobile-contest");
const mobileResult = $(".mobile-result");

const elScore = $("#score");
const elUserPick = $("#user-pick");
const elCPU = $("#computer-pick");
const elMobUser = $("#mobile-user-pick");
const elMobCPU = $("#mobile-computer-pick");
const elDecision = $("#decision");
const elDecisionMob = $("#mobile-decision");

// ===== UI HELPERS =====
function showHands() {
  contestBox.style.display = "none";
  mobileContest.style.display = "none";
  mobileResult.style.display = "none";
  setDecision("Make your move");
}

function showContest() {
  handsBox.style.display = "none";
  contestBox.style.display = "flex";
  mobileContest.style.display = "flex";
  mobileResult.style.display = "flex";
}

function setDecision(decision, kind) {
  elDecision.textContent = decision;
  elDecisionMob.textContent = decision;
  ["win", "lose", "draw"].forEach(k => {
    elDecision.classList.toggle(k, k === kind);
    elDecisionMob.classList.toggle(k, k === kind);
  });
}

function setScore(score) {
  SCORE = score;
  elScore.textContent = score;
}

// ===== OFFLINE FALLBACK (если WS не работает) =====
const LOCAL_HANDS = ["Rock", "Paper", "Scissors"];
const localCPU = () => LOCAL_HANDS[Math.floor(Math.random() * 3)];
const judge = (you, opp) => {
  if (you === opp) return "draw";
  if (you === "Rock" && opp === "Scissors") return "win";
  if (you === "Scissors" && opp === "Paper") return "win";
  if (you === "Paper" && opp === "Rock") return "win";
  return "lose";
};
function offlinePlay(hand) {
  showContest();
  const cpu = localCPU();
  elCPU.src = IMG(cpu); elMobCPU.src = IMG(cpu);
  applyOutcome(judge(hand, cpu));
}

// ===== WS CORE =====
function wsUrl() {
  const proto = location.protocol === "https:" ? "wss" : "ws";
  return `${proto}://${location.host}${WS_PATH}`;
}

function connectWS() {
  clearTimeout(reconnectTimer);
  try { ws = new WebSocket(wsUrl()); } catch { scheduleReconnect(); return; }
  ws.addEventListener("open", onOpen);
  ws.addEventListener("message", onMessage);
  ws.addEventListener("close", onClose);
  ws.addEventListener("error", onError);
}

function onOpen() {
  wsConnected = true;
  // сообщаем о входе/готовности
  sendWS({ type: "JOINED", data: { t: t, room: ROOM } });
}

function onClose() {
  wsConnected = false;
  currentRoundId = null;
  // scheduleReconnect();
}

function onError() {
  // тихо логируем, без алертов
}

function scheduleReconnect() {
  if (reconnectTimer) return;
  reconnectTimer = setTimeout(() => { reconnectTimer = null; connectWS(); }, 1200);
}

function sendWS(obj) {
  if (!ws || ws.readyState !== 1) return;
  ws.send(JSON.stringify(obj));
}

// ===== WS MESSAGE HANDLERS =====
function onMessage(ev) {
  let msg; try { msg = JSON.parse(ev.data); } catch { return; }
  const { type, data } = msg || {};

  switch (type) {
    case "JOINED": {
      // можно подсветить “подключено”, если нужно
      // если сервер сразу умеет стартануть раунд — ждем ROUND_START
      console.log("Joined the game room", data);
      break;
    }
    case "ROOM_FULL": {
      setDecision("Room is full", "lose");
      break;
    }
    case "ROUND_START": {
      // начало нового раунда
      currentRoundId = data?.round_id || null;
      lastUserHand = null;
      showHands(); // вернёмся на экран выбора
      break;
    }
    case "RESULT": {
      // сервер прислал результат
      const you = cap(data?.you_move || "");
      const opp = cap(data?.opp_move || "");
      const outcome = (data?.outcome || "draw").toLowerCase();

      // если пользователь ещё на экране выбора — переводим в contest
      showContest();

      // отрисовываем руки из ответа сервера (даже если local картинка уже стояла)
      if (you) { elUserPick.src = IMG(you); elMobUser.src = IMG(you); }
      if (opp) { elCPU.src = IMG(opp); elMobCPU.src = IMG(opp); }

      applyOutcome(outcome);
      break;
    }
    case "OPP_TIMEOUT": {
      // победа по таймауту оппонента
      showContest();
      setDecision("Opponent timeout — you win", "win");
      setScore(SCORE + 1);
      break;
    }
    case "OPP_LEFT": {
      // оппонент ушел — можно ждать нового ROUND_START
      setDecision("Opponent left — waiting…");
      break;
    }
    case "WAIT_FOR_OPPONENT": {
      // ожидание второго игрока
      setDecision("Waiting for opponent…");
      break;
    }
    case "OPP_READY": {
      setDecision("Opponent is ready");
      break;
    }
    default: {
      // игнор неизвестных событий
      break;
    }
  }
}

// ===== GAME FLOW (публичные функции, совместимые с твоим HTML) =====
function userPick(hand) {
  lastUserHand = hand; // Rock/Paper/Scissors

  // показать экран результата сразу, как у тебя
  showContest();

  // поставить картинки своего выбора
  elUserPick.src = IMG(hand);
  elMobUser.src = IMG(hand);

  if (wsConnected && currentRoundId) {
    // отправляем ход на сервер
    sendWS({
      type: "MOVE",
      data: { tg_id: TG_ID, round_id: currentRoundId, move: hand.toLowerCase(), ts: Date.now() }
    });
    // до результата
    setDecision("Waiting for opponent…");
  } else {
    // оффлайн режим — локальный бот
    offlinePlay(hand);
  }
}

function restartGame() {
  // Обычно сервер сам шлёт новый ROUND_START.
  // Если у тебя требуется ручной триггер — раскомментируй:
  sendWS({ type: "READY", data: { room: ROOM } });
  showHands();
}

// ===== HELPERS =====
function cap(s) { return (s || "").charAt(0).toUpperCase() + (s || "").slice(1).toLowerCase(); }

function applyOutcome(outcome) {
  if (outcome === "win") {
    setDecision("You Win!", "win");
    setScore(SCORE + 1);
  } else if (outcome === "lose") {
    setDecision("You Lose!", "lose");
    setScore(SCORE - 1);
  } else {
    setDecision("It's a tie!", "draw");
  }
}

// ===== INIT =====
connectWS();
showHands();

// экспортируем для inline-обработчиков из HTML
window.userPick = userPick;
window.restartGame = restartGame;
