"""
Excel Handler - Manages audit report generation and population
"""

import shutil
from pathlib import Path
from openpyxl import load_workbook
from typing import Dict, Any


class ExcelHandler:
    """Handles duplicating template and populating audit results"""
    
    def __init__(self, template_path: str, output_path: str):
        """
        Initialize Excel Handler
        
        Args:
            template_path: Path to the Audit_Report_Template.xlsx
            output_path: Path where Audit_Report_Results.xlsx will be created
        """
        self.template_path = template_path
        self.output_path = output_path
        self.current_row = 3  # Start from row 3
        
        # Duplicate template to output file
        self._duplicate_template()
        
    def _duplicate_template(self):
        """Duplicate the template file to output file"""
        print(f"Duplicating template from {self.template_path} to {self.output_path}...")
        shutil.copy(self.template_path, self.output_path)
        print(f"Template duplicated successfully\n")
    
    def write_audit_row(self, auditor_data: Dict[str, Any]) -> None:
        """
        Write a single audit row to the Excel file
        
        Args:
            auditor_data: Dictionary containing audit data to write
                - ticket_number (A)
                - created_by (B)
                - priority (C)
                - tcs_resolver_group (D)
                - resolved_by (E)
                - response_within_sla (F)
                - short_desc_quality (G)
                - priority_reassessed (H)
                - incident_reassigned (I)
                - user_contact (J)
                - resolution_sla (N)
                - resolution_notes_quality (M)
                - user_confirmation (O)
                - reopened_user_connect (P)
                - kba_education (Q)
        """
        workbook = load_workbook(self.output_path)
        worksheet = workbook.active
        
        # Write data to cells starting from column A
        worksheet[f'A{self.current_row}'] = auditor_data.get('ticket_number', '')
        worksheet[f'B{self.current_row}'] = auditor_data.get('created_by', '')
        worksheet[f'C{self.current_row}'] = auditor_data.get('priority', '')
        worksheet[f'D{self.current_row}'] = auditor_data.get('tcs_resolver_group', '')
        worksheet[f'E{self.current_row}'] = auditor_data.get('resolved_by', '')
        
        worksheet[f'F{self.current_row}'] = auditor_data.get('response_sla_met', 'NA')
        worksheet[f'G{self.current_row}'] = auditor_data.get('short_desc_quality', 'NA')
        worksheet[f'H{self.current_row}'] = auditor_data.get('priority_reassessed', 'NA')
        worksheet[f'I{self.current_row}'] = auditor_data.get('incident_reassigned', 'NA')
        worksheet[f'J{self.current_row}'] = auditor_data.get('user_contact', 'NA')
        worksheet[f'K{self.current_row}'] = auditor_data.get('pending_status', 'NA')
        worksheet[f'L{self.current_row}'] = auditor_data.get('work_notes_regular_update', 'NA')
        worksheet[f'M{self.current_row}'] = auditor_data.get('resolution_notes_quality', 'NA')
        
        worksheet[f'N{self.current_row}'] = auditor_data.get('resolution_sla_met', 'NA')
        worksheet[f'O{self.current_row}'] = auditor_data.get('user_confirmation', 'NA')
        worksheet[f'P{self.current_row}'] = auditor_data.get('reopened_user_connect', 'NA')
        worksheet[f'Q{self.current_row}'] = auditor_data.get('kba_education', 'NA')
        # Save the workbook
        workbook.save(self.output_path)
        
        print(f"✓ Row {self.current_row}: {auditor_data.get('ticket_number', 'N/A')} written to Excel")
        
        # Increment row for next ticket
        self.current_row += 1
    
    def get_current_row(self) -> int:
        """Get the current row number"""
        return self.current_row
