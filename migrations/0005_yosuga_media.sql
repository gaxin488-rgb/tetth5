-- Register the twelve browser-ready MP4 files and their Vietnamese VTT tracks.
-- The video files remain separate from the subtitles so playback quality is unchanged.
UPDATE episodes SET video_key='movies/yosuga-no-sora/episode-01-720p.mp4', video_url='', updated_at=datetime('now')
WHERE movie_id=(SELECT id FROM movies WHERE slug='yosuga-no-sora') AND season_number=1 AND episode_number=1;
UPDATE episodes SET video_key='movies/yosuga-no-sora/episode-02-720p.mp4', video_url='', updated_at=datetime('now')
WHERE movie_id=(SELECT id FROM movies WHERE slug='yosuga-no-sora') AND season_number=1 AND episode_number=2;
UPDATE episodes SET video_key='movies/yosuga-no-sora/episode-03-720p.mp4', video_url='', updated_at=datetime('now')
WHERE movie_id=(SELECT id FROM movies WHERE slug='yosuga-no-sora') AND season_number=1 AND episode_number=3;
UPDATE episodes SET video_key='movies/yosuga-no-sora/episode-04-720p.mp4', video_url='', updated_at=datetime('now')
WHERE movie_id=(SELECT id FROM movies WHERE slug='yosuga-no-sora') AND season_number=1 AND episode_number=4;
UPDATE episodes SET video_key='movies/yosuga-no-sora/episode-05-720p.mp4', video_url='', updated_at=datetime('now')
WHERE movie_id=(SELECT id FROM movies WHERE slug='yosuga-no-sora') AND season_number=1 AND episode_number=5;
UPDATE episodes SET video_key='movies/yosuga-no-sora/episode-06-720p.mp4', video_url='', updated_at=datetime('now')
WHERE movie_id=(SELECT id FROM movies WHERE slug='yosuga-no-sora') AND season_number=1 AND episode_number=6;
UPDATE episodes SET video_key='movies/yosuga-no-sora/episode-07-720p.mp4', video_url='', updated_at=datetime('now')
WHERE movie_id=(SELECT id FROM movies WHERE slug='yosuga-no-sora') AND season_number=1 AND episode_number=7;
UPDATE episodes SET video_key='movies/yosuga-no-sora/episode-08-720p.mp4', video_url='', updated_at=datetime('now')
WHERE movie_id=(SELECT id FROM movies WHERE slug='yosuga-no-sora') AND season_number=1 AND episode_number=8;
UPDATE episodes SET video_key='movies/yosuga-no-sora/episode-09-720p.mp4', video_url='', updated_at=datetime('now')
WHERE movie_id=(SELECT id FROM movies WHERE slug='yosuga-no-sora') AND season_number=1 AND episode_number=9;
UPDATE episodes SET video_key='movies/yosuga-no-sora/episode-10-720p.mp4', video_url='', updated_at=datetime('now')
WHERE movie_id=(SELECT id FROM movies WHERE slug='yosuga-no-sora') AND season_number=1 AND episode_number=10;
UPDATE episodes SET video_key='movies/yosuga-no-sora/episode-11-720p.mp4', video_url='', updated_at=datetime('now')
WHERE movie_id=(SELECT id FROM movies WHERE slug='yosuga-no-sora') AND season_number=1 AND episode_number=11;
UPDATE episodes SET video_key='movies/yosuga-no-sora/episode-12-720p.mp4', video_url='', updated_at=datetime('now')
WHERE movie_id=(SELECT id FROM movies WHERE slug='yosuga-no-sora') AND season_number=1 AND episode_number=12;

DELETE FROM subtitles
WHERE movie_id=(SELECT id FROM movies WHERE slug='yosuga-no-sora') AND episode_id IS NOT NULL AND language_code='vi';

INSERT INTO subtitles(movie_id,episode_id,language_code,label,r2_key)
SELECT m.id,e.id,'vi','Tiếng Việt','subtitles/yosuga-no-sora/episode-' || printf('%02d',e.episode_number) || '.vi.vtt'
FROM movies m JOIN episodes e ON e.movie_id=m.id
WHERE m.slug='yosuga-no-sora' AND e.season_number=1 AND e.episode_number BETWEEN 1 AND 12;
