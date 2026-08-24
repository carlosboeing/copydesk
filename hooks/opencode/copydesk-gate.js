// CopyDesk write-time gate for OpenCode.
//
// OpenCode has no shell hooks: its extension point is a plugin module whose
// exported factory returns event handlers. This module maps the
// tool.execute.before event onto the same lib/linter.py the Claude Code and
// Grok gates call. No rule lives here; lint, retry counts, severity handling
// and fail-open behaviour stay in linter.py.
//
// The envelope translation mirrors hooks/grok-gate.py:
//
//   - tool names `write` / `edit` map to `Write` / `Edit`; the camelCase
//     arguments (filePath, content, oldString, newString) map to the
//     snake_case keys linter.py reads. The names come from OpenCode's own
//     parameter declaration in the 1.18.21 binary, which annotates
//     filePath, oldString, newString and an optional replaceAll.
//   - `replaceAll` is optional there and defaults to false; linter.py
//     refuses to guess, so a missing one is injected as false rather than
//     letting the edit fail open past the gate.
//   - the session id gains an `opencode-` prefix, which gives OpenCode its
//     own retry state files instead of sharing three-strike counters with
//     other harnesses.
//
// Throwing from tool.execute.before denies the tool call and shows the error
// message to the model. Every internal failure — python3 missing, linter
// missing, unreadable payload — lets the write through: the gate fails open,
// because a hook that blocks on its own misconfiguration is worse than one
// that lets the write through.
//
// Set COPYDESK_TRACE to a writable path to log one line per hook invocation.
// A module that loads but never fires is indistinguishable from a working
// gate that finds nothing; the trace exists to tell those apart.

import { spawnSync } from "node:child_process"
import { appendFileSync, existsSync } from "node:fs"
import path from "node:path"
import { fileURLToPath } from "node:url"

const TOOL_NAMES = {
  write: "Write",
  edit: "Edit",
}

function linterPath() {
  const override = process.env.COPYDESK_LINTER
  if (override && existsSync(override)) return override
  const here = path.dirname(fileURLToPath(import.meta.url))
  // Installed layout: plugins/copydesk-gate.js beside copydesk/linter.py.
  const installed = path.resolve(here, "..", "copydesk", "linter.py")
  if (existsSync(installed)) return installed
  // Source bundle: hooks/opencode/copydesk-gate.js beside lib/.
  const bundled = path.resolve(here, "..", "..", "lib", "linter.py")
  if (existsSync(bundled)) return bundled
  return null
}

function translate(input, args) {
  const toolName = TOOL_NAMES[input.tool]
  if (!toolName) return null
  const filePath = typeof args.filePath === "string" ? args.filePath : args.file_path
  if (typeof filePath !== "string" || !filePath) return null
  const sessionId = typeof input.sessionID === "string" ? input.sessionID : ""
  if (!sessionId) return null
  const toolInput = { file_path: filePath }
  if (toolName === "Write") {
    if (typeof args.content !== "string") return null
    toolInput.content = args.content
  } else {
    if (typeof args.oldString !== "string" || typeof args.newString !== "string") return null
    toolInput.old_string = args.oldString
    toolInput.new_string = args.newString
    toolInput.replace_all =
      typeof args.replaceAll === "boolean" ? args.replaceAll :
      typeof args.replace_all === "boolean" ? args.replace_all : false
  }
  return {
    tool_name: toolName,
    tool_input: toolInput,
    session_id: `opencode-${sessionId}`,
  }
}

// OpenCode evaluates tool.execute.before twice for one tool call: a first
// pass whose input carries no callID, then the pre-execution pass with
// callID and sessionID. Only the second may reach the linter — otherwise one
// user-visible write consumes two three-strike attempts and the escape's
// allow is immediately re-judged as a fresh block. The verdict cache keeps
// the decision stable should both passes ever carry a callID.
const VERDICTS = new Map()

export const CopydeskGate = async () => ({
  "tool.execute.before": async (input, output) => {
    if (typeof input.callID !== "string" || !input.callID) return
    const cacheKey = input.callID
    if (VERDICTS.has(cacheKey)) {
      const denial = VERDICTS.get(cacheKey)
      if (denial !== null) {
        throw new Error(`CopyDesk denied this Markdown write:\n${denial}`)
      }
      return
    }
    let denial = null
    try {
      const trace = process.env.COPYDESK_TRACE
      if (trace) {
        try {
          appendFileSync(trace, JSON.stringify({
            tool: input.tool,
            callID: input.callID,
            session: input.sessionID,
            file: output?.args?.filePath ?? output?.args?.file_path ?? null,
          }) + "\n")
        } catch { } // tracing must never gate behaviour
      }
      const args = output?.args ?? {}
      const payload = translate(input, args)
      if (payload === null) return
      const linter = linterPath()
      if (linter === null) return
      const result = spawnSync("python3", [linter, "--hook"], {
        input: JSON.stringify(payload),
        encoding: "utf8",
        timeout: 60000,
        // Bun snapshots the environment at startup and hands spawns that
        // snapshot, so runtime mutations would silently vanish. Spreading
        // the live environment keeps the child's view current on either
        // runtime — COPYDESK_* overrides included.
        env: { ...process.env },
      })
      if (result.status === 2) {
        denial = (result.stderr || "").trim() ||
          "CopyDesk blocked this Markdown write."
      }
    } catch {
      denial = null // fail open on any internal error
    }
    try {
      VERDICTS.set(cacheKey, denial)
    } catch { } // an uncacheable verdict still applies to this call
    if (denial !== null) {
      throw new Error(`CopyDesk denied this Markdown write:\n${denial}`)
    }
  },
})
