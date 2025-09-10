import json
import datetime
from flask import request, jsonify
from flask_restx import Namespace, Resource
from sqlalchemy import delete
from app.api.decorators import api_key_required
from app.authentication.handlers import handle_admin_required
from app.api.models import model_404, model_result
from plugins.ESPHome.models import ESPHomeDevice, ESPHomeSensor
from app.database import row2dict, session_scope
from plugins.ESPHome import ESPHome
from app.core.lib.object import setLinkToObject, removeLinkFromObject

_api_ns = Namespace(name="ESPHome", description="ESPHome namespace", validate=True)

response_result = _api_ns.model("Result", model_result)
response_404 = _api_ns.model("Error", model_404)


_instance: ESPHome = None


def create_api_ns(instance:ESPHome):
    global _instance
    _instance = instance
    return _api_ns

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
                device_data = {
                    "id": device.id,
                    "name": device.name,
                    "host": device.host,
                    "port": device.port,
                    "connected": device.name in _instance.api_clients,
                    "last_seen": (
                        device.last_seen.isoformat() if device.last_seen else None
                    ),
                    "firmware_version": device.firmware_version,
                    "sensors": [
                        {
                            "id": sensor.id,
                            "name": sensor.name,
                            "type": sensor.entity_type,
                            "class": sensor.device_class,
                            "state": sensor.state,
                            "unit": sensor.unit_of_measurement,
                            "key": sensor.entity_key,
                            "linked_object": sensor.linked_object,
                            "linked_property": sensor.linked_property,
                            "linked_method": sensor.linked_method
                        }
                        for sensor in device.sensors
                    ],
                }
                result.append(device_data)

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
                    'sensors': device.sensors
                }

                return _instance.render('sensor_editor.html', content)

        except Exception as e:
            _instance.logger.error(f"Error loading sensor editor: {e}")
            return "Error loading sensor editor", 500

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
                if data.get("id",None):
                    device = session.query(ESPHomeDevice).where(ESPHomeDevice.id == data['id']).one_or_none()
                else:
                    device = ESPHomeDevice()
                    session.add(device)

                device.name = data['name']
                device.host = data['host']
                device.port = data['port']
                device.password = data.get('password', None)
                if data['sensors']:
                    for sensor in data['sensors']:
                        sensor_obj = session.query(ESPHomeSensor).where(ESPHomeSensor.id == sensor['id']).one_or_none()
                        if sensor_obj:
                            if sensor_obj.linked_object:
                                removeLinkFromObject(sensor_obj.linked_object, sensor_obj.linked_property, _instance.name)
                            sensor_obj.linked_object = sensor['linked_object']
                            sensor_obj.linked_property = sensor['linked_property']
                            sensor_obj.linked_method = sensor['linked_method']
                            if sensor_obj.linked_object:
                                setLinkToObject(sensor_obj.linked_object, sensor_obj.linked_property, _instance.name)
                        
                session.commit()

            return jsonify({'status': 'success'})

        except Exception as e:
            _instance.logger.error(f"Error adding device: {e}")
            return jsonify({'status': 'error', 'message': str(e)})

    @api_key_required
    @handle_admin_required
    def delete(self):
        id = request.args.get("id")
        """ Delete device """
        with session_scope() as session:
            sql = delete(ESPHomeDevice).where(ESPHomeDevice.id == id)
            session.execute(sql)
            session.commit()
            return {"success": True}, 200
