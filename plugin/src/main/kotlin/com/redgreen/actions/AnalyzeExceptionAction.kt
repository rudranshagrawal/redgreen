package com.redgreen.actions

import com.intellij.openapi.actionSystem.ActionUpdateThread
import com.intellij.openapi.actionSystem.AnAction
import com.intellij.openapi.actionSystem.AnActionEvent
import com.intellij.openapi.actionSystem.CommonDataKeys
import com.intellij.openapi.application.ApplicationManager
import com.intellij.openapi.wm.ToolWindowManager
import com.redgreen.RedGreenService
import com.redgreen.RedGreenToolWindowRegistry

/**
 * Right-click action. Sends the currently-open file + a synthetic fake
 * exception to the backend and streams the race into the tool window.
 *
 * This is the fallback trigger. The real flow (M3.4) fires from the
 * debugger on actual exceptions; until that's wired, this action is how
 * we smoke-test the plugin <-> backend loop.
 */
class AnalyzeExceptionAction : AnAction() {

    override fun getActionUpdateThread(): ActionUpdateThread = ActionUpdateThread.BGT

    override fun update(e: AnActionEvent) {
        e.presentation.isEnabledAndVisible = e.project != null
    }

    override fun actionPerformed(e: AnActionEvent) {
        val project = e.project ?: return
        val svc = RedGreenService.getInstance(project)
        val editor = e.getData(CommonDataKeys.EDITOR)
        val vf = e.getData(CommonDataKeys.VIRTUAL_FILE)
        val text = editor?.document?.text ?: ""

        // Grab ~40 lines centered on the caret for frame_source, or fall back
        // to the whole file if short.
        val lineNo = editor?.caretModel?.logicalPosition?.line ?: 0
        val allLines = text.split("\n")
        val lo = maxOf(0, lineNo - 20)
        val hi = minOf(allLines.size, lineNo + 20)
        val frameSource = allLines.subList(lo, hi).mapIndexed { i, s -> "%4d: %s".format(lo + i + 1, s) }.joinToString("\n")

        val stacktrace = """
            Traceback (most recent call last):
              File "${vf?.path ?: "unknown"}", line ${lineNo + 1}, in <synthetic>
                raise NotImplementedError("RedGreen smoke-test — no real exception yet.")
            NotImplementedError: RedGreen smoke-test — no real exception yet.
        """.trimIndent()

        // Pretty-quick JSON serialization. No external dep — hand-rolled since
        // we're only encoding two strings, a map, an int, and a path.
        val frameFileRel = vf?.path?.substringAfterLast('/') ?: "unknown.py"
        val repoHash = "plugin-smoke:" + (project.basePath ?: "")
        val snapshotPath = project.basePath ?: ""
        val body = """
            {
              "stacktrace": ${jsonEscape(stacktrace)},
              "locals_json": {},
              "frame_file": ${jsonEscape(frameFileRel)},
              "frame_line": ${lineNo + 1},
              "frame_source": ${jsonEscape(frameSource)},
              "repo_hash": ${jsonEscape(repoHash)},
              "repo_snapshot_path": ${jsonEscape(snapshotPath)}
            }
        """.trimIndent()

        val window = RedGreenToolWindowRegistry.get(project)
        ToolWindowManager.getInstance(project).getToolWindow("RedGreen")?.show()
        window?.clearAnd("POST /analyze ...")

        // Fire the request off the EDT so the UI doesn't freeze.
        ApplicationManager.getApplication().executeOnPooledThread {
            try {
                val respJson = svc.postAnalyze(body)
                val episodeId = respJson.substringAfter("\"episode_id\":\"").substringBefore("\"")
                ApplicationManager.getApplication().invokeLater {
                    window?.appendLine("episode: $episodeId")
                    window?.appendLine("polling /status ...")
                }
                // Poll until state changes.
                var tries = 0
                while (tries < 90) {
                    Thread.sleep(1000)
                    val statusJson = svc.getStatus(episodeId)
                    val state = statusJson.substringAfter("\"state\":\"").substringBefore("\"")
                    ApplicationManager.getApplication().invokeLater {
                        window?.appendLine("  [${tries + 1}s] state=$state")
                    }
                    if (state != "racing") {
                        ApplicationManager.getApplication().invokeLater {
                            window?.appendLine("---")
                            window?.appendLine(statusJson.take(2000))
                        }
                        break
                    }
                    tries++
                }
            } catch (t: Throwable) {
                ApplicationManager.getApplication().invokeLater {
                    window?.appendLine("ERROR: ${t.message}")
                }
            }
        }
    }

    private fun jsonEscape(s: String): String {
        val sb = StringBuilder("\"")
        for (c in s) {
            when (c) {
                '\\' -> sb.append("\\\\")
                '"' -> sb.append("\\\"")
                '\n' -> sb.append("\\n")
                '\r' -> sb.append("\\r")
                '\t' -> sb.append("\\t")
                else -> if (c.code < 0x20) sb.append("\\u%04x".format(c.code)) else sb.append(c)
            }
        }
        sb.append('"')
        return sb.toString()
    }
}
