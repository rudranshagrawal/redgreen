package com.redgreen.indexer

import com.intellij.openapi.components.Service
import com.intellij.openapi.components.service
import com.intellij.openapi.diagnostic.Logger
import com.intellij.openapi.project.Project
import com.intellij.openapi.roots.ProjectRootManager
import com.intellij.openapi.vfs.VirtualFile
import java.util.concurrent.atomic.AtomicReference
import kotlin.io.path.Path

/**
 * Background codebase scanner. On first request, walks a capped set of the
 * user's Python files and distills project conventions into a compact string
 * that the backend hands every racing agent.
 *
 * We sample — not index — to keep this cheap. ~50 files, first 200 lines of
 * each, basic regex extraction. Good enough to notice "this project uses
 * pydantic BaseModel", "they have a RefundError exception class",
 * "tests are pytest-style with fixtures", without dragging PSI.
 */
@Service(Service.Level.PROJECT)
class CodebaseIndexer(private val project: Project) {
    private val log = Logger.getInstance(CodebaseIndexer::class.java)

    /** Latest completed context, or null if never indexed. Atomic for async swaps. */
    private val contextRef = AtomicReference<String?>(null)

    /** True while a scan is running, to dedupe concurrent triggers. */
    @Volatile private var scanning = false

    fun getContext(): String? = contextRef.get()

    /**
     * Kicks off a scan if one isn't already running. Non-blocking.
     * Call from plugin startup, and also lazily at analyze-time if nothing
     * has been indexed yet.
     */
    fun ensureIndexed() {
        if (contextRef.get() != null || scanning) return
        scanning = true
        Thread({
            try {
                val summary = scanSync()
                contextRef.set(summary)
                log.info("RedGreen: codebase indexed (${summary.length} chars)")
            } catch (t: Throwable) {
                log.warn("RedGreen: codebase indexing failed", t)
            } finally {
                scanning = false
            }
        }, "RedGreen-CodebaseIndexer").start()
    }

