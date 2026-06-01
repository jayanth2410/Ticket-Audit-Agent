# Ticket Audit

## Folder Structure
```
ticketaudit/
├── backend/
│   ├── api.py                  ← Flask API server  (START HERE)
│   ├── auditor.py
│   ├── excel_handler.py
│   ├── incident_fetcher.py
│   ├── llm.py
│   └── main.py
├── frontend/
│   └── index.html              ← Open in browser
├── Audit_Report_Template.xlsx  ← Place your template here
├── .env                        ← Credentials
└── requirements.txt
```

## Setup

### 1. Create .env in the ticketaudit/ root
```
SERVICENOW_INSTANCE=https://dev392253.service-now.com
SERVICENOW_USER=admin
SERVICENOW_PASSWORD=your_password
GROQ_API_KEY=your_groq_key
```

### 2. Install dependencies
```bash
pip install -r requirements.txt
```

### 3. Place your template
Copy `Audit_Report_Template.xlsx` into the `ticketaudit/` root folder.

### 4. Start the API server
```bash
cd ticketaudit/backend
python api.py
```
Server runs on http://localhost:5000

### 5. Open the UI
Open `ticketaudit/frontend/index.html` in your browser.

## Usage
1. Select start and end date
2. Optionally enter a resolver group to filter
3. Click **Run Audit**
4. Watch live progress in the log panel
5. View summary cards, metric pass rates, and per-ticket results
6. Click **Download Excel** to get the full report
