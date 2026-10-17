// Jig for VS Code: starts `python -m jig lsp` and connects it to the editor.
// A minimal Language Server Protocol client with no dependencies: diagnostics as you type, hover, go to
// definition, completion and the outline all come from the Jig language server.

const vscode = require("vscode");
const cp = require("child_process");

let server = null;
let buffer = Buffer.alloc(0);
let nextId = 1;
const pending = new Map();
let diagnostics = null;
let output = null;
let ready = null;
const opened = new Set();

const SEVERITY = { 1: 0, 2: 1, 3: 2, 4: 3 }; // LSP -> vscode.DiagnosticSeverity

function pythonPath() {
  const configured = vscode.workspace.getConfiguration("jig").get("pythonPath");
  return configured || (process.platform === "win32" ? "python" : "python3");
}

function send(message) {
  if (!server) return;
  const body = Buffer.from(JSON.stringify({ jsonrpc: "2.0", ...message }), "utf8");
  server.stdin.write(`Content-Length: ${body.length}\r\n\r\n`);
  server.stdin.write(body);
}

function notify(method, params) {
  send({ method, params });
}

function request(method, params) {
  const id = nextId++;
  return new Promise((resolve, reject) => {
    pending.set(id, { resolve, reject });
    send({ id, method, params });
  });
}

function onData(chunk) {
  buffer = Buffer.concat([buffer, chunk]);
  for (;;) {
    const headerEnd = buffer.indexOf("\r\n\r\n");
    if (headerEnd < 0) return;
    const header = buffer.slice(0, headerEnd).toString("ascii");
    const match = /Content-Length:\s*(\d+)/i.exec(header);
    if (!match) {
      buffer = buffer.slice(headerEnd + 4);
      continue;
    }
    const length = Number(match[1]);
    if (buffer.length < headerEnd + 4 + length) return;
    const body = buffer.slice(headerEnd + 4, headerEnd + 4 + length).toString("utf8");
    buffer = buffer.slice(headerEnd + 4 + length);
    handle(JSON.parse(body));
  }
}

function normalize(uri) {
  return vscode.Uri.file(vscode.Uri.parse(uri).fsPath);
}

function toRange(r) {
  return new vscode.Range(r.start.line, r.start.character, r.end.line, r.end.character);
}

function handle(msg) {
  if (msg.id !== undefined && pending.has(msg.id) && msg.method === undefined) {
    const { resolve, reject } = pending.get(msg.id);
    pending.delete(msg.id);
    if (msg.error) reject(new Error(msg.error.message));
    else resolve(msg.result);
    return;
  }
  if (msg.method === "textDocument/publishDiagnostics") {
    const items = msg.params.diagnostics.map((d) => {
      const item = new vscode.Diagnostic(toRange(d.range), d.message, SEVERITY[d.severity] ?? 0);
      item.code = d.code;
      item.source = "jig";
      return item;
    });
    diagnostics.set(normalize(msg.params.uri), items);
    return;
  }
  if (msg.id !== undefined && msg.method) send({ id: msg.id, result: null }); // a request from the server
}

function isJig(doc) {
  return doc.languageId === "jig" && doc.uri.scheme === "file";
}

function open(doc) {
  if (!isJig(doc) || opened.has(doc.uri.toString())) return;
  opened.add(doc.uri.toString());
  notify("textDocument/didOpen", {
    textDocument: { uri: doc.uri.toString(), languageId: "jig", version: doc.version, text: doc.getText() },
  });
}

function position(doc, pos) {
  return { textDocument: { uri: doc.uri.toString() }, position: { line: pos.line, character: pos.character } };
}

async function ask(method, params) {
  if (!server) return null;
  await ready;
  try {
    return await request(method, params);
  } catch (e) {
    output.appendLine(`${method}: ${e.message}`);
    return null;
  }
}

