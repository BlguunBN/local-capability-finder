#!/usr/bin/env node
'use strict';

const fs = require('node:fs');
const os = require('node:os');
const path = require('node:path');
const { spawnSync } = require('node:child_process');
const readline = require('node:readline');
const { version } = require('../package.json');

const source = path.resolve(__dirname, '..');
const scripts = ['capability_finder.py', 'capability_mcp.py', 'install_agents.py', 'library_catalog.py'];
const agentNames = ['codex', 'claude', 'gemini', 'antigravity', 'hermes', 'cursor', 'opencode', 'omp'];
const agents = new Set(agentNames);

function usage() {
  console.log(`capfind - search local skills, MCP tools, and plugins

Usage:
  capfind --version
  capfind search "task description" [--kind skill|tool|plugin] [--limit 3] [--json]
  capfind show <exact-id>
  capfind activate <exact-skill-id> --agent <name>
  capfind refresh [--probe-tools]
  capfind add [--agent <name>] [--all] [--dry-run] [--skip-refresh]
  capfind setup [same options as add]
  capfind mcp

add opens an interactive agent picker in a terminal. Use --agent for scripts.
Supported agents: codex, claude, gemini, antigravity, hermes, cursor, opencode, omp.
Python 3.11+ is required. Search reads the local cached index.`);
}

function fail(message) {
  console.error(`capfind: ${message}`);
  process.exitCode = 1;
}

function python() {
  const candidates = process.env.CAPFIND_PYTHON
    ? [[process.env.CAPFIND_PYTHON, []]]
    : process.platform === 'win32'
      ? [['py', ['-3']], ['python', []], ['python3', []]]
      : [['python3', []], ['python', []]];
  for (const [command, prefix] of candidates) {
    const check = spawnSync(command, [...prefix, '-c', 'import sys; sys.exit(sys.version_info < (3, 11))'],
      { stdio: 'ignore', timeout: 5000, windowsHide: true });
    if (check.status === 0) return [command, prefix];
  }
  throw new Error('Python 3.11+ was not found; set CAPFIND_PYTHON to its executable path');
}

function run(py, script, args, env = process.env) {
  const [command, prefix] = py;
  const result = spawnSync(command, [...prefix, path.join(script.root, script.name), ...args],
    { stdio: 'inherit', windowsHide: true, env });
  if (result.error) throw result.error;
  process.exitCode = result.status === null ? 1 : result.status;
}

function stableRoot() {
  const base = process.platform === 'win32'
    ? process.env.LOCALAPPDATA || path.join(os.homedir(), 'AppData', 'Local')
    : path.join(os.homedir(), '.local', 'share');
  return path.join(base, 'local-capability-finder');
}

function setupRoot(dryRun) {
  // A Git checkout is durable. A package fetched by npx lives in a disposable cache.
  if (fs.existsSync(path.join(source, '.git'))) return source;
  const target = stableRoot();
  if (dryRun) {
    console.log(`Would copy the MCP server to ${target}`);
    return target;
  }
  fs.mkdirSync(target, { recursive: true });
  for (const name of scripts) {
    const temporary = path.join(target, `.${name}.${process.pid}.tmp`);
    try {
      fs.copyFileSync(path.join(source, name), temporary);
      fs.renameSync(temporary, path.join(target, name));
    } finally {
      if (fs.existsSync(temporary)) fs.unlinkSync(temporary);
    }
  }
  return target;
}

