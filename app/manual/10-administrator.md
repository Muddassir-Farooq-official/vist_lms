# Administrator

The administrator sets the institute up and oversees everything. Your menu is grouped into People, Academics, Finance, Reports, Library, Panels, Support and System. The **Dashboard** shows everything happening today in one place: collections, attendance, approvals waiting and alerts.

## First-time setup (in this order)

1. **Settings**: the institute's name, logo and details (they appear on every page, document and SMS), its look, and the rules the system applies (late fees, attendance minimum, pass marks, card validity and more).
2. **Programs**: each program with its number of semesters (or years).
3. **Courses → Academic terms**: register each term. Because batches start and end on their own dates, give each term its **batch** (program and semester), for example "Fall 2026 BSN 8TH" = BSN, semester 8.
4. **Courses**: the course catalogue, which program and semester each course belongs to, and the term offerings (who teaches which section).
5. **Timetable**: list your **Rooms** (with seats) and the daily periods, then add each course's weekly slots from the course page.
6. **Results → Grading tables & mark split**: give each program the grading table of its university or board and its mark split (see the Exam department chapter).
7. **Fees**: fee types and fee structures (see the Accounts office chapter).
8. **Employees** and **Students** (or **Admissions** for new intakes, and **CSV Imports** for bulk records).

## People

- **Students**: every student on record. Open one to see their details, guardians and courses, change their photo or update the record.
- **Admissions**: open a round per program and term, take applications (one by one or CSV), verify documents, publish merit lists and admit. Admitting creates the student record, roll number and login.
- **Employees**: everyone on staff. Open a person to see their record, pay and access, and give them roles (teacher, accounts officer, exam controller and others).
- **Hostel**: rooms and beds, who lives where, the monthly hostel charge (added to each resident's dues) and the history of stays.
- **Profile changes**: students and staff ask to change their contact details and photo. Nothing changes until you approve.
- **Certificate requests**: certificates students asked for. Approve with the fee (or waive it); the student downloads the certificate once it is paid.
- **Warnings**: formal warnings to students and staff. A warning counts for 12 months; the 3rd one is final and you decide the action.

### ID cards

From a student's or employee's record (or many at once from the list) print **ID cards** with a front and back. A student card is valid from the start to the end of the student's own batch term. If a student's batch term is not set up yet, the card runs the number of months set in Settings and you get a warning naming those students. Anyone can scan the card's QR code to check it is genuine, still valid or not valid yet.

## Academics

- **Programs**: each program with its students and curriculum. (A school has **Classes & sections** here instead: classes, sections, class teachers and who teaches which subject.)
- **Courses**: the course catalogue, curriculum placement, term offerings and materials. Academic terms are on the Academic terms tab.
- **Timetable**: the weekly timetable for any class, semester, section or teacher, with PDF download. Tabs: weekly view, slots, **Daily periods**, **Rooms** and **Batches side by side**. Once rooms are listed, the timetable only accepts listed rooms big enough for the class.
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
- **Settings**: institute details, look, rules and your own account.
