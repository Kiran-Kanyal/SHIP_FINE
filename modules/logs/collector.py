class LogCollector:

    def collect_from_build_result(self, build_result: dict) -> str:
        """Returns the best available raw logs from build execution result."""
        if not build_result:
            return ""

        logs = build_result.get("logs")
        if logs:
            return logs

        error_log = build_result.get("error_log")
        if error_log:
            return error_log

        output = build_result.get("output")
        if output:
            return output

        return ""
