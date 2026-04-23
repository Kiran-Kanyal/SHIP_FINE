import os
import time
import socket
import subprocess
import socketserver
import threading
from http.server import HTTPServer, BaseHTTPRequestHandler
from modules.ai.diagnoser import AIDiagnoser
from modules.deployment.dockerfile_generator import DockerfileGenerator
from modules.deployment.error_page import _build_error_html


APP_PORT       = 8000
POLL_INTERVAL  = 3 # Polling: check every N seconds, for up to MAX seconds total
POLL_MAX_WAIT  = 60   # heavy apps need up to a minute to cold-start
POST_READY_WATCH = 8 # How long to keep watching AFTER port becomes reachable 


class DeploymentExecutor:
    MAX_RETRIES = 2

    def __init__(self):
        self.diagnoser      = AIDiagnoser()
        self.dockerfile_gen = DockerfileGenerator()
        self.active_server  = None
        self._server_lock         = threading.Lock()
        self._current_container   = None  

    def _get_container_name(self):
        return f"deployment_{int(time.time()*1000)}"

    def _build_diagnosis_context(
        self,
        failure_stage: str,
        stack_key: str = "unknown",
        dockerfile_source: str = "generated",
        dockerfile_content: str = "",
        repo_signals: dict = None,
        notes: str = "",
    ) -> dict:
        repo_signals = repo_signals or {}
        context = {
            "failure_stage": failure_stage,
            "stack_key": stack_key,
            "dockerfile_source": dockerfile_source,
            "generated_dockerfile": dockerfile_content or "",
            "config_files": repo_signals.get("config_files", []),
            "file_extensions": repo_signals.get("file_extensions", {}),
            "all_files_sample": repo_signals.get("all_files", [])[:50],
        }
        if notes:
            context["notes"] = notes
        if dockerfile_source == "generated" and failure_stage in {
            "build_failed",
            "dockerfile_generation_failed",
        }:
            context["ownership_hint"] = (
                "Likely deployment-system Dockerfile generation issue unless logs "
                "clearly show user-repo dependency/config/runtime errors."
            )
        return context

    def _diagnose_failure(
        self,
        raw_log: str,
        failure_stage: str,
        stack_key: str = "unknown",
        dockerfile_source: str = "generated",
        dockerfile_content: str = "",
        repo_signals: dict = None,
        notes: str = "",
    ) -> dict:
        context = self._build_diagnosis_context(
            failure_stage=failure_stage,
            stack_key=stack_key,
            dockerfile_source=dockerfile_source,
            dockerfile_content=dockerfile_content,
            repo_signals=repo_signals,
            notes=notes,
        )
        return self.diagnoser.diagnose(raw_log or "", context=context)

    def _docker_build(self, work_dir, image_name):
        """Builds the Docker image from the Dockerfile in work_dir."""
        try:
            print(f"[Executor] Running docker build for {image_name}...")
            
            cmd = ["docker", "build", "--progress=plain", "-t", image_name, work_dir]
            
            # Run the build process
            result = subprocess.run(
                cmd, 
                cwd=work_dir, 
                capture_output=True, 
                text=True
            )
            
            if result.returncode != 0:
                full_log = ((result.stdout or "") + "\n" + (result.stderr or "")).strip()
                print(f"[Executor] Build failed: {full_log[:1200]}")
                return {"status": "failed", "error": full_log}
            
            print(f"[Executor] Build successful: {image_name}")
            return {"status": "success", "image": image_name}
            
        except Exception as e:
            print(f"[Executor] Docker build exception: {str(e)}")
            return {"status": "failed", "error": str(e)}

    def execute(self, stack_info: dict, repo_signals: dict, work_dir: str) -> dict:
        stack_key = stack_info.get("stack_key", "unknown")

        port = 3000 if stack_key == "nextjs" else 8000
        print(f"[Executor] Stack: {stack_key}, Port: {port}, dir: {work_dir}")

        self._teardown(port)

        existing_dockerfile = os.path.join(work_dir, "Dockerfile")
        if os.path.exists(existing_dockerfile):
            print("[Executor] Repo has its own Dockerfile, using it directly.")
            repo_dockerfile = ""
            try:
                with open(existing_dockerfile, "r") as fh:
                    repo_dockerfile = fh.read()
            except Exception:
                pass
            return self._build_and_run(
                work_dir,
                "deployed-app-repo",
                port,
                stack_key=stack_key,
                repo_signals=repo_signals,
                dockerfile_content=repo_dockerfile,
                dockerfile_source="repo",
            )
        
        prior_error = None
        last_failure = None
        last_generated_dockerfile = ""
        for attempt in range(1, self.MAX_RETRIES + 1):
            print(f"[Executor] Generating Dockerfile (attempt {attempt}/{self.MAX_RETRIES})...")
            dockerfile_content = self.dockerfile_gen.generate(
                stack_info=stack_info,
                repo_signals=repo_signals,
                work_dir=work_dir,
                prior_failure=prior_error,
            )
            with open(os.path.join(work_dir, "Dockerfile"), "w") as f:
                f.write(dockerfile_content)
            print(f"[Executor] Generated Dockerfile:\n{dockerfile_content}\n")
            last_generated_dockerfile = dockerfile_content

            result = self._build_and_run(
                work_dir,
                f"deployed-app-{attempt}",
                port,
                stack_key=stack_key,
                repo_signals=repo_signals,
                dockerfile_content=dockerfile_content,
                dockerfile_source="generated",
            )
            if result["status"] == "success":
                return result

            last_failure = result
            prior_error = result.get("raw_build_log", "")
            print(f"[Executor] Failed on attempt {attempt}, retrying with error context...")

        raw_log = (
            (last_failure or {}).get("raw_build_log")
            or prior_error
            or "Docker build failed after retries."
        )
        diagnosis = (last_failure or {}).get("diagnosis") or self._diagnose_failure(
            raw_log,
            failure_stage="dockerfile_generation_failed",
            stack_key=stack_key,
            dockerfile_source="generated",
            dockerfile_content=last_generated_dockerfile,
            repo_signals=repo_signals,
            notes="All generated Dockerfile attempts failed.",
        )

        return {
            "status": "failed",
            "failure_stage": "dockerfile_generation_failed",
            "error": "Docker build failed after Dockerfile generation retries.",
            "raw_build_log": raw_log,
            "diagnosis": diagnosis,
            "generated_dockerfile": last_generated_dockerfile,
            "dockerfile_source": "generated",
            "url": f"http://localhost:{port}",
        }
    
    # Build and run pipeline
    def _build_and_run(
        self,
        work_dir: str,
        image_name: str,
        port: int,
        stack_key: str = "unknown",
        repo_signals: dict = None,
        dockerfile_content: str = "",
        dockerfile_source: str = "generated",
    ) -> dict:
        build_result = self._docker_build(work_dir, image_name)
        if build_result["status"] != "success":
            raw_build_log = build_result.get("error", "")
            diagnosis = self._diagnose_failure(
                raw_build_log,
                failure_stage="build_failed",
                stack_key=stack_key,
                dockerfile_source=dockerfile_source,
                dockerfile_content=dockerfile_content,
                repo_signals=repo_signals,
            )
            return {
                "status": "failed",
                "failure_stage": "build_failed",
                "error": "Docker build failed",
                "raw_build_log": raw_build_log,
                "diagnosis": diagnosis,
                "generated_dockerfile": dockerfile_content,
                "dockerfile_source": dockerfile_source,
            }

        return self._run_and_verify(
            image_name,
            port,
            stack_key=stack_key,
            repo_signals=repo_signals,
            dockerfile_content=dockerfile_content,
            dockerfile_source=dockerfile_source,
        )
    

    def _run_and_verify(
        self,
        image_name: str,
        port: int,
        stack_key: str = "unknown",
        repo_signals: dict = None,
        dockerfile_content: str = "",
        dockerfile_source: str = "generated",
    ) -> dict:

        container_name = self._get_container_name()
        self._current_container = container_name   # save so _teardown can find it later

        run_result = subprocess.run(
            ["docker", "run", "-d",
            "--name", container_name,
            "-p", f"{port}:{port}",
            image_name],
            capture_output=True, text=True,
        )

        if run_result.returncode != 0:
            run_log = ((run_result.stdout or "") + "\n" + (run_result.stderr or "")).strip()
            diag = self._diagnose_failure(
                run_log,
                failure_stage="runtime_crash",
                stack_key=stack_key,
                dockerfile_source=dockerfile_source,
                dockerfile_content=dockerfile_content,
                repo_signals=repo_signals,
            )
            self._serve_error_page(diag, run_log, "runtime_crash", port)
            return {
                "status": "failed",
                "failure_stage": "runtime_crash",
                "raw_build_log": run_log,
                "diagnosis": diag,
                "url": f"http://localhost:{port}",
            }

        print(f"[Executor] Polling port {port} (up to {POLL_MAX_WAIT}s)...")
        deadline = time.time() + POLL_MAX_WAIT

        while time.time() < deadline:
            time.sleep(POLL_INTERVAL)

            still_running = self._is_container_running(container_name)
            logs = self._get_container_logs(container_name)

            if not still_running:
                print("[Executor] Container exited — runtime crash.")
                diag = self._diagnose_failure(
                    logs,
                    failure_stage="runtime_crash",
                    stack_key=stack_key,
                    dockerfile_source=dockerfile_source,
                    dockerfile_content=dockerfile_content,
                    repo_signals=repo_signals,
                )
                self._serve_error_page(diag, logs, "runtime_crash", port)
                return {
                    "status": "failed",
                    "failure_stage": "runtime_crash",
                    "raw_build_log": logs,
                    "diagnosis": diag,
                    "url": f"http://localhost:{port}",
                }

            if self._is_port_reachable(port):
                print(f"[Executor] Port up — watching {POST_READY_WATCH}s for late crashes...")
                for _ in range(POST_READY_WATCH):
                    time.sleep(1)
                    if not self._is_container_running(container_name):
                        logs = self._get_container_logs(container_name)
                        print("[Executor] Container crashed after becoming ready.")
                        diag = self._diagnose_failure(
                            logs,
                            failure_stage="runtime_crash",
                            stack_key=stack_key,
                            dockerfile_source=dockerfile_source,
                            dockerfile_content=dockerfile_content,
                            repo_signals=repo_signals,
                        )
                        self._serve_error_page(diag, logs, "runtime_crash", port)
                        return {
                            "status": "failed",
                            "failure_stage": "runtime_crash",
                            "raw_build_log": logs,
                            "diagnosis": diag,
                            "url": f"http://localhost:{port}",
                        }
                print(f"[Executor] App is live at http://localhost:{port}")
                return {"status": "success", "url": f"http://localhost:{port}"}

        # Timeout
        logs = self._get_container_logs(container_name)
        diag_log = (
            logs +
            f"\n\n[System] Container is running but port {port} did not become "
            "reachable within the timeout. The app may be binding to 127.0.0.1 "
            "instead of 0.0.0.0, or listening on a different port."
        )
        diag = self._diagnose_failure(
            diag_log,
            failure_stage="port_unreachable",
            stack_key=stack_key,
            dockerfile_source=dockerfile_source,
            dockerfile_content=dockerfile_content,
            repo_signals=repo_signals,
        )
        self._serve_error_page(diag, diag_log, "port_unreachable", port)
        return {
            "status": "failed",
            "failure_stage": "port_unreachable",
            "raw_build_log": diag_log,
            "diagnosis": diag,
            "url": f"http://localhost:{port}",
        }

    def _teardown(self, port: int) -> None:
        """Kill previous container and error-page server before a new deploy."""
        print(f"[Executor] Tearing down previous deployment on port {port}...")

        # Kill the specific container we started last time
        if self._current_container:
            subprocess.run(
                ["docker", "rm", "-f", self._current_container],
                capture_output=True,
            )
            self._current_container = None

        # Kill any container still occupying our port 
        result = subprocess.run(
            ["docker", "ps", "-q", "--filter", f"publish={port}"],
            capture_output=True, text=True,
        )
        ids = [i for i in result.stdout.strip().split() if i]
        if ids:
            subprocess.run(["docker", "rm", "-f"] + ids, capture_output=True)
            print(f"[Executor] Force-removed {len(ids)} container(s) holding port {port}.")

        # Stop the Python error-page server if running
        self._stop_error_server()

        # Let the OS release the port binding
        time.sleep(0.8)

    def _stop_error_server(self) -> None:
        with self._server_lock:
            if self.active_server is not None:
                try:
                    self.active_server.shutdown()
                    self.active_server.server_close()
                except Exception:
                    pass
                self.active_server = None
                


    def _serve_error_page(self, diagnosis, raw_log, stage, port):
        self._stop_error_server()

        # Kill any Docker container still holding the port
        result = subprocess.run(
            ["docker", "ps", "-q", "--filter", f"publish={port}"],
            capture_output=True, text=True,
        )
        ids = [i for i in result.stdout.strip().split() if i]
        if ids:
            subprocess.run(["docker", "rm", "-f"] + ids, capture_output=True)

        time.sleep(0.5)

        html = _build_error_html(diagnosis, raw_log, stage, port)

        class _Handler(BaseHTTPRequestHandler):
            def do_GET(self):
                self.send_response(200)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.end_headers()
                self.wfile.write(html.encode("utf-8"))
            def log_message(self, fmt, *args): pass

        class _Server(socketserver.TCPServer):
            allow_reuse_address = True  # MUST be class attr, not set after __init__

        def _try_start_server(current_port):
            try:
                with self._server_lock:
                    self.active_server = _Server(("0.0.0.0", current_port), _Handler)
                print(f"[Executor] Error-page server live on port {current_port}")
                self.active_server.serve_forever()
            except OSError as e:
                print(f"[Executor] Port {current_port} busy ({e}), trying {current_port + 1}")
                _try_start_server(current_port + 1)

        threading.Thread(target=_try_start_server, args=(port,), daemon=True).start()



    def _is_container_running(self, name: str) -> bool:
        r = subprocess.run(
            ["docker", "inspect", "--format", "{{.State.Running}}", name],
            capture_output=True, text=True,
        )
        return r.stdout.strip() == "true"

    def _get_container_logs(self, name: str, tail: int = 120) -> str:
        r = subprocess.run(
            ["docker", "logs", "--tail", str(tail), name],
            capture_output=True, text=True,
        )
        return (r.stdout + "\n" + r.stderr).strip()

    def _is_port_reachable(self, port: int) -> bool:
        try:
            with socket.create_connection(("127.0.0.1", port), timeout=2):
                return True
        except OSError:
            return False
        
    
