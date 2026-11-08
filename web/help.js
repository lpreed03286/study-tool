"use strict";

// [[Label]] names something on screen. A test checks that each one really exists.
const HELP_GROUPS = [
  {
    title: "Getting started",
    sections: [
      {
        id: "start", title: "Opening the program",
        what: "Everything runs on your own computer, inside a browser window. A Claude Code session has to stay open, because that's what keeps the program running. Each person needs their own Claude Code and uses their own Claude usage.",
        example: "In a terminal, run <code>claude</code>, then type <code>/worksheet</code>. A browser window opens at your Study materials page.",
        how: [
          "Bookmark the page with Ctrl+D. Its address includes a private key, so typing only the first part of the address won't work.",
          "A bookmark only works while a Claude session is open and <code>/worksheet</code> has been run in it.",
          "Your work is saved as you go, so closing the window or the session loses nothing.",
        ],
      },
      {
        id: "home", title: "The Study materials page",
        what: "Your home page, listing every worksheet, study guide and equation sheet you've made.",
        example: "To find your physics guides, set [[Show]] to Study guides and [[Sort by]] to Name, then click a row to open it.",
        how: [
          "[[Sort by]] and [[Show]] remember your choice for next time.",
          "A row saying Claude is making this turns into the finished item by itself, so there's no need to refresh.",
          "Use [[All materials]] at the top of any item to come back here.",
          "[[Light mode]] and [[Dark mode]] switch the colors on any page.",
        ],
      },
      {
        id: "new", title: "Making something new",
        what: "[[New]] opens a form for a worksheet, study guide or equation sheet. Claude builds it in your open session.",
        example: "Choose Study guide, then enter Physics, Giancoli, 7th edition and Chapter 11: oscillations and waves. In the focus box, write: I'm struggling with resonance, so keep it simple.",
        how: [
          "Be specific in the focus box. It's the best way to shape what you get.",
          "Watch the terminal after submitting, because that's where Claude asks any questions and any permission prompts appear.",
          "If a row stays on Claude is making this for a long time, check the terminal first. If it says it didn't finish, press [[Try again]] or [[Dismiss]].",
          "You can skip the form and just ask in the terminal, for example: make a 5-problem worksheet on Euler-Cauchy equations.",
          "Each item uses some of your Claude usage, and citing sources takes extra time.",
        ],
      },
    ],
  },
  {
    title: "Worksheets",
    sections: [
      {
        id: "steps", title: "Working through a problem",
        what: "Each problem has its own tab and shows one step at a time. A correct answer locks in and opens the next step. The count on the tab shows your progress.",
        example: "For $y''+3y'+2y=0$, the first step asks for the characteristic equation. Enter $r^2+3r+2=0$ and press [[Submit]]. The next step then asks for the roots.",
        how: [
          "Answers are checked by what makes them correct, not by matching text, so $(r+1)(r+2)=0$ and $2r^2+6r+4=0$ both pass.",
          "If an answer is marked wrong unexpectedly, check the Read as line to see how it was understood.",
          "Use exact fractions. A decimal like $0.25$ will ask you to write $\\frac{1}{4}$ instead.",
          "A correct general solution can use your own constant names, like $A$ and $B$.",
        ],
      },
      {
        id: "typing", title: "Typing math",
        what: "The answer box turns what you type into real math, with a live preview underneath. The [[LaTeX cheat sheet]] lists the common symbols, and clicking one inserts it.",
        example: "To enter $\\frac{1}{3}e^{2x}$, type <code>1/3</code>, press the right arrow, type <code>e^2x</code>, then press the right arrow again.",
        how: [
          "Press the right arrow to leave a fraction, exponent or subscript. It stays open until you do, and this is the most common typing mistake.",
          "Be careful with order: $1/2x$ reads as $x/2$. Use a fraction for $\\frac{1}{2x}$.",
          "$\\log$ means natural log, the same as $\\ln$.",
          "Check the preview before you submit.",
        ],
      },
      {
        id: "explain", title: "Wrong answers and Explain why",
        what: "A wrong answer stays on screen so you can fix it. [[Explain why]] asks Claude what went wrong with your specific answer, without giving away the right one.",
        example: "You enter $y=c_1x^2+c_2x^2$. Explain why points out the problem with your two solutions rather than just showing the answer, and you try again.",
        how: [
          "Try to spot the mistake yourself first. Explain why uses your Claude usage.",
          "Its replies are live, so they have no source links.",
        ],
      },
      {
        id: "hints", title: "Hints",
        what: "Each step has three hints, shown in order: [[Hint: nudge]], [[Hint: method]] and [[Hint: answer]]. Hints use none of your Claude usage.",
        example: "Stuck on a trial form? The nudge points you in the right direction, the method hint tells you which rule to use, and the answer hint shows the answer.",
        how: [
          "Take the smallest hint that gets you moving.",
          "Your hint use is recorded and helps decide what future worksheets focus on.",
        ],
      },
      {
        id: "fade", title: "Pre-filled steps",
        what: "The [[Pre-filled steps]] slider fills in the early steps of a problem for you, so you can practice just the later ones. It changes as you move it.",
        example: "If you're comfortable finding roots but want practice applying initial conditions, slide it up until only the last steps are left.",
        how: [
          "Start low on a new topic and raise it as a review tool.",
          "Answers you've already got right stay yours.",
        ],
      },
      {
        id: "own", title: "My own method",
        what: "If you solve a step your own way, [[My own method]] lets you write out your reasoning for Claude to judge. A correct path counts even if it isn't the book's.",
        example: "You solve an equation by a substitution the worksheet doesn't use. Write out your steps and your result. SymPy checks the result, and Claude checks the reasoning.",
        how: [
          "Show your actual steps, since a right answer from faulty reasoning won't pass.",
          "Give your result too, so SymPy can confirm it. This uses your Claude usage.",
        ],
      },
      {
        id: "method-notes", title: "How the book does it",
        what: "A panel on each problem card summarizes how your textbook presents the method, with a button to [[Ask Claude to walk through the method]] using a different example.",
        example: "Before starting an undetermined coefficients problem, open the panel to review the Basic, Modification and Sum Rules.",
        how: [
          "Read it before you start if the method is rusty.",
          "It says whether it came from your pages or from general knowledge of the book, so check the latter against your copy.",
        ],
      },
      {
        id: "solution", title: "Worked solutions",
        what: "Finishing a problem shows a worked solution with every step, and a note on how the answer key was verified.",
        example: "After finishing, compare the worked solution with your own work, especially any step where you needed a hint.",
        how: [
          "Math answer keys are verified by SymPy before you ever see them.",
          "If a saved key is ever changed on disk, the problem refuses to show or grade rather than risk being wrong.",
        ],
      },
      {
        id: "concept", title: "Concept check and plot discussions",
        what: "New worksheets usually start with short written questions on when and why methods apply. Some problems also end by asking you to discuss a plot. Claude judges these, and you press [[Submit for Claude to read]].",
        example: "Which methods can get you a general solution of a nonhomogeneous ODE from the homogeneous one? Answer in a sentence or two.",
        how: [
          "Answer in your own words, and be specific. A vague answer won't pass.",
          "SymPy can't check written answers, so treat these reference answers as less certain than the math keys. The page tells you when that applies.",
          "To leave them out of a new worksheet, say so in the focus box.",
        ],
      },
      {
        id: "skip", title: "Skipping",
        what: "[[Skip this part]] shows that step's answer and moves on, since later steps build on it. [[Skip this problem]] takes a whole problem out of your progress and exam mode.",
        example: "A problem covers a topic that isn't on your exam, so press [[Skip this problem]]. If you change your mind, open its tab and press [[Bring it back]].",
        how: [
          "Skipping never counts against you in the error log.",
          "[[Undo skip]] reopens a part, but you've already seen its answer.",
          "Skipping doesn't affect future worksheets, so ask Claude directly to leave out a topic.",
        ],
      },
      {
        id: "exam", title: "Exam mode",
        what: "[[Exam mode]] opens every step at once, with an optional time limit and no hints. Nothing is graded until you submit, and your practice progress is kept.",
        example: "Tick [[Exam mode]], set 45 minutes and press [[Start exam]]. When you're done, press [[Submit exam for grading]] to see your score by step type, then [[Back to practice]].",
        how: [
          "Use it after practicing, to find out what you can do without help.",
          "If the time runs out, your exam is submitted automatically.",
          "Skipped problems stay out of the exam, and you can't skip once it has started.",
        ],
      },
      {
        id: "difficulty", title: "Difficulty levels",
        what: "When making a worksheet, Difficulty sets how hard its problems are: Off (textbook level), Easier, Harder or Challenge.",
        example: "Choose Harder for a practice set before an exam. You'd get special cases like repeated roots, rather than just uglier arithmetic.",
        how: [
          "A level is chosen when the worksheet is made and can't change afterward, so make a new one to change it.",
          "Without your book's pages, textbook level is approximate.",
        ],
      },
      {
        id: "extras", title: "Weak spots, added problems and methods",
        what: "Your misses and hint use are recorded in an error log, and new worksheets lean toward your weak spots. You can also add your own problem to a worksheet, and the [[Method repertoire]] lists what the worksheet can teach and check.",
        example: "To add a problem from class, open [[Type in a new problem for this set]], enter the ODE and initial conditions, choose a method and press [[Verify and add]].",
        how: [
          "If a new topic needs a method the worksheet doesn't have, Claude adds and verifies one first.",
          "Interactive worksheets currently cover ODEs. Study guides and equation sheets work for any class.",
        ],
      },
    ],
  },
  {
    title: "Study guides and equation sheets",
    sections: [
      {
        id: "guides", title: "Study guides",
        what: "A guide that explains a topic, with plots, drawn diagrams and optional example problems. Every guide includes definitions and, if it has equations, a list of symbols.",
        example: "Ask for a guide on Chapter 11 with Include example problems and Include plots and diagrams ticked. Try each example before you press [[Show worked solution]].",
        how: [
          "Say how you want it explained in the focus box, like: break it down simply, I'm struggling with the concepts.",
          "Plots are computed from their formulas. Examples are labeled verified by SymPy, or not checked by SymPy.",
          "Diagrams are simple drawings by Claude, not pictures from your book or the web.",
          "Tell Claude which section isn't clicking, and it can rework it.",
        ],
      },
      {
        id: "reference", title: "Quick reference",
        what: "On a study guide, [[Quick reference]] opens a side panel with Definitions, Equations and Nomenclature tabs, which stays open while you scroll.",
        example: "Reading about energy and forgot what $x_m$ means? Press [[Quick reference]] and open the Nomenclature tab, without losing your place.",
        how: [
          "Press [[Expand]] to see more in a larger pop-up, and [[Back to side panel]] or Esc to return.",
          "[[Close]] or the [[Quick reference]] button turns the panel off. It remembers whether it was open.",
          "Everything in the tabs also stays in the guide itself, so printouts stay complete.",
        ],
      },
      {
        id: "sheets", title: "Equation sheets",
        what: "A compact two-column sheet of formulas grouped by topic, each with a short note on when it applies.",
        example: "Before an exam, ask for an equation sheet on Chapters 1 and 2, using your library.",
        how: [
          "SymPy doesn't check these formulas. Check them against your book or the linked sources, especially if you're relying on it in an exam.",
        ],
      },
      {
        id: "print", title: "Printing",
        what: "[[Print or save as PDF]] gives a clean white printout on guides and equation sheets, with every solution opened.",
        example: "Press it, then choose Save to PDF in your browser's print window to keep a copy.",
        how: [
          "Source links print with their full addresses in the Sources list.",
        ],
      },
    ],
  },
  {
    title: "Checking the information",
    sections: [
      {
        id: "sources", title: "Sources",
        what: "Written claims in new guides link to where they came from. A link opens the web page, or the right page of your own file. When something is saved, the server opens each cited page and confirms the quote is there.",
        example: "A definition shows Source: Your Chapter 11 slides, p. 5. Click it to open that page, then compare it with the quote in the Sources list at the end.",
        how: [
          "The quote tells you what to look for. If the page doesn't jump to it, use Ctrl+F.",
          "A confirmed quote shows the source says that passage, not that Claude's explanation of it is right.",
          "(not auto-checked) means a site blocked the check, or the page is a picture. Open it and check it yourself.",
          "Claude's own explanation, no source marks an analogy or explanation that's Claude's rather than a source's.",
          "Live replies such as Explain why have no sources, and math answers are verified by SymPy rather than linked.",
        ],
      },
    ],
  },
  {
    title: "Your library",
    sections: [
      {
        id: "library-add", title: "Adding files",
        what: "The [[Library]] keeps your material for each class, so Claude can use it later without you adding it again. PDF, Word, PowerPoint, images and text files all work, up to 200 MB each.",
        example: "Press [[Add files]], drag in your lecture slides, enter PHYS 301 and Waves, choose Lecture slides as the type, and press [[Add to library]].",
        how: [
          "Keep the class name and number consistent, since that's how files are grouped. Suggestions appear as you type.",
          "Your original files aren't changed. The library keeps its own copy, stored in a hidden folder in your home folder, so back it up with [[Export]] if it matters.",
          "PowerPoint speaker notes aren't included.",
        ],
      },
      {
        id: "library-find", title: "Finding files",
        what: "Search looks through file names and the text inside your files. Filter by class, subject, type or status, and sort by recently added, name, class, subject, type or length.",
        example: "Search for standing wave, and each matching file lists the pages it's on. Click a page to open the file there.",
        how: [
          "Every word you search for has to be on the page, so start with one or two words.",
          "Scans and handwriting can't be searched until Claude has read them.",
          "[[Edit details]] fixes a file's class, subject or type.",
          "[[Delete]] removes the library's copy and stops any source links to it from working. Your original stays where it is.",
        ],
      },
      {
        id: "library-index", title: "Indexing and picture pages",
        what: "Adding a file saves its text page by page, at no Claude usage, so it's searchable right away. Claude then writes a short index of what's where, which later work reads first instead of reopening the file.",
        example: "A status of Ready to use means it's indexed. Claude hasn't indexed it means you can press [[Ask Claude to index]], and [[Finish indexing]] reads the picture pages that are left.",
        how: [
          "Scans, handwriting and image-only slides have to be read by Claude once, and these cost the most. Claude asks first if a file has more than 30.",
          "Files you add through the page are indexed automatically, which uses some of your Claude usage.",
        ],
      },
      {
        id: "library-move", title: "Exporting and importing",
        what: "[[Export]] saves the files you're currently showing as a single zip, with their saved text and index. [[Import]] adds a zip like that to this library.",
        example: "To back up one class, set the Class filter to it and press [[Export]]. To share notes with a classmate, send them that zip, and they press [[Import]].",
        how: [
          "Check your filters first, because exactly the files you're showing are what gets exported.",
          "A zip holds your actual files. Only share it with people who should have them, and don't share anything you aren't allowed to, like a textbook scan.",
          "Importing skips a file already in the same class, and keeps its index, so Claude doesn't have to redo that work.",
          "Only import zips from people you trust. Claude treats what's in them as information, not as instructions, but they can still be wrong.",
        ],
      },
      {
        id: "library-use", title: "Making something from your library",
        what: "In the [[New]] form, Use my library tells Claude to work from that class's files and cite their pages, using the web only for what they don't cover.",
        example: "Choose Study guide, set Use my library to PHYS 301 Waves, and enter Chapter 3: standing waves as the topic.",
        how: [
          "Add your material before making a document, so it can be cited.",
          "You can also just ask in the terminal, for example: using my PHYS 301 notes, make an equation sheet on standing waves.",
        ],
      },
    ],
  },
  {
    title: "Courses (experimental)",
    sections: [
      {
        id: "courses", title: "Courses (experimental)",
        what: "A course teaches a topic you're new to, one section at a time. Each section has a lesson and a check, and it opens once you've mastered the sections it builds on. [[Courses]] on the home page lists yours. Courses are experimental, so expect rough edges.",
        example: "Press [[New]], choose Course (experimental), and write: Orbital mechanics, for a CubeSat project at school. Claude researches it, then agrees a teaching style, the kinds of checks, how much counts as mastered, and a syllabus with you in the terminal.",
        how: [
          "[[Generate]] on an available section makes its lesson and check. Only one section is made at a time.",
          "A check counts once every item is answered or skipped. An item counts if it's right without opening the answer hint, and a few nudges or method hints are fine, up to the limit in your plan.",
          "Multiple choice and numeric items are graded exactly by the program. Short answers are judged by Claude, and the page says so.",
          "For a numeric item, type only the number. The unit you should use is shown beside the box, and decimals are fine.",
          "If you don't pass, press [[Retry check]] for a new check aimed at what you missed.",
          "A placement quiz can mark early sections as tested out. It only counts when you take it in exam mode.",
          "[[Edit syllabus]] and [[Change plan]] ask Claude in your terminal. Sections keep their progress when the syllabus changes.",
          "[[Delete course]] removes the course but keeps its lessons and checks in your list.",
        ],
      },
    ],
  },
  {
    title: "Good to know",
    sections: [
      {
        id: "privacy", title: "Privacy",
        what: "The program can only be reached from this computer, and nothing is published or hosted. Your files, progress and documents stay on this machine.",
        example: "Other devices on your network can't open the page, even with the address.",
        how: [
          "Anything Claude reads goes to Anthropic to be processed, under your Claude account's terms.",
          "When saving a document, the server visits only the web pages it cites, to check their quotes.",
          "Don't share the full page address, since it includes your private key.",
        ],
      },
      {
        id: "trouble", title: "If something goes wrong",
        what: "Most problems have a quick fix.",
        example: "If the page says the worksheet server isn't running, type <code>/worksheet</code> in Claude Code and reload the page. Your work is safe.",
        how: [
          "Blank page: your browser may be blocking JavaScript for 127.0.0.1. In a blocker like uBlock Origin, turn off its JavaScript blocking for this site.",
          "Not authorised: the address is missing its private key. Use your bookmark, or run <code>/worksheet</code>.",
          "A PDF, Word or PowerPoint file won't add: reading PDFs needs poppler-utils, and Word and PowerPoint need LibreOffice. The message names what's missing.",
          "Primes like $y'$ look tiny, or math looks wrong: your browser may be blocking website fonts. Allow them for this site.",
          "Stuck on Claude is making this: check the terminal for a question or permission prompt.",
          "A wrong grade you can't explain: tell Claude what you entered and which step it was, since it may be a real bug.",
        ],
      },
    ],
  },
];
