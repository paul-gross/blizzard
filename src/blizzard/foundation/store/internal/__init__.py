"""Package-private store adapters — concrete SQLAlchemy implementations of the
foundation store seams. Nothing outside ``blizzard.foundation.store`` imports from
here (``bzh:dependency-inversion``): callers depend on the Protocols one level up.
"""
