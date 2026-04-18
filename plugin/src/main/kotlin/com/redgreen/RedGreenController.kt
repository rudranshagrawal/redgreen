package com.redgreen

import com.google.gson.Gson
import com.intellij.openapi.application.ApplicationManager
import com.intellij.openapi.project.Project
import com.intellij.openapi.wm.ToolWindowManager

/**
 * Single orchestrator on the IDE side: takes a payload, POSTs /analyze, polls
 * /status, and pushes state into the tool window. Decouples the two triggers
 * (right-click action, debugger listener) from the UI.
 */
class RedGreenController(val project: Project) {
    private val gson = Gson()

    fun analyze(payload: AnalyzePayload) {
        val svc = RedGreenService.getInstance(project)
        val window = ensureWindowVisible()

        // Snapshot the episode metadata in the UI immediately so the user sees
        // SOMETHING before the backend replies.
        ApplicationManager.getApplication().invokeLater {
            window?.showAnalyzing(payload)
        }

        ApplicationManager.getApplication().executeOnPooledThread {
            val respJson = try {
                svc.postAnalyze(gson.toJson(payload))
            } catch (t: Throwable) {
                postError(window, "Could not reach backend at ${RedGreenSettings.getInstance().backendBaseUrl}: ${t.message}")
                return@executeOnPooledThread
            }
            val resp = gson.fromJson(respJson, AnalyzeResponse::class.java)
            val episodeId = resp.episode_id

            ApplicationManager.getApplication().invokeLater {
                window?.showRacing(episodeId)
            }

            // Poll until the episode resolves or we give up.
            var ticks = 0
            while (ticks < 120) {
                Thread.sleep(1_000)
                val statusJson = try {
                    svc.getStatus(episodeId)
                } catch (t: Throwable) {
                    postError(window, "Status polling failed: ${t.message}")
                    return@executeOnPooledThread
                }
                val status = gson.fromJson(statusJson, StatusResponse::class.java)
                ApplicationManager.getApplication().invokeLater {
                    window?.showStatus(status)
                }
                if (status.state != "racing") {
                    break
                }
                ticks++
            }
        }
    }

    private fun ensureWindowVisible(): RedGreenToolWindow? {
        ToolWindowManager.getInstance(project).getToolWindow("RedGreen")?.show()
        return RedGreenToolWindowRegistry.get(project)
    }

    private fun postError(window: RedGreenToolWindow?, msg: String) {
        ApplicationManager.getApplication().invokeLater {
            window?.showError(msg)
        }
    }
}
