package com.baestheorem.inbox.gmail

import org.junit.Assert.assertEquals
import org.junit.Assert.assertNull
import org.junit.Test

// Same cases as tests/test_mailto.py for the Mac parser
class MailtoTest {
    @Test fun plainAddress() = assertEquals("a@example.com", parseMailto("mailto:a@example.com")!!.to)

    @Test fun headersCaseAndEncoding() {
        val f = parseMailto("mailto:a@example.com?Subject=Hi%20there&body=line1%0D%0Aa+b&CC=c@example.com")!!
        assertEquals("Hi there", f.subject)
        assertEquals("line1\r\na+b", f.body)
        assertEquals("c@example.com", f.cc)
    }

    @Test fun toHeaderJoinsPathAndDedupes() = assertEquals(
        "a@example.com, b@example.com, d@example.com",
        parseMailto("mailto:a@example.com,b@example.com?to=b@example.com&to=d@example.com")!!.to,
    )

    @Test fun encodedAndEmptyPath() {
        assertEquals("a@example.com", parseMailto("mailto:a%40example.com")!!.to)
        assertEquals("", parseMailto("mailto:?subject=x")!!.to)
    }

    @Test fun notMailto() = assertNull(parseMailto("https://example.com"))

    @Test fun extrasFillGaps() {
        val f = parseMailto("mailto:")!!.withExtras(arrayOf("a@example.com"), null, null, "S", "B")
        assertEquals("a@example.com", f.to)
        assertEquals("S", f.subject)
        assertEquals("B", f.body)
    }
}
