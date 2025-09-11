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
          
        # Reconnection settings  
        self.auto_reconnect = True  
        self.reconnect_attempts = 0  
        self.max_reconnect_attempts = 10  
        self.reconnect_delay = 5.0  
        self._reconnect_task = None  

    async def connect(self) -> bool:  
        """Connect to ESPHome device with automatic reconnection"""  
        try:  
            await self.client.connect(login=True)  
  
            # Get device info  
            device_info = await self.client.device_info()  
            self.logger.info(f"Connected to {device_info.name} (ESPHome {device_info.esphome_version})")  
  
            # List entities  
            entities, services = await self.client.list_entities_services()  
            self.entities = {entity.key: entity for entity in entities}  
  
            # Setup reconnect logic with callbacks  
            self.reconnect_logic = ReconnectLogic(  
                client=self.client,  
                on_disconnect=self._on_disconnect,  
                on_connect=self._on_connect,  
                zeroconf_instance=None,  # Optional zeroconf for discovery  
                name=f"{self.host}:{self.port}"  
            )  
  
            # Start reconnect logic  
            await self.reconnect_logic.start()  
  
            if self.state_callback:  
                self.client.subscribe_states(self.state_callback)  
  
            self.connected = True  
            self.reconnect_attempts = 0  # Reset counter on successful connection  
  
            return True  
  
        except APIConnectionError as e:  
            self.logger.error(f"Failed to connect to {self.host}:{self.port}: {e}")  
            if self.auto_reconnect:  
                await self._schedule_reconnect()  
            return False  
        except Exception as e:  
            self.logger.error(f"Unexpected error connecting to {self.host}:{self.port}: {e}")  
            if self.auto_reconnect:  
                await self._schedule_reconnect()  
            return False  
  
    async def disconnect(self):  
        """Disconnect from device and stop reconnection"""  
        self.auto_reconnect = False  
        self.connected = False  
          
        # Cancel reconnection task  
        if self._reconnect_task and not self._reconnect_task.done():  
            self._reconnect_task.cancel()  
            try:  
                await self._reconnect_task  
            except asyncio.CancelledError:  
                pass  
  
        # Stop reconnect logic  
        if self.reconnect_logic:  
            await self.reconnect_logic.stop()  
            self.reconnect_logic = None  
  
        # Disconnect client  
        try:  
            await self.client.disconnect()  
        except Exception as e:  
            self.logger.warning(f"Error during disconnect: {e}")  
  
    def is_connected(self) -> bool:  
        """Check if connected"""  
        return self.connected and self.client._connection and self.client._connection.is_connected  

  
    async def _on_connect(self):  
        """Called when connection is established"""  
        self.connected = True  
        self.reconnect_attempts = 0  
        self.logger.info(f"Connected to ESPHome device at {self.host}:{self.port}")  
  
        # Re-subscribe to state changes after reconnection  
        if self.state_callback:  
            try:  
                self.client.subscribe_states(self.state_callback)  
                self.logger.debug(f"Re-subscribed to states for {self.host}:{self.port}")  
            except Exception as e:  
                self.logger.error(f"Failed to re-subscribe to states: {e}")  
  
    async def _on_disconnect(self):  
        """Called when connection is lost"""  
        self.connected = False  
        self.logger.warning(f"Disconnected from ESPHome device at {self.host}:{self.port}")  
          
        # Schedule reconnection if auto-reconnect is enabled  
        if self.auto_reconnect:  
            await self._schedule_reconnect()  
  
    async def _schedule_reconnect(self):  
        """Schedule reconnection attempt"""  
        if self.reconnect_attempts >= self.max_reconnect_attempts:  
            self.logger.error(f"Max reconnection attempts ({self.max_reconnect_attempts}) reached for {self.host}:{self.port}")  
            self.auto_reconnect = False  
            return  
  
        self.reconnect_attempts += 1  
          
        # Exponential backoff with jitter  
        delay = min(self.reconnect_delay * (2 ** (self.reconnect_attempts - 1)), 300)  # Max 5 minutes  
        jitter = delay * 0.1 * (0.5 - asyncio.get_event_loop().time() % 1)  # Add some randomness  
        total_delay = delay + jitter  
  
        self.logger.info(f"Scheduling reconnection attempt {self.reconnect_attempts} in {total_delay:.1f}s for {self.host}:{self.port}")  
          
        # Cancel previous reconnect task if exists  
        if self._reconnect_task and not self._reconnect_task.done():  
            self._reconnect_task.cancel()  
  
        self._reconnect_task = asyncio.create_task(self._reconnect_after_delay(total_delay))  
  
    async def _reconnect_after_delay(self, delay: float):  
        """Reconnect after specified delay"""  
        try:  
            await asyncio.sleep(delay)  
              
            if not self.auto_reconnect:  
                return  
                  
            self.logger.info(f"Attempting reconnection {self.reconnect_attempts} to {self.host}:{self.port}")  
              
            # Try to reconnect  
            success = await self.connect()  
            if not success:  
                self.logger.warning(f"Reconnection attempt {self.reconnect_attempts} failed for {self.host}:{self.port}")  
                  
        except asyncio.CancelledError:  
            self.logger.debug(f"Reconnection cancelled for {self.host}:{self.port}")  
        except Exception as e:  
            self.logger.error(f"Error during reconnection attempt: {e}")  
  
    def enable_auto_reconnect(self, max_attempts: int = 10, delay: float = 5.0):  
        """Enable automatic reconnection"""  
        self.auto_reconnect = True  
        self.max_reconnect_attempts = max_attempts  
        self.reconnect_delay = delay  
        self.reconnect_attempts = 0  
  
    def disable_auto_reconnect(self):  
        """Disable automatic reconnection"""  
        self.auto_reconnect = False  
        if self._reconnect_task and not self._reconnect_task.done():  
            self._reconnect_task.cancel()  
  
    async def force_reconnect(self):  
        """Force immediate reconnection"""  
        self.logger.info(f"Forcing reconnection to {self.host}:{self.port}")  
          
        # Disconnect first  
        await self.disconnect()  
          
        # Re-enable auto-reconnect and connect  
        self.auto_reconnect = True  
        self.reconnect_attempts = 0  
          
        return await self.connect()
    
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

            self.client.subscribe_states(callback)  
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
        
    def set_text_state(self, key: int, state: str) -> bool:
        """Control text entity"""
        try:
            if not self.is_connected():
                return False

            self.client.text_command(key, state)
            return True

        except Exception as e:
            self.logger.error(f"Failed to set textsensor state: {e}")
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
