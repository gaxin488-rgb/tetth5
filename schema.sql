PRAGMA foreign_keys = ON;
CREATE TABLE IF NOT EXISTS movies (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  slug TEXT NOT NULL UNIQUE,
  title TEXT NOT NULL,
  original_title TEXT DEFAULT '',
  type TEXT NOT NULL DEFAULT 'movie' CHECK(type IN ('movie','series')),
  description TEXT NOT NULL,
  release_year INTEGER,
  duration_minutes INTEGER,
  status TEXT NOT NULL DEFAULT 'draft' CHECK(status IN ('draft','published')),
  quality TEXT DEFAULT 'HD',
  age_rating TEXT DEFAULT 'T13',
  country TEXT DEFAULT '',
  poster_url TEXT NOT NULL,
  backdrop_url TEXT NOT NULL,
  video_key TEXT,
  video_url TEXT,
  featured INTEGER NOT NULL DEFAULT 0,
  created_at TEXT NOT NULL DEFAULT (datetime('now')),
  updated_at TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE TABLE IF NOT EXISTS genres (id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT NOT NULL UNIQUE, slug TEXT NOT NULL UNIQUE);
CREATE TABLE IF NOT EXISTS movie_genres (movie_id INTEGER NOT NULL, genre_id INTEGER NOT NULL, PRIMARY KEY(movie_id,genre_id), FOREIGN KEY(movie_id) REFERENCES movies(id) ON DELETE CASCADE, FOREIGN KEY(genre_id) REFERENCES genres(id) ON DELETE CASCADE);
CREATE TABLE IF NOT EXISTS episodes (
  id INTEGER PRIMARY KEY AUTOINCREMENT, movie_id INTEGER NOT NULL, season_number INTEGER NOT NULL DEFAULT 1, episode_number INTEGER NOT NULL,
  title TEXT NOT NULL, duration_minutes INTEGER, status TEXT NOT NULL DEFAULT 'published', video_key TEXT, video_url TEXT,
  created_at TEXT NOT NULL DEFAULT (datetime('now')), updated_at TEXT NOT NULL DEFAULT (datetime('now')),
  UNIQUE(movie_id,season_number,episode_number), FOREIGN KEY(movie_id) REFERENCES movies(id) ON DELETE CASCADE
);
CREATE TABLE IF NOT EXISTS subtitles (id INTEGER PRIMARY KEY AUTOINCREMENT, movie_id INTEGER, episode_id INTEGER, language_code TEXT NOT NULL, label TEXT NOT NULL, r2_key TEXT NOT NULL, FOREIGN KEY(movie_id) REFERENCES movies(id) ON DELETE CASCADE, FOREIGN KEY(episode_id) REFERENCES episodes(id) ON DELETE CASCADE);
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
CREATE TABLE IF NOT EXISTS view_events (id INTEGER PRIMARY KEY AUTOINCREMENT, movie_id INTEGER NOT NULL, viewed_at TEXT NOT NULL DEFAULT (datetime('now')), FOREIGN KEY(movie_id) REFERENCES movies(id) ON DELETE CASCADE);
CREATE INDEX IF NOT EXISTS idx_movies_status_updated ON movies(status,updated_at DESC);
CREATE INDEX IF NOT EXISTS idx_movies_slug ON movies(slug);
CREATE INDEX IF NOT EXISTS idx_episodes_movie ON episodes(movie_id,season_number,episode_number);
CREATE INDEX IF NOT EXISTS idx_subtitles_movie_episode ON subtitles(movie_id,episode_id,language_code);
CREATE INDEX IF NOT EXISTS idx_subtitle_jobs_status ON subtitle_jobs(status,updated_at DESC);
CREATE INDEX IF NOT EXISTS idx_views_movie_time ON view_events(movie_id,viewed_at DESC);
