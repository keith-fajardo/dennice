"""Evidence-based completion checks. No model self-score stands in for tests."""

import json
import os
import stat
from uuid import uuid4

from dennice.core.models import EventKind
from dennice.core.tools import ToolBroker, run_command


async def verify_result(config, output, *, approve=None, emit=None):
    checks = []
    if config.verification.require_nonempty:
        checks.append({"name": "nonempty_response", "passed": bool(output.strip()),
                       "scope": "response presence, not factual correctness"})
    broker = ToolBroker(config, approve=approve, emit=emit)
    for path in config.verification.required_files:
        try:
            descriptor = broker._open(path, os.O_RDONLY | os.O_NONBLOCK)
            try:
                passed = stat.S_ISREG(os.fstat(descriptor).st_mode)
            finally:
                os.close(descriptor)
        except (OSError, ValueError, PermissionError):
            passed = False
        checks.append({"name": f"file:{path}", "passed": passed})
    for argv in config.verification.commands:
        await broker.event(EventKind.APPROVAL_REQUESTED, {"tool": "verification", "arguments": {"argv": argv}})
        accepted = bool(approve and await approve("verification", {"argv": argv}))
        await broker.event(EventKind.APPROVAL_RESOLVED, {"tool": "verification", "approved": accepted})
        if not accepted or config.privacy.local_only:
            checks.append({"name": str(argv), "passed": False, "reason": "Not authorized"})
            continue
        operation_id = uuid4().hex
        await broker.event(EventKind.TOOL_STARTED, {"tool": "verification", "operation_id": operation_id,
                                                   "arguments": {"argv": argv}})
        result = json.loads(await run_command(argv, broker.root, config.tools.timeout_seconds))
        await broker.event(EventKind.TOOL_COMPLETED, {"tool": "verification", "operation_id": operation_id,
                                                     "exit_code": result["exit_code"]})
        checks.append({"name": str(argv), "passed": result["exit_code"] == 0,
                       "evidence": result})
    return {"passed": all(check["passed"] for check in checks), "checks": checks,
            "independent_checks": bool(config.verification.commands or config.verification.required_files)}
