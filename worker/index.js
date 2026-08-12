const JSON_HEADERS = { 'content-type': 'application/json; charset=utf-8', 'cache-control': 'no-store' };
const json = (data, status = 200, headers = {}) => new Response(JSON.stringify(data), { status, headers: { ...JSON_HEADERS, ...headers } });

export default {
  async fetch(request, env, ctx) {
    const url = new URL(request.url);
    try {
      if (url.pathname === '/health' || url.pathname === '/api/health') {
        return json({ ok: true, service: 'cinezero', database: Boolean(env.DB), storage: Boolean(env.VIDEO_BUCKET), time: new Date().toISOString() });
      }
      if (url.pathname.startsWith('/media/')) return serveMedia(request, env, url.pathname.slice(7));
      if (url.pathname.startsWith('/api/')) return handleApi(request, env, ctx, url);
      return env.ASSETS.fetch(request);
    } catch (error) {
      console.error(error);
      return json({ error: 'internal_error', message: error instanceof Error ? error.message : String(error) }, 500);
    }
  }
};

async function handleApi(request, env, ctx, url) {
  const method = request.method.toUpperCase();
  if (url.pathname === '/api/movies' && method === 'GET') return listMovies(env, url);
  const characterMappingMatch = url.pathname.match(/^\/api\/movies\/([^/]+)\/character-mapping$/);
  if (characterMappingMatch && method === 'GET') {
    return getCharacterMapping(env, decodeURIComponent(characterMappingMatch[1]), url);
  }
  const subtitleMatch = url.pathname.match(/^\/api\/movies\/([^/]+)\/subtitles$/);
  if (subtitleMatch && method === 'GET') return listSubtitles(env, decodeURIComponent(subtitleMatch[1]), url);
  if (url.pathname.startsWith('/api/movies/') && method === 'GET') return getMovie(env, decodeURIComponent(url.pathname.slice('/api/movies/'.length)));
  if (url.pathname === '/api/view' && method === 'POST') return recordView(request, env, ctx);

  if (url.pathname.startsWith('/api/admin/')) {
    const denied = authorize(request, env);
    if (denied) return denied;
    if (url.pathname === '/api/admin/storage' && method === 'GET') return adminStorage(env, url);
    if (!env.DB) return json({ error: 'D1 chưa được cấu hình. Hãy dùng wrangler.full.jsonc.' }, 503);
    if (url.pathname === '/api/admin/movies' && method === 'GET') return adminListMovies(env);
    if (url.pathname === '/api/admin/movies' && method === 'POST') return createMovie(request, env);
    if (url.pathname === '/api/admin/subtitles' && method === 'POST') return registerSubtitle(request, env);
    if (url.pathname === '/api/admin/subtitles' && method === 'GET') return adminListSubtitles(env, url);
    const subtitleIdMatch = url.pathname.match(/^\/api\/admin\/subtitles\/(\d+)$/);
    if (subtitleIdMatch && method === 'DELETE') return deleteSubtitle(env, Number(subtitleIdMatch[1]));
    const match = url.pathname.match(/^\/api\/admin\/movies\/(\d+)$/);
    if (match && method === 'PUT') return updateMovie(request, env, Number(match[1]));
    if (match && method === 'DELETE') return deleteMovie(env, Number(match[1]));
    if (url.pathname.startsWith('/api/admin/assets/') && method === 'PUT') return uploadSmallAsset(request, env, decodeURIComponent(url.pathname.slice('/api/admin/assets/'.length)));
  }
  return json({ error: 'not_found' }, 404);
}

function authorize(request, env) {
  if (!env.ADMIN_TOKEN) return json({ error: 'ADMIN_TOKEN chưa được thiết lập.' }, 503);
  const value = request.headers.get('authorization') || '';
  return value === `Bearer ${env.ADMIN_TOKEN}` ? null : json({ error: 'unauthorized' }, 401);
}

async function fallbackMovies(env) {
  const response = await env.ASSETS.fetch(new Request('https://assets.local/data/movies.json'));
  return response.json();
}

