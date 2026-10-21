"""Config apply — the use case that reconciles a declared document against a tenant's stored records. It sits above
``config``, ``projects`` and ``garden``, the three packages whose records one apply writes, because none of them may
import the others (``bzh:config-apply``)."""
