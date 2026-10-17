// End-to-end test of the VS Code client against the real Jig language server, with a stand-in for the
// `vscode` module. Run: node vscode-jig/test/client.test.js [project dir] (needs Jig installed: python -m jig).

const assert = require("assert");
const fs = require("fs");
const path = require("path");
const Module = require("module");
const { pathToFileURL, fileURLToPath } = require("url");

const os = require("os");

// The project: the argument, or a copy of Jig's class fixture with a module that uses it.
function makeProject() {
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), "jig-vscode-"));
  fs.cpSync(path.join(__dirname, "..", "..", "tests", "fixtures", "classes"), dir, { recursive: true });
  fs.rmSync(path.join(dir, "bank", "bugs.jig"));
  fs.writeFileSync(
    path.join(dir, "bank", "use.jig"),
    [
      "module bank.use",
      "",
      "from bank.accounts import Account, Kind, Money",
      "",
      "",
      "def run(a: Account) -> Money:",
      '    """Deposit five."""',
      "    left = a.deposit(Money(5))",
      "    return left",
      "",
    ].join("\n")
  );
  return dir;
}
const project = process.argv[2] ? path.resolve(process.argv[2]) : makeProject();
const listeners = {};
const providers = {};
const published = new Map();

class Uri {
  constructor(fsPath) {
    this.fsPath = path.resolve(fsPath);
    this.scheme = "file";
  }
  static file(p) {
    return new Uri(p);
  }
  static parse(s) {
    return new Uri(fileURLToPath(s));
  }
  toString() {
    return pathToFileURL(this.fsPath).href;
  }
}
class Range {
  constructor(a, b, c, d) {
    Object.assign(this, { start: { line: a, character: b }, end: { line: c, character: d } });
  }
}
const vscode = {
  Uri,
  Range,
  Diagnostic: class {
    constructor(range, message, severity) {
      Object.assign(this, { range, message, severity });
    }
  },
  Hover: class {
    constructor(contents) {
      this.contents = contents;
    }
  },
  MarkdownString: class {
    constructor(value) {
      this.value = value;
    }
  },
  Location: class {
    constructor(uri, range) {
      Object.assign(this, { uri, range });
    }
  },
  CompletionItem: class {
    constructor(label, kind) {
      Object.assign(this, { label, kind });
    }
  },
  DocumentSymbol: class {
    constructor(name, detail, kind, range, selectionRange) {
      Object.assign(this, { name, kind, range, selectionRange, children: [] });
    }
  },
  workspace: {
    workspaceFolders: [{ uri: Uri.file(project) }],
    textDocuments: [],
    getConfiguration: () => ({ get: () => process.env.JIG_PYTHON || "" }),
    onDidOpenTextDocument: (f) => ((listeners.open = f), { dispose() {} }),
    onDidChangeTextDocument: (f) => ((listeners.change = f), { dispose() {} }),
    onDidSaveTextDocument: (f) => ((listeners.save = f), { dispose() {} }),
    onDidCloseTextDocument: (f) => ((listeners.close = f), { dispose() {} }),
    onDidChangeConfiguration: () => ({ dispose() {} }),
  },
  languages: {
    createDiagnosticCollection: () => ({
      set: (uri, items) => published.set(uri.toString(), items),
      dispose() {},
    }),
    registerHoverProvider: (_, p) => ((providers.hover = p), { dispose() {} }),
    registerDefinitionProvider: (_, p) => ((providers.definition = p), { dispose() {} }),
    registerCompletionItemProvider: (_, p) => ((providers.completion = p), { dispose() {} }),
    registerDocumentSymbolProvider: (_, p) => ((providers.symbols = p), { dispose() {} }),
  },
  window: {
    createOutputChannel: () => ({ append() {}, appendLine() {}, dispose() {} }),
    showErrorMessage: (m) => {
      throw new Error(m);
    },
  },
  commands: { registerCommand: () => ({ dispose() {} }) },
};
const resolve = Module._resolveFilename;
Module._resolveFilename = function (request, ...rest) {
  return request === "vscode" ? "vscode" : resolve.call(this, request, ...rest);
};
require.cache.vscode = { id: "vscode", filename: "vscode", loaded: true, exports: vscode };

function doc(file, text, version) {
  return { uri: Uri.file(file), languageId: "jig", version, getText: () => text };
}

async function until(check, what) {
  for (let i = 0; i < 200; i++) {
    const value = check();
    if (value) return value;
    await new Promise((r) => setTimeout(r, 50));
  }
  throw new Error(`timed out waiting for ${what}`);
}

(async () => {
  const ext = require("../extension.js");
  const context = { subscriptions: [] };
  await ext.activate(context);

  const file = path.join(project, "bank", "use.jig");
  const good = fs.readFileSync(file, "utf8");
  const uri = Uri.file(file).toString();
  listeners.open(doc(file, good, 1));
  await until(() => published.has(uri), "diagnostics on open");
  assert.deepStrictEqual(published.get(uri), []);

  const bad = good.replace("a.deposit(Money(5))", 'a.depositt("5")');
  published.delete(uri);
  listeners.change({ document: doc(file, bad, 2) });
  const diags = await until(() => (published.get(uri) || []).length && published.get(uri), "diagnostics on change");
  assert.strictEqual(diags[0].code, "R002");
  assert.match(diags[0].message, /has no attribute 'depositt'/);

  published.delete(uri);
  listeners.change({ document: doc(file, good, 3) });
  await until(() => published.has(uri) && published.get(uri).length === 0, "errors cleared");

  const line = good.split("\n").findIndex((l) => l.includes("a.deposit("));
  const at = { line, character: good.split("\n")[line].indexOf("deposit") + 2 };
  const hover = await providers.hover.provideHover(doc(file, good, 3), at);
  assert.match(hover.contents.value, /def deposit\(self, amount: Money\) -> Money/);

  const def = await providers.definition.provideDefinition(doc(file, good, 3), at);
  assert.ok(def.uri.fsPath.endsWith("accounts.jig"), def.uri.fsPath);

  const typing = good.replace("    left = a.deposit(Money(5))", "    left = a.");
  listeners.change({ document: doc(file, typing, 4) });
  const items = await providers.completion.provideCompletionItems(doc(file, typing, 4), { line, character: "    left = a.".length });
  assert.ok(items.some((i) => i.label === "deposit"), items.map((i) => i.label).join(","));

  const symbols = await providers.symbols.provideDocumentSymbols(doc(path.join(project, "bank", "accounts.jig"), "", 1));
  assert.ok(symbols.some((s) => s.name === "Account" && s.children.some((c) => c.name === "deposit")));

  ext.deactivate();
  console.log("vscode client: ok");
  process.exit(0);
})().catch((e) => {
  console.error(e);
  process.exit(1);
});
