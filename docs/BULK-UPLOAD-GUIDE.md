# Bulk Upload — Seed Data & How To Ingest

Seed files live in `client-app-backend/docs/`. Every file in this guide was validated
against the running backend (dry-run) — the "good" ones report `ok` with zero errors,
the "errors" one fails with exactly 5 documented errors.

Regenerate any time with:

```
.venv/Scripts/python.exe scripts/make_seed_sheets.py
```

## 1. Where to upload

**UI:** sign in → sidebar **System & Settings → Bulk Upload** (`/admin/bulk-upload`).

Who can reach it: **Super Admin only** (`BULK_UPLOAD_USERS`). Principal, teacher, coach
and ALPHA admin get 403 on `/bulk-upload/*` and do not see the nav item.

The page has three steps:

| Step | What it does |
|------|--------------|
| 1. Download template | Excel workbook, one sheet per record type. Row 1 = column names, row 2 = guidance (ignored on upload). |
| 2. Pick file & check | Full validation, **nothing is written**. Reports problems row by row. |
| 3. Pick file & import | Validates again, then writes. **All-or-nothing** — if any row fails, nothing is saved. |

Pick the record type first with the chips at the top (**All sheets / Students / Players /
Staff / Teachers**). "All sheets" accepts one workbook containing several sheets.

**API equivalent** (same validation):

```
GET  /api/bulk-upload/schema             # machine-readable column spec
GET  /api/bulk-upload/template           # .xlsx, all sheets
GET  /api/bulk-upload/template.csv?kind=player
POST /api/bulk-upload/workbook?dry_run=true      # multi-sheet workbook
POST /api/bulk-upload/students?dry_run=true      # single kind: students|players|staff|teachers
```

## 2. Seed files

| File | Contents | Expected result |
|------|----------|-----------------|
| `seed-bulk-upload-all.xlsx` | 4 students, 4 players, 3 staff, 2 teachers | `ok` — 13 records |
| `seed-students.xlsx` / `.csv` | 4 students | `ok` |
| `seed-players.xlsx` / `.csv` | 4 players | `ok` |
| `seed-staff.xlsx` / `.csv` | 3 staff | `ok` |
| `seed-teachers.xlsx` / `.csv` | 2 teachers | `ok` |
| `seed-players-with-errors.xlsx` | 5 deliberately broken rows | `validation_failed` — 5 errors |

Also shipped: `bulk-upload-blank.xlsx` (headers + guidance only, no data).

The seed rows use mobile numbers in the `98765001xx`–`98765004xx` range and IDs prefixed
`SEED-` so they never collide with demo data and are easy to find and delete afterwards.

### What the error file demonstrates

| Row | Error |
|-----|-------|
| 3 | `Mobile Number: must be a 10-digit Indian mobile number` |
| 4 | `Centre: must be one of: Balua, Harding Park, Defense Colony` |
| 5 | `Player Type: Harding Park supports Daily only` (cross-field rule; Defense Colony is the same) |
| 6 | `Full Name: required` |
| 7 | `Monthly Fee Override: must not be negative` |

## 3. What can be ingested

Four record types. Limits: **500 rows** per sheet, **8 MB** per file.

### Students — 34 columns

Required: **Full Name**, **Class**, **Date of Admission**, **Student Type**

| Group | Columns |
|-------|---------|
| Identity | Admission Number (unique), Roll Number, Section, Gender, Date of Birth |
| Family | Father's Name, Mother's Name, Guardian Name, Emergency Contact |
| Contact | Primary Mobile, Email, Address, Locality, City |
| Boarding / transport | Hostel Resident, Transport Enabled, Transport Distance, Transport Fee Monthly |
| Fee overrides | Monthly, Registration, Hostel + per-head: Registration, Admission Charges, Security (Refundable), Annual Charges, Tuition, Physical Education, Exam Fee, Transport |
| Lifecycle | Status |