function start() {
  const folder = vscode.workspace.workspaceFolders && vscode.workspace.workspaceFolders[0];
  const python = pythonPath();
  buffer = Buffer.alloc(0);
  opened.clear();
  server = cp.spawn(python, ["-m", "jig", "lsp"], { cwd: folder ? folder.uri.fsPath : undefined });
  server.on("error", (e) => {
    server = null;
    vscode.window.showErrorMessage(
      `Jig: could not start "${python} -m jig lsp" (${e.message}). Install Jig with "pip install jig-lang", ` +
        `or set "jig.pythonPath" to the Python that has it.`
    );
  });
  server.stdout.on("data", onData);
  server.stderr.on("data", (d) => output.append(d.toString()));
  server.on("exit", (code) => {
    output.appendLine(`jig lsp stopped (exit ${code})`);
    for (const { reject } of pending.values()) reject(new Error("server stopped"));
    pending.clear();
    server = null;
  });
  ready = request("initialize", {
    processId: process.pid,
    rootUri: folder ? folder.uri.toString() : null,
    capabilities: {},
  }).then(() => {
    notify("initialized", {});
    vscode.workspace.textDocuments.forEach(open);
  });
  return ready;
}

function stop() {
  if (!server) return;
  const s = server;
  request("shutdown", {}).finally(() => notify("exit", {}));
  setTimeout(() => s.kill(), 1000);
  server = null;
}

function activate(context) {
  output = vscode.window.createOutputChannel("Jig");
  diagnostics = vscode.languages.createDiagnosticCollection("jig");
  const selector = { language: "jig", scheme: "file" };
  context.subscriptions.push(
    output,
    diagnostics,
    vscode.workspace.onDidOpenTextDocument(open),
    vscode.workspace.onDidChangeTextDocument((e) => {
      if (!isJig(e.document)) return;
      open(e.document);
      notify("textDocument/didChange", {
        textDocument: { uri: e.document.uri.toString(), version: e.document.version },
        contentChanges: [{ text: e.document.getText() }],
      });
    }),
    vscode.workspace.onDidSaveTextDocument((doc) => {
      if (isJig(doc)) notify("textDocument/didSave", { textDocument: { uri: doc.uri.toString() } });
    }),
    vscode.workspace.onDidCloseTextDocument((doc) => {
      if (!isJig(doc)) return;
      opened.delete(doc.uri.toString());
      notify("textDocument/didClose", { textDocument: { uri: doc.uri.toString() } });
    }),
    vscode.languages.registerHoverProvider(selector, {
      async provideHover(doc, pos) {
        const r = await ask("textDocument/hover", position(doc, pos));
        return r ? new vscode.Hover(new vscode.MarkdownString(r.contents.value)) : null;
      },
    }),
    vscode.languages.registerDefinitionProvider(selector, {
      async provideDefinition(doc, pos) {
        const r = await ask("textDocument/definition", position(doc, pos));
        return r ? new vscode.Location(normalize(r.uri), toRange(r.range)) : null;
      },
    }),
    vscode.languages.registerCompletionItemProvider(
      selector,
      {
        async provideCompletionItems(doc, pos) {
          const r = await ask("textDocument/completion", position(doc, pos));
          return (r ? r.items : []).map((i) => {
            const item = new vscode.CompletionItem(i.label, (i.kind || 1) - 1);
            if (i.detail) item.detail = i.detail;
            return item;
          });
        },
      },
      "."
    ),
    vscode.languages.registerDocumentSymbolProvider(selector, {
      async provideDocumentSymbols(doc) {
        const r = await ask("textDocument/documentSymbol", { textDocument: { uri: doc.uri.toString() } });
        const convert = (s) => {
          const sym = new vscode.DocumentSymbol(s.name, "", s.kind - 1, toRange(s.range), toRange(s.selectionRange));
          sym.children = (s.children || []).map(convert);
          return sym;
        };
        return (r || []).map(convert);
      },
    }),
    vscode.commands.registerCommand("jig.restartServer", () => {
      stop();
      return start();
    }),
    vscode.workspace.onDidChangeConfiguration((e) => {
      if (e.affectsConfiguration("jig.pythonPath")) {
        stop();
        start();
      }
    }),
    { dispose: stop }
  );
  return start();
}

function deactivate() {
  stop();
}

module.exports = { activate, deactivate };
