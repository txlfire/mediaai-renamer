"""AI 调用摘要白名单；不记录正文、路径、凭据或原始异常字符串。"""

from dataclasses import dataclass
import json
from pathlib import Path
import traceback

from app.core.logger import get_llm_logger
from app.utils.redaction import redactUrlCredentials


@dataclass(frozen=True)
class AiCallContext:
    requestId: str
    sourceModule: str | None = None
    sourceRecordId: int | None = None


def recordAiSummary(context: AiCallContext, *, provider: str, model: str, phase: str, status: str,
                    elapsedMs: int | None = None, httpStatus: int | None = None,
                    errorType: str | None = None, usage: dict | None = None, exception: Exception | None = None,
                    attempt: int | None = None) -> None:
    try:
        payload = {
            "request_id": context.requestId, "source_module": context.sourceModule,
            "source_record_id": context.sourceRecordId, "provider": redactUrlCredentials(provider)[:128],
            "model": redactUrlCredentials(model)[:128], "phase": phase, "status": status,
            "elapsed_ms": elapsedMs, "http_status": httpStatus, "error_type": errorType,
            "attempt": attempt if attempt is not None else (1 if phase == "transport" else None),
        }
        if usage:
            payload["usage"] = {key: value for key, value in usage.items()
                                if key in {"prompt_tokens", "completion_tokens", "total_tokens"}
                                and isinstance(value, int) and not isinstance(value, bool) and value >= 0}
        if exception is not None:
            payload["exception_type"] = type(exception).__name__
            payload["frames"] = [{"module": Path(frame.filename).name, "function": frame.name, "line": frame.lineno}
                                 for frame in traceback.extract_tb(exception.__traceback__)[-8:]]
        get_llm_logger().info("AI调用摘要 %s", json.dumps(payload, ensure_ascii=False))
    except Exception:  # noqa: BLE001 - 摘要是辅助日志，不能覆盖调用结果或主异常。
        pass
