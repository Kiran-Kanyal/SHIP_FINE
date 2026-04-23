import re
import json
import ollama
from typing import Optional, Dict, Any, List
from config import LLM_MODEL
from utils.logger import get_logger

logger = get_logger("AIDiagnoser")

MAX_LOG_CHARS = 12000  
MAX_CONTEXT_STR_CHARS = 3000
_ALLOWED_OWNERS = {"user_repo", "deploy_system", "infrastructure", "unclear"}


class AIDiagnoser:

    def _clean_json(self, raw: str) -> str:
        """Strip markdown fences the LLM sneaks in despite instructions."""
        # Remove ```json ... ``` or ``` ... ``` wrappers
        raw = raw.strip()
        raw = re.sub(r"^```(?:json)?\s*", "", raw)
        raw = re.sub(r"\s*```$", "", raw)
        return raw.strip()

    def _truncate_log(self, error_log: str) -> str:
        """Keep the tail of the log — errors are almost always at the end."""
        if len(error_log) <= MAX_LOG_CHARS:
            return error_log
        truncated = error_log[-MAX_LOG_CHARS:]
        first_newline = truncated.find("\n")
        if first_newline > 0:
            truncated = truncated[first_newline + 1:]
        return "[...truncated...]\n" + truncated

    def _truncate_text(self, value: str, max_chars: int = MAX_CONTEXT_STR_CHARS) -> str:
        value = str(value or "")
        if len(value) <= max_chars:
            return value
        return value[:max_chars] + "... [truncated]"

    def _normalize_action_items(self, value: Any) -> List[str]:
        if isinstance(value, list):
            return [str(v).strip() for v in value if str(v).strip()]
        if isinstance(value, str):
            items: List[str] = []
            for line in value.split("\n"):
                cleaned = line.strip().lstrip("-").lstrip("*").strip()
                if cleaned:
                    items.append(cleaned)
            return items
        return []

    def _build_context_payload(self, context: Optional[Dict[str, Any]]) -> Dict[str, Any]:
        if not context:
            return {}

        payload: Dict[str, Any] = {}
        for key, value in context.items():
            if value is None:
                continue
            if isinstance(value, str):
                payload[key] = self._truncate_text(value)
            elif isinstance(value, (list, tuple)):
                payload[key] = [
                    self._truncate_text(item, 240) for item in [str(v) for v in value[:60]]
                ]
            elif isinstance(value, dict):
                trimmed = {}
                for i, (k, v) in enumerate(value.items()):
                    if i >= 60:
                        break
                    trimmed[str(k)] = self._truncate_text(v, 240)
                payload[key] = trimmed
            else:
                payload[key] = self._truncate_text(value, 600)

        return payload

    def _fallback_response(self, error_log: str, context: Optional[Dict[str, Any]] = None) -> dict:
        short_log = (error_log or "").strip()[:500]
        log_lower = (error_log or "").lower()

        who_should_fix = "user_repo"
        user_actions: List[str] = []
        system_actions: List[str] = []

        if context:
            source = str(context.get("dockerfile_source", "")).lower()
            stage = str(context.get("failure_stage", "")).lower()
            if source == "generated" and stage in {"build_failed", "dockerfile_generation_failed"}:
                who_should_fix = "deploy_system"
                system_actions.extend([
                    "Inspect the generated Dockerfile and the exact failing build layer in the raw logs.",
                    "Patch Dockerfile generation rules/templates for the detected stack and retry deployment.",
                ])

        if "could not find a version that satisfies the requirement" in log_lower or "no matching distribution found" in log_lower:
            user_actions.append(
                "Pin compatible dependency versions in requirements.txt/package.json and retry build."
            )

        if "modulenotfounderror" in log_lower:
            user_actions.append(
                "Add the missing Python package to requirements.txt and commit the updated file."
            )

        if "npm err! missing script: start" in log_lower:
            user_actions.append(
                "Add a start script in package.json (for example: \"start\": \"node index.js\") and redeploy."
            )

        if "copy failed" in log_lower or "file not found in build context" in log_lower:
            user_actions.append(
                "Ensure Dockerfile COPY paths match actual repository files and that required files are committed."
            )

        if not user_actions and who_should_fix != "deploy_system":
            user_actions.append(
                "Review the exact failing command in raw logs and update repository dependencies/config accordingly."
            )

        if who_should_fix == "deploy_system" and not user_actions:
            user_actions.append(
                "No repository-side change is clearly required yet; wait for deployment template/system fix and retry."
            )

        suggested_fix = "\n".join(f"- {step}" for step in user_actions)

        return {
            "simple_explanation": "Build failed due to one or more errors in the build logs.",
            "root_cause": short_log or "Could not determine root cause from empty logs.",
            "suggested_fix": suggested_fix or "Review raw logs and apply the failing-step fix.",
            "who_should_fix": who_should_fix,
            "user_action_items": user_actions,
            "system_action_items": system_actions,
        }

    def diagnose(self, error_log: str, context: Optional[Dict[str, Any]] = None) -> dict:
        log = self._truncate_log(error_log or "")
        context_payload = self._build_context_payload(context)
        context_json = json.dumps(context_payload, indent=2)
        content = ""

        prompt = f"""You are a senior DevOps engineer.

            Analyze the deployment failure and return ONLY valid JSON.

            Strict rules:
            - NO markdown
            - NO explanations outside JSON
            - Mention the concrete failing command/layer/error token whenever present
            - Distinguish owner clearly: user repo vs deploy system vs infra
            - If generated Dockerfile is likely wrong, classify as deploy_system unless logs prove a user-repo issue

            JSON format:
            {{
            "simple_explanation": "one simple sentence",
            "root_cause": "technical cause with specific failing clue",
            "who_should_fix": "user_repo|deploy_system|infrastructure|unclear",
            "suggested_fix": "step-by-step fix",
            "user_action_items": ["repo-side action 1", "repo-side action 2"],
            "system_action_items": ["platform-side action 1", "platform-side action 2"]
            }}

            DIAGNOSIS CONTEXT:
            {context_json}

            ERROR LOG:
            {log}
        """

        try:
            response = ollama.chat(
                model=LLM_MODEL,
                messages=[{"role": "user", "content": prompt}],
            )

            content = response.get("message", {}).get("content", "")
            cleaned = self._clean_json(content)

            data = json.loads(cleaned)

            simple_explanation = str(data.get("simple_explanation", "")).strip()
            root_cause = str(data.get("root_cause", "")).strip()

            suggested_fix_raw = data.get("suggested_fix", "")
            if isinstance(suggested_fix_raw, list):
                suggested_fix = "\n".join(str(v).strip() for v in suggested_fix_raw if str(v).strip())
            else:
                suggested_fix = str(suggested_fix_raw or "").strip()

            who_should_fix = str(data.get("who_should_fix", "unclear")).strip().lower()
            if who_should_fix not in _ALLOWED_OWNERS:
                who_should_fix = "unclear"

            user_action_items = self._normalize_action_items(data.get("user_action_items", []))
            system_action_items = self._normalize_action_items(data.get("system_action_items", []))

            if not user_action_items and suggested_fix:
                user_action_items = self._normalize_action_items(suggested_fix)

            if not simple_explanation:
                simple_explanation = "Deployment failed. Check root cause and actions below."
            if not root_cause:
                root_cause = "Could not extract an exact root cause from the provided logs."
            if not suggested_fix:
                suggested_fix = "Review the raw logs and apply the listed action items."

            return {
                "simple_explanation": simple_explanation,
                "root_cause": root_cause,
                "suggested_fix": suggested_fix,
                "who_should_fix": who_should_fix,
                "user_action_items": user_action_items,
                "system_action_items": system_action_items,
            }

        except Exception as e:
            logger.warning(f"Diagnosis fallback triggered: {str(e)}")
            return self._fallback_response(error_log, context=context)
        
    def generate_raw(self, prompt: str) -> str:
        try:
            response = ollama.chat(
                model=LLM_MODEL,
                messages=[{"role": "user", "content": prompt}],
            )
            return response.get("message", {}).get("content", "")
        except Exception as e:
            logger.error(f"generate_raw failed: {str(e)}")
            return ""