// ===== CONFIG =====
const WS_ROOM_PATH = "/ws/rooms";
const queryParameters = new URLSearchParams(location.search);
const t = queryParameters.get("t") || "";
const IMG = (name) => `images/${name}.png`;

// Сколько держим экран результата, прежде чем разрешать новый раунд
const RESULT_GRACE_MS = 1200; // ★ можно 800–1500 по вкусу

// Кулдаун после отмены поиска (3 минуты)
const COOLDOWN_MS = 3 * 60 * 1000;
const LS_CD_KEY = "cooldown_until_ts_v1";

// Экранные состояния верхнего уровня
const APP_STATE = { IDLE: "idle", SEARCHING: "searching", MATCHED: "matched" };
let appState = APP_STATE.IDLE;

// ===== STATE =====
let SCORE = 0;
let ws = null;
let wsConnected = false;
let reconnectTimer = null;
let lastWSMode = false;      // true = lobby/matchmaking, false = room
let allowReconnect = true;   // блокируем ре‑коннект при ручном закрытии (cancel/search→idle)

let currentRoundId = null;
let lastUserHand = null;

const MAX_WINS = 3;
let winsYou = 0;
let winsOpp = 0;

// флаги матча и readiness
const ui = {
  phase: "hands",           // "hands" | "waiting" | "result" | "match_over"
  lastResultAt: 0,
  lastResultRoundId: null,
  pendingRoundStart: null,
  selfReady: false,
  oppReady: false,
};

// ===== DOM =====
const $ = (s) => document.querySelector(s);

// Новые экраны
const startScreen = $("#start-screen");
const searchScreen = $("#search-screen");
const startBtn = $("#start-btn");
const cancelBtn = $("#cancel-btn");
const cooldownWrap = $("#cooldown");
const cooldownLeft = $("#cd-left");

// Твой существующий UI
const handsBox = $(".game-hands");
const contestBox = $(".contest");
const mobileContest = $(".mobile-contest");
const mobileResult = $(".mobile-result");

const elScore = $("#score");
const elUserPick = $("#user-pick");
const elOpp = $("#opp-pick");
const elMobUser = $("#mobile-user-pick");
const elMobOpp = $("#mobile-opp-pick");
const elDecision = $("#decision");
const elDecisionMob = $("#mobile-decision");

const oppReady = $("#opp-ready");
const oppReadyMob = $("#mobile-opp-ready");
const oppLoader = $("#opp-loader");
const oppLoaderMob = $("#mobile-opp-loader");
const elStarsYou = $("#stars-you");
const elStarsOpp = $("#stars-opp");
const btnReady = $("#ready-btn");

// ===== helpers for visibility =====
function showNode(node, display = "flex") { if (node) node.style.display = display; }
function hideNode(node) { if (node) node.style.display = "none"; }

// ===== TOP-LEVEL SCREENS =====
function setAppState(next) {
  appState = next;
  // Сначала спрячем всё верхнего уровня
  hideNode(startScreen);
  hideNode(searchScreen);
  hideNode(handsBox);
  hideNode(contestBox);
  hideNode(mobileContest);
  hideNode(mobileResult);

  switch (next) {
    case APP_STATE.IDLE: {
      showNode(startScreen);
      break;
    }
    case APP_STATE.SEARCHING: {
      showNode(searchScreen);
      break;
    }
    case APP_STATE.MATCHED: {
      // Показываем матчевый UI
      showNode(handsBox);
      showNode(contestBox);
      // showNode(mobileContest);
      // showNode(mobileResult);
      break;
    }
  }
}

// ===== MATCHMAKING FLOW =====
function beginMatchmaking() {
  if (isCooldownActive()) return;

  allowReconnect = true;
  setAppState(APP_STATE.SEARCHING);
  connectWS(true); // ← просто коннект; FIND_MATCH пошлём в onOpen
}

function cancelMatchmaking() {
  if (wsConnected && lastWSMode === true) {
    sendWS({ type: "CANCEL_MATCH" });
  }

  imposeCooldown(Date.now() + COOLDOWN_MS);

  allowReconnect = false;  // не пытаться переподключаться после ручного cancel
  safeCloseWS();

  setAppState(APP_STATE.IDLE);
}

function onMatched(payload) {
  // Сервер прислал комнату
  const newRoom = payload?.room_id || "";
  if (!newRoom) {
    setDecision("Ошибка матча: не пришла комната");
    return;
  }
  ROOM = newRoom;

  // Переподключаемся уже к конкретной комнате
  allowReconnect = true;
  // safeCloseWS();
  // connectWS(false); // normal room mode
  setAppState(APP_STATE.MATCHED);

  // Подготовим UI к раунду
  setOppState("loading");
  setDecision("Make your move");
  resetMatchUI();
}

