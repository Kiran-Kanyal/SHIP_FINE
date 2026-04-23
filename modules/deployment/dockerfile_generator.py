import os
import re
import ast
import json
from modules.ai.diagnoser import AIDiagnoser
from typing import Optional

# ---------------------------------------------------------------------------
# Deterministic Dockerfile templates for well-known stacks.
# The LLM is only invoked for "unknown" stacks or when a template needs
# patching after a build failure.
# ---------------------------------------------------------------------------
DOCKERFILE_TEMPLATES = {
    "web": """\
FROM nginx:alpine
COPY . /usr/share/nginx/html
RUN printf 'server {\\n    listen 8000;\\n    root /usr/share/nginx/html;\\n    index index.html;\\n    location / { try_files $uri $uri/ /index.html; }\\n}\\n' > /etc/nginx/conf.d/default.conf
EXPOSE 8000
CMD ["nginx", "-g", "daemon off;"]
""",

    # NOTE: requirements.txt is guaranteed to exist before this template is
    # used — _ensure_requirements() writes one if absent.
    "python": """\
FROM python:3.11-slim
WORKDIR /app
ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1
COPY . .
RUN if [ -f requirements.txt ]; then pip install --no-cache-dir -r requirements.txt; fi
EXPOSE 8000
CMD ["python", "app.py"]
""",

    "node": """\
FROM node:20-alpine
WORKDIR /app
COPY package*.json ./
RUN npm ci --omit=dev
COPY . .
ENV HOST=0.0.0.0
ENV PORT=8000
EXPOSE 8000
CMD ["npm", "start"]
""",

    "nextjs": """\
FROM node:20-alpine AS builder
WORKDIR /app
COPY package*.json ./
RUN npm ci
COPY . .
RUN npm run build

FROM node:20-alpine AS runner
WORKDIR /app
ENV NODE_ENV=production
ENV HOSTNAME=0.0.0.0
ENV PORT=3000
COPY --from=builder /app/package.json ./package.json
COPY --from=builder /app/.next ./.next
COPY --from=builder /app/public ./public
COPY --from=builder /app/node_modules ./node_modules
EXPOSE 3000
CMD ["npm", "start"]
""",

    "go": """\
FROM golang:1.22-alpine AS builder
WORKDIR /app
COPY go.mod go.sum ./
RUN go mod download
COPY . .
RUN go build -o /app/server ./...

FROM alpine:3.19
WORKDIR /app
COPY --from=builder /app/server .
EXPOSE 8000
CMD ["./server"]
""",

    "java": """\
FROM eclipse-temurin:21-jdk-alpine AS builder
WORKDIR /app
COPY . .
RUN if [ -f mvnw ]; then chmod +x mvnw && ./mvnw package -DskipTests; \
    elif [ -f pom.xml ]; then mvn package -DskipTests; \
    elif [ -f gradlew ]; then chmod +x gradlew && ./gradlew bootJar -x test; \
    else gradle build -x test; fi

FROM eclipse-temurin:21-jre-alpine
WORKDIR /app
COPY --from=builder /app/target/*.jar app.jar 2>/dev/null || true
COPY --from=builder /app/build/libs/*.jar app.jar 2>/dev/null || true
EXPOSE 8080
CMD ["java", "-jar", "app.jar"]
""",

    "rust": """\
FROM rust:1.78-slim AS builder
WORKDIR /app
COPY . .
RUN cargo build --release

FROM debian:bookworm-slim
WORKDIR /app
COPY --from=builder /app/target/release/* ./
EXPOSE 8000
CMD ["./app"]
""",
}

