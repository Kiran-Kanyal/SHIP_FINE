import os
import shutil
from git import Repo
from config import DEFAULT_CLONE_DIR
from utils.logger import get_logger

logger = get_logger("RepositoryScanner")


class RepositoryScanner:

    def clone(self, repo_url: str, target_dir: str = DEFAULT_CLONE_DIR) -> str:
        try:
            parent_dir = os.path.dirname(target_dir)
            if parent_dir:
                os.makedirs(parent_dir, exist_ok=True)

            if os.path.exists(target_dir):
                shutil.rmtree(target_dir)

            logger.info(f"Cloning repository from {repo_url}")
            Repo.clone_from(repo_url, target_dir)
            logger.info("Repository cloned successfully")

            abs_path = os.path.abspath(target_dir)
            logger.info(f"Repository cloned to: {abs_path}")
            
            return abs_path

        except Exception as e:
            logger.error(f"Failed to clone repository: {e}")
            raise