// ===== COOLDOWN =====
let cdTimer = null;

function isCooldownActive() {
  const until = Number(localStorage.getItem(LS_CD_KEY) || 0);
  const active = until > Date.now();
  if (active) {
    startCooldownTick(until);
    if (startBtn) startBtn.disabled = true;
  } else {
    clearCooldown();
  }
  return active;
}

function imposeCooldown(untilTs) {
  localStorage.setItem(LS_CD_KEY, String(untilTs));
  if (startBtn) startBtn.disabled = true;
  if (cooldownWrap) cooldownWrap.style.display = "block";
  startCooldownTick(untilTs);
}

function startCooldownTick(untilTs) {
  if (!cooldownWrap || !cooldownLeft) return;
  cooldownWrap.style.display = "block";
  tickCooldown(); // сразу показать
  clearInterval(cdTimer);
  cdTimer = setInterval(tickCooldown, 1000);

  function tickCooldown() {
    const until = Number(localStorage.getItem(LS_CD_KEY) || 0);
    const left = Math.max(0, until - Date.now());
    if (left <= 0) {
      clearCooldown();
      return;
    }
    cooldownLeft.textContent = msToMMSS(left);
  }
}

function clearCooldown() {
  localStorage.removeItem(LS_CD_KEY);
  if (startBtn) startBtn.disabled = false;
  if (cooldownWrap) cooldownWrap.style.display = "none";
  clearInterval(cdTimer);
  cdTimer = null;
}

function msToMMSS(ms) {
  const s = Math.ceil(ms / 1000);
  const m = Math.floor(s / 60);
  const r = s % 60;
  return `${String(m).padStart(2, "0")}:${String(r).padStart(2, "0")}`;
}

// ===== EXISTING UI PIECES (unchanged but safer) =====
function setOppState(mode) {
  // mode: 'idle' | 'loading' | 'ready' | 'reveal'
  const show = (el, v) => { if (el) el.style.display = v ? "block" : "none"; };

  // desktop
  show(oppLoader, mode === "loading");
  show(oppReady, mode === "ready");
  show(elOpp, mode === "reveal");

  // mobile
  show(oppLoaderMob, mode === "loading");
  show(oppReadyMob, mode === "ready");
  show(elMobOpp, mode === "reveal");
}

function renderStars() {
  const youStars = elStarsYou ? elStarsYou.querySelectorAll(".star") : [];
  const oppStars = elStarsOpp ? elStarsOpp.querySelectorAll(".star") : [];
  youStars.forEach((s, i) => s.classList.toggle("filled", i < winsYou));
  oppStars.forEach((s, i) => s.classList.toggle("filled", i < winsOpp));
}

function resetMatchUI() {
  winsYou = 0; winsOpp = 0;
  ui.selfReady = false; ui.oppReady = false;
  ui.phase = "hands";
  renderStars();
}

// ===== UI HELPERS =====
function showHands() {
  showNode(handsBox);
  setDecision("Make your move");
  setOppState("loading");
  renderStars();
  ui.phase = "hands";
}

function sendReady() {
  if (!wsConnected) return;
  ui.selfReady = true;
  setDecision("Waiting for opponent…");
  sendWS({ type: "READY", data: { room: ROOM } });
}

function showContest() {
  if (ui.phase === "waiting" || ui.phase === "result") return;
  hideNode(handsBox);
  showNode(contestBox);
  ui.phase = "waiting";
}

function setDecision(text, kind) {
  if (elDecision && elDecision.textContent !== text) elDecision.textContent = text;
  if (elDecision) ["win", "lose", "draw"].forEach(k => elDecision.classList.toggle(k, k === kind));
  if (elDecisionMob && elDecisionMob.textContent !== text) elDecisionMob.textContent = text;
  if (elDecisionMob) ["win", "lose", "draw"].forEach(k => elDecisionMob.classList.toggle(k, k === kind));
}

function setScore(score) {
  if (SCORE === score) return;
  SCORE = score;
  if (elScore) elScore.textContent = score;
}

function showOppLoader(visible) {
  toggleHidden(oppLoader, !visible);
  toggleHidden(oppLoaderMob, !visible);
  toggleHidden(elOpp, visible);
  toggleHidden(elMobOpp, visible);
}

function toggleHidden(el, hidden) {
  if (!el) return;
  const need = hidden ? "add" : "remove";
  if (el.classList.contains("hidden") === hidden) return;
  el.classList[need]("hidden");
}

function setImg(el, path) {
  if (!el || !path) return;
  if (el.getAttribute("src") === path) return;
  el.setAttribute("src", path);
  el.style.display = "block";
}

