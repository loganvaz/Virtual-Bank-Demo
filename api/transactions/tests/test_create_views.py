from decimal import Decimal

from django.urls import reverse

from notifications.models import Notification
from transactions.models import Transaction

from .helpers import TransactionAPITestCase, card_expiry, make_account, make_card


class DepositViewTests(TransactionAPITestCase):
    """Tests for POST deposits/create/: ownership checks, balance update, notifications."""

    url = reverse("api:deposit_creation")

    def test_unauthenticated_is_rejected(self):
        self.client.force_authenticate(None)
        r = self.client.post(self.url, {"account_number": self.alice_acct.number, "amount": "10"})
        self.assertIn(r.status_code, (401, 403))

    def test_happy_path_credits_balance_and_notifies(self):
        r = self.client.post(self.url, {"account_number": self.alice_acct.number, "amount": "25.50"})
        self.assertEqual(r.status_code, 201, r.data)
        self.refresh(self.alice_acct)
        self.assertEqual(self.alice_acct.balance, Decimal("125.50"))
        txn = Transaction.objects.get()
        self.assertEqual((txn.transaction_type, txn.payer, txn.payee, txn.rate), ("DEPOSIT", self.alice_acct, self.alice_acct, 1))
        self.assertEqual(Notification.objects.filter(user=self.alice).count(), 1)

    def test_unknown_account_404(self):
        r = self.client.post(self.url, {"account_number": "9999999999", "amount": "1"})
        self.assertEqual(r.status_code, 404)

    def test_other_users_account_403_and_balance_unchanged(self):
        r = self.client.post(self.url, {"account_number": self.bob_acct.number, "amount": "1"})
        self.assertEqual(r.status_code, 403)
        self.refresh(self.bob_acct)
        self.assertEqual(self.bob_acct.balance, Decimal("50.00"))

    def test_negative_amount_400(self):
        r = self.client.post(self.url, {"account_number": self.alice_acct.number, "amount": "-10"})
        self.assertEqual(r.status_code, 400)
        self.assertEqual(Transaction.objects.count(), 0)


class TransferViewTests(TransactionAPITestCase):
    """Tests for POST transfers/create/: every rejection branch plus same/cross-currency success."""

    url = reverse("api:transfer_creation")

    def post(self, payer, payee, amount):
        return self.client.post(self.url, {"payer_account_number": payer, "payee_account_number": payee, "amount": amount})

    def test_same_account_rejected(self):
        r = self.post(self.alice_acct.number, self.alice_acct.number, "1")
        self.assertEqual(r.status_code, 403)
        self.assertEqual(Notification.objects.filter(user=self.alice).count(), 1)

    def test_payer_not_found_404(self):
        self.assertEqual(self.post("9999999999", self.bob_acct.number, "1").status_code, 404)

    def test_payer_not_owned_403_and_victim_gets_security_notification(self):
        r = self.post(self.bob_acct.number, self.alice_acct.number, "1")
        self.assertEqual(r.status_code, 403)
        note = Notification.objects.get(user=self.bob)
        self.assertEqual(note.notification_type, "SECURITY_NOTIFICATION")
        self.refresh(self.bob_acct)
        self.assertEqual(self.bob_acct.balance, Decimal("50.00"))

    def test_payee_not_found_404(self):
        self.assertEqual(self.post(self.alice_acct.number, "9999999999", "1").status_code, 404)

    def test_insufficient_funds_403_balances_unchanged(self):
        r = self.post(self.alice_acct.number, self.bob_acct.number, "100.01")
        self.assertEqual(r.status_code, 403)
        self.refresh(self.alice_acct, self.bob_acct)
        self.assertEqual((self.alice_acct.balance, self.bob_acct.balance), (Decimal("100.00"), Decimal("50.00")))
        self.assertEqual(Transaction.objects.count(), 0)

    def test_exact_balance_transfer_succeeds(self):
        r = self.post(self.alice_acct.number, self.bob_acct.number, "100")
        self.assertEqual(r.status_code, 201, r.data)
        self.refresh(self.alice_acct, self.bob_acct)
        self.assertEqual((self.alice_acct.balance, self.bob_acct.balance), (Decimal("0.00"), Decimal("150.00")))
        self.assertEqual(Notification.objects.filter(user=self.alice).count(), 1)
        self.assertEqual(Notification.objects.filter(user=self.bob).count(), 1)

    def test_cross_currency_transfer_converts_amount(self):
        eur = make_account(self.bob, balance="0", currency="EUR")
        r = self.post(self.alice_acct.number, eur.number, "100")
        self.assertEqual(r.status_code, 201, r.data)
        self.refresh(eur)
        self.assertEqual(eur.balance, Decimal("85.00"))
        txn = Transaction.objects.get()
        self.assertEqual((txn.currency_sent, txn.currency_received, txn.amount_received), ("USD", "EUR", Decimal("85.00")))

    def test_transfer_between_own_accounts_single_notification(self):
        other = make_account(self.alice, balance="0")
        r = self.post(self.alice_acct.number, other.number, "10")
        self.assertEqual(r.status_code, 201)
        self.assertEqual(Notification.objects.count(), 1)

    def test_response_does_not_echo_write_only_fields(self):
        r = self.post(self.alice_acct.number, self.bob_acct.number, "1")
        self.assertNotIn("payer_account_number", r.data)
        self.assertNotIn("amount", r.data)


