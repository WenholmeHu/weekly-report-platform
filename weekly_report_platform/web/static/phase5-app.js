const POLL_INTERVAL_MS = 5000;
const RUNNING_STATUSES = ["pending", "running", "stopping"];
const TERMINAL_STATUSES = ["completed", "failed", "stopped"];

const STATUS_LABELS = {
  idle: "未开始",
  pending: "准备中",
  running: "执行中",
  stopping: "停止中",
  completed: "已完成",
  failed: "执行失败",
  stopped: "已停止",
  error: "状态获取失败",
  unknown: "未知",
};

const ITEM_STATUS_LABELS = {
  queued: "等待处理",
  analyzing: "分析中",
  syncing: "写入中",
  synced: "已完成",
  skipped: "已跳过",
  failed: "失败",
};

const taskForm = document.querySelector("#task-form");
const submitButton = document.querySelector("#submit-button");
const stopTaskButton = document.querySelector("#stop-task-button");
const runIdValue = document.querySelector("#run-id-value");
const taskStatusValue = document.querySelector("#task-status-value");
const taskStatusNote = document.querySelector("#task-status-note");
const messageBanner = document.querySelector("#message-banner");
const statusCard = document.querySelector(".status-card-primary");
const mailItemsList = document.querySelector("#mail-items-list");
const mailItemsEmpty = document.querySelector("#mail-items-empty");

const summaryFields = {
  total_emails: document.querySelector('[data-field="total_emails"]'),
  processed_emails: document.querySelector('[data-field="processed_emails"]'),
  synced_count: document.querySelector('[data-field="synced_count"]'),
  skipped_count: document.querySelector('[data-field="skipped_count"]'),
  failed_count: document.querySelector('[data-field="failed_count"]'),
};

let pollTimer = null;
let taskBusy = false;
let stopBusy = false;
let taskLocked = false;

function translateStatus(status) {
  return STATUS_LABELS[status] || STATUS_LABELS.unknown;
}

function translateItemStatus(status) {
  return ITEM_STATUS_LABELS[status] || "处理中";
}

function setBanner(message, tone = "") {
  messageBanner.textContent = message;
  messageBanner.className = "message-banner";
  if (statusCard) {
    statusCard.className = "status-card status-card-primary";
  }
  if (tone) {
    messageBanner.classList.add(`is-${tone}`);
    if (statusCard) {
      statusCard.classList.add(`is-${tone}`);
    }
  }
}

function setStatusNote(message) {
  taskStatusNote.textContent = message;
}

function resetSummary() {
  Object.values(summaryFields).forEach((node) => {
    node.textContent = "0";
  });
}

function renderMailItems(items) {
  mailItemsList.innerHTML = "";
  if (!Array.isArray(items) || items.length === 0) {
    mailItemsEmpty.hidden = false;
    return;
  }

  mailItemsEmpty.hidden = true;
  items.forEach((item) => {
    const row = document.createElement("article");
    row.className = "mail-item-row";

    const subject = document.createElement("strong");
    subject.className = "mail-item-subject";
    subject.textContent = item.subject || "未命名邮件";

    const status = document.createElement("span");
    status.className = `mail-item-status is-${item.status || "queued"}`;
    status.textContent = translateItemStatus(item.status || "queued");
    row.append(subject, status);

    if (item.reason) {
      const reason = document.createElement("p");
      reason.className = "mail-item-reason";
      reason.textContent = item.reason;
      row.append(reason);
    }

    mailItemsList.append(row);
  });
}

function syncLockState() {
  const taskFields = taskForm.querySelectorAll("input");
  taskFields.forEach((node) => {
    node.disabled = taskLocked || taskBusy;
  });

  submitButton.disabled = taskLocked || taskBusy;
  submitButton.textContent = taskBusy ? "启动中" : "开始抽取";
  stopTaskButton.disabled = stopBusy || !taskLocked;
  stopTaskButton.textContent = stopBusy ? "停止中" : "停止当前任务";
}

function setTaskLocked(isLocked) {
  taskLocked = isLocked;
  syncLockState();
}

function stopPolling() {
  if (pollTimer !== null) {
    window.clearTimeout(pollTimer);
    pollTimer = null;
  }
}