# Stack-specific hints used when we must call the LLM
STACK_HINTS = {
    "node": {
        "base_image": "node:20-alpine",
        "hints": (
            "Check package.json 'scripts.start' or 'scripts.serve' for the CMD. "
            "Use 'npm ci' not 'npm install' for reproducibility. "
            "If it's a Next.js/Vite/React app that needs a build step, run it "
            "and serve with 'node server.js' or similar."
        ),
    },
    "python": {
        "base_image": "python:3.11-slim",
        "hints": (
            "Check for requirements.txt, pyproject.toml, or Pipfile. "
            "Look for app.py, main.py, run.py, wsgi.py, manage.py (Django), "
            "or __main__.py as the entrypoint. "
            "For Flask/FastAPI prefer 'uvicorn app:app --host 0.0.0.0 --port 8000'."
        ),
    },
    "go": {
        "base_image": "golang:1.22-alpine AS builder",
        "hints": (
            "Use a multi-stage build: build in golang:alpine, copy binary to "
            "scratch or alpine. go mod download then go build -o /app ./..."
        ),
    },
    "java": {
        "base_image": "eclipse-temurin:21-jdk-alpine AS builder",
        "hints": (
            "If pom.xml exists use 'mvn package -DskipTests'. "
            "If build.gradle exists use './gradlew bootJar'. "
            "Multi-stage: build in JDK image, run in JRE image. "
            "CMD java -jar target/*.jar or build/libs/*.jar"
        ),
    },
    "rust": {
        "base_image": "rust:1.78-slim AS builder",
        "hints": (
            "cargo build --release, then copy binary to debian:slim. "
            "Infer binary name from Cargo.toml [package].name."
        ),
    },
    "web": {
        "base_image": "nginx:alpine",
        "hints": (
            "Static site. COPY . /usr/share/nginx/html. "
            "Expose port 8000. CMD nginx -g 'daemon off;'"
        ),
    },
}

DEFAULT_HINT = {
    "base_image": "ubuntu:22.04",
    "hints": "Inspect all config files carefully to determine the runtime and entrypoint.",
}

# stdlib modules we should never add to requirements.txt
_STDLIB: frozenset = frozenset({
    "os", "sys", "re", "io", "json", "math", "time", "datetime", "pathlib",
    "collections", "itertools", "functools", "typing", "abc", "copy", "random",
    "string", "struct", "hashlib", "hmac", "base64", "binascii", "uuid",
    "logging", "warnings", "traceback", "inspect", "threading", "multiprocessing",
    "subprocess", "shutil", "tempfile", "glob", "fnmatch", "stat", "socket",
    "http", "urllib", "email", "html", "xml", "csv", "sqlite3", "pickle",
    "shelve", "gzip", "zipfile", "tarfile", "configparser", "argparse",
    "unittest", "doctest", "pdb", "profile", "timeit", "gc", "weakref",
    "contextlib", "dataclasses", "enum", "queue", "heapq", "bisect",
    "array", "codecs", "unicodedata", "locale", "gettext",
    "platform", "signal", "errno", "ctypes", "ast", "dis", "token",
    "tokenize", "keyword", "builtins", "__future__", "importlib",
})

# Maps bare import name → pip package name (when they differ)
_IMPORT_TO_PACKAGE: dict = {
    "cv2":             "opencv-python-headless",
    "PIL":             "Pillow",
    "sklearn":         "scikit-learn",
    "skimage":         "scikit-image",
    "bs4":             "beautifulsoup4",
    "yaml":            "PyYAML",
    "dotenv":          "python-dotenv",
    "google":          "google-cloud",
    "googleapiclient": "google-api-python-client",
    "jwt":             "PyJWT",
    "Crypto":          "pycryptodome",
    "serial":          "pyserial",
    "usb":             "pyusb",
    "wx":              "wxPython",
    "gi":              "PyGObject",
    "dateutil":        "python-dateutil",
    "attr":            "attrs",
    "pkg_resources":   "setuptools",
    "pyarrow":         "pyarrow",
    "numba":           "numba",
    "xgboost":         "xgboost",
    "lightgbm":        "lightgbm",
    "catboost":        "catboost",
    "transformers":    "transformers",
    "datasets":        "datasets",
    "huggingface_hub": "huggingface-hub",
    "yfinance":        "yfinance",
    "ta":              "ta",
    "mplfinance":      "mplfinance",
    "keras":           "tensorflow",
    "tensorflow":      "tensorflow",
    "torch":           "torch",
    "torchvision":     "torchvision",
    "streamlit":       "streamlit",
    "gradio":          "gradio",
    "fastapi":         "fastapi",
    "uvicorn":         "uvicorn",
    "flask":           "flask",
    "django":          "django",
    "pandas":          "pandas",
    "numpy":           "numpy",
    "matplotlib":      "matplotlib",
    "seaborn":         "seaborn",
    "plotly":          "plotly",
    "scipy":           "scipy",
    "nltk":            "nltk",
    "spacy":           "spacy",
    "requests":        "requests",
    "aiohttp":         "aiohttp",
    "sqlalchemy":      "sqlalchemy",
    "pydantic":        "pydantic",
}

