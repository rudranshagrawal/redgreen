package com.redgreen

import java.io.File


/**
 * Context-based unified-diff applier — a Kotlin twin of runner/run_test.py's.
 *
 * Intentionally IGNORES line numbers in `@@` headers because model output
 * rarely gets them right. We treat the hunk body as search-and-replace: the
 * ` ` + `-` lines must appear verbatim in the target file, and get replaced
 * with ` ` + `+` lines.
 */
object UnifiedDiffApplier {

    /**
     * Apply [diffText] anchored at [repoRoot]. Returns the list of absolute
     * paths that were modified.
     */
    fun apply(repoRoot: String, diffText: String): List<String> {
        val hunksByFile = parse(diffText)
        val modified = mutableListOf<String>()
        for ((relPath, hunks) in hunksByFile) {
            val target = resolveTarget(File(repoRoot), relPath)
                ?: throw IllegalStateException("patch targets missing file: $relPath")
            applyHunksToFile(target, hunks)
            modified += target.absolutePath
        }
        return modified
    }

    /**
     * Try, in order:
     *   1. repoRoot + relPath as-is.
     *   2. Progressively strip leading directories off relPath.
     *      E.g. "seeds/null_guard/name.py" → try "null_guard/name.py", then
     *      "name.py".
     *   3. Find any file under repoRoot with the same basename. If exactly
     *      one match, use it. Multiple matches → ambiguous, give up.
     * Models routinely write longer-than-expected prefixes, especially for
     * syntax-error fast-path. This makes the applier forgive that.
     */
    private fun resolveTarget(repoRoot: File, relPath: String): File? {
        val direct = File(repoRoot, relPath)
        if (direct.exists()) return direct

        val segs = relPath.split("/")
        for (start in 1 until segs.size) {
            val candidate = File(repoRoot, segs.drop(start).joinToString("/"))
            if (candidate.exists()) return candidate
        }

        val basename = segs.last()
        val matches = repoRoot.walkTopDown()
            .filter { it.isFile && it.name == basename }
            .filter {
                val p = it.absolutePath
                !p.contains("/.gradle/") && !p.contains("/.venv/") &&
                    !p.contains("/node_modules/") && !p.contains("/build/") &&
                    !p.contains("/.next/")
            }
            .take(2)
            .toList()
        return matches.singleOrNull()
    }

    private data class Hunk(val lines: List<String>)

    private fun parse(diffText: String): Map<String, List<Hunk>> {
        val result = linkedMapOf<String, MutableList<Hunk>>()
        var currentFile: String? = null
        var active: MutableList<String>? = null
        for (raw in diffText.lineSequence()) {
            when {
                raw.startsWith("+++ ") -> {
                    var tgt = raw.substring(4).trim()
                    if (tgt.startsWith("b/")) tgt = tgt.substring(2)
                    if (tgt == "/dev/null") currentFile = null else currentFile = tgt
                }
                raw.startsWith("--- ") -> {
                    active = null
                }
                raw.startsWith("@@") -> {
                    if (currentFile != null) {
                        val list = mutableListOf<String>()
                        result.getOrPut(currentFile!!) { mutableListOf() }.add(Hunk(list))
                        active = list
                    }
                }
                else -> {
                    if (active != null && raw.isNotEmpty() && raw[0] in charArrayOf('+', '-', ' ')) {
                        active!!.add(raw)
                    }
                }
            }
        }
        return result
    }

    private fun applyHunksToFile(file: File, hunks: List<Hunk>) {
        val originalText = file.readText()
        val trailingNl = originalText.endsWith("\n")
        val lines = originalText.split("\n").toMutableList()
        if (trailingNl && lines.last() == "") lines.removeAt(lines.size - 1)

        for (hunk in hunks) {
            val before = hunk.lines.filter { it.startsWith(" ") || it.startsWith("-") }.map { it.substring(1) }
            val after = hunk.lines.filter { it.startsWith(" ") || it.startsWith("+") }.map { it.substring(1) }
            if (before.isEmpty()) {
                throw IllegalStateException("pure-insertion hunk without context — cannot anchor")
            }

            var idx = findSubsequence(lines, before)
            if (idx < 0) {
                idx = findSubsequence(lines.map { it.trimEnd() }, before.map { it.trimEnd() })
            }
            if (idx < 0) {
                throw IllegalStateException("hunk does not match any location; first before-line: ${before.first().take(80)}")
            }
            repeat(before.size) { lines.removeAt(idx) }
            after.forEachIndexed { i, l -> lines.add(idx + i, l) }
        }

        val rebuilt = lines.joinToString("\n") + if (trailingNl) "\n" else ""
        file.writeText(rebuilt)
    }

    private fun findSubsequence(haystack: List<String>, needle: List<String>): Int {
        if (needle.isEmpty() || needle.size > haystack.size) return -1
        for (i in 0..(haystack.size - needle.size)) {
            var match = true
            for (j in needle.indices) {
                if (haystack[i + j] != needle[j]) { match = false; break }
            }
            if (match) return i
        }
        return -1
    }
}
