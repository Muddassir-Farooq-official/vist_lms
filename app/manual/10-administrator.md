# Administrator

The administrator sets the institute up and oversees everything. Your menu is grouped by job: Students, Teaching & exams, Fees & accounts, HR & payroll, Store & assets, Reports, Communication, Other offices and System. Tap a group's name to open or close it (see *Finding your way in the menu* in Getting started). Setup pages you rarely need (CSV Imports, SMS & WhatsApp, Top bars, Activity log, Backups, Server health) are under **Settings → More tools**, and the menu's search finds them too. The **Dashboard** shows everything happening today in one place: collections, attendance, approvals waiting and alerts.

## First-time setup (in this order)

1. **Settings**: the institute's name, logo and details (they appear on every page, document and SMS), its look, and the rules the system applies (late fees, attendance minimum, pass marks, card validity and more).
2. **Programs**: each program with its number of semesters (or years).
3. **Courses → Academic terms**: register each term. Because batches start and end on their own dates, give each term its **batch** (program and semester), for example "Fall 2026 BSN 8TH" = BSN, semester 8. Then group the batch terms into **sessions** (see *Sessions* below).
4. **Courses**: the course catalogue, which program and semester each course belongs to, and the term offerings (who teaches which section).
5. **Timetable**: list your **Rooms** (with seats) and the daily periods, then add each course's weekly slots from the course page.
6. **Results → Grading tables & mark split**: give each program the grading table of its university or board and its mark split (see the Exam department chapter).
7. **Fees**: fee types and fee structures (see the Accounts office chapter).
8. **Employees** and **Students** (and **CSV Imports** for bulk records).

## Students

- **Students**: every student on record. Open one to see their details, guardians and courses, change their photo or update the record.
- **University Reg Nos**: the registration number the affiliating university (KMU, AUST …) gives each student. Type them in one by one, or download the class list as CSV, fill the `reg_number` column and upload it (you see every change before it is saved). The Reg No is printed on award lists, admit cards, result cards, the gazette and transcripts, students and parents see it on their profile, and you can search for a student by it. Two students can never share one. **Deadlines** (same page, affiliated programs only): set the last day for one batch term or for every batch of a session. Before it, the administration, the exam office and each student without a Reg No are reminded (once a week, then every day in the last 7 days; students also get an SMS). After it, that student's admit card is held until the Reg No is entered. To let one student's admit card print anyway, press **Exempt…** on the student, write the reason and optionally the last day; revoking keeps the record.
- **Employees**: everyone on staff. Open a person to see their record, pay and access, and give them roles (teacher, accounts officer, exam controller and others).
- **Hand over work** (Employees → a person): opens when you put someone *On leave*, terminate them, or suspend or terminate them on a final warning. You can also open it any time from the person's page.
  - **Away for a while** (suspended or on long leave): their courses stay theirs. Every class from a date until a date is listed, up to 120 days at a time. Choose a cover for each class, or one teacher for all classes of a course. Cover pay works as usual and the substitute is told.
  - **When they are back** (status *Active* again): covers still to come are taken back and the substitutes are told.
  - **Leaving / has left**: each running course gets a new teacher, or "no teacher yet" to decide later. The timetable goes with the course, and classes already held stay in the old teacher's name. Tick *Record that they have left* to terminate them in the same step. Choose how they left and their last working day:
    - **Resigned**: also enter the day they gave notice. With fewer than 30 days between notice and the last day, the salary for the days worked in their last month is not paid, and the payslip says why. Earlier months and their security deposit are not touched.
    - **Terminated by the institute** or **Contract ended**: they are paid for the days worked in their last month.
  - **Both cases**: covers they were going to take are given back. Upcoming exam duties, school class-teacher roles and mentees go to whoever you choose.
  - **Also when they leave**: committee seats end and clinical supervision stops. While away they keep their committee seat, shown as *away*.
  - Everything is in the activity log, and nothing already held or recorded changes.
