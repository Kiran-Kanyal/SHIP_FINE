import json
import os
import sys

from utils.logger import get_logger

logger = get_logger("CommandGenerator")


class CommandGenerator:

    def _load_package_json(self, package_json_path: str) -> dict:
        with open(package_json_path, "r", encoding="utf-8") as fp:
            return json.load(fp)

    def get_build_commands(self, stack_info: dict, repo_path: str = None) -> list:
        stack_key = stack_info.get("stack_key", "unknown")
        project_dir = stack_info.get("project_dir", ".")
        work_dir = os.path.normpath(os.path.join(repo_path, project_dir)) if repo_path else None

        if stack_key == "python":
            commands = []
            if work_dir and os.path.isfile(os.path.join(work_dir, "requirements.txt")):
                commands.append([sys.executable, "-m", "pip", "install", "-r", "requirements.txt"])
            elif work_dir and (
                os.path.isfile(os.path.join(work_dir, "pyproject.toml"))
                or os.path.isfile(os.path.join(work_dir, "setup.py"))
            ):
                commands.append([sys.executable, "-m", "pip", "install", "."])
            commands.append([sys.executable, "-m", "compileall", "."])
            return commands

        if stack_key == "node":
            commands = [["npm", "install", "--no-audit", "--no-fund"]]
            if work_dir:
                package_json_path = os.path.join(work_dir, "package.json")
                if os.path.isfile(package_json_path):
                    try:
                        package_data = self._load_package_json(package_json_path)
                        scripts = package_data.get("scripts", {})
                        if "build" in scripts:
                            commands.append(["npm", "run", "build"])
                    except Exception as e:
                        logger.warning(f"Could not inspect package.json scripts: {e}")
            return commands

        if stack_key == "nextjs":
            return [["npm", "install"], ["npm", "run", "build"]]

        if stack_key == "java":
            if work_dir and os.path.isfile(os.path.join(work_dir, "pom.xml")):
                return [["mvn", "-DskipTests", "package"]]
            if work_dir and os.path.isfile(os.path.join(work_dir, "gradlew")):
                return [["./gradlew", "build", "-x", "test"]]
            if work_dir and (
                os.path.isfile(os.path.join(work_dir, "build.gradle"))
                or os.path.isfile(os.path.join(work_dir, "build.gradle.kts"))
            ):
                return [["gradle", "build", "-x", "test"]]
            return []

        if stack_key == "go":
            commands = []
            if work_dir and os.path.isfile(os.path.join(work_dir, "go.mod")):
                commands.append(["go", "mod", "download"])
            commands.append(["go", "build", "./..."])
            return commands

        if stack_key == "rust":
            return [["cargo", "build", "--release"]]

        return []

    def get_start_commands(self, stack_info: dict, repo_path: str = None) -> list:
        stack_key = stack_info.get("stack_key", "unknown")
        project_dir = stack_info.get("project_dir", ".")
        work_dir = os.path.normpath(os.path.join(repo_path, project_dir)) if repo_path else None

        if stack_key == "python":
            candidates = ["app.py", "main.py", "manage.py"]
            if not work_dir:
                return [[sys.executable, "app.py"], [sys.executable, "main.py"]]
            for filename in candidates:
                if os.path.isfile(os.path.join(work_dir, filename)):
                    if filename == "manage.py":
                        return [[sys.executable, "manage.py", "runserver", "0.0.0.0:8000"]]
                    return [[sys.executable, filename]]
            return []

        if stack_key == "node":
            if not work_dir:
                return [["npm", "start"], ["node", "index.js"]]
            package_json_path = os.path.join(work_dir, "package.json")
            if os.path.isfile(package_json_path):
                try:
                    package_data = self._load_package_json(package_json_path)
                    scripts = package_data.get("scripts", {})
                    if "start" in scripts:
                        return [["npm", "start"]]
                    if "dev" in scripts:
                        return [["npm", "run", "dev"]]
                except Exception as e:
                    logger.warning(f"Could not inspect package.json start scripts: {e}")
            for filename in ["index.js", "app.js", "server.js", "main.js"]:
                if os.path.isfile(os.path.join(work_dir, filename)):
                    return [["node", filename]]
            return []

        if stack_key == "nextjs":
            return [["npm", "start"]]

        if stack_key == "go":
            return [["go", "run", "."]]

        if stack_key == "rust":
            return [["cargo", "run", "--release"]]

        if stack_key == "java":
            return [["java", "-jar", "target/app.jar"]]

        return []