async function listMovies(env, url) {
  if (!env.DB) {
    let data = await fallbackMovies(env);
    const q = (url.searchParams.get('q') || '').trim().toLowerCase();
    const type = url.searchParams.get('type');
    if (q) data = data.filter(m => [m.title, m.original_title, m.country, ...(m.genres || [])].join(' ').toLowerCase().includes(q));
    if (type && type !== 'all') data = data.filter(m => m.type === type);
    return json({ movies: data, source: 'sample' }, 200, { 'cache-control': 'public, max-age=60' });
  }
  const q = (url.searchParams.get('q') || '').trim();
  const type = url.searchParams.get('type');
  const params = []; const where = ["m.status = 'published'"];
  if (q) { where.push('(m.title LIKE ? OR m.original_title LIKE ? OR m.description LIKE ?)'); params.push(`%${q}%`, `%${q}%`, `%${q}%`); }
  if (type && type !== 'all') { where.push('m.type = ?'); params.push(type); }
  const sql = `SELECT m.*, (SELECT GROUP_CONCAT(g.name, '||') FROM movie_genres mg JOIN genres g ON g.id=mg.genre_id WHERE mg.movie_id=m.id) AS genres, COUNT(DISTINCT e.id) AS episode_count
    FROM movies m LEFT JOIN episodes e ON e.movie_id=m.id AND e.status='published'
    WHERE ${where.join(' AND ')} GROUP BY m.id ORDER BY m.featured DESC, m.updated_at DESC`;
  const result = await env.DB.prepare(sql).bind(...params).all();
  return json({ movies: result.results || [], source: 'd1' }, 200, { 'cache-control': 'public, max-age=30' });
}

async function getMovie(env, slug) {
  if (!env.DB) {
    const data = await fallbackMovies(env); const movie = data.find(m => m.slug === slug);
    return movie ? json({ ...movie, character_mapping_url: characterMappingUrl(slug) }) : json({ error: 'movie_not_found' }, 404);
  }
  const movie = await env.DB.prepare(`SELECT m.*, (SELECT GROUP_CONCAT(g.name, '||') FROM movie_genres mg JOIN genres g ON g.id=mg.genre_id WHERE mg.movie_id=m.id) AS genres FROM movies m WHERE m.slug=? AND m.status='published'`).bind(slug).first();
  if (!movie) return json({ error: 'movie_not_found' }, 404);
  const episodes = await env.DB.prepare(`SELECT id, season_number, episode_number, title, duration_minutes, video_key, video_url FROM episodes WHERE movie_id=? AND status='published' ORDER BY season_number, episode_number`).bind(movie.id).all();
  return json({ ...movie, character_mapping_url: characterMappingUrl(slug), episodes: episodes.results || [] });
}

async function getCharacterMapping(env, slug, url) {
  if (!env.ASSETS) return json({ error: 'character_mapping_unavailable' }, 503);
  const assetPath = `/data/character-mappings/${encodeURIComponent(slug)}.json`;
  const response = await env.ASSETS.fetch(new Request(`https://assets.local${assetPath}`));
  if (!response.ok || !String(response.headers.get('content-type') || '').includes('application/json')) {
    return json({ error: 'character_mapping_not_found' }, 404);
  }
  let payload;
  try {
    payload = await response.json();
  } catch {
    return json({ error: 'character_mapping_invalid' }, 502);
  }
  const episodeNumber = Number(url.searchParams.get('episode') || 0);
  const episodeKey = episodeNumber > 0 ? String(episodeNumber).padStart(2, '0') : '';
  const selectedEpisode = episodeKey ? (payload.episodes || {})[episodeKey] : null;
  const { episodes: _episodes, ...base } = payload;
  return json({
    ...base,
    episode: selectedEpisode || null,
    requested_episode: episodeNumber || null,
    source: 'assets'
  }, 200, { 'cache-control': 'public, max-age=60' });
}

async function listSubtitles(env, slug, url) {
  if (!env.DB) return json({ subtitles: [], character_mapping_url: characterMappingUrl(slug), source: 'sample' }, 200, { 'cache-control': 'public, max-age=60' });
  const episodeNumber = Number(url.searchParams.get('episode') || 0);
  const params = [slug];
  const episodeFilter = episodeNumber > 0
    ? ` AND (s.episode_id IS NULL OR s.episode_id=(SELECT e.id FROM episodes e WHERE e.movie_id=m.id AND e.season_number=1 AND e.episode_number=?))`
    : '';
  if (episodeNumber > 0) params.push(episodeNumber);
  const result = await env.DB.prepare(`SELECT s.id,s.language_code,s.label,s.r2_key,s.episode_id
    FROM subtitles s JOIN movies m ON m.id=s.movie_id
    WHERE m.slug=? AND m.status='published'${episodeFilter}
    ORDER BY s.episode_id IS NOT NULL, s.language_code, s.id`).bind(...params).all();
  const subtitles = (result.results || []).map(row => ({
    ...row,
    kind: 'subtitles',
    format: 'vtt',
    url: mediaUrl(row.r2_key)
  }));
  return json({ subtitles, character_mapping_url: characterMappingUrl(slug), source: 'd1' }, 200, { 'cache-control': 'public, max-age=30' });
}

