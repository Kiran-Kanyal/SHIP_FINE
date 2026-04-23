
_STAGE_META = {
    "runtime_crash":                ("Runtime Crash", "#ef4444"),
    "port_unreachable":             ("Network Issue", "#f97316"),
    "build_failed":                 ("Build Failed", "#a78bfa"),
    "dockerfile_generation_failed": ("Dockerfile Generation Failed", "#f43f5e"),
    "stack_detection_failed":       ("Stack Detection Failed", "#f59e0b"),
    "entrypoint_missing":           ("Entrypoint Missing", "#eab308"),
    "server_exception":             ("Server Exception", "#fb7185"),
}


_OWNER_META = {
    "user_repo": (
        "User Repository",
        "#34d399",
        "This failure is likely fixable by changing code/config/dependencies in the user repository.",
    ),
    "deploy_system": (
        "Deployment System",
        "#f97316",
        "This likely comes from Dockerfile generation/deployment-engine behavior, not user code alone.",
    ),
    "infrastructure": (
        "Infrastructure",
        "#60a5fa",
        "This appears related to Docker/network/host infrastructure rather than repository code.",
    ),
    "unclear": (
        "Unclear",
        "#a1a1aa",
        "Insufficient signal to confidently attribute ownership; inspect raw logs and retry with more context.",
    ),
}


def _parse_steps(value) -> list:
    if isinstance(value, list):
        return [str(v).strip() for v in value if str(v).strip()]
    if not isinstance(value, str):
        value = str(value or "")

    steps = []
    for line in value.split("\n"):
        for part in line.split("•"):
            cleaned = part.strip().lstrip("-").lstrip("*").strip()
            if cleaned:
                steps.append(cleaned)
    return steps


def _render_numbered_steps(items: list) -> str:
    return "".join(
        f'<li><span class="num">{i + 1}</span><span class="step-text">{_esc(item)}</span></li>'
        for i, item in enumerate(items)
    )


def _stage_subtitle(stage: str) -> str:
    if stage in {"build_failed", "dockerfile_generation_failed", "stack_detection_failed", "entrypoint_missing"}:
        return "Failure happened before app runtime became healthy"
    if stage == "port_unreachable":
        return "Container started, but app port was not reachable"
    if stage == "runtime_crash":
        return "Container/app exited during or after startup"
    return "AI Diagnosis Report"


