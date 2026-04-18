package com.redgreen

import com.intellij.openapi.project.Project
import com.intellij.openapi.startup.ProjectActivity

/**
 * Placeholder project-activity hook. M3.4 will wire the XDebuggerManager listener
 * here so exception breakpoints trigger an analyze automatically.
 */
class StartupActivity : ProjectActivity {
    override suspend fun execute(project: Project) {
        // Initialize the project service so it's ready when the debugger fires.
        RedGreenService.getInstance(project)
    }
}
