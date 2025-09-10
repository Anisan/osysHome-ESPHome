import socket
import time
from typing import List, Dict


class ESPHomeDiscovery:
    def __init__(self, logger):
        self.logger = logger

    def discover_devices(self, timeout: int = 5) -> List[Dict]:
        """Discover ESPHome devices on network using mDNS"""
        devices = []

        try:
            # Try mDNS discovery first
            mdns_devices = self._mdns_discovery(timeout)
            devices.extend(mdns_devices)

        except Exception as e:
            self.logger.error(f"Error during discovery: {e}")

        return devices

    def _mdns_discovery(self, timeout: int) -> List[Dict]:
        """Discover devices using mDNS"""
        devices = []

        try:
            from zeroconf import ServiceBrowser, Zeroconf, ServiceListener

            class ESPHomeListener(ServiceListener):
                def __init__(self):
                    self.devices = []

                def add_service(self, zc: Zeroconf, type_: str, name: str) -> None:
                    info = zc.get_service_info(type_, name)
                    if info:
                        device = {
                            "name": name.replace("._esphomelib._tcp.local.", ""),
                            "host": socket.inet_ntoa(info.addresses[0]),
                            "port": info.port,
                        }
                        # Добавляем TXT записи
                        for key, value in info.properties.items():
                            device[key.decode()] = value.decode()
                        self.devices.append(device)

            zeroconf = Zeroconf()
            listener = ESPHomeListener()
            browser = ServiceBrowser(zeroconf, "_esphomelib._tcp.local.", listener)  # noqa

            time.sleep(timeout)
            zeroconf.close()

            devices = listener.devices

        except ImportError:
            self.logger.warning("zeroconf not available, skipping mDNS discovery")
        except Exception as e:
            self.logger.error(f"mDNS discovery error: {e}")

        return devices
