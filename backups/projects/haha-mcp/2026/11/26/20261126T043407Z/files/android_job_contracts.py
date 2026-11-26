"""Bounded Android and durable-job contracts for the composite gateway.
No caller-supplied commands, credentials, APK installs or application builds.
"""
import asyncio
import base64
import hashlib
import json
import re
import sys
import tempfile
import time
import zipfile
from pathlib import Path

NAMES = frozenset(("android_sdk_status", "apk_inspect", "apk_signature_verify",
                   "apk_signature_compare", "banphim_run_executable",
                   "banphim_gradle_task", "task_status", "task_log_cursor",
                   "task_create", "task_result", "task_validate", "task_completion_gate_manage",
                   "task_resume_after_restart", "task_failure_analyze",
                   "task_list", "task_recover", "task_deadlock_detect", "task_stage_output_verify", "task_acceptance_check", "task_live_metrics", "task_event_subscribe", "task_wait_condition",
                   "task_heartbeat_monitor", "task_iteration_compare", "task_resource_limit"))
TERMINAL = {"completed", "failed", "cancelled", "interrupted", "paused"}
TOKEN = "HAHA_CONTRACT_NATIVE_OUTPUT"

def normalize(value):
    if isinstance(value, dict) and isinstance(value.get("text"), str):
        try:
            inner = json.loads(value["text"])
            if isinstance(inner, dict):
                return inner
        except ValueError:
            pass
    return value if isinstance(value, dict) else {}

def sha(path):
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()

