import { existsSync } from 'node:fs';
import { dirname, join } from 'node:path';
import { fileURLToPath } from 'node:url';
import { spawn } from 'node:child_process';

const root = dirname(fileURLToPath(import.meta.url));
const candidates = [process.env.PYTHON, join(root, '.venv', process.platform === 'win32' ? 'Scripts/python.exe' : 'bin/python'), process.platform === 'win32' ? 'python' : 'python3'].filter(Boolean);
const python = candidates.find(candidate => candidate.includes('/') || candidate.includes('\\') ? existsSync(candidate) : true);
const args = process.argv.slice(2);
let child;

function run(command, commandArgs, capture = false) {
  return new Promise((resolve, reject) => {
    let output = '';
    child = spawn(command, commandArgs, { cwd: root, windowsHide: true, stdio: capture ? ['inherit', 'pipe', 'inherit'] : 'inherit' });
    if (capture) child.stdout.on('data', chunk => { output += chunk; });
    child.once('error', reject);
    child.once('close', (code, signal) => {
      if (code === 0) resolve(output.trim());
      else reject(new Error(signal ? `启动进程被 ${signal} 中断。` : `启动进程退出（${code ?? 1}）。`));
    });
  });
}

process.on('SIGINT', () => child?.kill('SIGINT'));
process.on('SIGTERM', () => child?.kill('SIGTERM'));

try {
  if (!python) throw new Error('未找到 Python。请先安装 Python 3.11+，或设置 PYTHON 环境变量。');
  if (args.some(arg => !['--check', '--reload'].includes(arg))) throw new Error('支持的启动选项：--check、--reload。');
  const mode = await run(python, [join(root, 'server.py'), '--check-database'], true);
  if (!['local-postgres', 'external-postgres'].includes(mode)) throw new Error('数据库启动检查未返回有效状态。');
  if (args.includes('--check')) {
    console.log(mode === 'local-postgres' ? 'PostgreSQL 配置有效，启动时会使用项目本地实例。' : 'PostgreSQL 配置有效，启动时会使用外部实例。');
  } else {
    if (mode === 'local-postgres') {
      await run(python, [join(root, 'server.py'), '--start-local-database']);
    }
    await run(python, [join(root, 'server.py'), ...args.filter(arg => arg === '--reload')]);
  }
} catch (error) {
  console.error(error.message);
  process.exitCode = 1;
}
