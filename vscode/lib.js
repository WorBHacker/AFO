'use strict';
// What the extension sends, without VS Code: pure helpers, tested with `node --test test/`.

const DEFAULT_HUB = 'http://127.0.0.1:8765';
const TOKEN_KEY = 'localchat.hubToken'; // in VS Code's secret storage, never in settings
const MAX_OUTPUT = 64 * 1024; // what is kept of a running command's output; the hub keeps far less
const LOCAL_HOSTS = new Set(['127.0.0.1', 'localhost', '[::1]']);

/** Only the hub on this computer: the token and the commands never go anywhere else. */
function isLocalHub(url) {
  try {
    const parsed = new URL(url);
    return parsed.protocol === 'http:' && LOCAL_HOSTS.has(parsed.hostname);
  } catch {
    return false;
  }
}

/** The output so far plus a new piece, cut to its last `max` characters. */
function keepTail(buffer, chunk, max = MAX_OUTPUT) {
  const joined = buffer + chunk;
  return joined.length > max ? joined.slice(joined.length - max) : joined;
}

/** {path, line} of the open file; null for anything that is not a file on disk (output panes, untitled). */
function focusPayload(uri, zeroBasedLine) {
  if (!uri || uri.scheme !== 'file' || !uri.fsPath) {
    return null;
  }
  return { path: uri.fsPath, line: Math.max(1, (zeroBasedLine || 0) + 1) };
}

/** A finished command as the hub takes it; null for an empty line (Enter alone). */
function commandPayload({ terminal, cwd, commandLine, exitCode, output, startedAt, endedAt }) {
  const command = String(commandLine || '').trim();
  if (!command) {
    return null;
  }
  return {
    terminal: String(terminal || '').slice(0, 200),
    cwd: String(cwd || ''),
    command: command.slice(0, 20000),
    exit_code: Number.isInteger(exitCode) ? exitCode : null,
    output: String(output || '').slice(-200000),
    duration_s: startedAt && endedAt && endedAt >= startedAt ? (endedAt - startedAt) / 1000 : null,
  };
}

/** The first folder that is known: a Uri, a path, or nothing. VS Code does not always tell a command's folder. */
function pickCwd(...candidates) {
  for (const candidate of candidates) {
    const folder = candidate && (typeof candidate === 'string' ? candidate : candidate.fsPath);
    if (folder) {
      return folder;
    }
  }
  return '';
}

/** `fn` once things have been quiet for `ms`. */
function debounce(fn, ms) {
  let timer = null;
  return (...args) => {
    clearTimeout(timer);
    timer = setTimeout(() => fn(...args), ms);
  };
}

module.exports = {
  DEFAULT_HUB, TOKEN_KEY, MAX_OUTPUT, isLocalHub, keepTail, focusPayload, commandPayload, pickCwd, debounce,
};
