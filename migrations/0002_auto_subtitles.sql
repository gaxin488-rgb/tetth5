CREATE TABLE IF NOT EXISTS subtitle_jobs (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  movie_id INTEGER,
  episode_id INTEGER,
  source_video_key TEXT NOT NULL,
  output_key TEXT,
  status TEXT NOT NULL DEFAULT 'queued' CHECK(status IN ('queued','processing','completed','failed')),
  provider TEXT NOT NULL DEFAULT 'local',
  model TEXT NOT NULL DEFAULT 'whisperx+inaSpeechSegmenter',
  detected_language TEXT,
  filter_mode TEXT NOT NULL DEFAULT 'heuristic',
  has_speaker_labels INTEGER NOT NULL DEFAULT 0,
  error_message TEXT,
  created_at TEXT NOT NULL DEFAULT (datetime('now')),
  updated_at TEXT NOT NULL DEFAULT (datetime('now')),
  FOREIGN KEY(movie_id) REFERENCES movies(id) ON DELETE CASCADE,
  FOREIGN KEY(episode_id) REFERENCES episodes(id) ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS idx_subtitles_movie_episode ON subtitles(movie_id,episode_id,language_code);
CREATE INDEX IF NOT EXISTS idx_subtitle_jobs_status ON subtitle_jobs(status,updated_at DESC);
