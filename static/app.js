const CONFIG = {
  API_ENDPOINT: "/analyze",
  CHECK_PORT_ENDPOINT: "/check-port",
};

function isValidUrl(str) {
  try {
    const url = new URL(str);
    return url.protocol === "http:" || url.protocol === "https:";
  } catch {
    return false;
  }
}

function extractPort(urlStr) {
  try {
    const u = new URL(urlStr);
    if (u.port) return parseInt(u.port);
    return u.protocol === "https:" ? 443 : 80;
  } catch {
    return 8000;
  }
}

async function analyzeRepo() {
  const el = {
    input: document.getElementById("repoInput"),
    inputWrap: document.getElementById("inputWrap"),
    btn: document.getElementById("analyzeBtn"),
    btnText: document.getElementById("btnText"),
    terminal: document.getElementById("terminal"),
    output: document.getElementById("output"),
    cursorLine: document.getElementById("cursorLine"),
    actionArea: document.getElementById("actionArea"),
    liveLink: document.getElementById("liveLink"),
    diagLink: document.getElementById("diagLink"),
    successState: document.getElementById("successState"),
    failureState: document.getElementById("failureState"),
    progressFill: document.getElementById("progressFill"),
    progressLabel: document.getElementById("termProgress"),
    statusBadge: document.getElementById("terminalStatus"),
  };

  const url = el.input.value.trim();

  if (!url || !isValidUrl(url)) {
    shakeInput(el.inputWrap);
    return;
  }

  el.btn.disabled = true;
  el.btnText.innerHTML = `<span class="spinner"></span> Running...`;
  el.output.textContent = "";
  el.progressFill.style.width = "5%";
  el.progressFill.style.background = "";
  el.progressLabel.textContent = "Starting...";
  el.terminal.classList.remove("hidden");
  el.actionArea.classList.add("hidden");
  el.successState.classList.add("hidden");
  el.failureState.classList.add("hidden");
  el.cursorLine.style.display = "flex";
  el.statusBadge.innerHTML = '<span class="status-spin"></span> RUNNING';
  el.statusBadge.className = "terminal-status-badge";
  el.liveLink.onclick = null;

  const stages = [
    { msg: "▸ Cloning repository…", progress: 10, delay: 800 },
    { msg: "▸ Scanning project structure…", progress: 22, delay: 1800 },
    { msg: "▸ Detecting tech stack…", progress: 34, delay: 2800 },
    { msg: "▸ Generating Dockerfile…", progress: 46, delay: 4000 },
    { msg: "▸ Building Docker image…", progress: 60, delay: 6000 },
    { msg: "▸ Starting container…", progress: 75, delay: 8000 },
    { msg: "▸ Verifying application health…", progress: 88, delay: 10000 },
  ];

  const stageTimers = stages.map(({ msg, progress, delay }) =>
    setTimeout(() => {
      el.output.textContent += msg + "\n";
      el.progressFill.style.width = progress + "%";
      el.progressLabel.textContent = msg.replace(/[▸\n]/g, "").trim();
      const body = el.output.closest(".terminal-body");
      if (body) body.scrollTop = body.scrollHeight;
    }, delay),
  );

  function clearStageTimers() {
    stageTimers.forEach(clearTimeout);
  }

  let data;
  try {
    const response = await fetch(CONFIG.API_ENDPOINT, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ repo_url: url }),
    });
    data = await response.json();
  } catch (err) {
    clearStageTimers();
    el.output.textContent += `\nCannot reach deploy server: ${err.message}\n`;
    setFailed(el, "Cannot reach the deploy server. Is it running?", null, null);
    return;
  }

  clearStageTimers();
  el.cursorLine.style.display = "none";

  if (data.status === "not_deployable") {
    el.output.textContent += `\n${data.title}\n`;
    el.output.textContent += `   ${data.message}\n\n`;
    el.output.textContent += `${data.suggestion}\n`;
    setFailed(el, data.title, null, null);
    return;
  }

  if (data.status === "failed") {
    const diag = data.diagnosis || {};
    el.output.textContent += `\nDEPLOYMENT FAILED\n`;
    if (diag.simple_explanation)
      el.output.textContent += `\n${diag.simple_explanation}\n`;
    if (diag.who_should_fix)
      el.output.textContent += `Owner: ${diag.who_should_fix}\n`;
    if (diag.root_cause)
      el.output.textContent += `Root Cause: ${diag.root_cause}\n`;
    if (diag.suggested_fix)
      el.output.textContent += `Fix: ${diag.suggested_fix}\n`;
    if (data.diagnosis_url)
      el.output.textContent += `\nFull diagnosis: ${data.diagnosis_url}\n`;

    // Build structured diagnosis for the sidebar panel
    const diagData = {
      rootCause:
        diag.root_cause ||
        diag.simple_explanation ||
        "See full diagnosis for details.",
      errorLog: data.raw_build_log || diag.error_log || diag.build_log || "",
      steps: buildFixSteps(diag),
    };

    setFailed(
      el,
      diag.simple_explanation || data.error || "Deployment failed.",
      data.diagnosis_url,
      diagData,
    );
    return;
  }

  if (data.status === "success") {
    const deployedUrl = data.url;
    const stack = data.stack || "";
    const port = extractPort(deployedUrl);

    el.output.textContent += `\n  Stack detected: ${stack}\n`;
    el.output.textContent += `  Container running on port ${port}\n`;
    el.output.textContent += `\nDEPLOYMENT SUCCESSFUL\n`;

    el.progressFill.style.width = "100%";
    el.progressLabel.textContent = "Deployment complete";
    el.statusBadge.innerHTML = "DONE";
    el.statusBadge.className = "terminal-status-badge done";

    el.liveLink.href = deployedUrl;
    el.liveLink.querySelector("span").textContent = "View Live Application";

    el.liveLink.onclick = async (e) => {
      e.preventDefault();
      try {
        const checkResp = await fetch(
          `${CONFIG.CHECK_PORT_ENDPOINT}?port=${port}`,
        );
        const checkData = await checkResp.json();
        if (checkData.alive) {
          window.open(deployedUrl, "_blank");
        } else {
          showToast(
            `Nothing is responding on port ${port}. ` +
              `The app may have crashed — check ${deployedUrl} for the error page.`,
          );
        }
      } catch {
        window.open(deployedUrl, "_blank");
      }
    };

    // Show success state
    el.actionArea.classList.remove("hidden");
    el.successState.classList.remove("hidden");
    el.failureState.classList.add("hidden");

    el.btn.disabled = false;
    el.btnText.innerHTML = `
      <svg width="14" height="14" viewBox="0 0 24 24" fill="none"
           stroke="currentColor" stroke-width="2.5">
        <line x1="5" y1="12" x2="19" y2="12"/>
        <polyline points="12 5 19 12 12 19"/>
      </svg>
      Deploy`;
    return;
  }

  setFailed(el, `Unexpected server response: ${data.status}`, null, null);
}

