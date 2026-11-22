# MCP haha task runner integration

This directory documents the working local task-runner hook at `D:\MCP\orchestrator\task_engine.py`, along with the policy module and tests.

## Workflow

1. Before every stage: re-read applicable ancestor `AGENTS.md` files.
2. Store the instruction file paths and SHA-256 hashes in task events.
3. Source editing stages (`source_edit` and `code_edit`) require `metadata.project_root` and an explicit list of `backup_files`.
4. Before writing source: validate file paths/extensions, screen for binary content and recognizable credentials, and archive selected allowed files in `gaxin488-rgb/tetth5`.
5. GitHub history retains earlier snapshots. Only after a successful push, rotate local `*.latest.bak` per source. A failed push blocks the stage and preserves existing local backups.

## Repository visibility

The user has explicitly approved **PUBLIC** storage for these low-value projects. The backup code therefore accepts PUBLIC or PRIVATE GitHub repositories; PRIVATE visibility is no longer required.

Even in PUBLIC repositories, never back up `.env`, credentials, secrets, keystores, private personal data or unscreened full-directory archives. Automated content scanning is heuristic and may miss secrets. Limit `backup_files` to source and documentation intentionally approved for public release.

The local worker requires working `git` and authenticated `gh` command-line tools. Having an authorized ChatGPT GitHub connector does not automatically authenticate GitHub CLI on the Windows machine.

## Structure

`backups/projects/<project-slug>/YYYY/MM/DD/YYYY-MM-DDTHH-MM-SSZ/{manifest.json,files/...}`.

## Scope

The current preflight covers stages executed by this specific task engine, not all standalone MCP actions. Full gateway enforcement remains separate work.
