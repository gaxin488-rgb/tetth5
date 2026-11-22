# Project backup archive

Repository: `gaxin488-rgb/tetth5` (**PUBLIC**, approved for these non-sensitive test projects).

## Layout

```text
backups/
  README.md
  projects/
    mcp-haha/
      YYYY/MM/DD/<UTC timestamp>/
        manifest.json
        files/<original relative path>
    h5/
    livesub/
    unity/
    blender/
```

A successful archive stores a manifest with timestamp, SHA-256, original relative paths and sizes. Backups never mix different projects into one folder. Keep long-term history in GitHub and only one latest local `*.latest.bak` per source file. Do not rotate local backup files on failed GitHub push.

**PUBLIC BACKUPS ALLOWED:** User approved public storage of these project files. Selected source and documentation may be automatically archived following validation. Never commit `.env`, API keys, tokens, credentials, keystores, personal data, databases, private keys, or other sensitive files. Automated filtering is heuristic: only explicitly allowlisted source files intended for public release should be sent.

For editable workflow stages, supply `metadata.project_root`, `metadata.project_slug`, and `backup_files`. Runner checks `AGENTS.md` before each stage. Git and working noninteractive Git push credentials are needed on the local Windows machine for unattended uploads; GitHub CLI (`gh`) is not required. ChatGPT connector authentication is separate.