- **Hostel**: rooms and beds, who lives where, each resident's mess fee (**Mess**), the night roll call, the month's hostel and mess draft for the accounts office, and the history of stays. The default mess fee for new residents is on Settings (Fees & fines → Hostel).
- **Transport**: routes (vehicle, seats, driver, monthly fee), their stops with pick-up times, and who rides which route (**Give a seat** by roll number; **Stops riding** ends it). **Prepare draft** works out the month's transport fees (part months by days) for the accounts office to approve. Parents and students see the route, stop and pick-up time on their home card.
- **Profile changes**: students and staff ask to change their contact details and photo. Nothing changes until you approve.
- **Graduation & alumni**:
  - **Graduation lists**: choose a program, a title and the graduation date, and **Draw up**. Every current student of the program is checked: every curriculum course passed, the CGPA needed (Settings), and clearance (fees, fines, library, store, hostel). Clear students are ticked; to include one who is not clear you must give a reason, which is recorded. **Check again** after fees are collected or results come out. **Graduate the ticked students** marks them graduated in one step (the usual checks still apply; anyone who fails one is skipped and the list says why) and turns their student login into an alumni login. Print the **List (PDF)**.
  - **Degrees & transcripts**: start a degree or transcript by roll number and move it on: being prepared, ready (give its serial number; the graduate is told), then collected (who took it) or posted (courier and tracking number). The exam office has the same screen.
  - **Alumni directory**: every graduate with the work and contact details they gave; download as CSV. **Alumni login** switches an earlier graduate's old login back on as an alumni login.
  - **Surveys**: write questions one per line (R: rating 1-5, C: choice with options after |, T: written answer). **Open** tells every alumnus; **Results** totals the answers. Once someone answers, the questions stay as they are.
- **Mentors**: give every student a teacher as mentor. Filter by program and semester (or **Without a mentor only**), tick the students, type one or more teachers' employee codes and **Assign**: with several mentors the students are shared out evenly, fewest mentees first. Keep the box ticked to leave students who already have a mentor as they are; untick it to move them. **End** stops a student's mentor (their notes stay). Mentors' meeting notes are private: only the mentor and administrators read them. **Referrals**: students a mentor referred to you, with the mentor's notes; **Answer…** records what was decided, and the mentor is told.
- **Certificate requests**: certificates students asked for. Approve with the fee (or waive it); the student downloads the certificate once it is paid.
- **Warnings**: formal warnings to students and staff. A warning counts for 12 months; the 3rd one is final and you decide the action.

### Suspended and expelled students

A student is **suspended** or **expelled** from the student's record (change status) or by a decision on a final warning (Discipline). While suspended:

- **Attendance**: the student stays on every attendance sheet (class, substitute, cover and school roll call), greyed and marked **Suspended**. Nobody can mark them, and those classes do not count for or against their attendance.
- **Exams**: no admit card; left out of seating plans; marks entry is locked; their result is held when the class result is published, and the result card and report card cannot be printed.
- **Other**: no certificates or letters; no new library books, hostel room or transport seat; no assignment submissions; the student cannot sign in. The parent portal stays open to view records and fees, with a notice saying the child is suspended.

When the suspension is lifted (status back to Active, or *Lift suspension* on the warning), every lock goes at once: nothing was deleted. Held results are worked out straight away if all the marks are in; a mark that is still missing is entered by the exam office as a correction.

An **expelled** student (status Dropped) is withdrawn from every course still running. Completed courses, grades, attendance and fees stay on record. Only the leaving papers (migration and fee clearance certificates) can still be issued. When a student leaves, is expelled or graduates, their hostel stay and transport seat end that day (charged up to then; the warden is told) and their mentor assignment ends.

**Undo by mistake**: an administrator can put a Dropped or Graduated student back to *Active* within 30 days, with a reason (at least 10 characters). Courses they were withdrawn from are not re-enrolled automatically: enrol them again from the student's page. Hostel, transport and cancelled future fees are not restored either.

**Fees while suspended**: fees assigned in bulk only reach active students. When a suspension is lifted, the fees given to their class in the meantime are listed, with one click to assign them.

