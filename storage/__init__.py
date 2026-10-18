"""SQL persistence shared by HTTP requests, authentication and workers."""

from .database import Database, database_for

__all__ = ['Database', 'database_for']
