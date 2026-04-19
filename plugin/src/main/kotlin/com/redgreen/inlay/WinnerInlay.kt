package com.redgreen.inlay

import com.intellij.openapi.application.ApplicationManager
import com.intellij.openapi.editor.Editor
import com.intellij.openapi.editor.Inlay
import com.intellij.openapi.editor.EditorCustomElementRenderer
import com.intellij.openapi.editor.event.EditorMouseEvent
import com.intellij.openapi.editor.event.EditorMouseListener
import com.intellij.openapi.editor.markup.TextAttributes
import com.intellij.openapi.fileEditor.FileEditorManager
import com.intellij.openapi.project.Project
import com.intellij.openapi.vfs.LocalFileSystem
import com.intellij.ui.JBColor
import com.intellij.util.ui.JBFont
import com.intellij.util.ui.JBUI
import com.redgreen.Winner
import java.awt.Cursor
import java.awt.Graphics
import java.awt.Graphics2D
import java.awt.Rectangle
import java.awt.RenderingHints
import java.io.File
import java.util.concurrent.ConcurrentHashMap

/**
 * Block inlay that floats below the failing line of the winning patch. Gives
 * the user a visible "fix is ready here" affordance right in the editor —
 * the thing the original plan called the gutter suggestion.
 *
 * Click the inlay to apply the patch. The inlay auto-clears itself after
 * apply or after 2 minutes, whichever comes first.
 */
object WinnerInlay {

    // Track active inlays so the next episode can clear the previous one.
    private val active = ConcurrentHashMap<Project, Inlay<*>>()

    fun show(project: Project, frameFile: String, frameLine: Int, winner: Winner) {
        ApplicationManager.getApplication().invokeLater {
            try {
                placeInlay(project, frameFile, frameLine, winner)
            } catch (_: Throwable) {
                // Non-fatal: the tool window's Apply button still works.
            }
        }
    }

    fun clear(project: Project) {
        active.remove(project)?.dispose()
    }

    private fun placeInlay(project: Project, frameFile: String, frameLine: Int, winner: Winner) {
        val base = project.basePath ?: return
        val absPath = if (File(frameFile).isAbsolute) frameFile else "$base/$frameFile"
        val vf = LocalFileSystem.getInstance().refreshAndFindFileByPath(absPath) ?: return

        val editor: Editor = FileEditorManager.getInstance(project)
            .openTextEditor(com.intellij.openapi.fileEditor.OpenFileDescriptor(project, vf, frameLine - 1, 0), true)
            ?: return

        active.remove(project)?.dispose()

        val addedLines = winner.patch_unified_diff.lines().count { it.startsWith("+") && !it.startsWith("+++ ") }
        val removedLines = winner.patch_unified_diff.lines().count { it.startsWith("-") && !it.startsWith("--- ") }
        val label = "⚡ RedGreen: fix ready · +$addedLines / −$removedLines · click to apply"

        val offset = editor.document.getLineStartOffset((frameLine - 1).coerceIn(0, editor.document.lineCount - 1))

        val renderer = LabelRenderer(label)
        val inlay = editor.inlayModel.addBlockElement(
            offset, /*relatesToPrecedingText=*/ true, /*showAbove=*/ false, /*priority=*/ 0, renderer,
        ) ?: return

        active[project] = inlay

        // Click-to-apply wiring.
        val listener = object : EditorMouseListener {
            override fun mouseClicked(event: EditorMouseEvent) {
                val inlayAtPoint = editor.inlayModel.getElementAt(event.mouseEvent.point)
                if (inlayAtPoint === inlay) {
                    event.consume()
                    ApplyBridge.apply(project, winner)
                    active.remove(project)
                    inlay.dispose()
                }
            }
        }
        editor.addEditorMouseListener(listener)
        editor.contentComponent.cursor = Cursor.getPredefinedCursor(Cursor.DEFAULT_CURSOR)

        // Auto-clear after 2 min so stale inlays don't haunt the editor.
        ApplicationManager.getApplication().executeOnPooledThread {
            Thread.sleep(120_000)
            ApplicationManager.getApplication().invokeLater {
                if (active[project] === inlay) {
                    active.remove(project)
                    if (!inlay.isValid) return@invokeLater
                    inlay.dispose()
                    editor.removeEditorMouseListener(listener)
                }
            }
        }
    }
}


private class LabelRenderer(private val text: String) : EditorCustomElementRenderer {
    override fun calcWidthInPixels(inlay: Inlay<*>): Int {
        val metrics = inlay.editor.contentComponent.getFontMetrics(JBFont.label())
        return metrics.stringWidth(text) + JBUI.scale(24)
    }

    override fun calcHeightInPixels(inlay: Inlay<*>): Int {
        val metrics = inlay.editor.contentComponent.getFontMetrics(JBFont.label())
        return metrics.height + JBUI.scale(8)
    }

    override fun paint(inlay: Inlay<*>, g: Graphics, r: Rectangle, textAttrs: TextAttributes) {
        val g2 = g as Graphics2D
        g2.setRenderingHint(RenderingHints.KEY_ANTIALIASING, RenderingHints.VALUE_ANTIALIAS_ON)
        g2.setRenderingHint(RenderingHints.KEY_TEXT_ANTIALIASING, RenderingHints.VALUE_TEXT_ANTIALIAS_ON)

        // Rounded background — subtle green tint.
        val pad = JBUI.scale(4)
        g2.color = JBColor(0x1E3A2A, 0x1E3A2A)
        g2.fillRoundRect(r.x + pad, r.y + pad, r.width - pad * 2, r.height - pad * 2, JBUI.scale(6), JBUI.scale(6))

        // Text
        g2.font = JBFont.label()
        g2.color = JBColor(0x7ED98F, 0x7ED98F)
        val metrics = g2.fontMetrics
        val tx = r.x + JBUI.scale(12)
        val ty = r.y + (r.height + metrics.ascent - metrics.descent) / 2
        g2.drawString(text, tx, ty)
    }
}


/**
 * Thin bridge so the inlay can trigger WinnerPanel's apply logic without
 * circular deps. WinnerPanel registers itself on show; inlay calls apply()
 * which runs the same code path the Apply button does.
 */
object ApplyBridge {
    @Volatile private var handler: ((Project, Winner) -> Unit)? = null

    fun register(fn: (Project, Winner) -> Unit) {
        handler = fn
    }

    fun apply(project: Project, winner: Winner) {
        handler?.invoke(project, winner)
    }
}
