package com.redgreen

import com.intellij.openapi.application.ApplicationManager
import com.intellij.openapi.command.WriteCommandAction
import com.intellij.openapi.project.Project
import com.intellij.openapi.ui.Messages
import com.intellij.openapi.vfs.LocalFileSystem
import com.intellij.openapi.vfs.VirtualFile
import com.intellij.ui.JBColor
import com.intellij.ui.components.JBLabel
import com.intellij.ui.components.JBPanel
import com.intellij.ui.components.JBScrollPane
import com.intellij.util.ui.JBFont
import com.intellij.util.ui.JBUI
import java.awt.BorderLayout
import java.awt.Dimension
import java.awt.FlowLayout
import javax.swing.BorderFactory
import javax.swing.JButton
import javax.swing.JTextArea


class WinnerPanel(
    private val project: Project,
    private val onDismiss: () -> Unit,
) {
    private val title = JBLabel(" ").apply { font = JBFont.label().asBold() }
    private val leaderboardHint = JBLabel(" ").apply {
        font = JBFont.small()
        foreground = JBColor.GRAY
    }
    private val rationale = JBLabel(" ").apply {
        foreground = JBColor.GRAY
    }
    private val diffArea = JTextArea().apply {
        isEditable = false
        lineWrap = false
        font = java.awt.Font("Monospaced", java.awt.Font.PLAIN, 12)
        border = BorderFactory.createEmptyBorder(4, 4, 4, 4)
    }
    private val applyBtn = JButton("Apply patch").apply {
        addActionListener { applyCurrentPatch() }
    }
    private val dismissBtn = JButton("Dismiss").apply {
        addActionListener { onDismiss() }
    }

    private var currentWinner: Winner? = null

    val root: JBPanel<JBPanel<*>> = JBPanel<JBPanel<*>>(BorderLayout()).apply {
        border = JBUI.Borders.compound(
            BorderFactory.createMatteBorder(1, 0, 0, 0, JBColor.border()),
            JBUI.Borders.empty(8, 0, 0, 0),
        )

        val header = JBPanel<JBPanel<*>>()
        header.layout = javax.swing.BoxLayout(header, javax.swing.BoxLayout.Y_AXIS)
        header.add(wrap(title))
        header.add(wrap(rationale))
        header.add(wrap(leaderboardHint))
        add(header, BorderLayout.NORTH)

        val scroll = JBScrollPane(diffArea)
        scroll.preferredSize = Dimension(400, 180)
        add(scroll, BorderLayout.CENTER)

        val buttons = JBPanel<JBPanel<*>>(FlowLayout(FlowLayout.LEFT, 6, 0))
        buttons.add(applyBtn)
        buttons.add(dismissBtn)
        add(buttons, BorderLayout.SOUTH)
    }

    fun show(winner: Winner, leaderboard: LeaderboardRow?) {
        currentWinner = winner
        title.text = "Winner · ${winner.agent} → ${winner.model} · +${winner.patch_unified_diff.count { it == '\n' && true }} lines · ${winner.total_elapsed_ms} ms"
        rationale.text = winner.rationale.take(240)
        leaderboardHint.text = leaderboard?.let {
            "Leaderboard: ${it.agent} ${it.wins}W / ${it.losses}L on this codebase"
        } ?: " "
        diffArea.text = winner.patch_unified_diff
        diffArea.caretPosition = 0
        root.isVisible = true
        root.revalidate()
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
            Messages.showErrorDialog(project, "Patch apply failed:\n${it.message}", "RedGreen")
            return
        }
        val touched = result.getOrDefault(emptyList())

        // Write the generated test file as tests/test_redgreen_generated.py so
        // the user can re-verify locally.
        val testFile = java.io.File(repoRoot, "tests/test_redgreen_generated.py")
        testFile.parentFile.mkdirs()
        testFile.writeText(winner.test_code)

        val refreshed = touched.mapNotNull {
            LocalFileSystem.getInstance().refreshAndFindFileByIoFile(java.io.File(it))
        } + listOfNotNull(LocalFileSystem.getInstance().refreshAndFindFileByIoFile(testFile))

        Messages.showInfoMessage(
            project,
            "Applied ${touched.size} file(s) + wrote tests/test_redgreen_generated.py.\n\nRun `pytest` to re-verify.",
            "RedGreen",
        )
        // Try to re-focus the user on a patched file.
        refreshed.firstOrNull()?.let {
            com.intellij.openapi.fileEditor.FileEditorManager.getInstance(project).openFile(it, true)
        }
    }

    private fun wrap(c: java.awt.Component) = JBPanel<JBPanel<*>>(BorderLayout()).apply {
        isOpaque = false
        add(c, BorderLayout.WEST)
    }
}