_PYTHON_ENTRYPOINTS = ("app.py", "main.py", "run.py", "server.py", "wsgi.py", "manage.py")
_VALID_INSTRUCTIONS = ("FROM", "WORKDIR", "COPY", "RUN", "CMD", "ENV", "EXPOSE", "ARG", "LABEL", "USER")


class DockerfileGenerator:
    def __init__(self):
        self.diagnoser = AIDiagnoser()


    def generate(
        self,
        stack_info: dict,
        repo_signals: dict,
        work_dir: str,
        prior_failure: Optional[str] = None, # Change this line
    ) -> str:
        """
        Return a Dockerfile string.

        Strategy
        --------
        1. Known stack + no prior failure → deterministic template (fast, reliable).
        2. Known stack + prior failure    → LLM patches the failing template.
        3. Unknown stack                  → LLM writes from scratch (last resort).
        """
        stack_key = stack_info.get("stack_key", "unknown")

        if stack_key in DOCKERFILE_TEMPLATES and not prior_failure:
            template = DOCKERFILE_TEMPLATES[stack_key]
            if stack_key == "python":
                self._ensure_requirements(work_dir, repo_signals)
                template = self._patch_python_cmd(template, repo_signals, work_dir)
            elif stack_key == "node":
                template = self._patch_node_cmd(template, repo_signals, work_dir)
            return template

        return self._generate_via_llm(stack_info, repo_signals, work_dir, prior_failure)

   

    def _scan_imports(self, work_dir: str, repo_signals: dict) -> set:
        """
        Walk .py files and extract top-level imported package names.
        Returns pip-installable package names (never stdlib).
        Reads the full file — not just the first N bytes.
        """
        all_paths = repo_signals.get("all_paths", [])
        py_files = [p for p in all_paths if p.endswith(".py")]

        raw_imports: set = set()
        for rel_path in py_files[:30]:  # cap for very large repos
            abs_path = os.path.join(work_dir, rel_path)
            if not os.path.isfile(abs_path):
                continue
            try:
                src = open(abs_path, "r", errors="replace").read()
                tree = ast.parse(src)
                for node in ast.walk(tree):
                    if isinstance(node, ast.Import):
                        for alias in node.names:
                            raw_imports.add(alias.name.split(".")[0])
                    elif isinstance(node, ast.ImportFrom) and node.module:
                        raw_imports.add(node.module.split(".")[0])
            except SyntaxError:
                # Graceful fallback: regex scan
                try:
                    src = open(abs_path, "r", errors="replace").read()
                    for m in re.finditer(
                        r"^\s*(?:import|from)\s+([a-zA-Z_][a-zA-Z0-9_]*)",
                        src,
                        re.MULTILINE,
                    ):
                        raw_imports.add(m.group(1))
                except Exception:
                    pass
            except Exception:
                pass

        packages: set = set()
        for name in raw_imports:
            if name in _STDLIB:
                continue
            packages.add(_IMPORT_TO_PACKAGE.get(name, name))

        return packages

    def _ensure_requirements(self, work_dir: str, repo_signals: dict) -> None:
        """
        Guarantee that requirements.txt exists and contains every package
        imported by the project.  Missing packages are appended; existing
        entries are never modified.
        """
        req_path = os.path.join(work_dir, "requirements.txt")

        existing_packages: set = set()
        if os.path.isfile(req_path):
            try:
                for raw_line in open(req_path, "r", errors="replace").read().splitlines():
                    line = raw_line.strip()
                    if line and not line.startswith(("#", "-")):
                        # strip version specifiers: requests>=2.0 → requests
                        pkg = re.split(r"[>=<!~\[;]", line)[0].strip().lower()
                        if pkg:
                            existing_packages.add(pkg)
                            existing_packages.add(pkg.replace("-", "_"))
                            existing_packages.add(pkg.replace("_", "-"))
            except Exception:
                pass

        scanned = self._scan_imports(work_dir, repo_signals)
        print(f"[DockerfileGenerator] Scanned imports: {scanned}")
        print(f"[DockerfileGenerator] Existing packages: {existing_packages}")

        missing = {p for p in scanned if p.lower() not in existing_packages}
        print(f"[DockerfileGenerator] Missing packages to auto-add: {missing}")

        # Always ensure the file exists — the template does `COPY . .` first now,
        # so a missing file won't crash the build, but pip needs something to read.
        if not missing:
            if not os.path.isfile(req_path):
                open(req_path, "w").close()
                print("[DockerfileGenerator] Created empty requirements.txt")
            else:
                print("[DockerfileGenerator] requirements.txt already complete, skipping.")
            return

        print(f"[DockerfileGenerator] Writing to {req_path}")
        with open(req_path, "a") as fh:
            fh.write("\n# Auto-detected by AI Deploy System\n")
            for pkg in sorted(missing):
                fh.write(pkg + "\n")

        print(f"[DockerfileGenerator] requirements.txt contents:\n{open(req_path).read()}")

   

    def _detect_python_framework(self, work_dir: str, entrypoint: str) -> str:
        """
        Read the *entire* source file and detect which web framework it uses.
        Returns one of: 'streamlit' | 'gradio' | 'fastapi' | 'flask' | 'django' | 'plain'
        """
        fpath = os.path.join(work_dir, entrypoint)
        try:
            src = open(fpath, "r", errors="replace").read().lower()
        except Exception:
            return "plain"

        # Order matters: check more specific frameworks first
        checks = (
            ("streamlit", ("import streamlit", "from streamlit")),
            ("gradio",    ("import gradio",    "from gradio")),
            ("fastapi",   ("import fastapi",   "from fastapi")),
            ("flask",     ("import flask",     "from flask")),
            ("django",    ("import django",    "from django", "django.conf")),
        )
        for framework, needles in checks:
            if any(needle in src for needle in needles):
                return framework
        return "plain"

    def _patch_python_cmd(self, template: str, repo_signals: dict, work_dir: str) -> str:
        """
        Replace the CMD line in the template with the correct command for the
        detected framework / entrypoint.
        """
        all_files = {f.lower() for f in repo_signals.get("all_files", [])}

        entrypoint = None
        for candidate in _PYTHON_ENTRYPOINTS:
            if candidate in all_files and os.path.isfile(os.path.join(work_dir, candidate)):
                entrypoint = candidate
                break

        if not entrypoint:
            return template.replace(
                'CMD ["python", "app.py"]',
                'CMD ["python", "-m", "http.server", "8000"]'
            )

        framework = self._detect_python_framework(work_dir, entrypoint)
        lines = template.splitlines()

        cmd_map = {
            "streamlit": (
                f'CMD ["streamlit", "run", "{entrypoint}", '
                '"--server.port=8000", "--server.address=0.0.0.0"]'
            ),
            "gradio": f'CMD ["python", "{entrypoint}"]',
            "fastapi": (
                f'CMD ["uvicorn", "{entrypoint.replace(".py", "")}:app", '
                '"--host", "0.0.0.0", "--port", "8000"]'
            ),
            "flask": f'CMD ["python", "{entrypoint}"]',
            "django": 'CMD ["python", "manage.py", "runserver", "0.0.0.0:8000"]',
            "plain": f'CMD ["python", "{entrypoint}"]',
        }

        # manage.py always uses the Django runner regardless of detected framework
        if entrypoint == "manage.py":
            cmd = cmd_map["django"]
        else:
            cmd = cmd_map.get(framework, f'CMD ["python", "{entrypoint}"]')

        patched = [cmd if line.startswith("CMD") else line for line in lines]
        return "\n".join(patched) + "\n"

  

    def _patch_node_cmd(self, template: str, repo_signals: dict, work_dir: str) -> str:
        """
        Patch the Node template based on package.json analysis:
        - Next.js / Vite / CRA → add build step + correct serve command
        - TypeScript            → compile then run dist output
        - No 'start' script    → fall back to node <entrypoint>
        """
        pkg_path = os.path.join(work_dir, "package.json")
        if not os.path.isfile(pkg_path):
            return template

        try:
            with open(pkg_path, "r", errors="replace") as fh:
                pkg = json.load(fh)
        except (json.JSONDecodeError, OSError):
            return template

        scripts = pkg.get("scripts", {})
        deps = {**pkg.get("dependencies", {}), **pkg.get("devDependencies", {})}
        lines = template.splitlines()

        is_typescript = "typescript" in deps or os.path.isfile(
            os.path.join(work_dir, "tsconfig.json")
        )
        is_nextjs = "next" in deps
        is_vite = "vite" in deps
        is_cra = "react-scripts" in deps

        def _inject_build(lines: list) -> list:
            """Insert 'RUN npm run build' right after the first 'RUN npm ...' line."""
            result = []
            inserted = False
            for line in lines:
                result.append(line)
                if not inserted and line.strip().startswith("RUN npm"):
                    result.append("RUN npm run build")
                    inserted = True
            return result

        def _replace_cmd(lines: list, new_cmd: str) -> list:
            return [new_cmd if ln.startswith("CMD") else ln for ln in lines if not ln.startswith("CMD")]

        if is_nextjs:
            patched = _inject_build(lines)
            patched = [ln for ln in patched if not ln.startswith(("CMD", "ENV HOST", "ENV PORT", "ENV HOSTNAME"))]
            patched += [
                "ENV HOSTNAME=0.0.0.0",
                "ENV PORT=3000",
                'CMD ["npm", "start"]',
            ]
            return "\n".join(patched) + "\n"

        if is_vite or is_cra:
            out_dir = "dist" if is_vite else "build"
            patched = _inject_build(lines)
            patched = [ln for ln in patched if not ln.startswith("CMD")]
            patched.append(f'CMD ["npx", "serve", "-s", "{out_dir}", "-l", "8000"]')
            return "\n".join(patched) + "\n"

        if is_typescript and "build" in scripts:
            patched = _inject_build(lines)
            patched = [ln for ln in patched if not ln.startswith("CMD")]
            # Resolve dist entry after build (may not exist pre-build, guess)
            dist_candidates = [
                "dist/index.js", "dist/app.js", "dist/server.js", "build/index.js",
            ]
            dist_entry = next(
                (c for c in dist_candidates if os.path.isfile(os.path.join(work_dir, c))),
                None,
            )
            if dist_entry:
                patched.append(f'CMD ["node", "{dist_entry}"]')
            elif "start" in scripts:
                patched.append('CMD ["npm", "start"]')
            else:
                patched.append('CMD ["node", "dist/index.js"]')
            return "\n".join(patched) + "\n"

        if "start" not in scripts:
            for entry in ("index.js", "app.js", "server.js", "main.js"):
                if os.path.isfile(os.path.join(work_dir, entry)):
                    patched = [ln for ln in lines if not ln.startswith("CMD")]
                    patched.append(f'CMD ["node", "{entry}"]')
                    return "\n".join(patched) + "\n"

        return template

  

    def _generate_via_llm(
        self,
        stack_info: dict,
        repo_signals: dict,
        work_dir: str,
        prior_failure: Optional[str], # Change this line
    ) -> str:
        stack_key = stack_info.get("stack_key", "unknown")
        hint = STACK_HINTS.get(stack_key, DEFAULT_HINT)

        context = {
            "stack_key": stack_key,
            "suggested_base_image": hint["base_image"],
            "stack_hints": hint["hints"],
            "config_files_found": repo_signals.get("config_files", []),
            "dominant_extensions": self._top_extensions(repo_signals),
            "all_files_sample": repo_signals.get("all_files", [])[:40],
            "folders": repo_signals.get("folders", []),
        }

        key_file_contents = self._read_key_files(
            work_dir, repo_signals.get("config_files", [])
        )
        if key_file_contents:
            context["key_file_contents"] = key_file_contents

        base_template = DOCKERFILE_TEMPLATES.get(stack_key, "")
        template_section = (
            f"\nThe following Dockerfile template was used but failed. "
            f"Fix ONLY what is broken:\n"
            f"--- BASE TEMPLATE ---\n{base_template}\n--- END TEMPLATE ---\n"
            if base_template and prior_failure
            else ""
        )

        failure_section = (
            f"\nPrevious build error (fix this):\n"
            f"--- BUILD ERROR ---\n{prior_failure[:2000]}\n--- END ERROR ---\n"
            if prior_failure
            else ""
        )

        prompt = f"""\
You are a DevOps expert. Generate a production-ready Dockerfile for the repository described below.

REPOSITORY SIGNALS:
{json.dumps(context, indent=2)}
{template_section}{failure_section}
STRICT REQUIREMENTS:
- Output ONLY raw Dockerfile syntax. Zero markdown fences, zero prose, zero explanation.
- Every line must be a valid Dockerfile instruction (FROM, RUN, COPY, CMD, ENV, EXPOSE, ARG, LABEL, USER) or a comment starting with #.
- CMD must use JSON array format: CMD ["executable", "arg1", "arg2"]
- Do NOT embed shell config, nginx server blocks, or non-Dockerfile content inside CMD.
- nginx config goes in a separate RUN printf/echo — never inside CMD.
- Use the suggested base image.
- The application must bind to 0.0.0.0 (not 127.0.0.1 / localhost).

Dockerfile:"""

        raw = self.diagnoser.generate_raw(prompt)
        return self._clean_dockerfile(raw)

   

    def _top_extensions(self, repo_signals: dict) -> list:
        exts = repo_signals.get("file_extensions", {})
        return [ext for ext, _ in sorted(exts.items(), key=lambda x: x[1], reverse=True)[:5]]

    def _read_key_files(self, work_dir: str, config_files: list) -> dict:
        priority = [
            "package.json", "pyproject.toml", "requirements.txt",
            "go.mod", "pom.xml", "cargo.toml", "procfile",
        ]
        contents: dict = {}
        for fname in priority:
            if fname in config_files:
                fpath = os.path.join(work_dir, fname)
                if os.path.isfile(fpath):
                    try:
                        with open(fpath, "r", errors="replace") as fh:
                            contents[fname] = fh.read(1500)
                    except OSError:
                        pass
        return contents

    def _clean_dockerfile(self, raw: str) -> str:
        """Strip markdown fences and leading junk; return valid Dockerfile text."""
        # Remove ```dockerfile / ```  fences
        cleaned = re.sub(r"```[a-z]*\n?", "", raw).rstrip()

        lines = cleaned.splitlines()
        for i, line in enumerate(lines):
            if line.strip().upper().startswith(_VALID_INSTRUCTIONS):
                return "\n".join(lines[i:]).strip()

        return ""  # Could not extract any valid Dockerfile content