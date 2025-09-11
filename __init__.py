import asyncio
import threading
from datetime import datetime
from flask import jsonify, redirect
from app.core.main.BasePlugin import BasePlugin
from app.database import session_scope, row2dict
from plugins.ESPHome.models import ESPHomeDevice, ESPHomeSensor
from plugins.ESPHome.discovery import ESPHomeDiscovery
from plugins.ESPHome.api_client import ESPHomeAPIClient
from app.core.lib.object import callMethod, updateProperty
from app.api import api

class ESPHome(BasePlugin):
    def __init__(self, app):
        super().__init__(app,__name__)
        self.title = "ESPHome"
        self.description = "Native API ESPHome protocol integration"
        self.category = "Devices"
        self.author = "Eraser"
        self.version = 1
        self.actions = ['cycle', 'search', 'widget']

        self.discovery = ESPHomeDiscovery(self.logger)
        self.api_clients = {}
        self.loop = None
        self._loop_thread = None

        from plugins.ESPHome.api import create_api_ns
        api_ns = create_api_ns(self)
        api.add_namespace(api_ns, path="/ESPHome")

    def initialization(self):
        """Initialize plugin"""
        self.logger.info("Initializing ESPHome plugin")

        # Start event loop in separate thread
        self._start_event_loop()

        # Load existing devices from database
        if self.loop:
            asyncio.run_coroutine_threadsafe(self.load_devices(), self.loop)

    def _start_event_loop(self):
        """Start asyncio event loop in separate thread"""
        def run_loop():
            asyncio.set_event_loop(self.loop)
            self.loop.run_forever()

        self.loop = asyncio.new_event_loop() 
        self._loop_thread = threading.Thread(target=run_loop, daemon=True)
        self._loop_thread.start()

    def admin(self, request):
        action = request.args.get('action')

        if action == 'discover_devices':
            self.trigger_discovery()
            return redirect(self.name)

        content = {
            'settings': self.config,
            'discovery_enabled': self.config.get('auto_discovery', True)
        }
        return self.render('esphome_admin.html', content)

    def cyclic_task(self):  
        """Background task for data collection"""  
        try:  
            self.event.wait(60.0)
            # if self.loop:  
            #     asyncio.run_coroutine_threadsafe(self._async_cyclic_task(), self.loop)  
              
            # self.dtUpdated = datetime.utcnow()  
              
        except Exception as e:  
            self.logger.error(f"Error in cyclic task: {e}")  

    def stop_cycle(self):
        """Переопределяем остановку цикла"""
        if self.loop:
            for client in self.api_clients.values():
                if client.is_connected():
                    asyncio.run_coroutine_threadsafe(client.disconnect(),self.loop)
            self.loop.call_soon_threadsafe(self.loop.stop)

        super().stop_cycle()

    async def _async_cyclic_task(self):  
        """Async cyclic task"""  
        await self.update_connections()  
      
    def search(self, query):  
        """Search for ESPHome devices"""  
        results = []  
          
        with session_scope() as session:  
            devices = session.query(ESPHomeDevice).filter(  
                ESPHomeDevice.name.contains(query)  
            ).all()  
              
            for device in devices:  
                results.append({  
                    'url': f'/admin/ESPHome?device_id={device.id}',  
                    'title': f'ESPHome Device: {device.name}',  
                    'tags': [{'name': 'ESPHome', 'color': 'primary'}]  
                })  
          
        return results  
      
    def widget(self):  
        """Dashboard widget"""  
        with session_scope() as session:  
            total_devices = session.query(ESPHomeDevice).count()  
            connected_devices = len(self.api_clients)  
        content = {  
            'total_devices': total_devices,  
            'connected_devices': connected_devices,  
        }  
          
        return self.render('esphome_widget.html', content)  
      
    async def load_devices(self):  
        """Load devices from database and establish connections"""  
        with session_scope() as session:  
            devices = session.query(ESPHomeDevice).filter_by(enabled=True).all()  
              
            for device in devices:  
                await self.async_connect_device(device)  

    def connect_device(self, device):
        if self.loop:
            asyncio.run_coroutine_threadsafe(self.async_connect_device(device), self.loop)

    def update_connections(self, device):
        if self.loop:
            asyncio.run_coroutine_threadsafe(self.async_update_connections(device), self.loop)

    async def async_update_connections(self, device):  
        """Update device connections"""  
        client = self.api_clients[device.name]
        await client.disconnect()  
        del self.api_clients[device.name]  
        await self.async_connect_device(device)  
  
    def remove_device(self, device_name):  
        """Remove device"""  
        try:  
            if device_name in self.api_clients:  
                if self.loop:  
                    asyncio.run_coroutine_threadsafe(self.api_clients[device_name].disconnect(),self.loop)  
                del self.api_clients[device_name]  
        except Exception as e:  
            self.logger.error(f"Error removing device: {e}")  

    async def async_connect_device(self, device):  
        """Connect to ESPHome device"""  
        try:  
            client = ESPHomeAPIClient(  
                host=device.host,  
                port=device.port,  
                password=device.password,  
                logger=self.logger  
            )
            # Enable auto-reconnect with custom settings  
            client.enable_auto_reconnect(max_attempts=5, delay=10.0) 

            dev = row2dict(device)
            client.set_state_callback(lambda state: self.on_state_change(dev, state))
              
            if await client.connect():  
                self.api_clients[device.name] = client  
                  
                # Update device info  
                device_info = await client.get_device_info()  
                if device_info:  
                    with session_scope() as session:  
                        db_device = session.query(ESPHomeDevice).get(device.id)  
                        db_device.firmware_version = device_info.get('esphome_version')  
                        db_device.mac_address = device_info.get('mac_address')  
                        db_device.last_seen = datetime.utcnow()  
                        session.commit()  
                  
                # Discover sensors  
                await self.discover_device_sensors(device, client)  
                  
                self.logger.info(f"Connected to ESPHome device: {device.name}")  
            else:  
                self.logger.warning(f"Failed to connect to device: {device.name}")  
                  
        except Exception as e:  
            self.logger.error(f"Error connecting to device {device.name}: {e}")  
      
    def on_state_change(self, device, state):
        """Handle state changes from ESPHome device"""
        self.logger.debug(f"State {device['name']} changed: {state}")
        try:
            with session_scope() as session:
                sensor = session.query(ESPHomeSensor).filter_by(
                    device_id=device['id'],
                    entity_key=str(state.key)
                ).first()

                if sensor:
                    old_state = sensor.state
                    sensor.state = str(state.state)
                    sensor.last_updated = datetime.utcnow()

                    if sensor.linked_object:
                        if sensor.linked_property:
                            updateProperty(sensor.linked_object + '.' + sensor.linked_property, state.state, self.name)
                        if sensor.linked_method:
                            callMethod(sensor.linked_object + '.' + sensor.linked_method, {'VALUE': state.state, 'NEW_VALUE': state.state, 'OLD_VALUE': old_state, 'TITLE': sensor.name}, self.name)

                    session.commit()

                    # Send real-time update via WebSocket
                    self.sendDataToWebsocket('esphome_sensor_update', {
                        'device': device['name'],
                        'sensor': sensor.name,
                        'state': str(state.state),
                        'key': state.key,
                        'entity_type': sensor.entity_type
                    })

        except Exception as e:
            self.logger.error(f"Error handling state change: {e}")

    async def discover_device_sensors(self, device, client):  
        """Discover sensors on connected device"""  
        try:  
            entities = await client.list_entities()  
              
            with session_scope() as session:  
                for entity in entities:  
                    existing = session.query(ESPHomeSensor).where(  
                        ESPHomeSensor.device_id == device.id,
                        ESPHomeSensor.entity_key == str(entity['key'])
                    ).one_or_none()

                    if not existing:
                        sensor = ESPHomeSensor(  
                            device_id=device.id,  
                            entity_key=str(entity['key']),  
                            name=entity['name'],  
                            entity_type=entity['type'],  
                            unit_of_measurement=entity.get('unit_of_measurement'),  
                            device_class=entity.get('device_class'),  
                            discovered_at=datetime.utcnow()  
                        )  
                        session.add(sensor)  
                  
                session.commit()  
                  
        except Exception as e:  
            self.logger.error(f"Error discovering sensors for {device.name}: {e}")  
      
    def trigger_discovery(self):  
        """Run device discovery"""  
        try:  
            discovered_devices = self.discovery.discover_devices()  
              
            for device_info in discovered_devices:  
                self.add_discovered_device(device_info)  
                  
        except Exception as e:  
            self.logger.error(f"Error in discovery: {e}")  
      
    def add_discovered_device(self, device_info):  
        """Add discovered device to database"""  
        try:  
            with session_scope() as session:  
                existing = session.query(ESPHomeDevice).filter_by(  
                    host=device_info['host'],  
                    port=device_info['port']  
                ).first()  
                  
                if not existing:  
                    device = ESPHomeDevice(  
                        name=device_info['name'],  
                        host=device_info['host'],  
                        port=device_info['port'],  
                        discovered_at=datetime.utcnow()  
                    )  
                    session.add(device)  
                    session.commit()  
                      
                    self.logger.info(f"Discovered new ESPHome device: {device_info['name']}")  
                      
                    # Try to connect  
                    self.connect_device(device)  
                  
        except Exception as e:  
            self.logger.error(f"Error adding discovered device: {e}")

    def changeLinkedProperty(self, obj, prop, val):  
        """Handle linked property changes from osysHome objects  
        
        This method is called when a linked osysHome object property changes.  
        It controls ESPHome devices based on sensor linking configuration.  
        
        Args:  
            obj (str): Object name that changed  
            prop (str): Property name that changed    
            val (any): New property value  
        """  
        try:  
            self.logger.debug(f"changeLinkedProperty: {obj}.{prop} = {val}")  
            
            # Find sensors linked to this object.property  
            with session_scope() as session:  
                linked_sensors = session.query(ESPHomeSensor).filter(  
                    ESPHomeSensor.linked_object == obj,  
                    ESPHomeSensor.linked_property == prop,  
                    ESPHomeSensor.enabled == True  
                ).all()  
                
                for sensor in linked_sensors:  
                    device = sensor.device  
                    if device.name in self.api_clients:  
                        # Control the ESPHome entity based on sensor type  
                        _sensor = row2dict(sensor)
                        _sensor['device'] = row2dict(sensor.device)  
                        self._control_linked_sensor(_sensor, val)
        except Exception as e:  
            self.logger.error(f"Error in changeLinkedProperty: {e}")  
  
    def _control_linked_sensor(self, sensor, value):  
        """Control ESPHome sensor based on linked property change"""  
        try:  
            device_name = sensor['device']['name']  
            client = self.api_clients[device_name]  
            
            if not client.is_connected():  
                self.logger.warning(f"Device {device_name} not connected")  
                return  
                
            entity_key = int(sensor['entity_key'])  
            
            if sensor['entity_type'] == 'switch':
                # Convert value to boolean
                state = self._convert_to_boolean(value)
                success = client.set_switch_state(entity_key, state)
            elif sensor['entity_type'] == 'number':
                success = client.set_number_state(entity_key, value)
            elif sensor['entity_type'] == 'light':
                if isinstance(value, dict):  
                    # Complex light control  
                    state = value.get('state', True)  
                    brightness = value.get('brightness')  
                    rgb = value.get('rgb')  
                    success = client.set_light_state(entity_key, state, brightness, rgb)  
                else:  
                    # Simple on/off control  
                    state = self._convert_to_boolean(value)  
                    success = client.set_light_state(entity_key, state)  
                    
            elif sensor['entity_type'] == 'cover':  
                # Cover control  
                if str(value).lower() in ['open', '1', 'true']:  
                    success = client.cover_command(entity_key, 'OPEN')  
                elif str(value).lower() in ['close', '0', 'false']:  
                    success = client.cover_command(entity_key, 'CLOSE')  
                else:  
                    success = client.cover_command(entity_key, 'STOP')  
                    
            else:  
                self.logger.warning(f"Unsupported entity type for control: {sensor['entity_type']}")  
                return  
            
            if success:  
                self.logger.info(f"Successfully controlled {device_name} sensor {sensor['name']}: {value}")  
            else:  
                self.logger.error(f"Failed to control {device_name} sensor {sensor['name']}: {value}")  
                
        except Exception as e:  
            self.logger.error(f"Error controlling linked sensor {sensor['name']}: {e}")
           
    def _convert_to_boolean(self, value):  
        """Convert various value types to boolean"""  
        if isinstance(value, bool):  
            return value  
        elif isinstance(value, (int, float)):  
            return bool(value)  
        elif isinstance(value, str):  
            return value.lower() in ['true', '1', 'on', 'yes']  
        else:  
            return bool(value)