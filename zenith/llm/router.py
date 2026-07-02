"""
llm/router.py - Smart Dynamic Model Router with Cloud Support
"""
from typing import Dict, Any, List, Optional
from dataclasses import dataclass
import os


@dataclass
class ModelTier:
    name: str
    models: List[str]
    capability: str
    is_cloud: bool = False


class ModelRouter:
    def __init__(self):
        # Local model tiers (your Ollama install)
        self.tiers = {
            "fast": ModelTier(
                name="fast",
                models=["phi4-mini:latest", "qwen2.5-coder:7b"],
                capability="fast",
                is_cloud=False
            ),
            "balanced": ModelTier(
                name="balanced", 
                models=["qwen2.5:7b-instruct", "qwen2.5:7b-instruct", "qwen2.5-coder:7b", "qwen2.5-coder:7b-16k"],
                capability="balanced",
                is_cloud=False
            ),
            "smart": ModelTier(
                name="smart",
                models=["qwen2.5:7b-instruct", "qwen2.5:7b-instruct-32k", "qwen2.5-coder:7b", "qwen2.5-coder:7b-32k"],
                capability="smart",
                is_cloud=False
            ),
            "expert": ModelTier(
                name="expert",
                models=["qwen2.5-coder:32b"],
                capability="expert",
                is_cloud=False
            ),
            "vision": ModelTier(
                name="vision",
                models=["qwen3-vl:8b", "moondream:latest"],
                capability="vision",
                is_cloud=False
            ),
            # Cloud models (Ollama Cloud - free tier with limits)
            "cloud_fast": ModelTier(
                name="cloud_fast",
                models=["ollama-cloud/llama3.1:8b", "ollama-cloud/mistral:7b"],
                capability="fast",
                is_cloud=True
            ),
            "cloud_balanced": ModelTier(
                name="cloud_balanced",
                models=["ollama-cloud/llama3.1:70b", "ollama-cloud/qwen2.5-coder:7b"],
                capability="balanced",
                is_cloud=True
            ),
            "cloud_smart": ModelTier(
                name="cloud_smart",
                models=["ollama-cloud/llama3.1:405b", "ollama-cloud/qwen2.5-coder:32b"],
                capability="smart",
                is_cloud=True
            ),
            "cloud_expert": ModelTier(
                name="cloud_expert",
                models=["ollama-cloud/gpt-4o", "ollama-cloud/claud-3.5"],
                capability="expert",
                is_cloud=True
            )
        }
        
        # Task type -> recommended tier (local first, cloud fallback)
        self.task_routing = {
            "code_gen": {"local": "balanced", "cloud": "cloud_balanced"},
            "code_debug": {"local": "smart", "cloud": "cloud_smart"},
            "code_review": {"local": "smart", "cloud": "cloud_smart"},
            "summarize": {"local": "fast", "cloud": "cloud_fast"},
            "explain": {"local": "balanced", "cloud": "cloud_balanced"},
            "document": {"local": "balanced", "cloud": "cloud_balanced"},
            "test_gen": {"local": "balanced", "cloud": "cloud_balanced"},
            "refactor": {"local": "smart", "cloud": "cloud_smart"},
            "vision_analyze": {"local": "vision", "cloud": "cloud_smart"},
            "embedding": {"local": "embedding", "cloud": "cloud_smart"},
            "default": {"local": "balanced", "cloud": "cloud_balanced"}
        }
        
        # Cloud configuration
        self.cloud_config = {
            "enabled": True,  # Auto-detect from Ollama auth
            "provider": "ollama-cloud",
            "free_tier_limits": {
                "requests_per_day": 100,
                "tokens_per_day": 100000
            }
        }
        
        # Track cloud usage
        self.cloud_usage = {
            "requests_today": 0,
            "tokens_today": 0
        }
        
        # Embedding model
        self.embedding_model = "qwen3-embedding:8b"
        
        # Fallback chains
        self.fallbacks = {
            "cloud_expert": ["cloud_smart", "cloud_balanced", "expert"],
            "cloud_smart": ["cloud_balanced", "smart", "balanced"],
            "cloud_balanced": ["cloud_fast", "balanced", "smart"],
            "cloud_fast": ["balanced", "fast"],
            "expert": ["smart", "balanced"],
            "smart": ["balanced", "fast"],
            "balanced": ["fast"],
            "fast": ["balanced"],
            "vision": ["balanced"]
        }
    
    def classify_task(self, task: str) -> str:
        """Classify task type from prompt"""
        task_lower = task.lower()
        
        if any(word in task_lower for word in ["debug", "fix error", "bug", "broken", "not working"]):
            return "code_debug"
        elif any(word in task_lower for word in ["review", "audit", "check code", "lint"]):
            return "code_review"
        elif any(word in task_lower for word in ["refactor", "rewrite", "optimize", "improve code"]):
            return "refactor"
        elif any(word in task_lower for word in ["test", "unittest", "pytest", "create test"]):
            return "test_gen"
        elif any(word in task_lower for word in ["create", "write code", "generate", "build", "implement"]):
            return "code_gen"
        elif any(word in task_lower for word in ["document", "docstring", "readme", "comment"]):
            return "document"
        elif any(word in task_lower for word in ["explain", "what is", "how does", "understand", "concepts"]):
            return "explain"
        elif any(word in task_lower for word in ["summarize", "brief", "short version", "tl;dr"]):
            return "summarize"
        elif any(word in task_lower for word in ["image", "picture", "photo", "visual", "detect in image"]):
            return "vision_analyze"
        
        return "default"
    
    def is_cloud_available(self) -> bool:
        """Check if Ollama cloud is available (signed in + under limits)"""
        if not self.cloud_config["enabled"]:
            return False
        
        # Check if under daily limits
        if self.cloud_usage["requests_today"] >= self.cloud_config["free_tier_limits"]["requests_per_day"]:
            return False
        
        if self.cloud_usage["tokens_today"] >= self.cloud_config["free_tier_limits"]["tokens_per_day"]:
            return False
        
        # Check if user is signed in (Ollama auth file exists)
        auth_file = os.path.expanduser("~/.ollama/auth.json")
        return os.path.exists(auth_file)
    
    def requires_cloud(self, task: str, complexity_estimate: float = 0.5) -> bool:
        """Determine if task should use cloud (based on complexity)"""
        # High complexity tasks → cloud
        if complexity_estimate > 0.8:
            return True
        
        # Check if local expert model exists
        local_expert = self.tiers["expert"].models
        if not local_expert or len(local_expert) == 0:
            return True
        
        return False
    
    def route(self, task: str, context: Optional[Dict[str, Any]] = None, use_cloud: bool = False) -> str:
        """Route task to best model"""
        task_type = self.classify_task(task)
        routing = self.task_routing.get(task_type, self.task_routing["default"])
        
        if use_cloud and self.is_cloud_available():
            tier_name = routing["cloud"]
        else:
            tier_name = routing["local"]
        
        tier = self.tiers.get(tier_name, self.tiers["balanced"])
        primary_model = tier.models[0] if tier.models else self.tiers["balanced"].models[0]
        
        return primary_model
    
    def route_smart(self, task: str, context: Optional[Dict[str, Any]] = None) -> Dict[str, str]:
        """Smart routing: local first, cloud fallback for complex tasks"""
        task_type = self.classify_task(task)
        
        # Estimate complexity (simple heuristic)
        complexity = len(task) / 500  # longer prompts = more complex
        complexity = min(complexity, 1.0)
        
        # Decide cloud vs local
        use_cloud = self.requires_cloud(task, complexity) and self.is_cloud_available()
        
        # Get primary model
        primary = self.route(task, context, use_cloud)
        
        # Get fallback
        tier_name = None
        for name, tier in self.tiers.items():
            if primary in tier.models:
                tier_name = name
                break
        
        fallback_model = None
        if tier_name and tier_name in self.fallbacks:
            for fallback_tier in self.fallbacks[tier_name]:
                fallback = self.tiers[fallback_tier].models[0]
                if fallback != primary:
                    fallback_model = fallback
                    break
        
        return {
            "primary": primary,
            "fallback": fallback_model or primary,
            "used_cloud": use_cloud
        }
    
    def route_with_fallback(self, task: str, error: Optional[str] = None) -> Dict[str, str]:
        """Route with fallback chain"""
        result = self.route_smart(task)
        return {
            "primary": result["primary"],
            "fallback": result["fallback"]
        }
    
    def get_model_info(self, model: str) -> Dict[str, Any]:
        """Get model metadata"""
        for tier_name, tier in self.tiers.items():
            if model in tier.models:
                return {
                    "name": model,
                    "tier": tier_name,
                    "capability": tier.capability,
                    "is_cloud": tier.is_cloud
                }
        return {"name": model, "tier": "unknown", "capability": "unknown", "is_cloud": False}
    
    def list_available_models(self) -> List[Dict[str, Any]]:
        """List all available models with metadata"""
        models = []
        for tier_name, tier in self.tiers.items():
            for model in tier.models:
                models.append({
                    "name": model,
                    "tier": tier_name,
                    "capability": tier.capability,
                    "is_cloud": tier.is_cloud
                })
        return models
    
    def track_cloud_usage(self, tokens_used: int):
        """Track cloud usage for limits"""
        self.cloud_usage["requests_today"] += 1
        self.cloud_usage["tokens_today"] += tokens_used