Classes, marks and results recorded before the suspension or expulsion are never changed.

### Which details are required (field rules)

On **CSV Imports**, open **Field rules** (above the template download) and tick which details must be filled when a student is added. First and last name are always required; everything else can be required or optional, including the university Reg No. Save, then download the template: required columns are red and listed on the template's *Rules* sheet, and optional columns can be left empty or left out of the file. The **Add student** form follows the same rules (required fields have a red \*).

- Without the guardian's CNIC the student has no parent portal and is not linked to brothers and sisters; the page warns you before you make it optional.
- Without the student's CNIC the import cannot spot a student added twice.
- Students already on record keep everything they have, and editing a student is never blocked by these rules.

### ID cards

From a student's or employee's record (or many at once from the list) print **ID cards** with a front and back. A student card is valid from the start to the end of the student's own batch term. If a student's batch term is not set up yet, the card runs the number of months set in Settings and you get a warning naming those students. Anyone can scan the card's QR code to check it is genuine, still valid or not valid yet.

## Teaching & exams

- **Programs**: each program with its students and curriculum. (A school has **Classes & sections** here instead: classes, sections, class teachers and who teaches which subject.)
- **Courses**: the course catalogue, curriculum placement, term offerings and materials. Academic terms are on the Academic terms tab.
- **Timetable**: a step-by-step wizard that makes the weekly timetable of one or more batch terms (batches, periods & rooms, teachers, classes & labs, free times, make & publish), with a guided tour and a safe **Start again** that clears a timetable from a date. The tabs on the right show any timetable: weekly view (with PDF download), batches side by side, what is free, and all classes. Once rooms are listed, the timetable only accepts listed rooms big enough for the class. See *Timetable wizard* below. The exam department has the same Timetable page.
- **Attendance**: classes nobody marked, teachers' correction requests, marking a register yourself, locking a course at the end of term, and holidays or approved leave. Students below the attendance minimum are barred from the final exam.
- **Teacher Progress**: how far each teacher actually is through their course versus where they should be, from topics marked complete.
- **Course feedback**: students' anonymous feedback on each course and teacher.
- **Institute Overview**: how the institute is doing right now. Read-only.
- **Result Types**: the institute-wide catalogue of assessments (Quiz 1, Mid Term, Final, Practical ...), each with fixed total marks and the part of the mark split it counts in. A type no subject uses is simply deleted. A type subjects already use can be **force-deleted** by an administrator: a warning shows how many subjects, students and marks it would remove, you type its name to confirm, and a CSV backup of every mark downloads first. Results already published stay exactly as they are; subjects waiting for approval go back to their teachers. A type used by a supplementary re-sit cannot be deleted.
- **Result Approval**: approve and publish results, print award lists, result cards and the gazette, promote classes, and set grading tables (see the Exam department chapter).
- **Datesheets**: plan exam papers, put teachers on duty, fix clashes, publish and print admit cards.
- **Outcomes (OBE)**: program outcomes (PLOs) and pass levels, teachers' course files to lock, and PLO attainment. See the Exam office chapter.
- **Exam paper files**: the question papers teachers sent for each datesheet; see the Exam office chapter.
- **Seating & invigilation**: seat students room by room (different papers side by side), put invigilators in each room, print door lists, room attendance sheets and duty sheets; admit cards show the room and seat. See the Exam office chapter.
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

