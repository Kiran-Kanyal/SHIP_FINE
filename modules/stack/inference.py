import json
import os
from typing import Dict, List, Tuple

from utils.logger import get_logger
from modules.stack.command_generator import CommandGenerator

logger = get_logger("StackInference")


class StackInference:
    STACK_ORDER = ["web", "python", "node", "nextjs", "java", "go", "rust"]

    STACK_DISPLAY_NAMES = {
        "web": "Static Web",
        "python": "Python",
        "node": "Node.js",
        "nextjs": "Next.js",
        "java": "Java",
        "go": "Go",
        "rust": "Rust",
        "unknown": "Unknown"
    }

    CONFIG_WEIGHTS = {
        "python": {
            "requirements.txt": 6,
            "pyproject.toml": 6,
            "setup.py": 5,
            "setup.cfg": 4,
            "pipfile": 5,
            "poetry.lock": 4,
            "environment.yml": 3,
            "environment.yaml": 3
        },
        "node": {
            "package.json": 7,
            "package-lock.json": 3,
            "yarn.lock": 3,
            "pnpm-lock.yaml": 3,
            "npm-shrinkwrap.json": 3
        },
        "java": {
            "pom.xml": 7,
            "build.gradle": 7,
            "build.gradle.kts": 7,
            "mvnw": 4,
            "gradlew": 4
        },
        "go": {
            "go.mod": 8,
            "go.sum": 3
        },
        "rust": {
            "cargo.toml": 8,
            "cargo.lock": 3
        }
    }

    EXTENSION_WEIGHTS = {
        "web": {".html": 3, ".css": 2, ".js": 2},
        "python": {".py": 4, ".ipynb": 2, ".pkl": 1},
        "node": {".js": 3, ".mjs": 2, ".cjs": 2, ".ts": 2, ".tsx": 1, ".jsx": 1},
        "java": {".java": 4},
        "go": {".go": 4},
        "rust": {".rs": 4}
    }

    FOLDER_HINT_WEIGHTS = {
        "web": {
            "public": 3,
            "assets": 2,
            "dist": 2,
            "css": 2,
            "js": 2
        },
        "python": {
            "venv": 2,
            ".venv": 2,
            "__pycache__": 2
        },
        "node": {
            "node_modules": 3
        },
        "java": {
            "src": 1
        },
        "go": {},
        "rust": {
            "src": 1
        }
    }

    ENTRYPOINT_HINTS = {
        "python": ["app.py", "main.py", "wsgi.py", "asgi.py", "manage.py"],
        "node": ["index.js", "app.js", "server.js", "main.js"],
        "java": ["main.java", "application.java"],
        "go": ["main.go"],
        "rust": ["main.rs", "lib.rs"]
    }

    CONFIG_NAME_MAP = {
        "python": {"requirements.txt", "pyproject.toml", "setup.py", "pipfile", "poetry.lock"},
        "node": {"package.json"},
        "java": {"pom.xml", "build.gradle", "build.gradle.kts", "mvnw", "gradlew"},
        "go": {"go.mod"},
        "rust": {"cargo.toml"}
    }

    def _normalize_signals(self, signals: dict) -> dict:
        return {
            "file_extensions": {str(k).lower(): int(v) for k, v in (signals.get("file_extensions") or {}).items()},
            "config_files": {str(x).lower() for x in (signals.get("config_files") or [])},
            "folders": {str(x).lower() for x in (signals.get("folders") or [])},
            "all_files": {str(x).lower() for x in (signals.get("all_files") or [])},
            "all_paths": [str(p).replace("\\", "/") for p in (signals.get("all_paths") or [])],
            "config_paths": [str(p).replace("\\", "/") for p in (signals.get("config_paths") or [])],
        }

    def _add_score(self, scores: dict, reasons: dict, stack: str, points: int, reason: str) -> None:
        if points <= 0:
            return
        scores[stack] += points
        reasons[stack].append(f"+{points}: {reason}")

    def _score_signals(self, normalized: dict) -> Tuple[Dict[str, int], Dict[str, List[str]]]:
        scores = {s: 0 for s in self.STACK_ORDER}
        reasons = {s: [] for s in self.STACK_ORDER}

        file_extensions = normalized["file_extensions"]
        config_files = normalized["config_files"]
        folders = normalized["folders"]
        all_files = normalized["all_files"]
        all_paths = [p.lower() for p in normalized["all_paths"]]

        for stack, ext_map in self.EXTENSION_WEIGHTS.items():
            for ext, base_weight in ext_map.items():
                count = file_extensions.get(ext, 0)
                if count <= 0:
                    continue
                magnitude_bonus = min(2, count // 20)
                self._add_score(scores, reasons, stack, base_weight + magnitude_bonus, f"found extension {ext} ({count} file(s))")

        for stack, config_map in self.CONFIG_WEIGHTS.items():
            for config_name, weight in config_map.items():
                if config_name in config_files:
                    self._add_score(scores, reasons, stack, weight, f"found config file {config_name}")

        for stack, folder_map in self.FOLDER_HINT_WEIGHTS.items():
            for folder_name, weight in folder_map.items():
                if folder_name in folders:
                    self._add_score(scores, reasons, stack, weight, f"found folder {folder_name}")

        for stack, entry_candidates in self.ENTRYPOINT_HINTS.items():
            for filename in entry_candidates:
                if filename in all_files:
                    self._add_score(scores, reasons, stack, 2, f"found entrypoint-like file {filename}")

        if any(path.startswith("src/main/java") for path in all_paths):
            self._add_score(scores, reasons, "java", 3, "found src/main/java convention")

        if any(path.endswith("/main.go") or path == "main.go" for path in all_paths):
            self._add_score(scores, reasons, "go", 3, "found main.go")

        if any(path.endswith("/src/main.rs") or path == "src/main.rs" for path in all_paths):
            self._add_score(scores, reasons, "rust", 3, "found src/main.rs")

        if any(path.endswith("/manage.py") or path == "manage.py" for path in all_paths):
            self._add_score(scores, reasons, "python", 2, "found Django manage.py")

        return scores, reasons

    def _confidence_label(self, top_score: int, second_score: int) -> str:
        margin = top_score - second_score
        if top_score >= 10 and margin >= 4:
            return "high"
        if top_score >= 6 and margin >= 2:
            return "medium"
        return "low"

    def _select_stack(self, scores: Dict[str, int]) -> dict:
        ranking = sorted(scores.items(), key=lambda x: x[1], reverse=True)
        top_stack, top_score = ranking[0]
        second_stack, second_score = ranking[1]

        if top_score < 3:
            return {
                "stack_key": "unknown",
                "confidence_score": top_score,
                "confidence_ratio": 0.0,
                "confidence_label": "low",
                "ambiguous_candidates": []
            }

        if (top_score - second_score) <= 1:
            return {
                "stack_key": "unknown",
                "confidence_score": top_score,
                "confidence_ratio": round(top_score / (top_score + second_score + 1), 3),
                "confidence_label": "low",
                "ambiguous_candidates": [top_stack, second_stack]
            }

        return {
            "stack_key": top_stack,
            "confidence_score": top_score,
            "confidence_ratio": round(top_score / (top_score + second_score + 1), 3),
            "confidence_label": self._confidence_label(top_score, second_score),
            "ambiguous_candidates": []
        }

    def _select_primary_path(self, candidate_paths: List[str]) -> str:
        if not candidate_paths:
            return None

        def priority(path: str):
            normalized = path.replace("\\", "/").lower()
            parts = normalized.split("/")
            depth = normalized.count("/")
            frontend_penalty = 1 if any(p in {"frontend", "client", "web", "ui"} for p in parts) else 0
            test_penalty = 1 if any(p in {"test", "tests", "example", "examples", "docs"} for p in parts) else 0
            return (test_penalty, frontend_penalty, depth, normalized)

        return sorted(candidate_paths, key=priority)[0]

    def _find_project_dir(self, stack_key: str, normalized: dict) -> str:
        config_candidates = []
        target_config_names = self.CONFIG_NAME_MAP.get(stack_key, set())
        for path in normalized.get("config_paths", []):
            if os.path.basename(path).lower() in target_config_names:
                config_candidates.append(path)

        config_path = self._select_primary_path(config_candidates)
        if config_path:
            return os.path.dirname(config_path) or "."

        entry_candidates = []
        entrypoint_names = set(self.ENTRYPOINT_HINTS.get(stack_key, []))
        for path in normalized.get("all_paths", []):
            if os.path.basename(path).lower() in entrypoint_names:
                entry_candidates.append(path)

        entry_path = self._select_primary_path(entry_candidates)
        if entry_path:
            return os.path.dirname(entry_path) or "."

        return "."

    def _is_nextjs(self, work_dir: str) -> bool:
        pj_path = os.path.join(work_dir, "package.json")
        if not os.path.exists(pj_path):
            return False
        try:
            with open(pj_path) as f:
                data = json.load(f)
                deps = {**data.get("dependencies", {}), **data.get("devDependencies", {})}
                return "next" in deps
        except Exception:
            return False

    def infer(self, signals: dict, repo_path: str = None) -> dict:
        logger.info("Running weighted heuristic stack detection")

        normalized = self._normalize_signals(signals)
        scores, reasons = self._score_signals(normalized)
        selection = self._select_stack(scores)

        stack_key = selection["stack_key"]

        if stack_key == "node" and repo_path and self._is_nextjs(repo_path):
            stack_key = "nextjs"
            logger.info("Deep check identified stack as Next.js")

        if stack_key == "unknown":
            logger.warning("Could not confidently detect stack from repository signals")
            return {
                "stack": "Unknown",
                "stack_key": "unknown",
                "confidence": selection["confidence_score"],
                "confidence_ratio": selection["confidence_ratio"],
                "confidence_label": selection["confidence_label"],
                "ambiguous_candidates": selection["ambiguous_candidates"],
                "scores": scores,
                "project_dir": ".",
                "commands": [],
                "start_commands": []
            }

        project_dir = self._find_project_dir(stack_key, normalized)
        stack_info = {
            "stack": self.STACK_DISPLAY_NAMES[stack_key],
            "stack_key": stack_key,
            "confidence": selection["confidence_score"],
            "confidence_ratio": selection["confidence_ratio"],
            "confidence_label": selection["confidence_label"],
            "scores": scores,
            "signals_used": reasons.get(stack_key, []),
            "project_dir": project_dir
        }

        cmd_gen = CommandGenerator()
        stack_info["commands"] = cmd_gen.get_build_commands(stack_info, repo_path)
        stack_info["start_commands"] = cmd_gen.get_start_commands(stack_info, repo_path)

        logger.info(
            f"Detected stack {stack_info['stack']} with confidence={stack_info['confidence']} "
            f"({stack_info['confidence_label']})"
        )
        return stack_info