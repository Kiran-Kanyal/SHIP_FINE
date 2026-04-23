import os
from collections import defaultdict
from config import MAX_SCAN_DEPTH
from utils.logger import get_logger

logger = get_logger("RepositoryAnalyzer")

KNOWN_CONFIGS = {
    "package.json",
    "package-lock.json",
    "npm-shrinkwrap.json",
    "yarn.lock",
    "pnpm-lock.yaml",
    "requirements.txt",
    "pyproject.toml",
    "setup.py",
    "setup.cfg",
    "pipfile",
    "poetry.lock",
    "environment.yml",
    "environment.yaml",
    "runtime.txt",
    "pom.xml",
    "build.gradle",
    "build.gradle.kts",
    "gradlew",
    "mvnw",
    "go.mod",
    "go.sum",
    "cargo.toml",
    "cargo.lock",
    "dockerfile",
    "procfile"
}

IGNORED_DIRS = {
    ".git",
    "node_modules",
    "venv",
    ".venv",
    "__pycache__",
    ".ipynb_checkpoints",
    ".idea",
    ".vscode"
}


class RepositoryAnalyzer:

    def analyze(self, repo_path: str) -> dict:
        signals = {
            "file_extensions": defaultdict(int),
            "config_files": set(),
            "config_paths": set(),
            "folders": set(),
            "all_files": set(),
            "all_paths": set()
        }

        for root, dirs, files in os.walk(repo_path, topdown=True):
            dirs[:] = [d for d in dirs if d not in IGNORED_DIRS]  # prune first

            depth = root.replace(repo_path, "").count(os.sep)
            if depth >= MAX_SCAN_DEPTH:  
                dirs[:] = [] 

            for d in dirs:
                signals["folders"].add(d)

            for f in files:
                signals["all_files"].add(f)
                rel_path = os.path.relpath(os.path.join(root, f), repo_path)
                signals["all_paths"].add(rel_path)

                ext = os.path.splitext(f)[1].lower()
                if ext:
                    signals["file_extensions"][ext] += 1

                if f.lower() in KNOWN_CONFIGS:
                    signals["config_files"].add(f.lower())
                    signals["config_paths"].add(rel_path)

        logger.info("Repository signals extracted")

        return {
            "file_extensions": dict(signals["file_extensions"]),
            "config_files": sorted(signals["config_files"]),
            "config_paths": sorted(signals["config_paths"]),
            "folders": sorted(signals["folders"]),
            "all_files": sorted(signals["all_files"]),
            "all_paths": sorted(signals["all_paths"])
        }