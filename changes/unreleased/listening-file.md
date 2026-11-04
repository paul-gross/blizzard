---
kind: added
surfaces: [cli]
---

`blizzard hub host` and `blizzard runner host` take `--listening-file PATH`: once the daemon is serving, it atomically
writes its URL (`http://<host>:<port>`, the port actually bound) to `PATH`. With `--port 0` the kernel picks a free
port, and the runner's local API URL, the serving line, and the listening file all report the port bound.
