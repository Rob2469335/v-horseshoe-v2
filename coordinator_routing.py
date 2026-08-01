# Coordinator Agent Routing Table
# Implements precise greeting detection and self-referential improvement handling
class CoordinatorRouter:
    """
    Routes incoming requests based on content analysis.
    
    Priority order:
    1. Exact greeting detection (case-insensitive 'hi' as standalone word)
    2. Self-referential improvement requests -> action=final
    3. Specific agent names -> that agent
    4. Simple coding tasks -> coder
    5. Everything else -> planner
    """
    
    def __init__(self):
        self.agent_names = [
            'coder', 'planner', 'debugger', 'reviewer',
            'executor', 'architect', 'tester', 'qa', 'researcher'
        ]
    
    def detect_greeting(self, input_text):
        """
        EXACT greeting detection: only matches standalone 'hi' (case-insensitive).
        Does NOT match strings that start with 'hi' or end with 'hello'.
        """
        import re
        # Match exact word boundary around 'hi'
        pattern = r'^\s*hi\s*$'
        return bool(re.match(pattern, input_text.strip(), re.IGNORECASE))
    
    def detect_self_improvement(self, input_text):
        """
        Detect requests to fix/improve the coordinator itself.
        Examples: 'fix yourself', 'improve yourself', 'rewrite your routing table'
        """
        lower = input_text.lower().strip()
        improvement_keywords = [
            'fix yourself', 'improve yourself', 'fix youself', 
            'improve youself', 'rewrite your routing', 'correct yourself',
            'update yourself', 'enhance yourself'
        ]
        for keyword in improvement_keywords:
            if keyword in lower:
                return True
        # Also check if the request is about improving the coordinator agent
        import re
        if re.search(r'coordinator.*(self|yourself)', lower):
            return True
        return False
    
    def detect_agent_name(self, input_text):
        """
        Detect specific agent names in the request.
        Returns the matched agent name or None.
        """
        import re
        for agent in self.agent_names:
            pattern = r'\b' + agent.replace(' ', r'\s+') + r'\b'
            if re.search(pattern, input_text, re.IGNORECASE):
                return agent
        return None
    
    def detect_simple_code_task(self, input_text):
        """
        Detect simple coding/programming tasks.
        Examples: 'create a function', 'write code', 'implement this feature'
        """
        import re
        code_keywords = [
            r'\b(create|write|implement|build|develop|fix|add|remove|change)\s+(function|code|method|feature|task|file|script|project)',
            r'\b(help me with)\s*(programming|coding|development)',
            r'\b(write|create)\s+.*?\s*python',
        ]
        for pattern in code_keywords:
            if re.search(pattern, input_text, re.IGNORECASE):
                return True
        # Simple heuristic: if it contains common code patterns
        if any(kw in input_text.lower() for kw in ['function:', 'def ', 'public function', 'private method']):
            return True
        return False

    def detect_research_task(self, input_text):
        """
        Detect tasks requiring internet search, reading, or gathering information.
        Examples: 'search the internet', 'research x', 'look up y'
        """
        import re
        research_keywords = [
            r'\b(search)\s+(the\s+)?(internet|web)\b',
            r'\b(research|look up|lookup|find out about|google|investigate)\b'
        ]
        for pattern in research_keywords:
            if re.search(pattern, input_text, re.IGNORECASE):
                return True
        return False
    
    def route_request(self, input_text):
        """
        Main routing logic with priority-based decision making.
        Returns the appropriate action to take.
        """
        # Priority 1: Exact greeting detection
        if self.detect_greeting(input_text):
            return {
                'action': 'final',
                'response': 'Hello! How can I assist you today?'
            }
        
        # Priority 2: Self-referential improvement requests
        if self.detect_self_improvement(input_text):
            return {
                'action': 'final',
                'response': 'I understand this is about improving myself. I will handle that directly.'
            }
        
        # Priority 3: Specific agent names
        matched_agent = self.detect_agent_name(input_text)
        if matched_agent:
            return {
                'action': 'delegate',
                'agent': matched_agent,
                'input': input_text
            }
        
        # Priority 4: Research tasks
        if self.detect_research_task(input_text):
            return {
                'action': 'delegate',
                'agent': 'researcher',
                'input': input_text
            }
        
        # Priority 5: Simple coding tasks
        if self.detect_simple_code_task(input_text):
            return {
                'action': 'delegate',
                'agent': 'coder',
                'input': input_text
            }
        
        # Default: Everything else -> planner
        return {
            'action': 'planner',
            'input': input_text
        }