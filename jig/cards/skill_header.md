---
name: jig
description: Write and change Jig code (.jig files), the LLM-first language checked by `jig check`. Use whenever a task creates or edits .jig files, lib/*.manifest, or .decisions/.
---

# Writing Jig

Humans describe what they want; you write the Jig; the compiler verifies it.

## Workflow

1. Read before writing: `jig interface <dir>` shows every module's signatures, contracts and examples without
   bodies. Read `lib/*.manifest` for what each library exports and `.decisions/*` for project rules.
2. Write or edit the module in idiomatic Python plus Jig's data types (see below).
3. The session starts with a map of the project: every module's signatures and effects, each library's
   exports, the project's decisions, and mistakes made here before. Use only names that exist there.
4. After each edit a hook runs `jig fix` and `jig check` on the file and the modules it imports. If it
   reports errors, fix every one and save again. If it says it rewrote the file, re-read it first.
   You cannot finish while a file you changed still fails the checks.
5. Without the hooks, run `jig fix <file>` then `jig check --for-model <dir>` yourself.
6. Then run `jig probe <file>`: it shows what each function returns on edge inputs (`""`, `0`, `[]`).
   Compare every line with what the user asked for, and fix any that differ.
7. Done means `jig check` passes and the probes match the request. Never edit generated Python.

