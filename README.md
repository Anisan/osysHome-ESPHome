# ESPHome - ESPHome Device Integration

![ESPHome Icon](static/ESPHome.png)

Native ESPHome API integration for osysHome with discovery, live state sync, reverse control, and object linking.

## Documentation

- [User Guide](docs/USER_GUIDE.md)
- [Technical Reference](docs/TECHNICAL_REFERENCE.md)
- [Documentation Index](docs/index.md)

## Highlights

- Native ESPHome API client with reconnect logic
- Manual mDNS discovery through `_esphomelib._tcp.local.`
- Device and entity registry in the database
- Bidirectional links between ESPHome entities and osysHome objects
- Property and method targets for sensor attributes
- Home Assistant subscription and service bridging
- Real-time admin updates through WebSocket

## Module Info

| Field | Value |
| --- | --- |
| Version | `1.0` |
| Category | `Devices` |
| Actions | `cycle`, `search`, `widget` |
| Author | `Eraser` |

## License

See the main osysHome project license.
