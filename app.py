import shutil
import traceback
import socket

from flask import Flask, request, jsonify, render_template, Response, url_for
from modules.deployment.error_page import _build_error_html
import uuid
import time
from flask_cors import CORS

from modules.repository.scanner import RepositoryScanner
from modules.repository.analyzer import RepositoryAnalyzer
from modules.stack.inference import StackInference
from modules.deployment.executor import DeploymentExecutor
from modules.ai.diagnoser import AIDiagnoser
from modules.repository.deployability import _check_deployable



app = Flask(__name__)
CORS(app)
diagnosis_pages = {}
DIAGNOSIS_PAGE_TTL_SECONDS = 30 * 60

scanner     = RepositoryScanner()
analyzer    = RepositoryAnalyzer()
stack_engine = StackInference()
executor    = DeploymentExecutor()


def _extract_port_from_url(url: str, default: int = 8000) -> int:
    try:
        if not url:
            return default
        return int(url.rsplit(":", 1)[-1])
    except Exception:
        return default


def _cleanup_old_diagnosis_pages() -> None:
    now = time.time()
    expired_ids = [
        diagnosis_id
        for diagnosis_id, page in diagnosis_pages.items()
        if now - page.get("created_at", now) > DIAGNOSIS_PAGE_TTL_SECONDS
    ]
    for diagnosis_id in expired_ids:
        diagnosis_pages.pop(diagnosis_id, None)


def _create_diagnosis_url(
    diagnosis: dict,
    raw_log: str,
    stage: str,
    deployed_url: str = "",
) -> str:
    _cleanup_old_diagnosis_pages()

    port = _extract_port_from_url(deployed_url)
    diagnosis_id = uuid.uuid4().hex

    diagnosis_pages[diagnosis_id] = {
        "html": _build_error_html(diagnosis, raw_log, stage, port),
        "created_at": time.time(),
    }

    return url_for("diagnosis_page", diagnosis_id=diagnosis_id)


def _failed_json_response(
    error: str,
    diagnosis: dict,
    raw_log: str = "",
    failure_stage: str = "unknown",
    deployed_url: str = "",
    status_code: int = 200,
):
    raw_log = raw_log or error or "No raw build log captured."
    diagnosis_url = _create_diagnosis_url(
        diagnosis=diagnosis,
        raw_log=raw_log,
        stage=failure_stage,
        deployed_url=deployed_url,
    )

    return jsonify({
        "status": "failed",
        "failure_stage": failure_stage,
        "error": error,
        "url": deployed_url,
        "diagnosis_url": diagnosis_url,
        "diagnosis": diagnosis,
        "raw_build_log": raw_log,
    }), status_code

@app.route("/")
def index():
    return render_template("index.html")


@app.route("/analyze", methods=["POST"])
def analyze_repo():
    data = request.get_json(silent=True)
    if not data:
        return jsonify({"status": "failed", "error": "Request body must be JSON"}), 400

    repo_url = (data.get("repo_url") or "").strip()
    if not repo_url:
        return jsonify({"status": "failed", "error": "repo_url is required"}), 400

    if not repo_url.startswith(("https://", "http://", "git@")):
        return jsonify({"status": "failed", "error": "Invalid Git URL"}), 400

    repo_path = None
    try:
        repo_path = scanner.clone(repo_url)
        signals = analyzer.analyze(repo_path)

        not_deployable = _check_deployable(signals)
        if not_deployable:
            return jsonify({
                "status": "not_deployable",
                **not_deployable,
            }), 200

        stack_info = stack_engine.infer(signals, repo_path)

        if not stack_info or stack_info.get("stack_key") == "unknown":

            diagnoser = AIDiagnoser()

            ai_log = f"""
                Repository stack detection failed.
                Config files: {signals.get('config_files', [])}
                File extensions: {signals.get('file_extensions', {})}
                All files sample: {signals.get('all_files', [])[:50]}
                Scores: {stack_info.get('scores', {}) if stack_info else {}}
            """

            diagnosis = diagnoser.diagnose(ai_log)

            return _failed_json_response(
                error="Unable to detect tech stack",
                diagnosis=diagnosis,
                raw_log=ai_log,
                failure_stage="stack_detection_failed",
            )
        
        if not stack_info.get("start_commands"):
            diagnosis = {
                "simple_explanation": f"Detected stack as {stack_info.get('stack')} but could not find anything to run.",
                "root_cause": "No recognisable entrypoint file found in the repository.",
                "suggested_fix": "Make sure your repo has a runnable entrypoint (e.g. app.py, index.js, main.go etc.)",
            }
            raw_log = f"""
No runnable entrypoint found.
Detected stack: {stack_info.get('stack')}
Stack key: {stack_info.get('stack_key')}
Config files: {signals.get('config_files', [])}
Files sample: {signals.get('all_files', [])[:50]}
"""

            return _failed_json_response(
                error="No runnable entrypoint found",
                diagnosis=diagnosis,
                raw_log=raw_log,
                failure_stage="entrypoint_missing",
            )

        build_result = executor.execute(stack_info, signals, repo_path)

        if build_result.get("status") == "success":
            return jsonify({
                "status":     "success",
                "url":        build_result.get("url", "http://localhost:8000"),
                "stack":      stack_info.get("stack", "Unknown"),
                "stack_key":  stack_info.get("stack_key", "unknown"),
            }), 200
        else:
            failure_stage = build_result.get("failure_stage", "unknown")
            error = build_result.get("error", "Deployment failed")
            deployed_url = build_result.get("url", "")

            diagnosis = build_result.get("diagnosis", {
                "simple_explanation": "Deployment failed.",
                "root_cause": error or "Unknown error",
                "suggested_fix": "Check the raw logs for details.",
            })

            raw_log = (
                build_result.get("raw_build_log")
                or build_result.get("error")
                or "No raw build log captured."
            )

            return _failed_json_response(
                error=error,
                diagnosis=diagnosis,
                raw_log=raw_log,
                failure_stage=failure_stage,
                deployed_url=deployed_url,
            )

    except Exception as e:
        traceback.print_exc()
        diagnosis = {
            "simple_explanation": "An unexpected server error occurred.",
            "root_cause": str(e),
            "suggested_fix": "Check the server logs for the full traceback.",
        }
        return _failed_json_response(
            error=str(e),
            diagnosis=diagnosis,
            raw_log=traceback.format_exc(),
            failure_stage="server_exception",
            status_code=500,
        )

    finally:
        if repo_path:
            shutil.rmtree(repo_path, ignore_errors=True)


@app.route("/diagnosis/<diagnosis_id>")
def diagnosis_page(diagnosis_id):
    page = diagnosis_pages.get(diagnosis_id)
    if not page:
        return Response("Diagnosis page expired or not found.", status=404)

    return Response(page["html"], mimetype="text/html")


@app.route("/check-port")
def check_port():
    port = request.args.get("port", type=int)
    if not port or not (1024 <= port <= 65535):
        return jsonify({"alive": False, "error": "Invalid port"}), 400

    try:
        with socket.create_connection(("127.0.0.1", port), timeout=3):
            return jsonify({"alive": True}), 200
    except OSError:
        return jsonify({"alive": False}), 200


if __name__ == "__main__":
    app.run(debug=True, host="0.0.0.0", port=5001, use_reloader=False)