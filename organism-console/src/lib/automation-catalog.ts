export type AutomationDifficulty = "beginner" | "intermediate" | "advanced"
export type AutomationAudience = "robert" | "senior" | "beginner"
export type AutomationGroup = "starter" | "senior-help" | "scary-help" | "basic-help"
export type AutomationCategory =
  | "research"
  | "career"
  | "coding"
  | "resume"
  | "money"
  | "reminders"
  | "safety"
  | "documents"
  | "communication"
  | "health"
  | "home"
  | "finance"
  | "accounts"
  | "files"
  | "email"
  | "printing"

export interface AutomationWord {
  term: string
  meaning: string
}

export interface AutomationCatalogItem {
  id: string
  title: string
  audience: AutomationAudience
  group: AutomationGroup
  category: AutomationCategory
  difficulty: AutomationDifficulty
  isStarter: boolean
  plainEnglish: string
  whenToUse: string
  inputs: string[]
  outputs: string[]
  example: string
  whyItHelps: string
  commonMistakes: string[]
  whatItMeans: string
  whyThisMatters: string
  wordsToKnow: AutomationWord[]
  beforeYouStart: string[]
  steps: string[]
  whatSuccessLooksLike: string[]
  whenToAskForHelp: string[]
}

export const automationCatalog: AutomationCatalogItem[] = [
  {
    id: "ai-chat-search",
    title: "AI Chat Search",
    audience: "robert",
    group: "starter",
    category: "research",
    difficulty: "beginner",
    isStarter: true,
    plainEnglish: "Searches across your AI chats to find ideas, code, answers, and past work quickly.",
    whenToUse: "Use this when you remember discussing something with ChatGPT, Claude, Gemini, Perplexity, or Copilot but cannot remember where.",
    inputs: ["Search words or question", "Selected chat sources"],
    outputs: ["Matched conversations", "Best answer snippets", "Source links or references"],
    example: "Find the conversation where I fixed async issues in the orchestrator runtime.",
    whyItHelps: "It saves time and stops you from repeating work you already did.",
    commonMistakes: ["Searching with terms that are too broad", "Forgetting which AI tools were included"],
    whatItMeans: "This automation helps you search through your past AI conversations the way you would search through old notes. Instead of opening many chats one by one, you ask one question and it looks for the best matches.",
    whyThisMatters: "Beginners often lose good ideas because they do not remember where they saw them. This helps you find useful work you already did instead of starting over.",
    wordsToKnow: [
      { term: "search term", meaning: "A word or phrase you type to look for something." },
      { term: "source", meaning: "The place the information came from, such as ChatGPT or Claude." },
      { term: "match", meaning: "A result that looks related to what you searched for." }
    ],
    beforeYouStart: [
      "Think of 2 or 3 specific words that appeared in the conversation.",
      "Choose which AI tools you want to search.",
      "Start with a narrow search before trying a broad one."
    ],
    steps: [
      "Type the main words or question you remember.",
      "Choose the chat sources you want to search.",
      "Run the search and look at the top matches first.",
      "Open the best match and check whether it answers your question.",
      "If needed, try a more specific search with fewer words."
    ],
    whatSuccessLooksLike: [
      "You find the right conversation without opening many old chats.",
      "You can quickly reuse the answer, code, or idea you found."
    ],
    whenToAskForHelp: [
      "If the search returns too many unrelated results.",
      "If you are not sure which AI source contains the information."
    ]
  },
  {
    id: "resume-cert-updater",
    title: "Resume + Certification Updater",
    audience: "robert",
    group: "starter",
    category: "resume",
    difficulty: "beginner",
    isStarter: true,
    plainEnglish: "Finds new certifications on your computer and updates your resume draft with them.",
    whenToUse: "Use this whenever you finish a course, earn a certificate, or want to refresh your resume.",
    inputs: ["Resume file location", "Certificate folders", "Preferred resume version"],
    outputs: ["Updated resume draft", "Certification change summary", "Suggested wording"],
    example: "Detect a new Coursera or AWS certificate and add it to the correct resume section.",
    whyItHelps: "It keeps your resume current without forcing you to remember every course or certification manually.",
    commonMistakes: ["Pointing at the wrong folders", "Adding every certificate without checking relevance", "Overwriting the main resume without review"],
    whatItMeans: "This automation checks the folders where your certificates live, notices what is new, and helps update your resume draft. A resume draft is a working copy of your resume, not the final version you send out.",
    whyThisMatters: "Beginners often earn certifications and forget to add them later. This helps keep your resume current so your effort turns into something useful.",
    wordsToKnow: [
      { term: "resume draft", meaning: "A working version of your resume that you can still edit." },
      { term: "certificate folder", meaning: "The folder where your saved certificates are stored." },
      { term: "relevant", meaning: "Useful for the kind of job or goal you care about." }
    ],
    beforeYouStart: [
      "Know where your main resume file is saved.",
      "Know where your certificates are stored on your computer.",
      "Decide whether you want one general resume or different versions for different jobs."
    ],
    steps: [
      "Choose the resume file you want to update.",
      "Choose the folders where your certificates are saved.",
      "Run the automation so it can look for newer certificates.",
      "Review the suggested resume changes before saving anything.",
      "Save the updated version as a new copy first."
    ],
    whatSuccessLooksLike: [
      "Your updated resume includes the right certifications.",
      "You still have the original resume in case you want to compare versions."
    ],
    whenToAskForHelp: [
      "If you are not sure which certifications belong on the resume.",
      "If the automation finds files that do not look like real certifications."
    ]
  },
  {
    id: "money-ideas-feed",
    title: "Money Ideas Feed",
    audience: "robert",
    group: "starter",
    category: "money",
    difficulty: "beginner",
    isStarter: true,
    plainEnglish: "Tracks current computer-based ways for you to make money and ranks ideas by fit.",
    whenToUse: "Use this when you want new income ideas that match your skills and setup.",
    inputs: ["Interests", "Skills", "Available time", "Optional earning goal"],
    outputs: ["Ranked money ideas", "Trend notes", "Beginner picks", "Why it fits you"],
    example: "Show the best current online opportunities for someone strong in AI, coding, and automation.",
    whyItHelps: "It helps you focus on realistic income ideas instead of random hype.",
    commonMistakes: ["Chasing every idea at once", "Ignoring difficulty level", "Not matching ideas to real skills"],
    whatItMeans: "This automation looks for ways to make money from your computer and sorts them by how well they match you. It does not promise instant income. It helps you compare options more clearly.",
    whyThisMatters: "Beginners often waste time on trends that do not fit their skills. This helps you focus on ideas that make sense for your background, tools, and time.",
    wordsToKnow: [
      { term: "fit", meaning: "How well something matches your skills, tools, and goals." },
      { term: "trend", meaning: "Something that is getting popular right now." },
      { term: "earning goal", meaning: "How much money you want to try to make." }
    ],
    beforeYouStart: [
      "Think about how many hours you realistically have each week.",
      "List the skills you already have.",
      "Decide whether you want fast side income or long-term growth."
    ],
    steps: [
      "Enter your main skills and interests.",
      "Add how much time you have available.",
      "Run the feed to see ranked income ideas.",
      "Read why each idea fits or does not fit you.",
      "Pick one or two ideas to explore first."
    ],
    whatSuccessLooksLike: [
      "You end with a short list of realistic money ideas.",
      "You understand why those ideas match your skills better than others."
    ],
    whenToAskForHelp: [
      "If every idea feels too advanced or unclear.",
      "If you need help choosing between two similar options."
    ]
  },
  {
    id: "upwork-analyzer",
    title: "Upwork Analyzer",
    audience: "robert",
    group: "starter",
    category: "money",
    difficulty: "beginner",
    isStarter: true,
    plainEnglish: "Looks at Upwork job posts and tells you which ones are the best match for your skills.",
    whenToUse: "Use this when you want to find freelance work on Upwork but do not want to read through hundreds of bad job posts.",
    inputs: ["Upwork search URL or job list", "Your profile summary", "Preferred budget range"],
    outputs: ["Ranked job list", "Match score for each job", "Suggested proposal focus"],
    example: "Analyze the top 20 React developer jobs and highlight the ones that match my portfolio.",
    whyItHelps: "It saves hours of manual searching and helps you apply to the jobs you are most likely to win.",
    commonMistakes: ["Using too many broad search terms", "Applying to jobs with very low match scores"],
    whatItMeans: "This automation acts like a smart assistant that reads Upwork jobs for you. It compares what the client wants with what you can do, and gives each job a score so you know where to focus.",
    whyThisMatters: "Freelance sites are crowded. Finding the right job quickly means you can be the first to apply with a great proposal.",
    wordsToKnow: [
      { term: "Upwork", meaning: "A website where businesses hire freelancers for project work." },
      { term: "Match score", meaning: "A number that shows how well your skills fit what the client is asking for." },
      { term: "Proposal", meaning: "The message you send to a client to explain why they should hire you." }
    ],
    beforeYouStart: [
      "Have your Upwork account ready.",
      "Make sure your profile description is up to date.",
      "Know which skills you want to use for work today."
    ],
    steps: [
      "Paste the Upwork search results or job link.",
      "Check that your profile information is correct.",
      "Run the analyzer to see the ranked jobs.",
      "Review the match scores and the \"Why it fits\" notes.",
      "Choose the top 2 or 3 jobs to write proposals for."
    ],
    whatSuccessLooksLike: [
      "You have a short list of high-quality jobs that fit your skills.",
      "You spend less time searching and more time earning."
    ],
    whenToAskForHelp: [
      "If the analyzer cannot read the job posts correctly.",
      "If you are not sure why a job received a low match score."
    ]
  },
  {
    id: "vscode-automation",
    title: "VS Code Automation",
    audience: "robert",
    group: "starter",
    category: "coding",
    difficulty: "beginner",
    isStarter: true,
    plainEnglish: "Helps you perform common coding tasks in VS Code using simple commands.",
    whenToUse: "Use this when you are working on code and want to automate repetitive tasks like formatting, searching, or creating files.",
    inputs: ["Current project folder", "Task description (e.g., \"Fix all lint errors\")", "Target files"],
    outputs: ["Updated code files", "Summary of changes made", "List of any errors found"],
    example: "Find all instances of 'TODO' in the project and list them in a summary file.",
    whyItHelps: "It makes coding faster and reduces mistakes by handling the boring parts of development.",
    commonMistakes: ["Running automation on the wrong folder", "Not reviewing changes before saving"],
    whatItMeans: "VS Code is the tool where you write code. This automation adds \"superpowers\" to it, allowing you to give it instructions in plain English to change or organize your files.",
    whyThisMatters: "Learning to automate your coding environment is a key skill for a modern developer. It allows you to focus on the logic instead of the typing.",
    wordsToKnow: [
      { term: "VS Code", meaning: "Visual Studio Code, a popular program used by developers to write and edit code." },
      { term: "Lint errors", meaning: "Small mistakes in code formatting or style that can be fixed automatically." },
      { term: "Project folder", meaning: "The main folder on your computer that contains all the files for your software." }
    ],
    beforeYouStart: [
      "Open your project in VS Code.",
      "Make sure you have saved your current work.",
      "Identify one repetitive task you want to finish quickly."
    ],
    steps: [
      "Choose the project folder you are working in.",
      "Describe the task you want the automation to do.",
      "Wait for the automation to finish scanning your files.",
      "Review the suggested changes to your code.",
      "Accept the changes to update your files."
    ],
    whatSuccessLooksLike: [
      "Your code is cleaner and more organized.",
      "You finished a repetitive task in seconds instead of minutes."
    ],
    whenToAskForHelp: [
      "If the automation makes a change you do not understand.",
      "If it cannot find the files you are looking for."
    ]
  },
  {
    id: "make-folder-helper",
    title: "Make Folder Helper",
    audience: "beginner",
    group: "basic-help",
    category: "files",
    difficulty: "beginner",
    isStarter: false,
    plainEnglish: "Creates a folder and explains what a folder is in simple terms.",
    whenToUse: "Use this when files feel messy and you want a clear place to keep things.",
    inputs: ["What the folder is for", "Where you want it"],
    outputs: ["Suggested folder name", "Folder plan", "Simple explanation"],
    example: "Make a Resume folder inside Documents.",
    whyItHelps: "It teaches a basic computer skill that many beginners find confusing.",
    commonMistakes: ["Making too many folders at once", "Choosing a folder name that is too vague"],
    whatItMeans: "A folder is like a labeled container on your computer. It holds files so they stay together in one place. Making a folder means creating that container before you put files into it.",
    whyThisMatters: "If files are saved everywhere with no plan, they become hard to find later. A folder gives related files one clear home.",
    wordsToKnow: [
      { term: "file", meaning: "A single item on your computer, like a document, picture, or PDF." },
      { term: "folder", meaning: "A place that holds files and sometimes other folders." },
      { term: "location", meaning: "Where something is stored on your computer." },
      { term: "rename", meaning: "To change the name of a file or folder." }
    ],
    beforeYouStart: [
      "Decide what kind of files will go into the folder.",
      "Pick one easy location, such as Documents.",
      "Use a name that clearly tells you what belongs there."
    ],
    steps: [
      "Choose where the folder should live, such as Documents or Desktop.",
      "Create the new folder in that location.",
      "Give it a simple name, such as Resume or Bills.",
      "Open the folder to make sure it is the right one.",
      "Move the matching files into that folder."
    ],
    whatSuccessLooksLike: [
      "You can open the folder and immediately understand what belongs there.",
      "The related files are now easier to find."
    ],
    whenToAskForHelp: [
      "If you are not sure where the folder should be created.",
      "If moving files makes you worried you may lose them."
    ]
  },
  {
    id: "suspicious-email-checker",
    title: "Suspicious Email Checker",
    audience: "beginner",
    group: "scary-help",
    category: "email",
    difficulty: "beginner",
    isStarter: false,
    plainEnglish: "Checks whether an email looks suspicious and explains why.",
    whenToUse: "Use this before clicking a link, downloading an attachment, or replying.",
    inputs: ["Email text", "Sender name", "Optional subject line"],
    outputs: ["Risk level", "Warning signs", "Safer next action"],
    example: "Check an urgent email asking you to verify bank details.",
    whyItHelps: "It helps stop phishing before it causes harm.",
    commonMistakes: ["Trusting a message because it looks official", "Opening attachments before checking"],
    whatItMeans: "A suspicious email is a message that may be trying to trick you. Some fake emails pretend to be from banks, stores, or support teams to get your password, money, or personal information.",
    whyThisMatters: "Beginners often click first because the message looks urgent or official. Slowing down and checking the message can prevent fraud.",
    wordsToKnow: [
      { term: "phishing", meaning: "A scam that tries to trick you into giving away information or clicking something unsafe." },
      { term: "attachment", meaning: "A file sent along with an email." },
      { term: "sender", meaning: "The person or company the email says it came from." }
    ],
    beforeYouStart: [
      "Do not click any link yet.",
      "Do not open any attachment yet.",
      "Read the full message carefully, including the sender details."
    ],
    steps: [
      "Look at who sent the email.",
      "Read the message slowly and notice any pressure or urgency.",
      "Check whether it asks for passwords, money, or personal information.",
      "Notice whether the link or request seems unusual for that company.",
      "Choose the safer next step, such as deleting it or contacting the company another way."
    ],
    whatSuccessLooksLike: [
      "You can explain why the message looks safe or unsafe.",
      "You avoid clicking something risky before checking."
    ],
    whenToAskForHelp: [
      "If the message appears to be about an important account and you are unsure.",
      "If you already clicked the link or opened the attachment."
    ]
  },
  {
    id: "web-search",
    title: "Web Search",
    audience: "robert",
    group: "starter",
    category: "research",
    difficulty: "beginner",
    isStarter: true,
    plainEnglish: "Searches the web for current information, prices, ideas, news, and answers.",
    whenToUse: "Use this when you need the most up-to-date information that happened recently, or to compare current prices for things you want to buy.",
    inputs: ["Search question or topic", "Search depth"],
    outputs: ["Live web results", "Summary of current facts", "Direct source links"],
    example: "Find the current news about a specific technology or today's price for a product.",
    whyItHelps: "Helps avoid outdated answers and makes it easier to compare real current options.",
    commonMistakes: [
      "Using search terms that are too short",
      "Not checking the date on the search results"
    ],
    whatItMeans: "This tool is like a personal researcher. It looks at the live internet to find information that is happening right now, which is better than relying on older data.",
    whyThisMatters: "The world changes fast. Web search ensures you are making decisions based on what is true today, not what was true a year ago.",
    wordsToKnow: [
      { term: "real-time", meaning: "Happening right now, with no delay." },
      { term: "source link", meaning: "The direct address of the website where the information was found." },
      { term: "outdated", meaning: "Information that is no longer correct because too much time has passed." }
    ],
    beforeYouStart: [
      "Think of 3-4 specific words related to your question.",
      "Decide if you need local information or global news."
    ],
    steps: [
      "Type your question or topic into the search bar.",
      "Review the summaries of the top websites found.",
      "Check the dates on the results to ensure they are current.",
      "Click the links to read more details on the most promising sites.",
      "Compare information from different sources to be sure it's right."
    ],
    whatSuccessLooksLike: [
      "You find the latest news, answers, or prices you needed.",
      "You feel confident that the information is not outdated."
    ],
    whenToAskForHelp: [
      "If the results seem to be mostly ads instead of real information.",
      "If you find two websites that say the exact opposite thing."
    ]
  },
  {
    id: "used-rv-finder",
    title: "Used RV Finder",
    audience: "robert",
    group: "starter",
    category: "money",
    difficulty: "beginner",
    isStarter: true,
    plainEnglish: "Searches for used Class B and Class C motorhomes under $30,000 within 50 miles of your home, compares features, and helps decide which one is the best buy.",
    whenToUse: "Use this when you are looking for a pre-owned Class B or Class C motorhome to live in full-time and want to find the best quality unit while staying under your $30,000 budget cap and close to home.",
    inputs: ["Desired RV type (Class B/C)", "Budget cap ($30,000)", "Home location + radius (50 mi)"],
    outputs: ["Matched used RV list", "Distance from home", "Feature and condition comparison", "Buy/Skip advice"],
    example: "Find Class B and Class C motorhomes under $30,000 within 50 miles of Roosevelt, NY and compare their features and condition.",
    whyItHelps: "Saves time, avoids bad RV deals, and helps focus on units with the best mix of price, features, condition, and distance from home.",
    commonMistakes: [
      "Judging a unit only by age instead of maintenance",
      "Ignoring signs of water damage",
      "Assuming good tire tread means the tires are safe",
      "Traveling hundreds of miles for a rig you could not fully inspect"
    ],
    whatItMeans: "This automation finds used Class B and Class C motorhomes for sale that fit your budget and are within a set distance of your home. It compares features like mileage and floorplans, but most importantly, it helps you evaluate the condition. Remember: A well-maintained older RV can be a better buy than a newer neglected one.",
    whyThisMatters: "Water damage is the biggest red flag in any used RV. This tool helps you focus on finding a solid, leak-free unit nearby so you can inspect it in person and avoid expensive repairs.",
    wordsToKnow: [
      { term: "Class B", meaning: "A camper van built on a van chassis — small, easy to drive, great gas mileage." },
      { term: "Class C", meaning: "A motorhome with a van-like cab and a coach body behind it — roomier than a B." },
      { term: "water damage", meaning: "Rot or mold caused by leaks—the #1 thing to avoid in a used RV." },
      { term: "tire age", meaning: "Tires should be replaced every 5-7 years regardless of how much tread is left." },
      { term: "independent inspection", meaning: "Hiring a professional to check the RV before you commit to buying it." }
    ],
    beforeYouStart: [
      "Confirm your budget cap is strictly $30,000.",
      "List the must-have features you want to compare.",
      "Be ready to ask sellers for full maintenance records.",
      "Plan to see any shortlisted rig in person before committing."
    ],
    steps: [
      "Search for used Class B and Class C motorhomes that fit your $30,000 budget.",
      "Check the distance from home on each match.",
      "Compare the features, mileage, and floorplans of each match.",
      "Inspect descriptions and photos for signs of leaks in the roof, floor, slide-outs, windows, and seams.",
      "Verify the age of the tires, not just the tread depth.",
      "Plan to test all appliances, plumbing, HVAC, and electrical systems.",
      "Always prefer an independent RV inspection before making a final purchase."
    ],
    whatSuccessLooksLike: [
      "You find a Class B or Class C under $30,000 that has been well-maintained.",
      "You can drive to see it in person and have the advice needed to decide which one is the best buy."
    ],
    whenToAskForHelp: [
      "If you see any soft spots in the floor or ceiling.",
      "If the seller cannot provide any maintenance history."
    ]
  }
]

export const starterAutomations = automationCatalog.filter((item) => item.group === "starter")
export const seniorAutomations = automationCatalog.filter((item) => item.group === "senior-help")
export const scaryAutomations = automationCatalog.filter((item) => item.group === "scary-help")
export const basicAutomations = automationCatalog.filter((item) => item.group === "basic-help")
