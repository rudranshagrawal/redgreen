package com.redgreen

import com.intellij.openapi.project.Project
import com.intellij.openapi.startup.ProjectActivity
import com.intellij.xdebugger.XDebuggerManager
import com.redgreen.debugger.RedGreenDebuggerManagerListener

/**
 * On project open, subscribe to XDebuggerManager.TOPIC so the plugin sees
 * every new debug session as it starts and can attach per-session listeners.
 */
class StartupActivity : ProjectActivity {
    override suspend fun execute(project: Project) {
        RedGreenService.getInstance(project)
        project.messageBus.connect().subscribe(
            XDebuggerManager.TOPIC,
            RedGreenDebuggerManagerListener(project),
        )
    }
}
