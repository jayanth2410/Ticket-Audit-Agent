import re

def _parse_work_note_entries(combined: str):
    if not combined:
        return []

    pattern = re.compile(
        r'\[(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2})\]\s+(.+?)\n(.*?)(?=\[\d{4}-\d{2}-\d{2}|\Z)',
        re.DOTALL
    )

    entries = []
    for match in pattern.finditer(combined):
        entries.append({
            "sys_created_on": match.group(1).strip(),
            "sys_created_by": match.group(2).strip(),
            "value"         : match.group(3).strip(),
        })
    return entries


# Simulate what your fetcher produces
sample = """[2026-05-26 11:21:04] admin
Contacted user via phone. User confirmed issue.

[2026-05-26 14:30:00] admin
Reassigned to hardware team with detailed notes.

[2026-05-27 09:15:00] admin
Hardware team ordered replacement.
User not available on first attempt.

[2026-05-28 11:00:00] admin
User received and tested - working fine."""

entries = _parse_work_note_entries(sample)

print(f"Total entries parsed: {len(entries)}")
print("Expected: 4\n")

for i, e in enumerate(entries, 1):
    print(f"Entry {i}:")
    print(f"  sys_created_on : {e['sys_created_on']}")
    print(f"  sys_created_by : {e['sys_created_by']}")
    print(f"  value          : {e['value'][:60]}...")
    print()