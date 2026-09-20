package com.baestheorem.inbox.auth

import android.net.Uri
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
import java.security.MessageDigest
import java.security.SecureRandom
import android.util.Base64

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
        private val redirectUri: String,
        private val clientId: String,
        private val clientSecret: String,
    ) {
        /** Blocks until the browser hits the loopback listener. */
        suspend fun awaitTokens(): Tokens = withContext(Dispatchers.IO) {
            try {
                val code = awaitCode()
                exchange(code, verifier, redirectUri, clientId, clientSecret)
            } finally {
                close()
            }
        }

        private fun awaitCode(): String {
            server.soTimeout = 5 * 60 * 1000
            val socket: Socket = try {
                server.accept()
            } catch (e: IOException) {
                throw AuthError("Timed out waiting for the Google sign-in to come back.")
            }
            socket.use { s ->
                val reader = BufferedReader(InputStreamReader(s.getInputStream()))
                val requestLine = reader.readLine() ?: ""
                val path = requestLine.split(" ").getOrNull(1) ?: ""
                val uri = Uri.parse("http://127.0.0.1$path")
                val code = uri.getQueryParameter("code")
                val error = uri.getQueryParameter("error")
                respond(s, code != null)
                if (code != null) return code
                throw AuthError(
                    when (error) {
                        "access_denied" -> "You declined the permissions Inbox needs."
                        null -> "The browser came back without an authorization code."
                        else -> "Google returned: $error"
                    },
                    error ?: "",
                )
            }
        }

        private fun respond(socket: Socket, ok: Boolean) {
            val title = if (ok) "Signed in" else "Sign-in cancelled"
            val note = if (ok) "You can close this tab and go back to Inbox."
            else "Nothing was changed. Go back to Inbox and try again."
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
            socket.getOutputStream().apply {
                write(
                    ("HTTP/1.1 200 OK\r\n" +
                        "Content-Type: text/html; charset=utf-8\r\n" +
                        "Content-Length: ${bytes.size}\r\n" +
                        "Connection: close\r\n\r\n").toByteArray(Charsets.UTF_8)
                )
                write(bytes)
                flush()
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
        val server = ServerSocket(0, 1, InetAddress.getByName("127.0.0.1"))
        val redirectUri = "http://127.0.0.1:${server.localPort}"
        val verifier = randomVerifier()
        val challenge = s256(verifier)
        val url = Uri.parse(AUTH_ENDPOINT).buildUpon()
            .appendQueryParameter("client_id", clientId)
            .appendQueryParameter("redirect_uri", redirectUri)
            .appendQueryParameter("response_type", "code")
            .appendQueryParameter("scope", SCOPES.joinToString(" "))
            .appendQueryParameter("code_challenge", challenge)
            .appendQueryParameter("code_challenge_method", "S256")
            .appendQueryParameter("access_type", "offline")
            // force the refresh token even if this account consented before
            .appendQueryParameter("prompt", "consent")
            .apply { if (loginHint.isNotEmpty()) appendQueryParameter("login_hint", loginHint) }
            .build()
            .toString()
        return Session(url, server, verifier, redirectUri, clientId, clientSecret)
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

    private fun randomVerifier(): String {
        val bytes = ByteArray(64)
        SecureRandom().nextBytes(bytes)
        return Base64.encodeToString(bytes, Base64.URL_SAFE or Base64.NO_WRAP or Base64.NO_PADDING)
    }

    private fun s256(verifier: String): String {
        val digest = MessageDigest.getInstance("SHA-256").digest(verifier.toByteArray(Charsets.US_ASCII))
        return Base64.encodeToString(digest, Base64.URL_SAFE or Base64.NO_WRAP or Base64.NO_PADDING)
    }
}
