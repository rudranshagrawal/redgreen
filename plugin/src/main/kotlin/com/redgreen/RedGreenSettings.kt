package com.redgreen

import com.intellij.openapi.components.Service
import com.intellij.openapi.components.service

/**
 * Trivial application-level settings holder. For the hackathon we don't ship a
 * settings UI — the backend URL lives here so it's easy to tweak in tests.
 */
@Service(Service.Level.APP)
class RedGreenSettings {
    val backendBaseUrl: String
        get() = System.getProperty("redgreen.backend.url")
            ?: System.getenv("REDGREEN_BACKEND_URL")
            ?: "http://127.0.0.1:8787"

    companion object {
        @JvmStatic
        fun getInstance(): RedGreenSettings = service()
    }
}
