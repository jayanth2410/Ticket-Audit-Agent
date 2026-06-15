"""
Database models for Incident Management System
Uses SQLAlchemy ORM for database interactions
"""

from datetime import datetime
from sqlalchemy import (
    Column, String, Integer, Text, DateTime, 
    Boolean, ForeignKey, create_engine, JSON
)
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import relationship, sessionmaker
from sqlalchemy.pool import StaticPool

Base = declarative_base()


class Incident(Base):
    """Main Incident model representing a ServiceNow incident"""
    __tablename__ = "incidents"

    # Primary Key
    id = Column(Integer, primary_key=True, autoincrement=True)
    sys_id = Column(String(255), unique=True, nullable=False, index=True)

    # Basic Incident Information
    number = Column(String(50), unique=True, nullable=False, index=True)
    task_effective_number = Column(String(50))
    sys_class_name = Column(String(100))

    # Descriptions
    short_description = Column(String(500))
    description = Column(Text)
    comments = Column(Text)
    comments_and_work_notes = Column(Text)
    close_notes = Column(Text)
    work_notes = Column(Text)

    # State & Status
    state = Column(String(50))
    incident_state = Column(String(50))
    active = Column(Boolean, default=True)
    made_sla = Column(Boolean)
    knowledge = Column(Boolean, default=False)

    # Priority and Impact
    priority = Column(String(50))
    urgency = Column(String(50))
    severity = Column(String(50))
    impact = Column(String(50))
    category = Column(String(100))
    subcategory = Column(String(100))

    # Resolution Information
    close_code = Column(String(100))
    escalation = Column(String(50))

    # Assignment
    assigned_to = Column(String(255))
    assignment_group = Column(String(255))
    additional_assignee_list = Column(Text)
    resolved_by = Column(String(255))
    closed_by = Column(String(255))

    # Caller & Contact
    caller_id = Column(String(255))
    contact_type = Column(String(50))
    company = Column(String(255))
    location = Column(String(255))

    # Business Information
    business_service = Column(String(255))
    business_impact = Column(String(255))
    cmdb_ci = Column(String(255))
    service_offering = Column(String(255))
    contract = Column(String(255))
    business_duration = Column(String(100))
    calendar_duration = Column(String(100))
    time_worked = Column(String(100))

    # Dates and Times
    sys_created_on = Column(DateTime, nullable=False)
    sys_updated_on = Column(DateTime)
    opened_at = Column(DateTime)
    opened_by = Column(String(255))
    closed_at = Column(DateTime)
    resolved_at = Column(DateTime)
    activity_due = Column(DateTime)
    due_date = Column(DateTime)
    expected_start = Column(DateTime)
    work_start = Column(DateTime)
    work_end = Column(DateTime)
    reopened_time = Column(DateTime)

    # SLA Information
    sla_due = Column(String(100))
    calendar_stc = Column(String(50))
    business_stc = Column(String(50))

    # Counters & Metadata
    sys_mod_count = Column(Integer)
    reassignment_count = Column(Integer)
    reopen_count = Column(Integer)
    
    # System Fields
    sys_domain = Column(String(100))
    sys_domain_path = Column(String(255))
    sys_created_by = Column(String(100))
    sys_updated_by = Column(String(100))
    notify = Column(String(100))

    # Additional Fields
    parent = Column(String(255))
    parent_incident = Column(String(255))
    child_incidents = Column(Text)
    correlation_id = Column(String(255))
    correlation_display = Column(String(255))
    approval = Column(String(100))
    approval_set = Column(Text)
    approval_history = Column(Text)
    upon_approval = Column(Text)
    upon_reject = Column(Text)
    follow_up = Column(String(255))
    cause = Column(Text)
    route_reason = Column(String(255))
    hold_reason = Column(String(255))
    watch_list = Column(Text)
    user_input = Column(Text)
    group_list = Column(Text)
    delivery_plan = Column(String(255))
    delivery_task = Column(String(255))
    universal_request = Column(String(255))
    order = Column(String(255))
    origin_table = Column(String(100))
    origin_id = Column(String(255))
    u_tcs_resolver_group = Column(String(255))
    sys_tags = Column(Text)

    # JSON fields for complex data
    sla_data = Column(JSON)  # Stores response_sla_breached, resolution_sla_breached

    # Relationships
    audit_history = relationship(
        "AuditHistory",
        back_populates="incident",
        cascade="all, delete-orphan",
        lazy="joined"
    )

    # Timestamps for tracking in database
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    def __repr__(self):
        return f"<Incident(number='{self.number}', sys_id='{self.sys_id}', state='{self.state}')>"


class AuditHistory(Base):
    """Model for tracking changes to incident fields"""
    __tablename__ = "audit_history"

    # Primary Key
    id = Column(Integer, primary_key=True, autoincrement=True)
    
    # Foreign Key
    incident_id = Column(Integer, ForeignKey("incidents.id"), nullable=False, index=True)
    
    # Audit Information
    fieldname = Column(String(255), nullable=False)
    oldvalue = Column(Text)
    newvalue = Column(Text)
    
    # Audit Metadata
    sys_created_by = Column(String(100), nullable=False)
    sys_created_on = Column(DateTime, nullable=False)
    
    # Relationship
    incident = relationship("Incident", back_populates="audit_history")
    
    # Timestamps for tracking in database
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)

    def __repr__(self):
        return f"<AuditHistory(fieldname='{self.fieldname}', oldvalue='{self.oldvalue}', newvalue='{self.newvalue}')>"


class DatabaseSession:
    """Database session manager for managing connections and transactions"""
    
    def __init__(self, database_url):
        """
        Initialize database session manager
        
        Args:
            database_url (str): Database connection URL
        """
        self.database_url = database_url
        self.engine = None
        self.SessionLocal = None
    
    def initialize(self):
        """Initialize database engine and session factory"""
        self.engine = create_engine(
            self.database_url,
            connect_args={"check_same_thread": False} if "sqlite" in self.database_url else {},
            poolclass=StaticPool if "sqlite" in self.database_url else None,
            echo=False  # Set to True for SQL query logging
        )
        self.SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=self.engine)
        
        # Create all tables
        Base.metadata.create_all(bind=self.engine)
        print(f"Database initialized at: {self.database_url}")
    
    def get_session(self):
        """Get a new database session"""
        if self.SessionLocal is None:
            self.initialize()
        return self.SessionLocal()
    
    def close(self):
        """Close database connection"""
        if self.engine:
            self.engine.dispose()
            print("Database connection closed")
