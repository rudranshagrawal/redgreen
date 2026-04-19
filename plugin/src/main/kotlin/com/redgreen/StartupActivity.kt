package com.redgreen

import com.intellij.openapi.project.Project
import com.intellij.openapi.startup.ProjectActivity
import com.intellij.xdebugger.XDebuggerManager
import com.redgreen.debugger.RedGreenDebuggerManagerListener
import com.redgreen.indexer.CodebaseIndexer

/**
 * On project open:
 *   1. Subscribe to XDebuggerManager.TOPIC so the plugin sees every new
 *      debug session as it starts and can attach per-session listeners.
 *   2. Kick off the codebase indexer so the first analyze request already
 *      has project-style context to hand the models.
 */
class StartupActivity : ProjectActivity {
    override suspend fun execute(project: Project) {
        RedGreenService.getInstance(project)
        project.messageBus.connect().subscribe(
            XDebuggerManager.TOPIC,
            RedGreenDebuggerManagerListener(project),
        )
        CodebaseIndexer.getInstance(project).ensureIndexed()
    }
}