function buildFixSteps(diag) {
  // If the backend sends explicit steps, we use them
  if (diag.steps && Array.isArray(diag.steps) && diag.steps.length) {
    return diag.steps;
  }

  // Otherwise generate generic steps from what we know
  const steps = [];

  if (
    diag.user_action_items &&
    Array.isArray(diag.user_action_items) &&
    diag.user_action_items.length
  ) {
    diag.user_action_items.forEach((item, idx) => {
      steps.push({
        title: `Repository fix ${idx + 1}`,
        desc: item,
      });
    });
  }

  if (diag.suggested_fix) {
    steps.push({
      title: "Apply the suggested fix",
      desc: diag.suggested_fix,
      code: diag.fix_code || undefined,
    });
  }

  if (
    diag.system_action_items &&
    Array.isArray(diag.system_action_items) &&
    diag.system_action_items.length
  ) {
    steps.push({
      title: "Deployment system issue detected",
      desc: "Some actions are required in the deployment engine/templates:",
      code: diag.system_action_items.join("\n"),
    });
  }

  if (!steps.length) {
    steps.push(
      {
        title: "Open the full diagnosis page",
        desc: 'Click "Full Diagnosis" to view the complete Docker build log with exact error lines highlighted.',
      },
      {
        title: "Identify the failing layer",
        desc: "Look for lines starting with ERROR or #N DONE in the build log — the last such line before failure is your culprit.",
      },
      {
        title: "Commit the fix and retry",
        desc: "Fix the root cause in your repo, push the changes, then hit RETRY DEPLOY to rebuild from scratch.",
      },
    );
  }

  return steps;
}

