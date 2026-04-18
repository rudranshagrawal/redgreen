package com.redgreen

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
import javax.swing.BorderFactory
import javax.swing.JSplitPane
import javax.swing.JTable
import javax.swing.JTextArea
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
 * Tool window UI. All updates arrive through the set* methods below; none of
 * them assume any particular calling thread — callers (the controller) are
 * responsible for invokeLater-ing into the EDT.
 */
class RedGreenToolWindow(private val project: Project) {

    // ---------- top strip: title + subtitle + context ----------

    private val title = JBLabel("RedGreen · 4-agent race").apply {
        font = JBFont.h3()
        border = BorderFactory.createEmptyBorder(0, 0, 2, 0)
    }
    private val subtitle = JBLabel("Idle — run your code in Debug.").apply {
        font = JBFont.label().asBold()
    }
    private val contextLine = JBLabel(" ").apply {
        font = JBFont.small()
        foreground = JBColor.GRAY
        border = BorderFactory.createEmptyBorder(2, 0, 6, 0)
    }

    // ---------- middle: 4-row race table ----------

    private val agentTableModel = object : DefaultTableModel(
        arrayOf("Agent", "Model", "Phase", "Time", "Note"), 0,
    ) {
        override fun isCellEditable(row: Int, col: Int): Boolean = false
    }

    private var winnerAgentName: String? = null
    private var lastStatus: StatusResponse? = null

    private val statusRenderer = StatusCellRenderer { winnerAgentName }
    private val winnerRowRenderer = WinnerRowRenderer { winnerAgentName }

    private val agentTable = JBTable(agentTableModel).apply {
        setShowGrid(false)
        intercellSpacing = Dimension(0, 0)
        rowHeight = 28
        selectionModel.selectionMode = ListSelectionModel.SINGLE_SELECTION
        setDefaultRenderer(Any::class.java, winnerRowRenderer)
        // Fixed column widths + horizontal scroll when the tool window is narrow.
        // Previously columns auto-shrank to "M..." "P..." — unreadable.
        autoResizeMode = JTable.AUTO_RESIZE_OFF
        tableHeader.reorderingAllowed = false
        columnModel.getColumn(0).apply { preferredWidth = 220; minWidth = 180 }
        columnModel.getColumn(1).apply { preferredWidth = 260; minWidth = 160 }
        columnModel.getColumn(2).apply { preferredWidth = 260; minWidth = 200 }
        columnModel.getColumn(3).apply { preferredWidth = 90;  minWidth = 70 }
        columnModel.getColumn(4).apply { preferredWidth = 420; minWidth = 160 }
        // Status column uses the phase-aware renderer.
        columnModel.getColumn(2).cellRenderer = statusRenderer
        selectionModel.addListSelectionListener { e ->
            if (!e.valueIsAdjusting) refreshDetailsPane()
        }
    }

    // ---------- details pane: shown below table, shows selected agent detail ----------

    private val detailHeader = JBLabel("Select an agent to see details.").apply {
        font = JBFont.label().asBold()
        border = BorderFactory.createEmptyBorder(8, 0, 4, 0)
    }
    private val detailBody = JTextArea().apply {
        isEditable = false
        lineWrap = true
        wrapStyleWord = true
        font = Font("Monospaced", Font.PLAIN, 12)
    }
    private val detailPane: JBPanel<JBPanel<*>> = JBPanel<JBPanel<*>>(BorderLayout()).apply {
        border = JBUI.Borders.compound(
            BorderFactory.createMatteBorder(1, 0, 0, 0, JBColor.border()),
            JBUI.Borders.empty(6, 0, 0, 0),
        )
        add(detailHeader, BorderLayout.NORTH)
        add(JBScrollPane(detailBody).apply { preferredSize = Dimension(400, 120) }, BorderLayout.CENTER)
    }

    // ---------- bottom: winner panel ----------

    private val winnerPanel = WinnerPanel(project) { showIdle() }

    // ---------- banner (for no_winner / errors) ----------

    private val banner = JBLabel(" ").apply {
        border = BorderFactory.createEmptyBorder(4, 0, 4, 0)
    }

    val root: JBPanel<JBPanel<*>> = JBPanel<JBPanel<*>>(BorderLayout()).apply {
        border = JBUI.Borders.empty(10)

        val top = JBPanel<JBPanel<*>>()
        top.layout = javax.swing.BoxLayout(top, javax.swing.BoxLayout.Y_AXIS)
        top.add(wrap(title))
        top.add(wrap(subtitle))
        top.add(wrap(contextLine))
        top.add(wrap(banner))
        add(top, BorderLayout.NORTH)

        val center = JSplitPane(JSplitPane.VERTICAL_SPLIT).apply {
            topComponent = JBScrollPane(agentTable)
            bottomComponent = detailPane
            resizeWeight = 0.55
            isContinuousLayout = true
            dividerSize = 4
            border = BorderFactory.createEmptyBorder()
        }
        add(center, BorderLayout.CENTER)

        add(winnerPanel.root, BorderLayout.SOUTH)
        winnerPanel.root.isVisible = false
    }

