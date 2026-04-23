NON_DEPLOYABLE_ONLY_FILES = {"readme.md", "license", "license.md", ".gitignore"}

DEPLOYABLE_EXTENSIONS = {
    ".py", ".js", ".ts", ".jsx", ".tsx", ".mjs",
    ".java", ".go", ".rs", ".rb", ".php", ".cs",
    ".html", ".css",
}

DEPLOYABLE_CONFIG_FILES = {
    "package.json", "requirements.txt", "pyproject.toml", "setup.py",
    "pipfile", "go.mod", "cargo.toml", "pom.xml", "build.gradle",
    "dockerfile", "docker-compose.yml", "procfile",
}


def _check_deployable(signals: dict):
    all_files    = {f.lower() for f in signals.get("all_files", [])}
    extensions   = {ext.lower() for ext in signals.get("file_extensions", {}).keys()}
    config_files = {c.lower() for c in signals.get("config_files", [])}

    if not all_files:
        return {
            "reason": "empty_repo",
            "title": "Repository is empty",
            "message": "The repository has no files. There is nothing to deploy.",
            "suggestion": "Add source code and a dependency manifest to your repo.",
        }

    notebook_files = [f for f in all_files if f.endswith(".ipynb")]
    if notebook_files and not (extensions & DEPLOYABLE_EXTENSIONS - {".ipynb"}):
        return {
            "reason": "notebooks_only",
            "title": "Jupyter Notebook — not directly deployable",
            "message": f"This repo contains {len(notebook_files)} notebook(s) but no web entrypoint.",
            "suggestion": (
                "Wrap it in a web server:\n"
                "• Streamlit: add app.py with streamlit run app.py\n"
                "• Gradio: add a Gradio interface\n"
                "• FastAPI/Flask: add an API that serves predictions"
            ),
        }

    if extensions and not (extensions & DEPLOYABLE_EXTENSIONS) and not (config_files & DEPLOYABLE_CONFIG_FILES):
        return {
            "reason": "no_runnable_code",
            "title": "No runnable application found",
            "message": f"Found only asset files ({', '.join(sorted(extensions)[:5])}) — no source code.",
            "suggestion": "Add source code files and a dependency manifest.",
        }

    return None