class Contracts:
    def __init__(self, invoke):
        self.invoke = invoke
        self.jobs = {}
        self.started_jobs = {}
        self.apk = None

    async def call(self, name, args):
        return normalize(await self.invoke(name, args))

    async def fixture(self):
        if self.apk is None:
            result = await self.call("banphim_find_apks", {"max_results": 10})
            # Use an existing local debug APK, never install or rewrite it.
            candidates = [Path(row["path"]) for row in result.get("files", [])
                          if isinstance(row.get("path"), str)]
            self.apk = next((p for p in candidates if p.name == "app-debug.apk"
                             and p.is_file() and 0 < p.stat().st_size <= 64*1024*1024), None)
            if self.apk is None:
                raise RuntimeError("Existing debug APK fixture unavailable (max 64 MiB)")
        return self.apk

    async def job(self, kind):
        if kind in self.jobs:
            return self.jobs[kind]
        if kind == "gradle":
            action = "banphim_gradle_task"
            args = {"task": "help", "extra_args": ["--offline"], "timeout": 60,
                    "confirm": "BUILD", "background": True}
        else:
            action = "banphim_run_executable"
            exit_code = 7 if kind == "negative" else 0
            args = {"executable": sys.executable, "args": ["-c",
                    "import sys; print('"+TOKEN+"', flush=True); sys.exit("+str(exit_code)+")"],
                    "cwd": str(Path(__file__).resolve().parent), "timeout": 15,
                    "confirm": "EXECUTE", "background": True}
        if kind not in self.started_jobs:
            # An interrupted launch can have committed remotely. Do not submit it twice.
            self.started_jobs[kind] = None
            started = await self.call(action, args)
            tid = started.get("task_id")
            if not (started.get("ok") is True and isinstance(tid, str)
                    and re.fullmatch(r"task_[0-9a-f]{12}", tid)
                    and tid == started.get("job_id")):
                raise RuntimeError("Durable job ID not returned: " + str(started.get("error_code", "")))
            self.started_jobs[kind] = started
        started = self.started_jobs[kind]
        if started is None:
            raise RuntimeError("Previous contract launch outcome unknown; refusing duplicate submission")
        tid = started["task_id"]
        deadline = time.monotonic() + (65 if kind == "gradle" else 20)
        while True:
            status = await self.call("task_status", {"task_id": tid})
            if status.get("id") != tid:
                raise RuntimeError("Task identity mismatch")
            if status.get("status") in TERMINAL:
                break
            if time.monotonic() >= deadline:
                raise TimeoutError("Contract job did not finish: " + tid)
            await asyncio.sleep(0.5)
        stage = status.get("current_stage") or {}
        result = await self.call("task_result", {"task_id": tid})
        log = await self.call("task_logs", {"task_id": tid, "stage_id": stage["id"], "tail": 80})
        evidence = {"job_id": tid, "status": status, "result": result,
                    "stage": stage, "logs": log.get("logs", []), "started": started}
        self.jobs[kind] = evidence
        return evidence

    @staticmethod
    def finished(job, expected):
        status, result, stage = job["status"], job["result"], job["stage"]
        data = stage.get("data", {})
        success = expected == 0
        terminal = "completed" if success else "failed"
        return (status.get("status") == terminal and result.get("status") == terminal
                and result.get("task_id") == job["job_id"]
                and stage.get("status") == terminal
                and type(data.get("exit_code")) is int and data["exit_code"] == expected
                and data.get("gate", {}).get("passed") is success
                and (success or data.get("error_code") == "PROCESS_EXIT_NONZERO"))

    async def run(self, action):
        row = {"action": action, "tested": True, "passed": False,
               "scope": "bounded real local backend contract; not game or release verification",
               "business_verified": False, "business_status": "NOT_TESTED"}
        checks = {}
        try:
            if action == "android_sdk_status":
                out = await self.call(action, {})
                checks = {key: out.get(key) is True for key in
                          ("ok", "adb_available", "apk_inspection_available",
                           "signature_verification_available")}
            elif action in {"apk_inspect", "apk_signature_verify", "apk_signature_compare"}:
                apk = await self.fixture()
                row["fixture_bytes"] = apk.stat().st_size
                with tempfile.TemporaryDirectory(prefix="haha_apk_contract_") as temp:
                    bad = Path(temp) / "unsigned.apk"
                    with zipfile.ZipFile(bad, "w") as archive:
                        archive.writestr("probe.txt", "unsigned fixture")
                    arg = {"apk_path": str(apk)}
                    negative = {"apk_path": str(bad)}
                    if action == "apk_inspect":
                        out = await self.call(action, arg)
                        checks = {"real_metadata": out.get("ok") is True and
                                  bool(out.get("package")) and bool(out.get("version_code")),
                                  "independent_sha256": out.get("sha256") == sha(apk)}
                        rejected = await self.call(action, negative)
                        checks["invalid_apk_rejected"] = (rejected.get("ok") is not True
                            and ("AndroidManifest.xml" in str(rejected) or
                                 "valid APK" in str(rejected)))
                        row["apk_sha256"] = out.get("sha256")
                    elif action == "apk_signature_verify":
                        out = await self.call(action, arg)
                        rejected = await self.call(action, negative)
                        certs = out.get("signer_certificate_sha256", [])
                        checks = {"signed_verified": out.get("verified") is True and out.get("exit_code") == 0,
                                  "certificate_sha256": bool(certs) and all(re.fullmatch(r"[0-9a-f]{64}", c) for c in certs),
                                  "unsigned_rejected": rejected.get("verified") is False and rejected.get("exit_code") != 0}
                        row["certificate_sha256"] = certs
                    else:
                        same = await self.call(action, {**arg, "other_apk_path": str(apk)})
                        mixed = await self.call(action, {**arg, "other_apk_path": str(bad)})
                        invalid = await self.call(action, {**negative, "other_apk_path": str(bad)})
                        checks = {"valid_same_signer": same.get("both_verified") is True and same.get("same_current_signer_set") is True,
                                  "mixed_rejected": mixed.get("both_verified") is False and mixed.get("same_current_signer_set") is False,
                                  "two_unsigned_rejected": invalid.get("both_verified") is False and invalid.get("same_current_signer_set") is False}
            elif action == "task_create":
                stage = {"id": "probe", "name": "Contract probe", "command": "Write-Output 'HAHA_CONTRACT_NATIVE_OUTPUT'",
                         "gate_type": "custom_command", "gate_criteria": {"pattern": TOKEN},
                         "timeout_seconds": 10, "max_retries": 0}
                created = await self.call(action, {"name": "MCP create contract (not executed)",
                    "stages": [stage], "metadata": {"project_root": "D:\\MCP", "contract_probe": True}})
                tid = created.get("id")
                if not isinstance(tid, str) or not re.fullmatch(r"task_[0-9a-f]{12}", tid):
                    raise RuntimeError("Invalid created task ID")
                row["job_id"] = tid
                try:
                    state = await self.call("task_status", {"task_id": tid})
                    current = state.get("current_stage") or {}
                    checks = {"created_pending": created.get("status") == "pending",
                              "persisted_identity": state.get("id") == tid and state.get("status") == "pending",
                              "stage_roundtrip": all(current.get(k) == val for k, val in stage.items()),
                              "not_started": state.get("runner_pid") is None and state.get("child_pid") is None}
                finally:
                    cancelled = await self.call("task_cancel", {"task_id": tid})
                    checks["fixture_cancel_requested"] = cancelled.get("task_id") == tid and cancelled.get("status") == "cancelled"
            elif action in {"task_result", "task_validate", "task_completion_gate_manage", "task_resume_after_restart", "task_failure_analyze"}:
                job = await self.job("positive")
                bad = await self.job("negative")
                row.update(job_id=job["job_id"], negative_job_id=bad["job_id"])
                checks = {"positive_fixture": self.finished(job, 0), "negative_fixture": self.finished(bad, 7)}
                if action == "task_result":
                    good_result = await self.call(action, {"task_id": job["job_id"]})
                    bad_result = await self.call(action, {"task_id": bad["job_id"]})
                    checks.update(completed_readback=good_result.get("task_id") == job["job_id"]
                                  and good_result.get("status") == "completed"
                                  and good_result.get("result", {}).get("workflow_complete") is True
                                  and job["stage"]["id"] in good_result.get("result", {}).get("verified_stage_ids", []),
                                  failure_readback=bad_result.get("task_id") == bad["job_id"]
                                  and bad_result.get("status") == "failed"
                                  and any(s.get("data", {}).get("exit_code") == 7 and
                                          s.get("data", {}).get("gate", {}).get("passed") is False
                                          for s in bad_result.get("stages", [])))
                elif action == "task_validate":
                    args = {"task_id": job["job_id"], "stage_id": job["stage"]["id"]}
                    yes = await self.call(action, {**args, "context": {"exit_code": 0, "log_output": TOKEN}})
                    no = await self.call(action, {**args, "context": {"exit_code": 7, "log_output": TOKEN}})
                    after = await self.call("task_status", {"task_id": job["job_id"]})
                    checks.update(zero_exit_accepted=yes.get("passed") is True,
                                  nonzero_exit_rejected=no.get("passed") is False,
                                  state_unchanged=after.get("status") == "completed" and
                                  (after.get("current_stage") or {}).get("data") == job["stage"].get("data"))
                elif action == "task_completion_gate_manage":
                    row["scope"] = "bounded inspect and validate on terminal contract jobs; replace not executed"
                    for label, item, expected in (("completed", job, True), ("failed", bad, False)):
                        args = {"task_id": item["job_id"]}
                        inspected = await self.call(action, {**args, "operation": "inspect"})
                        checks[label + "_inspect"] = (inspected.get("ok") is True
                            and inspected.get("task_id") == item["job_id"]
                            and inspected.get("structural_checks_passed") is expected
                            and inspected.get("acceptance_verified") is expected)
                        validation = {**args, "operation": "validate", "stage_id": item["stage"]["id"]}
                        yes = await self.call(action, {**validation, "context": {"exit_code": 0, "log_output": TOKEN}})
                        no = await self.call(action, {**validation, "context": {"exit_code": 7, "log_output": TOKEN}})
                        checks[label + "_zero_exit_accepted"] = yes.get("passed") is True
                        checks[label + "_nonzero_exit_rejected"] = no.get("passed") is False
                        after = await self.call("task_status", args)
                        checks[label + "_state_unchanged"] = (after.get("id") == item["job_id"]
                            and after.get("status") == label and after.get("current_stage") == item["stage"]
                            and after.get("runner_pid") is None)
                elif action == "task_resume_after_restart":
                    for label, item, terminal in (("completed", job, "completed"), ("failed", bad, "failed")):
                        out = await self.call(action, {"task_id": item["job_id"], "operation": "inspect"})
                        checks[label + "_not_resumable"] = (out.get("ok") is True and
                            out.get("task_id") == item["job_id"] and out.get("status") == terminal
                            and out.get("resume_allowed") is False and out.get("runner_pid") is None)
                    row["scope"] = "terminal-task resume inspection; no restart or live recovery executed"
                else:
                    out = await self.call(action, {"task_id": bad["job_id"], "tail": 10})
                    status = normalize({"text": out.get("status")})
                    events = normalize({"text": out.get("events")})
                    logs = normalize({"text": out.get("logs_redacted")})
                    checks.update(failure_identity=out.get("ok") is True and out.get("task_id") == bad["job_id"]
                                  and status.get("id") == bad["job_id"] and status.get("status") == "failed",
                                  failure_code=(status.get("current_stage") or {}).get("data", {}).get("error_code") == "PROCESS_EXIT_NONZERO",
                                  failure_event=any(e.get("status") == "failed" and e.get("data", {}).get("exit_code") == 7
                                                    for e in events.get("events", [])),
                                  log_evidence=logs.get("task_id") == bad["job_id"] and TOKEN in logs.get("logs", []),
                                  no_automatic_mutation=out.get("automatic_recovery") is False)
            elif action in {"task_list", "task_recover", "task_deadlock_detect", "task_stage_output_verify", "task_acceptance_check", "task_live_metrics", "task_event_subscribe", "task_wait_condition"}:
                job = await self.job("positive")
                bad = await self.job("negative")
                row.update(job_id=job["job_id"], negative_job_id=bad["job_id"])
                checks = {"positive_fixture": self.finished(job, 0), "negative_fixture": self.finished(bad, 7)}
                if action in {"task_event_subscribe", "task_wait_condition"}:
                    for label, item in (("completed", job), ("failed", bad)):
                        args = {"task_id": item["job_id"]}
                        before = await self.call("task_status", args)
                        if action == "task_event_subscribe":
                            full = await self.call("task_events", {**args, "cursor": 0})
                            expected = full.get("events", [])
                            first = await self.call(action, {**args, "cursor": 0, "limit": 1, "timeout_seconds": 0})
                            rest = await self.call(action, {**args, "cursor": 1, "limit": 100, "timeout_seconds": 0})
                            eof = await self.call(action, {**args, "cursor": len(expected), "limit": 1, "timeout_seconds": 0})
                            checks[label + "_first_page"] = (len(expected) > 1 and first.get("ok") is True
                                and first.get("task_id") == item["job_id"] and first.get("events") == expected[:1]
                                and first.get("cursor") == 1 and first.get("truncated") is True)
                            checks[label + "_remaining_page"] = (rest.get("ok") is True
                                and rest.get("events") == expected[1:] and rest.get("cursor") == len(expected)
                                and rest.get("truncated") is False)
                            checks[label + "_eof"] = (eof.get("ok") is True and eof.get("events") == []
                                and eof.get("cursor") == len(expected) and eof.get("truncated") is False)
                        else:
                            for target in ("completed", "failed"):
                                out = await self.call(action, {**args, "target_status": target})
                                checks[label + "_target_" + target] = (out.get("ok") is True
                                    and out.get("task_id") == item["job_id"] and out.get("status") == label
                                    and out.get("met") is (label == target))
                        after = await self.call("task_status", args)
                        checks[label + "_state_unchanged"] = (before == after and after.get("status") == label)
                    row["scope"] = "terminal fixture event pagination or status predicate; no live runner claim"
                elif action == "task_heartbeat_monitor":
                    for label, item in (("completed", job), ("failed", bad)):
                        args = {"task_id": item["job_id"]}
                        before = await self.call("task_status", args)
                        out = await self.call(action, {**args, "duration_seconds": 0,
                                                        "interval_seconds": 0.25})
                        after = await self.call("task_status", args)
                        evidence_path = out.get("evidence_path")
                        evidence_file = Path(evidence_path) if isinstance(evidence_path, str) else None
                        evidence = (json.loads(evidence_file.read_text(encoding="utf-8-sig"))
                                    if evidence_file is not None and evidence_file.is_file() else {})
                        first, last = out.get("before"), out.get("after")
                        checks[label + "_snapshot_read_only"] = (
                            out.get("ok") is True and out.get("task_id") == item["job_id"]
                            and type(out.get("polls")) is int and out["polls"] == 0
                            and isinstance(first, dict) and isinstance(last, dict)
                            and first.get("id") == item["job_id"] and last.get("id") == item["job_id"]
                            and first.get("status") == label and last.get("status") == label
                            and first == last and out.get("state_changed") is False
                            and out.get("error_type") is None
                            and before.get("id") == item["job_id"] and before.get("status") == label
                            and after == before)
                        checks[label + "_saved_evidence"] = (
                            out.get("evidence_saved") is True and evidence_file is not None
                            and evidence_file.is_file() and evidence.get("ok") is True
                            and evidence.get("task_id") == item["job_id"]
                            and evidence.get("state_changed") is False
                            and evidence.get("before") == first and evidence.get("after") == last)
                        row.setdefault("evidence_paths", {})[label] = {
                            "path": evidence_path,
                            "sha256": sha(evidence_file) if evidence_file is not None and evidence_file.is_file() else None,
                        }
                    row["scope"] = "zero-duration runner heartbeat snapshot and saved evidence; no persistent monitor claim"
                elif action == "task_iteration_compare":
                    same = await self.call(action, {"task_id": job["job_id"],
                                                    "other_task_id": job["job_id"]})
                    different = await self.call(action, {"task_id": job["job_id"],
                                                         "other_task_id": bad["job_id"]})
                    def parsed_result(value):
                        try:
                            payload = json.loads(value) if isinstance(value, str) else {}
                            return payload if isinstance(payload, dict) else {}
                        except ValueError:
                            return {}
                    same_first, same_second = parsed_result(same.get("first")), parsed_result(same.get("second"))
                    diff_first, diff_second = parsed_result(different.get("first")), parsed_result(different.get("second"))
                    after_job = await self.call("task_status", {"task_id": job["job_id"]})
                    after_bad = await self.call("task_status", {"task_id": bad["job_id"]})
                    checks = {
                        "identical_final_result": (
                            same.get("ok") is True and same.get("equal") is True
                            and same.get("task_id") == job["job_id"]
                            and same.get("other_task_id") == job["job_id"]
                            and same_first.get("task_id") == job["job_id"]
                            and same_second.get("task_id") == job["job_id"]
                            and same_first.get("status") == "completed"
                            and same_second.get("status") == "completed"),
                        "different_final_results": (
                            different.get("ok") is True and different.get("equal") is False
                            and different.get("task_id") == job["job_id"]
                            and different.get("other_task_id") == bad["job_id"]
                            and diff_first.get("task_id") == job["job_id"]
                            and diff_second.get("task_id") == bad["job_id"]
                            and diff_first.get("status") == "completed"
                            and diff_second.get("status") == "failed"),
                        "fixture_state_unchanged": (
                            after_job == job["status"] and after_bad == bad["status"]),
                    }
                    row["scope"] = "compares only final persisted task results; task states remain read-only"
                elif action == "task_resource_limit":
                    for label, item in (("completed", job), ("failed", bad)):
                        args = {"task_id": item["job_id"]}
                        before = await self.call("task_status", args)
                        out = await self.call(action, {**args, "operation": "get"})
                        after = await self.call("task_status", args)
                        limits = out.get("limits")
                        enforcement = out.get("last_enforcement")
                        checks[label + "_read_only_get"] = (
                            out.get("ok") is True and out.get("task_id") == item["job_id"]
                            and out.get("status") == label
                            and (limits is None or isinstance(limits, dict))
                            and isinstance(out.get("scope"), str)
                            and "Job Object" in out.get("scope", "")
                            and isinstance(enforcement, dict)
                            and enforcement.get("kernel_enforced") is True
                            and type(enforcement.get("active_processes")) is int
                            and enforcement["active_processes"] == 0
                            and before.get("id") == item["job_id"] and before.get("status") == label
                            and after == before)
                    row["scope"] = "operation=get only; verifies persisted Windows Job Object evidence without changing limits"
                elif action in {"task_acceptance_check", "task_live_metrics"}:
                    for label, item, expected in (("completed", job, True), ("failed", bad, False)):
                        out = await self.call(action, {"task_id": item["job_id"]})
                        after = await self.call("task_status", {"task_id": item["job_id"]})
                        checks[label + "_identity_and_read_only"] = (
                            out.get("ok") is True and out.get("task_id") == item["job_id"]
                            and after.get("id") == item["job_id"] and after.get("status") == label
                            and after.get("current_stage") == item["stage"]
                            and after.get("runner_pid") is None)
                        if action == "task_acceptance_check":
                            checks[label + "_gate"] = (
                                out.get("structural_checks_passed") is expected
                                and out.get("acceptance_verified") is expected
                                and isinstance(out.get("issues"), list)
                                and (not out["issues"] if expected else
                                     any(item["stage"]["id"] in str(issue) and "failed" in str(issue)
                                         for issue in out["issues"])))
                        else:
                            checks[label + "_metrics"] = (
                                out.get("status") == label
                                and bool(out.get("heartbeat_at"))
                                and out.get("heartbeat_at") == after.get("heartbeat_at")
                                and type(out.get("stages")) is int
                                and out["stages"] == after.get("stages_count") == 1)
                    row["scope"] = "terminal task acceptance or persisted heartbeat readback; no live runner claim"
                elif action == "task_list":
                    for label, item in (("completed", job), ("failed", bad)):
                        out = await self.call(action, {"status": label, "limit": 100})
                        tasks = out.get("tasks")
                        checks[label + "_filtered"] = (out.get("ok") is True and isinstance(tasks, list)
                            and all(t.get("status") == label for t in tasks)
                            and type(out.get("total")) is int and out["total"] >= len(tasks)
                            and any(t.get("id") == item["job_id"] for t in tasks))
                    limited = await self.call(action, {"limit": 1})
                    checks["limit_enforced"] = limited.get("ok") is True and len(limited.get("tasks", [])) == 1
                elif action == "task_recover":
                    for label, item in (("completed", job), ("failed", bad)):
                        out = await self.call(action, {"task_id": item["job_id"]})
                        after = await self.call("task_status", {"task_id": item["job_id"]})
                        checks[label + "_read_only"] = (out.get("ok") is True and out.get("task_id") == item["job_id"]
                            and out.get("status") == label and type(out.get("checkpoints")) is int
                            and out["checkpoints"] == after.get("checkpoints_count")
                            and after.get("status") == label and after.get("runner_pid") is None)
                elif action == "task_deadlock_detect":
                    listed = await self.call("task_list", {"limit": 5})
                    out = await self.call(action, {"limit": 5})
                    evidence_path = out.get("evidence_path")
                    evidence = json.loads(Path(evidence_path).read_text(encoding="utf-8-sig")) if evidence_path else {}
                    actual_ids = {t["id"] for t in listed.get("tasks", [])}
                    checks.update(nonempty_scan=out.get("ok") is True and out.get("tasks_checked") == len(actual_ids) > 0,
                                  evidence_saved=out.get("evidence_saved") is True,
                                  real_dependency_rows={t["task_id"] for t in evidence.get("dependency_evidence", [])} == actual_ids,
                                  actual_task_rows={t["id"] for t in evidence.get("before_tasks", [])} == actual_ids,
                                  bounded_scope=out.get("total_tasks") == listed.get("total") and out.get("scan_complete") is (listed.get("total") == len(actual_ids)))
                    row["evidence_path"] = evidence_path
                    row["scope"] = "actual bounded task inventory and dependency readback; no synthetic deadlock or OS lease claim"
                else:
                    for label, item, expected in (("completed", job, True), ("failed", bad, False)):
                        out = await self.call(action, {"task_id": item["job_id"]})
                        acceptance = normalize({"text": out.get("acceptance_evidence")})
                        artifacts = normalize({"text": out.get("artifacts")})
                        checks[label + "_acceptance"] = (out.get("ok") is True and out.get("task_id") == item["job_id"]
                            and acceptance.get("task_id") == item["job_id"] and acceptance.get("acceptance_verified") is expected
                            and artifacts.get("task_id") == item["job_id"]
                            and any(t.get("stage_id") == item["stage"]["id"] and t.get("size_bytes", 0) > 0 for t in artifacts.get("logs", []))
                            and out.get("semantic_quality_verified") is False)
            elif action == "banphim_gradle_task":
                job = await self.job("gradle")
                row["job_id"] = job["job_id"]
                checks = {"durable_job": self.finished(job, 0),
                          "gradle_success_log": any(line.strip().startswith("BUILD SUCCESSFUL") for line in job["logs"])}
                row["scope"] = "real Gradle help --offline; no application compilation"
            elif action in {"banphim_run_executable", "task_status", "task_log_cursor"}:
                job = await self.job("positive")
                row["job_id"] = job["job_id"]
                checks = {"completed_job": self.finished(job, 0)}
                if action == "banphim_run_executable":
                    bad = await self.job("negative")
                    row["negative_job_id"] = bad["job_id"]
                    checks.update(stdout_verified=TOKEN in job["logs"],
                                  nonzero_exit_verified=self.finished(bad, 7))
                elif action == "task_status":
                    bad = await self.job("negative")
                    row["negative_job_id"] = bad["job_id"]
                    checks.update(terminal_failure=self.finished(bad, 7),
                                  heartbeat=bool(job["status"].get("heartbeat_at")),
                                  completed_gate=job["stage"].get("data", {}).get("gate", {}).get("passed") is True)
                else:
                    p = Path(job["stage"]["data"]["log_file"])
                    raw = p.read_bytes()
                    if not (7 < len(raw) < 65536 and TOKEN.encode() in raw):
                        raise RuntimeError("Unexpected fixture log size or contents")
                    args = {"task_id": job["job_id"], "stage_id": job["stage"]["id"]}
                    first = await self.call(action, {**args, "offset": 0, "max_bytes": 7})
                    rest = await self.call(action, {**args, "offset": 7, "max_bytes": 65536})
                    eof = await self.call(action, {**args, "offset": len(raw), "max_bytes": 7})
                    checks.update(first_chunk=first.get("ok") is True and first.get("next_offset")==7 and first.get("eof") is False
                                  and base64.b64decode(first.get("base64", ""), validate=True)==raw[:7],
                                  remaining_chunk=rest.get("ok") is True and rest.get("next_offset")==len(raw) and rest.get("eof") is True
                                  and base64.b64decode(rest.get("base64", ""), validate=True)==raw[7:],
                                  exact_eof=eof.get("ok") is True and eof.get("next_offset")==len(raw) and eof.get("eof") is True
                                  and base64.b64decode(eof.get("base64", ""), validate=True)==b"")
                    row["log_sha256"] = hashlib.sha256(raw).hexdigest()
            else:
                raise ValueError("Unsupported contract")
            row["passed"] = bool(checks) and all(value is True for value in checks.values())
        except Exception as exc:
            row["reason"] = type(exc).__name__ + ": " + str(exc)[:300]
        row["checks"] = checks
        row["contract_passed"] = row["passed"]
        row["runtime_verified"] = row["passed"]
        return row