def _build_error_html(diagnosis: dict, raw_log: str, stage: str, port: int) -> str:
    label, color = _STAGE_META.get(stage, ("Deployment Failed", "#ef4444"))

    simple  = _esc(diagnosis.get("simple_explanation", "The application failed to start."))
    cause   = _esc(diagnosis.get("root_cause",         "Could not determine root cause."))
    owner_key = str(diagnosis.get("who_should_fix", "unclear")).strip().lower()
    owner_label, owner_color, owner_note = _OWNER_META.get(owner_key, _OWNER_META["unclear"])

    user_actions = _parse_steps(diagnosis.get("user_action_items", []))
    system_actions = _parse_steps(diagnosis.get("system_action_items", []))

    if not user_actions:
        user_actions = _parse_steps(diagnosis.get("suggested_fix", "Review the logs and try again."))
    if not user_actions:
        user_actions = ["Review raw logs and identify the exact failing command/layer."]

    user_steps_html = _render_numbered_steps(user_actions)
    system_steps_html = _render_numbered_steps(system_actions)
    stage_sub = _esc(_stage_subtitle(stage))

    log_esc = _esc(raw_log or "(no log captured)")

    system_section_html = ""
    if system_actions:
        system_section_html = f"""
        <div class="section">
            <div class="section-label">⚙️ Deployment System Actions</div>
            <ol class="steps">{system_steps_html}</ol>
        </div>
        """

    return f"""<!doctype html>
        <html lang="en">
        <head>
        <meta charset="UTF-8"/>
        <meta name="viewport" content="width=device-width,initial-scale=1"/>
        <title>Deployment Failed — AI Diagnosis</title>
        <style>
        *,*::before,*::after{{box-sizing:border-box;margin:0;padding:0}}
        body{{
        background:#060a10;
        color:#c9d1d9;
        font-family:-apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif;
        min-height:100vh;
        display:flex;
        align-items:flex-start;
        justify-content:center;
        padding:48px 16px 80px;
        }}
        .card{{
        width:100%;
        max-width:740px;
        background:#0d1117;
        border:1px solid #21262d;
        border-radius:16px;
        overflow:hidden;
        }}
        .header{{
        display:flex;
        justify-content:space-between;
        align-items:center;
        padding:18px 24px;
        border-bottom:1px solid #21262d;
        border-left:4px solid {color};
        }}
        .stage-label{{font-size:11px;font-weight:700;letter-spacing:.1em;text-transform:uppercase;color:{color}}}
        .stage-sub{{font-size:11px;color:#484f58;margin-top:4px}}
        .dot{{width:10px;height:10px;border-radius:50%;background:{color};animation:pulse 2s infinite}}
        @keyframes pulse{{0%,100%{{opacity:1;transform:scale(1)}}50%{{opacity:.3;transform:scale(1.5)}}}}
        .explain{{padding:22px 24px;border-bottom:1px solid #21262d}}
        .badge{{
        display:inline-block;
        background:rgba(125,211,252,.07);
        border:1px solid rgba(125,211,252,.18);
        color:#7dd3fc;
        font-size:11px;
        border-radius:20px;
        padding:2px 10px;
        margin-bottom:14px;
        font-family:"Courier New",monospace;
        }}
        .explain p{{font-size:17px;font-weight:600;color:#f0f6fc;line-height:1.55}}
        .section{{padding:18px 24px;border-bottom:1px solid #21262d}}
        .section-label{{font-size:10px;font-weight:700;letter-spacing:.12em;text-transform:uppercase;color:#484f58;margin-bottom:10px}}
        .section p{{font-size:14px;color:#8b949e;line-height:1.75}}
        .owner-row{{display:flex;align-items:flex-start;gap:10px;line-height:1.65}}
        .owner-pill{{
        display:inline-block;
        font-size:10px;
        text-transform:uppercase;
        letter-spacing:.08em;
        border-radius:999px;
        padding:4px 9px;
        border:1px solid currentColor;
        font-weight:700;
        }}
        ol.steps{{list-style:none;display:flex;flex-direction:column;gap:10px}}
        ol.steps li{{display:flex;align-items:flex-start;gap:12px}}
        .num{{
        min-width:24px;height:24px;
        border-radius:6px;
        background:rgba(52,211,153,.1);
        border:1px solid rgba(52,211,153,.28);
        color:#34d399;
        font-size:11px;font-weight:700;
        display:flex;align-items:center;justify-content:center;
        flex-shrink:0;margin-top:2px;
        font-family:"Courier New",monospace;
        }}
        .step-text{{font-size:14px;color:#cdd9e5;line-height:1.65}}
        .footer{{padding:14px 24px}}
        .toggle-btn{{
        background:transparent;
        border:1px solid #30363d;
        color:#484f58;
        border-radius:8px;
        padding:7px 14px;
        font-size:12px;
        cursor:pointer;
        font-family:inherit;
        transition:border-color .2s,color .2s;
        }}
        .toggle-btn:hover{{border-color:#6e7681;color:#8b949e}}
        .log-drawer{{
            display:none;
            background:#0d1117;
            border-top:1px solid #30363d;
            padding:20px 24px;
            max-height:450px;
            overflow:auto;
        }}
        .log-drawer pre{{
            font-family:"JetBrains Mono","SFMono-Regular",Consolas,monospace;
            font-size:13px;
            font-weight:500;
            color:#e6edf3;
            line-height:1.8;
            white-space:pre-wrap;
            overflow-wrap:anywhere;
            letter-spacing:0.1px;
            text-shadow:none;
        }}
        </style>
        </head>
        <body>
        <div class="card">
        <div class="header">
            <div>
            <div class="stage-label">{label}</div>
            <div class="stage-sub">{stage_sub}</div>
            </div>
            <div class="dot"></div>
        </div>
        <div class="explain">
            <div class="badge">Deployment target: localhost:{port}</div>
            <p>{simple}</p>
        </div>
        <div class="section">
            <div class="section-label">🧭 Ownership</div>
            <div class="owner-row">
              <span class="owner-pill" style="color:{owner_color}">{_esc(owner_label)}</span>
              <p>{_esc(owner_note)}</p>
            </div>
        </div>
        <div class="section">
            <div class="section-label">🔍 Root Cause</div>
            <p>{cause}</p>
        </div>
        <div class="section">
            <div class="section-label">🛠 Recommended Changes in Repository</div>
            <ol class="steps">{user_steps_html}</ol>
        </div>
        {system_section_html}
        <div class="footer">
            <button class="toggle-btn" id="logBtn" onclick="
            var d=document.getElementById('log');
            var open=d.style.display==='block';
            d.style.display=open?'none':'block';
            this.textContent=open?'▼ View Raw Logs':'▲ Hide Raw Logs';
            ">▼ View Raw Logs</button>
        </div>
        <div class="log-drawer" id="log"><pre>{log_esc}</pre></div>
        </div>
        </body>
        </html>"""


def _esc(s: str) -> str:
    s = "" if s is None else str(s)
    return (s.replace("&", "&amp;")
             .replace("<", "&lt;")
             .replace(">", "&gt;")
             .replace('"', "&quot;"))

