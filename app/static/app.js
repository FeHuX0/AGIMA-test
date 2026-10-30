import { api, ApiError, setSession } from "./api.js";
import { conversations } from "./conversations.js";
import { renderMarkdown } from "./markdown.js";
import { modeInfo } from "./modes.js";

const $ = (id) => document.getElementById(id);
let history = [];
let turns = [];
let conversationId = null;
let pendingPayload = null;
let pendingJobId = null;
let current = null;
let mode = "ask";
let busy = false;
let config = null;
let user = null;
let viewVersion = 0;
let pollController = null;
let olderController = null;
let historyController = null;
let historyRevision = 0;
let retryAction = null;
let failedOperation = null;
let historyRetryAction = null;
let hasOlderTurns = false;
let hasMoreHistory = false;
let toastTimer;
let copyTimer;
let copying = false;
const MAX_POLL_MS = 120000;

function isCurrentView(version, accountId, chatId) {
  return !!user && viewVersion === version && user.id === accountId
    && (chatId === undefined || conversationId === chatId);
}

function showError(error, retry, label = "Попробовать снова ↗") {
  failedOperation = retry;
  const version = viewVersion;
  const accountId = user?.id;
  retryAction = retry ? () => {
    if (isCurrentView(version, accountId)) return retry();
  } : null;
  $("error-message").textContent = error instanceof Error ? error.message : String(error);
  $("retry-button").hidden = !retryAction;
  $("retry-button").textContent = label;
  answerState("error");
}

function toast(message) {
  clearTimeout(toastTimer);
  $("toast").textContent = message;
  $("toast").hidden = false;
  toastTimer = setTimeout(() => { $("toast").hidden = true; }, 4500);
}

function answerState(state) {
  for (const [id, visible] of [
    ["answer-empty", state === "empty"], ["answer-loading", state === "loading"],
    ["error-panel", state === "error"], ["answer-result", state === "result"],
    ["copy-button", state === "result"],
  ]) $(id).hidden = !visible;
  $("answer-panel").setAttribute("aria-busy", String(state === "loading"));
}

function updateForm() {
  $("question-count").textContent = `${$("question").value.length} / 6000`;
  $("context-count").textContent = `${$("context").value.length} / 12000`;
  $("submit-button").disabled = busy || !user || !config?.ready || !!pendingJobId || $("question").value.trim().length < 3;
}

function setBusy(value) {
  busy = value;
  $("question").readOnly = value || !!pendingJobId;
  $("context").readOnly = value || !!pendingJobId;
  document.querySelectorAll("[data-mode], .suggestion-button").forEach(button => { button.disabled = value; });
  for (const id of ["new-conversation", "delete-conversation", "clear-history"]) $(id).disabled = value;
  $("older-turns").disabled = value || !!olderController;
  $("more-history").disabled = value || !!historyController;
  $("clear-history").disabled = value || !history.length;
  $("submit-label").textContent = value ? "Готовим ответ…" : "Спросить AI";
  updateForm();
  renderHistory();
}

function setMode(next) {
  if (busy) return;
  mode = next;
  const info = modeInfo[mode];
  $("breadcrumb-title").textContent = info.label;
  const spark = document.createElement("span");
  spark.className = "spark";
  spark.textContent = "✳";
  $("composer-title").replaceChildren(spark, document.createTextNode(info.title));
  $("question").placeholder = info.placeholder;
  document.querySelectorAll("[data-mode]").forEach(button => {
    button.classList.toggle("active", button.dataset.mode === mode);
    if (button.dataset.mode === mode) button.setAttribute("aria-current", "page");
    else button.removeAttribute("aria-current");
  });
  $("suggestion-list").replaceChildren();
  for (const [symbol, label, question] of info.ideas) {
    const button = document.createElement("button");
    button.type = "button";
    button.className = "suggestion-button";
    const top = document.createElement("span");
    top.className = "suggestion-top";
    const icon = document.createElement("span");
    icon.textContent = symbol;
    const arrow = document.createElement("span");
    arrow.textContent = "↗";
    top.append(icon, arrow);
    button.append(top, document.createTextNode(label));
    button.addEventListener("click", () => {
      if (busy) return;
      $("question").value = question;
      if (mode === "summary") document.querySelector(".context-details").open = true;
      pendingPayload = null;
      updateForm();
      $("question").focus();
    });
    $("suggestion-list").append(button);
  }
  if (mode === "summary") document.querySelector(".context-details").open = true;
}

