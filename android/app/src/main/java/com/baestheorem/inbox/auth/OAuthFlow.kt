package com.baestheorem.inbox.auth

import com.baestheorem.inbox.gmail.Net
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.withContext
import kotlinx.serialization.Serializable
import okhttp3.FormBody
import okhttp3.Request
import java.io.BufferedReader
import java.io.IOException
import java.io.InputStreamReader
import java.net.InetAddress
import java.net.ServerSocket
import java.net.Socket
import java.net.SocketTimeoutException
import java.net.URLDecoder
import java.net.URLEncoder
import java.security.MessageDigest
import java.security.SecureRandom
import java.util.Base64

/**
 * Installed-app OAuth with PKCE against a **Desktop app** client, exactly like
 * the Mac server does: a one-shot loopback listener on 127.0.0.1 catches the
 * redirect from the system browser. Google deprecated loopback for clients
 * registered as Android, and custom URI schemes with them, so the Desktop
 * client type is the flow that still works from a sideloaded app with no
 * Play-services dependency and no hosted redirect page.
 *
 * Consent must happen in a real browser (Custom Tab). Google blocks OAuth
 * inside an embedded WebView with `disallowed_useragent`.
 *
 * Only java.* is used here, on purpose: `OAuthFlowTest` drives the listener on
 * a plain JVM, where android.net.Uri and android.util.Base64 are stubs.
 */
object OAuthFlow {
    const val AUTH_ENDPOINT = "https://accounts.google.com/o/oauth2/v2/auth"
    const val TOKEN_ENDPOINT = "https://oauth2.googleapis.com/token"
    const val REVOKE_ENDPOINT = "https://oauth2.googleapis.com/revoke"

    // Read/label/archive plus send. No delete scope, no settings scope: the
    // Android app never writes Gmail filters, and the display-name lookup
    // degrades to the From header of your own sent mail when sendAs is denied.
    val SCOPES = listOf(
        "https://www.googleapis.com/auth/gmail.modify",
        "https://www.googleapis.com/auth/gmail.send",
    )

    private const val CONSENT_TIMEOUT_MS = 5 * 60 * 1000L
    private const val READ_TIMEOUT_MS = 10_000

    class AuthError(message: String, val code: String = "") : Exception(message)

    @Serializable
    private data class TokenResponse(
        val access_token: String? = null,
        val refresh_token: String? = null,
        val expires_in: Long? = null,
        val error: String? = null,
        val error_description: String? = null,
    )

    data class Tokens(val accessToken: String, val refreshToken: String, val expiresIn: Long)

