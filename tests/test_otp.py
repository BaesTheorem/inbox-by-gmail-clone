"""Rules for emailed one-time codes. Fixtures are synthetic, shaped like the
mail that calibrated them (login codes, meeting invites, receipts, FAQs)."""

import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import otp


def code(subject, body, sender="Example <no-reply@mail.example.com>"):
    r = otp.extract_code(subject, body, sender)
    return r and r["code"]


class Positives(unittest.TestCase):
    def test_subject_carries_the_code(self):
        self.assertEqual(code("482913 is your Example verification code", "Use it within 10 minutes."), "482913")
        self.assertEqual(code("Login code: 6502", "== Here's your code == 6502 =="), "6502")
        self.assertEqual(code("VD7LE is your Example verification code", "Use the code below. VD7LE"), "VD7LE")

    def test_body_after_phrase(self):
        self.assertEqual(code("Verify your email address", "Your verification code is 910185 This is automatic."), "910185")
        self.assertEqual(code("Sudo email verification code", "Here is your sudo authentication code: 12006051 Valid 15 minutes."), "12006051")
        self.assertEqual(code("Welcome", "Login request received, verification code: 219699 (valid for 10 minutes)"), "219699")
        self.assertEqual(code("Your one-time passcode", "Here's your one-time passcode. Use this passcode to verify. 892589 It expires soon."), "892589")

    def test_body_code_before_phrase(self):
        self.assertEqual(code("New verification code", "Use the following code: 424440 This code will expire today."), "424440")
        self.assertEqual(code("Account data access attempt", "If this was you, your verification code is: 958795 If not, deny."), "958795")

    def test_alphanumeric_and_grouped(self):
        self.assertEqual(code("Activate your online account", "Your verification code Enter the code below to verify. T0L0F4KT This is"), "T0L0F4KT")
        self.assertEqual(code("Your code", "Your verification code is 123 456. Thanks."), "123456")
        self.assertEqual(code("ZZVUU is your Example verification code", "Use the code below. ZZVUU"), "ZZVUU")

    def test_html_body(self):
        html = "<html><style>.x{}</style><body><p>Hi,</p><p>Your verification code is:</p><span style='font-family:Courier'>775301</span></body></html>"
        self.assertEqual(code("Passwordless login!", html), "775301")


class Negatives(unittest.TestCase):
    def test_meeting_invites(self):
        teams = "Join the meeting now Meeting ID: 238 322 506 101 761 Passcode: XC774in2 Need help?"
        self.assertIsNone(code("Teleconference, 24 September", teams))
        self.assertIsNone(code("Weekly sync", teams))
        zoom = ("Join Zoom Meeting Meeting ID: 977 5193 7256 Passcode: 58Fg83!g9Y Join by Telephone "
                "+1 301 715 8592 US Meeting ID: 977 5193 7256 Passcode: 8611849640 SIP: 97751937256@zoomcrc.com")
        self.assertIsNone(code("Weekly Zoom Connect", zoom))

    def test_mentions_without_a_code(self):
        self.assertIsNone(code("We're processing your payment",
                               "Payment of $30.24 from Bank Account *3968. Can I receive my verification code without my phone? No."))
        self.assertIsNone(code("Sign-in attempt from new device",
                               "If you're unable to receive the 6-digit login code, contact Support."))
        self.assertIsNone(code("New in MyChart", "MyChart will never ask for a password or verification code by email."))

    def test_receipts_and_promos(self):
        self.assertIsNone(code("Thank You For Your Purchase",
                               "Reference Code: TNDR.3c3ce490 Confirmation Code: RT.1d6bc3a2 Expiration Date: 9/20/2026"))
        self.assertIsNone(code("You've got 36 unread messages", "TechLetter is a partner, so the code is ours. Code: PARTNER50 ti.to/x"))
        self.assertIsNone(code("Re: Direct order quote",
                               "Type in your credit card number, expiration date and security code. Order number: TR9014-45175."))

    def test_link_only_verification(self):
        self.assertIsNone(code("Verify your candidate account",
                               "Click this link to confirm your email address https://x.example/activate/abc The link expires after 24 hours."))

    def test_card_last_four_is_not_the_code(self):
        self.assertEqual(code("Card activation code for digital wallet",
                              "Your activation code is 214813 for adding your card 8078 to your wallet."), "214813")


class Labels(unittest.TestCase):
    def test_service_name(self):
        self.assertEqual(otp.service_name("MyDisney via ESPN <no-reply@mydisney.espn.com>"), "MyDisney")
        self.assertEqual(otp.service_name("No Reply <no-reply@alerts.cdco.io>"), "Cdco")
        self.assertEqual(otp.service_name("\"Jagex 'no-reply at contact.jagex.com'\" <x@contact.jagex.com>"), "Jagex")
        self.assertEqual(otp.service_name("notify@dayforce.com"), "Dayforce")

    def test_registrable_domain(self):
        self.assertEqual(otp.registrable_domain("no-reply@mydisney.espn.com"), "espn.com")
        self.assertEqual(otp.registrable_domain("a@b.co.uk"), "b.co.uk")
        self.assertEqual(otp.registrable_domain("Example <x@example.com>"), "example.com")


if __name__ == "__main__":
    unittest.main()
