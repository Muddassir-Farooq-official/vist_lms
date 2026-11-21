# Administrator

The administrator sets the institute up and oversees everything. Your menu is grouped into People, Academics, Finance, Reports, Library, Panels, Support and System. The **Dashboard** shows everything happening today in one place: collections, attendance, approvals waiting and alerts.

## First-time setup (in this order)

1. **Settings**: the institute's name, logo and details (they appear on every page, document and SMS), its look, and the rules the system applies (late fees, attendance minimum, pass marks, card validity and more).
2. **Programs**: each program with its number of semesters (or years).
3. **Courses → Academic terms**: register each term. Because batches start and end on their own dates, give each term its **batch** (program and semester), for example "Fall 2026 BSN 8TH" = BSN, semester 8. Then group the batch terms into **sessions** (see *Sessions* below).
4. **Courses**: the course catalogue, which program and semester each course belongs to, and the term offerings (who teaches which section).
5. **Timetable**: list your **Rooms** (with seats) and the daily periods, then add each course's weekly slots from the course page.
6. **Results → Grading tables & mark split**: give each program the grading table of its university or board and its mark split (see the Exam department chapter).
7. **Fees**: fee types and fee structures (see the Accounts office chapter).
8. **Employees** and **Students** (or **Admissions** for new intakes, and **CSV Imports** for bulk records).

## People