    private fun scanSync(): String {
        val base = project.basePath ?: return ""
        val roots = ProjectRootManager.getInstance(project).contentRoots.toList()
        val rootPath = Path(base)

        val pyFiles = mutableListOf<VirtualFile>()
        for (root in roots) {
            collectPyFiles(root, pyFiles, MAX_FILES)
            if (pyFiles.size >= MAX_FILES) break
        }
        if (pyFiles.isEmpty()) return ""

        val imports = mutableMapOf<String, Int>()
        val exceptionClasses = mutableSetOf<String>()
        val domainExceptionUses = mutableMapOf<String, Int>()
        var usesPytest = 0
        var usesUnittest = 0
        var googleDocstrings = 0
        var sphinxDocstrings = 0
        var bareDocstrings = 0
        var asyncFiles = 0
        var dataclassHits = 0
        var pydanticHits = 0
        var loggerHits = 0
        var fromFutureAnnotations = 0

        for (vf in pyFiles) {
            val text = try { String(vf.contentsToByteArray()).take(MAX_FILE_CHARS) }
            catch (_: Throwable) { continue }

            if ("from __future__ import annotations" in text) fromFutureAnnotations++
            if ("import pytest" in text || "\nimport pytest\n" in text || "from pytest" in text) usesPytest++
            if ("import unittest" in text || "from unittest" in text) usesUnittest++
            if ("async def " in text) asyncFiles++
            if ("@dataclass" in text || "from dataclasses" in text) dataclassHits++
            if ("BaseModel" in text && "pydantic" in text) pydanticHits++
            if ("logging.getLogger" in text || "logger = " in text) loggerHits++

            // Imports (top of file only — faster + more signal-y)
            IMPORT_RX.findAll(text.take(3000)).take(10).forEach {
                val mod = it.groupValues[1]
                imports.merge(mod, 1, Int::plus)
            }

            // Exception class definitions (domain exceptions)
            EXCEPTION_DEF_RX.findAll(text).forEach {
                exceptionClasses += it.groupValues[1]
            }

            // Exception raises (which domain errors are actually used)
            RAISE_RX.findAll(text).forEach {
                domainExceptionUses.merge(it.groupValues[1], 1, Int::plus)
            }

            // Docstring style — sample the first docstring if any
            val dsMatch = DOCSTRING_RX.find(text)
            if (dsMatch != null) {
                val body = dsMatch.groupValues[1]
                when {
                    "\n    Args:\n" in body || "\n    Returns:\n" in body -> googleDocstrings++
                    ":param " in body || ":return:" in body -> sphinxDocstrings++
                    body.isNotBlank() -> bareDocstrings++
                }
            }

            val relPath = vf.path.removePrefix("$base/")
            if ("test" in relPath.lowercase() || "tests/" in relPath) {
                if ("def test_" in text) usesPytest++
            }
        }

        // ---- compose the summary ----
        val sb = StringBuilder()
        sb.appendLine("Python project scan (${pyFiles.size} files, truncated).")

        val topImports = imports.entries.sortedByDescending { it.value }.take(8)
        if (topImports.isNotEmpty()) {
            sb.appendLine("Common imports: " + topImports.joinToString { "${it.key}×${it.value}" })
        }

        if (exceptionClasses.isNotEmpty()) {
            sb.appendLine("Domain exception classes: " + exceptionClasses.sorted().joinToString())
            sb.appendLine("→ Prefer raising these over generic ValueError / RuntimeError when they fit.")
        }

        val topRaises = domainExceptionUses.entries.sortedByDescending { it.value }.take(5)
        if (topRaises.isNotEmpty()) {
            sb.appendLine("Commonly raised: " + topRaises.joinToString { "${it.key}×${it.value}" })
        }

        val testStyle = when {
            usesPytest > 0 && usesUnittest == 0 -> "pytest only"
            usesUnittest > 0 && usesPytest == 0 -> "unittest only"
            usesPytest > 0 && usesUnittest > 0 -> "mixed (prefer pytest for new tests)"
            else -> "no tests found"
        }
        sb.appendLine("Test style: $testStyle.")

        val docStyle = listOf(
            "Google" to googleDocstrings,
            "Sphinx" to sphinxDocstrings,
            "bare" to bareDocstrings,
        ).maxByOrNull { it.second }
        if (docStyle != null && docStyle.second > 0) {
            sb.appendLine("Docstring style: ${docStyle.first}.")
        }

        val extras = mutableListOf<String>()
        if (fromFutureAnnotations > pyFiles.size / 2) extras += "`from __future__ import annotations` is standard"
        if (dataclassHits >= 2) extras += "dataclasses used"
        if (pydanticHits >= 2) extras += "pydantic BaseModel used"
        if (loggerHits >= 2) extras += "stdlib logging is the logger"
        if (asyncFiles >= 2) extras += "async/await is used"
        if (extras.isNotEmpty()) {
            sb.appendLine("Conventions: " + extras.joinToString("; ") + ".")
        }

        return sb.toString().trim()
    }

    private fun collectPyFiles(root: VirtualFile, out: MutableList<VirtualFile>, limit: Int) {
        if (out.size >= limit) return
        if (!root.isValid) return
        if (root.isDirectory) {
            val name = root.name
            if (name in SKIP_DIRS) return
            for (child in root.children) {
                collectPyFiles(child, out, limit)
                if (out.size >= limit) return
            }
        } else if (root.extension == "py" && !root.name.startsWith("_test_")) {
            out += root
        }
    }

    companion object {
        private const val MAX_FILES = 60
        private const val MAX_FILE_CHARS = 20_000

        private val SKIP_DIRS = setOf(
            ".venv", "venv", "node_modules", "build", "dist", ".gradle",
            ".idea", "__pycache__", ".pytest_cache", ".mypy_cache", ".ruff_cache",
            ".next", ".vercel", "site-packages",
        )

        private val IMPORT_RX = Regex("""^\s*(?:from\s+([a-zA-Z_][\w.]*)|import\s+([a-zA-Z_][\w.]*))""", RegexOption.MULTILINE).let {
            // merged regex: we only keep the first captured module
            Regex("""^\s*(?:from|import)\s+([a-zA-Z_][\w.]*)""", RegexOption.MULTILINE)
        }
        private val EXCEPTION_DEF_RX = Regex("""class\s+([A-Z][A-Za-z0-9_]*(?:Error|Exception))\b""")
        private val RAISE_RX = Regex("""raise\s+([A-Z][A-Za-z0-9_]*(?:Error|Exception))\b""")
        private val DOCSTRING_RX = Regex(""""{3}([\s\S]{0,800}?)"{3}""")

        fun getInstance(project: Project): CodebaseIndexer = project.service()
    }
}
