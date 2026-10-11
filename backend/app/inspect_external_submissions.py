"""本机一次性外发核对；只读默认，不重发请求、不解除未知结果保护。"""

import argparse
from contextlib import closing
import json
from pathlib import Path
import sqlite3

from app.core.config import AppSettings, load_settings
from app.service.audit_service import record_audit_event


def _connection(settings: AppSettings, *, writable: bool = False) -> sqlite3.Connection:
    mode = "rw" if writable else "ro"
    connection = sqlite3.connect(settings.database_path.resolve().as_uri() + f"?mode={mode}", uri=True)
    connection.row_factory = sqlite3.Row
    return connection


def _summary(row: sqlite3.Row) -> dict:
    corrupt = False
    try:
        outcome = json.loads(row["outcome_json"]) if row["outcome_json"] else {}
        if not isinstance(outcome, dict):
            corrupt = True
            outcome = {}
    except (ValueError, TypeError):
        corrupt, outcome = True, {}
    status = outcome.get("status")
    known = status in {"completed", "failed"} and not corrupt
    return {"id": row["id"], "block_id": row["block_id"], "authorization_status": row["status"],
            "outcome_status": status if status in {"completed", "failed", "unknown"} else None,
            "created_at": row["created_at"], "consumed_at": row["consumed_at"],
            "completed_at": row["completed_at"], "corrupt_outcome": corrupt,
            "requires_manual_check": row["status"] != "prepared" and not known,
            "saved_result_pending_audit": known and row["status"] == "consumed",
            "may_still_be_running": row["status"] == "consumed" and not row["outcome_json"]}


def inspectExternalSubmissions(settings: AppSettings, *, limit: int = 200, afterId: str = "") -> dict:
    safeLimit = max(1, min(limit, 200))
    with closing(_connection(settings)) as connection:
        rows = connection.execute("SELECT id,block_id,status,outcome_json,created_at,consumed_at,completed_at "
            "FROM external_submission_authorizations WHERE id>? ORDER BY id LIMIT ?",
            (afterId, safeLimit + 1)).fetchall()
    selected = rows[:safeLimit]
    return {"items": [_summary(row) for row in selected], "next_id": selected[-1]["id"] if selected else "",
            "has_more": len(rows) > safeLimit, "network_requests": 0, "state_changed": False}


def recordExternalObservation(settings: AppSettings, grantId: str, observation: str) -> dict:
    if observation not in {"verified_received", "verified_rejected", "undetermined"}:
        raise ValueError("核查结论不受支持")
    with closing(_connection(settings, writable=True)) as connection:
        connection.execute("BEGIN IMMEDIATE")
        row = connection.execute("SELECT id,block_id,status,outcome_json,created_at,consumed_at,completed_at "
            "FROM external_submission_authorizations WHERE id=?", (grantId,)).fetchone()
        if row is None:
            raise LookupError("授权不存在或已清理")
        if row["status"] == "prepared":
            raise ValueError("未消费的授权无需核查外发结果")
        # 人工观察仅作为审计证据，不覆盖请求自身结果，也不恢复再次外发能力。
        record_audit_event(settings, event_type="external_submission.manual_observation",
            action="inspect_external_submission", result="pending", summary="本机记录外部提交人工核查结论",
            target_type="external_submission_block", target_id=row["block_id"], actor_name="本机维护",
            detail={"authorization_id": grantId, "observation": observation,
                    "outcome_overwritten": False, "resubmission_enabled": False}, connection=connection)
        connection.commit()
    return {"id": grantId, "observation": observation, "network_requests": 0,
            "outcome_overwritten": False, "resubmission_enabled": False, "audit_recorded": True}


def main() -> int:
    parser = argparse.ArgumentParser(description="核对一次性外发；默认只读，不自动重试或解锁未知结果")
    parser.add_argument("--config", required=True)
    parser.add_argument("--limit", type=int, default=200)
    parser.add_argument("--after-id", default="")
    parser.add_argument("--grant-id")
    parser.add_argument("--record-observation", choices=("verified_received", "verified_rejected", "undetermined"))
    parser.add_argument("--confirm-service-stopped", action="store_true")
    args = parser.parse_args()
    if not Path(args.config).is_file():
        parser.error("必须提供存在的配置文件，不能核对默认实例")
    if args.record_observation and (not args.grant_id or not args.confirm_service_stopped):
        parser.error("记录观察需要授权ID及停服确认；工具不负责停服")
    try:
        settings = load_settings(args.config)
        report = (recordExternalObservation(settings, args.grant_id, args.record_observation)
                  if args.record_observation else inspectExternalSubmissions(settings, limit=args.limit, afterId=args.after_id))
    except (OSError, sqlite3.Error, ValueError, LookupError):
        print("核对失败，请检查配置、数据库版本及授权ID；不会初始化实例、重放请求或解锁未知结果")
        return 1
    print(json.dumps(report, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
