# PALOS — PA Documentation Edge Cases

PA Networks syslog documentation contains a number of inconsistencies across log types:
variable names truncated in field table parentheticals, outright typos, fields with no
parenthetical at all, long names in the format string that differ from auto-detected table
mappings, and at least one literal PA docs structural bug. PALOS corrects all of these
automatically through `paloalto_scraper_exceptions.yaml`. This file catalogs every known
correction, its root cause, and the affected log types.

Corrections are applied in four layers, matching the internal pipeline order. See
[DEVELOPERS_GUIDE.md](DEVELOPERS_GUIDE.md) for how each layer fits into the pipeline.

---

## Layer 1 — Token Corrections (Variable Name column typos)

PA field tables sometimes contain wrong or truncated variable names in the parenthetical
alongside a field's long name. `token_corrections` maps each incorrect value to its correct
form. This correction is applied both to the `*_fields.csv` Variable Name column and to each
output token in the transformed format string line.

| Affected Log(s) | Field Name | PA docs value | Corrected to | Notes |
|---|---|---|---|---|
| User-ID_Log | FUTURE_USE | `FUTURE_USER` | `FUTURE_USE` | Typo in PA field table |
| IP_Tag_Log, Auth, URL, Threat, User-ID | High Resolution Timestamp | `high_res` | `high_res_timestamp` | Truncated; full name confirmed from other log types where it appears correctly |
| IP_Tag_Log | Event ID | `event_id` | `eventid` | Underscore inconsistency; PA uses `eventid` (no underscore) across all other log types |
| Traffic_Log | Tunnel ID/IMSI | `tunnelid/imsi` | `tunnel_id/imsi` | Missing underscore before slash |
| GTP_Log | A Slice Service Type | `nsdsai_sst` | `nssai_sst` | "nsdsai" is a PA typo for "nssai" (correct 3GPP S-NSSAI abbreviation) |
| GTP_Log | A Slice Differentiator | `nsdsai_sd` | `nssai_sd` | Same "nsdsai" → "nssai" typo |

---

## Layer 2 — Missing Variable Names (no parenthetical in PA docs)

Some log types omit the parenthetical variable name from their field table entirely. PALOS
fills these from `global_name_overrides`, using the long field name as the lookup key. Values
are cross-referenced from other log types (System_Log, Config_Log) that do include the
parenthetical for the same field.

| Log Type | Field Name | PA docs Variable Name | PALOS fills with | Cross-reference source |
|---|---|---|---|---|
| Audit_Log | Serial Number | *(empty)* | `serial` | System_Log / Config_Log field tables |
| Audit_Log | Event ID | *(empty)* | `eventid` | System_Log field table |
| Audit_Log | Object | *(empty)* | `object` | Config_Log field table |
| Audit_Log | CLI Command | *(empty)* | `cmd` | Config_Log field table |
| Audit_Log | Severity | *(empty)* | `severity` | System_Log field table |

---

## Layer 3 — Long Name Mapping Gaps (format string → variable name)

The format string uses long human-readable names (e.g. "Generated Time", "Source Address").
PALOS auto-detects the mapping from long name to variable name by reading the field table
parentheticals. However, some long names fail to auto-match due to casing differences,
inconsistent phrasing across log types, or missing parentheticals. These are encoded in
`global_name_overrides`.

### Time fields

Two log types (IP_Tag_Log uses "Generate Time"; most others use "Generated Time") produce
a long name that does not match any parenthetical in their own field table.

| Long name in format string | Mapped to | Affected log types |
|---|---|---|
| "Generated Time" | `time_generated` | Most log types |
| "Generate Time" | `time_generated` | IP_Tag_Log |

### Device Group Hierarchy (three naming patterns across log types)

PA documentation uses three distinct naming patterns for the same concept across different
log types. The code handles `Device Group Hierarchy Level N` via regex; the other two are
handled by `global_name_overrides`.

| Pattern in format string | Example | Mapped to |
|---|---|---|
| `Device Group Hierarchy Level N` | "Device Group Hierarchy Level 1" | `dg_hier_level_1` (regex in `_transform_format_string`) |
| `DG Hierarchy Level N` | "DG Hierarchy Level 1" | `dg_hier_level_1` |
| `Device Group Hierarchy N` | "Device Group Hierarchy 1" | `dg_hier_level_1` |

### Other unmapped long names

These long names appear in format strings but do not auto-map to a variable name, each
for a specific reason documented in the Root Cause column below.

