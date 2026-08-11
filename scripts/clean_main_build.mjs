import { rmSync } from 'node:fs';
import path from 'node:path';

const root = process.cwd();
const target = path.join(root, 'build', 'main');

if (path.dirname(target) !== path.join(root, 'build')) {
  throw new Error('主进程构建目录解析异常，已停止清理。');
}

rmSync(target, { recursive: true, force: true });
