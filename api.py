import json
from flask import request, jsonify
from flask_restx import Namespace, Resource
from sqlalchemy import delete
from app.api.decorators import api_key_required
from app.authentication.handlers import handle_admin_required
from app.api.models import model_404, model_result
from plugins.ESPHome.models import ESPHomeDevice, ESPHomeSensor
from app.database import session_scope
from plugins.ESPHome import ESPHome
from app.core.lib.object import setLinkToObject, removeLinkFromObject
from app.core.main.ObjectsStorage import objects_storage

_api_ns = Namespace(name="ESPHome", description="ESPHome namespace", validate=True)

response_result = _api_ns.model("Result", model_result)
response_404 = _api_ns.model("Error", model_404)


_instance: ESPHome = None


def create_api_ns(instance:ESPHome):
    global _instance
    _instance = instance
    return _api_ns

def _is_method_link(link: str) -> bool:
    try:
        if not link:
            return False
        if '.' in link and link.count('.') == 1:
            obj_name, member = link.split('.')
            obj = objects_storage.getObjectByName(obj_name)
            return bool(obj and member in getattr(obj, 'methods', {}))
        return False
    except Exception:
        return False

@_api_ns.route("/devices", endpoint="esphome_devices")
class GetESPHomeDevices(Resource):
    @api_key_required
    @handle_admin_required
    @_api_ns.doc(security="apikey")
    @_api_ns.response(200, "List devices", response_result)
    def get(self):
        with session_scope() as session:
            devices = session.query(ESPHomeDevice).all()
            result = []
            for device in devices:
                connected = False
                if device.name in _instance.api_clients:
                    connected = _instance.api_clients[device.name].is_connected()
                sensors_list = [
                    {
                        "id": sensor.id,
                        "name": sensor.name,
                        "type": sensor.entity_type,
                        "class": sensor.device_class,
                        "state": json.loads(sensor.state) if sensor.state else {},
                        "unit": sensor.unit_of_measurement,
                        "icon": sensor.icon,
                        "key": sensor.entity_key,
                        "accuracy_decimals": sensor.accuracy_decimals,
                        "links": json.loads(sensor.links) if sensor.links else {},
                    }
                    for sensor in device.sensors
                ]
                sensors_list.sort(key=lambda s: s['name'].lower())

                device_data = {
                    "id": device.id,
                    "name": device.name,
                    "host": device.host,
                    "port": device.port,
                    "client_info": device.client_info,
                    "connected": connected,
                    "enabled": device.enabled if device.enabled is not None else True,
                    "last_seen": (
                        device.last_seen.isoformat() if device.last_seen else None
                    ),
                    "firmware_version": device.firmware_version,
                    "sensors": sensors_list,
                }
                result.append(device_data)
            result.sort(key=lambda x: x['name'].lower()) 
            return jsonify(result)

@_api_ns.route("/device/<int:device_id>/sensors", endpoint="esphome_sensors")
class GetESPHomeSensors(Resource):
    @api_key_required
    @handle_admin_required
    @_api_ns.doc(security="apikey")
    @_api_ns.response(200, "List sensors", response_result)
    def get(self,device_id):
        """Render sensor editor interface"""
        try:
            with session_scope() as session:
                device = session.query(ESPHomeDevice).get(device_id)
                if not device:
                    return "Device not found", 404

                content = {
                    'device': device,
                    'sensors': device.sensors,
                }

                return _instance.render('sensor_editor.html', content)

        except Exception as e:
            _instance.logger.error(f"Error loading sensor editor: {e}")
            return "Error loading sensor editor", 500
        
@_api_ns.route("/reconnect/<int:device_id>", endpoint="esphome_reconnect")
class ReconnectESPHomeDevice(Resource):
    @api_key_required
    @handle_admin_required
    @_api_ns.doc(security="apikey")
    def get(self,device_id):
        try:
            with session_scope() as session:
                device = session.query(ESPHomeDevice).get(device_id)
                if not device:
                    return "Device not found", 404

                _instance.update_connections(device)

                return jsonify({'status': 'success'})

        except Exception as e:
            _instance.logger.error(f"Error reconnect device: {e}")
            return "Error reconnect device", 500

@_api_ns.route("/device", endpoint="esphome_device")
class AddESPHomeDevice(Resource):
    @api_key_required
    @handle_admin_required
    @_api_ns.doc(security="apikey")
    def post(self):
        data = request.get_json()

        if not data['host'] or not data['name']:
            return jsonify({'status': 'error', 'message': 'Host and name are required'})

        try:
            with session_scope() as session:
                if_new = False
                if_update = False
                if data.get("id",None):
                    device = session.query(ESPHomeDevice).where(ESPHomeDevice.id == data['id']).one_or_none()
                else:
                    device = ESPHomeDevice()
                    device.name = data['name']
                    device.host = data['host']
                    device.port = data['port']
                    device.client_info = data['client_info']
                    session.add(device)
                    session.commit()
                    if_new = True

                if device.host != data['host'] or device.port != data['port']:
                    if_update = True
                if device.name != data['name']:
                    _instance.remove_device(device.name)
                    if_new = True
                
                # Проверяем изменение флага enabled
                enabled_changed = device.enabled != data.get('enabled', True)
                
                device.name = data['name']
                device.host = data['host']
                device.port = data['port']
                device.password = data.get('password', None)
                device.client_info = data.get('client_info', None)
                device.enabled = data.get('enabled', True)
                if data['sensors']:
                    for sensor in data['sensors']:
                        sensor_obj = session.query(ESPHomeSensor).where(ESPHomeSensor.id == sensor['id']).one_or_none()
                        if sensor_obj:
                            if sensor_obj.links:
                                links = json.loads(sensor_obj.links)
                                for _, link in links.items():
                                    if link and not _is_method_link(link):
                                        op = link.split('.')
                                        removeLinkFromObject(op[0], op[1], _instance.name)
                            links = sensor['links']
                            sensor_obj.links = json.dumps(links)
                            for _, link in links.items():
                                if link and not _is_method_link(link):
                                    op = link.split('.')
                                    setLinkToObject(op[0], op[1], _instance.name)
                session.commit()
                
                # Обновляем объект из сессии для получения актуальных данных
                session.refresh(device)

                # Обновляем подключение если изменился флаг enabled или параметры подключения
                if if_new:
                    _instance.connect_device(device)
                elif if_update or enabled_changed:
                    _instance.update_connections(device)

            return jsonify({'status': 'success'})

        except Exception as e:
            _instance.logger.exception(f"Error adding device: {e}")
            return jsonify({'status': 'error', 'message': str(e)})

    @api_key_required
    @handle_admin_required
    def delete(self):
        id = request.args.get("id")
        """ Delete device """
        with session_scope() as session:
            device = session.query(ESPHomeDevice).where(ESPHomeDevice.id == id).one_or_none()
            name = device.name
            sql = delete(ESPHomeSensor).where(ESPHomeSensor.device_id == id)
            session.execute(sql)
            sql = delete(ESPHomeDevice).where(ESPHomeDevice.id == id)
            session.execute(sql)
            session.commit()
            _instance.remove_device(name)
            return {"success": True}, 200
