from channels.generic.websocket import AsyncWebsocketConsumer
from channels.db import database_sync_to_async
import json

from virtual_bank.log_redaction import get_redacted_logger

logger = get_redacted_logger(__name__)


class Consumer(AsyncWebsocketConsumer):
    async def connect(self):
        self.user = self.scope['user']
        print(self.user)
        
        if self.user.is_authenticated:
            self.group_name = f"user_{self.user.id}"
            
            await self.channel_layer.group_add(
                self.group_name,
                self.channel_name
            )
            
            self.send()
            await self.accept()
            logger.info("Websocket connected user_id=%s group=%s", self.user.id, self.group_name)
        else:
            logger.warning("Websocket rejected: unauthenticated connection attempt")
            await self.close()

    async def disconnect(self, close_code):
        if self.user.is_authenticated:
            await self.channel_layer.group_discard(
                self.group_name,
                self.channel_name
            )
            logger.info("Websocket disconnected user_id=%s close_code=%s", self.user.id, close_code)

    async def receive(self, text_data):
        data = json.loads(text_data)
        print(data)
        logger.debug("Websocket message received user_id=%s size=%s", self.user.id, len(text_data))

    async def send_transaction(self, event):
        data = event['data']
        logger.info(
            "Websocket transaction pushed user_id=%s transaction_id=%s amount=%s currency=%s",
            self.user.id, data.get('id'), data.get('amount_sent'), data.get('currency_sent'),
        )
        
        await self.send(text_data=json.dumps({
            'content': data,
            'event': 'transaction'
        }))
        
    async def send_notification(self, event):
        message = event['data']
        logger.info(
            "Websocket notification pushed user_id=%s notification_id=%s type=%s",
            self.user.id, message.get('id'), message.get('notification_type'),
        )
        
        await self.send(text_data=json.dumps({
            'content': message,
            'event': 'notification'
        }))