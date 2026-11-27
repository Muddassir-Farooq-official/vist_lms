// Guided-tour words for the main pages (used by guide.js). Every page also gets a tour without an entry here: its
// introduction comes from the manual (the page's menu item), its tabs from the page itself. An entry adds:
//   tabs:    { "<data-tab key>": "what this tab is for and how to use it" }
//   buttons: { "<words on a button or link>": "what pressing it does" }   (first visible match on the page)
//   steps:   [{ el: "<css selector>", title, text }]                         (extra stops, after the tabs)
// Keep each text to two or three short sentences: what it is, then what to do.
window.VIST_TOURS = {
  // ------------------------------------------------------------------ administrator
  "/dashboard/admin": {
    steps: [
      { el: "#og-setup", title: "Set up your LMS", text: "The steps to start, in order. Each step ticks itself when it is done. Press Do it to open the page, or Show me how for its tour." },
      { el: "#drawer", title: "The menu", text: "Pages are grouped by job: Students, Teaching & exams, Fees & accounts, HR & payroll and more. Tap a group's name to open it, or type in Search the menu." }
    ]
  },
  "/dashboard/admin/students": {
    buttons: {
      "Add student": "Enter one student by hand: personal details, program and semester. They get a login with a first-time password.",
      "Import": "Add many students at once from a CSV spreadsheet (CSV Imports).",
      "Export": "Download the list as a spreadsheet."
    },
    steps: [{ el: "#students-table, .students-table, table", title: "Your students", text: "Click a student to open their sheet: profile, courses, fees, attendance, documents, status (suspend, left, graduated) and their parent's login." }]
  },
  "/dashboard/admin/employees": {
    tabs: {
      list: "Everyone on the staff. Open a person for their record, salary, login and roles. From their sheet you can also hand over their work when they are away or leave.",
      performance: "Teachers' punctuality, classes taken and feedback, side by side.",
      jobroles: "The jobs at your institute (Lecturer, Clerk...), each with its portal and timing."
    },
    buttons: { "Add employee": "Enter one person: details, job, joining date and basic salary. They get a login.", "Import": "Add many people at once from a CSV spreadsheet." }
  },
  "/dashboard/admin/programs": {
    tabs: { all: "Your programs (BS Nursing, BS Anesthesia...) with their years and semesters. Open one to change it.", add: "Add a program: its code, name, number of years and semesters." }
  },
  "/dashboard/admin/courses": {
    tabs: {
      catalog: "Every subject taught: code, title and credit hours. Add subjects here first.",
      curriculum: "Which semester of which program each subject belongs to. Students see their own semester's subjects.",
      offerings: "Course sections: a subject offered in one batch term, with its teacher and students. Open a section for its timetable, students and combined class.",
      terms: "Academic terms, one per batch (e.g. Fall 2026 BSN 1st), with start and end dates. Group them into sessions so they can be chosen together.",
      performance: "How each teacher's courses are going: syllabus covered, attendance taken, marks entered."
    }
  },
  "/dashboard/admin/imports": {
    tabs: {
      upload: "Choose what you are importing (students, staff, results...), download its template, fill it in a spreadsheet and upload it. Errors are listed by row before anything is saved.",
      history: "Every import, who did it and how many rows were added.",
      "past-results": "Results from before this system, so transcripts are complete."
    }
  },
  "/dashboard/admin/fees": {
    tabs: {
      types: "Kinds of fee (tuition, exam, hostel...). Add these first.",
      structures: "Fee structures: the amount, installments and due dates for a program and term. Activate one to charge every matching student.",
      assignments: "Who has been charged what, and their installments.",
      accounts: "One student's fee account: charges, payments, challans and concessions.",
      scholarships: "Concessions and scholarships, with approval.",
      "previous-dues": "Fees owed from before this system."
    }
  },
  "/dashboard/admin/attendance": {
    tabs: {
      attention: "Classes whose attendance was never taken. Mark them late (with a note) or cancel the class.",
      corrections: "Teachers' requests to correct attendance already saved. Approve or reject them.",
      teachers: "Each teacher's classes today: taken on time, late or missed. Give a class to another teacher here (Cover).",
      mark: "Mark any class's attendance yourself.",
      fines: "Short-attendance fines by band, and students below the minimum waiting for a decision.",
      lock: "At the end of a term, lock attendance so it can no longer change.",
      holiday: "Holidays: classes on those days are cancelled for everyone."
    }
  },
  "/dashboard/admin/payroll": {
    tabs: {
      runs: "Prepare a month's payroll, check each payslip, send it for approval and pay it. Everyone gets their payslip.",
      items: "Allowances and deductions that repeat each month for a person (conveyance, tax...).",
      advances: "Salary advances, recovered from later payslips.",
      security: "Security deposits held from salaries, and paying them back when someone leaves.",
      visiting: "Visiting faculty paid per lecture: set each one's rate and see their lectures."
    }
  },
  "/dashboard/admin/datesheets": {
    buttons: { "New datesheet": "Start a datesheet for a term (Midterm, Final...), then add each paper's day, time and room. Clashes are shown before you publish." }
  },
  "/dashboard/admin/settings": {
    steps: [{ el: ".content-main", title: "Settings", text: "Your institute's name, logo, address and type; the rules for attendance, fees, payroll and results. Each setting says what it changes." }]
  },
  // ------------------------------------------------------------------ teachers
  "/dashboard/faculty": {
    steps: [
      { el: "#og-banner", title: "What to do now", text: "Classes waiting for attendance, marks still missing and work to check. Each line opens the right page." },
      { el: "#drawer", title: "Your menu", text: "Attendance, Grading, Assignments, My Timetable and more. Search the menu if you cannot find a page." }
    ]
  },
  "/dashboard/faculty/attendance": {
    tabs: {
      take: "Choose your course and today's class, mark who is present (everyone starts as present), and save. You can mark from the class's start time for a limited time; after that, ask the office.",
      requests: "Corrections you asked for after saving, and the office's answer."
    }
  },
  "/dashboard/faculty/grading": {
    steps: [{ el: ".content-main", title: "Marks", text: "Choose a course, add its assessments (quiz, mid, final...), and enter every student's marks. When all marks are in, send the result for approval; it is locked until the office returns or publishes it." }]
  },
  "/dashboard/faculty/assignments": {
    tabs: {
      list: "Your assignments with their deadlines and how many students have handed in.",
      review: "Work handed in: open it, then accept it or send it back with feedback.",
      new: "Set new work for a class: title, instructions and the deadline. Students are told."
    }
  },
  "/dashboard/faculty/timetable": {
    tabs: {
      weekly: "Your classes for the week. Download it as a PDF.",
      upcoming: "Your next classes with their dates, rooms and topics.",
      extra: "Free periods you can use for an extra or make-up class.",
      avail: "Periods you cannot teach (for the timetable). The office may have fixed these for you."
    }
  },
  // ------------------------------------------------------------------ students and parents
  "/dashboard/student": {
    steps: [
      { el: "#og-banner", title: "What to do now", text: "Your profile, fees due, assignments due and important notices. Each line opens the right page." },
      { el: "#drawer", title: "Your menu", text: "Timetable, Attendance, Results, Fees, Assignments and your profile." }
    ]
  },
  "/dashboard/student/assignments": {
    tabs: { todo: "Work still to hand in, with the time left. Submit a Google Drive link before the deadline.", done: "Work you handed in and what your teacher said.", closed: "Work whose deadline has passed." }
  },
  "/dashboard/student/attendance": {
    tabs: { summary: "Your attendance in each course and whether it is above the minimum to sit the exam.", history: "Every class: present, absent or leave." }
  },
  "/dashboard/student/courses": {
    tabs: { mine: "The courses you are enrolled in.", available: "Your semester's courses you can still join.", browse: "Courses of other terms, for a retake." }
  },
  "/dashboard/student/profile": {
    tabs: {
      details: "Your details. To change one, ask for the change here: the office approves it before it shows.",
      quals: "Your past education.",
      certs: "Upload copies of your CNIC / B-Form and certificates for the office."
    }
  },
  "/dashboard/faculty/profile": {
    tabs: {
      details: "Your details and bank account for salary. Changes go to the office for approval.",
      quals: "Your degrees and qualifications.",
      certs: "Upload copies of your CNIC and degrees for the office."
    }
  }
};