    /** A live consent attempt: the listener is already bound, the URL is ready. */
    class Session internal constructor(
        val authUrl: String,
        private val server: ServerSocket,
        private val verifier: String,
        internal val state: String,
        private val redirectUri: String,
        private val clientId: String,
        private val clientSecret: String,
    ) {
        internal val port: Int get() = server.localPort

        /** Blocks until the browser hits the loopback listener. */
        suspend fun awaitTokens(): Tokens = withContext(Dispatchers.IO) {
            try {
                val code = awaitCode()
                exchange(code, verifier, redirectUri, clientId, clientSecret)
            } finally {
                close()
            }
        }

        private class Outcome(val code: String?, val error: String?)

        /**
         * Serves the loopback port until a request carrying this session's
         * `state` arrives. Everything else the browser throws at the port is
         * answered and ignored: the speculative preconnects Chrome opens and
         * never writes to, the favicon fetch, and any request with a foreign or
         * missing state (another app on the phone can reach 127.0.0.1 too).
         * A single stray hit must not end the real sign-in.
         */
        internal fun awaitCode(timeoutMs: Long = CONSENT_TIMEOUT_MS): String {
            val deadline = System.currentTimeMillis() + timeoutMs
            while (true) {
                val left = deadline - System.currentTimeMillis()
                if (left <= 0) throw AuthError("Timed out waiting for the Google sign-in to come back.")
                server.soTimeout = left.coerceAtMost(Int.MAX_VALUE.toLong()).toInt()
                val socket: Socket = try {
                    server.accept()
                } catch (e: SocketTimeoutException) {
                    throw AuthError("Timed out waiting for the Google sign-in to come back.")
                } catch (e: IOException) {
                    throw AuthError("The sign-in was cancelled before Google came back.")
                }
                val outcome = socket.use { handle(it) } ?: continue
                if (outcome.code != null) return outcome.code
                throw AuthError(
                    when (outcome.error) {
                        "access_denied" -> "You declined the permissions Inbox needs."
                        null -> "The browser came back without an authorization code."
                        else -> "Google returned: ${outcome.error}"
                    },
                    outcome.error ?: "",
                )
            }
        }

        /** One connection. Null means "not the redirect, keep listening". */
        private fun handle(socket: Socket): Outcome? {
            socket.soTimeout = READ_TIMEOUT_MS
            val requestLine = try {
                BufferedReader(InputStreamReader(socket.getInputStream(), Charsets.ISO_8859_1)).readLine()
            } catch (e: IOException) {
                null
            } ?: return null
            val target = requestLine.split(" ").getOrNull(1) ?: return null
            val path = target.substringBefore('?')
            val query = parseQuery(target.substringAfter('?', ""))
            if (path != "/" && path != "") {
                respond(socket, 404, "Not found", "")
                return null
            }
            val code = query["code"]
            val error = query["error"]
            if (code == null && error == null) {
                respond(socket, 404, "Not found", "")
                return null
            }
            val theirs = query["state"] ?: ""
            if (!MessageDigest.isEqual(theirs.toByteArray(), state.toByteArray())) {
                respond(socket, 400, "Sign-in mismatch", "This response belongs to a different sign-in attempt.")
                return null
            }
            if (code != null) {
                respond(socket, 200, "Signed in", "Inbox is bringing itself back. You can close this tab.")
            } else {
                respond(socket, 200, "Sign-in cancelled", "Nothing was changed. Go back to Inbox and try again.")
            }
            return Outcome(code, error)
        }

        private fun respond(socket: Socket, status: Int, title: String, note: String) {
            val body = """
                <!doctype html><html><head><meta charset="utf-8">
                <meta name="viewport" content="width=device-width,initial-scale=1">
                <title>$title</title></head>
                <body style="margin:0;display:flex;align-items:center;justify-content:center;
                  height:100vh;background:#F2F2F2;color:#212121;
                  font-family:Roboto,-apple-system,Helvetica,Arial,sans-serif">
                <div style="text-align:center;padding:24px">
                  <div style="font-size:22px;margin-bottom:8px">$title</div>
                  <div style="color:#5F6368;font-size:15px">$note</div>
                </div></body></html>
            """.trimIndent()
            val bytes = body.toByteArray(Charsets.UTF_8)
            val reason = when (status) {
                200 -> "OK"
                400 -> "Bad Request"
                else -> "Not Found"
            }
            try {
                socket.getOutputStream().apply {
                    write(
                        ("HTTP/1.1 $status $reason\r\n" +
                            "Content-Type: text/html; charset=utf-8\r\n" +
                            "Content-Length: ${bytes.size}\r\n" +
                            "Connection: close\r\n\r\n").toByteArray(Charsets.UTF_8)
                    )
                    write(bytes)
                    flush()
                }
            } catch (e: IOException) {
                // the browser hung up first; the query string was already read
            }
        }

        fun close() {
            try {
                server.close()
            } catch (e: IOException) {
                // listener already gone; nothing to clean up
            }
        }
    }