- **Students**: every student on record. Open one to see their details, guardians and courses, change their photo or update the record.
- **University Reg Nos**: the registration number the affiliating university (KMU, AUST …) gives each student. Type them in one by one, or download the class list as CSV, fill the `reg_number` column and upload it (you see every change before it is saved). The Reg No is printed on award lists, admit cards, result cards, the gazette and transcripts, students and parents see it on their profile, and you can search for a student by it. Two students can never share one.
- **Admissions**: open a round per program and term, take applications (one by one or CSV), verify documents, publish merit lists and admit. Admitting creates the student record, roll number and login.
- **Employees**: everyone on staff. Open a person to see their record, pay and access, and give them roles (teacher, accounts officer, exam controller and others).
- **Hostel**: rooms and beds, who lives where, the monthly hostel charge (added to each resident's dues) and the history of stays.
- **Profile changes**: students and staff ask to change their contact details and photo. Nothing changes until you approve.
- **Certificate requests**: certificates students asked for. Approve with the fee (or waive it); the student downloads the certificate once it is paid.
- **Warnings**: formal warnings to students and staff. A warning counts for 12 months; the 3rd one is final and you decide the action.

### Which details are required (field rules)

On **CSV Imports**, open **Field rules** (above the template download) and tick which details must be filled when a student is added. First and last name are always required; everything else can be required or optional, including the university Reg No. Save, then download the template: required columns are red and listed on the template's *Rules* sheet, and optional columns can be left empty or left out of the file. The **Add student** form follows the same rules (required fields have a red \*).

- Without the guardian's CNIC the student has no parent portal and is not linked to brothers and sisters; the page warns you before you make it optional.
- Without the student's CNIC the import cannot spot a student added twice.
- Students already on record keep everything they have, and editing a student is never blocked by these rules.

### ID cards

From a student's or employee's record (or many at once from the list) print **ID cards** with a front and back. A student card is valid from the start to the end of the student's own batch term. If a student's batch term is not set up yet, the card runs the number of months set in Settings and you get a warning naming those students. Anyone can scan the card's QR code to check it is genuine, still valid or not valid yet.

## Academics

- **Programs**: each program with its students and curriculum. (A school has **Classes & sections** here instead: classes, sections, class teachers and who teaches which subject.)
- **Courses**: the course catalogue, curriculum placement, term offerings and materials. Academic terms are on the Academic terms tab.
- **Timetable**: the weekly timetable for any class, semester, section or teacher, with PDF download. Tabs: weekly view, slots, **Daily periods**, **Rooms** and **Batches side by side**. Once rooms are listed, the timetable only accepts listed rooms big enough for the class.
- **Smart timetable**: makes a whole term's timetable in one go as a draft, which you check, adjust and publish. See *Smart timetable* below.
- **Attendance**: classes nobody marked, teachers' correction requests, marking a register yourself, locking a course at the end of term, and holidays or approved leave. Students below the attendance minimum are barred from the final exam.
- **Teacher Progress**: how far each teacher actually is through their course versus where they should be, from topics marked complete.
- **Course feedback**: students' anonymous feedback on each course and teacher.
- **Institute Overview**: how the institute is doing right now. Read-only.
- **Result Types**: the institute-wide catalogue of assessments (Quiz 1, Mid Term, Final, Practical ...), each with fixed total marks and the part of the mark split it counts in. A type no subject uses is simply deleted. A type subjects already use can be **force-deleted** by an administrator: a warning shows how many subjects, students and marks it would remove, you type its name to confirm, and a CSV backup of every mark downloads first. Results already published stay exactly as they are; subjects waiting for approval go back to their teachers. A type used by a supplementary re-sit cannot be deleted.
- **Result Approval**: approve and publish results, print award lists, result cards and the gazette, promote classes, and set grading tables (see the Exam department chapter).
- **Datesheets**: plan exam papers, put teachers on duty, fix clashes, publish and print admit cards.
- **Supplementary Exams**: re-sit papers for students who failed on marks.
- **Calendar**: holidays, exams, events and deadlines.

### Daily periods, breaks and changing times

Timetable → **Daily periods** lists the periods of the day. Each has a type:

- **Class period**: ordinary teaching period.
- **Zero period / Assembly**: before the day starts; a class may be placed in it, the auto-scheduler never uses it.
- **Break / Prayer / Lunch**: no class can be placed; it shows on every timetable and PDF with its name (e.g. *Lunch*, *Jumu'ah*).
- **Library / Self-study**: no class and no teacher, but students are not "free" (it does not count as a free period).

Tick days to make a period apply to some days only, e.g. a shorter Friday *Period 3* plus a Monday–Thursday *Period 3*. The same number cannot be used twice on one day, and periods on the same day cannot overlap.

To make a period longer or shorter after the timetable is built, press **Change…** on it, set the new times and the date from which classes move, then **Preview**. You see every period that moves (with **Move the later periods by the same minutes** ticked, the periods after it shift too, so breaks keep their length) and how many classes move. Nothing changes until **Save this change**. Classes before that date, and any class whose attendance is already taken, keep their recorded times. Make-up or extra classes that would now overlap are listed as warnings. Deleting a period never moves classes, so use Change… to give a period new times.

All times are shown on the 12-hour clock (8:00 AM, 1:30 PM).

### Several batches at once (one term per batch)

When programs such as BS Dental, BS Anesthesia and BS MLT study semester 4 at the same time and share some subjects, give each batch its own term (e.g. *AHS BS Dental 4th*, *AHS BS Anesthesia 4th*) with the same start and end dates, and offer each subject in the term of every batch that studies it.

1. **Shared subject, one teacher, one room**: open the subject in one batch's term, choose **Combined class**, and tick the same subject in the other batches' terms. It becomes one class: its timetable is the same in every batch, and the teacher is not "already booked" by his own combined class. Sections can only be combined when their terms run at the same time of year.
2. **Batches side by side** (Timetable): tick the batch terms and press **Show side by side**. Each column is one group of students; a red cell is a free period for them. The chips above show how many free periods each batch has and how many weekly classes are still to place (a course needs as many classes a week as its credit hours, at most 5).
3. **Fill free periods…** proposes classes for the free periods: combined classes first (one class fills a period in every batch), each at a time that suits the teacher, the students and a room big enough for them. Nothing is added until you press **Add these classes**; existing classes are never moved. Anything that could not be placed is listed with the reason.

If free periods remain after that, those students have fewer weekly class hours than the week has periods. Add a tutorial, lab or library period, or shorten the day. Two batches in different terms are treated as different students (so their own subjects may run at the same time) when the system can tell the batches apart: set the program and semester on each batch term in Academic terms, or offer at least one program-only subject in each. Students actually enrolled in both are always checked.

### Sessions (many batch terms at once)

When every batch has its own term (*BSN 6th Fall 2026*, *BSN 4th Fall 2026*, *MLT 2nd Fall 2026* …), a **session** such as *Fall 2026* groups the ones that run at the same time. Grouping changes no record: fees, attendance, marks and results keep each batch's term exactly as before.

1. Courses → **Academic terms** → **Sessions** → **Suggest sessions from the term names**. Sessions are read from the names (*… Fall 2026*, *… 2026-27*); a term whose name says no session but whose dates fall in one is offered there unticked (*by its dates*). For terms with no batch yet, the batch is guessed from the name (*BSN 6th* = BSN, semester 6). Check, untick or fix, then **Save these sessions**.
2. A new term whose name contains a session's name (e.g. *BSN 2nd Fall 2026*) joins that session by itself. **Edit** a session to rename it, set its dates or choose its terms; **Remove** takes away only the grouping.
3. Everywhere in the admin panel, term dropdowns are grouped by session (the current one first, older ones as *Older · …*), and term tick-lists show one block per session with **Tick all** (older sessions folded away).

**Start next session…** (on a session): give the new session's name (suggested, e.g. *Fall 2026* → *Spring 2027*) and dates, then **Preview**. For every batch it shows the next term (*BSN 6th Fall 2026* → *BSN 7th Spring 2027*; you can change the name), the courses of the next semester from the curriculum and the sections (the same as now), and how many students are already promoted to that semester and how many are not yet. A batch in its last semester, or a term with no batch, is skipped with the reason. Nothing is made until **Start the session**; then, in one step (all or nothing), the terms are created in the new session, their sections opened, and (if ticked) the promoted students enrolled in their own section. Students not yet promoted are not enrolled: promote them first (Result Approval), then enroll them from the course sections.

### Smart timetable

**Smart timetable** makes the timetable of one or more whole terms in one go. It never changes the live timetable by itself: you get a **draft**, and only **Publish** makes it live.

**Before the first run**
1. Timetable → **Daily periods** and **Rooms**: the periods of the day, and every classroom, lab and hall with its seats. The smart timetable picks rooms from this list.
2. Smart timetable → **Classes a week & labs**: each section gets as many classes a week as its credit hours; change that where needed. Tick **Lab (double)** for a lab or practical: each class is two back-to-back periods in a lab room. A **home room** is used whenever it is free. A combined class keeps the same settings in all its sections.
3. Smart timetable → **Teachers' available times**: tick the periods a teacher cannot teach (visiting faculty, other duties) and, if needed, that teacher's own limits. Teachers can mark their own times under *My Timetable → My available times*. Tick **Fixed by the administration** to stop a teacher changing them.

**Make the draft**
1. On **Make & publish**, tick the term(s) to plan together and the teaching days (tick Saturday if you teach Saturdays). Set the teachers' limits: at most so many classes a day and so many periods in a row.
2. Press **Generate draft** (up to about 20 seconds). These rules are never broken: no student group, teacher or room in two places at once; teachers' unavailable times and limits; one class of a course a day; labs as two back-to-back periods in a lab; rooms big enough; combined classes together in a room big enough for all their students; classes of other terms running at the same time stay where they are. As far as possible it also spreads a course over the week (e.g. Mon/Wed/Fri), keeps it in the same period each day, and keeps students' days compact.
3. The chips show how many weekly classes were placed. **Needs your attention** lists anything that could not be placed and why (e.g. *Dr. Ali has 32 periods to teach but only 25 free periods a week*). Fix the cause and generate again, or press **Place by hand…**.
4. **Show** a class, a teacher or a room to see their week. Click a class to move it (day, period, room) or take it out. Every change is checked against the same rules. A class you place or move yourself is **pinned**: **Generate again (keep pinned)** keeps it where you put it.

**Publish**
Press **Publish…**, choose the date the new timetable starts (next Monday by default) and whom to tell: teachers, students and parents (untick to stay silent). You see what will happen before anything changes. From that date the draft replaces the timetable of its courses. Classes before that date, and every attendance mark, stay exactly as they are; upcoming classes that already have a topic or files are kept as cancelled, not deleted. Publishing is all or nothing: if something changed since the draft was made (e.g. a room was booked meanwhile), nothing changes and you are asked to generate again.

### Teacher workload and what is free

**Teacher workload** (a tab on Smart timetable): tick the term(s) and press **Show workload**. For every teacher you see the credit hours a week they teach while those terms run (every section whose term dates overlap counts, so batch terms running together add up; a combined class counts once), their periods a week on the timetable, and whether they are **Over**, **OK** or **Under** their range.

- The range is 12 to 18 credit hours a week by default: change it under Settings → **Teaching load**. A teacher can have their own range (e.g. a head of department 6–9, a visiting teacher 3–6): type it in their row; leave it empty for the institute's.
- Giving a teacher a section that takes them **above their most** is refused, both when you create the section and when you change its teacher. If it really must happen, type a reason and press **Allow and save**; the reason is kept in the activity log. Being under the least is only shown, never blocked.
- When you choose a teacher for a section, the list shows the **suggested** teachers first: those who taught the course before or teach in its program, those under their least, then the least busy. Each shows their credit hours now → with this section, and their most. Teachers who would go over, or who are busy (or unavailable) at the section's times, are greyed.
- **Propose teachers for them…** suggests a teacher for every section without one, keeping everyone within their most and free at the section's times. Untick any you don't want and press **Assign the ticked teachers** (all or nothing).

**What is free** (a tab on Smart timetable): tick the term(s) and press **Show**. Each period shows how many rooms and teachers are free. Click it to see which rooms (with seats) and which teachers (least busy first; teachers who marked that period unavailable are left out) are free, e.g. to find a cover teacher or a room for an extra class. Choose **a class's free periods** to see one class's free periods in the week.

Not used in School mode: a school's subjects are not counted in credit hours.

### Make-up and extra classes

Teachers book make-up classes (for a missed class) and extra classes (for a course that is behind) in free periods. Each request arrives in **Approvals**. Approving checks the slot again, adds the class to the timetable and attendance, marks the missed class "made up" and tells the students and the teacher. A booking is refused if the teacher is on approved leave that day.

## Finance

- **Fees**, **Refunds**, **1Bill payments**, **Fines**, **Finance reports** and **Accounts**: see the Accounts office chapter.
- **Inventory / Store**: what the institute keeps in stock, where every item went and who has it.
- **Fixed assets**: furniture, computers, equipment, vehicles and buildings, with tag numbers, holders and depreciation.
- **Approvals**: actions that need a second person: waiving or cancelling a fee, waiving a fine, writing off previous dues, moving a payment to the right fee, reopening closed books, new scholarships or discounts, and make-up or extra classes. Approving carries the action out exactly as asked; nobody can approve their own request.
- **Payroll**: pay staff each month: salary, allowances, bonuses and deductions, with a payslip for everyone.
- **Staff attendance**: when each member of staff arrives and leaves, against the timing of their job. Payroll uses it for absences and late marks.

## Reports

- **Reports**: fees, accounts, payroll, academics, staff and store reports. Choose a report, set the filters, then print it or download it as a spreadsheet.
- **Monthly report**: one month at a glance, also sent to you on the 1st of every month.

## Library

**Library** opens the library portal (see the Library chapter).

## Panels

Open another office's portal with your administrator rights: **Accounts office**, **Exam department**, **Principal**, **Gate**, **Hostel warden** and **General staff**. **View as a person** shows exactly what a teacher, student, parent or member of staff sees (read-only, and logged).

## Support

- **Announcements**: issue official notices to students, teachers and staff, with attachments.
- **Top bars**: a notice across the top of every page for the people you choose.
- **Gate visitors**: the visitor log book kept at the gate.
- **Tasks**: assign work and see who delivers on time.
- **Applications**: leave and other requests from students and employees. Review, ask questions, approve or reject.
- **Complaints**: every complaint filed, with per-person patterns highlighted.

## System

- **CSV Imports**: bring in students, employees, past results and more from spreadsheets. Each file is checked before anything is saved.
- **SMS & WhatsApp**: messages to students and guardians. Turn each kind on or off in Settings → SMS messages.
- **Activity log**: everything done in the system: who, what, when and from where.
- **Backups**: see the backups and download a complete copy. See *Backups* below.
- **Settings**: institute details, look, rules and your own account.

### Backups

Every night at 2:30 AM the server copies the database and all uploaded files, encrypted, to the backup account (off this server). On the 1st of each month it also restores the newest copy into a scratch database to prove the backup can really be opened. **Backups** shows:

- **Last backup**, how much is **stored on the backup account**, the size of the database and of the uploaded files, the **restore test** result and how many backups worked in the last 30 days.
- The **database copies kept**: one from each of the last 14 nights and one from each of the last 8 weeks. The uploaded files are kept as one up-to-date copy plus whatever was changed or deleted in the last 31 days.
- The **history** of every backup and restore test, with the reason when one failed.

If a backup fails, or none has finished for 26 hours, or the restore test fails, every administrator gets a notification (repeated once a day until it is fixed).

The buttons ask the server to do something; it starts within a minute:

- **Back up now**: the same backup as the nightly one, for example before a big change.
- **Download the whole database (.dump)**: one file with everything in the database. Open it in pgAdmin with *Restore* (into a new, empty database) or with `pg_restore`.
- **Download all uploaded files (ZIP)**: every photo, document and receipt. It can be large.

A download needs your password again (and the 6-digit code if two-step sign-in is on). When it is ready a **Download** button appears; only you can use it, and the file is deleted from the server after **1 hour**. The other administrators are told when a download is asked for and when it is downloaded, and both are in the activity log. A downloaded copy holds everyone's data: keep it somewhere safe and delete it when you no longer need it.