function renderHistory() {
  $("history-list").replaceChildren();
  $("history-empty").hidden = history.length > 0;
  $("history-count").textContent = String(history.length);
  $("clear-history").disabled = busy || !history.length;
  $("more-history").hidden = !hasMoreHistory;
  for (const item of history) {
    const li = document.createElement("li");
    const button = document.createElement("button");
    button.type = "button";
    button.className = "history-item";
    button.disabled = busy;
    const selected = turns.at(-1)?.id === item.id;
    button.classList.toggle("selected", selected);
    if (selected) button.setAttribute("aria-current", "true");
    const title = document.createElement("span");
    title.className = "history-question";
    title.textContent = item.question;
    const time = document.createElement("time");
    time.className = "history-meta";
    time.dateTime = item.created_at;
    time.textContent = new Intl.DateTimeFormat("ru", { day: "numeric", month: "short", hour: "2-digit", minute: "2-digit" }).format(new Date(item.created_at));
    button.append(title, time);
    button.addEventListener("click", () => { void openConversation(item.conversation_id, item.id); });
    li.append(button);
    $("history-list").append(li);
  }
}

async function refreshHistory(append = false) {
  const accountId = user?.id;
  const version = viewVersion;
  const revision = ++historyRevision;
  historyController?.abort();
  const controller = new AbortController();
  historyController = controller;
  const before = append ? history.at(-1)?.id : null;
  $("more-history").disabled = true;
  try {
    const page = await conversations.history(before, controller.signal);
    if (!isCurrentView(version, accountId) || revision !== historyRevision) return;
    const items = page.items;
    history = append ? [...history, ...items.filter(item => !history.some(old => old.id === item.id))] : items;
    hasMoreHistory = page.has_more;
    historyRetryAction = null;
    $("history-error").hidden = true;
    $("retry-history").hidden = true;
    renderHistory();
  } catch (error) {
    if (!isCurrentView(version, accountId) || revision !== historyRevision) return;
    $("history-error").textContent = error.message;
    $("history-error").hidden = false;
    historyRetryAction = () => refreshHistory(append);
    $("retry-history").hidden = false;
  } finally {
    if (isCurrentView(version, accountId) && revision === historyRevision) {
      historyController = null;
      $("more-history").disabled = busy;
    }
  }
}

function renderTranscript() {
  $("conversation-log").replaceChildren();
  for (const turn of turns.slice(0, -1)) {
    const section = document.createElement("section");
    section.className = "transcript-turn card";
    const question = document.createElement("p");
    question.className = "answer-question";
    question.textContent = turn.question;
    section.append(question);
    if (turn.context) {
      const details = document.createElement("details");
      const summary = document.createElement("summary");
      summary.textContent = "Контекст вопроса";
      const context = document.createElement("p");
      context.className = "turn-context";
      context.textContent = turn.context;
      details.append(summary, context);
      section.append(details);
    }
    const content = document.createElement("div");
    content.className = "markdown";
    if (turn.status === "succeeded") renderMarkdown(turn.answer, content);
    else content.textContent = turn.error_message || "Ответ ещё не готов.";
    section.append(content);
    $("conversation-log").append(section);
  }
  $("older-turns").hidden = !hasOlderTurns;
  $("delete-conversation").hidden = !conversationId;
  $("conversation-label").textContent = conversationId ? "Продолжение диалога" : "Новый диалог";
}

function showAnswer(answer) {
  current = answer;
  clearTimeout(copyTimer);
  $("copy-label").textContent = "Копировать ответ";
  $("answer-question").textContent = answer.question;
  $("answer-source").textContent = `${modeInfo[answer.mode].label} · ${answer.model}`;
  renderMarkdown(answer.answer, $("answer-content"));
  $("truncation-note").hidden = !answer.truncated;
  const seconds = new Intl.NumberFormat("ru", { maximumFractionDigits: 1 }).format(answer.duration_ms / 1000);
  $("answer-meta").textContent = `${answer.model} · ${seconds} с`;
  answerState("result");
}

function stopPolling() {
  viewVersion++;
  pollController?.abort();
  pollController = null;
  olderController?.abort();
  olderController = null;
  historyController?.abort();
  historyController = null;
  historyRevision++;
  retryAction = null;
  historyRetryAction = null;
  failedOperation = null;
  $("retry-history").hidden = true;
  $("pause-waiting").disabled = true;
}