async function recordView(request, env, ctx) {
  if (!env.DB) return json({ ok: true, stored: false });
  const body = await readJson(request); const slug = String(body.slug || '').slice(0, 180);
  if (!slug) return json({ error: 'slug_required' }, 400);
  ctx.waitUntil(env.DB.prepare(`INSERT INTO view_events(movie_id, viewed_at) SELECT id, datetime('now') FROM movies WHERE slug=?`).bind(slug).run());
  return json({ ok: true, stored: true }, 202);
}

async function adminListMovies(env) {
  const result = await env.DB.prepare(`SELECT id,slug,title,status,type,release_year,poster_url,updated_at FROM movies ORDER BY updated_at DESC`).all();
  return json({ movies: result.results || [] });
}

async function adminListSubtitles(env, url) {
  const movieId = Number(url.searchParams.get('movie_id') || 0);
  const query = movieId
    ? env.DB.prepare(`SELECT s.id,s.movie_id,s.episode_id,s.language_code,s.label,s.r2_key,m.title
      FROM subtitles s JOIN movies m ON m.id=s.movie_id WHERE s.movie_id=? ORDER BY s.id DESC`).bind(movieId)
    : env.DB.prepare(`SELECT s.id,s.movie_id,s.episode_id,s.language_code,s.label,s.r2_key,m.title
      FROM subtitles s JOIN movies m ON m.id=s.movie_id ORDER BY s.id DESC LIMIT 200`);
  const result = await query.all();
  return json({ subtitles: result.results || [] });
}

async function registerSubtitle(request, env) {
  const b = await readJson(request);
  const movieId = await resolveMovieId(env, b);
  const episodeId = emptyInt(b.episode_id);
  const language = String(b.language_code || '').trim().slice(0, 20);
  const label = String(b.label || language || 'Phụ đề').trim().slice(0, 100);
  const key = emptyNull(b.r2_key);
  if (!movieId || !language || !key || key.includes('..') || key.startsWith('/')) return json({ error: 'movie_id/slug, language_code và r2_key là bắt buộc' }, 400);
  const movie = await env.DB.prepare('SELECT id FROM movies WHERE id=?').bind(movieId).first();
  if (!movie) return json({ error: 'movie_not_found' }, 404);
  if (episodeId) {
    const episode = await env.DB.prepare('SELECT id FROM episodes WHERE id=? AND movie_id=?').bind(episodeId, movieId).first();
    if (!episode) return json({ error: 'episode_not_found' }, 404);
  }
  const result = await env.DB.prepare(`INSERT INTO subtitles(movie_id,episode_id,language_code,label,r2_key) VALUES(?,?,?,?,?)`).bind(movieId, episodeId, language, label, key).run();
  return json({ ok: true, id: result.meta.last_row_id, url: mediaUrl(key) }, 201);
}

async function deleteSubtitle(env, id) {
  await env.DB.prepare('DELETE FROM subtitles WHERE id=?').bind(id).run();
  return json({ ok: true });
}

async function resolveMovieId(env, body) {
  const id = emptyInt(body.movie_id);
  if (id) return id;
  const slug = String(body.slug || '').trim().slice(0, 180);
  if (!slug) return 0;
  const movie = await env.DB.prepare('SELECT id FROM movies WHERE slug=?').bind(slug).first();
  return movie ? Number(movie.id) : 0;
}

async function createMovie(request, env) {
  const b = normalizeMovie(await readJson(request));
  if (!b.title || !b.slug || !b.description) return json({ error: 'title, slug và description là bắt buộc' }, 400);
  const result = await env.DB.prepare(`INSERT INTO movies(slug,title,original_title,type,description,release_year,duration_minutes,status,quality,age_rating,country,poster_url,backdrop_url,video_key,video_url,featured,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,datetime('now'),datetime('now'))`).bind(
    b.slug,b.title,b.original_title,b.type,b.description,b.release_year,b.duration_minutes,b.status,b.quality,b.age_rating,b.country,b.poster_url,b.backdrop_url,b.video_key,b.video_url,b.featured
  ).run();
  await syncGenres(env, Number(result.meta.last_row_id), b.genres);
  return json({ ok: true, id: result.meta.last_row_id }, 201);
}

