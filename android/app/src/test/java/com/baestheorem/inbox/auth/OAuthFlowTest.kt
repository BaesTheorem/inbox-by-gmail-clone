package com.baestheorem.inbox.auth

import org.junit.Assert.assertEquals
import org.junit.Assert.assertTrue
import org.junit.Assert.fail
import org.junit.Test
import java.io.BufferedReader
import java.io.InputStreamReader
import java.net.Socket
import java.net.URLDecoder
import java.util.concurrent.Callable
import java.util.concurrent.Executors
import java.util.concurrent.TimeUnit

/**
 * Drives the loopback listener the way a real browser (and a nosy neighbour
 * app) would, on a plain JVM. The listener has to survive everything that is
 * not the redirect, and only ever hand back the code that carries its state.
 */
class OAuthFlowTest {
    private val pool = Executors.newSingleThreadExecutor()

    private fun request(port: Int, target: String): Pair<Int, String> =
        Socket("127.0.0.1", port).use { s ->
            s.getOutputStream().write("GET $target HTTP/1.1\r\nHost: 127.0.0.1:$port\r\n\r\n".toByteArray())
            s.getOutputStream().flush()
            val r = BufferedReader(InputStreamReader(s.getInputStream()))
            val statusLine = r.readLine() ?: ""
            val status = statusLine.split(" ").getOrNull(1)?.toIntOrNull() ?: -1
            val body = r.readText()
            status to body
        }

    @Test
    fun strayConnectionsDoNotEndTheSignIn() {
        val s = OAuthFlow.start("id.apps.googleusercontent.com", "secret")
        try {
            val code = pool.submit(Callable { s.awaitCode(15_000) })

            // Chrome's speculative preconnect: opened, never written to.
            Socket("127.0.0.1", s.port).close()
            // The favicon fetch.
            assertEquals(404, request(s.port, "/favicon.ico").first)
            // Another app on the phone guessing at the port.
            val (mismatch, body) = request(s.port, "/?state=nope&code=evil")
            assertEquals(400, mismatch)
            assertTrue(body.contains("different sign-in"))
            // A hit with no OAuth parameters at all.
            assertEquals(404, request(s.port, "/").first)

            // Then the real redirect.
            val (ok, okBody) = request(s.port, "/?state=${s.state}&code=abc%2F123&scope=x")
            assertEquals(200, ok)
            assertTrue(okBody.contains("Signed in"))
            assertEquals("abc/123", code.get(5, TimeUnit.SECONDS))
        } finally {
            s.close()
        }
    }

    @Test
    fun declinedConsentIsReportedWithItsCode() {
        val s = OAuthFlow.start("id.apps.googleusercontent.com", "secret")
        try {
            val result = pool.submit(Callable {
                try {
                    s.awaitCode(15_000)
                    "no error"
                } catch (e: OAuthFlow.AuthError) {
                    e.code
                }
            })
            assertEquals(200, request(s.port, "/?state=${s.state}&error=access_denied").first)
            assertEquals("access_denied", result.get(5, TimeUnit.SECONDS))
        } finally {
            s.close()
        }
    }

    @Test
    fun closingTheListenerUnblocksTheWaiter() {
        val s = OAuthFlow.start("id.apps.googleusercontent.com", "secret")
        val result = pool.submit(Callable {
            try {
                s.awaitCode(15_000)
                fail("returned a code from a closed listener")
                ""
            } catch (e: OAuthFlow.AuthError) {
                e.message ?: ""
            }
        })
        Thread.sleep(100)
        s.close()
        assertTrue(result.get(5, TimeUnit.SECONDS).contains("cancelled"))
    }

    @Test
    fun consentUrlCarriesPkceStateAndTheLoopbackPort() {
        val s = OAuthFlow.start("id.apps.googleusercontent.com", "secret", "me@example.com")
        try {
            assertTrue(s.authUrl.startsWith(OAuthFlow.AUTH_ENDPOINT + "?"))
            val q = OAuthFlow.parseQuery(s.authUrl.substringAfter('?'))
            assertEquals("http://127.0.0.1:${s.port}", q["redirect_uri"])
            assertEquals("S256", q["code_challenge_method"])
            assertEquals(s.state, q["state"])
            assertEquals("consent", q["prompt"])
            assertEquals("offline", q["access_type"])
            assertEquals("me@example.com", q["login_hint"])
            assertEquals(OAuthFlow.SCOPES.joinToString(" "), q["scope"])
            assertTrue((q["code_challenge"] ?: "").length >= 43)
            // Every second start gets its own state.
            val t = OAuthFlow.start("id.apps.googleusercontent.com", "secret")
            assertTrue(t.state != s.state)
            t.close()
        } finally {
            s.close()
        }
    }

    @Test
    fun queryParsingDecodesAndTolerates() {
        val q = OAuthFlow.parseQuery("a=1&b=x%20y&c&d=%ZZ&=e")
        assertEquals("1", q["a"])
        assertEquals("x y", q["b"])
        assertEquals("", q["c"])
        assertEquals(null, q["d"])
        assertEquals(URLDecoder.decode("e", "UTF-8"), q[""])
    }
}
