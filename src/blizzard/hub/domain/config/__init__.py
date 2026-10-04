"""Configured records and the change log — the hub's configuration held as data.

Every committed write to a configured record is made by :class:`ConfigAuthoring` and
appends one :class:`ConfigChange` in the record's own transaction."""