    /** Binds the loopback listener and builds the consent URL for it. */
    fun start(clientId: String, clientSecret: String, loginHint: String = ""): Session {
        // Backlog above 1: Chrome opens a spare connection alongside the real
        // one, and a refused real one is a dead-end error page for the user.
        val server = ServerSocket(0, 8, InetAddress.getByName("127.0.0.1"))
        val redirectUri = "http://127.0.0.1:${server.localPort}"
        val verifier = randomToken(64)
        val state = randomToken(32)
        val challenge = s256(verifier)
        val params = mutableListOf(
            "client_id" to clientId,
            "redirect_uri" to redirectUri,
            "response_type" to "code",
            "scope" to SCOPES.joinToString(" "),
            "code_challenge" to challenge,
            "code_challenge_method" to "S256",
            "state" to state,
            "access_type" to "offline",
            // force the refresh token even if this account consented before
            "prompt" to "consent",
        )
        if (loginHint.isNotEmpty()) params += "login_hint" to loginHint
        val url = AUTH_ENDPOINT + "?" + params.joinToString("&") { (k, v) ->
            k + "=" + URLEncoder.encode(v, "UTF-8")
        }
        return Session(url, server, verifier, state, redirectUri, clientId, clientSecret)
    }

    internal fun parseQuery(query: String): Map<String, String> {
        if (query.isEmpty()) return emptyMap()
        val out = HashMap<String, String>()
        for (pair in query.split('&')) {
            if (pair.isEmpty()) continue
            val k = pair.substringBefore('=')
            val v = pair.substringAfter('=', "")
            try {
                out[URLDecoder.decode(k, "UTF-8")] = URLDecoder.decode(v, "UTF-8")
            } catch (e: IllegalArgumentException) {
                // a malformed percent-escape from something that is not Google
            }
        }
        return out
    }

    private fun exchange(
        code: String,
        verifier: String,
        redirectUri: String,
        clientId: String,
        clientSecret: String,
    ): Tokens {
        val form = FormBody.Builder()
            .add("code", code)
            .add("client_id", clientId)
            .add("client_secret", clientSecret)
            .add("redirect_uri", redirectUri)
            .add("grant_type", "authorization_code")
            .add("code_verifier", verifier)
            .build()
        val req = Request.Builder().url(TOKEN_ENDPOINT).post(form).build()
        val body = Net.client.newCall(req).execute().use { resp ->
            resp.body?.string() ?: ""
        }
        val parsed = try {
            Net.json.decodeFromString(TokenResponse.serializer(), body)
        } catch (e: Exception) {
            throw AuthError("Google's token response could not be read.")
        }
        if (parsed.error != null) {
            throw AuthError(explain(parsed.error, parsed.error_description), parsed.error)
        }
        val refresh = parsed.refresh_token
            ?: throw AuthError(
                "Google did not return a refresh token. Remove Inbox from your " +
                    "account's third-party access list and sign in again."
            )
        return Tokens(parsed.access_token ?: "", refresh, parsed.expires_in ?: 3600)
    }

    /** Best effort: a revoked token means the account page stops listing the app. */
    suspend fun revoke(refreshToken: String) = withContext(Dispatchers.IO) {
        if (refreshToken.isEmpty()) return@withContext
        val form = FormBody.Builder().add("token", refreshToken).build()
        try {
            Net.client.newCall(Request.Builder().url(REVOKE_ENDPOINT).post(form).build())
                .execute().close()
        } catch (e: IOException) {
            // offline sign-out is still a sign-out locally
        }
    }

    private fun explain(error: String, description: String?): String = when (error) {
        "redirect_uri_mismatch" ->
            "That OAuth client does not accept a loopback redirect. Create the " +
                "client with application type \"Desktop app\" and try again."
        "invalid_client" ->
            "Google does not recognize that client ID and secret pair. Check both " +
                "values, and that they come from the same project."
        "invalid_grant" ->
            "The sign-in code expired before it was used. Try again."
        "access_denied" ->
            "You declined the permissions Inbox needs."
        else -> description ?: "Google returned: $error"
    }

    private val urlSafe = Base64.getUrlEncoder().withoutPadding()

    private fun randomToken(bytes: Int): String {
        val buf = ByteArray(bytes)
        SecureRandom().nextBytes(buf)
        return urlSafe.encodeToString(buf)
    }

    private fun s256(verifier: String): String {
        val digest = MessageDigest.getInstance("SHA-256").digest(verifier.toByteArray(Charsets.US_ASCII))
        return urlSafe.encodeToString(digest)
    }
}
