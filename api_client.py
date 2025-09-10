import asyncio  
from typing import Dict, List, Optional, Callable  
from aioesphomeapi import APIClient, APIConnectionError, ReconnectLogic  
from app.logging_config import getLogger  

class ESPHomeAPIClient:  
    def __init__(self, host: str, port: int = 6053, password: str = "", logger=None):  
        self.host = host  
        self.port = port  
        self.password = password  
        self.logger = logger or getLogger('ESPHome.APIClient')  

        self.client = APIClient(host, port, password)  
        self.reconnect_logic = None  
        self.connected = False  
        self.entities = {}  
        self.state_callback: Optional[Callable] = None  

    async def connect(self) -> bool:  
        """Connect to ESPHome device"""  
        try:  
            await self.client.connect(login=True)  

            # Get device info
            device_info = await self.client.device_info()  
            self.logger.info(f"Connected to {device_info.name} (ESPHome {device_info.esphome_version})")  

            # List entities
            entities, services = await self.client.list_entities_services()  
            self.entities = {entity.key: entity for entity in entities}  

            # Setup reconnect logic
            self.reconnect_logic = ReconnectLogic(  
                client=self.client,  
                on_disconnect=self._on_disconnect,  
                on_connect=self._on_connect  
            )  

            if self.state_callback:  
                self.client.subscribe_states(self.state_callback)    

            self.connected = True  

            return True  

        except APIConnectionError as e:  
            self.logger.error(f"Failed to connect to {self.host}:{self.port}: {e}")  
            return False  
        except Exception as e:  
            self.logger.error(f"Unexpected error connecting to {self.host}:{self.port}: {e}")  
            return False  

    async def disconnect(self):  
        """Disconnect from device"""  
        self.connected = False  
        if self.reconnect_logic:  
            await self.reconnect_logic.stop()  
        await self.client.disconnect()  

    def is_connected(self) -> bool:  
        """Check if connected"""  
        return self.connected and self.client._connection.is_connected  

    async def _on_connect(self):  
        """Called when connection is established"""  
        self.connected = True  
        self.logger.info(f"Connected to ESPHome device at {self.host}:{self.port}")  

        # Subscribe to state changes
        if self.state_callback:  
            await self.client.subscribe_states(self.state_callback)  

    async def _on_disconnect(self):  
        """Called when connection is lost"""  
        self.connected = False  
        self.logger.warning(f"Disconnected from ESPHome device at {self.host}:{self.port}")  

    async def get_device_info(self) -> Optional[dict]:  
        """Get device information"""  
        try:  
            if not self.is_connected():  
                return None  

            device_info = await self.client.device_info()  
            return {  
                'name': device_info.name,  
                'esphome_version': device_info.esphome_version,  
                'compilation_time': device_info.compilation_time,  
                'model': device_info.model,  
                'mac_address': device_info.mac_address  
            }  

        except Exception as e:  
            self.logger.error(f"Failed to get device info: {e}")  
            return None  

    async def list_entities(self) -> List[dict]:  
        """List all entities on device"""  
        try:  
            if not self.is_connected():  
                return []  

            entities, _ = await self.client.list_entities_services()  
            result = []  

            for entity in entities:  
                entity_dict = {  
                    'key': entity.key,  
                    'name': entity.name,  
                    'type': entity.__class__.__name__.lower().replace('info', ''),  
                    'unique_id': entity.object_id,  
                }  

                # Add type-specific attributes
                if hasattr(entity, 'unit_of_measurement'):  
                    entity_dict['unit_of_measurement'] = entity.unit_of_measurement  
                if hasattr(entity, 'device_class'):  
                    entity_dict['device_class'] = entity.device_class  
                if hasattr(entity, 'icon'):  
                    entity_dict['icon'] = entity.icon  

                result.append(entity_dict)  

            return result  

        except Exception as e:  
            self.logger.error(f"Failed to list entities: {e}")  
            return []  

    def set_state_callback(self, callback: Callable):  
        """Set callback for state changes"""  
        self.state_callback = callback  

    async def subscribe_states(self, callback: Callable):  
        """Subscribe to state changes"""  
        try:  
            if not self.is_connected():  
                return False  

            await self.client.subscribe_states(callback)  
            return True  

        except Exception as e:  
            self.logger.error(f"Failed to subscribe to states: {e}")  
            return False  

    def set_number_state(self, key: int, state: float) -> bool:
        """Control number entity"""
        try:
            if not self.is_connected():
                return False

            self.client.number_command(key, state)
            return True

        except Exception as e:
            self.logger.error(f"Failed to set number state: {e}")
            return False

    def set_switch_state(self, key: int, state: bool) -> bool:
        """Control switch entity"""
        try:
            if not self.is_connected():
                return False

            self.client.switch_command(key, state)
            return True

        except Exception as e:
            self.logger.error(f"Failed to set switch state: {e}")
            return False

    async def set_light_state(
        self, key: int, state: bool, brightness: float = None, rgb: tuple = None
    ) -> bool:
        """Control light entity"""
        try:
            if not self.is_connected():
                return False

            self.client.light_command(
                key=key, state=state, brightness=brightness, rgb=rgb
            )
            return True

        except Exception as e:
            self.logger.error(f"Failed to set light state: {e}")
            return False
