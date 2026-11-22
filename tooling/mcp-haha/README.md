# MCP haha task runner integration

This directory contains the reviewed preflight module and a smoke test. The active local integration is in `D:\MCP\orchestrator\task_engine.py` (not published here because it belongs to the local MCP installation).

## Behavior

1. Every stage invokes `stage_preflight()` and re-reads ancestor `AGENTS.md` files.
2. Stage events capture file paths and SHA-256 hashes of applicable policy files.
3. Editing stage types `code_edit` and `source_edit` must provide `metadata.project_root` and the exact `backup_files` list.
4. Private repo verification, path validation, binary/secret heuristics, and upload happen before local `*.latest.bak` rotation.
5. On any blocked or failed upload, the editing stage fails closed and existing local backups remain untouched.

## Security

The chosen backup repository `gaxin488-rgb/tetth5` is currently **PUBLIC**. Raw backup uploads are intentionally blocked. The test must not publish source or secret data. Current filtering is heuristic and not a substitute for secret scanning and access-policy review. Do not enable production automated archival until the destination is PRIVATE and a restore test is successful.

## Archive layout

`backups/projects/<project-slug>/YYYY/MM/DD/YYYY-MM-DDTHH-MM-SSZ/{manifest.json,files/...}`.

## Scope

This hook covers stages launched through this task engine, not every existing standalone haha MCP action. A global gateway middleware/interceptor is still required for universal enforcement.
