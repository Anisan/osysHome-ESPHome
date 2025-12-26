import asyncio
import threading
import json
import math
from datetime import date, datetime
from flask import redirect
from app.core.main.BasePlugin import BasePlugin
from app.database import session_scope, row2dict
from plugins.ESPHome.models import ESPHomeDevice, ESPHomeSensor
from plugins.ESPHome.discovery import ESPHomeDiscovery
from plugins.ESPHome.api_client import ESPHomeAPIClient
from app.core.lib.object import getProperty, updateProperty, callMethodThread
from app.core.main.ObjectsStorage import objects_storage
from app.api import api
from app.core.lib.converters import hex_to_rgb_float, convert_to_boolean

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
            # Передаем ID устройства для получения актуальных данных
            device_id = device.id
            asyncio.run_coroutine_threadsafe(self.async_update_connections(device_id), self.loop)
    
    async def async_update_connections(self, device_id):
        """Update device connections"""
        # Получаем актуальные данные устройства из базы
        with session_scope() as session:
            device = session.query(ESPHomeDevice).get(device_id)
            if not device:
                self.logger.error(f"Device with id {device_id} not found")
                return
            
            device_name = device.name
            device_enabled = device.enabled
            
            # Отключаем устройство если оно подключено
            if device_name in self.api_clients:
                client = self.api_clients[device_name]
                await client.disconnect()
                del self.api_clients[device_name]
                
                # Отправляем обновление статуса через WebSocket
                self.sendDataToWebsocket('device_update', {
                    'device': device_name,
                    'state': False,
                })
            
            # Подключаем только если устройство включено
            if device_enabled:
                await self.async_connect_device(device)
            else:
                self.logger.info(f"Device {device_name} is disabled, skipping connection")
                # Убеждаемся, что статус отключен отправлен
                self.sendDataToWebsocket('device_update', {
                    'device': device_name,
                    'state': False,
                })

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
        # Проверяем, что устройство включено
        if not device.enabled:
            self.logger.info(f"Device {device.name} is disabled, skipping connection")
            return
        
        try:
            client = ESPHomeAPIClient(
                name=device.name,
                host=device.host,
                port=device.port,
                password=device.password,
                logger=self.logger,
                client_info=device.client_info if device.client_info else 'osysHome',
            )

            dev = row2dict(device)
            client.set_connected_callback(lambda: self.on_connected(dev))
            client.set_state_callback(lambda state: self.on_state_change(dev, state))
            client.set_ha_subscribe_callback(lambda entity_id, attribute: self.on_ha_subscribe_callback(dev, entity_id, attribute))
            client.set_service_callback(lambda service: self.on_service_callback(dev, service))

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
                from app.core.lib.converters import rgb_float_to_hex
                result['rgb'] = rgb_float_to_hex(state.red, state.green, state.blue)
        else:
            result = state.to_dict()
            del result['key']
            del result['device_id']
            if 'missing_state' in result:
                del result['missing_state']
        if 'state' in result:
            if isinstance(result['state'], float) and math.isnan(result['state']):
                result['state'] = None
        return result
    
    def on_ha_subscribe_callback(self, device, entity_id, attribute):
        self.logger.debug(f"{device['name']} subs {entity_id} {attribute}")
        try:
            with session_scope() as session:
                existing = session.query(ESPHomeSensor).where(
                    ESPHomeSensor.device_id == device["id"],
                    ESPHomeSensor.unique_id == entity_id
                ).one_or_none()

                if attribute == '':
                    attribute = 'state'

                if not existing:
                    links = {}
                    links[attribute] = ''
                    sensor = ESPHomeSensor(
                        device_id=device["id"],
                        entity_key=entity_id,
                        unique_id=entity_id,
                        name=entity_id,
                        entity_type="homeassistant",
                        links=json.dumps(links),
                        discovered_at=datetime.utcnow()
                    )
                    session.add(sensor)
                else:
                    links = json.loads(existing.links)
                    if attribute not in links:
                        links[attribute] = ''
                        existing.links = json.dumps(links)
                    else:
                        if links[attribute] != '':
                            # send data
                            device_name = device["name"]
                            client = self.api_clients[device_name]
                            state = self._read_link_value(links[attribute])
                            if client.is_connected():
                                attr = attribute
                                if attribute == 'state':
                                    attr = None
                                client.send_home_assistant_state(entity_id, attr, str(state))

                session.commit()

        except Exception as e:
            self.logger.error(f"Error discovering sensors for {device.name}: {e}")

    def on_service_callback(self, device, service: any):
        """Handle Home Assistant service subscriptions"""
        self.logger.debug(f"{device['name']} service {service.to_dict()}")
        try:
            # Обработка вызовов сервисов Home Assistant
            service_dict = service.to_dict()
            service_name = service_dict.get('service', '')
            service_data = service_dict.get('data', {})
            service_variables = service_dict.get('variables', {})
            service_data_template = service_dict.get('data_template', {})  # noqa: F841
            
            self.logger.info(f"Service {service_name} called on {device['name']} with data: {service_data}")
            
            # Получаем entity_id из данных сервиса
            entity_id = service_data.get('entity_id') if isinstance(service_data, dict) else None

            # Формируем единый словарь параметров для обработки
            unified_params = {}
            try:
                filtered_data = {}
                if isinstance(service_data, dict):
                    filtered_data = {k: v for k, v in service_data.items() if k != 'entity_id'}

                if filtered_data:
                    unified_params = filtered_data
                elif isinstance(service_data_template, dict) and len(service_data_template) > 0:
                    # Берем ключи из data_template, значения из variables (если нет соответствия — весь variables)
                    for k in service_data_template.keys():
                        if isinstance(service_variables, dict) and k in service_variables:
                            unified_params[k] = service_variables.get(k)
                        else:
                            unified_params[k] = service_variables if service_variables else None
                else:
                    # Нет параметров — используем действие из service
                    service_action = service_name
                    unified_params[service_action] = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
            except Exception as ex:
                self.logger.error(f"Failed build unified_params for service {service_name}: {ex}")
                unified_params = {}
            
            if not entity_id:
                self.logger.warning(f"No entity_id found in service data for {device['name']}")
                return
            
            # Находим сенсор по entity_id
            with session_scope() as session:
                sensor = session.query(ESPHomeSensor).filter(
                    ESPHomeSensor.device_id == device['id'],
                    ESPHomeSensor.unique_id == entity_id
                ).first()
                
                # Если сенсор не найден, создаем его
                if not sensor:
                    self.logger.info(f"Creating new sensor for entity_id {entity_id} on device {device['name']}")
                    links = {}

                    # Добавляем все ключи из unified_params в links
                    for param_name in unified_params.keys():
                        links[param_name] = ''
                    
                    sensor = ESPHomeSensor(
                        device_id=device['id'],
                        entity_key=entity_id,
                        unique_id=entity_id,
                        name=entity_id,
                        entity_type="homeassistant",
                        links=json.dumps(links),
                        discovered_at=datetime.utcnow()
                    )
                    session.add(sensor)
                    session.commit()
                    self.logger.info(f"Created new sensor {entity_id} for device {device['name']}")
                
                # Сохраняем текущее состояние как словарь параметров сервиса (с мержем, без перетирания отсутствующих ключей)
                merged_state = {}
                try:
                    if sensor.state:
                        try:
                            existing_state = json.loads(sensor.state)
                            if isinstance(existing_state, dict):
                                merged_state = dict(existing_state)
                        except Exception:
                            pass
                    # Обновляем/добавляем только ключи из unified_params
                    for k, v in unified_params.items():
                        merged_state[k] = v
                    sensor.state = json.dumps(merged_state)
                    sensor.last_updated = datetime.utcnow()
                    session.commit()
                except Exception as ex:
                    self.logger.error(f"Failed to save service state for {sensor.name}: {ex}")

                # Получаем связи сенсора
                links = {}
                if sensor.links:
                    links = json.loads(sensor.links)
                
                # Обновляем links, добавляя новые параметры, которых еще нет
                links_updated = False
                for param_name in unified_params.keys():
                    if param_name not in links:
                        links[param_name] = ''
                        links_updated = True
                
                # Если были добавлены новые атрибуты, сохраняем обновленные links
                if links_updated:
                    sensor.links = json.dumps(links)
                    session.commit()
                
                # Обрабатываем каждый параметр из unified_params
                processed_attributes = []

                for param_name, param_value in unified_params.items():
                    if param_name in links:
                        link = links[param_name]
                        if link:
                            processed_attributes.append(param_name)
                            self._process_service_link(link, param_value, device['name'], service_name, param_name)

                if not processed_attributes:
                    self.logger.debug("No links found for service %s parameters in sensor %s on device %s", service_name, sensor.name, device['name'])
                
                # Отправляем обновление по вебсокету
                try:
                    self.sendDataToWebsocket('sensor_update', {
                        'device': device['name'],
                        'sensor': sensor.name,
                        'state': merged_state if merged_state else unified_params,
                        'key': 'service',
                    })
                except Exception:
                    pass
                
        except Exception as e:
            self.logger.error(f"Error handling HA service callback for {device['name']}: {e}")
    
    def _process_service_link(self, link, value, device_name, service_name, attribute_name):
        """Обрабатывает связь для сервиса: вызывает метод или устанавливает значение свойства"""
        try:
            # Определяем, это метод или свойство
            # Методы имеют формат Object.Method
            if '.' in link and link.count('.') == 1:
                parts = link.split('.')
                obj_name = parts[0]
                method_or_prop = parts[1]
                
                # Проверяем, есть ли метод с таким именем
                obj = objects_storage.getObjectByName(obj_name)
                
                if obj and method_or_prop in obj.methods:
                    # Это метод - вызываем его
                    # Передаем значение как аргумент, если это не True/False (действие без параметров)
                    method_args = {'VALUE': value, 'NEW_VALUE': value, 'service_name': service_name, 'attribute_name': attribute_name}
                    result = callMethodThread(link, method_args, self.name)
                    self.logger.debug(f"Method {link} called on {device_name} for service {service_name} (attribute: {attribute_name}): {result}")
                else:
                    # Это свойство - устанавливаем значение
                    self._update_property_value(link, value, device_name, service_name, attribute_name)
                
        except Exception as e:
            self.logger.error(f"Error processing service link {link} for {device_name}: {e}")
    
    def _update_property_value(self, link, value, device_name, service_name, attribute_name):
        """Обновляет значение свойства из сервиса"""
        try:
            updateProperty(link, value, self.name)
            self.logger.debug(f"Property {link} updated on {device_name} for service {service_name} (attribute: {attribute_name}): {value}")
        except Exception as e:
            self.logger.error(f"Error updating property {link} for {device_name}: {e}")

    def _read_link_value(self, link):
        """Возвращает значение по ссылке: поддерживает как свойства, так и методы без аргументов"""
        try:
            if not link:
                return None
            if '.' in link and link.count('.') == 1:
                obj_name, member = link.split('.')
                obj = objects_storage.getObjectByName(obj_name)
                if obj and member in getattr(obj, 'methods', {}):
                    try:
                        return callMethod(link, {}, self.name)
                    except Exception:
                        return None
            return getProperty(link)
        except Exception:
            return None

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
                            if sensor.accuracy_decimals and value:
                                values[key] = round(value, int(sensor.accuracy_decimals))
                        except Exception as ex:
                            self.logger.exception(ex)

                    str_values = json.dumps(values)
                    sensor.state = str_values
                    sensor.last_updated = datetime.utcnow()

                    links = {} 
                    if sensor.links:
                        links = json.loads(sensor.links)

                    for key, value in values.items():
                        if key in links:
                            link = links[key]
                            if link:
                                # Поддержка ссылок на методы: если link указывает на метод, вызываем его с value
                                try:
                                    if '.' in link and link.count('.') == 1:
                                        obj_name, member = link.split('.')
                                        obj = objects_storage.getObjectByName(obj_name)
                                        if obj and member in getattr(obj, 'methods', {}):
                                            # Используем общий обработчик как для сервисов
                                            self._process_service_link(link, value, device['name'], 'state_update', key)
                                            continue
                                except Exception:
                                    pass
                                # Обычная привязка к свойству
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
                obj_str = str(obj) if obj is not None else ""
                prop_str = str(prop) if prop is not None else ""
                pattern = f"{obj_str}.{prop_str}"
                # Escape SQL wildcards if needed
                escaped_pattern = "%" + pattern.replace('%', '\\%').replace('_', '\\_') + "%"
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

            if sensor["entity_type"] == "homeassistant":
                entity_key = sensor["entity_key"]
                value = str(value)
                attr = state
                if state == 'state':
                    attr = None
                success = client.send_home_assistant_state(entity_key, attr, value)
            elif sensor["entity_type"] == "switch":
                entity_key = int(sensor["entity_key"])
                # Convert value to boolean
                state = convert_to_boolean(value)
                success = client.set_switch_state(entity_key, state)
            elif sensor['entity_type'] == 'number':
                entity_key = int(sensor["entity_key"])
                success = client.set_number_state(entity_key, value)
            elif sensor['entity_type'] in ['text', 'textsensor']:
                entity_key = int(sensor["entity_key"])
                success = client.set_text_state(entity_key, str(value))
            elif sensor['entity_type'] == 'light':
                entity_key = int(sensor["entity_key"])
                if state != 'state':
                    links = json.loads(sensor['links'])
                    # Complex light control
                    state = None
                    brightness = None
                    rgb = None
                    if 'state' in links:
                        state_val = self._read_link_value(links['state'])
                        state = convert_to_boolean(state_val)
                    if 'brightness' in links:
                        brightness_val = self._read_link_value(links['brightness'])
                        if brightness_val is not None:
                            brightness = brightness_val / 100
                    if 'rgb' in links:
                        hex_rgb = self._read_link_value(links['rgb'])
                        rgb = hex_to_rgb_float(hex_rgb)
                    success = client.set_light_state(entity_key, state, brightness, rgb)
                else:
                    # Simple on/off control
                    state = convert_to_boolean(value)
                    success = client.set_light_state(entity_key, state)
            elif sensor["entity_type"] == "cover":
                entity_key = int(sensor["entity_key"])
                # Cover control
                if str(value).lower() in ["open", "1", "true"]:
                    success = client.cover_command(entity_key, position=1.0)
                elif str(value).lower() in ["close", "0", "false"]:
                    success = client.cover_command(entity_key, position=0.0)
                else:
                    success = client.cover_command(entity_key, stop=True)

            else:
                self.logger.warning(f"Unsupported entity type for control: {sensor['entity_type']}")
                return

            if success:
                self.logger.info(f"Successfully controlled {device_name} sensor {sensor['name']}({state}): {value}")
            else:
                self.logger.error(f"Failed to control {device_name} sensor {sensor['name']}({state}): {value}")

        except Exception as e:
            self.logger.error(f"Error controlling linked sensor {sensor['name']}({state}): {e}")