Allowed values — Class: `Nursery, UKG, Class I … Class X` · Section: `A–F` ·
Gender: `Male, Female, Other` · Student Type: `Day School, Boarding, Day Boarding` ·
Transport Distance: `Up to 5 km, Over 5 km` · Status: `active, deactivated`

### Players — 24 columns

Required: **Full Name**, **Mobile Number**, **Centre**, **Sport**, **Player Type**, **Slot**, **Skill Level**, **Date of Admission**

Optional: Father's Name, Guardian Name, Guardian Phone, Age, Date of Birth, Email,
Address, Locality, City, Transport Fee Monthly, Monthly/Registration/Hostel Fee Override,
Boarding Class, Boarding Section, Status

Allowed values — Centre: `Balua, Harding Park, Defense Colony` · Sport: `Cricket, Football` ·
Player Type: `Daily, Day Boarding, Hostel, Boarding` · Slot: `Morning, Evening, Both` ·
Skill Level: `Beginner, Intermediate, Advanced`

Cross-field rule: **Harding Park and Defense Colony support Daily only.**

### Staff — 17 columns

Required: **Full Name**, **Organization**, **Primary Mobile**

Optional: Employee ID, Department, Designation, Gender, Date of Birth, Date of Joining,
Email, Address, Locality, City, Guardian Name, Guardian Phone, Centre, Status

Allowed values — Organization: `PWS, ALPHA, BOTH` · Centre: `Balua, Harding Park, Defense Colony`

### Teachers — 17 columns

Required (12): **Full Name, Date of Birth, Address, Mobile, Personal Email, Aadhaar
Number, Qualification, Last Job, Guardian Name, Guardian Mobile, Reference Name,
Reference Mobile**

Optional: Qualification (Other), Teacher Designation, Date of Joining, Enable Login, Login Email

Allowed values — Qualification: `B.Ed, Bachelor's Degree, Master's Degree, Other` ·
Teacher Designation: `CLASS_TEACHER, TEACHER`

Cross-field rules: `Qualification = Other` requires **Qualification (Other)** ·
`Enable Login = Yes` requires **Login Email**, which must end in `@prarambhika.com`.

## 4. Formats accepted

- **Dates** — `YYYY-MM-DD` or `DD/MM/YYYY`. Both seed sets mix the two on purpose.
- **Mobiles** — 10 digits; `+91`, spaces and dashes are stripped before validation.
- **Yes/No** — `Yes/No`, `Y/N`, `true/false`, `1/0`.
- **Money** — plain numbers; `₹`, commas and spaces are stripped. Negatives rejected.
- **Aadhaar** — 12 digits, stored masked.
- **Files** — `.xlsx` or `.csv`. A CSV saved from the Excel template keeps working —
  its row-2 guidance line is skipped like in the workbook.

## 5. What happens on a successful import

- Students and players get their **fee schedule generated automatically** from the rate
  card (prorated for the admission month), including any per-head overrides in the sheet.
- Students are matched to a **section** from Class + Section.
- Teachers with `Enable Login = Yes` get a login account; everyone else is a roster record.
- Namesakes are **not** blocked — you get a warning (`An existing player is also called X`)
  and a separate record is created. Duplicates *within one file* (same name + DOB) are
  rejected outright.

## 6. Cleaning up after a test

Seed rows are identifiable by the `SEED-` prefix and the `98765001xx`–`98765004xx` mobile
range:

```javascript
// mongosh
db.people.deleteMany({ mobile: /^98765(00|01|02|03|04)/ })
db.people.deleteMany({ admission_number: /^SEED-/ })
db.people.deleteMany({ employee_id: /^SEED-EMP-/ })
db.users.deleteMany({ email: "nikhil.ranjan@prarambhika.com" })
```

Delete their generated fees too, otherwise the roster delete is refused with
"Person has fee records — deactivate instead of deleting":

```javascript
const ids = db.people.find({ admission_number: /^SEED-/ }, { id: 1 }).toArray().map(p => p.id)
db.fees.deleteMany({ player_id: { $in: ids } })
```
