package com.redgreen.actions

import com.intellij.openapi.actionSystem.ActionUpdateThread
import com.intellij.openapi.actionSystem.AnAction
import com.intellij.openapi.actionSystem.AnActionEvent
import com.intellij.openapi.actionSystem.CommonDataKeys
import com.redgreen.AnalyzePayload
import com.redgreen.RedGreenController

/**
 * Manual fallback trigger. Sends the currently-focused file + caret position
 * to the backend using a synthetic stacktrace header. Useful when there's no
 * live debugger session (e.g. the user wants to explore what the models
 * would say about a piece of code they already know is buggy).
 *
 * For the real "exception in debugger" flow, see debugger/ExceptionListener.
 */
class AnalyzeExceptionAction : AnAction() {

    override fun getActionUpdateThread(): ActionUpdateThread = ActionUpdateThread.BGT

    override fun update(e: AnActionEvent) {
        e.presentation.isEnabledAndVisible = e.project != null &&
            e.getData(CommonDataKeys.EDITOR) != null
    }

    override fun actionPerformed(e: AnActionEvent) {
        val project = e.project ?: return
        val editor = e.getData(CommonDataKeys.EDITOR) ?: return
        val vf = e.getData(CommonDataKeys.VIRTUAL_FILE) ?: return

        val allLines = editor.document.text.split("\n")
        val caretLine = editor.caretModel.logicalPosition.line
        val lo = maxOf(0, caretLine - 20)
        val hi = minOf(allLines.size, caretLine + 20)
        val frameSource = allLines.subList(lo, hi)
            .mapIndexed { i, s -> "%4d: %s".format(lo + i + 1, s) }
            .joinToString("\n")

        val base = project.basePath ?: return
        val rel = if (vf.path.startsWith("$base/")) vf.path.removePrefix("$base/") else vf.path

        val stacktrace = """
            Manual RedGreen trigger (no live exception).
              File "${vf.path}", line ${caretLine + 1}, in <caret-frame>
                (user invoked the right-click action; no exception is actually raised)
        """.trimIndent()

        val payload = AnalyzePayload(
            stacktrace = stacktrace,
            locals_json = emptyMap(),
            frame_file = rel,
            frame_line = caretLine + 1,
            frame_source = frameSource,
            repo_hash = "manual:$base",
            repo_snapshot_path = base,
        )

        RedGreenController(project).analyze(payload)
    }
}
