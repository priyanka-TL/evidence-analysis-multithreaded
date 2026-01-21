"""
Base repository class providing common database operations.

This class implements the Repository pattern and provides:
- Common CRUD operations
- Database connection management
- Error handling
- Logging
"""

import logging
from typing import Dict, List, Optional, Any
from database.connection import db

logger = logging.getLogger(__name__)


class BaseRepository:
    """Base class for all repository classes."""
    
    def __init__(self, table_name: str):
        """
        Initialize the base repository.
        
        Args:
            table_name: Name of the database table this repository manages
        """
        self.table_name = table_name
        self.db = db
    
    def find_by_id(self, record_id: int) -> Optional[Dict[str, Any]]:
        """
        Find a record by its ID.
        
        Args:
            record_id: The ID of the record to find
        
        Returns:
            Dictionary with record data, or None if not found
        """
        query = f"SELECT * FROM {self.table_name} WHERE id = %s"
        try:
            return self.db.execute_query(query, (record_id,), fetch_one=True)
        except Exception as e:
            logger.error(f"Error finding record by ID in {self.table_name}: {e}")
            raise
    
    def find_all(self, limit: Optional[int] = None, offset: int = 0) -> List[Dict[str, Any]]:
        """
        Find all records in the table.
        
        Args:
            limit: Maximum number of records to return
            offset: Number of records to skip
        
        Returns:
            List of dictionaries with record data
        """
        query = f"SELECT * FROM {self.table_name}"
        params = []
        
        if limit:
            query += " LIMIT %s OFFSET %s"
            params = [limit, offset]
        
        try:
            return self.db.execute_query(query, tuple(params) if params else None)
        except Exception as e:
            logger.error(f"Error finding all records in {self.table_name}: {e}")
            raise
    
    def find_by_field(self, field_name: str, field_value: Any, 
                      fetch_one: bool = False) -> Optional[Dict[str, Any]] | List[Dict[str, Any]]:
        """
        Find records by a specific field value.
        
        Args:
            field_name: Name of the field to search
            field_value: Value to search for
            fetch_one: If True, return only the first match
        
        Returns:
            Single record (dict) if fetch_one=True, otherwise list of records
        """
        query = f"SELECT * FROM {self.table_name} WHERE {field_name} = %s"
        try:
            return self.db.execute_query(query, (field_value,), fetch_one=fetch_one)
        except Exception as e:
            logger.error(f"Error finding by field {field_name} in {self.table_name}: {e}")
            raise
    
    def find_active(self, additional_filters: Optional[Dict[str, Any]] = None) -> List[Dict[str, Any]]:
        """
        Find all active records (where is_active = TRUE).
        
        Args:
            additional_filters: Additional WHERE conditions as dict
        
        Returns:
            List of active records
        """
        query = f"SELECT * FROM {self.table_name} WHERE is_active = TRUE"
        params = []
        
        if additional_filters:
            for field, value in additional_filters.items():
                query += f" AND {field} = %s"
                params.append(value)
        
        try:
            return self.db.execute_query(query, tuple(params) if params else None)
        except Exception as e:
            logger.error(f"Error finding active records in {self.table_name}: {e}")
            raise
    
    def insert(self, data: Dict[str, Any], return_id: bool = True) -> Optional[int]:
        """
        Insert a new record.
        
        Args:
            data: Dictionary of column names and values to insert
            return_id: If True, returns the ID of the inserted record
        
        Returns:
            ID of inserted record if return_id=True, otherwise None
        """
        columns = ', '.join(data.keys())
        placeholders = ', '.join(['%s'] * len(data))
        query = f"INSERT INTO {self.table_name} ({columns}) VALUES ({placeholders})"
        
        try:
            result = self.db.execute_insert(query, tuple(data.values()), return_id=return_id)
            logger.info(f"Inserted record into {self.table_name}: {result}")
            return result
        except Exception as e:
            logger.error(f"Error inserting into {self.table_name}: {e}")
            raise
    
    def update(self, record_id: int, data: Dict[str, Any]) -> int:
        """
        Update an existing record.
        
        Args:
            record_id: ID of the record to update
            data: Dictionary of column names and new values
        
        Returns:
            Number of rows updated
        """
        set_clause = ', '.join([f"{key} = %s" for key in data.keys()])
        query = f"UPDATE {self.table_name} SET {set_clause} WHERE id = %s"
        params = tuple(list(data.values()) + [record_id])
        
        try:
            rows_affected = self.db.execute_update(query, params)
            logger.info(f"Updated {rows_affected} row(s) in {self.table_name}")
            return rows_affected
        except Exception as e:
            logger.error(f"Error updating {self.table_name}: {e}")
            raise
    
    def delete(self, record_id: int) -> int:
        """
        Delete a record by ID.
        
        Args:
            record_id: ID of the record to delete
        
        Returns:
            Number of rows deleted
        """
        query = f"DELETE FROM {self.table_name} WHERE id = %s"
        try:
            rows_affected = self.db.execute_delete(query, (record_id,))
            logger.info(f"Deleted {rows_affected} row(s) from {self.table_name}")
            return rows_affected
        except Exception as e:
            logger.error(f"Error deleting from {self.table_name}: {e}")
            raise
    
    def soft_delete(self, record_id: int) -> int:
        """
        Soft delete a record (set is_active to FALSE).
        
        Args:
            record_id: ID of the record to soft delete
        
        Returns:
            Number of rows updated
        """
        return self.update(record_id, {'is_active': False})
    
    def count(self, filters: Optional[Dict[str, Any]] = None) -> int:
        """
        Count records with optional filters.
        
        Args:
            filters: Dictionary of field names and values to filter by
        
        Returns:
            Number of matching records
        """
        query = f"SELECT COUNT(*) as count FROM {self.table_name}"
        params = []
        
        if filters:
            where_clauses = []
            for field, value in filters.items():
                where_clauses.append(f"{field} = %s")
                params.append(value)
            query += " WHERE " + " AND ".join(where_clauses)
        
        try:
            result = self.db.execute_query(query, tuple(params) if params else None, fetch_one=True)
            return result['count'] if result else 0
        except Exception as e:
            logger.error(f"Error counting records in {self.table_name}: {e}")
            raise
    
    def exists(self, field_name: str, field_value: Any) -> bool:
        """
        Check if a record exists with the given field value.
        
        Args:
            field_name: Name of the field to check
            field_value: Value to check for
        
        Returns:
            True if record exists, False otherwise
        """
        query = f"SELECT EXISTS(SELECT 1 FROM {self.table_name} WHERE {field_name} = %s) as exists"
        try:
            result = self.db.execute_query(query, (field_value,), fetch_one=True)
            return result['exists'] if result else False
        except Exception as e:
            logger.error(f"Error checking existence in {self.table_name}: {e}")
            raise
