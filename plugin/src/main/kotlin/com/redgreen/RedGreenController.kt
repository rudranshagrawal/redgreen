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

        // Show the tool window + snapshot metadata on the EDT.
        ApplicationManager.getApplication().invokeLater {
            ToolWindowManager.getInstance(project).getToolWindow("RedGreen")?.show()
            RedGreenToolWindowRegistry.get(project)?.showAnalyzing(payload)
        }

        ApplicationManager.getApplication().executeOnPooledThread {
            val respJson = try {
                svc.postAnalyze(gson.toJson(payload))
            } catch (t: Throwable) {
                postError("Could not reach backend at ${RedGreenSettings.getInstance().backendBaseUrl}: ${t.message}")
                return@executeOnPooledThread
            }
            val resp = gson.fromJson(respJson, AnalyzeResponse::class.java)
            val episodeId = resp.episode_id

            ApplicationManager.getApplication().invokeLater {
                RedGreenToolWindowRegistry.get(project)?.showRacing(episodeId)
            }

            // Poll until the episode resolves or we give up.
            var ticks = 0
            while (ticks < 120) {
                Thread.sleep(1_000)
                val statusJson = try {
                    svc.getStatus(episodeId)
                } catch (t: Throwable) {
                    postError("Status polling failed: ${t.message}")
                    return@executeOnPooledThread
                }
                val status = gson.fromJson(statusJson, StatusResponse::class.java)
                ApplicationManager.getApplication().invokeLater {
                    RedGreenToolWindowRegistry.get(project)?.showStatus(status)
                }
                if (status.state != "racing") {
                    break
                }
                ticks++
            }
        }
    }

    private fun postError(msg: String) {
        ApplicationManager.getApplication().invokeLater {
            RedGreenToolWindowRegistry.get(project)?.showError(msg)
        }
    }
}
