package com.redgreen

import com.intellij.openapi.editor.colors.EditorColorsManager
import com.intellij.openapi.project.Project
import com.intellij.openapi.wm.ToolWindow
import com.intellij.openapi.wm.ToolWindowFactory
import com.intellij.ui.JBColor
import com.intellij.ui.components.JBLabel
import com.intellij.ui.components.JBPanel
import com.intellij.ui.components.JBScrollPane
import com.intellij.ui.content.ContentFactory
import com.intellij.ui.table.JBTable
import com.intellij.util.ui.JBFont
import com.intellij.util.ui.JBUI
import java.awt.BorderLayout
import java.awt.Component
import java.awt.Dimension
import java.awt.Font
import java.awt.GridBagConstraints
import java.awt.GridBagLayout
import javax.swing.BorderFactory
import javax.swing.JTable
import javax.swing.ListSelectionModel
import javax.swing.table.DefaultTableCellRenderer
import javax.swing.table.DefaultTableModel

class RedGreenToolWindowFactory : ToolWindowFactory {
    override fun createToolWindowContent(project: Project, toolWindow: ToolWindow) {
        val panel = RedGreenToolWindow(project).root
        val content = ContentFactory.getInstance().createContent(panel, "", false)
        toolWindow.contentManager.addContent(content)
    }

    override fun shouldBeAvailable(project: Project): Boolean = true
}


/**
 * Tool window UI, driven by RedGreenController.
 *
 * State machine:
 *   IDLE            -> header only, help text
 *   ANALYZING       -> header + episode metadata + "reaching backend..."
 *   RACING          -> header + episode metadata + 4-row agent table (live)
 *   COMPLETED_WIN   -> all of the above + winner panel (patch preview + apply)
 *   COMPLETED_NONE  -> header + agent table + "no winner" banner
 *   ERROR           -> header + error banner
 */
class RedGreenToolWindow(private val project: Project) {

    private val headerStatus = JBLabel("RedGreen is idle.").apply {
        border = BorderFactory.createEmptyBorder(0, 0, 4, 0)
        font = JBFont.label().asBold()
    }
    private val episodeMeta = JBLabel(" ").apply {
        foreground = JBColor.GRAY
        font = JBFont.small()
    }

    private val agentTableModel = object : DefaultTableModel(
        arrayOf("Agent", "Model", "Status", "Time", "Note"), 0
    ) {
        override fun isCellEditable(row: Int, col: Int): Boolean = false
    }

    private val agentTable = JBTable(agentTableModel).apply {
        setShowGrid(false)
        intercellSpacing = Dimension(0, 0)
        setDefaultRenderer(Any::class.java, StatusCellRenderer)
        rowHeight = 26
        selectionModel.selectionMode = ListSelectionModel.SINGLE_SELECTION
        columnModel.getColumn(0).preferredWidth = 110
        columnModel.getColumn(1).preferredWidth = 200
        columnModel.getColumn(2).preferredWidth = 90
        columnModel.getColumn(3).preferredWidth = 60
        columnModel.getColumn(4).preferredWidth = 300
    }

    private val winnerPanel = WinnerPanel(project) { showIdle() }

    private val banner = JBLabel(" ").apply {
        border = BorderFactory.createEmptyBorder(6, 0, 6, 0)
    }

    val root: JBPanel<JBPanel<*>> = JBPanel<JBPanel<*>>(BorderLayout()).apply {
        border = JBUI.Borders.empty(10)

        val top = JBPanel<JBPanel<*>>()
        top.layout = javax.swing.BoxLayout(top, javax.swing.BoxLayout.Y_AXIS)
        top.add(wrap(JBLabel("RedGreen · 4-agent race").apply { font = JBFont.h3() }))
        top.add(wrap(headerStatus))
        top.add(wrap(episodeMeta))
        top.add(wrap(banner))
        add(top, BorderLayout.NORTH)

        add(JBScrollPane(agentTable), BorderLayout.CENTER)
        add(winnerPanel.root, BorderLayout.SOUTH)

        winnerPanel.root.isVisible = false
    }

    init {
        RedGreenToolWindowRegistry.register(project, this)
        showIdle()
    }

    // ---------- state transitions ----------

    fun showIdle() {
        headerStatus.text = "Idle. Run your code in Debug to catch the next exception."
        episodeMeta.text = " "
        clearAgents()
        banner.text = " "
        winnerPanel.root.isVisible = false
    }

