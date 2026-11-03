#!/usr/bin/env node
/**
 * logic-agent-mcp - npm launcher for the bundled Python MCP server.
 *
 * Contract: this process must never write to stdout. The MCP client owns that
 * byte stream, so the Python child gets the client's real stdin/stdout via
 * stdio "inherit": JSON-RPC frames are neither buffered nor re-encoded here.
 * Diagnostics go to stderr only.
 *
 * Layout expected next to this file (the portable bundle layout):
 *   <package>/runtime/python/python.exe
 *   <package>/runtime/app/logic_agent/...
 *   <package>/runtime/bin/{dslab.exe,sigrok-cli.exe,res/}
 *   <package>/runtime/decoders/
 */
import { spawn } from 'node:child_process';
import { existsSync } from 'node:fs';
import { homedir } from 'node:os';
import { delimiter, dirname, join } from 'node:path';
import { fileURLToPath } from 'node:url';

const packageRoot = dirname(dirname(fileURLToPath(import.meta.url)));
const runtimeDir = join(packageRoot, 'runtime');
const appDir = join(runtimeDir, 'app');
const binDir = join(runtimeDir, 'bin');
const resDir = join(binDir, 'res');
const decoderDir = join(runtimeDir, 'decoders');

const pythonCandidates =
  process.platform === 'win32'
    ? [join(runtimeDir, 'python', 'python.exe')]
    : [join(runtimeDir, 'python', 'bin', 'python3'), join(runtimeDir, 'python', 'bin', 'python')];
const bundledPython = pythonCandidates.find((candidate) => existsSync(candidate));
const python =
  process.env.LOGIC_AGENT_PYTHON ||
  bundledPython ||
  (process.platform === 'win32' ? 'python' : 'python3');

/** Per-user data directory; the server creates <data-dir>/captures inside it. */
function defaultDataDir() {
  if (process.platform === 'win32') {
    const base = process.env.LOCALAPPDATA || join(homedir(), 'AppData', 'Local');
    return join(base, 'logic-agent');
  }
  const base = process.env.XDG_DATA_HOME || join(homedir(), '.local', 'share');
  return join(base, 'logic-agent');
}

const forwarded = process.argv.slice(2);
const dataDirGiven =
  forwarded.includes('--data-dir') ||
  forwarded.some((arg) => arg.startsWith('--data-dir=')) ||
  Boolean(process.env.LOGIC_AGENT_DATA_DIR);

const args = ['-m', 'logic_agent.mcp_server'];
if (!dataDirGiven) args.push('--data-dir', defaultDataDir());
args.push(...forwarded);

const env = { ...process.env, PYTHONDONTWRITEBYTECODE: '1' };
// Same wiring as packaging/runtime/logic-agent-mcp.cmd, but only what exists.
if (!env.DSLAB_EXE && existsSync(join(binDir, 'dslab.exe'))) env.DSLAB_EXE = join(binDir, 'dslab.exe');
if (!env.DSLAB_RES_DIR && existsSync(resDir)) env.DSLAB_RES_DIR = resDir;
if (!env.LOGIC_AGENT_DECODER_EXE && existsSync(join(binDir, 'sigrok-cli.exe'))) {
  env.LOGIC_AGENT_DECODER_EXE = join(binDir, 'sigrok-cli.exe');
}
if (!env.SIGROKDECODE_DIR && existsSync(decoderDir)) env.SIGROKDECODE_DIR = decoderDir;
// Embedded Python reads its own ._pth and ignores PYTHONPATH; this only matters
// for the system-Python fallback (mock backend / development checkouts).
if (existsSync(appDir)) {
  env.PYTHONPATH = env.PYTHONPATH ? `${appDir}${delimiter}${env.PYTHONPATH}` : appDir;
}

const hint =
  `logic-agent-mcp: cannot start the Python server.\n` +
  `  interpreter: ${python}\n` +
  `  package:     ${packageRoot}\n` +
  `Set LOGIC_AGENT_PYTHON to a Python >= 3.11, or reinstall so runtime/python is present.\n`;

let failed = false;
const child = spawn(python, args, { env, stdio: 'inherit', windowsHide: true });

child.on('error', (error) => {
  failed = true;
  process.stderr.write(`${hint}  reason:      ${error.message}\n`);
  process.exit(127);
});

child.on('exit', (code, signal) => {
  if (failed) return;
  if (signal) {
    try {
      process.kill(process.pid, signal);
      return;
    } catch {
      process.exit(1);
    }
  }
  if (code === 9009 || code === 127) process.stderr.write(hint);
  process.exit(code ?? 1);
});

for (const signal of ['SIGINT', 'SIGTERM']) {
  process.on(signal, () => {
    if (!child.killed) child.kill(signal);
  });
}
