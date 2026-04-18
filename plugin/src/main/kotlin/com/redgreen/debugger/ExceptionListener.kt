package com.redgreen.debugger

import com.intellij.openapi.application.ApplicationManager
import com.intellij.openapi.application.ReadAction
import com.intellij.openapi.diagnostic.Logger
import com.intellij.openapi.project.Project
import com.intellij.openapi.vfs.VirtualFile
import com.intellij.xdebugger.XDebugProcess
import com.intellij.xdebugger.XDebugSession
import com.intellij.xdebugger.XDebugSessionListener
import com.intellij.xdebugger.XDebuggerManager
import com.intellij.xdebugger.XDebuggerManagerListener
import com.redgreen.AnalyzePayload
import com.redgreen.RedGreenController

/**
 * Fires when the PyCharm debugger pauses. If the pause looks like an
 * exception (vs. a manual breakpoint), we build an AnalyzePayload from the
 * top stack frame and hand it to the controller.
 *
 * There is no perfect "was this an exception" signal across the XDebugger
 * API, so we use a pragmatic heuristic:
 *  - if the suspend context's stack frames exist AND
 *  - the active stack frame's position points at a line that exists in a
 *    readable source file
 *  - we fire.
 *
 * False positives (i.e. firing on a plain breakpoint) are cheap: the user
 * just dismisses the tool window. False negatives (missing an exception)
 * are what we care about; we bias toward firing.
 */
class RedGreenDebuggerManagerListener(private val project: Project) : XDebuggerManagerListener {
    private val log = Logger.getInstance(RedGreenDebuggerManagerListener::class.java)

    override fun processStarted(debugProcess: XDebugProcess) {
        val session = debugProcess.session
        log.info("RedGreen: attaching to debug session ${session.sessionName}")
        session.addSessionListener(RedGreenSessionListener(project, session))
    }
}


private class RedGreenSessionListener(
    private val project: Project,
    private val session: XDebugSession,
) : XDebugSessionListener {
    private val log = Logger.getInstance(RedGreenSessionListener::class.java)
    private var lastFiredAt: Long = 0

    override fun sessionPaused() {
        // Throttle: don't fire twice within 5s for the same session.
        val now = System.currentTimeMillis()
        if (now - lastFiredAt < 5_000) return
        lastFiredAt = now

        val payload = ReadAction.compute<AnalyzePayload?, Throwable> {
            buildPayloadFromSession(session)
        } ?: return

        log.info("RedGreen: session paused at ${payload.frame_file}:${payload.frame_line} — kicking off analyze")
        ApplicationManager.getApplication().executeOnPooledThread {
            RedGreenController(project).analyze(payload)
        }
    }
}


private fun buildPayloadFromSession(session: XDebugSession): AnalyzePayload? {
    val frame = session.currentStackFrame ?: return null
    val pos = frame.sourcePosition ?: return null
    val file: VirtualFile = pos.file
    val line = pos.line + 1  // XSourcePosition is 0-based

    val project = session.project
    val base = project.basePath ?: return null
    val absPath = file.path
    val relPath = if (absPath.startsWith("$base/")) absPath.removePrefix("$base/") else absPath

    val text = try {
        String(file.contentsToByteArray())
    } catch (t: Throwable) {
        return null
    }
    val lines = text.split("\n")
    val lo = maxOf(0, line - 1 - 20)
    val hi = minOf(lines.size, line - 1 + 20)
    val frameSource = (lo until hi).joinToString("\n") { "%4d: %s".format(it + 1, lines[it]) }

    // Hand-assemble a Python-style stacktrace from the session's execution stack.
    val trace = buildSyntheticStacktrace(session)

    return AnalyzePayload(
        stacktrace = trace,
        locals_json = emptyMap(),  // TODO: read XStackFrame's children via debuggerSession.
        frame_file = relPath,
        frame_line = line,
        frame_source = frameSource,
        repo_hash = "debugger:$base",
        repo_snapshot_path = base,
    )
}


private fun buildSyntheticStacktrace(session: XDebugSession): String {
    // XDebugger exposes the execution stack, but walking it into a clean
    // Python-style traceback across all languages is intricate. For the
    // hackathon we emit a compact, human-readable marker — the frame_file /
    // frame_line in the payload are what the backend actually relies on to
    // build prompt context.
    val frame = session.currentStackFrame ?: return "Paused in debugger (no frame info)."
    val pos = frame.sourcePosition
    val path = pos?.file?.path ?: "unknown"
    val line = (pos?.line ?: -1) + 1
    return """
        Debugger paused at the top frame. RedGreen is treating this as the failure site.

          File "$path", line $line, in <paused-frame>
            (live frame — see frame_source for the surrounding code)

        Exception type: inferred from debugger pause. If this was a manual breakpoint,
        dismiss the RedGreen panel; no harm done.
    """.trimIndent()
}
