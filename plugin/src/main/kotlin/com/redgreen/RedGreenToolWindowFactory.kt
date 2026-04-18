package com.redgreen

import com.intellij.openapi.project.Project
import com.intellij.openapi.wm.ToolWindow
import com.intellij.openapi.wm.ToolWindowFactory
import com.intellij.ui.components.JBLabel
import com.intellij.ui.components.JBPanel
import com.intellij.ui.components.JBScrollPane
import com.intellij.ui.content.ContentFactory
import java.awt.BorderLayout
import java.awt.Font
import javax.swing.BorderFactory
import javax.swing.JTextArea

class RedGreenToolWindowFactory : ToolWindowFactory {
    override fun createToolWindowContent(project: Project, toolWindow: ToolWindow) {
        val panel = RedGreenToolWindow(project).root
        val content = ContentFactory.getInstance().createContent(panel, "", false)
        toolWindow.contentManager.addContent(content)
    }

    override fun shouldBeAvailable(project: Project): Boolean = true
}

/**
 * The tool-window UI — intentionally plain for M3.2/M3.3. A header + a
 * monospace log area that the action writes into. M5 will swap this for a
 * 4-row race table; for now, streaming text is enough to prove the hook works.
 */
class RedGreenToolWindow(project: Project) {

    private val log: JTextArea = JTextArea().apply {
        isEditable = false
        lineWrap = false
        font = Font("Monospaced", Font.PLAIN, 12)
        text = "RedGreen is idle.\nRight-click in an editor → \"RedGreen: Analyze Exception\" to smoke-test.\n\n"
    }

    val root: JBPanel<JBPanel<*>> = JBPanel<JBPanel<*>>(BorderLayout()).apply {
        border = BorderFactory.createEmptyBorder(8, 8, 8, 8)
        add(JBLabel("RedGreen · 4-agent race"), BorderLayout.NORTH)
        add(JBScrollPane(log), BorderLayout.CENTER)
    }

    init {
        // Register this instance so actions can append into it.
        RedGreenToolWindowRegistry.register(project, this)
    }

    fun appendLine(line: String) {
        log.append(line.trimEnd() + "\n")
        log.caretPosition = log.document.length
    }

    fun clearAnd(header: String) {
        log.text = header.trimEnd() + "\n"
        log.caretPosition = log.document.length
    }
}

/**
 * Tiny global registry so an Action can find the tool window instance for a
 * given project without leaning on IntelliJ's message bus for M3.2. Good enough
 * for the hackathon.
 */
object RedGreenToolWindowRegistry {
    private val byProject = java.util.concurrent.ConcurrentHashMap<Int, RedGreenToolWindow>()

    fun register(project: Project, window: RedGreenToolWindow) {
        byProject[System.identityHashCode(project)] = window
    }

    fun get(project: Project): RedGreenToolWindow? =
        byProject[System.identityHashCode(project)]
}
