import { access } from 'node:fs/promises';
import { join, resolve } from 'node:path';
import { spawn } from 'node:child_process';
import { fileURLToPath } from 'node:url';

const root = resolve(fileURLToPath(new URL('..', import.meta.url)));
const pipeline = join(root, 'scripts', 'auto-sub-local.py');

async function exists(path) {
  try {
    await access(path);
    return true;
  } catch {
    return false;
  }
}

async function findPython() {
  if (process.env.SUBTITLE_PYTHON) {
    return { command: process.env.SUBTITLE_PYTHON, args: [] };
  }

  const venvPython = join(root, '.venv-subtitles', process.platform === 'win32' ? 'Scripts' : 'bin', process.platform === 'win32' ? 'python.exe' : 'python');
  if (await exists(venvPython)) {
    return { command: venvPython, args: [] };
  }

  if (process.platform === 'win32') {
    return { command: process.env.PYTHON_LAUNCHER || 'py.exe', args: ['-3.13'] };
  }
  return { command: process.env.PYTHON_BIN || 'python3', args: [] };
}

function run(command, args) {
  return new Promise((resolvePromise, reject) => {
    const child = spawn(command, args, {
      cwd: root,
      env: { ...process.env, PYTHONIOENCODING: process.env.PYTHONIOENCODING || 'utf-8', PYTHONUTF8: '1' },
      stdio: 'inherit'
    });
    child.on('error', reject);
    child.on('close', code => resolvePromise(code ?? 1));
  });
}

async function main() {
  const python = await findPython();
  const code = await run(python.command, [...python.args, '-u', pipeline, ...process.argv.slice(2)]);
  if (code !== 0) process.exitCode = code;
}

main().catch(error => {
  console.error(`Không thể chạy Python pipeline local: ${error.message}`);
  console.error('Hãy chạy: .\\scripts\\setup-free-subtitles.ps1');
  process.exitCode = 1;
});
