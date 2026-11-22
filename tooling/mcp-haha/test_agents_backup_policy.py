"""Read-only policy integration smoke test. No task or GitHub writes."""
from pathlib import Path
import tempfile
import task_engine as engine
import task_policy_backup as policy
root=str(Path(__file__).resolve().parent)
agents=policy.read_policy(root)
assert agents and Path(agents[-1]["path"]).name=="AGENTS.md"
normal={"gate_type":"report","backup_files":[]}
result=policy.stage_preflight({"metadata":{"project_root":root}},normal,root)
assert result["agents"] and not result.get("github")
try:
    policy.stage_preflight({"metadata":{}},{"gate_type":"source_edit","backup_files":[]},root)
except ValueError: pass
else: raise AssertionError("Missing backup_files was not rejected")
stage=engine.stage_spec({"id":"src","command":"Write-Output safe","gate_type":"source_edit","backup_files":["task_engine.py"]},0)
assert stage["backup_files"]==["task_engine.py"]
print("PASS: AGENTS hierarchy, report preflight, fail-closed source edit, backup_files persistence; no network writes")
