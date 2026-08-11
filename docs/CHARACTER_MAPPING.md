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