    init {
        RedGreenToolWindowRegistry.register(project, this)
        showIdle()
    }

    // ---------- state transitions ----------

    fun showIdle() {
        subtitle.text = "Idle — run your code in Debug."
        contextLine.text = "When the debugger trips an exception, RedGreen fires automatically."
        banner.text = " "
        winnerAgentName = null
        clearAgents()
        winnerPanel.root.isVisible = false
        detailHeader.text = "Select an agent to see details."
        detailBody.text = ""
    }

    fun showAnalyzing(payload: AnalyzePayload) {
        subtitle.text = "Captured exception — preparing race…"
        contextLine.text = "${payload.frame_file}:${payload.frame_line}"
        banner.text = " "
        winnerAgentName = null
        clearAgents()
        // Seed the 4 expected lanes so the table doesn't flash empty.
        for ((agent, model) in DEFAULT_POOL) {
            agentTableModel.addRow(arrayOf<Any>(humanAgentName(agent), model, "queued", "—", ""))
        }
        winnerPanel.root.isVisible = false
        detailHeader.text = "Select an agent to see details."
        detailBody.text = ""
    }

    fun showRacing(episodeId: String) {
        subtitle.text = "Racing 4 agents in parallel…"
        // The placeholder episode id is an internal artifact; don't show it.
        if (!episodeId.startsWith("pending-")) {
            contextLine.text = "${contextLine.text.substringBefore(" · ")} · episode ${episodeId.take(8)}"
        }
    }

    fun showStatus(status: StatusResponse) {
        lastStatus = status
        status.agents.forEach(::appendOrUpdateAgent)

        when (status.state) {
            "racing" -> {
                subtitle.text = "Racing 4 agents in parallel…"
            }
            "completed" -> {
                subtitle.text = "Race complete · winner found."
                status.winner?.let {
                    winnerAgentName = it.agent
                    winnerPanel.show(it, status.leaderboard_row)
                }
                banner.text = " "
                // Trigger re-render with winner highlight.
                agentTable.repaint()
                refreshDetailsPane()
            }
            "no_winner" -> {
                subtitle.text = "Race complete · no winner."
                banner.text = "All four models failed their gates. Click a row for details."
                banner.foreground = JBColor.ORANGE
            }
        }
    }

    fun showError(msg: String) {
        subtitle.text = "Something broke."
        banner.text = msg
        banner.foreground = JBColor.RED
    }

    // ---------- table plumbing ----------

    private fun appendOrUpdateAgent(a: AgentResult) {
        val existing = (0 until agentTableModel.rowCount).firstOrNull {
            val cell = agentTableModel.getValueAt(it, 0) as? String ?: return@firstOrNull false
            // We display the humanized name but key on the raw agent id via suffix.
            cell.startsWith(humanAgentName(a.agent).substringBefore(" "))
        }
        val phaseText = phaseLabel(a)
        val timeText = humanizeMs(a.elapsed_ms)
        val note = a.eliminated_reason?.let { truncateOneLine(it, 200) } ?: ""
        val row = arrayOf<Any>(humanAgentName(a.agent), a.model, phaseText, timeText, note)
        if (existing != null) {
            for (c in row.indices) agentTableModel.setValueAt(row[c], existing, c)
        } else {
            agentTableModel.addRow(row)
        }
    }

    private fun clearAgents() {
        while (agentTableModel.rowCount > 0) agentTableModel.removeRow(0)
    }

    /** Phase label combines status + elapsed_ms to give richer live feedback. */
    private fun phaseLabel(a: AgentResult): String = when {
        a.status == "pending" && a.elapsed_ms == 0 -> "waiting for model…"
        a.status == "pending" && a.elapsed_ms > 0 -> "model done · running RED gate"
        a.status == "red_ok" -> "RED ✓ · running GREEN gate"
        a.status == "red_failed" -> "RED ✗ failed"
        a.status == "green_ok" -> if (a.agent == winnerAgentName) "🏆 WINNER · GREEN ✓" else "GREEN ✓ passed"
        a.status == "green_failed" -> "GREEN ✗ failed"
        a.status == "error" -> "model error"
        else -> a.status
    }

