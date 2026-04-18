package com.redgreen.debugger

import com.intellij.openapi.application.ApplicationManager
import com.intellij.openapi.application.ReadAction
import com.intellij.openapi.diagnostic.Logger
import com.intellij.openapi.project.Project
import com.intellij.openapi.vfs.VirtualFile
import com.intellij.xdebugger.XDebugProcess
import com.intellij.xdebugger.XDebugSession
import com.intellij.xdebugger.XDebugSessionListener
import com.intellij.xdebugger.XDebuggerManagerListener
import com.intellij.xdebugger.frame.XExecutionStack
import com.intellij.xdebugger.frame.XStackFrame
import com.redgreen.AnalyzePayload
import com.redgreen.RedGreenController
import java.util.concurrent.CountDownLatch
import java.util.concurrent.TimeUnit

/**
 * Fires when the PyCharm debugger pauses.
 *
 * Frame selection strategy (critical for usefulness):
 *   1. If the TOP frame's source file is inside the user's project, use it.
 *   2. Otherwise walk the stack top-to-bottom, pick the first frame that IS
 *      inside the project. This handles the common case where an exception
 *      surfaces deep in a library but the user's code is further down.
 *   3. If NO project frame exists anywhere in the stack (e.g. SyntaxError
 *      raised from PyCharm's own exec(compile()) helper), skip firing —
 *      we have nothing productive to say.
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

        val payload = ReadAction.compute<AnalyzePayload?, Throwable> {
            buildPayloadFromSession(session)
        }

        if (payload == null) {
            log.info("RedGreen: skipping this pause — no user-project frame in the stack")
            return
        }

        lastFiredAt = now
        log.info("RedGreen: session paused at ${payload.frame_file}:${payload.frame_line} — kicking off analyze")
        ApplicationManager.getApplication().executeOnPooledThread {
            RedGreenController(project).analyze(payload)
        }
    }
}


private fun buildPayloadFromSession(session: XDebugSession): AnalyzePayload? {
    val project = session.project
    val base = project.basePath ?: return null

    // Prefer the current (top) frame if it's inside the project — cheap path.
    val topFrame = session.currentStackFrame
    val topPos = topFrame?.sourcePosition
    val topPath = topPos?.file?.path
    val selected = if (topPath != null && isProjectPath(topPath, base)) {
        topFrame
    } else {
        // Walk the stack for the first user-project frame.
        findFirstProjectFrame(session, base)
    } ?: return null

    val pos = selected.sourcePosition ?: return null
    val file: VirtualFile = pos.file
    val line = pos.line + 1  // XSourcePosition is 0-based

    val relPath = file.path.removePrefix("$base/")
    val text = try {
        String(file.contentsToByteArray())
    } catch (t: Throwable) {
        return null
    }
    val lines = text.split("\n")
    val lo = maxOf(0, line - 1 - 20)
    val hi = minOf(lines.size, line - 1 + 20)
    val frameSource = (lo until hi).joinToString("\n") { "%4d: %s".format(it + 1, lines[it]) }

    val trace = buildSyntheticStacktrace(file.path, line, selected === topFrame)

    return AnalyzePayload(
        stacktrace = trace,
        locals_json = emptyMap(),
        frame_file = relPath,
        frame_line = line,
        frame_source = frameSource,
        repo_hash = "debugger:$base",
        repo_snapshot_path = base,
    )
}


private fun isProjectPath(absPath: String, base: String): Boolean {
    return absPath.startsWith("$base/") &&
        // Reject common virtual-root locations that land under a project but aren't user code.
        !absPath.contains("/.gradle/") &&
        !absPath.contains("/.venv/") &&
        !absPath.contains("/node_modules/") &&
        !absPath.contains("/build/") &&
        !absPath.contains("/dist/")
}


/**
 * Blocking walk of the execution stack to find the first frame whose file
 * lives inside the user's project. The XDebugger API is async; we rendezvous
 * through a CountDownLatch with a 2-second ceiling.
 */
private fun findFirstProjectFrame(session: XDebugSession, base: String): XStackFrame? {
    val stack: XExecutionStack = session.suspendContext?.activeExecutionStack ?: return null
    val collected = mutableListOf<XStackFrame>()
    val done = CountDownLatch(1)

    val container = object : XExecutionStack.XStackFrameContainer {
        override fun addStackFrames(frames: MutableList<out XStackFrame>, last: Boolean) {
            collected.addAll(frames)
            if (last) done.countDown()
        }
        override fun errorOccurred(errorMessage: String) {
            done.countDown()
        }
    }
    stack.computeStackFrames(0, container)
    done.await(2, TimeUnit.SECONDS)

    return collected.firstOrNull { frame ->
        val path = frame.sourcePosition?.file?.path ?: return@firstOrNull false
        isProjectPath(path, base)
    }
}


private fun buildSyntheticStacktrace(path: String, line: Int, isTopFrame: Boolean): String {
    val note = if (isTopFrame) {
        "Debugger paused at the current top frame — the raising line."
    } else {
        "Debugger paused deep in framework code; RedGreen walked the stack to this user frame."
    }
    return """
        $note

          File "$path", line $line, in <user-frame>
            (live frame — see frame_source for the surrounding code)

        Exception type inferred from debugger pause. If this was a manual
        breakpoint (not an exception), dismiss the panel — no harm done.
    """.trimIndent()
}