// ===== WS CORE =====
function wsUrl(matchmaking = false) {
  const proto = location.protocol === "https:" ? "wss" : "ws";
  return `${proto}://${location.host}${WS_ROOM_PATH}/`;
}

function connectWS(matchmakingMode = false) {
  clearTimeout(reconnectTimer);
  lastWSMode = matchmakingMode;
  try { ws = new WebSocket(wsUrl(matchmakingMode)); }
  catch { scheduleReconnect(matchmakingMode); return; }

  ws.addEventListener("open", () => onOpen(matchmakingMode));
  ws.addEventListener("message", onMessage);
  ws.addEventListener("close", onClose);
  ws.addEventListener("error", onError);
}

function safeCloseWS() {
  if (ws) {
    try { ws.onclose = null; ws.onerror = null; ws.onmessage = null; ws.onopen = null; } catch { }
    try { ws.close(); } catch { }
  }
  ws = null;
  wsConnected = false;
}

function onOpen(matchmakingMode) {
  wsConnected = true;
  console.debug("[ws] open", { matchmakingMode, ROOM });

  if (matchmakingMode) {
    // Лобби: начинаем поиск сразу после установления сокета
    sendWS({ type: "FIND_MATCH", data: { t } });
  } else if (ROOM) {
    // Комната: обычный вход
    sendWS({ type: "JOINED", data: { t, room: ROOM } });
  }
}

function onClose() {
  console.debug("[ws] closed");
  wsConnected = false;
  currentRoundId = null;
  // ре‑коннектим только если это не ручной cancel и не стартовый IDLE
  if (allowReconnect && (appState === APP_STATE.SEARCHING || appState === APP_STATE.MATCHED)) {
    scheduleReconnect(lastWSMode);
  }
}

function onError() { /* no-op */ }
function scheduleReconnect(matchmakingMode = false) {
  if (reconnectTimer) return;
  reconnectTimer = setTimeout(() => {
    reconnectTimer = null;
    connectWS(matchmakingMode);
  }, 1200);
}

function sendWS(obj) {
  if (!ws || ws.readyState !== 1) return;
  ws.send(JSON.stringify(obj));
}

// ===== WS MESSAGE HANDLERS =====
function onMessage(ev) {
  let msg; try { msg = JSON.parse(ev.data); } catch { return; }
  const { type, data } = msg || {};
  console.debug("[ws] msg:", type, data);

  switch (type) {
    // ---- МАТЧМЕЙКИНГ ----
    case "MATCH_FOUND": {
      onMatched(data);
      break;
    }
    case "MATCH_SEARCHING": {
      // опционально: обновлять текст «Ищем соперника…»
      break;
    }
    case "MATCH_CANCELLED": {
      // сервер подтвердил отмену — у нас уже повешен кулдаун
      break;
    }

    // ---- АВТОРИЗАЦИЯ В КОМНАТЕ ----
    case "AUTH_OK": {
      // можем при желании сверить TG_ID
      break;
    }
    case "AUTH_FAIL": {
      setDecision("Auth failed");
      allowReconnect = false;
      safeCloseWS();
      setAppState(APP_STATE.IDLE);
      break;
    }

    // ---- ТВОЙ ИМЕЮЩИЙСЯ ПРОТОКОЛ ----
    case "JOINED": { break; }

    case "ROOM_FULL": {
      setDecision("Room is full", "lose");
      break;
    }

    case "ROUND_START": {
      currentRoundId = data?.round_id || null;

      const scores = data?.scores || {};
      winsYou = Number(scores[`${TG_ID}`] || 0);
      const otherScores = Object.keys(scores)
        .filter(k => k !== `${TG_ID}`)
        .map(k => Number(scores[k] || 0));
      winsOpp = otherScores.length ? Math.max(...otherScores) : 0;

      lastUserHand = null;

      if (ui.phase === "match_over") resetMatchUI();

      ui.selfReady = false; ui.oppReady = false;
      showHands();
      break;
    }

    case "RESULT": {
      handleResultBo3(data);
      break;
    }

    case "OPP_READY": {
      ui.oppReady = true;
      setOppState("ready");
      break;
    }

    case "WAIT_FOR_OPPONENT": {
      setOppState("loading");
      break;
    }

    case "OPP_LEFT": {
      setDecision("Opponent left — waiting…");
      break;
    }

    case "MATCH_OVER": {
      const scores = data?.scores || {};
      const youScore = Number(scores[`${TG_ID}`] || 0);
      const otherScores = Object.keys(scores)
        .filter(k => k !== `${TG_ID}`)
        .map(k => Number(scores[k] || 0));
      const oppScore = otherScores.length ? Math.max(...otherScores) : 0;

      const youWon = youScore > oppScore;
      setDecision(youWon ? "Match over — You Win!" : "Match over — You Lose.", youWon ? "win" : "lose");
      ui.phase = "match_over";
      break;
    }

    default: break;
  }
}

