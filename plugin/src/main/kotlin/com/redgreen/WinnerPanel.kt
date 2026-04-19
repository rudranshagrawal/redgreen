package com.redgreen

import com.intellij.openapi.project.Project
import com.intellij.openapi.ui.Messages
import com.intellij.openapi.vfs.LocalFileSystem
import com.redgreen.inlay.ApplyBridge
import com.intellij.ui.JBColor
import com.intellij.ui.components.JBLabel
import com.intellij.ui.components.JBPanel
import com.intellij.ui.components.JBScrollPane
import com.intellij.util.ui.JBFont
import com.intellij.util.ui.JBUI
import java.awt.BorderLayout
import java.awt.Color
import java.awt.Component
import java.awt.Dimension
import java.awt.FlowLayout
import java.awt.Font
import javax.swing.BorderFactory
import javax.swing.JButton
import javax.swing.JTextPane
import javax.swing.text.SimpleAttributeSet
import javax.swing.text.StyleConstants


class WinnerPanel(
    private val project: Project,
    private val onDismiss: () -> Unit,
) {
    private val title = JBLabel(" ").apply {
        font = JBFont.h3().asBold()
        foreground = JBColor(0x3FB950, 0x3FB950)
    }
    private val scoreLine = JBLabel(" ").apply {
        font = JBFont.small()
        foreground = JBColor.GRAY
    }
    private val leaderboardHint = JBLabel(" ").apply {
        font = JBFont.small()
        foreground = JBColor.GRAY
    }
    private val rationaleLabel = JBLabel("Why").apply {
        font = JBFont.small().asBold()
        foreground = JBColor.GRAY
        border = BorderFactory.createEmptyBorder(6, 0, 2, 0)
    }
    private val rationale = JTextPane().apply {
        isEditable = false
        font = JBFont.small()
        foreground = JBColor.GRAY
        background = null
        border = BorderFactory.createEmptyBorder(0, 0, 0, 0)
    }
    private val judgeLabel = JBLabel("Judge’s note").apply {
        font = JBFont.small().asBold()
        foreground = JBColor(0xB88800, 0xE0B84B)
        border = BorderFactory.createEmptyBorder(6, 0, 2, 0)
    }
    private val judgeNote = JTextPane().apply {
        isEditable = false
        font = JBFont.small()
        foreground = JBColor.GRAY
        background = null
        border = BorderFactory.createEmptyBorder(0, 0, 0, 0)
    }
    private val judgeBlock = JBPanel<JBPanel<*>>(BorderLayout()).apply {
        isOpaque = false
        add(judgeLabel, BorderLayout.NORTH)
        add(judgeNote, BorderLayout.CENTER)
    }
    private val diffPane = JTextPane().apply {
        isEditable = false
        font = Font("Monospaced", Font.PLAIN, 12)
        border = BorderFactory.createEmptyBorder(6, 6, 6, 6)
    }
    private val applyBtn = JButton("Apply patch").apply {
        addActionListener { applyCurrentPatch() }
    }
    private val dismissBtn = JButton("Dismiss").apply {
        addActionListener { onDismiss() }
    }

    private var currentWinner: Winner? = null

    init {
        // Let the editor inlay invoke the same apply path.
        ApplyBridge.register { _, winner ->
            if (winner === currentWinner) applyCurrentPatch()
        }
    }

    val root: JBPanel<JBPanel<*>> = JBPanel<JBPanel<*>>(BorderLayout()).apply {
        border = JBUI.Borders.compound(
            BorderFactory.createMatteBorder(1, 0, 0, 0, JBColor.border()),
            JBUI.Borders.empty(10, 0, 0, 0),
        )

        // Header: big title, one score line, one leaderboard line. That's it.
        val header = JBPanel<JBPanel<*>>()
        header.layout = javax.swing.BoxLayout(header, javax.swing.BoxLayout.Y_AXIS)
        header.add(wrap(title))
        header.add(wrap(scoreLine))
        header.add(wrap(leaderboardHint))
        add(header, BorderLayout.NORTH)

        // Center: diff is the hero. Rationale + judge quote stack below in a
        // compact, scrolling area so they never dominate.
        val center = JBPanel<JBPanel<*>>(BorderLayout())
        center.isOpaque = false
        val diffScroll = JBScrollPane(diffPane).apply { preferredSize = Dimension(400, 220) }
        center.add(diffScroll, BorderLayout.CENTER)

        val notes = JBPanel<JBPanel<*>>()
        notes.isOpaque = false
        notes.layout = javax.swing.BoxLayout(notes, javax.swing.BoxLayout.Y_AXIS)
        notes.add(rationaleLabel)
        notes.add(rationale)
        notes.add(judgeBlock)
        val notesScroll = JBScrollPane(notes).apply {
            preferredSize = Dimension(400, 90)
            border = BorderFactory.createEmptyBorder()
        }
        center.add(notesScroll, BorderLayout.SOUTH)
        add(center, BorderLayout.CENTER)

        val buttons = JBPanel<JBPanel<*>>(FlowLayout(FlowLayout.LEFT, 6, 6))
        buttons.add(applyBtn)
        buttons.add(dismissBtn)
        add(buttons, BorderLayout.SOUTH)
    }

    fun show(winner: Winner, leaderboard: LeaderboardRow?, isSyntaxMode: Boolean = false) {
        currentWinner = winner
        applyBtn.isEnabled = true
        applyBtn.text = "Apply patch"
        dismissBtn.text = "Dismiss"

        val addedLines = winner.patch_unified_diff.lines().count { it.startsWith("+") && !it.startsWith("+++ ") }
        val removedLines = winner.patch_unified_diff.lines().count { it.startsWith("-") && !it.startsWith("--- ") }
        val friendlyTime = RedGreenToolWindow.humanizeMs(winner.total_elapsed_ms)
        title.text = if (isSyntaxMode) {
            "🛠  Syntax fix  ·  ${winner.model}"
        } else {
            "🏆  ${RedGreenToolWindow.humanAgentName(winner.agent).substringBefore(" ·")}  ·  ${winner.model}"
        }

        // One compact line of scores — the thing the old panel buried under rationale.
        val totalCV = winner.cross_val_passed + winner.cross_val_failed
        val totalReg = winner.regression_passed + winner.regression_failed
        val parts = mutableListOf<String>()
        parts += "+$addedLines / −$removedLines lines"
        parts += friendlyTime
        if (totalCV > 0) parts += "peer ${winner.cross_val_passed}/$totalCV"
        if (totalReg > 0) parts += "regression ${winner.regression_passed}/$totalReg"
        scoreLine.text = parts.joinToString("  ·  ")

        // Split the combined rationale into model-voice + judge-voice blocks so
        // neither walls of text dominate the panel.
        val rawRationale = winner.rationale.ifBlank { "(no rationale)" }
        val judgeMarker = "[Judge]"
        val idx = rawRationale.indexOf(judgeMarker)
        if (idx >= 0) {
            val model = rawRationale.substring(0, idx).trim().trimEnd(',', '.', ';').take(280)
            val judge = rawRationale.substring(idx + judgeMarker.length).trim().take(320)
            rationale.text = model.ifBlank { "(no rationale)" }
            judgeNote.text = judge
            judgeBlock.isVisible = judge.isNotBlank()
        } else {
            rationale.text = rawRationale.take(360)
            judgeNote.text = ""
            judgeBlock.isVisible = false
        }
        rationale.caretPosition = 0
        judgeNote.caretPosition = 0

        leaderboardHint.text = if (isSyntaxMode) {
            "Single-model fast-path · no race · no Docker gates"
        } else {
            leaderboard?.let {
                "On this codebase: ${it.agent} leads ${it.wins}W / ${it.losses}L (avg ${RedGreenToolWindow.humanizeMs(it.avg_ms)})"
            } ?: "First episode on this codebase — no priors yet."
        }
        renderColoredDiff(winner.patch_unified_diff)
        root.isVisible = true
        root.revalidate()
    }

    private fun renderColoredDiff(diff: String) {
        val doc = diffPane.styledDocument
        doc.remove(0, doc.length)

        val added = SimpleAttributeSet().apply {
            StyleConstants.setForeground(this, JBColor(0x3FB950, 0x7ED98F))
            StyleConstants.setBackground(this, JBColor(0x0E2B18, 0x0E2B18))
        }
        val removed = SimpleAttributeSet().apply {
            StyleConstants.setForeground(this, JBColor(0xE04B4B, 0xF08080))
            StyleConstants.setBackground(this, JBColor(0x2B0E0E, 0x2B0E0E))
        }
        val meta = SimpleAttributeSet().apply {
            StyleConstants.setForeground(this, JBColor(0x888888, 0xAAAAAA))
            StyleConstants.setBold(this, true)
        }
        val context = SimpleAttributeSet().apply {
            StyleConstants.setForeground(this, JBColor.foreground())
        }

        for (line in diff.lines()) {
            val style = when {
                line.startsWith("+++") || line.startsWith("---") || line.startsWith("@@") -> meta
                line.startsWith("+") -> added
                line.startsWith("-") -> removed
                else -> context
            }
            doc.insertString(doc.length, line + "\n", style)
        }
        diffPane.caretPosition = 0
    }

    private fun applyCurrentPatch() {
        val winner = currentWinner ?: return
        val repoRoot = project.basePath ?: run {
            Messages.showErrorDialog(project, "Project has no base path; can't apply.", "RedGreen")
            return
        }

        val result = runCatching {
            UnifiedDiffApplier.apply(repoRoot, winner.patch_unified_diff)
        }
        result.onFailure {
            // Most likely the file was already edited. Offer a clear next step
            // instead of a wall-of-text stacktrace.
            val msg = it.message ?: "unknown"
            val hint = if (msg.contains("does not match any location")) {
                "\n\nLikely cause: the target file was already modified (maybe a previous apply). Revert with:\n    git restore <path>\nthen re-run Debug to trigger a fresh race."
            } else ""
            Messages.showErrorDialog(project, "Patch apply failed:\n$msg$hint", "RedGreen")
            return
        }
        val touched = result.getOrDefault(emptyList())

        val testFile = java.io.File(repoRoot, "tests/test_redgreen_generated.py")
        testFile.parentFile.mkdirs()
        testFile.writeText(winner.test_code)

        val refreshed = touched.mapNotNull {
            LocalFileSystem.getInstance().refreshAndFindFileByIoFile(java.io.File(it))
        } + listOfNotNull(LocalFileSystem.getInstance().refreshAndFindFileByIoFile(testFile))

        // Inline success feedback — flip the apply button + update the title,
        // no modal blocking the user.
        title.text = "✓ Applied · ${RedGreenToolWindow.humanAgentName(winner.agent)}"
        applyBtn.isEnabled = false
        applyBtn.text = "Applied ✓"
        dismissBtn.text = "Close"
        leaderboardHint.text = "Patched ${touched.size} file(s) · wrote tests/test_redgreen_generated.py. Run pytest to verify."

        refreshed.firstOrNull()?.let {
            com.intellij.openapi.fileEditor.FileEditorManager.getInstance(project).openFile(it, true)
        }
    }

    private fun wrap(c: Component) = JBPanel<JBPanel<*>>(BorderLayout()).apply {
        isOpaque = false
        add(c, BorderLayout.WEST)
    }
}
