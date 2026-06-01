
import os
from pathlib import Path
from dotenv import load_dotenv
from incident_fetcher import IncidentFetcher
from auditor import Auditor
from excel_handler import ExcelHandler

# Load environment variables from .env file
env_path = Path(__file__).parent.parent / '.env'
load_dotenv(env_path)

# Configuration
SERVICENOW_INSTANCE = os.getenv('SERVICENOW_INSTANCE', 'https://dev392253.service-now.com')
SERVICENOW_USER = os.getenv('SERVICENOW_USER', 'admin')
SERVICENOW_PASSWORD = os.getenv('SERVICENOW_PASSWORD', 'afi+0^JBr4LX')


def main():
    """Main execution flow"""
    
    # Step 1: Initialize fetcher
    fetcher = IncidentFetcher(SERVICENOW_INSTANCE, SERVICENOW_USER, SERVICENOW_PASSWORD)
    
    # Step 2: Fetch incidents (example date range)
    start_date = "2026-05-26"
    end_date = "2026-05-28"
    
    print(f"Fetching incidents from {start_date} to {end_date}...")
    incidents = fetcher.fetch_incidents_in_range(start_date, end_date)
    print(f"Fetched {len(incidents)} incidents")
    
    # Step 3: Save to JSON for reference
    fetcher.save_to_json(incidents, "incidents.json")
    
    # Step 4: Initialize Excel Handler
    template_path = os.path.join(Path(__file__).parent.parent, "Audit_Report_Template.xlsx")
    output_path = os.path.join(Path(__file__).parent.parent, "Audit_Report_Results.xlsx")
    excel_handler = ExcelHandler(template_path, output_path)
    
    # Step 5: Process each incident through rules
    for idx, incident in enumerate(incidents, 1):
        print(f"\nProcessing incident {idx}/{len(incidents)}...")
        
        # Create auditor instance
        auditor = Auditor(incident)
        
        # Get audit data and write to Excel
        audit_data = auditor.get_audit_data()
        excel_handler.write_audit_row(audit_data)
    
    print(f"\n{'='*80}")
    print(f"Processing complete. Total incidents processed: {len(incidents)}")
    print(f"Results saved to: {output_path}")
    print(f"{'='*80}")


if __name__ == "__main__":
    main()
