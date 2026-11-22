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

# Public backup policy: repo visibility is no longer a reason to reject uploads.
import inspect
assert "ls-remote" in inspect.getsource(policy.github_repo_ready)
assert "gh" not in inspect.getsource(policy.github_repo_ready).split("remote=")[0]
assert "private_repo_required" not in inspect.getsource(policy.archive)
assert policy.prepare(root,["task_policy_backup.py"])
print("PASS: public destination allowed after file screening (no GitHub upload in smoke test)")

with tempfile.TemporaryDirectory() as folder:
    example=Path(folder)
    (example/"safe.py").write_text("print('hello')\n",encoding="utf-8")
    assert len(policy.prepare(folder,["safe.py"]))==1
    (example/".env").write_text("TOKEN=dummy\n",encoding="utf-8")
    try: policy.prepare(folder,[".env"])
    except ValueError: pass
    else: raise AssertionError("public upload accepted .env")
    (example/"secret.py").write_text("api_key = 'ghp_"+"x"*30+"'\n",encoding="utf-8")
    try: policy.prepare(folder,["secret.py"])
    except ValueError: pass
    else: raise AssertionError("public upload accepted secret")
print("PASS: allowlisted public backup and sensitive-content refusals")