function newConversation() {
  if (busy) return;
  stopPolling();
  conversationId = null;
  current = null;
  pendingPayload = null;
  pendingJobId = null;
  turns = [];
  hasOlderTurns = false;
  $("question").value = "";
  $("context").value = "";
  $("answer-content").replaceChildren();
  renderTranscript();
  renderHistory();
  answerState("empty");
  setBusy(false);
}

async function pollJob(job, version, accountId) {
  const controller = new AbortController();
  pollController = controller;
  if (["pending", "running"].includes(job.status)) pendingJobId = job.id;
  $("pause-waiting").disabled = !pendingJobId;
  const started = performance.now();
  let workerCheckedAt = -Infinity;
  while (["pending", "running"].includes(job.status)) {
    if (!isCurrentView(version, accountId)) return null;
    controller.signal.throwIfAborted();
    if (performance.now() - started >= MAX_POLL_MS) {
      throw new ApiError("Запрос сохранён, но ожидание затянулось. Можно проверить его состояние позже или открыть другой диалог.", "wait_paused", 0);
    }
    if (job.status === "pending" && performance.now() - workerCheckedAt >= 5000) {
      const available = await conversations.workerAvailable(controller.signal);
      if (!isCurrentView(version, accountId)) return null;
      if (!available) {
        throw new ApiError("Обработчик временно недоступен. Запрос сохранён: проверьте его состояние позже или откройте другой диалог.", "worker_unavailable", 503);
      }
      workerCheckedAt = performance.now();
    }
    $("loading-title").textContent = job.status === "pending" ? "Запрос сохранён и ожидает обработки" : "Вектор разбирается в задаче";
    await new Promise(resolve => setTimeout(resolve, 1000));
    if (!isCurrentView(version, accountId)) return null;
    controller.signal.throwIfAborted();
    job = await conversations.request(job.id, controller.signal);
  }
  return isCurrentView(version, accountId) ? job : null;
}

function displayCompletedJob(result) {
  retryAction = null;
  failedOperation = null;
  pendingJobId = null;
  pendingPayload = null;
  if (result.status === "failed") {
    current = null;
    $("question").value = result.question;
    $("context").value = result.context;
    showError(result.error_message, () => sendRequest({
      request_id: crypto.randomUUID(), conversation_id: result.conversation_id,
      question: result.question, context: result.context, mode: result.mode,
    }));
  } else {
    $("question").value = "";
    $("context").value = "";
    showAnswer(result);
  }
}

async function finishJob(job, version, accountId) {
  const result = await pollJob(job, version, accountId);
  if (!result || !isCurrentView(version, accountId)) return;
  const index = turns.findIndex(turn => turn.id === result.id);
  if (index >= 0) turns[index] = result;
  else turns.push(result);
  renderTranscript();
  displayCompletedJob(result);
  await refreshHistory();
}

async function openConversation(id, requestId = null) {
  if (busy) return;
  stopPolling();
  const version = viewVersion;
  const accountId = user?.id;
  let opened = false;
  setBusy(true);
  $("pause-waiting").disabled = true;
  answerState("loading");
  try {
    const data = await conversations.get(id, { through: requestId });
    if (!isCurrentView(version, accountId)) return;
    opened = true;
    conversationId = data.id;
    turns = data.generations;
    hasOlderTurns = data.has_more;
    pendingPayload = null;
    pendingJobId = null;
    renderTranscript();
    const last = turns.at(-1);
    if (last) {
      await finishJob(last, version, accountId);
    } else answerState("empty");
  } catch (error) {
    if (isCurrentView(version, accountId)) {
      if (opened && pendingJobId) {
        const jobId = pendingJobId;
        showError(error, () => resumeRequest(jobId), "Проверить состояние ↗");
      } else showError(error, () => openConversation(id, requestId));
    }
  } finally { if (isCurrentView(version, accountId)) setBusy(false); }
}

async function loadConfig() {
  const version = viewVersion;
  const accountId = user?.id;
  const received = await api("/config");
  if (!isCurrentView(version, accountId)) return;
  config = received;
  $("provider-badge").textContent = config.model;
  $("provider-badge").classList.toggle("live", config.ready);
  $("mode-notice").textContent = config.ready
    ? "Диалог сохраняется в вашем аккаунте. При продолжении модель учитывает историю текущей беседы."
    : "Модель ещё не настроена администратором.";
  $("mode-notice").classList.toggle("error", !config.ready);
  updateForm();
}