| Field Name (in *_fields.csv) | Long name in format string | Mapped to | Affected log types | Root Cause |
|---|---|---|---|---|
| Threat/Content Name (threatid) | "Threat ID" | `threatid` | Data_Filtering_Log, Threat_Log, URL_Filtering_Log | Format string name differs from field table name |
| Source Country (srcloc) | "Source Location" | `srcloc` | GTP_Log, Threat_Log, Tunnel_Inspection_Log | Format string name differs from field table name |
| Destination Country (dstloc) | "Destination Location" | `dstloc` | GTP_Log, Threat_Log, Tunnel_Inspection_Log | Format string name differs from field table name |
| *(absent from field table)* | "Parent Start Time" | `parent_start_time` | Data_Filtering_Log, Threat_Log, Traffic_Log, Tunnel_Inspection_Log, URL_Filtering_Log | Field absent from most of these log types' field tables |
| PCAP ID (pcap_id) | "PCAP_ID" | `pcap_id` | Data_Filtering_Log, Threat_Log, URL_Filtering_Log | Format string uses underscore; field table uses space |
| Threat/Content Type (subtype) | "Subtype" | `subtype` | Config_Log, Tunnel_Inspection_Log | Format string name differs from field table name |
| Serial Number (serial) | "Serial" | `serial` | IP_Tag_Log | Format string name differs from field table name |
| Certificate Fingerprint (fingerprint) | "Fingerprint" | `fingerprint` | Decryption_Log | Format string name differs from field table name |
| IPv6 System Address (srcipv6) | "IPv6 Source Address" | `srcipv6` | HIP_Match_Log | Format string name differs from field table name |
| High Resolution Timestamp (high_res_timestamp) | "High Res Timestamp" | `high_res_timestamp` | Decryption_Log, GlobalProtect_Log | Format string abbreviates field table name |
| Gateway Selection Method (selection_type) | "Selection Type" | `selection_type` | GlobalProtect_Log | Format string name differs from field table name |
| SSL Response Time (response_time) | "Response Time" | `response_time` | GlobalProtect_Log | Format string name differs from field table name |
| Gateway Priority (priority) | "Priority" | `priority` | GlobalProtect_Log | Format string name differs from field table name |
| Gateway Name (gateway) | "Gateway" | `gateway` | GlobalProtect_Log | Format string name differs from field table name |
| Tunnel ID (tunnelid) | "Tunnel ID/IMSI" | `tunnelid` | Data_Filtering_Log, GTP_Log, Threat_Log, Traffic_Log, Tunnel_Inspection_Log, URL_Filtering_Log | Format string has "/IMSI" suffix absent from field table name |
| Monitor Tag (monitortag) | "Monitor Tag/IMEI" | `monitortag` | Data_Filtering_Log, GTP_Log, Threat_Log, Traffic_Log, Tunnel_Inspection_Log, URL_Filtering_Log | Format string has "/IMEI" suffix absent from field table name |
| Tunnel Type (tunnel) | "Tunnel" | `tunnel` | Decryption_Log, Tunnel_Inspection_Log | Format string name differs from field table name |
| Strict Checking (strict_check) | "Strict Check" | `strict_check` | Tunnel_Inspection_Log | Format string name differs from field table name |
| Security Rule UUID (rule_uuid) | "Rule UUID" | `rule_uuid` | Data_Filtering_Log, Threat_Log, Traffic_Log, Tunnel_Inspection_Log, URL_Filtering_Log | Format string name differs from field table name |
| Dynamic User Group Name (dynusergroup_name) | "Dynamic User Group" | `dynusergroup_name` | Tunnel_Inspection_Log | Format string name differs from field table name |
| Threat/ContentType (subtype) | "Threat/Content Type" | `subtype` | Decryption_Log | Typo in PA field table: missing space ("ContentType" instead of "Content Type") — auto-detected key `"Threat/ContentType"` does not match format string token `"Threat/Content Type"` |
| Issuer Common Name (issuer_cn) | "Issuer Subject Common Name" | `issuer_cn` | Decryption_Log | Format string name differs from field table name |
| Root Common Name (root_cn) | "Root Subject Common Name" | `root_cn` | Decryption_Log | Format string name differs from field table name |
| Server Name Indication(sni) | "Server Name Indication" | `sni` | Decryption_Log | Malformed parenthetical (no space before `(`) — regex fails to extract variable name |
| End IP Address (end_ip_adr) | "End User IP Address" | `end_ip_adr` | GTP_Log | Format string name differs from field table name |
| Serving Network MCC (mcc) | "Serving Country MCC" | `mcc` | GTP_Log | Format string name differs from field table name |
| Tunnel Inspection Rule(tunnel_insp_rule) | "Tunnel Inspection Rule" | `tunnel_insp_rule` | GTP_Log, Tunnel_Inspection_Log | Malformed parenthetical (no space before `(`) — regex fails to extract variable name |

---

## Layer 4 — Per-Log Structural Corrections

Some corrections are position-based and specific to one log type — either a PA docs structural
bug that breaks parsing, or a case where auto-detection produces the wrong result at a specific
field position that cannot be fixed by a simple token or name override.

### GlobalProtect_Log — Serial field collision at position 1

PA's GlobalProtect field table contains two serial-related rows:

| Row | Field Name | PA docs Variable Name | Correct value |
|---|---|---|---|
| 2 | Serial # (serial) | `serial` | `serial` ✅ |
| 19 | Serial Number (serialnumber) | `serialnumber` | `serialnumber` ✅ |

The format string position 1 maps to the "Serial #" field (the firewall's own serial number),
which correctly resolves to `serial`. However, PA's raw field table parenthetical at that
position reads `serialnumber` (the same string as the unrelated row 19 field). The
`per_log_corrections` rule replaces position 1 with `serial` to fix this collision without
affecting position 19's `serialnumber` (a different field — the machine's serial number).

### Correlated_Events_Log — Period instead of comma (PA docs literal bug)

PA documentation uses a period instead of a comma between "Source Address" and "Source User"
in the Correlated Events format string, producing the single token:

```
"Source Address. Source User"
```

This is a literal bug in the PA documentation page — not a PALOS parsing issue. The
`split_into` correction at position 6 expands this single malformed token back into two
correct tokens: `src`, `srcuser`.
