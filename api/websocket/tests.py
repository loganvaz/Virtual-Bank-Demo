import gc
import json
import re
import warnings
from contextlib import asynccontextmanager
from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock, patch

from asgiref.sync import sync_to_async

from channels.layers import get_channel_layer
from channels.routing import URLRouter
from channels.testing import WebsocketCommunicator
from django.contrib.auth.models import AnonymousUser
from django.db.models.signals import post_save
from django.test import SimpleTestCase, TestCase, override_settings

from accounts.models import Account
from notifications.models import Notification
from transactions.models import Transaction
from users.models import User

from .apps import WebsocketConfig
from .consumers import Consumer
from .routing import websocket_urlpatterns
from .signals import notification_created, transaction_created

IN_MEMORY_CHANNEL_LAYERS = {"default": {"BACKEND": "channels.layers.InMemoryChannelLayer"}}
CONSUMERS_LOGGER = "websocket.consumers"
SIGNALS_LOGGER = "websocket.signals"
SOCKET_PATH = "/ws/socket/"


@override_settings(CHANNEL_LAYERS=IN_MEMORY_CHANNEL_LAYERS)
class WebsocketTestBase(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.alice = User.objects.create_user(
            username="alice", email="alice@example.com", password="pw",
            first_name="Alice", last_name="Anderson",
        )
        cls.bob = User.objects.create_user(
            username="bob", email="bob@example.com", password="pw",
            first_name="Bob", last_name="Brown",
        )
        cls.alice_usd = Account.objects.create(
            user=cls.alice, name="Alice USD", currency="USD",
            balance=Decimal("1000.00"), number=100000000001,
        )
        cls.bob_ngn = Account.objects.create(
            user=cls.bob, name="Bob NGN", currency="NGN",
            balance=Decimal("500000.00"), number=200000000001,
        )

    @staticmethod
    def make_transaction(payer, payee, transaction_type="TRANSFER", amount=Decimal("25.00")):
        return Transaction.objects.create(
            account=payer, payer=payer, payee=payee, transaction_type=transaction_type,
            amount_sent=amount, amount_received=amount,
            currency_sent=payer.currency, currency_received=payee.currency,
        )

    @staticmethod
    def make_notification(user, notification_type="TRANSACTION_NOTIFICATION", content="You got paid"):
        return Notification.objects.create(user=user, notification_type=notification_type, content=content)

    @staticmethod
    def communicator(user, app=None, path=SOCKET_PATH):
        communicator = WebsocketCommunicator(app or Consumer.as_asgi(), path)
        communicator.scope["user"] = user
        return communicator

    @asynccontextmanager
    async def connected(self, user, **kwargs):
        communicator = self.communicator(user, **kwargs)
        connected, _ = await communicator.connect()
        self.assertTrue(connected)
        try:
            yield communicator
        finally:
            if not communicator.future.done():
                await communicator.disconnect()

    async def assert_still_connected(self, communicator):
        self.assertTrue(await communicator.receive_nothing())
        if communicator.future.done():
            communicator.future.result()

    def pii_values(self):
        values = [str(self.alice_usd.number), str(self.bob_ngn.number), "pw"]
        for user in (self.alice, self.bob):
            values += [user.first_name, user.last_name, user.email]
        return values

    def assert_no_pii(self, output):
        text = "\n".join(output)
        for value in self.pii_values():
            self.assertNotIn(value, text)

    def assertLogged(self, output, level, *fragments):
        matching = [line for line in output if line.startswith(f"{level}:")]
        self.assertTrue(
            any(all(f in line for f in fragments) for line in matching),
            f"No {level} log containing {fragments!r} in {output!r}",
        )


class AppConfigTests(SimpleTestCase):
    def test_app_config(self):
        self.assertEqual(WebsocketConfig.name, "websocket")
        self.assertEqual(WebsocketConfig.default_auto_field, "django.db.models.BigAutoField")


class RoutingTests(WebsocketTestBase):
    def test_single_route_points_at_consumer(self):
        self.assertEqual(len(websocket_urlpatterns), 1)
        self.assertIs(websocket_urlpatterns[0].callback.consumer_class, Consumer)
        self.assertTrue(websocket_urlpatterns[0].pattern.match("ws/socket/"))
        self.assertIsNone(websocket_urlpatterns[0].pattern.match("ws/other/"))

    async def test_router_dispatches_socket_path_to_consumer(self):
        async with self.connected(self.alice, app=URLRouter(websocket_urlpatterns)) as communicator:
            self.assertTrue(await communicator.receive_nothing())

    async def test_router_rejects_unknown_path(self):
        communicator = self.communicator(self.alice, app=URLRouter(websocket_urlpatterns), path="/ws/unknown/")
        with self.assertRaisesMessage(ValueError, "No route found for path"):
            await communicator.connect()


class ConsumerConnectTests(WebsocketTestBase):
    async def test_authenticated_user_is_accepted_and_joins_user_group(self):
        async with self.connected(self.alice) as communicator:
            layer = get_channel_layer()
            self.assertIn(f"user_{self.alice.id}", layer.groups)
            self.assertEqual(len(layer.groups[f"user_{self.alice.id}"]), 1)
            self.assertTrue(await communicator.receive_nothing())

    async def test_authenticated_connect_is_logged_without_pii(self):
        with self.assertLogs(CONSUMERS_LOGGER, level="INFO") as cm:
            async with self.connected(self.alice):
                pass
        self.assertLogged(cm.output, "INFO", "Websocket connected", f"user_id={self.alice.id}", f"group=user_{self.alice.id}")
        self.assert_no_pii(cm.output)

    async def test_anonymous_user_is_closed_without_joining_a_group(self):
        communicator = self.communicator(AnonymousUser())
        connected, code = await communicator.connect()
        self.assertFalse(connected)
        self.assertEqual(code, 1000)
        self.assertNotIn("user_None", get_channel_layer().groups)

    async def test_anonymous_rejection_is_logged_without_pii(self):
        communicator = self.communicator(AnonymousUser())
        with self.assertLogs(CONSUMERS_LOGGER, level="WARNING") as cm:
            await communicator.connect()
        self.assertLogged(cm.output, "WARNING", "Websocket rejected", "unauthenticated")
        self.assert_no_pii(cm.output)

    async def test_anonymous_disconnect_does_not_touch_channel_layer(self):
        communicator = self.communicator(AnonymousUser())
        await communicator.connect()
        with patch.object(Consumer, "channel_layer", create=True) as layer:
            await communicator.disconnect()
        layer.group_discard.assert_not_called()

    async def test_connect_does_not_leave_unawaited_send_coroutine(self):
        """BUG: connect() calls ``self.send()`` without ``await`` (and without any
        payload), so a coroutine object is created and immediately dropped. CPython
        reports this as ``RuntimeWarning: coroutine ... was never awaited`` on every
        connection. The call is dead code and should be removed (or awaited with a
        real payload)."""
        with warnings.catch_warnings(record=True) as caught:
            warnings.simplefilter("always")
            async with self.connected(self.alice):
                gc.collect()
        unawaited = [w for w in caught if issubclass(w.category, RuntimeWarning) and "never awaited" in str(w.message)]
        self.assertEqual(unawaited, [], [str(w.message) for w in unawaited])


class ConsumerDisconnectTests(WebsocketTestBase):
    async def test_disconnect_leaves_user_group(self):
        communicator = self.communicator(self.alice)
        await communicator.connect()
        group = f"user_{self.alice.id}"
        self.assertEqual(len(get_channel_layer().groups[group]), 1)
        await communicator.disconnect()
        self.assertNotIn(group, get_channel_layer().groups)

    async def test_disconnect_is_logged_with_close_code_and_no_pii(self):
        communicator = self.communicator(self.alice)
        await communicator.connect()
        with self.assertLogs(CONSUMERS_LOGGER, level="INFO") as cm:
            await communicator.disconnect(code=4001)
        self.assertLogged(cm.output, "INFO", "Websocket disconnected", f"user_id={self.alice.id}", "close_code=4001")
        self.assert_no_pii(cm.output)


class ConsumerReceiveTests(WebsocketTestBase):
    async def test_receive_json_keeps_connection_open(self):
        async with self.connected(self.alice) as communicator:
            await communicator.send_json_to({"ping": "pong"})
            self.assertTrue(await communicator.receive_nothing())

    async def test_receive_is_logged_with_size_only(self):
        async with self.connected(self.alice) as communicator:
            payload = {"email": self.alice.email, "name": self.alice.first_name}
            with self.assertLogs(CONSUMERS_LOGGER, level="DEBUG") as cm:
                await communicator.send_json_to(payload)
                self.assertTrue(await communicator.receive_nothing())
            self.assertLogged(cm.output, "DEBUG", "Websocket message received", f"user_id={self.alice.id}", f"size={len(json.dumps(payload))}")
            self.assert_no_pii(cm.output)

    async def test_malformed_json_does_not_kill_the_connection(self):
        """BUG: receive() calls ``json.loads`` without error handling, so a single
        malformed client frame raises ``JSONDecodeError`` inside the consumer and the
        socket dies. The consumer should ignore (or reply with an error for) bad
        payloads instead of crashing."""
        async with self.connected(self.alice) as communicator:
            await communicator.send_to(text_data="not json")
            await self.assert_still_connected(communicator)

    async def test_binary_frame_does_not_kill_the_connection(self):
        """BUG: receive() is declared as ``receive(self, text_data)`` but Channels
        invokes it with ``bytes_data=`` for binary frames, so any binary frame raises
        ``TypeError`` and kills the connection. The signature should be
        ``receive(self, text_data=None, bytes_data=None)``."""
        async with self.connected(self.alice) as communicator:
            await communicator.send_to(bytes_data=b"\x00\x01")
            await self.assert_still_connected(communicator)


class ConsumerPushTests(WebsocketTestBase):
    async def test_send_transaction_wraps_data_in_transaction_event(self):
        async with self.connected(self.alice) as communicator:
            data = {"id": 7, "amount_sent": "25.00", "currency_sent": "USD"}
            await get_channel_layer().group_send(f"user_{self.alice.id}", {"type": "send_transaction", "data": data})
            self.assertEqual(await communicator.receive_json_from(), {"content": data, "event": "transaction"})

    async def test_send_transaction_is_logged_without_pii(self):
        async with self.connected(self.alice) as communicator:
            data = {
                "id": 7, "amount_sent": "25.00", "currency_sent": "USD",
                "payer": {"number": str(self.alice_usd.number), "user": {"email": self.alice.email}},
            }
            with self.assertLogs(CONSUMERS_LOGGER, level="INFO") as cm:
                await get_channel_layer().group_send(f"user_{self.alice.id}", {"type": "send_transaction", "data": data})
                await communicator.receive_json_from()
            self.assertLogged(cm.output, "INFO", "Websocket transaction pushed", f"user_id={self.alice.id}", "transaction_id=7", "amount=25.00", "currency=USD")
            self.assert_no_pii(cm.output)

    async def test_send_notification_wraps_data_in_notification_event(self):
        async with self.connected(self.bob) as communicator:
            data = {"id": 3, "notification_type": "ACCOUNT_NOTIFICATION", "content": "hi"}
            await get_channel_layer().group_send(f"user_{self.bob.id}", {"type": "send_notification", "data": data})
            self.assertEqual(await communicator.receive_json_from(), {"content": data, "event": "notification"})

    async def test_send_notification_is_logged_without_pii(self):
        async with self.connected(self.bob) as communicator:
            data = {"id": 3, "notification_type": "ACCOUNT_NOTIFICATION", "content": f"Hello {self.bob.first_name}", "user": {"email": self.bob.email}}
            with self.assertLogs(CONSUMERS_LOGGER, level="INFO") as cm:
                await get_channel_layer().group_send(f"user_{self.bob.id}", {"type": "send_notification", "data": data})
                await communicator.receive_json_from()
            self.assertLogged(cm.output, "INFO", "Websocket notification pushed", f"user_id={self.bob.id}", "notification_id=3", "type=ACCOUNT_NOTIFICATION")
            self.assert_no_pii(cm.output)

    async def test_push_is_scoped_to_target_user_group(self):
        async with self.connected(self.alice) as alice_socket, self.connected(self.bob) as bob_socket:
            await get_channel_layer().group_send(f"user_{self.bob.id}", {"type": "send_notification", "data": {"id": 1}})
            self.assertEqual((await bob_socket.receive_json_from())["event"], "notification")
            self.assertTrue(await alice_socket.receive_nothing())


class SignalRegistrationTests(WebsocketTestBase):
    def receivers_for(self, sender):
        sync_receivers, async_receivers = post_save._live_receivers(sender)
        return list(sync_receivers) + list(async_receivers)

    def test_transaction_handler_is_connected_to_post_save(self):
        self.assertIn(transaction_created, self.receivers_for(Transaction))
        self.assertNotIn(notification_created, self.receivers_for(Transaction))

    def test_notification_handler_is_connected_to_post_save(self):
        self.assertIn(notification_created, self.receivers_for(Notification))
        self.assertNotIn(transaction_created, self.receivers_for(Notification))


class TransactionSignalTests(WebsocketTestBase):
    def setUp(self):
        self.layer = MagicMock()
        self.layer.group_send = AsyncMock()
        patcher = patch("websocket.signals.get_channel_layer", return_value=self.layer)
        patcher.start()
        self.addCleanup(patcher.stop)

    def sent_groups(self):
        return [c.args[0] for c in self.layer.group_send.call_args_list]

    def test_created_transfer_is_sent_to_payer_and_payee_groups(self):
        transaction = self.make_transaction(self.alice_usd, self.bob_ngn)
        self.assertEqual(self.sent_groups(), [f"user_{self.alice.id}", f"user_{self.bob.id}"])
        for call in self.layer.group_send.call_args_list:
            event = call.args[1]
            self.assertEqual(event["type"], "send_transaction")
            self.assertEqual(event["data"]["id"], transaction.pk)
            self.assertEqual(event["data"]["identifier"], str(transaction.identifier))
            self.assertEqual(event["data"]["amount_sent"], "25.00")
            self.assertEqual(event["data"]["payer"]["id"], self.alice_usd.pk)
            self.assertEqual(event["data"]["payee"]["id"], self.bob_ngn.pk)

    def test_event_payload_is_json_serialisable(self):
        self.make_transaction(self.alice_usd, self.bob_ngn)
        json.dumps(self.layer.group_send.call_args.args[1])

    def test_updating_a_transaction_does_not_broadcast(self):
        transaction = self.make_transaction(self.alice_usd, self.bob_ngn)
        self.layer.group_send.reset_mock()
        transaction.description = "edited"
        transaction.save()
        self.layer.group_send.assert_not_called()

    def test_handler_ignores_non_created_saves_when_called_directly(self):
        transaction = self.make_transaction(self.alice_usd, self.bob_ngn)
        self.layer.group_send.reset_mock()
        transaction_created(Transaction, transaction, created=False)
        self.layer.group_send.assert_not_called()

    def test_deposit_notifies_the_account_owner_once(self):
        """BUG: a deposit has payer == payee, so ``transaction_created`` sends the same
        event to ``user_<id>`` twice and the client renders a duplicate transaction.
        The handler should de-duplicate the recipient user ids."""
        self.make_transaction(self.alice_usd, self.alice_usd, transaction_type="DEPOSIT")
        self.assertEqual(self.sent_groups(), [f"user_{self.alice.id}"])

    def test_created_transaction_is_logged_without_pii(self):
        with self.assertLogs(SIGNALS_LOGGER, level="INFO") as cm:
            transaction = self.make_transaction(self.alice_usd, self.bob_ngn)
        self.assertLogged(
            cm.output, "INFO", "Broadcasting transaction", f"id={transaction.pk}",
            f"identifier={transaction.identifier}", "type=TRANSFER", "amount=25.00", "currency=USD",
            f"payer_user_id={self.alice.id}", f"payee_user_id={self.bob.id}",
        )
        self.assert_no_pii(cm.output)

    def test_updating_a_transaction_logs_nothing(self):
        transaction = self.make_transaction(self.alice_usd, self.bob_ngn)
        with self.assertNoLogs(SIGNALS_LOGGER, level="INFO"):
            transaction.save()


class NotificationSignalTests(WebsocketTestBase):
    def setUp(self):
        self.layer = MagicMock()
        self.layer.group_send = AsyncMock()
        patcher = patch("websocket.signals.get_channel_layer", return_value=self.layer)
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_created_notification_is_sent_to_owner_group(self):
        notification = self.make_notification(self.bob)
        self.layer.group_send.assert_called_once()
        group, event = self.layer.group_send.call_args.args
        self.assertEqual(group, f"user_{self.bob.id}")
        self.assertEqual(event["type"], "send_notification")
        self.assertEqual(event["data"]["id"], notification.pk)
        self.assertEqual(event["data"]["notification_type"], "TRANSACTION_NOTIFICATION")
        self.assertEqual(event["data"]["content"], "You got paid")
        self.assertEqual(event["data"]["status"], "UNREAD")
        json.dumps(event)

    def test_handler_called_directly_uses_instance_owner(self):
        notification = self.make_notification(self.alice)
        self.layer.group_send.reset_mock()
        notification_created(Notification, notification, created=True)
        self.assertEqual(self.layer.group_send.call_args.args[0], f"user_{self.alice.id}")

    def test_marking_notification_read_does_not_rebroadcast(self):
        """BUG: ``notification_created`` ignores the ``created`` flag, so every
        ``save()`` (for example flipping status to READ) pushes the notification to the
        client again as if it were new. It should return early when ``created`` is False."""
        notification = self.make_notification(self.bob)
        self.layer.group_send.reset_mock()
        notification.status = "READ"
        notification.save()
        self.layer.group_send.assert_not_called()

    def test_created_notification_is_logged_without_pii(self):
        with self.assertLogs(SIGNALS_LOGGER, level="INFO") as cm:
            notification = self.make_notification(self.bob, content=f"Hi {self.bob.first_name} <{self.bob.email}>")
        self.assertLogged(
            cm.output, "INFO", "Broadcasting notification", f"id={notification.pk}",
            "type=TRANSACTION_NOTIFICATION", "status=UNREAD", f"user_id={self.bob.id}", "created=True",
        )
        self.assert_no_pii(cm.output)


class EndToEndTests(WebsocketTestBase):
    """Signals -> real InMemoryChannelLayer -> consumer -> client."""

    async def test_transfer_reaches_both_parties(self):
        async with self.connected(self.alice) as alice_socket, self.connected(self.bob) as bob_socket:
            transaction = await sync_to_async(self.make_transaction)(self.alice_usd, self.bob_ngn)
            for socket in (alice_socket, bob_socket):
                message = await socket.receive_json_from()
                self.assertEqual(message["event"], "transaction")
                self.assertEqual(message["content"]["id"], transaction.pk)
                self.assertEqual(message["content"]["currency_received"], "NGN")

    async def test_notification_reaches_only_its_owner(self):
        async with self.connected(self.alice) as alice_socket, self.connected(self.bob) as bob_socket:
            notification = await sync_to_async(self.make_notification)(self.alice)
            message = await alice_socket.receive_json_from()
            self.assertEqual(message, {
                "content": {
                    "id": notification.pk,
                    "user": message["content"]["user"],
                    "notification_type": "TRANSACTION_NOTIFICATION",
                    "content": "You got paid",
                    "status": "UNREAD",
                    "created_date": message["content"]["created_date"],
                },
                "event": "notification",
            })
            self.assertEqual(message["content"]["user"]["id"], self.alice.id)
            self.assertTrue(await bob_socket.receive_nothing())

    async def test_full_flow_logs_never_contain_pii(self):
        async with self.connected(self.alice) as socket:
            with self.assertLogs(CONSUMERS_LOGGER, level="INFO") as consumer_logs, \
                    self.assertLogs(SIGNALS_LOGGER, level="INFO") as signal_logs:
                await sync_to_async(self.make_transaction)(self.alice_usd, self.bob_ngn)
                await socket.receive_json_from()
            self.assertLogged(consumer_logs.output, "INFO", "Websocket transaction pushed", f"user_id={self.alice.id}")
            self.assertLogged(signal_logs.output, "INFO", "Broadcasting transaction")
            self.assert_no_pii(consumer_logs.output + signal_logs.output)
            self.assertIsNone(re.search(r"\d{10,19}", "\n".join(consumer_logs.output + signal_logs.output)))