1. **Shared subject, one teacher, one room**: open the subject in one batch's term, choose **Combined class**, and tick the same subject in the other batches' terms. It becomes one class: its timetable is the same in every batch, and the teacher is not "already booked" by his own combined class. Sections can only be combined when their terms run at the same time of year. You can also combine from three other places: (a) when you add a class and the same subject of another batch is already there at that time, the message says so and offers **Combine**; (b) step 1 of the Timetable lists **Taught together?** suggestions for the chosen batches, each with a **Combine** button (choose the teacher when the sections have different teachers); (c) step 4, *Classes & labs*, has a **Taught together** column with **Combine** and **Separate**. Nothing is ever combined without you pressing the button. The Courses list shows "combined with" on each combined section; a section of another batch is named with its term, e.g. "A (AHS 1st)".
2. **Batches side by side** (Timetable): tick the batch terms and press **Show side by side**. Each column is one group of students; a red cell is a free period for them. The chips above show how many free periods each batch has and how many weekly classes are still to place (a course needs as many classes a week as its credit hours, at most 5).
3. **Fill free periods…** proposes classes for the free periods: combined classes first (one class fills a period in every batch), each at a time that suits the teacher, the students and a room big enough for them. Nothing is added until you press **Add these classes**; existing classes are never moved. Anything that could not be placed is listed with the reason.

If free periods remain after that, those students have fewer weekly class hours than the week has periods. Add a tutorial, lab or library period, or shorten the day. Two batches in different terms are treated as different students (so their own subjects may run at the same time) when the system can tell the batches apart: set the program and semester on each batch term in Academic terms, or offer at least one program-only subject in each. Students actually enrolled in both are always checked.

### Sessions (many batch terms at once)

When every batch has its own term (*BSN 6th Fall 2026*, *BSN 4th Fall 2026*, *MLT 2nd Fall 2026* …), a **session** such as *Fall 2026* groups the ones that run at the same time. Grouping changes no record: fees, attendance, marks and results keep each batch's term exactly as before.

1. Courses → **Academic terms** → **Sessions** → **Suggest sessions from the term names**. Sessions are read from the names (*… Fall 2026*, *… 2026-27*); a term whose name says no session but whose dates fall in one is offered there unticked (*by its dates*). For terms with no batch yet, the batch is guessed from the name (*BSN 6th* = BSN, semester 6). Check, untick or fix, then **Save these sessions**.
2. A new term whose name contains a session's name (e.g. *BSN 2nd Fall 2026*) joins that session by itself. **Edit** a session to rename it, set its dates or choose its terms; **Remove** takes away only the grouping.
3. Everywhere in the admin panel, term dropdowns are grouped by session (the current one first, older ones as *Older · …*), and term tick-lists show one block per session with **Tick all** (older sessions folded away).

**Start next session…** (on a session): give the new session's name (suggested, e.g. *Fall 2026* → *Spring 2027*) and dates, then **Preview**. For every batch it shows the next term (*BSN 6th Fall 2026* → *BSN 7th Spring 2027*; you can change the name), the courses of the next semester from the curriculum and the sections (the same as now), and how many students are already promoted to that semester and how many are not yet. A batch in its last semester, or a term with no batch, is skipped with the reason. Nothing is made until **Start the session**; then, in one step (all or nothing), the terms are created in the new session, their sections opened, and (if ticked) the promoted students enrolled in their own section. Students not yet promoted are not enrolled: promote them first (Result Approval), then enroll them from the course sections.

**One datesheet for a whole session**: on Datesheets, choose *Whole session: Fall 2026* instead of one term. Papers can then come from any batch term of that session (the course list is grouped by batch term), and it is published once, with admit cards as usual. Datesheets made for one term work as before.

