"""
Incident Orchestrator
=====================
Orchestrates the fetch, enrich, and store workflow with smart database lookups.
Minimizes API calls by checking database first and only fetching new/updated incidents.
"""

from datetime import datetime
from typing import List, Dict, Any, Tuple
from sqlalchemy import and_

from db_modal import Incident
from db_config import DBConfig
from incident_fetcher import IncidentFetcher
from incident_storage import IncidentStorage


class IncidentOrchestrator:
    """Orchestrates fetching, enriching, and storing incidents with database awareness"""

    def __init__(self, db_config: DBConfig, fetcher: IncidentFetcher):
        """
        Initialize the orchestrator
        
        Args:
            db_config (DBConfig): Database configuration
            fetcher (IncidentFetcher): ServiceNow incident fetcher
        """
        self.db_config = db_config
        self.fetcher = fetcher
        self.storage = IncidentStorage(db_config)

    def _parse_datetime(self, date_string: str) -> datetime:
        """Parse 'YYYY-MM-DD' to datetime at start of day"""
        return datetime.strptime(date_string, "%Y-%m-%d")

    def get_incidents_in_database(self, start_date: str, end_date: str) -> Dict[str, Any]:
        """
        Get incidents from database for a date range (based on closed_at)
        
        Args:
            start_date (str): "YYYY-MM-DD"
            end_date (str): "YYYY-MM-DD"
        
        Returns:
            dict: {
                'sys_id': incident_record,  # keyed by sys_id for quick lookup
                'count': int
            }
        """
        session = self.db_config.get_session()
        
        try:
            start_dt = datetime.strptime(f"{start_date} 00:00:00", "%Y-%m-%d %H:%M:%S")
            end_dt = datetime.strptime(f"{end_date} 23:59:59", "%Y-%m-%d %H:%M:%S")
            
            incidents = session.query(Incident).filter(
                and_(
                    Incident.closed_at >= start_dt,
                    Incident.closed_at <= end_dt
                )
            ).all()
            
            # Convert to dict keyed by sys_id for quick lookup
            incidents_dict = {incident.sys_id: incident for incident in incidents}

            return {
                'incidents': incidents_dict,
                'count': len(incidents)
            }
        
            

        
        finally:
            session.close()

    def identify_incidents_to_fetch(
        self,
        start_date: str,
        end_date: str,
        ticket_type: str = "incident",
        resolver_group: str = None
    ) -> Dict[str, Any]:
        """
        Identify which incidents need to be fetched from ServiceNow
        
        Strategy:
        1. Fetch ALL incidents from ServiceNow for the date range
        2. Compare with database:
           - NEW: sys_id not in DB → fetch
           - MODIFIED: sys_id in DB but sys_updated_on in ServiceNow > sys_updated_on in DB → re-fetch
           - UNCHANGED: sys_id in DB and not modified → skip
        
        Args:
            start_date (str): "YYYY-MM-DD"
            end_date (str): "YYYY-MM-DD"
            ticket_type (str): "incident", "service_request", etc.
            resolver_group (str): Optional filter
        
        Returns:
            dict: {
                'to_fetch': List[incident_numbers],  # Incidents to fetch
                'new_count': int,                     # New incidents
                'modified_count': int,                # Modified incidents
                'unchanged_count': int,               # Already in DB and unchanged
                'total_in_range': int,                # Total in ServiceNow for date range
                'db_data': dict                       # Existing DB data by sys_id
            }
        """
        print(f"\n📊 Checking database for incidents in range {start_date} to {end_date}...")
        
        # Step 1: Get what we have in the database
        db_result = self.get_incidents_in_database(start_date, end_date)
        db_incidents = db_result['incidents']
        print(f"✅ Found {db_result['count']} incidents in database for this date range")
        
        # Step 2: Fetch incident list from ServiceNow (without enrichment yet)
        print(f"🔍 Fetching incident list from ServiceNow for {start_date} to {end_date}...")
        
        url = f"{self.fetcher.instance_url}/api/now/table/incident"
        start_datetime = f"{start_date} 00:00:00"
        end_datetime = f"{end_date} 23:59:59"
        
        query_parts = [
            "state=7",  # Closed
            f"closed_at>={start_datetime}",
            f"closed_at<={end_datetime}",
        ]
        
        if resolver_group:
            query_parts.append(f"u_tcs_resolver_group={resolver_group}")
        
        params = {
            "sysparm_query": "^".join(query_parts),
            "sysparm_fields": "sys_id,number,sys_updated_on",
            "sysparm_display_value": "true",
            "sysparm_exclude_reference_link": "true",
            "sysparm_limit": 1000,
        }
        
        try:
            resp = self.fetcher._session_get(url, params=params) if hasattr(self.fetcher, '_session_get') else \
                   self.fetcher._make_request(url, params=params) if hasattr(self.fetcher, '_make_request') else \
                   self._fallback_request(url, params)
            
            if isinstance(resp, dict) and 'result' in resp:
                sn_incidents = resp['result']
            else:
                import requests
                resp = requests.get(url, auth=self.fetcher.auth, headers=self.fetcher.headers, params=params, verify=True)
                resp.raise_for_status()
                sn_incidents = resp.json().get('result', [])
        
        except Exception as e:
            print(f"❌ Error fetching incident list from ServiceNow: {e}")
            return {
                'to_fetch': [],
                'new_count': 0,
                'modified_count': 0,
                'unchanged_count': 0,
                'total_in_range': 0,
                'db_data': db_incidents
            }
        
        print(f"📋 Found {len(sn_incidents)} incidents in ServiceNow for this date range")
        
        # Step 3: Compare and identify which to fetch
        to_fetch = []
        new_count = 0
        modified_count = 0
        unchanged_count = 0
        
        for sn_incident in sn_incidents:
            sys_id = sn_incident.get('sys_id')
            number = sn_incident.get('number', 'UNKNOWN')
            sn_updated_on = sn_incident.get('sys_updated_on', '')
            
            if sys_id not in db_incidents:
                # NEW incident
                to_fetch.append(number)
                new_count += 1
                print(f"  📌 NEW: {number}")
            else:
                # Incident exists in DB - check if it was updated
                db_incident = db_incidents[sys_id]
                db_updated_on = db_incident.sys_updated_on
                
                # Parse datetime for comparison
                try:
                    sn_dt = datetime.strptime(sn_updated_on, "%Y-%m-%d %H:%M:%S")
                    db_dt = db_updated_on if isinstance(db_updated_on, datetime) else datetime.now()
                    
                    if sn_dt > db_dt:
                        # MODIFIED incident
                        to_fetch.append(number)
                        modified_count += 1
                        print(f"  🔄 MODIFIED: {number}")
                    else:
                        # UNCHANGED
                        unchanged_count += 1
                        print(f"  ✓ UNCHANGED: {number}")
                
                except Exception as e:
                    print(f"  ⚠️ Error comparing timestamps for {number}: {e}")
                    # Default to re-fetch if we can't compare
                    to_fetch.append(number)
                    modified_count += 1
        
        return {
            'to_fetch': to_fetch,
            'new_count': new_count,
            'modified_count': modified_count,
            'unchanged_count': unchanged_count,
            'total_in_range': len(sn_incidents),
            'db_data': db_incidents
        }

    def fetch_and_store(
        self,
        start_date: str,
        end_date: str,
        ticket_type: str = "incident",
        resolver_group: str = None
    ) -> Dict[str, Any]:
        """
        Orchestrate the complete fetch, enrich, and store workflow
        
        Args:
            start_date (str): "YYYY-MM-DD"
            end_date (str): "YYYY-MM-DD"
            ticket_type (str): "incident", "service_request", etc.
            resolver_group (str): Optional filter
        
        Returns:
            dict: {
                'analysis': {...},          # From identify_incidents_to_fetch
                'fetched_count': int,       # Incidents fetched from API
                'storage_results': {...}    # From store_incidents
            }
        """
        print(f"\n{'='*80}")
        print(f"🚀 INCIDENT ORCHESTRATION: {start_date} to {end_date}")
        print(f"{'='*80}")
        
        # Step 1: Analyze what needs fetching
        analysis = self.identify_incidents_to_fetch(
            start_date, end_date, ticket_type, resolver_group
        )
        
        print(f"\n📊 SUMMARY:")
        print(f"  New incidents: {analysis['new_count']}")
        print(f"  Modified incidents: {analysis['modified_count']}")
        print(f"  Unchanged incidents: {analysis['unchanged_count']}")
        print(f"  Total to fetch: {len(analysis['to_fetch'])}")
        
        # Step 2: Fetch from ServiceNow (only those that need it)
        if analysis['to_fetch']:
            print(f"\n🔄 Fetching {len(analysis['to_fetch'])} incidents from ServiceNow...")
            enriched_incidents = self.fetcher.fetch_incidents_in_range(
                ticket_type=ticket_type,
                start_date=start_date,
                end_date=end_date,
                resolver_group=resolver_group
            )
            
            # Filter to only incidents we need (in case we fetched extras)
            to_fetch_set = set(analysis['to_fetch'])
            filtered_incidents = [inc for inc in enriched_incidents if inc.get('number') in to_fetch_set]
            print(f"✅ Fetched and enriched {len(filtered_incidents)} incidents")
        else:
            print(f"\n✓ All incidents in date range are already in database and unchanged")
            filtered_incidents = []
        
        # Step 3: Store in database
        if filtered_incidents:
            print(f"\n💾 Storing {len(filtered_incidents)} incidents in database...")
            storage_results = self.storage.store_incidents(filtered_incidents)
        else:
            storage_results = {
                'success': 0,
                'failed': 0,
                'total': 0,
                'errors': []
            }
        
        # Step 4: Final summary
        print(f"\n{'='*80}")
        print(f"✅ ORCHESTRATION COMPLETE")
        print(f"{'='*80}")
        print(f"Database incidents for range: {analysis['total_in_range']}")
        print(f"Already in DB (unchanged): {analysis['unchanged_count']}")
        print(f"Fetched from API: {len(filtered_incidents)}")
        print(f"Successfully stored: {storage_results['success']}")
        if storage_results['failed'] > 0:
            print(f"❌ Failed to store: {storage_results['failed']}")
        print(f"{'='*80}\n")
        
        return {
            'analysis': analysis,
            'fetched_count': len(filtered_incidents),
            'storage_results': storage_results
        }

    def _fallback_request(self, url: str, params: dict):
        """Fallback request method using requests library"""
        import requests
        resp = requests.get(url, auth=self.fetcher.auth, headers=self.fetcher.headers, params=params, verify=True)
        resp.raise_for_status()
        return resp.json()
