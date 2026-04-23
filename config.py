import os

BASE_DIR = os.path.dirname(os.path.abspath(__file__))

STORAGE_DIR = os.path.join(BASE_DIR, "storage")

DEFAULT_CLONE_DIR = os.path.join(STORAGE_DIR, "cloned_repo")

LLM_MODEL = "llama3.2:latest"

MAX_SCAN_DEPTH = 3

LOG_SNIPPET_MAX_CHARS = 4000

