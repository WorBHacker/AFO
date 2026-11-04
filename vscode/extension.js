'use strict';
// AFO for VS Code (stage 6.6): tells the hub on this computer which file is open and which commands ran in the
// terminals (with shell integration), so the hub's chats and agents see what the user is doing. It only reports;
// it reads nothing from the hub and changes nothing in VS Code. Nothing is queued: with the hub stopped, reports
// are simply dropped.

const vscode = require('vscode');
const lib = require('./lib');

const FOCUS_QUIET_MS = 1500;
const OUTPUT_SETTLE_MS = 300; // the end event can come before the last output piece is read

let lastProblem = '';
let reported = 0;
let askedForToken = false;

function settings() {
  return vscode.workspace.getConfiguration('localchat');
}

async function askForToken(context, why) {
  if (askedForToken) {
    return;
  }
  askedForToken = true;
  const choice = await vscode.window.showInformationMessage(
    `AFO: ${why}. Создайте токен: python -m hub token create vscode — и вставьте его.`,
    'Задать токен',
  );
  if (choice) {
    await setToken(context);
  }
}

async function setToken(context) {
  const token = await vscode.window.showInputBox({
    title: 'Токен хаба AFO',
    prompt: 'Его печатает python -m hub token create vscode (показывается один раз)',
    password: true,
    ignoreFocusOut: true,
  });
  if (token && token.trim()) {
    await context.secrets.store(lib.TOKEN_KEY, token.trim());
    askedForToken = false;
    lastProblem = '';
    vscode.window.showInformationMessage('AFO: токен сохранён в хранилище секретов VS Code');
  }
}

async function report(context, path, body) {
  if (!body || !settings().get('enabled', true)) {
    return;
  }
  const base = settings().get('hubUrl', lib.DEFAULT_HUB);
  if (!lib.isLocalHub(base)) {
    lastProblem = `адрес хаба ${base} не на этом компьютере — ничего не отправляется`;
    return;
  }
  // the secret storage first; LOCALCHAT_TOKEN (as the hub's stdio adapter reads it) for a launched-with-env VS Code
  const token = (await context.secrets.get(lib.TOKEN_KEY)) || process.env.LOCALCHAT_TOKEN;
  if (!token) {
    lastProblem = 'нет токена хаба';
    askForToken(context, 'нужен токен хаба');
    return;
  }
  try {
    const response = await fetch(base.replace(/\/+$/, '') + path, {
      method: 'POST',
      headers: { Authorization: `Bearer ${token}`, 'Content-Type': 'application/json' },
      body: JSON.stringify(body),
      signal: AbortSignal.timeout(5000),
    });
    if (response.status === 401) {
      lastProblem = 'хаб не принял токен';
      askForToken(context, 'хаб не принял токен');
    } else if (!response.ok) {
      lastProblem = `хаб ответил ${response.status}`;
    } else {
      lastProblem = '';
      reported += 1;
    }
  } catch {
    lastProblem = 'хаб не отвечает (не запущен?)';
  }
}

function activate(context) {
  const reportFocus = lib.debounce(() => {
    const editor = vscode.window.activeTextEditor;
    if (editor) {
      report(context, '/v1/editor/focus', lib.focusPayload(editor.document.uri, editor.selection.active.line));
    }
  }, FOCUS_QUIET_MS);

  const running = new Map(); // execution -> {output, startedAt}
  context.subscriptions.push(
    vscode.window.onDidChangeActiveTextEditor(reportFocus),
    vscode.window.onDidChangeTextEditorSelection(reportFocus),
    vscode.window.onDidChangeWindowState((state) => state.focused && reportFocus()),
    vscode.window.onDidStartTerminalShellExecution((event) => {
      const record = { output: '', startedAt: Date.now() };
      running.set(event.execution, record);
      if (settings().get('sendOutput', true)) {
        (async () => {
          try {
            for await (const piece of event.execution.read()) {
              record.output = lib.keepTail(record.output, piece);
            }
          } catch {
            // the terminal went away: what was read is what there is
          }
        })();
      }
    }),
    vscode.window.onDidEndTerminalShellExecution((event) => {
      const record = running.get(event.execution) || { output: '', startedAt: null };
      running.delete(event.execution);
      const endedAt = Date.now();
      setTimeout(() => {
        report(context, '/v1/editor/command', lib.commandPayload({
          terminal: event.terminal.name,
          // the command's folder; else the terminal's now; else where it was opened; else the only workspace folder
          cwd: lib.pickCwd(
            event.execution.cwd,
            event.shellIntegration && event.shellIntegration.cwd,
            event.terminal.creationOptions && event.terminal.creationOptions.cwd,
            (vscode.workspace.workspaceFolders || []).length === 1 ? vscode.workspace.workspaceFolders[0].uri : null,
          ),
          commandLine: event.execution.commandLine && event.execution.commandLine.value,
          exitCode: event.exitCode,
          output: record.output,
          startedAt: record.startedAt,
          endedAt,
        }));
      }, OUTPUT_SETTLE_MS);
    }),
    vscode.commands.registerCommand('localchat.setToken', () => setToken(context)),
    vscode.commands.registerCommand('localchat.status', () => {
      const state = lastProblem ? `проблема: ${lastProblem}` : 'связь в порядке';
      vscode.window.showInformationMessage(`AFO: ${state}; отправлено сообщений: ${reported}`);
    }),
  );
  reportFocus();
}

function deactivate() {}

module.exports = { activate, deactivate };