async function updateMovie(request, env, id) {
  const b = normalizeMovie(await readJson(request));
  await env.DB.prepare(`UPDATE movies SET slug=?,title=?,original_title=?,type=?,description=?,release_year=?,duration_minutes=?,status=?,quality=?,age_rating=?,country=?,poster_url=?,backdrop_url=?,video_key=?,video_url=?,featured=?,updated_at=datetime('now') WHERE id=?`).bind(
    b.slug,b.title,b.original_title,b.type,b.description,b.release_year,b.duration_minutes,b.status,b.quality,b.age_rating,b.country,b.poster_url,b.backdrop_url,b.video_key,b.video_url,b.featured,id
  ).run();
  await syncGenres(env, id, b.genres);
  return json({ ok: true });
}

async function deleteMovie(env, id) {
  await env.DB.batch([
    env.DB.prepare('DELETE FROM movie_genres WHERE movie_id=?').bind(id),
    env.DB.prepare('DELETE FROM episodes WHERE movie_id=?').bind(id),
    env.DB.prepare('DELETE FROM movies WHERE id=?').bind(id)
  ]);
  return json({ ok: true });
}

async function syncGenres(env, movieId, genres) {
  await env.DB.prepare('DELETE FROM movie_genres WHERE movie_id=?').bind(movieId).run();
  for (const name of genres.slice(0, 12)) {
    await env.DB.prepare('INSERT OR IGNORE INTO genres(name,slug) VALUES(?,?)').bind(name, slugify(name)).run();
    await env.DB.prepare('INSERT OR IGNORE INTO movie_genres(movie_id,genre_id) SELECT ?,id FROM genres WHERE name=?').bind(movieId,name).run();
  }
}

async function uploadSmallAsset(request, env, key) {
  if (!env.VIDEO_BUCKET) return json({ error: 'R2 chưa được cấu hình' }, 503);
  if (!key || key.includes('..') || key.startsWith('/')) return json({ error: 'invalid_key' }, 400);
  const limit = Math.min(Number(env.ADMIN_UPLOAD_LIMIT_MB || 95), 95) * 1024 * 1024;
  const length = Number(request.headers.get('content-length') || 0);
  if (length > limit) return json({ error: 'file_too_large', limit_bytes: limit }, 413);
  const storageLimit = storageLimitBytes(env);
  const currentUsage = await bucketUsage(env.VIDEO_BUCKET);
  const previousObject = await env.VIDEO_BUCKET.head(key);
  const projectedUsage = currentUsage - Number(previousObject?.size || 0) + length;
  if (projectedUsage > storageLimit) {
    return json({ error: 'storage_limit', limit_bytes: storageLimit, current_bytes: currentUsage, projected_bytes: projectedUsage }, 413);
  }
  const isCaption = /\.(vtt|srt)$/i.test(key);
  await env.VIDEO_BUCKET.put(key, request.body, { httpMetadata: { contentType: request.headers.get('content-type') || 'application/octet-stream', cacheControl: isCaption ? 'public, max-age=60' : 'public, max-age=31536000, immutable' } });
  return json({ ok: true, key, url: `/media/${key}` }, 201);
}

async function adminStorage(env, url) {
  if (!env.VIDEO_BUCKET) return json({ error: 'R2 chưa được cấu hình' }, 503);
  const key = String(url.searchParams.get('key') || '').trim();
  const usage = await bucketUsage(env.VIDEO_BUCKET);
  const existing = key ? await env.VIDEO_BUCKET.head(key) : null;
  const currentBytes = Math.max(0, usage - Number(existing?.size || 0));
  const limitBytes = storageLimitBytes(env);
  return json({
    ok: true,
    bytes: currentBytes,
    gb: Number((currentBytes / 1024 ** 3).toFixed(3)),
    limit_bytes: limitBytes,
    limit_gb: Number((limitBytes / 1024 ** 3).toFixed(3)),
    remaining_bytes: Math.max(0, limitBytes - currentBytes),
    key: key || null
  });
}

async function bucketUsage(bucket) {
  let total = 0;
  let cursor;
  do {
    const page = await bucket.list(cursor ? { cursor, limit: 1000 } : { limit: 1000 });
    for (const object of page.objects || []) total += Number(object.size || 0);
    cursor = page.truncated && page.cursor ? page.cursor : null;
  } while (cursor);
  return total;
}

