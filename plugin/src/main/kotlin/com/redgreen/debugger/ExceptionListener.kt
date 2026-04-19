package com.redgreen.debugger

import com.intellij.openapi.application.ApplicationManager
import com.intellij.openapi.application.ReadAction
import com.intellij.openapi.diagnostic.Logger
import com.intellij.openapi.fileEditor.FileEditorManager
import com.intellij.openapi.project.Project
import com.intellij.openapi.vfs.VirtualFile
import com.intellij.psi.PsiDocumentManager
import com.intellij.psi.PsiErrorElement
import com.intellij.psi.PsiManager
import com.intellij.psi.util.PsiTreeUtil
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
        findFirstProjectFrame(session, base)
    }

    if (selected != null) {
        val pos = selected.sourcePosition ?: return null
        return payloadFromFrame(base, pos.file, pos.line + 1, selected === topFrame)
    }

    // No user frame anywhere — classic "exception was raised during module load"
    // signature (SyntaxError, ImportError of a user file, etc.). Fall back to
    // PyCharm's parser: find a PsiErrorElement in an open editor tab.
    val syntaxHit = findSyntaxErrorInOpenEditors(project)
    if (syntaxHit != null) {
        val (file, line, msg) = syntaxHit
        return payloadFromSyntaxError(base, file, line, msg)
    }

    return null
}


private fun payloadFromFrame(
    base: String,
    file: VirtualFile,
    line: Int,
    isTopFrame: Boolean,
): AnalyzePayload? {
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
    val trace = buildSyntheticStacktrace(file.path, line, isTopFrame)
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


private fun payloadFromSyntaxError(
    base: String,
    file: VirtualFile,
    line: Int,
    msg: String,
): AnalyzePayload? {
    val relPath = file.path.removePrefix("$base/")
    val text = try {
        String(file.contentsToByteArray())
    } catch (t: Throwable) {
        return null
    }
    val lines = text.split("\n")
    val lo = maxOf(0, line - 1 - 10)
    val hi = minOf(lines.size, line - 1 + 10)
    val frameSource = (lo until hi).joinToString("\n") { "%4d: %s".format(it + 1, lines[it]) }

    // Special stacktrace so the backend knows this was caught at parse time.
    // This prefix ("SyntaxError [parse-time]") is the signal the orchestrator's
    // prompt builder picks up to switch guidance for the models.
    val trace = """
        SyntaxError [parse-time] — caught before any user code ran.

          File "${file.path}", line $line
            $msg

        The whole module fails to parse. Tests for this bug must use
        importlib.import_module(...) inside a function body, so pytest
        collection doesn't die on the import line itself.
    """.trimIndent()

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


/**
 * Scan open editor tabs for PyCharm's PsiErrorElement nodes — these are what
 * PyCharm's parser inserts when source fails to parse. When a module can't
 * load at runtime due to a SyntaxError, there's no user frame in the stack,
 * but PyCharm has already parsed the buffer and knows exactly where the
 * problem is.
 */
private fun findSyntaxErrorInOpenEditors(project: Project): Triple<VirtualFile, Int, String>? {
    val fem = FileEditorManager.getInstance(project)
    val base = project.basePath ?: return null

    // Start with the currently-selected tab, then all open tabs, de-duped.
    val ordered = (fem.selectedFiles.toList() + fem.openFiles.toList()).distinct()

    for (vf in ordered) {
        if (!vf.path.startsWith("$base/")) continue
        val psi = PsiManager.getInstance(project).findFile(vf) ?: continue
        val errors = PsiTreeUtil.findChildrenOfType(psi, PsiErrorElement::class.java)
        if (errors.isEmpty()) continue
        val first = errors.first()
        val doc = PsiDocumentManager.getInstance(project).getDocument(psi) ?: continue
        val line = doc.getLineNumber(first.textOffset) + 1
        val msg = first.errorDescription.ifBlank { "syntax error" }
        return Triple(vf, line, msg)
    }
    return null
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


@Suppress("unused") // called by payloadFromFrame only — kept for clarity of the syntax-error vs runtime split.
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
