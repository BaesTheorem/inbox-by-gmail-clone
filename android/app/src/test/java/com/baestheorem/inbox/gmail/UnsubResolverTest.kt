package com.baestheorem.inbox.gmail

import okhttp3.HttpUrl.Companion.toHttpUrl
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test

// Fixtures modeled on what real ESPs actually serve. The same set runs against
// app.py's resolver and the iOS port; all three must agree, so keep them in step.
class UnsubResolverTest {

    private val base = "https://sender.example.com/p".toHttpUrl()

    /** "method action k=v&k=v", or "nil" when no form should be submitted. */
    private fun payload(html: String, email: String?): String {
        val form = UnsubResolver.pickUnsubForm(
            UnsubResolver.scrapeForms(html), UnsubResolver.visibleText(html)
        ) ?: return "nil"
        val p = UnsubResolver.formPayload(form, email)
        return "${form.method} ${form.action} " + p.joinToString("&") { "${it.first}=${it.second}" }
    }

    private val mailchimp = """
        <html><body><h1>Unsubscribe</h1>
        <p>We're sorry to see you go. Confirm below.</p>
        <form action="/mc-done" method="post">
          <input type="hidden" name="u" value="abc123">
          <input type="hidden" name="id" value="7f">
          <input type="hidden" name="e" value="tok">
          <input type="submit" name="action" value="Unsubscribe">
        </form></body></html>
    """.trimIndent()

    @Test fun keepsHiddenTokensAndPressesConfirm() {
        assertEquals(
            "post /mc-done u=abc123&id=7f&e=tok&action=Unsubscribe",
            payload(mailchimp, "alex@example.com"),
        )
    }

    /** "We're sorry to see you go" sits above the confirm button, so it is not success. */
    @Test fun confirmPageIsNotSuccess() {
        assertFalse(UnsubResolver.saysDone(mailchimp))
    }

    @Test fun readsMetaRefresh() {
        val html = """<html><head><meta http-equiv="refresh" content="0;url=/chain3">""" +
            """</head><body>Redirecting...</body></html>"""
        assertEquals(
            "https://sender.example.com/chain3",
            UnsubResolver.findClientRedirect(html, base).toString(),
        )
    }

    @Test fun picksTheConfirmLinkNotTheEscapeHatch() {
        val html = """
            <html><body><p>Do you really want to opt out?</p>
            <a href="/keep">No, keep me subscribed</a>
            <a href="/chain-done">Yes, unsubscribe me</a></body></html>
        """.trimIndent()
        assertEquals(
            "https://sender.example.com/chain-done",
            UnsubResolver.findConfirmLink(html, base).toString(),
        )
    }

    @Test fun fillsTheAddressBoxAndAnswersTheReasonDropdown() {
        val html = """
            <html><body><h1>Manage your email preferences</h1>
            <form action="/em-done" method="post">
              <label>Email address</label><input type="email" name="email_address" value="">
              <select name="reason"><option value="">Pick a reason</option>
              <option value="too_many">Too many emails</option></select>
              <textarea name="comments"></textarea>
              <button type="submit">Confirm unsubscribe</button>
            </form></body></html>
        """.trimIndent()
        assertEquals(
            "post /em-done email_address=alex@example.com&reason=too_many&comments=",
            payload(html, "alex@example.com"),
        )
    }

    @Test fun refusesLoginForms() {
        val html = """
            <html><body><h1>Sign in to manage preferences</h1>
            <form action="/auth" method="post" id="login-form">
              <input type="text" name="user"><input type="password" name="pw">
              <input type="submit" value="Sign in">
            </form></body></html>
        """.trimIndent()
        assertEquals("nil", payload(html, "a@b.c"))
    }

    @Test fun readsTheDonePage() {
        assertTrue(
            UnsubResolver.saysDone(
                "<html><body>You have been successfully removed from our mailing list.</body></html>"
            )
        )
    }

    @Test fun ticksTheOptOutRadioNotTheFirstOne() {
        val html = """
            <html><body><h1>Email preferences</h1>
            <form action="/radio-done" method="post">
              <input type="radio" name="pref" value="weekly" id="weekly">Weekly digest
              <input type="radio" name="pref" value="unsubscribe_all" id="unsub-all">Unsubscribe from all
              <input type="checkbox" name="confirm" value="1" id="confirm-optout">I confirm
              <button type="submit" name="go" value="Save preferences">Save preferences</button>
            </form></body></html>
        """.trimIndent()
        assertEquals(
            "post /radio-done confirm=1&pref=unsubscribe_all&go=Save preferences",
            payload(html, null),
        )
    }

    /** A JS-only page has nothing to submit; give up rather than guess. */
    @Test fun givesUpOnAScriptOnlyPage() {
        val html = """<html><body><div id="root"></div><script>fetch('/api/unsub')</script></body></html>"""
        assertEquals("nil", payload(html, null))
        assertNull(UnsubResolver.findConfirmLink(html, base))
        assertNull(UnsubResolver.findClientRedirect(html, base))
    }

    @Test fun handlesGetForms() {
        val html = """
            <html><body><form action="/get-done" method="get">
              <input type="hidden" name="tok" value="zz"><input type="submit" value="Opt out">
            </form></body></html>
        """.trimIndent()
        assertEquals("get /get-done tok=zz", payload(html, null))
    }
}
