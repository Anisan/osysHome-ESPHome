import asyncio
import threading
import json
from datetime import datetime
from flask import redirect
from app.core.main.BasePlugin import BasePlugin
from app.database import session_scope, row2dict
from plugins.ESPHome.models import ESPHomeDevice, ESPHomeSensor
from plugins.ESPHome.discovery import ESPHomeDiscovery
from plugins.ESPHome.api_client import ESPHomeAPIClient
from app.core.lib.object import getProperty, updateProperty
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
                name=device.name,
                host=device.host,
                port=device.port,
                password=device.password,
                logger=self.logger
            )

            dev = row2dict(device)
            client.set_connected_callback(lambda: self.on_connected(dev))
            client.set_state_callback(lambda state: self.on_state_change(dev, state))

            self.api_clients[device.name] = client

            await client.connect()

        except Exception as e:
            self.logger.error(f"Error connecting to device {device.name}: {e}")

    def on_connected(self, dev):
        client = self.api_clients[dev['name']]

        if client.is_connected():
            with session_scope() as session:
                db_device = session.query(ESPHomeDevice).get(dev['id'])

                # Update device info
                device_info = client.device_info
                if device_info:
                    db_device.firmware_version = device_info.get('esphome_version')
                    db_device.mac_address = device_info.get('mac_address')
                db_device.last_seen = datetime.utcnow()
                session.commit()

                # Discover sensors
                self.discover_device_sensors(db_device, client)
        
        self.sendDataToWebsocket('device_update', {
            'device': dev['name'],
            'state': client.is_connected(),
        })

    def _getStates(self, state):
        from aioesphomeapi import SensorState, LightState, ColorMode
        result = {}
        if isinstance(state, SensorState):
            result['state'] = state.state
        elif isinstance(state, LightState):
            result['state'] = state.state
            if state.color_mode in [ColorMode.BRIGHTNESS, ColorMode.LEGACY_BRIGHTNESS, ColorMode.RGB]:
                result['brightness'] = state.brightness * 100
            if state.color_mode == ColorMode.RGB:
                from plugins.ESPHome.utils import rgb_float_to_hex
                result['rgb'] = rgb_float_to_hex(state.red, state.green, state.blue)
        else:
            result = state.to_dict()
            del result['key']
            del result['device_id']
            if 'missing_state' in result:
                del result['missing_state']
        return result

    def on_state_change(self, device, state):
        """Handle state changes from ESPHome device"""
        self.logger.debug(f"State {device['name']} changed: {state.to_dict()}")
        try:
            with session_scope() as session:
                sensor = session.query(ESPHomeSensor).filter_by(
                    device_id=device['id'],
                    entity_key=str(state.key)
                ).first()

                if sensor:

                    values = self._getStates(state)

                    for key, value in values.items():
                        try:
                            if sensor.accuracy_decimals:
                                values[key] = round(value, int(sensor.accuracy_decimals))
                        except Exception as ex:
                            self.logger.exception(ex)

                    str_values = json.dumps(values)
                    #old_state = sensor.state
                    
                    #if old_state == str_values:
                    #    return
                    
                    sensor.state = str_values
                    sensor.last_updated = datetime.utcnow()

                    links = {} 
                    if sensor.links:
                        links = json.loads(sensor.links)

                    for key, value in values.items():
                        if key in links:
                            link = links[key]
                            if link:
                                updateProperty(link, value, self.name)

                    session.commit()

                    # Send real-time update via WebSocket
                    self.sendDataToWebsocket('sensor_update', {
                        'device': device['name'],
                        'sensor': sensor.name,
                        'state': values,
                        'key': state.key,
                    })

        except Exception as e:
            self.logger.error(f"Error handling state change: {e}")

    def discover_device_sensors(self, device, client):
        """Discover sensors on connected device"""
        try:
            entities = client.entities

            with session_scope() as session:
                for entity in entities:
                    existing = session.query(ESPHomeSensor).where(
                        ESPHomeSensor.device_id == device.id,
                        ESPHomeSensor.unique_id == entity['unique_id']
                    ).one_or_none()

                    if not existing:
                        sensor = ESPHomeSensor(
                            device_id=device.id,
                            entity_key=str(entity['key']),
                            unique_id=entity['unique_id'],
                            name=entity['name'],
                            entity_type=entity['type'],
                            unit_of_measurement=entity.get('unit_of_measurement'),
                            icon=entity.get('icon'),
                            device_class=entity.get('device_class'),
                            accuracy_decimals=entity.get('accuracy_decimals'),
                            discovered_at=datetime.utcnow()
                        )
                        session.add(sensor)
                    else:
                        existing.name = entity['name']
                        existing.entity_key = str(entity['key'])
                        existing.entity_type = entity['type']
                        existing.unit_of_measurement = entity.get('unit_of_measurement')
                        existing.device_class = entity.get('device_class')
                        existing.accuracy_decimals = entity.get('accuracy_decimals')
                        existing.icon = entity.get('icon')

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
                existing = (
                    session.query(ESPHomeDevice)
                    .filter_by(host=device_info["host"], port=device_info["port"])
                    .first()
                )

                if not existing:
                    device = ESPHomeDevice(
                        name=device_info["name"],
                        host=device_info["host"],
                        port=device_info["port"],
                        discovered_at=datetime.utcnow(),
                    )
                    session.add(device)
                    session.commit()

                    self.logger.info(
                        f"Discovered new ESPHome device: {device_info['name']}"
                    )

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
                obj_str = str(obj) if obj is not None else ""
                prop_str = str(prop) if prop is not None else ""
                pattern = f"{obj_str}.{prop_str}"
                # Escape SQL wildcards if needed
                escaped_pattern = f"%{pattern.replace('%', '\\%').replace('_', '\\_')}%"
                linked_sensors = (
                    session.query(ESPHomeSensor)
                    .filter(
                        ESPHomeSensor.links.like(escaped_pattern, escape='\\'),
                        ESPHomeSensor.enabled == True, # noqa
                    )
                    .all()
                )

                if len(linked_sensors) == 0:
                    from app.core.lib.object import removeLinkFromObject

                    removeLinkFromObject(obj, prop, self.name)
                    return

                for sensor in linked_sensors:
                    device = sensor.device
                    self.logger.debug(device)
                    if device.name in self.api_clients:
                        # Control the ESPHome entity based on sensor type
                        _sensor = row2dict(sensor)
                        _sensor["device"] = device.name
                        links = json.loads(sensor.links)
                        state = 'state'
                        for key, link in links.items():
                            if link == obj + "." + prop:
                                state = key
                        self._control_linked_sensor(_sensor, state, val)
        except Exception as e:
            self.logger.error(f"Error in changeLinkedProperty: {e}")

    def _control_linked_sensor(self, sensor, state, value):
        """Control ESPHome sensor based on linked property change"""
        try:
            device_name = sensor["device"]
            client = self.api_clients[device_name]

            if not client.is_connected():
                self.logger.warning(f"Device {device_name} not connected")
                return

            entity_key = int(sensor["entity_key"])

            if sensor["entity_type"] == "switch":
                # Convert value to boolean
                state = self._convert_to_boolean(value)
                success = client.set_switch_state(entity_key, state)
            elif sensor['entity_type'] == 'number':
                success = client.set_number_state(entity_key, value)
            elif sensor['entity_type'] in ['text', 'textsensor']:
                success = client.set_text_state(entity_key, str(value))
            elif sensor['entity_type'] == 'light':
                if state != 'state':
                    links = json.loads(sensor['links'])
                    # Complex light control
                    state = None
                    brightness = None
                    rgb = None
                    if 'state' in links:
                        state = self._convert_to_boolean(getProperty(links['state']))
                    if 'state' in links:
                        brightness = getProperty(links['brightness']) / 100
                    if 'rgb' in links:
                        hex_rgb = getProperty(links['rgb'])
                        from plugins.ESPHome.utils import hex_to_rgb_float
                        rgb = hex_to_rgb_float(hex_rgb)
                    success = client.set_light_state(entity_key, state, brightness, rgb)
                else:
                    # Simple on/off control
                    state = self._convert_to_boolean(value)
                    success = client.set_light_state(entity_key, state)

            elif sensor["entity_type"] == "cover":
                # Cover control
                if str(value).lower() in ["open", "1", "true"]:
                    success = client.cover_command(entity_key, "OPEN")
                elif str(value).lower() in ["close", "0", "false"]:
                    success = client.cover_command(entity_key, "CLOSE")
                else:
                    success = client.cover_command(entity_key, "STOP")

            else:
                self.logger.warning(
                    f"Unsupported entity type for control: {sensor['entity_type']}"
                )
                return

            if success:
                self.logger.info(
                    f"Successfully controlled {device_name} sensor {sensor['name']}({state}): {value}"
                )
            else:
                self.logger.error(
                    f"Failed to control {device_name} sensor {sensor['name']}({state}): {value}"
                )

        except Exception as e:
            self.logger.error(f"Error controlling linked sensor {sensor['name']}({state}): {e}")

    def _convert_to_boolean(self, value):
        """Convert various value types to boolean"""
        if isinstance(value, bool):
            return value
        elif isinstance(value, (int, float)):
            return bool(value)
        elif isinstance(value, str):
            return value.lower() in ["true", "1", "on", "yes"]
        else:
            return bool(value)