function storageLimitBytes(env) {
  const configuredGb = Number(env.R2_STORAGE_LIMIT_GB || 9);
  const safeGb = Math.min(9, Math.max(0.1, Number.isFinite(configuredGb) ? configuredGb : 9));
  return Math.floor(safeGb * 1024 ** 3);
}

async function serveMedia(request, env, rawKey) {
  if (!env.VIDEO_BUCKET) return json({ error: 'R2 chưa được cấu hình' }, 503);
  const key = rawKey.split('/').map(decodeURIComponent).join('/');
  if (!key || key.includes('..')) return json({ error: 'invalid_key' }, 400);
  const isCaption = /\.(vtt|srt)$/i.test(key);
  // Browsers parse WebVTT more reliably as one complete 200 response.
  // Keep byte-range support for video, where seeking depends on it.
  const object = !isCaption
    ? await env.VIDEO_BUCKET.get(key, { range: request.headers })
    : await env.VIDEO_BUCKET.get(key);
  if (!object) return json({ error: 'media_not_found' }, 404);
  const headers = new Headers(); object.writeHttpMetadata(headers); headers.set('etag', object.httpEtag); headers.set('accept-ranges','bytes');
  headers.set('cache-control', isCaption ? 'no-cache, must-revalidate' : (headers.get('cache-control') || 'public, max-age=86400'));
  if (isCaption) {
    const text = stripSubtitleSpeakerLabels(await object.text());
    headers.set('content-type', key.toLowerCase().endsWith('.vtt') ? 'text/vtt; charset=utf-8' : 'text/plain; charset=utf-8');
    headers.set('content-length', String(new TextEncoder().encode(text).byteLength));
    return new Response(request.method === 'HEAD' ? null : text, { status: 200, headers });
  }
  let status = 200;
  if (!isCaption && object.range && typeof object.range.offset === 'number') {
    status = 206; const end = object.range.offset + object.range.length - 1;
    headers.set('content-range', `bytes ${object.range.offset}-${end}/${object.size}`);
    headers.set('content-length', String(object.range.length));
  } else headers.set('content-length', String(object.size));
  return new Response(request.method === 'HEAD' ? null : object.body, { status, headers });
}

function stripSubtitleSpeakerLabels(value) {
  return String(value || '')
    .replace(/<v\b[^>]*>/gi, '')
    .replace(/<\/v>/gi, '')
    .replace(/^\s*\[[^\]\r\n]{1,120}\]\s*/gm, '');
}

function mediaUrl(key) {
  return `/media/${String(key || '').split('/').map(encodeURIComponent).join('/')}`;
}

function characterMappingUrl(slug) {
  return `/api/movies/${encodeURIComponent(String(slug || ''))}/character-mapping`;
}

function normalizeMovie(b) {
  return {
    slug: String(b.slug || '').trim().slice(0,180), title: String(b.title || '').trim().slice(0,250), original_title: String(b.original_title || '').trim().slice(0,250),
    type: b.type === 'series' ? 'series' : 'movie', description: String(b.description || '').trim().slice(0,5000), release_year: clampInt(b.release_year,1888,2200,2026),
    duration_minutes: clampInt(b.duration_minutes,1,10000,90), status: b.status === 'draft' ? 'draft' : 'published', quality: String(b.quality || 'HD').slice(0,30),
    age_rating: String(b.age_rating || 'T13').slice(0,20), country: String(b.country || 'Việt Nam').slice(0,100), poster_url: String(b.poster_url || '/assets/posters/hanh-trinh-sao-bang.svg').slice(0,1000),
    backdrop_url: String(b.backdrop_url || '/assets/backdrops/hanh-trinh-sao-bang.svg').slice(0,1000), video_key: emptyNull(b.video_key), video_url: emptyNull(b.video_url),
    featured: b.featured ? 1 : 0, genres: Array.isArray(b.genres) ? b.genres.map(x=>String(x).trim()).filter(Boolean) : []
  };
}
function emptyNull(v){const x=String(v||'').trim();return x?x.slice(0,1000):null}
function clampInt(v,min,max,fallback){const n=Number.parseInt(v,10);return Number.isFinite(n)?Math.min(max,Math.max(min,n)):fallback}
function emptyInt(v){const n=Number.parseInt(v,10);return Number.isFinite(n)&&n>0?n:null}
function slugify(v){return String(v).normalize('NFD').replace(/[\u0300-\u036f]/g,'').toLowerCase().replace(/đ/g,'d').replace(/[^a-z0-9]+/g,'-').replace(/^-|-$/g,'')}
async function readJson(request){try{return await request.json()}catch{return {}}}