function renderTask(task) {
  const status = task.status || "unknown";
  runIdValue.textContent = task.run_id || "-";
  runIdValue.title = task.run_id || "-";
  taskStatusValue.textContent = translateStatus(status);

  const summary = task.summary || {};
  Object.entries(summaryFields).forEach(([key, node]) => {
    node.textContent = String(summary[key] ?? 0);
  });

  renderMailItems(task.items || []);
  setTaskLocked(RUNNING_STATUSES.includes(status));

  if (status === "stopped") {
    setStatusNote("任务已停止");
    setBanner("任务已停止", "conflict");
    stopPolling();
    return;
  }

  if (status === "stopping") {
    setStatusNote("正在停止");
    setBanner("任务正在停止", "conflict");
    return;
  }

  if (TERMINAL_STATUSES.includes(status)) {
    const tone = status === "completed" ? "completed" : "failed";
    setStatusNote(status === "completed" ? "任务处理结束" : "任务处理失败");
    setBanner(status === "completed" ? "任务已完成" : "任务处理失败", tone);
    stopPolling();
    return;
  }

  setStatusNote("任务进行中");
  setBanner("页面正在刷新进度", "running");
}

function renderIdleTask() {
  runIdValue.textContent = "-";
  runIdValue.title = "-";
  taskStatusValue.textContent = translateStatus("idle");
  setStatusNote("当前没有运行中的任务");
  setBanner("等待执行", "");
  renderMailItems([]);
  resetSummary();
  setTaskLocked(false);
}

async function fetchJson(url, options = undefined) {
  const response = await fetch(url, options);
  const data = await response.json();
  return { ok: response.ok, status: response.status, data };
}

async function fetchTask(runId) {
  const result = await fetchJson(`/api/tasks/${runId}`);
  if (!result.ok) {
    throw new Error(`failed to fetch task status: ${result.status}`);
  }
  return result.data;
}

async function pollTask(runId) {
  stopPolling();
  try {
    const task = await fetchTask(runId);
    renderTask(task);
    if (RUNNING_STATUSES.includes(task.status)) {
      pollTimer = window.setTimeout(() => {
        pollTask(runId);
      }, POLL_INTERVAL_MS);
    }
  } catch (error) {
    setStatusNote("状态获取失败");
    setBanner("暂时无法刷新进度", "failed");
    taskStatusValue.textContent = translateStatus("error");
  }
}

function buildTaskPayload(formData) {
  const rawMaxEmails = String(formData.get("max_emails") || "").trim();
  return {
    start_date: String(formData.get("start_date") || "").trim(),
    subject_keyword: String(formData.get("subject_keyword") || "").trim(),
    force_refresh: formData.get("force_refresh") === "on",
    max_emails: rawMaxEmails ? Number(rawMaxEmails) : null,
  };
}

taskForm.addEventListener("submit", async (event) => {
  event.preventDefault();
  if (taskLocked) {
    setBanner("当前已有任务在运行", "conflict");
    return;
  }

  const payload = buildTaskPayload(new FormData(taskForm));
  taskBusy = true;
  syncLockState();
  stopPolling();
  resetSummary();
  renderMailItems([]);
  runIdValue.textContent = "-";
  runIdValue.title = "-";
  taskStatusValue.textContent = translateStatus("pending");
  setStatusNote("正在提交任务");
  setBanner("正在按本次参数启动抽取", "running");

  try {
    const result = await fetchJson("/api/tasks", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    });

    if (result.ok) {
      setBanner("任务已开始", "running");
      await pollTask(result.data.run_id);
      return;
    }

    if (result.status === 409 && result.data.active_task) {
      renderTask(result.data.active_task);
      setBanner("当前已有任务在运行", "conflict");
      await pollTask(result.data.active_task.run_id);
      return;
    }

    setStatusNote("任务创建失败");
    setBanner("任务启动失败", "failed");
    taskStatusValue.textContent = translateStatus("failed");
  } catch (error) {
    setStatusNote("任务创建失败");
    setBanner("任务启动失败", "failed");
    taskStatusValue.textContent = translateStatus("failed");
  } finally {
    taskBusy = false;
    syncLockState();
  }
});

stopTaskButton.addEventListener("click", async () => {
  stopBusy = true;
  syncLockState();
  try {
    const result = await fetchJson("/api/tasks/stop", { method: "POST" });
    if (!result.ok) {
      throw new Error(`failed to stop task: ${result.status}`);
    }
    renderTask(result.data);
    if (result.data.run_id) {
      await pollTask(result.data.run_id);
    }
  } catch (error) {
    setStatusNote("当前没有可停止的任务");
    setBanner("没有运行中的任务", "conflict");
  } finally {
    stopBusy = false;
    syncLockState();
  }
});

renderIdleTask();
