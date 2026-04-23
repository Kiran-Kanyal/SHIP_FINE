from config import LOG_SNIPPET_MAX_CHARS


class LogPreprocessor:

    ERROR_KEYWORDS = (
        "error",
        "failed",
        "traceback",
        "exception",
        "npm err",
        "module not found",
        "cannot find",
        "syntaxerror"
    )

    def _line_is_error(self, line: str) -> bool:
        lower = line.lower()
        return any(keyword in lower for keyword in self.ERROR_KEYWORDS)

    def extract_relevant_error_log(self, raw_logs: str, max_chars: int = LOG_SNIPPET_MAX_CHARS) -> str:
        """
        Extracts a compact error-focused snippet from raw build logs.
        """
        if not raw_logs:
            return ""

        lines = raw_logs.splitlines()
        matching_indexes = [idx for idx, line in enumerate(lines) if self._line_is_error(line)]

        if not matching_indexes:
            snippet = raw_logs[-max_chars:]
            return snippet.strip()

        selected = []
        for idx in matching_indexes:
            start = max(0, idx - 2)
            end = min(len(lines), idx + 3)
            selected.extend(lines[start:end])

        # Deduplicate while preserving order
        deduped = []
        seen = set()
        for line in selected:
            key = line.strip()
            if key in seen:
                continue
            seen.add(key)
            deduped.append(line)

        snippet = "\n".join(deduped).strip()
        if len(snippet) > max_chars:
            snippet = snippet[-max_chars:]

        return snippet