async function submit() {
  if (busy || pendingJobId || !user || !config?.ready || !$("question-form").reportValidity()) return;
  const input = { question: $("question").value.trim(), context: $("context").value.trim(), mode, conversation_id: conversationId };
  if (input.question.length < 3) return;
  const fingerprint = JSON.stringify(input);
  if (!pendingPayload || pendingPayload.fingerprint !== fingerprint) {
    pendingPayload = { ...input, request_id: crypto.randomUUID(), fingerprint };
  }
  const { fingerprint: ignored, ...payload } = pendingPayload;
  await sendRequest(payload);
}

async function sendRequest(payload) {
  if (busy || !user) return;
  const version = viewVersion;
  const accountId = user.id;
  let acceptedId = null;
  retryAction = null;
  setBusy(true);
  $("pause-waiting").disabled = true;
  answerState("loading");
  try {
    // Retrying this POST after a lost response reuses the same request_id.
    const job = await conversations.submit(payload);
    if (!isCurrentView(version, accountId)) return;
    acceptedId = job.id;
    conversationId = job.conversation_id;
    pendingJobId = job.id;
    if (!turns.some(turn => turn.id === job.id)) turns.push(job);
    renderTranscript();
    await refreshHistory();
    await finishJob(job, version, accountId);
  } catch (error) {
    if (isCurrentView(version, accountId)) {
      if (acceptedId) showError(error, () => resumeRequest(acceptedId), "Проверить состояние ↗");
      else showError(error, () => sendRequest(payload));
    }
  } finally { if (isCurrentView(version, accountId)) setBusy(false); }
}

async function resumeRequest(id) {
  if (busy || !user) return;
  const version = viewVersion;
  const accountId = user.id;
  retryAction = null;
  setBusy(true);
  $("pause-waiting").disabled = true;
  answerState("loading");
  try {
    const job = await conversations.request(id);
    if (!isCurrentView(version, accountId)) return;
    conversationId = job.conversation_id;
    await finishJob(job, version, accountId);
  } catch (error) {
    if (isCurrentView(version, accountId)) showError(error, () => resumeRequest(id), "Проверить состояние ↗");
  } finally { if (isCurrentView(version, accountId)) setBusy(false); }
}

async function loadOlderMessages() {
  if (busy || olderController || !turns.length || !hasOlderTurns || !user) return;
  const version = viewVersion;
  const accountId = user.id;
  const chatId = conversationId;
  const before = turns[0].id;
  const controller = new AbortController();
  olderController = controller;
  $("older-turns").disabled = true;
  try {
    const data = await conversations.get(chatId, { before, signal: controller.signal });
    if (!isCurrentView(version, accountId, chatId) || olderController !== controller) return;
    const existing = new Set(turns.map(turn => turn.id));
    turns = [...data.generations.filter(turn => !existing.has(turn.id)), ...turns];
    hasOlderTurns = data.has_more;
    renderTranscript();
    if (!busy && !pendingJobId && failedOperation === loadOlderMessages) {
      const question = $("question").value;
      const context = $("context").value;
      displayCompletedJob(turns.at(-1));
      $("question").value = question;
      $("context").value = context;
      updateForm();
    }
  } catch (error) {
    if (isCurrentView(version, accountId, chatId) && !controller.signal.aborted) {
      showError(error, loadOlderMessages);
    }
  } finally {
    if (isCurrentView(version, accountId, chatId) && olderController === controller) {
      olderController = null;
      $("older-turns").disabled = busy;
    }
  }
}

function showLogin() {
  stopPolling();
  user = null;
  config = null;
  history = [];
  turns = [];
  current = null;
  conversationId = null;
  pendingPayload = null;
  pendingJobId = null;
  setSession(null, showLogin);
  $("workspace-view").hidden = true;
  $("login-screen").hidden = false;
  $("question").value = "";
  $("context").value = "";
  $("answer-content").replaceChildren();
  $("answer-question").textContent = "";
  $("conversation-log").replaceChildren();
  $("history-list").replaceChildren();
  $("toast").hidden = true;
  hasMoreHistory = false;
  hasOlderTurns = false;
  answerState("empty");
  setBusy(false);
}

