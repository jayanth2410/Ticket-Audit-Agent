"""
Incident Storage Module
========================
Handles storing fetched ServiceNow incidents into the PostgreSQL database.
"""

from datetime import datetime
from typing import List, Dict, Any, Optional
from sqlalchemy.exc import IntegrityError
from db_modal import Incident, AuditHistory, Base
from db_config import DBConfig


class IncidentStorage:
    """Handles storing incidents in the database"""

    def __init__(self, db_config: DBConfig):
        """
        Initialize incident storage
        
        Args:
            db_config (DBConfig): Database configuration instance
        """
        self.db_config = db_config

    def _parse_datetime(self, date_string: str) -> Optional[datetime]:
        """
        Parse ServiceNow datetime strings to Python datetime objects
        
        Args:
            date_string (str): Date string in format "YYYY-MM-DD HH:MM:SS"
        
        Returns:
            datetime or None: Parsed datetime object or None if invalid
        """
        if not date_string or date_string.strip() == "":
            return None
        
        try:
            return datetime.strptime(date_string.strip(), "%Y-%m-%d %H:%M:%S")
        except (ValueError, TypeError):
            return None

    def _parse_boolean(self, value: Any) -> Optional[bool]:
        """
        Convert string booleans to Python booleans
        
        Args:
            value: Value that could be "true", "false", True, False, or None
        
        Returns:
            bool or None: Converted boolean value or None
        """
        if value is None:
            return None
        
        if isinstance(value, bool):
            return value
        
        if isinstance(value, str):
            if value.lower() == "true":
                return True
            elif value.lower() == "false":
                return False
        
        return None

    def _clean_string(self, value: Any) -> Optional[str]:
        """
        Clean string values
        
        Args:
            value: Value to clean
        
        Returns:
            str or None: Cleaned string or None if empty
        """
        if value is None:
            return None
        
        str_value = str(value).strip()
        return str_value if str_value else None

    def store_incidents(self, incidents: List[Dict[str, Any]]) -> Dict[str, Any]:
        """
        Store fetched incidents in the database
        
        Args:
            incidents (List[Dict]): List of incident dictionaries from API
        
        Returns:
            dict: Summary of storage results {
                'success': int,
                'failed': int,
                'total': int,
                'errors': List[str]
            }
        """
        session = self.db_config.get_session()
        results = {
            'success': 0,
            'failed': 0,
            'total': len(incidents),
            'errors': []
        }
        
        try:
            for incident_data in incidents:
                try:
                    incident = self._create_incident_object(incident_data)
                    
                    # Check if incident already exists
                    existing = session.query(Incident).filter_by(
                        sys_id=incident.sys_id
                    ).first()
                    
                    if existing:
                        # Update existing incident
                        self._update_incident(session, existing, incident_data)
                        results['success'] += 1
                    else:
                        # Add new incident
                        session.add(incident)
                        session.flush()  # Get the incident ID for audit history
                        
                        # Store audit history if available
                        if 'audit_history' in incident_data and incident_data['audit_history']:
                            self._store_audit_history(
                                session, 
                                incident.id, 
                                incident_data['audit_history']
                            )
                        
                        results['success'] += 1
                
                except Exception as e:
                    results['failed'] += 1
                    error_msg = f"Error storing incident {incident_data.get('number', 'UNKNOWN')}: {str(e)}"
                    results['errors'].append(error_msg)
                    print(f"❌ {error_msg}")
                    session.rollback()
            
            # Commit all successful transactions
            session.commit()
            print(f"✅ Successfully stored {results['success']}/{results['total']} incidents")
            
        except Exception as e:
            session.rollback()
            results['failed'] = results['total'] - results['success']
            error_msg = f"Database error during bulk insert: {str(e)}"
            results['errors'].append(error_msg)
            print(f"❌ {error_msg}")
        
        finally:
            session.close()
        
        return results

    def _create_incident_object(self, incident_data: Dict[str, Any]) -> Incident:
        """
        Create an Incident ORM object from API data
        
        Args:
            incident_data (dict): Raw incident data from API
        
        Returns:
            Incident: SQLAlchemy Incident object
        """
        incident = Incident(
            # Primary identifiers
            sys_id=incident_data.get('sys_id'),
            number=incident_data.get('number'),
            task_effective_number=self._clean_string(incident_data.get('task_effective_number')),
            sys_class_name=self._clean_string(incident_data.get('sys_class_name')),
            
            # Descriptions
            short_description=self._clean_string(incident_data.get('short_description')),
            description=self._clean_string(incident_data.get('description')),
            comments=self._clean_string(incident_data.get('comments')),
            comments_and_work_notes=self._clean_string(incident_data.get('comments_and_work_notes')),
            close_notes=self._clean_string(incident_data.get('close_notes')),
            work_notes=self._clean_string(incident_data.get('work_notes')),
            
            # State & Status
            state=self._clean_string(incident_data.get('state')),
            incident_state=self._clean_string(incident_data.get('incident_state')),
            active=self._parse_boolean(incident_data.get('active')),
            made_sla=self._parse_boolean(incident_data.get('made_sla')),
            knowledge=self._parse_boolean(incident_data.get('knowledge')),
            
            # Priority and Impact
            priority=self._clean_string(incident_data.get('priority')),
            urgency=self._clean_string(incident_data.get('urgency')),
            severity=self._clean_string(incident_data.get('severity')),
            impact=self._clean_string(incident_data.get('impact')),
            category=self._clean_string(incident_data.get('category')),
            subcategory=self._clean_string(incident_data.get('subcategory')),
            
            # Resolution Information
            close_code=self._clean_string(incident_data.get('close_code')),
            escalation=self._clean_string(incident_data.get('escalation')),
            
            # Assignment
            assigned_to=self._clean_string(incident_data.get('assigned_to')),
            assignment_group=self._clean_string(incident_data.get('assignment_group')),
            additional_assignee_list=self._clean_string(incident_data.get('additional_assignee_list')),
            resolved_by=self._clean_string(incident_data.get('resolved_by')),
            closed_by=self._clean_string(incident_data.get('closed_by')),
            
            # Caller & Contact
            caller_id=self._clean_string(incident_data.get('caller_id')),
            contact_type=self._clean_string(incident_data.get('contact_type')),
            company=self._clean_string(incident_data.get('company')),
            location=self._clean_string(incident_data.get('location')),
            
            # Business Information
            business_service=self._clean_string(incident_data.get('business_service')),
            business_impact=self._clean_string(incident_data.get('business_impact')),
            cmdb_ci=self._clean_string(incident_data.get('cmdb_ci')),
            service_offering=self._clean_string(incident_data.get('service_offering')),
            contract=self._clean_string(incident_data.get('contract')),
            business_duration=self._clean_string(incident_data.get('business_duration')),
            calendar_duration=self._clean_string(incident_data.get('calendar_duration')),
            time_worked=self._clean_string(incident_data.get('time_worked')),
            
            # Dates and Times
            sys_created_on=self._parse_datetime(incident_data.get('sys_created_on')),
            sys_updated_on=self._parse_datetime(incident_data.get('sys_updated_on')),
            opened_at=self._parse_datetime(incident_data.get('opened_at')),
            opened_by=self._clean_string(incident_data.get('opened_by')),
            closed_at=self._parse_datetime(incident_data.get('closed_at')),
            resolved_at=self._parse_datetime(incident_data.get('resolved_at')),
            activity_due=self._parse_datetime(incident_data.get('activity_due')),
            due_date=self._parse_datetime(incident_data.get('due_date')),
            expected_start=self._parse_datetime(incident_data.get('expected_start')),
            work_start=self._parse_datetime(incident_data.get('work_start')),
            work_end=self._parse_datetime(incident_data.get('work_end')),
            reopened_time=self._parse_datetime(incident_data.get('reopened_time')),
            
            # SLA Information
            sla_due=self._clean_string(incident_data.get('sla_due')),
            calendar_stc=self._clean_string(incident_data.get('calendar_stc')),
            business_stc=self._clean_string(incident_data.get('business_stc')),
            
            # Counters & Metadata
            sys_mod_count=incident_data.get('sys_mod_count'),
            reassignment_count=incident_data.get('reassignment_count'),
            reopen_count=incident_data.get('reopen_count'),
            
            # System Fields
            sys_domain=self._clean_string(incident_data.get('sys_domain')),
            sys_domain_path=self._clean_string(incident_data.get('sys_domain_path')),
            sys_created_by=self._clean_string(incident_data.get('sys_created_by')),
            sys_updated_by=self._clean_string(incident_data.get('sys_updated_by')),
            notify=self._clean_string(incident_data.get('notify')),
            
            # Additional Fields
            parent=self._clean_string(incident_data.get('parent')),
            parent_incident=self._clean_string(incident_data.get('parent_incident')),
            child_incidents=self._clean_string(incident_data.get('child_incidents')),
            correlation_id=self._clean_string(incident_data.get('correlation_id')),
            correlation_display=self._clean_string(incident_data.get('correlation_display')),
            approval=self._clean_string(incident_data.get('approval')),
            approval_set=self._clean_string(incident_data.get('approval_set')),
            approval_history=self._clean_string(incident_data.get('approval_history')),
            upon_approval=self._clean_string(incident_data.get('upon_approval')),
            upon_reject=self._clean_string(incident_data.get('upon_reject')),
            follow_up=self._clean_string(incident_data.get('follow_up')),
            cause=self._clean_string(incident_data.get('cause')),
            route_reason=self._clean_string(incident_data.get('route_reason')),
            hold_reason=self._clean_string(incident_data.get('hold_reason')),
            watch_list=self._clean_string(incident_data.get('watch_list')),
            user_input=self._clean_string(incident_data.get('user_input')),
            group_list=self._clean_string(incident_data.get('group_list')),
            delivery_plan=self._clean_string(incident_data.get('delivery_plan')),
            delivery_task=self._clean_string(incident_data.get('delivery_task')),
            universal_request=self._clean_string(incident_data.get('universal_request')),
            order=self._clean_string(incident_data.get('order')),
            origin_table=self._clean_string(incident_data.get('origin_table')),
            origin_id=self._clean_string(incident_data.get('origin_id')),
            u_tcs_resolver_group=self._clean_string(incident_data.get('u_tcs_resolver_group')),
            sys_tags=self._clean_string(incident_data.get('sys_tags')),
            
            # SLA Data (JSON)
            sla_data=incident_data.get('sla_data'),
        )
        
        return incident

    def _store_audit_history(self, session, incident_id: int, audit_history_list: List[Dict]):
        """
        Store audit history records for an incident
        
        Args:
            session: SQLAlchemy session
            incident_id (int): The incident database ID
            audit_history_list (List[Dict]): List of audit history records
        """
        for audit_record in audit_history_list:
            audit_entry = AuditHistory(
                incident_id=incident_id,
                fieldname=audit_record.get('fieldname'),
                oldvalue=self._clean_string(audit_record.get('oldvalue')),
                newvalue=self._clean_string(audit_record.get('newvalue')),
                sys_created_by=audit_record.get('sys_created_by'),
                sys_created_on=self._parse_datetime(audit_record.get('sys_created_on')),
            )
            session.add(audit_entry)

    def _update_incident(self, session, existing_incident: Incident, incident_data: Dict[str, Any]):
        """
        Update an existing incident with new data
        
        Args:
            session: SQLAlchemy session
            existing_incident (Incident): The existing incident to update
            incident_data (dict): New incident data
        """
        # Update key fields
        existing_incident.short_description = self._clean_string(incident_data.get('short_description'))
        existing_incident.description = self._clean_string(incident_data.get('description'))
        existing_incident.state = self._clean_string(incident_data.get('state'))
        existing_incident.incident_state = self._clean_string(incident_data.get('incident_state'))
        existing_incident.active = self._parse_boolean(incident_data.get('active'))
        existing_incident.priority = self._clean_string(incident_data.get('priority'))
        existing_incident.urgency = self._clean_string(incident_data.get('urgency'))
        existing_incident.severity = self._clean_string(incident_data.get('severity'))
        existing_incident.impact = self._clean_string(incident_data.get('impact'))
        existing_incident.assigned_to = self._clean_string(incident_data.get('assigned_to'))
        existing_incident.sys_updated_on = self._parse_datetime(incident_data.get('sys_updated_on'))
        existing_incident.sys_updated_by = self._clean_string(incident_data.get('sys_updated_by'))
        existing_incident.sla_data = incident_data.get('sla_data')
        
        session.add(existing_incident)
