package com.redgreen

import com.intellij.openapi.components.Service
import com.intellij.openapi.components.service
import com.intellij.openapi.project.Project
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.SupervisorJob
import java.net.URI
import java.net.http.HttpClient
import java.net.http.HttpRequest
import java.net.http.HttpResponse
import java.time.Duration

/**
 * Project-scoped service that holds the HTTP client + a shared scope for
 * background coroutines. The tool window and actions retrieve this via
 * `project.service<RedGreenService>()`.
 */
@Service(Service.Level.PROJECT)
class RedGreenService(val project: Project) {

    val scope = CoroutineScope(SupervisorJob() + Dispatchers.IO)

    private val http: HttpClient = HttpClient.newBuilder()
        .connectTimeout(Duration.ofSeconds(5))
        .build()

    private val baseUrl: String
        get() = RedGreenSettings.getInstance().backendBaseUrl.trimEnd('/')

    fun postAnalyze(bodyJson: String): String {
        val req = HttpRequest.newBuilder()
            .uri(URI.create("$baseUrl/analyze"))
            .timeout(Duration.ofSeconds(15))
            .header("content-type", "application/json")
            .POST(HttpRequest.BodyPublishers.ofString(bodyJson))
            .build()
        val resp = http.send(req, HttpResponse.BodyHandlers.ofString())
        if (resp.statusCode() !in 200..299) {
            error("POST /analyze -> ${resp.statusCode()}: ${resp.body().take(200)}")
        }
        return resp.body()
    }

    fun getStatus(episodeId: String): String {
        val req = HttpRequest.newBuilder()
            .uri(URI.create("$baseUrl/status/$episodeId"))
            .timeout(Duration.ofSeconds(15))
            .GET()
            .build()
        val resp = http.send(req, HttpResponse.BodyHandlers.ofString())
        if (resp.statusCode() !in 200..299) {
            error("GET /status/$episodeId -> ${resp.statusCode()}: ${resp.body().take(200)}")
        }
        return resp.body()
    }

    companion object {
        @JvmStatic
        fun getInstance(project: Project): RedGreenService = project.service()
    }
}
