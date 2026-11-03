---
kind: changed
surfaces: [config]
---

The shipped graphs' delivery nodes poll every 60 seconds rather than 30. The GitHub adapter reads conditionally, so a
landing pass in which nothing moved costs at most one counted call.
