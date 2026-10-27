'use strict';
const test = require('node:test');
const assert = require('node:assert/strict');
const lib = require('../lib');

test('only the hub on this computer', () => {
  assert.equal(lib.isLocalHub('http://127.0.0.1:8765'), true);
  assert.equal(lib.isLocalHub('http://localhost:8765/'), true);
  assert.equal(lib.isLocalHub('http://[::1]:8765'), true);
  assert.equal(lib.isLocalHub('https://127.0.0.1:8765'), false);
  assert.equal(lib.isLocalHub('http://192.168.0.5:8765'), false);
  assert.equal(lib.isLocalHub('http://127.0.0.1.evil.example:8765'), false);
  assert.equal(lib.isLocalHub('not a url'), false);
});

test('the output keeps its end', () => {
  assert.equal(lib.keepTail('abc', 'def', 4), 'cdef');
  assert.equal(lib.keepTail('', 'xy'), 'xy');
});

test('the open file', () => {
  assert.deepEqual(lib.focusPayload({ scheme: 'file', fsPath: 'D:\\p\\a.py' }, 41), { path: 'D:\\p\\a.py', line: 42 });
  assert.equal(lib.focusPayload({ scheme: 'untitled', fsPath: '' }, 0), null);
  assert.equal(lib.focusPayload(undefined, 0), null);
});

test('a finished command', () => {
  const body = lib.commandPayload({
    terminal: 'pwsh', cwd: 'D:\\p', commandLine: '  pytest -q ', exitCode: 1, output: 'E  1 failed', startedAt: 1000,
    endedAt: 3500,
  });
  assert.deepEqual(body, { terminal: 'pwsh', cwd: 'D:\\p', command: 'pytest -q', exit_code: 1, output: 'E  1 failed',
    duration_s: 2.5 });
  assert.equal(lib.commandPayload({ commandLine: '   ' }), null);
  assert.equal(lib.commandPayload({ commandLine: 'ls', exitCode: undefined }).exit_code, null);
});

test('debounce waits for quiet', async () => {
  let calls = 0;
  const later = lib.debounce(() => { calls += 1; }, 20);
  later(); later(); later();
  await new Promise((resolve) => setTimeout(resolve, 60));
  assert.equal(calls, 1);
});

test('the folder of a command', () => {
  assert.equal(lib.pickCwd(undefined, null, 'D:/p', { fsPath: 'D:/q' }), 'D:/p');
  assert.equal(lib.pickCwd(undefined, { fsPath: 'D:/q' }), 'D:/q');
  assert.equal(lib.pickCwd(undefined, { fsPath: '' }), '');
});