/**
 * Show the failure state in the action area.
 * @param {object} el       - element refs
 * @param {string} message  - toast message
 * @param {string|null} diagUrl  - URL for "Full Diagnosis" button (served by Flask: /diagnosis/<id>)
 * @param {object|null} diagData - structured data for the AI sidebar
 */
function setFailed(el, message, diagUrl, diagData) {
  el.progressFill.style.width = "100%";
  el.progressFill.style.background = "#ef4444";
  el.progressLabel.textContent = "Deployment failed";
  el.statusBadge.innerHTML = "✗ FAILED";
  el.statusBadge.className = "terminal-status-badge error";
  el.cursorLine.style.display = "none";

  el.btn.disabled = false;
  el.btnText.innerHTML = `
    <svg width="14" height="14" viewBox="0 0 24 24" fill="none"
         stroke="currentColor" stroke-width="2.5">
      <line x1="5" y1="12" x2="19" y2="12"/>
      <polyline points="12 5 19 12 12 19"/>
    </svg>
    Deploy`;

  if (diagUrl) {
    el.diagLink.href = diagUrl;
    el.diagLink.style.display = "inline-flex";
  } else {
    el.diagLink.removeAttribute("href");
    el.diagLink.style.display = "none";
  }

  if (typeof window !== "undefined") {
    window._diagData = diagData || {
      rootCause: message || "Build failed — see full diagnosis.",
      errorLog: "",
      steps: buildFixSteps({}),
    };
  }

  el.actionArea.classList.remove("hidden");
  el.failureState.classList.remove("hidden");
  el.successState.classList.add("hidden");

  const body = el.output.closest(".terminal-body");
  if (body) body.scrollTop = body.scrollHeight;
}

function shakeInput(element) {
  element.classList.remove("shake-anim");
  void element.offsetWidth;
  element.classList.add("shake-anim");
}

function showToast(message) {
  const existing = document.getElementById("errorToast");
  if (existing) existing.remove();

  const toast = document.createElement("div");
  toast.id = "errorToast";
  toast.innerHTML = `
    <span>⚠️</span>
    <span>${message}</span>
    <button onclick="this.parentElement.remove()" style="
      background:transparent;border:none;color:inherit;
      font-size:18px;cursor:pointer;margin-left:8px;opacity:.7;
    ">×</button>
  `;
  toast.style.cssText = `
    position:fixed; bottom:32px; left:50%; transform:translateX(-50%);
    background:#1a0a0a; border:1px solid #ef4444; border-radius:12px;
    color:#fca5a5; font-family:'JetBrains Mono',monospace; font-size:13px;
    padding:14px 20px; display:flex; align-items:center; gap:10px;
    box-shadow:0 8px 32px rgba(239,68,68,0.3); z-index:9999;
    max-width:500px; animation:slideUp .3s ease;
  `;
  document.body.appendChild(toast);
  setTimeout(() => toast?.remove(), 7000);
}

const _style = document.createElement("style");
_style.textContent = `
  @keyframes slideUp {
    from { opacity:0; transform:translateX(-50%) translateY(20px); }
    to   { opacity:1; transform:translateX(-50%) translateY(0); }
  }
  @keyframes shake-anim {
    0%,100% { transform:translateX(0); }
    20%     { transform:translateX(-6px); }
    40%     { transform:translateX(6px); }
    60%     { transform:translateX(-4px); }
    80%     { transform:translateX(4px); }
  }
  .shake-anim { animation: shake-anim 0.4s ease; }
  @keyframes spin { to { transform:rotate(360deg); } }
`;
document.head.appendChild(_style);
