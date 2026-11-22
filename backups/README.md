# MCP / Task Runner Backup Archive

Repository: `gaxin488-rgb/tetth5` (currently **PUBLIC**).

## Layout

```text
backups/
  README.md
  projects/
    mcp-haha/
      YYYY/MM/DD/<timestamp>/
        manifest.json
        files/<relative-project-path>
    h5/
      YYYY/MM/DD/<timestamp>/
        manifest.json
        files/<relative-project-path>
    livesub/
    unity/
    blender/
```

Each snapshot contains a manifest (UTC timestamp, SHA-256, relative paths and file sizes); never flatten different projects into one folder. Keep history in GitHub; local backups keep one `*.latest.bak` per original file. Verify GitHub commit and file hashes **before** removing any superseded local backup. A failed upload must never trigger local cleanup.

**SECURITY:** This repository is PUBLIC. Only pre-reviewed, nonsensitive text snapshots may be committed here. Never push `.env`, API keys, keystores, auth tokens, databases, player/user data, crash logs, private code or raw unsanitized project archives. Automated unreviewed backups MUST fail closed until a PRIVATE repository and authenticated git/gh are verified. No secrets should be stored in history, even temporarily. Update the repo setting before enabling live auto-upload.

For multi-project workflows supply explicit `metadata.project_root`, `metadata.project_slug`, and `backup_files` for editing stages. Every stage reloads applicable `AGENTS.md` from ancestor folders, with SHA-256 recorded in the task event log.
