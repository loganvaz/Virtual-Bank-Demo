from channels.generic.websocket import AsyncWebsocketConsumer
from channels.db import database_sync_to_async
import json
from virtual_bank.logging import get_logger

logger = get_logger(__name__)

class Consumer(AsyncWebsocketConsumer):
    async def connect(self):
        self.user = self.scope['user']
        logger.debug("websocket connect", extra={"user_id": getattr(self.user, 'pk', None)})
        
        if self.user.is_authenticated:
            self.group_name = f"user_{self.user.id}"
            
            await self.channel_layer.group_add(
                self.group_name,
                self.channel_name
            )
            
            self.send()
            await self.accept()
        else:
            await self.close()

    async def disconnect(self, close_code):
        if self.user.is_authenticated:
            await self.channel_layer.group_discard(
                self.group_name,
                self.channel_name
            )

    async def receive(self, text_data):
        data = json.loads(text_data)
        logger.debug("websocket message received", extra={"user_id": getattr(self.user, 'pk', None)})

    async def send_transaction(self, event):
        data = event['data']
        
        await self.send(text_data=json.dumps({
            'content': data,
            'event': 'transaction'
        }))
        
    async def send_notification(self, event):
        message = event['data']
        
        await self.send(text_data=json.dumps({
            'content': message,
            'event': 'notification'
        }))