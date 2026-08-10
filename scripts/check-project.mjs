import { readFile, access } from 'node:fs/promises';
const required = ['public/index.html','public/styles.css','public/app.js','public/data/movies.json','worker/index.js','scripts/auto-sub.mjs','scripts/auto-sub-local.py','scripts/character_rules.py','scripts/test-character-rules.py','scripts/setup-free-subtitles.ps1','requirements-free.txt','config/pronoun-rules.vi.json','profiles/yosuga-no-sora-01.json','migrations/0002_auto_subtitles.sql','wrangler.jsonc','schema.sql','seed.sql'];
let failed=false;
for (const file of required) { try { await access(file); console.log(`PASS ${file}`); } catch { console.error(`MISSING ${file}`); failed=true; } }
try { const data=JSON.parse(await readFile('public/data/movies.json','utf8')); if(!Array.isArray(data)||data.length<1) throw new Error('movies.json rỗng'); console.log(`PASS movies=${data.length}`); } catch(e){console.error(`FAIL movies.json: ${e.message}`);failed=true}
try { await import(new URL('../worker/index.js', import.meta.url)); console.log('PASS worker syntax'); } catch(e){console.error(`FAIL worker syntax: ${e.message}`);failed=true}
if (failed) process.exit(1); console.log('PROJECT_CHECK=PASS');
