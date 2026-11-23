package com.baestheorem.inbox.gmail

import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test

class ReplyRecipientsTest {
    private val me = "me@gmail.com"
    private fun e(l: List<Recipient>) = l.map { it.email }

    @Test fun replyGoesToSenderAndAllAddsTheOthersAsCc() {
        val r = computeReplyPresets("Ann <ann@x.com>", "", "Me <me@gmail.com>, bob@x.com", "Cy <cy@x.com>", me)
        assertEquals(listOf("ann@x.com"), e(r.reply.to))
        assertEquals(listOf("ann@x.com"), e(r.all.to))
        assertEquals(listOf("bob@x.com", "cy@x.com"), e(r.all.cc))
        assertTrue(r.allDiffers)
    }

    @Test fun replyToHeaderWinsOverFrom() {
        val r = computeReplyPresets("List <list@x.com>", "ann@x.com", me, "", me)
        assertEquals(listOf("ann@x.com"), e(r.reply.to))
        assertFalse(r.allDiffers)
    }

    @Test fun myOwnMessageGoesToThePeopleIWroteTo() {
        val r = computeReplyPresets("Me <ME@gmail.com>", "", "ann@x.com, bob@x.com", "cy@x.com", me)
        assertEquals(listOf("ann@x.com", "bob@x.com"), e(r.reply.to))
        assertEquals(listOf("cy@x.com"), e(r.all.cc))
    }

    @Test fun nameWithACommaIsOneAddress() {
        val r = computeReplyPresets("ann@x.com", "", "\"Doe, Bob\" <bob@x.com>, me@gmail.com", "", me)
        assertEquals(listOf("bob@x.com"), e(r.all.cc))
        assertEquals("Doe, Bob", r.all.cc[0].name)
    }

    @Test fun dottedAndTaggedFormsOfMyGmailAddressAreMe() {
        val r = computeReplyPresets("ann@x.com", "", "m.e+shop@gmail.com, bob@x.com, Ann <ANN@x.com>", "ME@googlemail.com", me)
        assertEquals(listOf("bob@x.com"), e(r.all.cc))
    }

    @Test fun noHeadersGivesEmptyPresets() {
        val r = computeReplyPresets("", "", "", "", me)
        assertTrue(r.reply.isEmpty && r.all.isEmpty)
    }

    @Test fun anAddressLivesInOneField() {
        val r = ReplyRecipients(to = listOf(Recipient("a", "a@x.com"))).add(Recipient("a", "A@x.com"), RcptField.CC)
        assertTrue(r.to.isEmpty())
        assertEquals(listOf("A@x.com"), e(r.cc))
    }

    @Test fun emailCheck() {
        assertTrue(isEmailAddress("a@b.co"))
        assertFalse(isEmailAddress("notanaddress"))
        assertFalse(isEmailAddress("a b@c.d"))
    }
}
