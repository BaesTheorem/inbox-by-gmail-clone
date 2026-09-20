package com.baestheorem.inbox.auth

import kotlinx.serialization.json.Json
import kotlinx.serialization.json.JsonObject
import kotlinx.serialization.json.jsonObject
import kotlinx.serialization.json.jsonPrimitive

/**
 * Accepts whatever the user pastes into the setup wizard: the whole
 * client_secret.json Google Cloud downloads (installed/web shaped), a trimmed
 * JSON object, or a bare client id and secret on two lines.
 */
object ClientCredentials {
    private val lenient = Json { isLenient = true; ignoreUnknownKeys = true }

    data class Parsed(val clientId: String, val clientSecret: String)

    class ParseError(message: String) : Exception(message)

    fun parse(input: String): Parsed {
        val text = input.trim()
        if (text.isEmpty()) throw ParseError("Paste the client ID and secret first.")
        val fromJson = if (text.startsWith("{")) tryJson(text) else null
        val parsed = fromJson ?: tryLoose(text)
        validate(parsed)
        return parsed
    }

    private fun tryJson(text: String): Parsed? {
        val root: JsonObject = try {
            lenient.parseToJsonElement(text).jsonObject
        } catch (e: Exception) {
            throw ParseError("That does not look like valid JSON. Paste the file exactly as downloaded.")
        }
        val node = (root["installed"] ?: root["web"])?.jsonObject ?: root
        val id = node["client_id"]?.jsonPrimitive?.contentOrNullSafe()
        val secret = node["client_secret"]?.jsonPrimitive?.contentOrNullSafe()
        if (id.isNullOrEmpty() || secret.isNullOrEmpty()) {
            throw ParseError("That JSON has no client_id / client_secret pair in it.")
        }
        if (root["web"] != null && root["installed"] == null) {
            throw ParseError(
                "That is a \"Web application\" client. Inbox needs one created as " +
                    "\"Desktop app\"."
            )
        }
        return Parsed(id, secret)
    }

    private fun tryLoose(text: String): Parsed {
        val tokens = text.split(Regex("""[\s,;]+""")).map { it.trim() }.filter { it.isNotEmpty() }
        val id = tokens.firstOrNull { it.endsWith(".apps.googleusercontent.com") }
        val secret = tokens.firstOrNull { it != id && it.length >= 10 && !it.contains("@") }
        if (id == null || secret == null) {
            throw ParseError(
                "Could not find a client ID and secret in that. Paste the whole " +
                    "downloaded JSON, or the ID on one line and the secret on the next."
            )
        }
        return Parsed(id, secret)
    }

    private fun validate(p: Parsed) {
        if (!p.clientId.endsWith(".apps.googleusercontent.com")) {
            throw ParseError("A Google client ID ends in .apps.googleusercontent.com.")
        }
        if (p.clientSecret.length < 8) {
            throw ParseError("That client secret looks too short to be real.")
        }
    }

    private fun kotlinx.serialization.json.JsonPrimitive.contentOrNullSafe(): String? =
        try { content } catch (e: Exception) { null }
}
