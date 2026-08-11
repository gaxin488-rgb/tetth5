-- Add the Yosuga no Sora catalog entry and keep its tags in D1.
ALTER TABLE movies ADD COLUMN tags TEXT NOT NULL DEFAULT '';

INSERT OR IGNORE INTO movies(
  slug,title,original_title,type,description,release_year,duration_minutes,status,quality,age_rating,country,tags,poster_url,backdrop_url,video_key,video_url,featured
) VALUES(
  'yosuga-no-sora',
  'Yosuga no Sora',
  'Yosuga no Sora: In Solitude When We Are Least Alone (ヨスガノソラ)',
  'series',
  'Sau khi cha mẹ qua đời trong một vụ tai nạn, Haruka Kasugano và cô em gái sinh đôi Sora mất đi chỗ dựa duy nhất. Hai anh em quyết định rời thành phố và chuyển đến một thị trấn vùng quê, nơi họ từng trải qua những mùa hè bên người ông quá cố. Ban đầu, mọi thứ nơi đây vẫn quen thuộc và yên bình. Tuy nhiên, Haruka dần nhớ lại những ký ức từ thời thơ ấu. Những ký ức tưởng như đã bị lãng quên ấy bắt đầu làm thay đổi cuộc sống của hai anh em.',
  2010,25,'published','HD','T18','Nhật Bản',
  'Loạn luân||Vũ trụ thay thế||Sinh đôi||Dàn nhân vật||Harem',
  '/assets/posters/yosuga-no-sora.svg','/assets/backdrops/yosuga-no-sora.svg',NULL,'',0
);

UPDATE movies SET
  title='Yosuga no Sora',
  original_title='Yosuga no Sora: In Solitude When We Are Least Alone (ヨスガノソラ)',
  type='series',
  description='Sau khi cha mẹ qua đời trong một vụ tai nạn, Haruka Kasugano và cô em gái sinh đôi Sora mất đi chỗ dựa duy nhất. Hai anh em quyết định rời thành phố và chuyển đến một thị trấn vùng quê, nơi họ từng trải qua những mùa hè bên người ông quá cố. Ban đầu, mọi thứ nơi đây vẫn quen thuộc và yên bình. Tuy nhiên, Haruka dần nhớ lại những ký ức từ thời thơ ấu. Những ký ức tưởng như đã bị lãng quên ấy bắt đầu làm thay đổi cuộc sống của hai anh em.',
  release_year=2010,duration_minutes=25,status='published',quality='HD',age_rating='T18',country='Nhật Bản',
  tags='Loạn luân||Vũ trụ thay thế||Sinh đôi||Dàn nhân vật||Harem',
  poster_url='/assets/posters/yosuga-no-sora.svg',backdrop_url='/assets/backdrops/yosuga-no-sora.svg',video_key=NULL,video_url='',featured=0,updated_at=datetime('now')
WHERE slug='yosuga-no-sora';

INSERT OR IGNORE INTO genres(name,slug) VALUES
  ('Drama','drama'),('Ecchi','ecchi'),('Lãng mạn','lang-man'),('Harem','harem');

INSERT OR IGNORE INTO movie_genres(movie_id,genre_id)
SELECT m.id,g.id FROM movies m JOIN genres g ON g.name IN ('Drama','Ecchi','Lãng mạn','Harem')
WHERE m.slug='yosuga-no-sora';

WITH RECURSIVE episode_numbers(number) AS (
  SELECT 1
  UNION ALL
  SELECT number + 1 FROM episode_numbers WHERE number < 12
)
INSERT OR IGNORE INTO episodes(movie_id,season_number,episode_number,title,duration_minutes,status,video_key,video_url)
SELECT m.id,1,episode_numbers.number,printf('Tập %02d', episode_numbers.number),25,'published',NULL,''
FROM movies m CROSS JOIN episode_numbers
WHERE m.slug='yosuga-no-sora';