async function enterWorkspace(account) {
  stopPolling();
  user = account;
  const version = viewVersion;
  setSession(account.csrf_token, showLogin);
  $("signed-name").textContent = account.display_name;
  $("password").value = "";
  $("login-error").hidden = true;
  $("login-screen").hidden = true;
  $("workspace-view").hidden = false;
  try {
    await loadConfig();
    if (!isCurrentView(version, account.id)) return;
    await refreshHistory();
    if (!isCurrentView(version, account.id)) return;
    if (history.length) await openConversation(history[0].conversation_id, history[0].id);
    else newConversation();
  } catch (error) {
    if (isCurrentView(version, account.id)) showError(error, () => enterWorkspace(account));
  }
}

$("login-form").addEventListener("submit", async event => {
  event.preventDefault();
  if (!$("login-form").reportValidity()) return;
  $("login-button").disabled = true;
  $("login-error").hidden = true;
  try {
    await enterWorkspace(await api("/auth/login", { method: "POST", body: {
      username: $("username").value.trim(), password: $("password").value,
    } }));
  } catch (error) {
    $("password").value = "";
    $("login-error").textContent = error.message;
    $("login-error").hidden = false;
  } finally { $("login-button").disabled = false; }
});
$("logout-button").addEventListener("click", async () => {
  try { await api("/auth/logout", { method: "POST" }); showLogin(); }
  catch (error) { toast(error.message); }
});
$("question-form").addEventListener("submit", event => { event.preventDefault(); void submit(); });
for (const id of ["question", "context"]) $(id).addEventListener("input", updateForm);
$("question-form").addEventListener("keydown", event => {
  if (event.key === "Enter" && (event.ctrlKey || event.metaKey)) { event.preventDefault(); void submit(); }
});
document.querySelectorAll("[data-mode]").forEach(button => button.addEventListener("click", () => setMode(button.dataset.mode)));
$("new-conversation").addEventListener("click", newConversation);
$("retry-button").addEventListener("click", async () => {
  if (busy || !retryAction) return;
  const retry = retryAction;
  retryAction = null;
  await retry();
});
$("retry-history").addEventListener("click", () => { if (!busy && historyRetryAction) void historyRetryAction(); });
$("more-history").addEventListener("click", () => { if (!busy) void refreshHistory(true); });
$("older-turns").addEventListener("click", () => { void loadOlderMessages(); });
$("pause-waiting").addEventListener("click", () => {
  pollController?.abort(new DOMException("Ожидание остановлено. Запрос сохранён и продолжает обрабатываться. Его состояние можно проверить позже.", "AbortError"));
});
$("delete-conversation").addEventListener("click", async () => {
  if (busy || !conversationId || !window.confirm("Удалить этот диалог без возможности восстановления?")) return;
  try { await conversations.remove(conversationId); newConversation(); await refreshHistory(); }
  catch (error) { toast(error.message); }
});
$("clear-history").addEventListener("click", async () => {
  if (busy || !window.confirm("Удалить все ваши диалоги без возможности восстановления?")) return;
  try { await conversations.removeAll(); newConversation(); await refreshHistory(); toast("Диалоги удалены"); }
  catch (error) { toast(error.message); }
});
$("copy-button").addEventListener("click", async () => {
  if (!current || copying) return;
  const id = current.id;
  const version = viewVersion;
  const accountId = user?.id;
  copying = true;
  try {
    await navigator.clipboard.writeText(current.answer);
    if (isCurrentView(version, accountId) && current?.id === id) {
      $("copy-label").textContent = "Скопировано ✓";
      clearTimeout(copyTimer);
      copyTimer = setTimeout(() => { $("copy-label").textContent = "Копировать ответ"; }, 2500);
    }
  } catch {
    if (!isCurrentView(version, accountId) || current?.id !== id) return;
    const selection = window.getSelection();
    const range = document.createRange();
    range.selectNodeContents($("answer-content"));
    selection.removeAllRanges();
    selection.addRange(range);
    toast("Ответ выделен — нажмите Ctrl+C или ⌘C.");
  } finally { copying = false; }
});
setSession(null, showLogin);
setMode("ask");
renderHistory();
const restoreVersion = viewVersion;
api("/auth/me").then(account => {
  if (viewVersion === restoreVersion && !user) return enterWorkspace(account);
}).catch(error => {
  if (viewVersion !== restoreVersion || user) return;
  showLogin();
  if (error.status !== 401) {
    $("login-error").textContent = error.message;
    $("login-error").hidden = false;
  }
});