    fun showAnalyzing(payload: AnalyzePayload) {
        headerStatus.text = "Analyzing exception…"
        episodeMeta.text = "${payload.frame_file}:${payload.frame_line}"
        banner.text = "Preparing request — snapshotting repo…"
        banner.foreground = JBColor.GRAY
        clearAgents()
        winnerPanel.root.isVisible = false
    }

    fun showRacing(episodeId: String) {
        headerStatus.text = "Racing 4 agents in parallel…"
        banner.text = "episode $episodeId"
        banner.foreground = JBColor.GRAY
    }

    fun showStatus(status: StatusResponse) {
        // Pre-populate the table with placeholder rows on first update so the
        // user always sees 4 lanes even before any agent has reported.
        if (agentTableModel.rowCount < 4 && status.agents.isNotEmpty()) {
            clearAgents()
            status.agents.forEach(::appendOrUpdateAgent)
        } else {
            status.agents.forEach(::appendOrUpdateAgent)
        }

        when (status.state) {
            "racing" -> {
                headerStatus.text = "Racing 4 agents in parallel…"
            }
            "completed" -> {
                headerStatus.text = "Winner found."
                status.winner?.let {
                    winnerPanel.show(it, status.leaderboard_row)
                }
                banner.text = " "
            }
            "no_winner" -> {
                headerStatus.text = "No agent survived both gates."
                banner.text = "All four models failed the RED or GREEN gate. Open their rows above for details."
                banner.foreground = JBColor.ORANGE
            }
        }
    }

    fun showError(msg: String) {
        headerStatus.text = "Something broke."
        banner.text = msg
        banner.foreground = JBColor.RED
    }

    // ---------- table plumbing ----------

    private fun appendOrUpdateAgent(a: AgentResult) {
        val existing = (0 until agentTableModel.rowCount).firstOrNull {
            agentTableModel.getValueAt(it, 0) == a.agent
        }
        val row = arrayOf<Any>(
            a.agent,
            a.model,
            humanStatus(a.status),
            "${a.elapsed_ms} ms".let { if (a.elapsed_ms == 0) "—" else it },
            a.eliminated_reason?.take(120) ?: "",
        )
        if (existing != null) {
            for (c in row.indices) agentTableModel.setValueAt(row[c], existing, c)
        } else {
            agentTableModel.addRow(row)
        }
    }

    private fun clearAgents() {
        while (agentTableModel.rowCount > 0) agentTableModel.removeRow(0)
    }

    private fun humanStatus(s: String): String = when (s) {
        "pending" -> "waiting"
        "red_ok" -> "RED ✓"
        "red_failed" -> "RED ✗"
        "green_ok" -> "GREEN ✓"
        "green_failed" -> "GREEN ✗"
        "error" -> "error"
        else -> s
    }

    private fun wrap(c: Component) = JBPanel<JBPanel<*>>(BorderLayout()).apply {
        isOpaque = false
        add(c, BorderLayout.WEST)
    }
}


/**
 * Custom renderer that colors GREEN/RED status text in the agent table.
 * Singleton — we don't need per-table state.
 */
private object StatusCellRenderer : DefaultTableCellRenderer() {
    override fun getTableCellRendererComponent(
        table: JTable?, value: Any?, isSelected: Boolean, hasFocus: Boolean,
        row: Int, column: Int,
    ): Component {
        val c = super.getTableCellRendererComponent(table, value, isSelected, hasFocus, row, column)
        if (column == 2 && value is String) {
            when {
                value.contains("GREEN ✓") -> foreground = JBColor(0x3FB950, 0x3FB950)
                value.contains("RED ✓") -> foreground = JBColor(0xCF8A4B, 0xCF8A4B)
                value.contains("✗") || value == "error" -> foreground = JBColor(0xE04B4B, 0xE04B4B)
                else -> foreground = JBColor.GRAY
            }
        }
        return c
    }
}


/**
 * Tiny global registry — lets the controller / action find the instance
 * without bouncing through the message bus. Good enough for the hackathon.
 */
object RedGreenToolWindowRegistry {
    private val byProject = java.util.concurrent.ConcurrentHashMap<Int, RedGreenToolWindow>()
    fun register(project: Project, window: RedGreenToolWindow) {
        byProject[System.identityHashCode(project)] = window
    }
    fun get(project: Project): RedGreenToolWindow? =
        byProject[System.identityHashCode(project)]
}