class DebitCardViewTests(TransactionAPITestCase):
    """Tests for POST debit-cards/payment/: card validation, expiry rules, Luhn, balances."""

    url = reverse("api:debit_card_payment")

    def setUp(self):
        super().setUp()
        self.card = make_card(self.bob_acct)

    def pay(self, amount="10", **overrides):
        data = {
            "payee_account_number": self.alice_acct.number, "card_number": str(self.card.card_number),
            "cvv": self.card.cvv, "expiration_date": card_expiry(self.card), "amount": amount,
        }
        data.update(overrides)
        return self.client.post(self.url, data)

    def test_happy_path_moves_funds_from_card_account(self):
        r = self.pay("20")
        self.assertEqual(r.status_code, 201, r.data)
        self.refresh(self.alice_acct, self.bob_acct)
        self.assertEqual((self.alice_acct.balance, self.bob_acct.balance), (Decimal("120.00"), Decimal("30.00")))
        txn = Transaction.objects.get()
        self.assertEqual((txn.transaction_type, txn.payer, txn.payee), ("DEBIT_CARD", self.bob_acct, self.alice_acct))
        self.assertEqual(Notification.objects.filter(user=self.bob).count(), 1)
        self.assertEqual(Notification.objects.filter(user=self.alice).count(), 1)

    def test_payee_account_must_belong_to_caller(self):
        self.assertEqual(self.pay(payee_account_number=self.bob_acct.number).status_code, 403)

    def test_unknown_payee_404(self):
        self.assertEqual(self.pay(payee_account_number="9999999999").status_code, 404)

    def test_invalid_month_and_year(self):
        self.assertEqual(self.pay(expiration_date="13/30").status_code, 403)
        self.assertEqual(self.pay(expiration_date="01/00").status_code, 403)

    def test_bad_expiry_format_403_not_500(self):
        self.assertEqual(self.pay(expiration_date="nope").status_code, 403)

    def test_luhn_failure_403(self):
        self.assertEqual(self.pay(card_number="4111111111111112").status_code, 403)

    def test_non_numeric_card_number_403_not_500(self):
        self.assertEqual(self.pay(card_number="4111-1111-1111-1111").status_code, 403)

    def test_wrong_cvv_403(self):
        self.assertEqual(self.pay(cvv="999").status_code, 403)
        self.assertEqual(Transaction.objects.count(), 0)

    def test_card_on_payee_account_rejected(self):
        own_card = make_card(self.alice_acct)
        r = self.pay(card_number=str(own_card.card_number), cvv=own_card.cvv, expiration_date=card_expiry(own_card))
        self.assertEqual(r.status_code, 403)

    def test_insufficient_funds_notifies_both_parties(self):
        r = self.pay("50.01")
        self.assertEqual(r.status_code, 403)
        self.assertEqual(Notification.objects.filter(user=self.alice).count(), 1)
        self.assertEqual(Notification.objects.filter(user=self.bob).count(), 1)
        self.refresh(self.bob_acct)
        self.assertEqual(self.bob_acct.balance, Decimal("50.00"))
