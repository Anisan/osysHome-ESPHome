from sqlalchemy import Column, Integer, String, DateTime, Boolean, Text, ForeignKey
from sqlalchemy.orm import relationship
from app.database import SurrogatePK, db


class ESPHomeDevice(SurrogatePK, db.Model):
    __tablename__ = "esphome_devices"

    name = Column(String(255), nullable=False)
    host = Column(String(255), nullable=False)
    port = Column(Integer, default=6053)
    password = Column(String(255))
    firmware_version = Column(String(100))
    mac_address = Column(String(17))
    discovered_at = Column(DateTime)
    last_seen = Column(DateTime)
    enabled = Column(Boolean, default=True)

    # Relationship to sensors
    sensors = relationship(
        "ESPHomeSensor", back_populates="device", cascade="all, delete-orphan"
    )


class ESPHomeSensor(SurrogatePK, db.Model):
    __tablename__ = "esphome_sensors"

    device_id = Column(Integer, ForeignKey("esphome_devices.id"), nullable=False)
    entity_key = Column(String(255), nullable=False)
    name = Column(String(255), nullable=False)
    entity_type = Column(String(50))  # sensor, binary_sensor, switch, light
    device_class = Column(String(50))
    unit_of_measurement = Column(String(20))
    icon = Column(String(50))
    state = Column(Text)
    accuracy_decimals = Column(Integer)
    linked_object = Column(String(255))
    linked_property = Column(String(255))
    linked_method = Column(String(255))
    last_updated = Column(DateTime)
    discovered_at = Column(DateTime)
    enabled = Column(Boolean, default=True)

    # Relationship to device
    device = relationship("ESPHomeDevice", back_populates="sensors")