**Session report** (a session's **Report** on Courses → Academic terms → Sessions): one row per batch term with its sections, courses and students, attendance (average, and students below the minimum in at least one course), results (published, approved, with the approver, not sent yet) and fees for that term (charged, paid, still owed, of which overdue), and the session's totals. **Download as CSV** for Excel.

### Timetable wizard

**Timetable** makes the timetable of one or more batch terms, step by step. The steps are numbered along the top; each shows **✓** when it is ready and **!** when something needs a look, and every step ends with **Back** and **Next**. **Take the tour** (under the page title) walks you through the page; it starts by itself the first time.

1. **Batches**: tick the batch terms you are making the timetable for (usually every batch of one session, which share teachers and rooms). Each batch keeps its own start and end dates, so semesters that do not run in parallel work too. Your choice is remembered and carried into every step. The checklist under it says what is in place for those batches; click a line to go to that step.
2. **Periods & rooms**: the periods of the day, and every classroom, lab and hall with its seats. The timetable picks rooms from this list.
3. **Teachers**: each teacher's weekly load, and the sections without a teacher (see *Teacher workload* below).
4. **Classes & labs**: each section gets as many classes a week as its credit hours; change that where needed. The **Taught together** column shows each combined class and offers to combine the same subject of other chosen batches. Tick **Lab (double)** for a lab or practical: each class is two back-to-back periods in a lab room. A **home room** is used whenever it is free. A combined class keeps the same settings in all its sections.
5. **Free times**: tick the periods a teacher cannot teach (visiting faculty, other duties) and, if needed, that teacher's own limits. Teachers can mark their own times under *My Timetable → My available times*. Tick **Fixed by the administration** to stop a teacher changing them.
6. **Make & publish**: generate a draft, check and adjust it, and publish it from a date (below).

**Start again: clear the current timetable** (on step 1). To remake a timetable from scratch, choose the date to clear from (today by default) and press **Show what will be cleared**. For every batch term you see the dates it is cleared from and until, its weekly classes, the empty classes that will be removed, the classes kept because they have attendance, the classes with a topic or files that are kept as cancelled, and the classes before the date (never touched). Each batch is cleared from the later of your date and its own start, and only up to its own end; a batch that has already finished is listed and left alone. A combined class is one class, so it is cleared in all its sections, also in a batch you did not tick (the table says so). Press **Clear…**, choose whom to tell (nobody by default: usually you tell them when you publish the new timetable) and type **CLEAR** to confirm. It is all or nothing and recorded in the activity log. One-off extra and make-up classes stay. Then go through steps 2–6.

**Make the draft**
1. On **Make & publish**, tick the term(s) to plan together and the teaching days (tick Saturday if you teach Saturdays). Set the teachers' limits: at most so many classes a day and so many periods in a row.
2. Press **Generate draft** (up to about 20 seconds). These rules are never broken: no student group, teacher or room in two places at once; teachers' unavailable times and limits; one class of a course a day; labs as two back-to-back periods in a lab; rooms big enough; combined classes together in a room big enough for all their students; classes of other terms running at the same time stay where they are. The same subject of two batches with the same teacher, not combined, is placed at different times, and the draft adds a note suggesting to combine them first. As far as possible it also spreads a course over the week (e.g. Mon/Wed/Fri), keeps it in the same period each day, and keeps students' days compact.
3. The chips show how many weekly classes were placed. **Needs your attention** lists anything that could not be placed and why (e.g. *Dr. Ali has 32 periods to teach but only 25 free periods a week*). Fix the cause and generate again, or press **Place by hand…**.
4. **Show** a class, a teacher or a room to see their week. Click a class to move it (day, period, room) or take it out. Every change is checked against the same rules. A class you place or move yourself is **pinned**: **Generate again (keep pinned)** keeps it where you put it.

**Publish**
Press **Publish…**, choose the date the new timetable starts (next Monday by default) and whom to tell: teachers, students and parents (untick to stay silent). You see what will happen before anything changes. From that date the draft replaces the timetable of its courses. Classes before that date, and every attendance mark, stay exactly as they are; upcoming classes that already have a topic or files are kept as cancelled, not deleted. Publishing is all or nothing: if something changed since the draft was made (e.g. a room was booked meanwhile), nothing changes and you are asked to generate again.

### Teacher workload and what is free

**Teacher workload** (step 3, *Teachers*, of the Timetable): tick the term(s) and press **Show workload**. For every teacher you see the credit hours a week they teach while those terms run (every section whose term dates overlap counts, so batch terms running together add up; a combined class counts once), their periods a week on the timetable, and whether they are **Over**, **OK** or **Under** their range.

- The range is 12 to 18 credit hours a week by default: change it under Settings → **Teaching load**. A teacher can have their own range (e.g. a head of department 6–9, a visiting teacher 3–6): type it in their row; leave it empty for the institute's.
- Giving a teacher a section that takes them **above their most** is refused, both when you create the section and when you change its teacher. If it really must happen, type a reason and press **Allow and save**; the reason is kept in the activity log. Being under the least is only shown, never blocked.
- When you choose a teacher for a section, the list shows the **suggested** teachers first: those who taught the course before or teach in its program, those under their least, then the least busy. Each shows their credit hours now → with this section, and their most. Teachers who would go over, or who are busy (or unavailable) at the section's times, are greyed.
- **Propose teachers for them…** suggests a teacher for every section without one, keeping everyone within their most and free at the section's times. Untick any you don't want and press **Assign the ticked teachers** (all or nothing).

**What is free** (a tab on the Timetable): tick the term(s) and press **Show**. Each period shows how many rooms and teachers are free. Click it to see which rooms (with seats) and which teachers (least busy first; teachers who marked that period unavailable are left out) are free, e.g. to find a cover teacher or a room for an extra class. Choose **a class's free periods** to see one class's free periods in the week.

Not used in School mode: a school's subjects are not counted in credit hours.

### Make-up and extra classes

Teachers book make-up classes (for a missed class) and extra classes (for a course that is behind) in free periods. Each request arrives in **Approvals**. Approving checks the slot again, adds the class to the timetable and attendance, marks the missed class "made up" and tells the students and the teacher. A booking is refused if the teacher is on approved leave that day.

- **Substitute teachers**: when you approve a teacher's leave, each of their classes during the leave gets a free teacher proposed (no class or exam duty of their own then, fewest substitute classes this week first). **Confirm** it, pick someone else from the list (busy ones show why; choosing one anyway needs a note), or **Cancel class**. The confirmed teacher is told and can mark that class's attendance from **Substitute classes** in their portal.
- **Clinical rotations** (nursing and allied health): add hospitals and their wards (how many students each takes), clinical **groups** of students (paste roll numbers), **supervisors** and the **logbook skills** each program requires. A supervisor is one of our staff (by employee code) or a hospital staff member, who gets a limited login that only opens their own rotations; the temporary password is shown once. **Schedule** a rotation: a group at a ward for a date range, on chosen days and hours. It is refused if the group is already somewhere else then, or the ward would hold more students than it takes. **Placement letter** gives the PDF letter for the hospital. **Progress** on a group shows each student's clinical attendance and how many logbook skills are complete. A rotation with attendance or logbook entries cannot be removed; change its dates instead.
  - **Internship** tab (the final-year internship): **Start** it for students by roll number, with the dates and the hours they must complete; they stay current students. **Open** an intern to place them at a ward with a supervisor for a period, approve or refuse their leave, verify or reject the duties they log (their supervisor usually does this), and read their evaluations. **Mark complete** once the verified hours reach the required hours with at least one evaluation (otherwise a recorded reason is needed); **Withdraw…** ends it with a reason. A student whose internship is not complete is not clear on a graduation list.
- **Semester rollover**: at the end of a session, four steps, each previewed first and done all at once or not at all.
  1. **Close the terms**: ends the semester for each batch term (open enrollments completed) and closes it. Unfinished courses (attendance not locked, results not published) are listed; closing anyway needs a reason.
  2. **Promote students**: everyone moves to the next semester except those you hold back (tick and write why). Failed subjects stay as carry-over for supplementary exams. A batch in its program's last semester is not promoted. **Undo** works for 7 days and puts back everyone whose semester was not changed since; fees charged for the new semester are kept and listed (financial records are never removed).
  3. **Start the next session**: the next semester's batch terms, their subjects from the curriculum (same sections), the promoted students enrolled, and the same teachers where they still work here.
  4. **Fee structures**: each batch's active fee structures copied to its next batch term as drafts (optionally raised by a %). Nothing is charged until you activate them in Fees.
## Fees & accounts, HR, store

- **Monthly charges**: the hostel, mess and transport drafts waiting for approval; see the Accounts office chapter.
- **Fees**, **Refunds**, **1Bill payments**, **Fines**, **Finance reports**, **Fee defaulters** and **Accounts**: see the Accounts office chapter. Only administrators can allow an admit card despite overdue fees (**Fee defaulters → Allow admit card…**).
- **Inventory / Store**: what the institute keeps in stock, where every item went and who has it.
- **Fixed assets**: furniture, computers, equipment, vehicles and buildings, with tag numbers, holders and depreciation.
- **Approvals**: actions that need a second person: waiving or cancelling a fee, waiving a fine, writing off previous dues, moving a payment to the right fee, reopening closed books, new scholarships or discounts, and make-up or extra classes. Approving carries the action out exactly as asked; nobody can approve their own request.
- **Payroll**: pay staff each month: salary, allowances, bonuses and deductions, with a payslip for everyone.
- **Payroll and leavers**: someone who left during the month gets a payslip for the days they worked, from the 1st to their last working day, unless they resigned with less than 30 days' notice (see *Hand over work*). Attendance after their last day is not counted against them, and no security deposit is held from their last payslip.
- **Payroll → Visiting faculty**: a visiting teacher is paid per lecture instead of a monthly salary.
  - **Setting it up**: set their employment type to *Visiting* (Employees → edit), then enter their **rate per lecture** here.
  - **What counts as a lecture**: a lecture is taken when they mark its attendance. A lab's double period is two lectures; a combined class taught to several batches at once is one; extra and make-up classes count too. Cancelled classes are neither paid nor deducted.
  - **The payslip**: every lecture held is earned. Lectures not taken, covered by another teacher, or marked by the office are deducted, with their dates. Days absent and late marks do not apply to them, but fixed allowances do.
  - **Covers**: when another teacher covers their class, the suggested cover pay is one of their lectures.
  - **Lecture sheet (PDF)**: lists every lecture of the month and what happened to it. The teacher sees the same sheet under *My payslips*.
  - **If the office marked a lecture**: if attendance was marked by the office but the teacher did teach, add the lecture back as an adjustment on the payslip.
- **Staff attendance**: when each member of staff arrives and leaves, against the timing of their job. Payroll uses it for absences and late marks.

## Reports

- **Reports**: fees, accounts, payroll, academics, staff and store reports. Choose a report, set the filters, then print it or download it as a spreadsheet.
  - **Regulator returns**: three summaries for the Nursing Council, HEC and similar returns. **Enrolment summary**: current students by program, semester and gender, with the year's added, graduated and dropped. **Teaching staff summary**: each teacher's designation, gender, employment type, joining date, years of service, and a term's courses and credit hours. **Results summary**: for a term, per program and semester, the students with results, those who passed every course, those who failed any, pass % and average GPA. They are generic: download them (CSV) and check the columns against the regulator's own form. Qualifications are not kept here.
- **Monthly report**: one month at a glance, also sent to you on the 1st of every month.
- **Morning summary**: at 8 AM, Monday to Saturday, every administrator gets a short notice (also by email when email notices are on): money received the day before and what is overdue, class and staff attendance, work waiting for you, and problems (server, backups, SMS that failed, Reg No deadlines). Monday's covers Saturday and Sunday. The page shows the same figures any time, with links. Switch it off in **Settings → Rules → Reports**.

## Library

**Library** opens the library portal (see the Library chapter).

## Other offices

Open another office's portal with your administrator rights: **Accounts office**, **Exam department**, **Principal**, **Gate**, **Hostel warden** and **General staff**. **View as a person** shows exactly what a teacher, student, parent or member of staff sees (read-only, and logged).

## Communication

- **Announcements**: issue official notices to students, teachers and staff, with attachments.
- **Gate visitors**: the visitor log book kept at the gate.
- **Tasks**: assign work and see who delivers on time.
- **Applications**: leave and other requests from students and employees. Review, ask questions, approve or reject.
- **Complaints**: every complaint filed, with per-person patterns highlighted.

## System

These are under **Settings → More tools** (and the menu's search), not in the menu list:

- **CSV Imports**: bring in students, employees, past results and more from spreadsheets. Each file is checked before anything is saved.
- **SMS & WhatsApp**: messages to students and guardians. Turn each kind on or off in Settings → SMS messages.
- **Top bars**: a notice across the top of every page for the people you choose.
- **Activity log**: everything done in the system: who, what, when and from where.
- **Backups**: see the backups and download a complete copy. See *Backups* below.
- **Server health**: the server's disk, memory and load, and whether the site, its database, its HTTPS certificate and its backups are fine. See *Server health* below.
- **Settings** (in the menu): institute details, look, rules and your own account, and the **More tools** above.
- **Data checker** (in the menu): finds gaps and mistakes in the records, each with **Fix…** to open the right page: students without a CNIC, mobile or parent's mobile, numbers that are not mobile numbers, students without a program or semester, the same name and date of birth on two students; staff without a CNIC, mobile, salary or login, and teachers with no subject; subjects without a teacher, students enrolled in nothing, open terms without dates, affiliated students without a Reg No; active students with no fee, and payments larger than the charges. It only reads; nothing is changed.

### Access control

**Access control** (System, in the menu) decides who can do what. Only administrators open it.

- **Roles**: every built-in role (Teacher, Accounts Officer, Exam Controller, HOD, Principal...) and your own roles. Pick one, then for each module (Students, Timetable, Attendance & classes, Exams & results, Fees & fines, Payroll...) choose **None**, **View** or **Edit**, and tick **Approve** for the deciding rights (approve results, attendance corrections, refunds...). **Advanced** shows the single rights. **Check and save** shows exactly what changes and for whom before anything is saved. A built-in role you changed says *changed*; **Back to the standard** puts it back. Your changes are kept through every update.
- **New role**: make your own, e.g. *Exam cell assistant* with Timetable and Exams. Start it from nothing or copy an existing role. **People with this role** (under the role's name) lists who has it; **Give it to people...** gives it to several at once, and **Take away** removes it. You can also give it on the **People** tab or on Employees → Extra access.
- **People**: one staff member's extra roles, and single rights **given** or **taken away**, with an end date if you like (it stops by itself the day after). A right taken away wins over every role. **Everything they can do now** lists each right and where it comes from. **See the system as them** opens their screens read-only.
- Someone given rights from the administration's pages sees **Administration (given to you)** in their portal switcher, with only those pages in its menu.
- **Safety**: the Administrator role always has everything and cannot be changed, and the last administrator can never lose it. Access control and View as stay with administrators. Nobody changes their own access. Students', parents' and alumni's roles keep their own portal. Giving money or control rights (refunds, reversing payments, payroll, scholarships, rules, backups...) or the Administrator role waits on **To confirm** for another administrator; with only one administrator it goes ahead and is logged.
- **Who can…**: pick a right (e.g. *Reverse payments*) to see everyone who holds it and whether it comes from a role or their own right.
- **Telling people**: whoever gains or loses a right gets a notification saying what changed. Two days before a right given until a date ends, the person and the administrators are reminded once.
- **Sign-in codes**: anyone given money or control rights here (not only administrators) must use two-step sign-in, while Settings → Sign-in security has it switched on. They are signed out when given such a right, and their next sign-in asks them to set it up.
- **History**: every change with who and when; **Undo** puts it back.

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

### Server health

**Server health** shows what the server reported in the last minute: how full its **disk** is, its **memory** and **load**, how long it has been running, and for the institute its site (up, database, how fast it answers), the **version** running and when it was deployed, when its **HTTPS certificate** expires, the size of its uploaded files and its **last backup**. Several institutes can share one server: each institute's administrators see the shared server and their own institute; the server's owner sees every institute.

Every administrator gets a **Server problem** notification (once a day while it lasts, and straight away when it changes) when: the disk is more than 85% full; a certificate expires within 14 days; a site or its database does not answer; memory is above 90% or the server is overloaded for 10 minutes or more; or the server stops reporting for 10 minutes. Certificates renew by themselves; a warning means the renewal is not working and whoever looks after the server should check it.
