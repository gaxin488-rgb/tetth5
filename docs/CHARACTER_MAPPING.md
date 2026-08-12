# Character mapping in the web player

The clean Vietnamese VTT is still subtitle-only. Character identity is kept in
`public/data/character-mappings/yosuga-no-sora.json`, generated from the local
named reports:

```powershell
python scripts/build-web-character-mapping.py `
  --reports-dir .\content\generated-subtitles `
  --character-profile .\profiles\yosuga-no-sora-01.json `
  --output .\public\data\character-mappings\yosuga-no-sora.json
```

The Worker exposes the selected episode through:

```text
GET /api/movies/yosuga-no-sora/character-mapping?episode=7
```

The response contains `characters` and an `episode.cues` array. Each cue keeps
`start`, `end`, `character_id`, name, confidence, `match_status`,
`candidate_margin`, `needs_review`, and up to four alternative candidates.

The player fetches this endpoint beside the VTT and renders the character name
as a separate optional badge above the subtitle. It never inserts `<v ...>` or
`[Character]` into the VTT. The button `Ẩn/Hiện tên nhân vật` only controls the
badge and is remembered per browser; the API mapping remains available either
way.

After changing a report, regenerate the JSON, run `npm run check`, and deploy
the Worker so the static mapping asset and API endpoint are updated together.

## Reviewing unresolved cues

Build the local review dashboard from the 1,812 remaining evidence cues:

```powershell
python scripts/build-cue-review-dashboard.py `
  --evidence-root .\content\generated-subtitles\video-evidence\batch-review-remaining `
  --output .\content\generated-subtitles\video-evidence\batch-review-remaining\review-dashboard.html
```

Open it through a local static server so the browser can read the MP4/JPG
files (run this from the project root):

```powershell
python -m http.server 8765
```

Then open
`http://127.0.0.1:8765/content/generated-subtitles/video-evidence/batch-review-remaining/review-dashboard.html`.

Run the full technical evidence check (it uses `ffprobe`, does not claim that
the human has listened to the cue, and returns one report for audit):

```powershell
python scripts/check-cue-evidence.py `
  --evidence-root .\content\generated-subtitles\video-evidence\batch-review-remaining `
  --reports-dir .\content\generated-subtitles `
  --output .\content\generated-subtitles\video-evidence\batch-review-remaining\technical-validation.json
```

Each card opens the source video, seeks to the cue start, stops at the cue end,
shows the midpoint frame and lists the main/alternative candidates with score
and margin. Decisions are saved locally in the browser and can be exported as
`cue-review-decisions.json`; they are not considered confirmed until imported
and audited against the report.

Apply an exported file only after the four checks are complete. The default is
validation-only; add `--apply` to write the named reports:

```powershell
python scripts/apply-cue-review-decisions.py `
  --reports-dir .\content\generated-subtitles `
  --decisions .\cue-review-decisions.json

python scripts/apply-cue-review-decisions.py `
  --reports-dir .\content\generated-subtitles `
  --decisions .\cue-review-decisions.json `
  --apply
```

Confirmed rows become `manual_audio_video_confirmed` and leave
`needs_review`. Unresolved rows remain review-required. Regenerate the web
mapping afterward; the VTT itself is not modified.