    private fun refreshDetailsPane() {
        val row = agentTable.selectedRow
        if (row < 0) {
            detailHeader.text = "Select an agent to see details."
            detailBody.text = ""
            return
        }
        val displayName = agentTableModel.getValueAt(row, 0) as? String ?: return
        val rawAgent = displayName.substringBefore(" ").lowercase()
        // Try exact match first; fall back to substring.
        val match = lastStatus?.agents?.firstOrNull { it.agent == rawAgent }
            ?: lastStatus?.agents?.firstOrNull { displayName.startsWith(humanAgentName(it.agent).substringBefore(" ")) }

        if (match == null) {
            detailHeader.text = "No data for that row yet."
            detailBody.text = ""
            return
        }

        detailHeader.text = "${humanAgentName(match.agent)} — ${match.model} — ${phaseLabel(match)}"
        val reason = match.eliminated_reason ?: "(no elimination reason — agent is still racing or won)"
        detailBody.text = reason
        detailBody.caretPosition = 0
    }

    private fun wrap(c: Component) = JBPanel<JBPanel<*>>(BorderLayout()).apply {
        isOpaque = false
        add(c, BorderLayout.WEST)
    }

    companion object {
        /** Matches backend/providers/__init__.py defaults; used to seed placeholder rows. */
        val DEFAULT_POOL = listOf(
            "null_guard" to "gpt-5-mini",
            "input_shape" to "meta-llama/Llama-3.3-70B-Instruct",
            "async_race" to "Qwen/Qwen3-32B",
            "config_drift" to "deepseek-ai/DeepSeek-V3.2-fast",
        )

        fun humanAgentName(agent: String): String = when (agent) {
            "null_guard" -> "null_guard · 'what's None?'"
            "input_shape" -> "input_shape · 'wrong shape?'"
            "async_race" -> "async_race · 'bad ordering?'"
            "config_drift" -> "config_drift · 'wrong config?'"
            else -> agent
        }

        fun humanizeMs(ms: Int): String = when {
            ms <= 0 -> "—"
            ms < 1_000 -> "${ms}ms"
            ms < 60_000 -> "%.1fs".format(ms / 1000.0)
            else -> "${ms / 60_000}m${(ms % 60_000) / 1000}s"
        }

        fun truncateOneLine(s: String, max: Int): String {
            val oneLine = s.replace('\n', ' ').trim()
            return if (oneLine.length <= max) oneLine else oneLine.take(max - 1) + "…"
        }
    }
}


/**
 * Renderer for the "Phase" column: colors based on the full phase label text.
 */
private class StatusCellRenderer(
    private val winnerAgent: () -> String?,
) : DefaultTableCellRenderer() {
    override fun getTableCellRendererComponent(
        table: JTable?, value: Any?, isSelected: Boolean, hasFocus: Boolean,
        row: Int, column: Int,
    ): Component {
        val c = super.getTableCellRendererComponent(table, value, isSelected, hasFocus, row, column)
        if (value is String) {
            foreground = when {
                value.startsWith("🏆") -> JBColor(0xB88800, 0xE0B84B)
                value.startsWith("GREEN ✓") -> JBColor(0x3FB950, 0x3FB950)
                value.startsWith("RED ✓") -> JBColor(0xCF8A4B, 0xE0A05A)
                value.contains("✗") || value == "model error" -> JBColor(0xE04B4B, 0xE04B4B)
                value.startsWith("waiting") || value.startsWith("queued") -> JBColor.GRAY
                else -> JBColor.foreground()
            }
            font = if (value.startsWith("🏆")) JBFont.label().asBold() else JBFont.label()
        }
        return c
    }
}


/**
 * Row-level background/bold for the winning row.
 */
private class WinnerRowRenderer(
    private val winnerAgent: () -> String?,
) : DefaultTableCellRenderer() {
    override fun getTableCellRendererComponent(
        table: JTable?, value: Any?, isSelected: Boolean, hasFocus: Boolean,
        row: Int, column: Int,
    ): Component {
        val c = super.getTableCellRendererComponent(table, value, isSelected, hasFocus, row, column)
        val winner = winnerAgent()
        val isWinnerRow = winner != null && table != null &&
            (table.model.getValueAt(row, 0) as? String)?.startsWith(
                RedGreenToolWindow.humanAgentName(winner).substringBefore(" ")
            ) == true
        if (isWinnerRow && !isSelected) {
            background = JBColor(0x1F3D2E, 0x1F3D2E)
            foreground = JBColor(0x7ED98F, 0x7ED98F)
            font = JBFont.label().asBold()
        } else if (!isSelected) {
            background = table?.background
            foreground = JBColor.foreground()
            font = JBFont.label()
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