function chooseAgents() {
  if (!process.stdin.isTTY || !process.stdout.isTTY || !process.stdin.setRawMode) {
    throw new Error('interactive agent selection needs a terminal; use --agent NAME or --all');
  }
  return new Promise((resolve, reject) => {
    const chosen = new Set();
    let cursor = 0;
    let search = '';
    const matches = () => agentNames.filter(name => name.includes(search.toLowerCase()));
    const render = () => {
      const visible = matches();
      cursor = Math.min(cursor, Math.max(visible.length - 1, 0));
      const lines = [
        ' ██████╗ █████╗ ██████╗ ███████╗██╗███╗   ██╗██████╗',
        '██╔════╝██╔══██╗██╔══██╗██╔════╝██║████╗  ██║██╔══██╗',
        '██║     ███████║██████╔╝█████╗  ██║██╔██╗ ██║██║  ██║',
        '██║     ██╔══██║██╔═══╝ ██╔══╝  ██║██║╚██╗██║██║  ██║',
        '╚██████╗██║  ██║██║     ██║     ██║██║ ╚████║██████╔╝',
        ' ╚═════╝╚═╝  ╚═╝╚═╝     ╚═╝     ╚═╝╚═╝  ╚═══╝╚═════╝',
        '',
        '┌  local-capability-finder',
        `│  Source: ${source}`,
        '│  Found 1 MCP server: local-capability-finder',
        `│  ${agentNames.length} supported agents`,
        '│',
        '◆  Which agents do you want to install to?',
        `│  Search: ${search}`,
        '│  ↑↓ move, space select, ctrl+a select all, enter confirm, esc cancel',
        '│',
        ...visible.map((name, index) => `│ ${index === cursor ? '❯' : ' '} ${chosen.has(name) ? '●' : '○'} ${name}`),
        ...(visible.length ? [] : ['│  No matching agents']),
        '│',
        `│  Selected: ${chosen.size ? [...chosen].join(', ') : 'none'}`,
        '└',
      ];
      process.stdout.write('\x1b[2J\x1b[H' + lines.join('\n') + '\n');
    };
    const cleanup = () => {
      process.stdin.off('keypress', onKey);
      process.stdin.setRawMode(false);
      process.stdin.pause();
    };
    const onKey = (character, key) => {
      if ((key.ctrl && key.name === 'c') || key.name === 'escape') {
        cleanup();
        reject(new Error('cancelled'));
        return;
      }
      const visible = matches();
      if (key.name === 'up') cursor = (cursor + visible.length - 1) % Math.max(visible.length, 1);
      else if (key.name === 'down') cursor = (cursor + 1) % Math.max(visible.length, 1);
      else if (key.name === 'space' && visible.length) {
        const name = visible[cursor];
        if (chosen.has(name)) chosen.delete(name); else chosen.add(name);
      } else if (key.name === 'return') {
        if (chosen.size) {
          cleanup();
          process.stdout.write(`Selected: ${[...chosen].join(', ')}\n`);
          resolve([...chosen]);
          return;
        }
      } else if (key.name === 'backspace') search = search.slice(0, -1);
      else if (key.ctrl && key.name === 'a') {
        for (const name of agentNames) chosen.add(name);
      } else if (character && character.length === 1 && !key.ctrl && !key.meta) search += character;
      render();
    };
    readline.emitKeypressEvents(process.stdin);
    process.stdin.setRawMode(true);
    process.stdin.resume();
    process.stdin.on('keypress', onKey);
    render();
  });
}

async function main(argv) {
  const command = argv.shift();
  if (!command || command === 'help' || command === '--help' || command === '-h') {
    usage();
    return;
  }
  if (command === 'version' || command === '--version' || command === '-v') {
    if (argv.length) throw new Error('version takes no arguments');
    console.log(version);
    return;
  }
  const py = python();
  if (command === 'setup' || command === 'add') {
    const selected = [];
    let all = false;
    let dryRun = false;
    let skipRefresh = false;
    while (argv.length) {
      const flag = argv.shift();
      if (flag === '--agent') {
        const name = argv.shift();
        if (!name || name.startsWith('-')) throw new Error('--agent requires a supported agent name');
        selected.push(name);
      }
      else if (flag === '--all') all = true;
      else if (flag === '--dry-run') dryRun = true;
      else if (flag === '--skip-refresh') skipRefresh = true;
      else throw new Error(`unknown setup option: ${flag}`);
    }
    if (all && selected.length) throw new Error('use --all or --agent, not both');
    for (const agent of selected) {
      if (!agents.has(agent)) throw new Error(`unsupported agent: ${agent}`);
    }
    if (!all && !selected.length) selected.push(...await chooseAgents());
    if (all) selected.push(...agentNames);
    const root = setupRoot(dryRun);
    const args = [
      '--agents', ...new Set(selected),
      ...(dryRun ? ['--dry-run'] : []),
      ...(skipRefresh ? ['--skip-refresh'] : []),
    ];
    const scriptRoot = dryRun && root !== source ? source : root;
    const env = scriptRoot !== root
      ? { ...process.env, CAPFIND_SERVER_PATH: path.join(root, 'capability_mcp.py') }
      : process.env;
    run(py, { root: scriptRoot, name: 'install_agents.py' }, args, env);
    return;
  }
  if (command === 'mcp') {
    if (argv.length) throw new Error('mcp takes no arguments');
    const root = fs.existsSync(path.join(source, '.git'))
      ? source
      : fs.existsSync(path.join(stableRoot(), 'capability_mcp.py')) ? stableRoot() : source;
    run(py, { root, name: 'capability_mcp.py' }, []);
    return;
  }
  if (!['search', 'show', 'activate', 'refresh'].includes(command)) {
    throw new Error(`unknown command: ${command}`);
  }
  if (command === 'search' && (!argv.length || argv[0].startsWith('-'))) {
    throw new Error('search requires a quoted task description');
  }
  if ((command === 'show' || command === 'activate') && (!argv.length || argv[0].startsWith('-'))) {
    throw new Error(`${command} requires an exact capability ID`);
  }
  run(py, { root: source, name: 'capability_finder.py' }, [command, ...argv]);
}

main(process.argv.slice(2)).catch(error => fail(error.message));