// ===== RESULT / ROUND_START логика =====
function handleRoundStart(data) {
  const nextRoundId = data?.round_id || null;

  const now = Date.now();
  const until = ui.lastResultAt + RESULT_GRACE_MS;
  if (ui.phase === "result" && now < until) {
    const delay = until - now;
    if (ui.pendingRoundStart) clearTimeout(ui.pendingRoundStart);
    ui.pendingRoundStart = setTimeout(() => {
      ui.pendingRoundStart = null;
      handleRoundStart(data);
    }, delay);
    return;
  }

  currentRoundId = nextRoundId;
  lastUserHand = null;
  showHands();
}

function handleResultBo3(data) {
  const you = cap(data?.you_move || "");
  const opp = cap(data?.opp_move || "");
  const outcome = (data?.outcome || "draw").toLowerCase();

  showOppLoader(false);
  if (you) { setImg(elUserPick, IMG(you)); setImg(elMobUser, IMG(you)); }
  if (opp) { setImg(elOpp, IMG(opp)); setImg(elMobOpp, IMG(opp)); }

  if (outcome === "win") winsYou = Math.min(MAX_WINS, winsYou + 1);
  if (outcome === "lose") winsOpp = Math.min(MAX_WINS, winsOpp + 1);
  renderStars();

  if (outcome === "draw") setDecision("It's tie.", outcome);
  else if (outcome === "win") setDecision("Victory!", outcome);
  else if (outcome === "lose") setDecision("Defeat :(", outcome);

  ui.phase = "result";
  ui.lastResultAt = Date.now();
  setOppState("reveal");

  if (winsYou >= MAX_WINS || winsOpp >= MAX_WINS) {
    ui.phase = "match_over";
    const youWon = winsYou > winsOpp;
    setDecision(youWon ? "Match over — You Win!" : "Match over — You Lose.", youWon ? "win" : "lose");
  } else {
    // ждём новый ROUND_START
  }
}

function handleResult(data) {
  const rid = data?.round_id ?? currentRoundId;
  if (ui.lastResultRoundId === rid && ui.phase === "result") return;

  ui.lastResultRoundId = rid;
  ui.lastResultAt = Date.now();
  ui.phase = "result";

  if (handsBox && handsBox.style.display !== "none") showContest();

  const you = cap(data?.you_move || "");
  const opp = cap(data?.opp_move || "");
  const outcome = (data?.outcome || "draw").toLowerCase();

  if (you) { setImg(elUserPick, IMG(you)); setImg(elMobUser, IMG(you)); }
  if (opp) { setImg(elOpp, IMG(opp)); setImg(elMobOpp, IMG(opp)); }

  // applyOutcome(outcome); // если нужно
}

// ===== GAME FLOW (вызывается из HTML) =====
function userPick(hand) {
  if (appState !== APP_STATE.MATCHED) {
    setDecision("Сначала начните игру и дождитесь соперника.");
    return;
  }
  if (ui.phase === "match_over") {
    setDecision("Match finished. Press Ready to start a new match.");
    return;
  }
  showContest();
  setImg(elUserPick, IMG(hand));
  setImg(elMobUser, IMG(hand));
  setOppState("loading");
  setDecision("Waiting for opponent…");
  ui.phase = "waiting";

  if (wsConnected && currentRoundId) {
    sendWS({ type: "MOVE", data: { tg_id: TG_ID, round_id: currentRoundId, move: hand.toLowerCase(), ts: Date.now() } });
  } else {
    setDecision("No server connection — waiting…");
  }
}

function restartGame() {
  if (appState !== APP_STATE.MATCHED) return;
  sendWS({ type: "READY", data: { room: ROOM } });
}

// ===== HELPERS =====
function cap(s) { return (s || "").charAt(0).toUpperCase() + (s || "").slice(1).toLowerCase(); }

// ===== INIT =====
function init() {
  // Вешаем кнопки
  if (startBtn) startBtn.addEventListener("click", beginMatchmaking);
  if (cancelBtn) cancelBtn.addEventListener("click", cancelMatchmaking);

  // Кулдаун восстановление
  if (!isCooldownActive()) clearCooldown();

  // Стартовый экран
  setAppState(APP_STATE.IDLE);

  // Не подключаемся к WS сразу — ждём «Начать игру»
  // Если нужно автодебагом — дерни beginMatchmaking() тут.
}

init();

// Экспорт в глобал (как у тебя было)
window.userPick = userPick;
window.restartGame = restartGame;
